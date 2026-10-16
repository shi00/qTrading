from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.util
import itertools
import logging
import multiprocessing
import os
import queue
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any

from utils.config_handler import ConfigHandler
from utils.error_classifier import log_classified
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.loop_local import del_loop_local, get_loop_local
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager

if TYPE_CHECKING:
    # 仅类型标注消费（运行时禁止导入：services.ai_service 组合根反向依赖本模块，
    # 顶层导入会成环）；R1 import-linter 豁免 TYPE_CHECKING 块。
    from services.ai_service.model_management_types import (
        AdapterResponse,
        AttemptContext,
        InferenceRequest,
        ResolvedConnection,
    )

logger = logging.getLogger(__name__)


class LocalInferenceTimeoutError(RuntimeError):
    """Raised when local model inference exceeds the configured timeout."""

    pass


_SENTINEL = "__SHUTDOWN__"
_VERIFICATION_TIMEOUT_SECONDS = 300

# invoke_for_connection 的默认推理参数（与 litellm_client.DEFAULT_LOCAL_MAX_TOKENS 对齐）。
_DEFAULT_INVOKE_MAX_TOKENS = 256
_DEFAULT_INVOKE_TEMPERATURE = 0.7
_DEFAULT_INVOKE_SYSTEM_PROMPT = "You are a helpful assistant."
_DEFAULT_INVOKE_TIMEOUT_S = 90.0


def _model_tag_of(model_path: str) -> str:
    """模型文件 stem 截断 ≤32 字符（GGUF 溯源标识统一口径，对抗轮 H 单点）。

    与 NewsSentimentScorer.model_tag 共用：``market_news.sentiment_model``
    String(32) 及 AdapterResponse.actual_model 均经本函数产出。
    """
    return Path(model_path).stem[:32]


def _response_generation(result: tuple) -> int | None:
    """S2 响应协议的 generation 字段统一位于下标 2。

    全部 worker 响应形态（ok/error 请求级 / ready / error 加载级 / shutdown）
    的第 3 个元素均为 worker_generation；缺失或非 int（含 bool）返回 None，
    由调用方按「协议坏 / 旧协议残留」处理，不参与 generation 过滤。
    """
    if len(result) >= 3:
        gen = result[2]
        if isinstance(gen, int) and not isinstance(gen, bool):
            return gen
    return None


# 受支持的本地模型文件魔数白名单（小端 32 位整数视角即 0x46554747）。
# 当前唯一后端 llama.cpp（llama_cpp.Llama）仅支持 GGUF；若未来引入支持
# 其他格式（ONNX/safetensors 等）的后端，在此登记其魔数并配套新增后端加载
# 分支即可，本校验逻辑无需改动。
_MODEL_FILE_MAGICS: tuple[bytes, ...] = (b"GGUF",)


def _validate_model_file(model_path: str) -> bool:
    """轻量校验本地模型文件（review 06 F14）。

    核对三项：路径规范化、存在性、受支持格式 magic header（前 4 字节须命中
    ``_MODEL_FILE_MAGICS``）。

    以魔数而非 ``.gguf`` 后缀为准：当前后端 llama.cpp 唯一支持的格式就是
    GGUF，故魔数校验既能拒绝"伪装成模型的其他文件"，又不会误伤"文件名非
    .gguf 但内容为真 GGUF"的模型。
    仅做防御性轻量校验，不做目录白名单（用户将模型放何处是他的自由）。

    Args:
        model_path: 用户在 UI 中选择的模型文件路径。

    Returns:
        文件通过校验返回 True，否则 False。
    """
    try:
        path = Path(model_path).resolve()
    except (OSError, ValueError, RuntimeError):
        return False
    if not path.is_file():
        return False
    try:
        with path.open("rb") as f:
            if f.read(4) not in _MODEL_FILE_MAGICS:
                return False
    except OSError:
        return False
    return True


def _persistent_worker(  # pragma: no cover — runs in subprocess, not coverable by unit tests
    model_path: str,
    config: dict,
    worker_generation: int,
    request_queue: multiprocessing.Queue,
    result_queue: multiprocessing.Queue,
):
    """Persistent subprocess worker that loads the model once and serves requests.

    Communication protocol (S2 单连接执行，响应统一携带 worker_generation）：
    - Parent sends a tuple via request_queue:
        (prompt, max_tokens, temperature, system_prompt, req_id)  5 元组
    - Worker puts result via result_queue:
        ("ok", req_id, worker_generation, output_text)
        ("error", req_id, worker_generation, error_message)
    - Parent sends _SENTINEL to request graceful shutdown:
        ("shutdown", "ok", worker_generation)
    - On model load failure, worker puts ("error", error_message, worker_generation) and exits.
    - Invalid request format → ("error", -1, worker_generation, msg)（rid=-1 广播）。
    """
    try:
        llama_cpp_module = importlib.import_module("llama_cpp")
        Llama = llama_cpp_module.Llama
        llama_types_module = importlib.import_module("llama_cpp.llama_types")
        ChatCompletionRequestSystemMessage = llama_types_module.ChatCompletionRequestSystemMessage
        ChatCompletionRequestUserMessage = llama_types_module.ChatCompletionRequestUserMessage
    except (ImportError, AttributeError) as e:
        result_queue.put(("error", f"llama-cpp-python import failed: {e}\n{traceback.format_exc()}", worker_generation))
        return

    try:
        llm = Llama(
            model_path=model_path,
            n_threads=config.get("n_threads", 4),
            n_batch=config.get("n_batch", 1024),
            n_ctx=config.get("n_ctx", 4096),
            n_gpu_layers=config.get("n_gpu_layers", 0),
            flash_attn=config.get("flash_attn", True),
            verbose=False,
        )
        result_queue.put(("ready", model_path, worker_generation))
    except Exception as e:
        sanitized = DataSanitizer.sanitize_error(e)
        log_classified(
            logger,
            e,
            "general",
            "[LocalModel] Model load failed (%s): %s",
            exc_info=True,
        )
        result_queue.put(("error", f"Model load failed: {sanitized}\n{traceback.format_exc()}", worker_generation))
        return

    while True:
        try:
            request = request_queue.get(timeout=1.0)
        except queue.Empty:
            continue
        except Exception as e:
            log_classified(
                logger,
                e,
                "general",
                "[LocalModel] Worker queue get error (%s): %s",
                exc_info=True,
            )
            continue

        if request == _SENTINEL:
            result_queue.put(("shutdown", "ok", worker_generation))
            break

        if not isinstance(request, tuple) or len(request) != 5:
            result_queue.put(("error", -1, worker_generation, f"Invalid request format: {type(request)}"))
            continue

        prompt, max_tokens, temperature, system_prompt, req_id = request
        try:
            messages = [
                ChatCompletionRequestSystemMessage(role="system", content=system_prompt),
                ChatCompletionRequestUserMessage(role="user", content=prompt),
            ]

            stream = llm.create_chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
            )

            collected_content: list[str] = []
            for chunk in stream:
                choices = chunk.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        collected_content.append(content)

            output = "".join(collected_content)
            result_queue.put(("ok", req_id, worker_generation, output))
        except Exception as e:
            sanitized = DataSanitizer.sanitize_error(e)
            log_classified(
                logger,
                e,
                "general",
                "[LocalModel] Worker inference failed (%s): %s",
                exc_info=True,
            )
            result_queue.put(("error", req_id, worker_generation, f"{sanitized}\n{traceback.format_exc()}"))


def _probe_llama_cpp() -> bool:
    """探测 llama_cpp 是否可用，仅解析模块元数据而不加载模块（PRF-04）。

    1. 优先复用 sys.modules（兼容已导入模块及测试中的 Mock 替身，规避无 __spec__ 时的 ValueError）；
    2. 若 sys.modules 中显式为 None（Python 标准的不可导入标记），返回 False；
    3. 否则通过 importlib.util.find_spec 探测元数据，防御常见导入异常。
    """
    mod = sys.modules.get("llama_cpp")
    if mod is not None:
        return True
    if "llama_cpp" in sys.modules:
        return False
    try:
        return importlib.util.find_spec("llama_cpp") is not None
    except (ImportError, AttributeError, ValueError):
        return False


# PRF-04: 用 find_spec 代替 import_module 探测 llama_cpp——find_spec 仅解析模块
# 元数据（ModuleSpec），不会真正加载模块；仅当其已安装时返回非 None。真正触发
# 加载发生在 _persistent_worker 内的运行时 import，此处仅做可用性判定。
_HAS_LLAMA_CPP = _probe_llama_cpp()
if not _HAS_LLAMA_CPP:
    logger.warning(
        "llama-cpp-python not installed. Embedded AI features will be disabled.",
    )


from utils.singleton_registry import register_singleton


@register_singleton
class LocalModelManager:
    """
    Manages the lifecycle of the embedded Llama.cpp model via subprocess isolation.

    The model runs in a dedicated subprocess (_persistent_worker). On timeout,
    the subprocess is terminated (process.terminate()), guaranteeing that all
    native memory (mmap, CUDA buffers) is reclaimed by the OS. This eliminates
    the segfault risk that existed when inference ran in an in-process thread.
    """

    _instance: LocalModelManager | None = None
    _initialized: bool = False
    # RLock 允许 get_instance 持锁后调用 LocalModelManager() 时 __new__ 重入同锁不死锁
    _lock = threading.RLock()

    def __new__(cls, *args, **kwargs):
        # §4.3 双检锁：先无锁检查，再加锁检查。RLock 允许 get_instance 持锁后重入。
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    @classmethod
    def _reset_singleton(cls):
        """Reset singleton for testing only. NEVER call in production."""
        with cls._lock:
            inst = cls._instance
            if inst is not None:
                try:
                    inst._cancel_cleanup_task()
                    inst._shutdown_worker()
                except Exception as e:
                    log_classified(
                        logger,
                        e,
                        "general",
                        "[LocalModel] Error during reset shutdown (%s): %s",
                        exc_info=True,
                    )
            cls._instance = None
            cls._initialized = False
        del_loop_local("local_load_lock")

    @classmethod
    def _atexit_cleanup(cls):
        """Release subprocess and queues on process exit. Called by singleton_registry."""
        inst = cls._instance
        if inst is not None:
            try:
                inst._cancel_cleanup_task()
                inst._shutdown_worker()
            except Exception as e:
                log_classified(
                    logger,
                    e,
                    "general",
                    "[LocalModel] Error during atexit shutdown (%s): %s",
                    exc_info=True,
                )

    @classmethod
    def _get_load_lock(cls):
        """Get or create load lock dynamically per event loop to avoid cross-loop binding deadlocks."""

        def _factory():
            return asyncio.Lock()

        return get_loop_local("local_load_lock", _factory)

    @classmethod
    async def get_instance(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = LocalModelManager()
                cls._instance._initialized = True
        return cls._instance

    def __init__(self, *, clock=None):
        # CON-01: double-checked locking。__new__ 持锁创建实例，但 __init__ 在锁外执行会
        # 导致并发首次访问时两线程都见 _initialized=False 而重复初始化。初始化整体移入
        # 锁内并二次检查，保证恰好执行一次。RLock 允许 get_instance 持锁后重入 __init__。
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            self._model_path: str = ""
            self._model_sha256: str = ""
            self._model_stat: tuple = (0, 0)
            self._last_config: dict = {}
            self._is_loading: bool = False
            self._cancel_event: threading.Event = threading.Event()
            self._worker_proc: multiprocessing.Process | None = None
            self._request_queue: multiprocessing.Queue | None = None
            self._result_queue: multiprocessing.Queue | None = None
            self._worker_ready: bool = False
            self._worker_lock: threading.Lock = threading.Lock()
            self._verification_mode: bool = False
            self._verification_start_time: float = 0.0
            self._clock = clock or time.monotonic
            # S2 单连接执行状态（ADR-0014 §6.1 invoke_for_connection 与 legacy 共享底座）：
            self._worker_generation: int = 0  # 每次 spawn 递增；req_id 计数器跨 generation 不重置
            self._worker_state: str = "stopped"  # idle / busy / quarantined / stopped
            self._inflight: dict[int, tuple[str, str]] = {}  # req_id → (invocation_id, attempt_id)
            self._req_counter = itertools.count()
            self._cleanup_task: asyncio.Task | None = None
            self._cleanup_target_generation: int = -1
            self._initialized = True

    def _shutdown_worker(self, *, force: bool = False, generation: int | None = None):
        """Shut down the persistent worker subprocess (thread-safe).

        Args:
            force: True 时跳过 sentinel 直接 terminate。用于关机路径——
                   worker 可能正忙于推理，sentinel 要等推理完成才会被处理。
            generation: 非 None 时仅当与当前 _worker_generation 一致才执行
                   （quarantined 清理任务的 generation 守卫，防误杀后继 worker）。
        """
        with self._worker_lock:
            self._shutdown_worker_locked(force=force, generation=generation)

    def _shutdown_worker_locked(self, *, force: bool = False, generation: int | None = None):
        """Internal: shut down worker. Caller MUST hold _worker_lock.

        Args:
            force: True 时跳过 sentinel 直接 terminate（关机路径使用）。
            generation: 非 None 时与当前 _worker_generation 不一致则幂等退出
                   （不 shutdown、不改状态）。
        """
        if generation is not None and generation != self._worker_generation:
            return
        if self._request_queue is not None and self._worker_proc is not None and self._worker_proc.is_alive():
            if not force:
                try:
                    self._request_queue.put(_SENTINEL, timeout=2.0)
                except Exception as e:
                    log_classified(
                        logger,
                        e,
                        "general",
                        "[LocalModel] Failed to send sentinel to worker (%s): %s",
                        exc_info=True,
                    )
                self._worker_proc.join(timeout=5)
                if self._worker_proc.is_alive():
                    self._worker_proc.terminate()
                    self._worker_proc.join(timeout=3)
                    if self._worker_proc.is_alive():
                        self._worker_proc.kill()
                        self._worker_proc.join(timeout=2)
            else:
                # Force path: worker 可能正忙于推理，sentinel 不会被及时处理。
                # 直接 terminate 释放资源（关机路径，推理结果将丢弃）。
                # terminate() 在 Windows 上是 TerminateProcess（同步立即返回），
                # 在 Linux 上是 SIGTERM；join 在进程退出后立即返回。
                # 超时预算 1.5+0.5=2.0s 适配 Step 5 的 2.0s 步超时。
                self._worker_proc.terminate()
                self._worker_proc.join(timeout=1.5)
                if self._worker_proc.is_alive():
                    self._worker_proc.kill()
                    self._worker_proc.join(timeout=0.5)
            logger.info("[LocalModel] Persistent worker shut down.")

        if self._request_queue is not None:
            try:
                self._request_queue.close()
                self._request_queue.join_thread()
            except Exception as e:
                log_classified(
                    logger,
                    e,
                    "general",
                    "[LocalModel] Failed to close request queue (%s): %s",
                    exc_info=True,
                )
        if self._result_queue is not None:
            try:
                self._result_queue.close()
                self._result_queue.join_thread()
            except Exception as e:
                log_classified(
                    logger,
                    e,
                    "general",
                    "[LocalModel] Failed to close result queue (%s): %s",
                    exc_info=True,
                )

        self._worker_proc = None
        self._request_queue = None
        self._result_queue = None
        self._worker_ready = False
        self._worker_state = "stopped"
        self._inflight.clear()

    async def _ensure_worker(self, model_path: str, core_config: dict) -> bool:
        """Start a persistent worker subprocess with the given model.

        Returns True if worker process was started, False on failure.
        Thread-safe: protected by _worker_lock.
        Call _await_worker_ready() after this to wait for model loading.

        F4-S-1: 整个函数体 offload 到线程池，因为 _shutdown_worker_locked 含
        process.join(timeout) 同步阻塞（最坏 ~12s），不能在事件循环线程执行 (R16)。
        """
        return await ThreadPoolManager().run_async(TaskType.IO, self._ensure_worker_impl, model_path, core_config)

    def _ensure_worker_impl(self, model_path: str, core_config: dict) -> bool:
        """_ensure_worker 的同步实现，在线程池中执行。"""
        with self._worker_lock:
            if (
                self._worker_proc is not None
                and self._worker_proc.is_alive()
                and self._worker_ready
                and self._model_path == model_path
                # quarantined：worker 状态未知（取消/超时后可能仍在推理），
                # 快路径失效，走完整 shutdown+respawn 自愈（对抗轮 E-1）。
                and self._worker_state != "quarantined"
            ):
                return True

            self._shutdown_worker_locked()
            # generation 每次 spawn 递增；旧响应经 _response_generation 过滤丢弃
            # （计数器 _req_counter 跨 generation 不重置，对抗轮 C）。
            self._worker_generation += 1
            generation = self._worker_generation

            self._request_queue = multiprocessing.Queue()
            self._result_queue = multiprocessing.Queue()

            proc = multiprocessing.Process(
                target=_persistent_worker,
                args=(model_path, core_config, generation, self._request_queue, self._result_queue),
                daemon=True,
            )
            proc.start()
            self._worker_proc = proc
            logger.info("[LocalModel] Persistent worker started. pid=%s generation=%s", proc.pid, generation)
            return True

    @log_async_operation(threshold_ms=PerfThreshold.AI_INFERENCE)
    async def _await_worker_ready(self, timeout: float = 180.0, *, check_cancel_event: bool = True) -> bool:
        """Wait for the persistent worker to become ready without blocking the event loop.

        Uses a polling approach (like run_inference) so we can detect a crashed
        subprocess early instead of blocking on result_queue.get() for the full timeout.

        Args:
            check_cancel_event: legacy 链（run_inference 语义）默认 True；统一链
                invoke_for_connection 传 False——``_cancel_event`` 为 legacy 全局
                取消语义（unload_model 后永久 set，仅 legacy 加载链 clear），
                统一链不消费该状态，否则每次 respawn 的 ready 等待都会被历史
                unload 误杀（与 _dispatch_and_collect 的对抗轮 B-3 同源）。
        """
        result_queue = self._result_queue
        if result_queue is None:
            logger.error("[LocalModel] Result queue not initialized.")
            return False

        result = None
        worker_died = False
        deadline = asyncio.get_running_loop().time() + timeout

        while True:
            if check_cancel_event and self._cancel_event.is_set():
                logger.info("[LocalModel] Cancel event detected while waiting for worker ready.")
                self._worker_ready = False
                # F4-S-1: offload 到线程池避免阻塞事件循环 (R16)
                await ThreadPoolManager().run_async(TaskType.IO, self._shutdown_worker)
                return False

            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break

            # Non-blocking check for result (use captured ref to avoid TOCTOU race)
            try:
                result = result_queue.get_nowait()
            except queue.Empty:
                result = None
            except OSError:
                logger.warning("[LocalModel] OSError reading result queue while waiting for worker ready.")
                result = None

            # S2: generation 过滤——旧 worker 的残留响应直接丢弃继续等待。
            if result is not None:
                gen = _response_generation(result) if isinstance(result, tuple) else None
                if gen is not None and gen != self._worker_generation:
                    logger.warning(
                        "[LocalModel] Discarding stale-generation response while waiting for ready (gen=%s).", gen
                    )
                    result = None
            if result is not None:
                break

            # Detect subprocess crash early (capture proc locally to avoid TOCTOU race)
            proc = self._worker_proc
            if proc is not None and not proc.is_alive():
                # One last attempt to read any residual result
                try:
                    result = result_queue.get_nowait()
                except (queue.Empty, OSError):
                    result = None
                if result is not None:
                    gen = _response_generation(result) if isinstance(result, tuple) else None
                    if gen is not None and gen != self._worker_generation:
                        logger.warning(
                            "[LocalModel] Discarding stale-generation response while waiting for ready (gen=%s).",
                            gen,
                        )
                        result = None
                worker_died = True
                break

            await asyncio.sleep(min(0.5, remaining))

        # Final drain attempt
        if result is None:
            try:
                result = result_queue.get_nowait()
            except (queue.Empty, OSError):
                pass

        if result is None:
            self._worker_ready = False
            if worker_died:
                exitcode = self._worker_proc.exitcode if self._worker_proc is not None else None
                logger.error("[LocalModel] Persistent worker crashed before ready. exitcode=%s", exitcode)
            else:
                logger.error("[LocalModel] Persistent worker failed to become ready within %ss.", timeout)
            # F4-S-1: offload 到线程池避免阻塞事件循环 (R16)
            await ThreadPoolManager().run_async(TaskType.IO, self._shutdown_worker)
            return False

        if not (isinstance(result, tuple) and len(result) == 3):
            logger.error("[LocalModel] Malformed result while waiting for worker ready: %s", type(result))
            self._worker_ready = False
            # F4-S-1: offload 到线程池避免阻塞事件循环 (R16)
            await ThreadPoolManager().run_async(TaskType.IO, self._shutdown_worker)
            return False

        status, payload = result[0], result[1]
        if status == "ready":
            self._worker_ready = True
            self._worker_state = "idle"
            logger.info("[LocalModel] Persistent worker ready with model: %s", payload)
            return True

        # R9: payload 来自子进程 result_queue，可能含 traceback.format_exc()（模型路径），
        # 经 sanitize_error 脱敏后再记录（与子进程内 sanitize_error 模式一致）
        logger.error("[LocalModel] Persistent worker failed: %s", DataSanitizer.sanitize_error(payload))
        self._worker_ready = False
        # F4-S-1: offload 到线程池避免阻塞事件循环 (R16)
        await ThreadPoolManager().run_async(TaskType.IO, self._shutdown_worker)
        return False

    def get_loaded_model_path(self) -> str:
        """Return the path of the currently loaded model, or empty string if none."""
        return self._model_path

    def get_loaded_model_sha256(self) -> str:
        """Return the SHA-256 hash of the currently loaded model, or empty string if none."""
        return self._model_sha256

    def get_loaded_model_md5(self) -> str:
        """Backward-compatible alias for get_loaded_model_sha256."""
        return self.get_loaded_model_sha256()

    @staticmethod
    def calculate_file_sha256(file_path: str) -> str:
        """
        Calculate SHA-256 hash of a file. Runs synchronously, should be called from thread pool.
        Uses chunked reading to handle large files efficiently.
        """
        hash_sha256 = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192 * 1024), b""):
                    hash_sha256.update(chunk)
            return hash_sha256.hexdigest()
        except Exception as e:
            log_classified(
                logger,
                e,
                "general",
                "[LocalModel] Failed to calculate SHA-256 (%s): %s",
                exc_info=True,
            )
            return ""

    @staticmethod
    def calculate_file_md5(file_path: str) -> str:
        """Backward-compatible alias for calculate_file_sha256."""
        return LocalModelManager.calculate_file_sha256(file_path)

    @log_async_operation(threshold_ms=PerfThreshold.AI_INFERENCE)
    async def load_model(
        self,
        model_path: str,
        config: dict[str, Any] | None = None,
        is_verification: bool = False,
    ) -> bool:
        """
        Load the model by starting a persistent subprocess worker.

        The model is loaded inside the subprocess, not in the main process.
        This ensures that on timeout, process.terminate() cleanly reclaims
        all native memory without segfault risk.
        """
        if not _HAS_LLAMA_CPP:
            logger.error("Cannot load model: llama-cpp-python is not installed.")
            return False

        if not _validate_model_file(model_path):
            logger.error("Invalid GGUF model file (missing, wrong type, or bad magic header): %s", model_path)
            return False

        if is_verification:
            self._verification_mode = True
            self._verification_start_time = self._clock()
            logger.info("[LocalModel] Entering verification mode for: %s", model_path)

        try:
            if config is None:
                config = ConfigHandler.get_local_ai_config()

            # Resolve load timeout: prefer panel's "timeout", then persisted "local_model_timeout", default 180s
            raw_timeout = config.get("timeout")
            if raw_timeout is None:
                raw_timeout = config.get("local_model_timeout")
            if raw_timeout is None:
                raw_timeout = 180
            load_timeout = max(1, min(int(raw_timeout), 3600))

            core_config = {
                "n_threads": config.get("n_threads", 4),
                "n_batch": config.get("n_batch", 1024),
                "n_ctx": config.get("n_ctx", 4096),
                "n_gpu_layers": config.get("n_gpu_layers", 0),
                "flash_attn": config.get("flash_attn", True),
            }

            async with self._get_load_lock():
                try:
                    stat = os.stat(model_path)
                    current_stat = (stat.st_mtime, stat.st_size)
                except OSError:
                    current_stat = (0, 0)

                if (
                    self._worker_ready
                    and self._model_path == model_path
                    and self._model_stat == current_stat
                    and self._last_config == core_config
                ):
                    return True

                logger.info(
                    "[LocalModel] Loading model from %s (Stat: %s)...",
                    model_path,
                    current_stat,
                )

                self._is_loading = True
                self._cancel_event.clear()
                start_time = asyncio.get_running_loop().time()

                try:
                    logger.info("[LocalModel] Verifying file integrity (SHA-256)...")
                    target_sha256 = await ThreadPoolManager().run_async(
                        TaskType.IO,
                        self.calculate_file_sha256,
                        model_path,
                    )

                    # Integrity check: compare with stored SHA-256 if available for this path
                    try:
                        stored_path = ConfigHandler.get_typed("local_model_sha256_path", str, "")
                        stored_sha256 = ConfigHandler.get_typed("local_model_sha256", str, "")
                    except (ValueError, OSError, RuntimeError):
                        stored_path = ""
                        stored_sha256 = ""

                    if stored_path == model_path and stored_sha256 and target_sha256 and stored_sha256 != target_sha256:
                        raise RuntimeError(
                            f"Model file integrity check failed: SHA-256 mismatch for {model_path}",
                        )

                    logger.info("[LocalModel] Starting persistent subprocess worker...")
                    if not await self._ensure_worker(model_path, core_config):
                        return False
                    if not await self._await_worker_ready(timeout=float(load_timeout)):
                        return False

                    self._model_path = model_path
                    self._model_sha256 = target_sha256
                    self._model_stat = current_stat
                    self._last_config = core_config

                    # Persist SHA-256 for future integrity verification
                    if target_sha256:
                        try:
                            ConfigHandler.save_config(
                                {
                                    "local_model_sha256": target_sha256,
                                    "local_model_sha256_path": model_path,
                                }
                            )
                        except (ValueError, OSError, RuntimeError) as e:
                            logger.warning(
                                "[LocalModel] Failed to persist model SHA-256: %s",
                                DataSanitizer.sanitize_error(e),
                                exc_info=True,
                            )

                    elapsed = asyncio.get_running_loop().time() - start_time
                    logger.info(
                        "[LocalModel] Model loaded via subprocess in %.2fs.",
                        elapsed,
                    )
                    return True
                except Exception as e:
                    self._model_path = ""
                    self._model_stat = (0, 0)
                    self._last_config = {}
                    log_classified(
                        logger,
                        e,
                        "general",
                        "[LocalModel] Failed to load model (%s): %s",
                        exc_info=True,
                    )
                    return False
                finally:
                    self._is_loading = False
        finally:
            if is_verification and self._model_path != model_path:
                self._verification_mode = False
                self._verification_start_time = 0.0
                logger.warning("[LocalModel] Verification failed, exiting verification mode.")

    @log_async_operation(threshold_ms=PerfThreshold.AI_INFERENCE)
    async def run_inference(
        self,
        prompt: str,
        max_tokens: int = 150,
        temperature: float = 0.7,
        system_prompt: str = "You are a helpful assistant.",
    ) -> str:
        """
        Run inference using a persistent subprocess worker.

        The model is loaded once in the worker and reused across calls.
        On timeout, the worker is terminated and restarted on next call,
        ensuring all native memory (mmap, CUDA) is reclaimed by the OS.

        Raises:
            RuntimeError: If model cannot be loaded or inference fails.
            ImportError: If llama-cpp-python is missing.
            LocalInferenceTimeoutError: If inference exceeds the configured timeout.
        """
        if not _HAS_LLAMA_CPP:
            raise ImportError("llama-cpp-python not installed.")

        config = ConfigHandler.get_local_ai_config()
        path = config.get("local_model_path", "")

        if not path:
            raise RuntimeError("Model not configured (no path set).")

        if self._verification_mode:
            elapsed = self._clock() - self._verification_start_time
            if elapsed < _VERIFICATION_TIMEOUT_SECONDS:
                raise RuntimeError(
                    f"Local inference blocked: model verification in progress "
                    f"({elapsed:.0f}s elapsed). Falling back to cloud."
                )
            logger.warning(
                "[LocalModel] Verification timed out after %ds, auto-cancelling.",
                _VERIFICATION_TIMEOUT_SECONDS,
            )
            # F4-S-1: cancel_verification → unload_model → _shutdown_worker 含 join timeout (~12s)，
            # offload 到线程池避免阻塞事件循环 (R16 举一反三)
            await ThreadPoolManager().run_async(TaskType.IO, self.cancel_verification)

        core_config = {
            "n_threads": config.get("n_threads", 4),
            "n_batch": config.get("n_batch", 1024),
            "n_ctx": config.get("n_ctx", 4096),
            "n_gpu_layers": config.get("n_gpu_layers", 0),
            "flash_attn": config.get("flash_attn", True),
        }

        if not await self._ensure_worker(path, core_config):
            raise RuntimeError("Persistent worker failed to start. Check logs for details.")

        if not self._worker_ready:
            if not await self._await_worker_ready():
                raise RuntimeError("Persistent worker failed to become ready. Check logs for details.")

        timeout_val = config.get("local_model_timeout", 90) or 90

        logger.info(
            "[LocalModel] Sending inference request to persistent worker. "
            "Input len: %s, Max tokens: %s, "
            "Temp: %s, Timeout: %ss",
            len(prompt),
            max_tokens,
            temperature,
            timeout_val,
        )
        start_time = asyncio.get_running_loop().time()
        # S2: legacy 链亦走 5-tuple 协议（req_id 响应配对）；attempt 绑定用
        # ("legacy", "0") 占位（attempt_context 归属仅 S3 Executor 语义需要）。
        req_id = next(self._req_counter)

        try:
            output = await self._dispatch_and_collect(
                req_id=req_id,
                generation=self._worker_generation,
                request_payload=(prompt, max_tokens, temperature, system_prompt, req_id),
                poll_budget=float(timeout_val),
                invocation_id="legacy",
                attempt_id="0",
                should_abort=self._cancel_event.is_set,
            )
        except TimeoutError as te:
            elapsed = asyncio.get_running_loop().time() - start_time
            logger.error(
                "[LocalModel] Persistent worker inference timed out after %ss (elapsed: %.1fs). Terminating worker.",
                timeout_val,
                elapsed,
            )
            # F4-S-1: offload 到线程池避免阻塞事件循环 (R16)
            await ThreadPoolManager().run_async(TaskType.IO, self._shutdown_worker)
            raise LocalInferenceTimeoutError(
                f"Local inference timed out ({timeout_val}s). Worker terminated, will restart on next call.",
            ) from te

        elapsed = asyncio.get_running_loop().time() - start_time
        logger.info(
            "[LocalModel] Persistent worker inference completed in %.2fs. Output len: %s",
            elapsed,
            len(output),
        )
        return output

    # ------------------------------------------------------------------ #
    # S2 单连接执行共享底座（legacy run_inference 与 invoke_for_connection）
    # ------------------------------------------------------------------ #
    async def _dispatch_and_collect(
        self,
        *,
        req_id: int,
        generation: int,
        request_payload: tuple,
        poll_budget: float,
        invocation_id: str,
        attempt_id: str,
        should_abort=None,
    ) -> str:
        """提交 5-tuple 请求并轮询配对响应（单点：submit + poll + 配对）。

        - req_id 进 ``_inflight``（绑定 invocation_id/attempt_id）；提交前注册，
          终态（发送失败/取消/超时/崩溃/成功消费/匹配 error）时移除；
        - 响应消费经 :meth:`_consume_inference_result` 单点（generation 过滤 +
          inflight 校验）；
        - ``should_abort`` 为 legacy 取消钩子（unload_model 语义）；统一链
          （invoke_for_connection）不复用 ``_cancel_event``（其 unload 后永久
          set，复用会误杀后续请求，对抗轮 B-3）。

        Raises:
            RuntimeError: 发送失败 / worker 取消 / 崩溃 / 匹配 error / 协议坏。
            TimeoutError: 轮询预算耗尽（调用方决定是否终止 worker）。
        """
        with self._worker_lock:
            self._inflight[req_id] = (invocation_id, attempt_id)
            self._worker_state = "busy"

        loop = asyncio.get_running_loop()
        try:
            request_queue = self._request_queue
            if request_queue is None:
                raise RuntimeError("Request queue not initialized")
            await loop.run_in_executor(
                None,
                lambda: request_queue.put(request_payload, timeout=5),
            )
        except asyncio.CancelledError:
            # 取消：req_id 终态（docstring 终态表），出站注册即移除
            # （迟到响应由 req_id 单调递增保证被 rid 配对丢弃）。
            with self._worker_lock:
                self._inflight.pop(req_id, None)
            raise
        except Exception as e:
            sanitized = DataSanitizer.sanitize_error(e)
            log_classified(
                logger,
                e,
                "general",
                "[LocalModel] Failed to send request to worker (%s): %s",
                exc_info=True,
            )
            self._worker_ready = False
            with self._worker_lock:
                self._inflight.pop(req_id, None)
            raise RuntimeError(f"Failed to send request to worker: {sanitized}") from e

        result_queue = self._result_queue
        if result_queue is None:
            with self._worker_lock:
                self._inflight.pop(req_id, None)
            raise RuntimeError("Result queue not initialized")

        poll_deadline = loop.time() + poll_budget
        while True:
            if should_abort is not None and should_abort():
                logger.info("[LocalModel] Cancel event detected during inference polling, aborting.")
                with self._worker_lock:
                    self._inflight.pop(req_id, None)
                # F4-S-1: offload 到线程池避免阻塞事件循环 (R16)
                await ThreadPoolManager().run_async(TaskType.IO, self._shutdown_worker)
                raise RuntimeError("Inference cancelled by user (unload_model called).")

            remaining = poll_deadline - loop.time()
            if remaining <= 0:
                with self._worker_lock:
                    self._inflight.pop(req_id, None)
                raise TimeoutError()

            try:
                result: object = result_queue.get_nowait()
            except queue.Empty:
                result = None
            except (OSError, ValueError):
                # ValueError：队列被 close()+join_thread() 后 get_nowait 的实测行为。
                logger.warning("[LocalModel] OSError/ValueError reading result queue during inference.")
                result = None

            if result is not None and isinstance(result, tuple):
                gen = _response_generation(result)
                if gen is not None and gen != generation:
                    logger.warning("[LocalModel] Discarding stale-generation response during inference (gen=%s).", gen)
                else:
                    output = self._consume_inference_result(req_id, result, generation=generation)
                    if output is not None:
                        return output

            # Capture proc locally to avoid TOCTOU race
            proc = self._worker_proc
            if proc is not None and not proc.is_alive():
                try:
                    result = result_queue.get_nowait()
                except (queue.Empty, OSError, ValueError):
                    result = None
                if result is not None and isinstance(result, tuple):
                    gen = _response_generation(result)
                    if gen is None or gen == generation:
                        output = self._consume_inference_result(req_id, result, generation=generation)
                        if output is not None:
                            return output
                # worker 已退出：ready 标志必须清除，否则下次调用走快路径
                # 对已死 worker 发请求（发送失败兜底，但状态先行失真）。
                self._worker_ready = False
                with self._worker_lock:
                    self._inflight.pop(req_id, None)
                raise RuntimeError("Inference worker exited without producing a result.")

            # R2: except Exception 不吞 CancelledError；sleep 被取消时直接传播，
            # req_id 按终态语义移除出站注册（docstring 终态表）。
            try:
                await asyncio.sleep(min(0.2, remaining))
            except asyncio.CancelledError:
                with self._worker_lock:
                    self._inflight.pop(req_id, None)
                raise

    def _consume_inference_result(self, req_id: int, result: tuple, *, generation: int) -> str | None:
        """单点解析推理响应（generation 配对 + _inflight 绑定，对抗轮 C/D）。

        Returns:
            匹配 ``req_id`` 的输出文本；不匹配 / 过期 generation 返回 None
            （调用方丢弃后继续轮询）。

        Raises:
            RuntimeError: 匹配 req_id 的 error / rid=-1 广播 / 意外 shutdown /
                协议坏（消息经 R9 脱敏）。
        """
        status = result[0]
        if status == "ok":
            if len(result) != 4:
                raise RuntimeError(f"Malformed ok result (len={len(result)}).")
            _, rid, gen, output = result
            if gen != generation:
                logger.warning("[LocalModel] Discarding stale-generation ok response (rid=%s gen=%s).", rid, gen)
                return None
            if rid != req_id:
                logger.warning("[LocalModel] Discarding mismatched ok response (rid=%s).", rid)
                return None
            with self._worker_lock:
                if req_id not in self._inflight:
                    # 迟到响应（请求已终态）：只消费 generation 匹配且在 inflight 的。
                    logger.warning("[LocalModel] Discarding late response for unknown req_id=%s.", req_id)
                    return None
                self._inflight.pop(req_id, None)
                if not self._inflight:
                    self._worker_state = "idle"
            if not isinstance(output, str):
                raise RuntimeError(f"Malformed output payload: {type(output)}.")
            return output
        if status == "error":
            if len(result) == 4:
                rid, gen, payload = result[1], result[2], result[3]
                if gen != generation:
                    logger.warning("[LocalModel] Discarding stale-generation error response (rid=%s gen=%s).", rid, gen)
                    return None
                if rid == req_id or rid == -1:
                    with self._worker_lock:
                        self._inflight.pop(req_id, None)
                        if not self._inflight:
                            self._worker_state = "idle"
                    # R9: payload 来自子进程，可能含 traceback，脱敏后入异常消息。
                    raise RuntimeError(f"Inference execution failed: {DataSanitizer.sanitize_error(payload)}")
                logger.warning("[LocalModel] Discarding mismatched error response (rid=%s).", rid)
                return None
            if len(result) == 3:
                # 加载级/worker 级广播形态 ("error", msg, gen)。
                gen, payload = result[2], result[1]
                if gen != generation:
                    return None
                with self._worker_lock:
                    self._inflight.pop(req_id, None)
                    if not self._inflight:
                        self._worker_state = "idle"
                raise RuntimeError(f"Inference execution failed: {DataSanitizer.sanitize_error(payload)}")
            raise RuntimeError(f"Malformed error result (len={len(result)}).")
        if status == "shutdown":
            gen = _response_generation(result)
            if gen is not None and gen != generation:
                return None
            # worker 已自行退出：ready 标志同步清除（下次调用走完整重载链）。
            self._worker_ready = False
            raise RuntimeError("Worker shut down unexpectedly during inference.")
        raise RuntimeError(f"Unknown result status from worker: {status!r}.")

    def _quarantine_worker(self, generation: int) -> None:
        """标记 worker quarantined 并 spawn 异步清理任务（须在事件循环线程调用）。

        取消/超时后 worker 状态未知（启动加载/推理中均可能）→ 隔离 + 异步清理；
        ``_ensure_worker_impl`` 快路径因 quarantined 失效，下次调用走完整
        shutdown+respawn 自愈。
        """
        with self._worker_lock:
            self._worker_state = "quarantined"
        self._spawn_cleanup_task(generation)

    def _spawn_cleanup_task(self, generation: int) -> None:
        """Spawn（或收编）quarantined worker 的异步清理任务（须在事件循环线程调用）。

        同一 generation 已有清理任务在跑则幂等；目标 generation 已过时的
        旧任务被取消（其 generation 守卫保证它本也会幂等退出，对抗轮 B-2）。
        """
        task = self._cleanup_task
        if task is not None and not task.done():
            if self._cleanup_target_generation == generation:
                return
            task.cancel()
        self._cleanup_target_generation = generation
        self._cleanup_task = asyncio.get_running_loop().create_task(self._cleanup_worker_async(generation))

    def _cancel_cleanup_task(self) -> None:
        """取消待执行/执行中的清理任务（reset/atexit 路径，防悬空任务）。"""
        task = self._cleanup_task
        if task is not None and not task.done():
            task.cancel()
        self._cleanup_task = None

    async def _cleanup_worker_async(self, generation: int) -> None:
        """异步清理 quarantined worker（同步段 offload 到线程池，R16）。

        generation 快照守卫经 ``_shutdown_worker(generation=...)`` 生效：执行时
        若 ``_worker_generation`` 已前进（新 worker 已 spawn），幂等退出不误杀。
        清理失败保持 quarantined（下次调用快路径失效，走完整重载链自愈）。
        """
        try:
            # 有界终止 force 语义：terminate 1.5s + kill 0.5s（对抗轮 I-2）。
            await ThreadPoolManager().run_async(
                TaskType.IO,
                self._shutdown_worker,
                force=True,
                generation=generation,
            )
        except Exception as e:
            log_classified(
                logger,
                e,
                "general",
                "[LocalModel] Cleanup of quarantined worker failed (%s): %s",
                exc_info=True,
            )

    @log_async_operation(threshold_ms=PerfThreshold.AI_INFERENCE, log_args=False)
    async def invoke_for_connection(
        self,
        request: InferenceRequest,
        resolved_connection: ResolvedConnection,
        deadline: float,
        attempt_context: AttemptContext,
    ) -> AdapterResponse:
        """统一单连接推理（S2，ADR-0014 §6.1）：不读全局配置、不做候选选择。

        - 模型路径 = ``resolved_connection.model``，服务参数（n_threads/n_batch/
          n_ctx/n_gpu_layers/flash_attn/timeout）= ``adapter_options``——配置快照
          语义由调用方（S3 ScenarioExecutor）保证，本方法不读
          ``ConfigHandler.get_local_ai_config``；
        - deadline 为单调时钟截止值（loop.time() 口径）：ready 等待 =
          min(180, remaining)，推理轮询 = min(adapter timeout, remaining)；
          remaining 耗尽抛 ``TimeoutError``（ready 前零出站）；
        - req_id 绑定 ``attempt_context`` 进 ``_inflight``；取消/超时 → worker
          标记 quarantined 并 spawn 异步清理任务（generation 守卫）；
        - ``asyncio.CancelledError`` 始终传播（R2），不包装；
        - 并发契约：同连接串行由 S3 ScenarioExecutor 许可语义保证
          （``concurrency_limit``）；与 legacy ``run_inference`` 的互斥沿用既有
          UI 层串行约束——本方法不建 loop-local 锁（与 run_inference 同现状），
          请求-响应配对经 rid 单调递增 + generation 过滤兜底丢弃错配。

        Raises:
            TimeoutError: deadline 已耗尽 / 轮询预算耗尽。
            RuntimeError: 文件校验失败 / worker 启动或就绪失败 / 推理失败。
            ImportError: llama-cpp-python 缺失。
        """
        if not _HAS_LLAMA_CPP:
            raise ImportError("llama-cpp-python not installed.")

        model_path = resolved_connection.model
        # GGUF 魔数校验含文件 open+read（同步 IO），offload 线程池（R16，与
        # scorer _validate_input_files 同口径）。
        if not await ThreadPoolManager().run_async(TaskType.IO, _validate_model_file, model_path):
            raise RuntimeError(f"Invalid GGUF model file (missing, wrong type, or bad magic header): {model_path}")

        loop = asyncio.get_running_loop()
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError("Deadline exhausted before local inference dispatch.")

        adapter_options = resolved_connection.adapter_options
        core_config = {
            "n_threads": adapter_options.get("n_threads", 4),
            "n_batch": adapter_options.get("n_batch", 1024),
            "n_ctx": adapter_options.get("n_ctx", 4096),
            "n_gpu_layers": adapter_options.get("n_gpu_layers", 0),
            "flash_attn": adapter_options.get("flash_attn", True),
        }

        try:
            if not await self._ensure_worker(model_path, core_config):
                raise RuntimeError("Persistent worker failed to start. Check logs for details.")
            if not self._worker_ready:
                ready_budget = min(180.0, deadline - loop.time())
                if ready_budget <= 0:
                    raise TimeoutError("Deadline exhausted while waiting for local worker ready.")
                if not await self._await_worker_ready(timeout=ready_budget, check_cancel_event=False):
                    raise RuntimeError("Persistent worker failed to become ready. Check logs for details.")
        except asyncio.CancelledError:
            # 启动/ready 阶段取消：worker 子进程可能正在加载模型（状态未知）→
            # 隔离 + 异步清理（取消隔离覆盖启动/ready/提交/推理全程，R2 传播）。
            self._quarantine_worker(self._worker_generation)
            raise
        except TimeoutError:
            # ready 预算耗尽：刚 spawn 的 worker 可能仍在加载 → 隔离 + 异步清理。
            self._quarantine_worker(self._worker_generation)
            raise

        payload = request.payload
        adapter_timeout = float(adapter_options.get("timeout", _DEFAULT_INVOKE_TIMEOUT_S) or _DEFAULT_INVOKE_TIMEOUT_S)
        poll_budget = min(adapter_timeout, deadline - loop.time())
        if poll_budget <= 0:
            raise TimeoutError("Deadline exhausted after local worker ready.")

        req_id = next(self._req_counter)
        request_payload = (
            str(payload.get("prompt", "")),
            int(payload.get("max_tokens", _DEFAULT_INVOKE_MAX_TOKENS)),
            float(payload.get("temperature", _DEFAULT_INVOKE_TEMPERATURE)),
            str(payload.get("system_prompt", _DEFAULT_INVOKE_SYSTEM_PROMPT)),
            req_id,
        )
        generation = self._worker_generation
        try:
            output = await self._dispatch_and_collect(
                req_id=req_id,
                generation=generation,
                request_payload=request_payload,
                poll_budget=poll_budget,
                invocation_id=attempt_context.invocation_id,
                attempt_id=attempt_context.attempt_id,
            )
        except asyncio.CancelledError:
            # 取消时 worker 状态未知（可能仍在推理）→ 隔离 + 异步清理；
            # 不复用 _cancel_event（其 unload 后永久 set）。
            self._quarantine_worker(generation)
            raise
        except TimeoutError:
            logger.warning(
                "[LocalModel] invoke_for_connection timed out (req_id=%s connection=%s); quarantining worker.",
                req_id,
                resolved_connection.connection_id,
            )
            self._quarantine_worker(generation)
            raise

        # lazy-import: AdapterResponse 仅 TYPE_CHECKING 导入（services.ai_service
        # 组合根反向依赖本模块，顶层运行时导入会成环）；成功路径运行时构造，
        # 判定用例（场景①）首次命中该路径时暴露 NameError，故函数内延迟导入。
        from services.ai_service.model_management_types import AdapterResponse

        return AdapterResponse(
            content=output,
            usage=None,
            actual_model=_model_tag_of(model_path),
            metadata={},
        )

    def unload_model(self, *, force: bool = False):
        """Free memory by terminating the worker subprocess and resetting state.

        Args:
            force: True 时强制 terminate 而非等待优雅关闭（关机路径使用）。
        """
        self._cancel_event.set()
        self._shutdown_worker(force=force)
        self._model_path = ""
        self._model_sha256 = ""
        self._model_stat = (0, 0)
        self._last_config = {}
        logger.info("[LocalModel] Model unloaded (worker terminated).")

    def commit_verification(self):
        """Clear verification mode flag. The verification model becomes the official model."""
        if self._verification_mode:
            logger.info("[LocalModel] Verification committed, model retained: %s", self._model_path)
            self._verification_mode = False
            self._verification_start_time = 0.0

    def cancel_verification(self):
        """Clear verification mode flag and unload the temporary verification model."""
        if self._verification_mode:
            logger.info("[LocalModel] Verification cancelled, unloading temporary model.")
            self._verification_mode = False
            self._verification_start_time = 0.0
            self.unload_model()

    @classmethod
    def commit_verification_if_active(cls):
        """Synchronously commit verification. Safe to call from sync code (e.g. will_unmount)."""
        inst = cls._instance
        if inst is not None:
            inst.commit_verification()

    @classmethod
    def cancel_verification_if_active(cls):
        """Cancel verification synchronously. Blocks up to ~12s for worker shutdown.

        WARNING: NOT safe for Flet event handlers (R16). Must be called from
        ThreadPool or non-UI thread. For async contexts, use cancel_verification
        via ThreadPoolManager.run_async(TaskType.IO, ...) instead.
        """
        inst = cls._instance
        if inst is not None:
            inst.cancel_verification()
