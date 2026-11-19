"""受管附件提取父进程（REQ D06 / DD §10.4）。

职责：内容类型核验、D06 前置限额、受监督子进程生命周期（真实退出/超时/取消）、
结果严格解码为 core 合同类型。解析本身在 ``attachment_worker`` 内完成，父进程
不直接读取用户路径。

子进程 stdout/stderr 由独立管道线程**流式有界**接收：超限即停止累积并终止子进程，
不做「先全量缓存、读完再判大小」（I74）。终止后必须确认进程与管道线程真实退出，
未退出的线程名作为残留事实记录（``residual_threads``）。
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import subprocess
import sys
import threading
import time
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import IO, Any, Final

from core.agent_types import (
    DEFAULT_ATTACHMENT_BUDGET,
    AgentValidationError,
    AttachmentBudget,
    AttachmentKind,
    FactValue,
    RangeLimitation,
    RangeStats,
    ReadState,
    SelectedRange,
    Selection,
    SourceKind,
    SourceVersionRef,
    decode_fact_value,
    decode_json_doc,
    decode_selected_range,
    to_wire_dict,
)
from services.agent_service.attachment_worker import (
    MAX_HEADER_BYTES,
    MAX_RESULT_BYTES,
    AttachmentParseError,
    validate_json_root,
)
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)

#: 等待子进程被终止后 join 的宽限秒数（先 terminate，再 kill）。
_KILL_GRACE_S: Final = 5.0
#: 父侧监督余量默认值（秒）：父超时 = worker 自身时限 + 本余量。worker 的时限从它
#: 读完请求头开始计（见 ``attachment_worker._handle_request``），而父侧计时包含
#: Popen、解释器启动、模块导入与退出回收，故父侧必须留出这段开销，不得与 worker
#: 时限同口径直接比较（I36）。
_DEFAULT_SUPERVISION_MARGIN_S: Final = 5.0
#: stderr 记录上限（只用于长度判断，不把内容写入错误/日志）。
_MAX_STDERR_BYTES: Final = 64 * 1024
#: 管道单次读取字节数：只管单次读取粒度，总规模由各管道上限约束。
_READ_CHUNK_BYTES: Final = 64 * 1024
#: 监督循环轮询间隔（秒）：决定取消/超限被察觉的最大延迟，不影响 worker 时限口径。
_SUPERVISION_POLL_S: Final = 0.05
#: 管道线程收尾 join 宽限（秒）：超时未退出的线程记为残留事实。
_THREAD_JOIN_GRACE_S: Final = 2.0

_EXTENSION_KINDS: Final[dict[str, AttachmentKind]] = {
    ".txt": AttachmentKind.TXT,
    ".md": AttachmentKind.MD,
    ".csv": AttachmentKind.CSV,
    ".json": AttachmentKind.JSON,
    ".xlsx": AttachmentKind.XLSX,
    ".pdf": AttachmentKind.PDF,
    ".png": AttachmentKind.PNG,
    ".jpg": AttachmentKind.JPEG,
    ".jpeg": AttachmentKind.JPEG,
}

_PNG_MAGIC: Final = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC: Final = b"\xff\xd8\xff"
_PDF_MAGIC: Final = b"%PDF-"


class AttachmentWorkerError(Exception):
    """附件提取失败（稳定 code；message 已脱敏，不含路径/正文/凭据）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class AttachmentExtractionRequest:
    """单文件提取请求（一次明确发送中的一件；数据已在内存，不接用户路径）。"""

    user_scope: str
    attachment_id: str
    file_name: str
    source_id: str
    source_version: str
    source_digest: str
    kind: AttachmentKind
    data: bytes
    selection: Selection
    #: 用户已确认 CSV 首条为表头；缺省 False 表示未知/未确认，首条按数据行计入限额（I23）。
    csv_header_confirmed: bool = False
    #: 请求方已取得「为定位所选范围必须读取选区外内容」的同意；缺省 False 即拒绝扩张读取（I70）。
    read_expansion_consent: bool = False


@dataclass(frozen=True, slots=True)
class AttachmentExtractionResult:
    """提取结果：同一源版的已读/未读范围与限制。"""

    source: SourceVersionRef
    selected_range: SelectedRange
    read_state: ReadState
    coverage: str
    body_text: str | None
    blocks: tuple[str, ...]
    facts: tuple[FactValue, ...]
    limitations: tuple[RangeLimitation, ...]
    stats: RangeStats
    worker_pid: int
    worker_exited: bool
    elapsed_s: float


def detect_attachment_kind(file_name: str, data: bytes) -> AttachmentKind:
    """扩展名给候选、内容魔数核验；不吻合即拒绝（不信扩展名/ZIP 压缩率）。"""

    if PurePath(file_name).name != file_name:
        raise AttachmentWorkerError("attachment_name_invalid", "file name must be a bare name without path")
    suffix = PurePath(file_name).suffix.lower()
    candidate = _EXTENSION_KINDS.get(suffix)
    if candidate is None:
        raise AttachmentWorkerError("attachment_type_unsupported", "attachment extension is not supported")

    head = data[:4096]
    if candidate is AttachmentKind.PDF:
        if not data.startswith(_PDF_MAGIC):
            raise AttachmentWorkerError("attachment_type_mismatch", "content is not a PDF document")
    elif candidate is AttachmentKind.PNG:
        if not data.startswith(_PNG_MAGIC):
            raise AttachmentWorkerError("attachment_type_mismatch", "content is not a PNG image")
    elif candidate is AttachmentKind.JPEG:
        if not data.startswith(_JPEG_MAGIC):
            raise AttachmentWorkerError("attachment_type_mismatch", "content is not a JPEG image")
    elif candidate is AttachmentKind.XLSX:
        if not _is_xlsx_container(data):
            raise AttachmentWorkerError("attachment_type_mismatch", "content is not an XLSX container")
    elif candidate is AttachmentKind.JSON:
        # 与 worker 解析共用同一 JSON 支持范围：BOM 剥离、标量根/对象/数组合法，
        # 非法输入沿用 worker 的错误码与消息，父子入口结论一致（I27）。
        try:
            validate_json_root(data)
        except AttachmentParseError as exc:
            raise AttachmentWorkerError(exc.code, exc.message) from exc
    else:
        if b"\x00" in head:
            raise AttachmentWorkerError("attachment_type_mismatch", "content is not UTF-8 text")
    return candidate


def _is_xlsx_container(data: bytes) -> bool:
    if not data.startswith(b"PK\x03\x04"):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError):
        return False
    return "xl/workbook.xml" in names


@dataclass
class _ActiveRun:
    """单次请求的子进程登记；取消只作用于本请求，不误杀同实例其他 worker。"""

    process: subprocess.Popen[bytes] | None = None
    cancelled: bool = False


class _BoundedPipeReader(threading.Thread):
    """流式有界读取单个管道：累计超过 ``limit`` 即置位并停止累积，不做全量缓存。

    置位后由监督循环终止子进程；线程自身不阻塞在关闭流程上（管道在 ``finally``
    关闭，进程被杀后读到 EOF 即退出）。
    """

    def __init__(self, stream: IO[bytes], limit: int, *, name: str) -> None:
        super().__init__(name=name, daemon=True)
        self._stream = stream
        self._limit = limit
        self._chunks: list[bytes] = []
        self.size = 0
        self.overflowed = threading.Event()
        self.read_error: bool = False

    def run(self) -> None:
        try:
            # ``read(n)`` 会阻塞到凑满 n 字节或 EOF（BufferedReader 语义），子进程长时间
            # 不退出时无法及时判超限；``read1`` 返回当前可用数据，才是流式接收。
            read1 = getattr(self._stream, "read1", None)
            read = read1 if read1 is not None else self._stream.read
            while True:
                chunk = read(_READ_CHUNK_BYTES)
                if not chunk:
                    return
                self.size += len(chunk)
                if self.size > self._limit:
                    self.overflowed.set()
                    return
                self._chunks.append(chunk)
        except (OSError, ValueError):
            # 收尾阶段管道被关闭：读取失败不等于结果完整，由调用方按协议失败处理。
            self.read_error = True
        finally:
            try:
                self._stream.close()
            except OSError:
                pass

    def payload(self) -> bytes:
        return b"".join(self._chunks)


class _StdinFeeder(threading.Thread):
    """请求体写入线程：子进程不读 stdin 时写入会阻塞，必须独立于监督循环。"""

    def __init__(self, stream: IO[bytes], payload: bytes) -> None:
        super().__init__(name="attachment-worker-stdin", daemon=True)
        self._stream = stream
        self._payload = payload

    def run(self) -> None:
        try:
            self._stream.write(self._payload)
            self._stream.flush()
        except (OSError, ValueError):
            # 子进程被终止后写管道必然失败（broken pipe）：退出码/结果协议给出结论。
            return
        finally:
            try:
                self._stream.close()
            except OSError:
                pass


def _consume_future_result(future: asyncio.Future[Any]) -> None:
    """已取消外层 waiter 的后台任务：取走异常，避免「never retrieved」告警。"""

    if not future.cancelled():
        future.exception()


class SubprocessAttachmentWorker:
    """受监督子进程提取器：默认 ``python -m services.agent_service.attachment_worker``。

    ``command``/``timeout_s``/``supervision_margin_s``/``max_result_bytes``/
    ``max_stderr_bytes`` 仅供测试注入（真实产品路径使用模块入口与 D06 预算）。
    """

    def __init__(
        self,
        *,
        budget: AttachmentBudget = DEFAULT_ATTACHMENT_BUDGET,
        command: Sequence[str] | None = None,
        timeout_s: float | None = None,
        supervision_margin_s: float | None = None,
        max_result_bytes: int | None = None,
        max_stderr_bytes: int | None = None,
    ) -> None:
        self._budget = budget
        self._command = tuple(command) if command is not None else None
        self._timeout_s = timeout_s
        self._supervision_margin_s = (
            supervision_margin_s if supervision_margin_s is not None else _DEFAULT_SUPERVISION_MARGIN_S
        )
        self._max_result_bytes = max_result_bytes if max_result_bytes is not None else MAX_RESULT_BYTES
        self._max_stderr_bytes = max_stderr_bytes if max_stderr_bytes is not None else _MAX_STDERR_BYTES
        self._active: dict[int, subprocess.Popen[bytes]] = {}
        self._residual: tuple[str, ...] = ()
        self._lock = threading.Lock()

    def active_pids(self) -> tuple[int, ...]:
        """当前在途子进程 PID（取消/超时后回收是异步的：worker 线程收尾后清空）。"""

        with self._lock:
            return tuple(self._active)

    def residual_threads(self) -> tuple[str, ...]:
        """最近一次提取收尾时仍存活的管道线程名；空元组表示进程与线程均已真实退出。"""

        with self._lock:
            return self._residual

    async def extract(self, request: AttachmentExtractionRequest) -> AttachmentExtractionResult:
        """在线程池执行同步子进程提取；调用方取消只终止本请求的子进程后 raise。"""

        run = _ActiveRun()
        pending = asyncio.ensure_future(ThreadPoolManager().run_async(TaskType.IO, self._run_sync, request, run))
        try:
            return await pending
        except asyncio.CancelledError:
            # 事件循环线程只做非阻塞 terminate；等待回收由 worker 线程完成（R16）。
            self._cancel_run(run)
            pending.add_done_callback(_consume_future_result)
            raise

    def _cancel_run(self, run: _ActiveRun) -> None:
        with self._lock:
            run.cancelled = True
            process = run.process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                # 进程可能刚退出；终止与管道线程回收仍由 worker 线程的监督循环完成。
                pass

    # ---- 同步实现（线程池内执行） ----

    def _run_sync(self, request: AttachmentExtractionRequest, run: _ActiveRun) -> AttachmentExtractionResult:
        if run.cancelled:
            raise AttachmentWorkerError("attachment_cancelled", "attachment extraction was cancelled")
        self._validate_request(request)
        detected = detect_attachment_kind(request.file_name, request.data)
        if detected is not request.kind:
            raise AttachmentWorkerError(
                "attachment_type_mismatch",
                f"declared kind {request.kind.value!r} does not match content kind {detected.value!r}",
            )
        worker_timeout_s = self._timeout_s if self._timeout_s is not None else self._budget.extract_timeout_s
        header = {
            "schema_version": 1,
            "kind": request.kind.value,
            "selection": to_wire_dict(request.selection),
            "csv_header_confirmed": request.csv_header_confirmed,
            "read_expansion_consent": request.read_expansion_consent,
            "source_digest": request.source_digest,
            "budget": {
                "max_files": self._budget.max_files,
                "max_source_bytes": self._budget.max_source_bytes,
                "max_pages": self._budget.max_pages,
                "max_sheets": self._budget.max_sheets,
                "max_rows": self._budget.max_rows,
                "max_codepoints": self._budget.max_codepoints,
                "extract_timeout_s": self._budget.extract_timeout_s,
            },
            "timeout_s": self._budget.extract_timeout_s,
            "payload_bytes": len(request.data),
        }
        header_bytes = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        # 字节口径：``MAX_HEADER_BYTES`` 只计请求头自身；其后的 ``\n`` 是 framing 分隔符，
        # 不计入限额（worker 侧按同一口径剥离换行后再判定，I36）。
        if len(header_bytes) > MAX_HEADER_BYTES:
            raise AttachmentWorkerError("attachment_request_too_large", "worker header exceeds bounded size")

        command = self._command or (sys.executable, "-m", "services.agent_service.attachment_worker")
        root = Path(__file__).resolve().parents[2]
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(root), env.get("PYTHONPATH"))))
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                list(command),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(root),
                env=env,
            )
        except OSError as exc:
            raise AttachmentWorkerError("worker_unavailable", "attachment worker cannot be started") from exc
        with self._lock:
            run.process = process
            self._active[process.pid] = process
            cancelled_before_spawn = run.cancelled
        if cancelled_before_spawn:
            # 取消发生在登记之前：此处终止，避免留下无人等待的孤儿子进程。
            self._terminate(process)
            with self._lock:
                self._active.pop(process.pid, None)
            raise AttachmentWorkerError("attachment_cancelled", "attachment extraction was cancelled")
        payload = header_bytes + b"\n" + request.data
        # 父侧监督：worker 时限（``worker_timeout_s``）自其读完请求头起算，父侧计时含
        # Popen/启动/回收开销，故以「worker 时限 + 余量」兜底，避免先杀仍在时限内、
        # 已产出部分结果的 worker（I36）。
        supervision_timeout = worker_timeout_s + self._supervision_margin_s
        try:
            # 流式有界接收：超限/取消/超时立即终止子进程，不缓存完整输出后再判大小。
            stdout = self._supervise(process, payload, worker_timeout_s, supervision_timeout, run)
        finally:
            with self._lock:
                self._active.pop(process.pid, None)
        elapsed_s = time.monotonic() - started
        exited = process.poll() is not None
        if not exited:
            self._terminate(process)
            raise AttachmentWorkerError("worker_not_exited", "attachment worker did not exit")
        if not stdout:
            # 无输出时退出码才有意义：worker 正常路径恒以 0 退出（失败经 JSON ``ok:false``
            # 上报），非 0 且无输出即启动/崩溃失败。
            raise AttachmentWorkerError(
                "worker_failed", f"attachment worker exited without result (exit={process.returncode})"
            )
        # 非 0 退出但 stdout 是完整合法结果时不额外拒绝：worker 恒以 0 退出，故本分支只
        # 可能来自异常退出；「非 0 + 完整结果」无冻结合同，保持既有语义——以结果协议为
        # 准，不新造错误码（I36）。
        return self._decode_result(request, stdout, process.pid, elapsed_s)

    def _supervise(
        self,
        process: subprocess.Popen[bytes],
        payload: bytes,
        worker_timeout_s: float,
        supervision_timeout: float,
        run: _ActiveRun,
    ) -> bytes:
        """流式接收 stdout/stderr 并监督子进程；任何异常路径都先终止进程再收尾线程。"""

        if process.stdin is None or process.stdout is None or process.stderr is None:
            self._terminate(process)
            raise AttachmentWorkerError("worker_unavailable", "attachment worker pipes are unavailable")
        stdout_reader = _BoundedPipeReader(process.stdout, self._max_result_bytes, name="attachment-worker-stdout")
        stderr_reader = _BoundedPipeReader(process.stderr, self._max_stderr_bytes, name="attachment-worker-stderr")
        stdin_feeder = _StdinFeeder(process.stdin, payload)
        threads = (stdin_feeder, stdout_reader, stderr_reader)
        started: list[threading.Thread] = []
        failure: AttachmentWorkerError | None = None
        try:
            for thread in threads:
                thread.start()
                started.append(thread)
            deadline = time.monotonic() + supervision_timeout
            while True:
                if run.cancelled:
                    failure = AttachmentWorkerError("attachment_cancelled", "attachment extraction was cancelled")
                    break
                if stdout_reader.overflowed.is_set():
                    # 超限即停：不再继续累积，直接终止子进程（不等待其自然结束）。
                    failure = AttachmentWorkerError("result_too_large", "attachment worker result exceeds bounded size")
                    break
                if stderr_reader.overflowed.is_set():
                    # 只以长度判定异常，不把 stderr 内容带入错误/日志（R9）。
                    failure = AttachmentWorkerError(
                        "worker_stderr_exceeded", "attachment worker stderr exceeds bounded size"
                    )
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    # 超时路径不发布已产出的部分结果（结果不完整，仅报时限）。
                    failure = AttachmentWorkerError(
                        "attachment_time_exceeded",
                        f"attachment extraction exceeded {worker_timeout_s:g} seconds",
                    )
                    break
                try:
                    process.wait(timeout=min(remaining, _SUPERVISION_POLL_S))
                except subprocess.TimeoutExpired:
                    continue
                break
        finally:
            if failure is not None or process.poll() is None:
                try:
                    self._terminate(process)
                except subprocess.TimeoutExpired:
                    # kill 后仍未退出：不在此抛出（避免掩盖原始失败），按残留事实记录。
                    pass
            residual = self._join_threads(started)
            if process.poll() is None:
                # 终止+kill 宽限后仍未退出：作为残留事实显式记录，不静默丢弃。
                residual = (*residual, "attachment-worker-process")
            with self._lock:
                self._residual = residual
            if residual:
                logger.warning("attachment worker pipes did not exit: %s", ", ".join(residual))
        if failure is None:
            if stdout_reader.overflowed.is_set():
                failure = AttachmentWorkerError("result_too_large", "attachment worker result exceeds bounded size")
            elif stderr_reader.overflowed.is_set():
                failure = AttachmentWorkerError(
                    "worker_stderr_exceeded", "attachment worker stderr exceeds bounded size"
                )
            elif stdout_reader.read_error:
                failure = AttachmentWorkerError("worker_protocol_invalid", "attachment worker result is unreadable")
            elif residual:
                # 进程已退出但管道线程未收尾：输出可能不完整，按残留事实失败。
                failure = AttachmentWorkerError(
                    "worker_thread_residual", f"attachment worker threads did not exit: {', '.join(residual)}"
                )
        if failure is not None:
            raise failure
        return stdout_reader.payload()

    def _join_threads(self, threads: Sequence[threading.Thread]) -> tuple[str, ...]:
        """join 管道线程并返回超时未退出的线程名（残留事实）。"""

        residual: list[str] = []
        for thread in threads:
            thread.join(_THREAD_JOIN_GRACE_S)
            if thread.is_alive():
                residual.append(thread.name)
        return tuple(residual)

    def _validate_request(self, request: AttachmentExtractionRequest) -> None:
        for field_name, value in (
            ("user_scope", request.user_scope),
            ("attachment_id", request.attachment_id),
            ("source_id", request.source_id),
            ("source_version", request.source_version),
            ("source_digest", request.source_digest),
        ):
            if not isinstance(value, str) or not value:
                raise AttachmentWorkerError("attachment_request_invalid", f"{field_name} must be a non-empty string")
        if not isinstance(request.data, bytes):
            raise AttachmentWorkerError("attachment_request_invalid", "attachment data must be bytes")
        if not isinstance(request.read_expansion_consent, bool):
            raise AttachmentWorkerError("attachment_request_invalid", "read_expansion_consent must be a bool")
        try:
            self._budget.enforce_source_bytes(len(request.data))
        except AgentValidationError as exc:
            raise AttachmentWorkerError(exc.code, "attachment source exceeds 20,000,000 bytes") from exc

    def _decode_result(
        self,
        request: AttachmentExtractionRequest,
        stdout: bytes,
        pid: int,
        elapsed_s: float,
    ) -> AttachmentExtractionResult:
        try:
            response = json.loads(stdout.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AttachmentWorkerError(
                "worker_protocol_invalid", "attachment worker result is not valid JSON"
            ) from exc
        if not isinstance(response, dict) or response.get("schema_version") != 1:
            raise AttachmentWorkerError("worker_protocol_invalid", "attachment worker result schema is invalid")
        if response.get("ok") is not True:
            error = response.get("error")
            if not isinstance(error, dict):
                raise AttachmentWorkerError("worker_failed", "attachment worker reported an unknown failure")
            code = error.get("code")
            message = error.get("message")
            if not isinstance(code, str) or not isinstance(message, str):
                raise AttachmentWorkerError("worker_protocol_invalid", "attachment worker error shape is invalid")
            raise AttachmentWorkerError(code, "attachment extraction failed")
        result = response.get("result")
        if not isinstance(result, dict):
            raise AttachmentWorkerError("worker_protocol_invalid", "attachment worker result payload is missing")
        return self._build_result(request, result, pid, elapsed_s)

    def _build_result(
        self,
        request: AttachmentExtractionRequest,
        result: dict[str, object],
        pid: int,
        elapsed_s: float,
    ) -> AttachmentExtractionResult:
        expected = {
            "read_state",
            "coverage",
            "body_text",
            "blocks",
            "facts",
            "limitations",
            "stats",
            "read_ranges",
            "unread_ranges",
        }
        if set(result) - {"schema_version"} != expected:
            raise AttachmentWorkerError("worker_protocol_invalid", "attachment worker result keys are invalid")
        try:
            read_state = ReadState(result["read_state"])
            coverage = result["coverage"]
            if not isinstance(coverage, str) or coverage not in {"full", "limited", "unknown"}:
                raise ValueError("coverage")
            body_text = result["body_text"]
            if body_text is not None and not isinstance(body_text, str):
                raise ValueError("body_text")
            blocks_raw = result["blocks"]
            facts_raw = result["facts"]
            limitations_raw = result["limitations"]
            read_raw = result["read_ranges"]
            unread_raw = result["unread_ranges"]
            if (
                not isinstance(blocks_raw, list)
                or not isinstance(facts_raw, list)
                or not isinstance(limitations_raw, list)
            ):
                raise ValueError("list fields")
            if not isinstance(read_raw, list) or not isinstance(unread_raw, list):
                raise ValueError("range fields")
            blocks_list: list[str] = []
            for index, block in enumerate(blocks_raw):
                if not isinstance(block, str):
                    raise ValueError(f"blocks[{index}]")
                decode_json_doc(block)
                blocks_list.append(block)
            blocks = tuple(blocks_list)
            facts = tuple(decode_fact_value(fact, f"$.result.facts[{index}]") for index, fact in enumerate(facts_raw))
            stats_raw = result["stats"]
            if not isinstance(stats_raw, dict):
                raise ValueError("stats")
            # 父进程按 §3.3.1 严格复核：all/limited 归属、同轴、包含与互斥重叠。
            selected_range = decode_selected_range(
                {
                    "selection": to_wire_dict(request.selection),
                    "read_ranges": read_raw,
                    "unread_ranges": unread_raw,
                    "stats": stats_raw,
                    "limitations": limitations_raw,
                    "accepted_limited_version": None,
                },
                "$.result.selected_range",
            )
        except AgentValidationError as exc:
            raise AttachmentWorkerError("worker_protocol_invalid", f"attachment result is invalid: {exc.code}") from exc
        except (TypeError, ValueError) as exc:
            raise AttachmentWorkerError("worker_protocol_invalid", "attachment result shape is invalid") from exc
        return AttachmentExtractionResult(
            source=SourceVersionRef(
                source_kind=SourceKind.ATTACHMENT,
                source_id=request.source_id,
                source_version=request.source_version,
                source_digest=request.source_digest,
            ),
            selected_range=selected_range,
            read_state=read_state,
            coverage=coverage,
            body_text=body_text,
            blocks=blocks,
            facts=facts,
            limitations=selected_range.limitations,
            stats=selected_range.stats,
            worker_pid=pid,
            worker_exited=True,
            elapsed_s=elapsed_s,
        )

    # ---- 子进程收尾 ----

    def _terminate(self, process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(_KILL_GRACE_S)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(_KILL_GRACE_S)
