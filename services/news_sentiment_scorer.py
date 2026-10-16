"""新闻情绪打分子进程服务（N3-2，ADR-0012）。

编码器（FinBERT GGUF，CLS 池化）运行在独立常驻子进程：与对话 LLM 子进程
分离（``embedding=True`` 需独立 ``Llama`` 实例，且避免与生成任务争抢），
超时/关机时 ``terminate`` 由 OS 回收原生内存（照 ``services/local_model_manager.py``
的既有做法）。

推理范式（ADR-0012 正本）：
- 分词必用 HF ``tokenizers``（``Tokenizer.from_file``），禁用 ``Llama.embed(text)``
  的内部分词（N0-4 实测 llama.cpp WordPiece 对 FinBERT2 扩充词表切分与 HF 不一致）；
- 子进程只产出 CLS 特征向量（(N, 768) float32）；分类头（``head.npz``）在
  父进程加载校验并经纯函数 :func:`apply_head_and_score` 应用，便于脱离
  llama_cpp 单测；
- 非有限输出（NaN / Inf）返回 ``None``，由调用方按缺失处理（R21），
  不回填中性。

对外契约（S2 双入口）：
- :meth:`NewsSentimentScorer.score_texts` 为 legacy 业务入口：整体失败返回
  ``None`` 降级，不抛异常、不阻断新闻链路；条目级失败同样以 ``None`` 表示。
- :meth:`NewsSentimentScorer.score_for_connection` 为统一单连接入口
  （ADR-0014 §6.1，S3 ScenarioExecutor 消费）：不读全局配置，返回
  ``ScoringOutcome`` 终态。
- 两者输出仅用于展示边界（ADR-0008），选股/策略模块不得读取。
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import itertools
import logging
import multiprocessing
import queue
import sys
import threading
import time
import traceback
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

import numpy as np

from services.ai_service.model_management_types import (
    AttemptContext,
    NormalizedLLMUsage,
    ResolvedConnection,
    ScoredText,
    ScoringOutcome,
)
from services.local_model_manager import _model_tag_of, _response_generation, _validate_model_file
from utils.config_handler import ConfigHandler
from utils.error_classifier import log_classified
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.loop_local import del_loop_local, get_loop_local
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager
from utils.singleton_registry import register_singleton

logger = logging.getLogger(__name__)


_SENTINEL = "__SENTIMENT_SCORER_SHUTDOWN__"
_EMBEDDING_DIM = 768
_NUM_CLASSES = 3
# head.npz 标签序契约（与训练/导出一致；顺序漂移会翻转 score 符号，故强校验）。
_LABELS_REQUIRED = ("利空", "中性", "利好")

# 批量拆分：单块 ≤ 32 条（32 × ~30ms ≈ 1s），控制单块请求延迟与超时均匀性。
_SCORE_CHUNK_SIZE = 32

_DEFAULT_N_THREADS = 6
_DEFAULT_MAX_LENGTH = 128
_DEFAULT_TIMEOUT_S = 30.0
_DEFAULT_N_CTX = 512

# S2 score_for_connection 预算上限（min(上限, deadline−now) 裁剪，对抗轮 A-2 同型）。
_READY_TIMEOUT_S = 180.0
_SCORE_CHUNK_TIMEOUT_S = 30.0


class _WorkerError(RuntimeError):
    """请求级 worker 失败（区别于「丢弃不匹配响应」的继续轮询）。"""


def _probe_module(module_name: str) -> bool:
    """探测可选依赖是否可用，仅解析模块元数据而不加载模块（PRF-04 模式）。

    1. 优先复用 sys.modules（兼容已导入模块及测试中的 Mock 替身）；
    2. 若 sys.modules 中显式为 None（Python 标准的不可导入标记），返回 False；
    3. 否则通过 importlib.util.find_spec 探测元数据，防御常见导入异常。
    """
    mod = sys.modules.get(module_name)
    if mod is not None:
        return True
    if module_name in sys.modules:
        return False
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


_HAS_LLAMA_CPP = _probe_module("llama_cpp")
_HAS_TOKENIZERS = _probe_module("tokenizers")
if not _HAS_LLAMA_CPP or not _HAS_TOKENIZERS:
    # NOTE(lazy): tokenizers / llama-cpp-python 未声明进 pyproject 运行时依赖（可选
    # 功能栈，与 LocalModelManager 的 llama_cpp 可选态度一致），缺失时打分降级为
    # None（与「模型未配置」同路径），不影响 App 启动与其他功能。
    # ceiling: 未安装 tokenizers 或 llama-cpp-python 的用户环境。
    # upgrade: N5-4 模型分发裁定安装包是否内置情绪打分栈时，纳入运行时依赖表。
    missing = [name for name, ok in (("llama-cpp-python", _HAS_LLAMA_CPP), ("tokenizers", _HAS_TOKENIZERS)) if not ok]
    logger.warning(
        "%s not installed. News sentiment scoring will be unavailable.",
        ", ".join(missing),
    )


# --------------------------------------------------------------------------- #
# 纯函数（可脱离 llama_cpp / tokenizers 单测）
# --------------------------------------------------------------------------- #
def softmax(logits: np.ndarray) -> np.ndarray:
    """按行做数值稳定 softmax（来源：scripts/verify_finbert_gguf.py 同名函数）。"""
    shifted = logits - logits.max(axis=1, keepdims=True)
    exp = np.exp(shifted)
    return exp / exp.sum(axis=1, keepdims=True)


def load_head_npz(path: str) -> dict[str, Any]:
    """载入并强校验 ``head.npz``（格式正本：reviews/新闻达标模型微调.md §8.2）。

    校验失败（mode 非法 / 形状不符 / 标签序漂移 / 数值非有限）抛 ``ValueError``
    拒绝加载，**不静默降级**（台账 N3-2）。

    Returns:
        ``{"mode": "linear"|"bert", "labels": tuple[str, ...], "cw", "cb",
        "pre": (pw, pb) | None}``。

    Raises:
        ValueError: 结构或数值校验不通过。
        OSError: 文件不存在或不可读。
    """
    # with 确保关闭 NpzFile 句柄（Windows 上未 close 会锁定文件）；allow_pickle=False
    # 防止 pickle 载体（台账 N3-2 明确要求）；copy() 在句柄关闭后仍持有独立数组。
    with np.load(path, allow_pickle=False) as data:
        mode = str(data["mode"])
        labels_raw = data["labels"]
        cw = np.asarray(data["cw"], dtype=np.float32).copy()
        cb = np.asarray(data["cb"], dtype=np.float32).copy()
        pre: tuple[np.ndarray, np.ndarray] | None = None
        if mode == "bert":
            pw = np.asarray(data["pw"], dtype=np.float32).copy()
            pb = np.asarray(data["pb"], dtype=np.float32).copy()
            pre = (pw, pb)
        elif mode != "linear":
            raise ValueError(f"head.npz mode 非法: {mode!r}（期望 linear / bert）")

    labels = tuple(str(label) for label in labels_raw.tolist())
    if labels != _LABELS_REQUIRED:
        raise ValueError(
            f"head.npz labels 非法: {labels}（期望 {_LABELS_REQUIRED}；顺序漂移会翻转 score 符号）",
        )
    if cw.shape != (_NUM_CLASSES, _EMBEDDING_DIM) or cb.shape != (_NUM_CLASSES,):
        raise ValueError(
            f"head.npz 形状不符: cw={cw.shape} cb={cb.shape}（期望 {_NUM_CLASSES}×{_EMBEDDING_DIM} / {_NUM_CLASSES}）",
        )
    if pre is not None and (pre[0].shape != (_EMBEDDING_DIM, _EMBEDDING_DIM) or pre[1].shape != (_EMBEDDING_DIM,)):
        raise ValueError(
            f"head.npz 形状不符: pw={pre[0].shape} pb={pre[1].shape}"
            f"（期望 {_EMBEDDING_DIM}×{_EMBEDDING_DIM} / {_EMBEDDING_DIM}）",
        )
    for arr in (cw, cb, *(pre if pre is not None else ())):
        if not np.all(np.isfinite(arr)):
            raise ValueError("head.npz 含非有限数值（NaN / Inf），拒绝加载")

    return {"mode": mode, "labels": labels, "cw": cw, "cb": cb, "pre": pre}


def apply_head_and_score(features: np.ndarray, head: dict[str, Any]) -> list[dict[str, Any] | None]:
    """应用分类头并打分（纯函数，不依赖 llama_cpp）。

    mode=bert 先 ``tanh(features @ pw.T + pb)``，linear 直接进头；
    ``score = p[利好] - p[利空] ∈ [-1, 1]``，``confidence = max(p)``。

    单行特征含非有限值 → 该行 ``None``（R21，不回填中性）。
    """
    if features.ndim != 2 or features.shape[1] != _EMBEDDING_DIM:
        raise ValueError(f"特征形状不符: {features.shape}（期望 (N, {_EMBEDDING_DIM})）")
    if features.shape[0] == 0:
        return []

    pre = head["pre"]
    x = features if pre is None else np.tanh(features @ pre[0].T + pre[1])
    logits = x @ head["cw"].T + head["cb"]
    probs = softmax(logits)

    labels: tuple[str, ...] = head["labels"]
    bearish = labels.index("利空")
    bullish = labels.index("利好")
    results: list[dict[str, Any] | None] = []
    for p in probs:
        if not np.all(np.isfinite(p)):
            results.append(None)
            continue
        results.append(
            {
                "label": labels[int(np.argmax(p))],
                "confidence": float(p.max()),
                "score": float(p[bullish] - p[bearish]),
                "probs": [float(v) for v in p],
            }
        )
    return results


def _validate_score_request(request: object) -> bool:
    """校验打分请求协议（独立于 local_model_manager 的 4-tuple 校验，台账 N3-2）。

    协议：``("score", req_id: int, [str, ...])``；``req_id`` 用于响应配对
    （防跨事件循环并发时响应错位）。
    """
    if not isinstance(request, tuple) or len(request) != 3:
        return False
    kind, req_id, texts = request
    if kind != "score" or not isinstance(req_id, int) or isinstance(req_id, bool):
        return False
    return isinstance(texts, list) and all(isinstance(text, str) for text in texts)


def _chunked(items: list[str], size: int) -> list[list[str]]:
    """把列表按固定大小切块（空列表返回空）。"""
    return [items[i : i + size] for i in range(0, len(items), size)]


# --------------------------------------------------------------------------- #
# 子进程 worker（pragma no cover — runs in subprocess, not coverable by unit tests）
# --------------------------------------------------------------------------- #
def _scoring_worker(  # pragma: no cover — runs in subprocess, not coverable by unit tests
    model_path: str,
    tokenizer_path: str,
    config: dict,
    worker_generation: int,
    request_queue: multiprocessing.Queue,
    result_queue: multiprocessing.Queue,
):
    """常驻打分子进程：加载一次模型，循环服务 ``("score", req_id, [texts])`` 请求。

    响应协议（S2，统一携带 worker_generation）：
    - ``("ok", req_id, worker_generation, (N, 768) float32, meta)``
      （meta = {"input_tokens": [int | None, ...], "truncated": [bool, ...], "processed": int}）
    - ``("error", req_id, worker_generation, msg)``（req_id=-1 为协议级广播）
    - 加载结果 ``("ready", model_path, worker_generation)`` /
      ``("error", msg, worker_generation)``
    - 父进程发 ``_SENTINEL`` 优雅退出 → ``("shutdown", "ok", worker_generation)``

    meta 语义（tokenizers Encoding）：截断后 ``ids`` 长度 = 实际输入 token 数
    （含特殊 token，供 encoder_input_tokens 计量）；``overflowing`` 非空 =
    该条发生截断。空 token 序列行不入向量（保持 NaN → 父进程判 None，R21），
    其 input_tokens 记 None。

    单条文本失败（如空 token 序列）→ 该行填 NaN 继续（父进程按 R21 判 None）；
    decode 失败属状态损坏 → 整批 ``("error", ...)``。
    """
    try:
        llama_cpp = importlib.import_module("llama_cpp")
        tokenizers_mod = importlib.import_module("tokenizers")
        Tokenizer = tokenizers_mod.Tokenizer
    except (ImportError, AttributeError) as e:
        result_queue.put(
            ("error", f"optional dependency import failed: {e}\n{traceback.format_exc()}", worker_generation)
        )
        return

    try:
        llm = llama_cpp.Llama(
            model_path=model_path,
            embedding=True,
            pooling_type=llama_cpp.LLAMA_POOLING_TYPE_CLS,
            n_ctx=config.get("n_ctx", _DEFAULT_N_CTX),
            n_batch=config.get("n_ctx", _DEFAULT_N_CTX),
            n_ubatch=config.get("n_ctx", _DEFAULT_N_CTX),
            n_threads=config.get("n_threads", _DEFAULT_N_THREADS),
            verbose=False,
        )
        tokenizer = Tokenizer.from_file(tokenizer_path)
        tokenizer.enable_truncation(config.get("max_length", _DEFAULT_MAX_LENGTH))
        result_queue.put(("ready", model_path, worker_generation))
    except Exception as e:
        sanitized = DataSanitizer.sanitize_error(e)
        log_classified(
            logger,
            e,
            "general",
            "[NewsSentiment] Model/tokenizer load failed (%s): %s",
            exc_info=True,
        )
        result_queue.put(
            ("error", f"Model/tokenizer load failed: {sanitized}\n{traceback.format_exc()}", worker_generation)
        )
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
                "[NewsSentiment] Worker queue get error (%s): %s",
                exc_info=True,
            )
            continue

        if request == _SENTINEL:
            result_queue.put(("shutdown", "ok", worker_generation))
            break

        if not _validate_score_request(request):
            result_queue.put(("error", -1, worker_generation, f"Invalid request format: {type(request)}"))
            continue

        _, req_id, texts = request
        try:
            n_embd = int(llm.n_embd())
            batch = llm._batch
            if batch is None:
                raise RuntimeError("llm._batch is NULL; cannot build token batches")
            vectors = np.full((len(texts), n_embd), np.nan, dtype=np.float32)
            input_tokens: list[int | None] = [None] * len(texts)
            truncated_flags: list[bool] = [False] * len(texts)
            processed = 0
            for row, text in enumerate(texts):
                encoding = tokenizer.encode(text, add_special_tokens=True)
                truncated_flags[row] = len(encoding.overflowing) > 0
                ids = list(encoding.ids)
                if not ids:
                    # 空 token 序列送入 add_sequence 会导致 llama.cpp 堆损坏
                    # （0xC0000374），必须跳过；该行保持 NaN → 父进程判 None（R21）。
                    continue
                input_tokens[row] = len(ids)
                llm._ctx.kv_cache_clear()
                batch.reset()
                batch.add_sequence(ids, 0, True)
                llm._ctx.decode(batch)
                ptr = llama_cpp.llama_get_embeddings_seq(llm._ctx.ctx, 0)
                vectors[row] = np.asarray(ptr[:n_embd], dtype=np.float32)
                batch.reset()
                processed += 1
            meta = {"input_tokens": input_tokens, "truncated": truncated_flags, "processed": processed}
            result_queue.put(("ok", req_id, worker_generation, vectors, meta))
        except Exception as e:
            # decode 失败后 KV/批状态可能损坏，按整批错误处理（父进程将终止并
            # 重启 worker），不做「单条降级继续」。
            sanitized = DataSanitizer.sanitize_error(e)
            log_classified(
                logger,
                e,
                "general",
                "[NewsSentiment] Worker scoring failed (%s): %s",
                exc_info=True,
            )
            result_queue.put(("error", req_id, worker_generation, f"{sanitized}\n{traceback.format_exc()}"))


# --------------------------------------------------------------------------- #
# 管理单例
# --------------------------------------------------------------------------- #
@register_singleton
class NewsSentimentScorer:
    """新闻情绪打分子进程服务的生命周期与请求管理。

    - ``score_texts`` 为 legacy 业务入口：整体失败返回 ``None``（降级不阻断新闻
      链路），成功返回与输入等长的 ``list[dict | None]``（条目 NaN/Inf →
      ``None``，R21）；``score_for_connection`` 为 S2 统一单连接入口。
    - 子进程生命周期管理照 ``LocalModelManager``：双检锁单例、loop-local 锁（R11）、
      阻塞操作 offload 到 IO 线程池（R16）、超时/关机 terminate（N3-3 对接）。
    """

    _instance: NewsSentimentScorer | None = None
    _initialized: bool = False
    # RLock 允许 get_instance 持锁后调用 NewsSentimentScorer() 时 __new__ 重入同锁不死锁
    _lock = threading.RLock()

    def __new__(cls, *args, **kwargs):
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
                        "[NewsSentiment] Error during reset shutdown (%s): %s",
                        exc_info=True,
                    )
            cls._instance = None
            cls._initialized = False
        del_loop_local("sentiment_scorer_lock")

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
                    "[NewsSentiment] Error during atexit shutdown (%s): %s",
                    exc_info=True,
                )

    @classmethod
    async def get_instance(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = NewsSentimentScorer()
                cls._instance._initialized = True
        return cls._instance

    def __init__(self, *, clock=None, config_reader=None):
        # CON-01: double-checked locking（同 LocalModelManager，初始化整体移入锁内）。
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            self._worker_proc: multiprocessing.Process | None = None
            self._request_queue: multiprocessing.Queue | None = None
            self._result_queue: multiprocessing.Queue | None = None
            self._worker_ready: bool = False
            self._worker_lock: threading.Lock = threading.Lock()
            self._req_counter = itertools.count()
            self._model_path: str = ""
            self._paths_stat: tuple = (0, 0)
            self._core_signature: tuple[int, int] = (0, 0)
            self._head: dict[str, Any] | None = None
            self._clock = clock or time.monotonic
            self._config_reader = config_reader or self._read_config
            # S2 单连接执行状态（ADR-0014 §6.1，与 LocalModelManager 同型；
            # scorer 以 req_id 响应配对兜底，不建 _inflight 绑定）：
            self._worker_generation: int = 0  # 每次 spawn 递增；req_id 计数器跨 generation 不重置
            self._worker_state: str = "stopped"  # idle / busy / quarantined / stopped
            self._cleanup_task: asyncio.Task | None = None
            self._cleanup_target_generation: int = -1
            self._initialized = True

    # ------------------------------------------------------------------ #
    # 配置
    # ------------------------------------------------------------------ #
    @staticmethod
    def _read_config() -> dict[str, Any]:
        """从 ConfigHandler 读取打分配置（构造时可注入 config_reader，测试用）。"""
        return {
            "enabled": ConfigHandler.get_typed("news_sentiment_enabled", bool, False),
            "model_path": ConfigHandler.get_typed("news_sentiment_model_path", str, ""),
            "head_path": ConfigHandler.get_typed("news_sentiment_head_path", str, ""),
            "tokenizer_path": ConfigHandler.get_typed("news_sentiment_tokenizer_path", str, ""),
            "n_threads": ConfigHandler.get_typed("news_sentiment_n_threads", int, _DEFAULT_N_THREADS),
            "max_length": ConfigHandler.get_typed("news_sentiment_max_length", int, _DEFAULT_MAX_LENGTH),
            "timeout_s": ConfigHandler.get_typed("news_sentiment_timeout_s", float, _DEFAULT_TIMEOUT_S),
        }

    @property
    def model_tag(self) -> str | None:
        """N5-2：已加载模型标识（GGUF 文件 stem，截断 ≤32 字符对齐
        market_news.sentiment_model String(32)）。

        未加载/未配置时 ``None``（R21：缺失不伪装）。仅在 ``score_texts`` 成功路径
        有值——打分载荷写入该标识即「本条分数由该模型产出」的溯源语义。
        """
        path = self._model_path
        if not path:
            return None
        return _model_tag_of(path)

    def _is_enabled(self) -> bool:
        return bool(self._config_reader().get("enabled"))

    def _resolve_paths(self) -> dict[str, str] | None:
        """读取并校验三个文件路径配置；未配置齐返回 None（降级路径）。"""
        cfg = self._config_reader()
        paths = {key: str(cfg.get(key, "")) for key in ("model_path", "head_path", "tokenizer_path")}
        if not all(paths.values()):
            logger.debug("[NewsSentiment] Model paths not fully configured, scoring unavailable.")
            return None
        return paths

    def _core_config(self) -> dict[str, Any]:
        cfg = self._config_reader()
        return {
            "n_ctx": _DEFAULT_N_CTX,
            "n_threads": max(1, int(cfg.get("n_threads", _DEFAULT_N_THREADS))),
            # 上限 clamp 至 n_ctx-8：max_length 超过上下文容量会使 add_sequence
            # 反复溢出 → worker 崩溃 → 杀重启循环（对抗性检视 C4）。
            "max_length": min(_DEFAULT_N_CTX - 8, max(8, int(cfg.get("max_length", _DEFAULT_MAX_LENGTH)))),
        }

    def _resolve_timeout(self) -> float:
        """轮询超时（下限 clamp 至 1s：0/负值会使轮询立即到期，退化为忙抖动）。"""
        raw = float(self._config_reader().get("timeout_s", _DEFAULT_TIMEOUT_S))
        return max(1.0, raw)

    @staticmethod
    def _validate_input_files(paths: dict[str, str]) -> bool:
        """GGUF 魔数 + tokenizer 文件存在性校验（同步，在线程池中执行）。"""
        if not _validate_model_file(paths["model_path"]):
            logger.error("[NewsSentiment] Invalid GGUF model file: %s", paths["model_path"])
            return False
        if not Path(paths["tokenizer_path"]).is_file():
            logger.error("[NewsSentiment] Tokenizer file not found: %s", paths["tokenizer_path"])
            return False
        return True

    @staticmethod
    def _stat_of(*paths: str) -> tuple:
        stat: list[tuple] = []
        for path in paths:
            try:
                st = Path(path).stat()
                stat.append((st.st_mtime, st.st_size))
            except OSError:
                stat.append((0, 0))
        return tuple(stat)

    # ------------------------------------------------------------------ #
    # 子进程生命周期
    # ------------------------------------------------------------------ #
    def _get_lock(self):
        """同 loop 串行化 score_texts 全程（加载 + 请求-响应）的 loop-local 锁（R11）。

        锁粒度覆盖整个 ``score_texts`` 而非仅加载：同 loop 并发的 score_texts
        共享一对队列，若在锁外发请求会互吞对方的响应（req_id 不匹配被丢弃 →
        双双等满超时后假降级）。asyncio.Lock 不可重入，故锁取在 ``score_texts``，
        ``_ensure_ready`` 不自取锁。跨 loop 并发（本项目现无此场景）由 req_id
        响应配对兜底：不匹配的响应被丢弃，最坏结果是该 loop 等待至超时降级
        None，不会发生分数张冠李戴。
        """

        def _factory():
            return asyncio.Lock()

        return get_loop_local("sentiment_scorer_lock", _factory)

    def _shutdown_worker(self, *, force: bool = False, generation: int | None = None):
        """Shut down the scoring worker subprocess (thread-safe).

        Args:
            force: True 时跳过 sentinel 直接 terminate（关机路径，N3-3 Step 5b
                   2.0s 预算：terminate 1.5s + kill 0.5s）。
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
                        "[NewsSentiment] Failed to send sentinel to worker (%s): %s",
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
                # Force path: worker 可能正忙于推理，sentinel 不会被及时处理，
                # 直接 terminate 释放资源（预算 1.5+0.5=2.0s，适配关机 Step 预算）。
                self._worker_proc.terminate()
                self._worker_proc.join(timeout=1.5)
                if self._worker_proc.is_alive():
                    self._worker_proc.kill()
                    self._worker_proc.join(timeout=0.5)
            logger.info("[NewsSentiment] Scoring worker shut down.")

        if self._request_queue is not None:
            try:
                self._request_queue.close()
                self._request_queue.join_thread()
            except Exception as e:
                log_classified(
                    logger,
                    e,
                    "general",
                    "[NewsSentiment] Failed to close request queue (%s): %s",
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
                    "[NewsSentiment] Failed to close result queue (%s): %s",
                    exc_info=True,
                )

        self._worker_proc = None
        self._request_queue = None
        self._result_queue = None
        self._worker_ready = False
        self._worker_state = "stopped"

    def unload_model(self, *, force: bool = False):
        """Terminate the scoring worker subprocess and reset load state (public API).

        与 ``LocalModelManager.unload_model`` 语义对齐：关机路径（utils.shutdown
        Step 5b）经本方法强制终止子进程；同时重置加载态，下次 ``_ensure_ready``
        走完整重载链（_worker_ready=False 已封死快路径，重置 head/签名释放引用）。

        Args:
            force: True 时跳过 sentinel 直接 terminate（关机路径使用）。
        """
        self._shutdown_worker(force=force)
        self._model_path = ""
        self._paths_stat = (0, 0)
        self._core_signature = (0, 0)
        self._head = None
        logger.info("[NewsSentiment] Scoring model unloaded (worker terminated).")

    def _ensure_worker_impl(self, model_path: str, tokenizer_path: str, core_config: dict) -> bool:
        """同步实现，在线程池中执行（含 process.join 同步阻塞，R16）。"""
        # 核心参数签名：n_threads / max_length 变更须重启 worker（模型已按旧参数
        # 构造，热改不生效；N3-6 设置页修改后经此路径生效）。
        signature = (
            int(core_config.get("n_threads", _DEFAULT_N_THREADS)),
            int(core_config.get("max_length", _DEFAULT_MAX_LENGTH)),
        )
        with self._worker_lock:
            if (
                self._worker_proc is not None
                and self._worker_proc.is_alive()
                and self._worker_ready
                and self._model_path == model_path
                and self._core_signature == signature
                # quarantined：worker 状态未知（取消/超时后可能仍在推理），
                # 快路径失效，走完整 shutdown+respawn 自愈（与 LMM 对抗轮 E-1 同型）。
                and self._worker_state != "quarantined"
            ):
                return True

            self._shutdown_worker_locked()
            # generation 每次 spawn 递增；旧响应经 _response_generation 过滤丢弃
            # （计数器 _req_counter 跨 generation 不重置）。
            self._worker_generation += 1
            generation = self._worker_generation
            self._core_signature = signature

            self._request_queue = multiprocessing.Queue()
            self._result_queue = multiprocessing.Queue()

            proc = multiprocessing.Process(
                target=_scoring_worker,
                args=(model_path, tokenizer_path, core_config, generation, self._request_queue, self._result_queue),
                daemon=True,
            )
            proc.start()
            self._worker_proc = proc
            logger.info("[NewsSentiment] Scoring worker started. pid=%s generation=%s", proc.pid, generation)
            return True

    async def _await_worker_ready(self, timeout: float) -> bool:
        """轮询等待 worker 就绪（异步不阻塞事件循环，R16）。

        S2：旧 generation 的残留响应直接丢弃继续等待（respawn 后队列虽已重建，
        此处仍为防御性过滤）。
        """
        result_queue = self._result_queue
        if result_queue is None:
            logger.error("[NewsSentiment] Result queue not initialized.")
            return False

        result: object = None
        worker_died = False
        deadline = asyncio.get_running_loop().time() + timeout

        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                result = result_queue.get_nowait()
            except queue.Empty:
                result = None
            except (OSError, ValueError):
                # ValueError：队列被 close()+join_thread() 后 get_nowait 的实测行为。
                logger.warning(
                    "[NewsSentiment] OSError/ValueError reading result queue while waiting for worker ready."
                )
                result = None

            # S2: generation 过滤——旧 worker 的残留响应直接丢弃继续等待。
            if result is not None:
                gen = _response_generation(result) if isinstance(result, tuple) else None
                if gen is not None and gen != self._worker_generation:
                    logger.warning(
                        "[NewsSentiment] Discarding stale-generation response while waiting for ready (gen=%s).", gen
                    )
                    result = None
            if result is not None:
                break

            proc = self._worker_proc
            if proc is not None and not proc.is_alive():
                try:
                    result = result_queue.get_nowait()
                except (queue.Empty, OSError, ValueError):
                    result = None
                if result is not None and isinstance(result, tuple):
                    gen = _response_generation(result)
                    if gen is not None and gen != self._worker_generation:
                        logger.warning(
                            "[NewsSentiment] Discarding stale-generation response while waiting for ready (gen=%s).",
                            gen,
                        )
                        result = None
                worker_died = True
                break

            # R2: except Exception 不会吞 CancelledError（BaseException）；
            # sleep 被取消时 CancelledError 直接向上传播。
            await asyncio.sleep(min(0.5, remaining))

        if result is None:
            try:
                result = result_queue.get_nowait()
            except (queue.Empty, OSError, ValueError):
                pass

        if result is None:
            self._worker_ready = False
            if worker_died:
                exitcode = self._worker_proc.exitcode if self._worker_proc is not None else None
                logger.error("[NewsSentiment] Scoring worker crashed before ready. exitcode=%s", exitcode)
            else:
                logger.error("[NewsSentiment] Scoring worker failed to become ready within %ss.", timeout)
            await ThreadPoolManager().run_async(TaskType.IO, self._shutdown_worker)
            return False

        if not (isinstance(result, tuple) and len(result) == 3):
            logger.error("[NewsSentiment] Malformed result while waiting for worker ready: %s", type(result))
            self._worker_ready = False
            await ThreadPoolManager().run_async(TaskType.IO, self._shutdown_worker)
            return False

        status, payload = result[0], result[1]
        if status == "ready":
            self._worker_ready = True
            self._worker_state = "idle"
            logger.info("[NewsSentiment] Scoring worker ready with model: %s", payload)
            return True

        # R9: payload 来自子进程，可能含 traceback（文件路径/系统信息），脱敏后记录。
        logger.error("[NewsSentiment] Scoring worker failed: %s", DataSanitizer.sanitize_error(payload))
        self._worker_ready = False
        await ThreadPoolManager().run_async(TaskType.IO, self._shutdown_worker)
        return False

    async def _ensure_ready(
        self,
        paths: dict[str, str],
        *,
        core_config: dict[str, Any],
        ready_timeout: float = _READY_TIMEOUT_S,
    ) -> dict[str, Any] | None:
        """确保 worker 就绪并返回分类头（失败返回 None）。

        加载链：文件校验 → head 加载校验（stat 变化时重载）→ 启动/复用 worker →
        等待 ready。任一步失败返回 None，不抛异常（CancelledError 传播，R2）。
        调用方（``score_texts`` / ``score_for_connection``）须已持有 loop-local
        串行锁；本方法不自取锁（asyncio.Lock 不可重入，锁粒度见 ``_get_lock``
        docstring）。

        Args:
            paths: 三文件路径（model/head/tokenizer）。
            core_config: worker 核心参数——legacy 链传配置派生值
                （``_core_config``），统一链传 adapter_options 派生值。
            ready_timeout: ready 等待预算；统一链传 ``min(180, deadline−now)``。
        """
        if not _HAS_LLAMA_CPP or not _HAS_TOKENIZERS:
            logger.error("[NewsSentiment] llama-cpp-python / tokenizers not installed, scoring unavailable.")
            return None

        # GGUF 魔数 + tokenizer 文件存在性校验（同步 Path 操作 offload 到 IO 线程池，
        # R16/ASYNC240；N3-6 设置页将沿用同一校验，台账 §9.2 明示复用）。
        valid = await ThreadPoolManager().run_async(TaskType.IO, self._validate_input_files, paths)
        if not valid:
            return None

        try:
            current_stat = self._stat_of(paths["model_path"], paths["head_path"], paths["tokenizer_path"])
            signature = (
                int(core_config.get("n_threads", _DEFAULT_N_THREADS)),
                int(core_config.get("max_length", _DEFAULT_MAX_LENGTH)),
            )
            if (
                self._worker_ready
                and self._worker_state != "quarantined"
                and self._model_path == paths["model_path"]
                and self._paths_stat == current_stat
                and self._head is not None
                and self._core_signature == signature
            ):
                return self._head

            head = await ThreadPoolManager().run_async(TaskType.IO, load_head_npz, paths["head_path"])
            started = await ThreadPoolManager().run_async(
                TaskType.IO,
                self._ensure_worker_impl,
                paths["model_path"],
                paths["tokenizer_path"],
                core_config,
            )
            if not started:
                logger.error("[NewsSentiment] Scoring worker failed to start.")
                return None
            if not await self._await_worker_ready(timeout=max(0.0, ready_timeout)):
                return None

            self._model_path = paths["model_path"]
            self._paths_stat = current_stat
            self._head = head
            logger.info("[NewsSentiment] Scoring model loaded (head mode=%s).", head["mode"])
            return self._head
        except Exception as e:
            log_classified(
                logger,
                e,
                "general",
                "[NewsSentiment] Failed to ensure scorer ready (%s): %s",
                exc_info=True,
            )
            return None

    # ------------------------------------------------------------------ #
    # 打分
    # ------------------------------------------------------------------ #
    @log_async_operation(threshold_ms=PerfThreshold.AI_INFERENCE)
    async def score_texts(self, texts: list[str]) -> list[dict[str, Any] | None] | None:
        """对一批新闻文本打分。

        同一事件循环内以 loop-local 锁串行化「就绪检查 + 请求-响应」全程
        （锁粒度说明见 ``_get_lock`` docstring；asyncio.Lock 不可重入，故锁取在
        本方法而非 ``_ensure_ready``）。

        Returns:
            与 ``texts`` 等长的 ``list[dict | None]``（label/confidence/score/probs；
            条目非有限 → ``None``，R21）；服务不可用（未启用/未配置/加载失败/
            超时/worker 错误）整体返回 ``None``，调用方按缺失处理，不阻断新闻链路。
            除 ``asyncio.CancelledError``（R2，向上传播以配合优雅停机）外不抛异常。
        """
        if not texts:
            return []
        if not self._is_enabled():
            return None
        paths = self._resolve_paths()
        if paths is None:
            return None

        async with self._get_lock():
            head = await self._ensure_ready(paths, core_config=self._core_config())
            if head is None:
                return None

            timeout_s = self._resolve_timeout()
            results: list[dict[str, Any] | None] = []
            for chunk in _chunked(texts, _SCORE_CHUNK_SIZE):
                try:
                    scored = await self._score_chunk(chunk, timeout_s=timeout_s, generation=self._worker_generation)
                except TimeoutError:
                    return None
                if scored is None:
                    return None
                results.extend(apply_head_and_score(scored[0], head))
            return results

    async def _score_chunk(
        self,
        chunk: list[str],
        *,
        timeout_s: float,
        generation: int,
    ) -> tuple[np.ndarray, Mapping[str, Any]] | None:
        """发送单块请求并轮询配对响应（S2 共享底座，legacy 与统一链共用）。

        timeout_s / generation 由调用方显式传入：legacy ``score_texts`` 传配置
        派生超时与当前 ``_worker_generation``；``score_for_connection`` 传
        ``min(块上限, deadline−now)`` 与进入时的 generation 快照。

        Returns:
            成功返回 ``(vectors, meta)``；worker 级失败（发送失败 / 请求级 error /
            崩溃且残留无匹配）返回 None（已同步终止 worker，调用方整体降级）。

        Raises:
            TimeoutError: 轮询预算耗尽（worker 已标记 quarantined 并 spawn 异步
                清理任务；调用方决定降级方式）。
            asyncio.CancelledError: 调用方取消（worker 已隔离，R2 传播）。
        """
        req_id = next(self._req_counter)
        request_queue = self._request_queue
        result_queue = self._result_queue
        if request_queue is None or result_queue is None:
            logger.error("[NewsSentiment] Request/result queue not initialized.")
            return None

        loop = asyncio.get_running_loop()
        start_time = loop.time()
        with self._worker_lock:
            self._worker_state = "busy"

        # put 在队列缓冲满时会阻塞，offload 到执行器避免阻塞事件循环（R16）。
        try:
            await loop.run_in_executor(
                None,
                lambda: request_queue.put(("score", req_id, chunk), timeout=5),
            )
        except asyncio.CancelledError:
            self._quarantine_worker(generation)
            raise
        except Exception as e:
            log_classified(
                logger,
                e,
                "general",
                "[NewsSentiment] Failed to send request to worker (%s): %s",
                exc_info=True,
            )
            self._worker_ready = False
            await ThreadPoolManager().run_async(TaskType.IO, self._shutdown_worker)
            return None

        try:
            polled = await self._poll_result(req_id, result_queue, timeout_s, generation)
        except asyncio.CancelledError:
            self._quarantine_worker(generation)
            raise
        except TimeoutError:
            logger.error(
                "[NewsSentiment] Scoring timed out after %.1fs (req_id=%s). Quarantining worker.",
                loop.time() - start_time,
                req_id,
            )
            self._quarantine_worker(generation)
            raise
        except _WorkerError as e:
            # 请求级失败（worker error / 意外 shutdown / 协议坏）：终止 worker，整体降级。
            logger.error("[NewsSentiment] Scoring request failed (req_id=%s): %s", req_id, e)
            self._worker_ready = False
            await ThreadPoolManager().run_async(TaskType.IO, self._shutdown_worker)
            return None

        if polled is None:
            # worker 崩溃且崩溃前残留响应无匹配：终止 worker，整体降级。
            logger.error(
                "[NewsSentiment] Scoring worker crashed without matching response (req_id=%s). Terminating worker.",
                req_id,
            )
            self._worker_ready = False
            await ThreadPoolManager().run_async(TaskType.IO, self._shutdown_worker)
            return None

        vectors, meta = polled
        with self._worker_lock:
            self._worker_state = "idle"
        return vectors, meta

    async def _poll_result(
        self,
        req_id: int,
        result_queue: multiprocessing.Queue,
        timeout_s: float,
        generation: int,
    ) -> tuple[np.ndarray, Mapping[str, Any]] | None:
        """轮询响应直到取得匹配 ``req_id`` 的结果 / 请求级失败 / 超时 / 崩溃。

        Returns:
            匹配的 ``((N, 768) 向量, meta)``；崩溃（含崩溃前最后一次残留响应仍
            无匹配）返回 None（调用方终止 worker 并整体降级）。

        Raises:
            TimeoutError: 轮询预算耗尽。
            _WorkerError: 收到匹配 req_id 的 error 响应 / 意外 shutdown / 协议坏。
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError()
            try:
                result: object = result_queue.get_nowait()
            except queue.Empty:
                result = None
            except (OSError, ValueError):
                # ValueError：队列已被 close()+join_thread() 后 get_nowait 的实测行为
                # （非 Empty/OSError），与「队列暂时不可读」同语义处理。
                logger.warning("[NewsSentiment] OSError/ValueError reading result queue during scoring.")
                result = None

            if result is not None and isinstance(result, tuple):
                gen = _response_generation(result)
                if gen is not None and gen != generation:
                    logger.warning("[NewsSentiment] Discarding stale-generation response during scoring (gen=%s).", gen)
                else:
                    consumed = self._consume_result(req_id, result, generation=generation)
                    if consumed is not None:
                        return consumed

            proc = self._worker_proc
            if proc is not None and not proc.is_alive():
                # 崩溃前最后一次尝试取残留响应（恰含匹配结果时仍可用）
                try:
                    result = result_queue.get_nowait()
                except (queue.Empty, OSError, ValueError):
                    result = None
                if result is not None and isinstance(result, tuple):
                    gen = _response_generation(result)
                    if gen is None or gen == generation:
                        consumed = self._consume_result(req_id, result, generation=generation)
                        if consumed is not None:
                            return consumed
                logger.error("[NewsSentiment] Scoring worker crashed during request (req_id=%s).", req_id)
                return None

            # R2: except Exception 不会吞 CancelledError；sleep 被取消时直接传播。
            await asyncio.sleep(min(0.2, remaining))

    def _consume_result(
        self,
        req_id: int,
        result: tuple,
        *,
        generation: int,
    ) -> tuple[np.ndarray, Mapping[str, Any]] | None:
        """单点解析打分响应（generation 配对 + req_id 配对，与 LMM 同型）。

        Returns:
            匹配 ``req_id`` 的 ``(vectors, meta)``；不匹配 / 过期 generation 返回
            None（调用方丢弃后继续轮询）。

        Raises:
            _WorkerError: 匹配 ``req_id`` 的 error 响应 / 意外 shutdown / 协议坏
                （消息经 R9 脱敏）。
        """
        status = result[0]
        if status == "ok":
            if len(result) != 5:
                raise _WorkerError(f"Malformed ok result (len={len(result)}).")
            _, rid, gen, vectors, meta = result
            if gen != generation:
                logger.warning("[NewsSentiment] Discarding stale-generation ok response (rid=%s gen=%s).", rid, gen)
                return None
            if rid != req_id:
                logger.warning("[NewsSentiment] Discarding mismatched ok response (rid=%s).", rid)
                return None
            if not isinstance(vectors, np.ndarray) or vectors.ndim != 2 or vectors.shape[1] != _EMBEDDING_DIM:
                raise _WorkerError(f"Malformed vectors payload: shape={getattr(vectors, 'shape', None)}.")
            if not isinstance(meta, Mapping):
                raise _WorkerError(f"Malformed meta payload: {type(meta)}.")
            return vectors, meta
        if status == "error":
            if len(result) == 4:
                rid, gen, payload = result[1], result[2], result[3]
                if gen != generation:
                    logger.warning(
                        "[NewsSentiment] Discarding stale-generation error response (rid=%s gen=%s).", rid, gen
                    )
                    return None
                if rid == req_id or rid == -1:
                    # R9: payload 可能含 traceback（文件路径/系统信息），脱敏后入异常消息。
                    raise _WorkerError(str(DataSanitizer.sanitize_error(payload)))
                logger.warning("[NewsSentiment] Discarding mismatched error response (rid=%s).", rid)
                return None
            if len(result) == 3:
                # 加载级/worker 级广播形态 ("error", msg, gen)。
                gen, payload = result[2], result[1]
                if gen != generation:
                    return None
                raise _WorkerError(str(DataSanitizer.sanitize_error(payload)))
            raise _WorkerError(f"Malformed error result (len={len(result)}).")
        if status == "shutdown":
            gen = _response_generation(result)
            if gen is not None and gen != generation:
                return None
            raise _WorkerError("Worker shut down unexpectedly during scoring.")
        raise _WorkerError(f"Unknown result status from worker: {status!r}.")

    # ------------------------------------------------------------------ #
    # quarantined worker 异步清理（S2，与 LocalModelManager 同型）
    # ------------------------------------------------------------------ #
    def _quarantine_worker(self, generation: int) -> None:
        """标记 worker quarantined 并 spawn 异步清理任务（须在事件循环线程调用）。

        取消/超时后 worker 状态未知（可能仍在推理）→ 隔离 + 异步清理；
        ``_ensure_worker_impl`` 快路径因 quarantined 失效，下次调用走完整
        shutdown+respawn 自愈。
        """
        with self._worker_lock:
            self._worker_state = "quarantined"
        self._spawn_cleanup_task(generation)

    def _spawn_cleanup_task(self, generation: int) -> None:
        """Spawn（或收编）quarantined worker 的异步清理任务（须在事件循环线程调用）。

        同一 generation 已有清理任务在跑则幂等；目标 generation 已过时的
        旧任务被取消（其 generation 守卫保证它本也会幂等退出）。
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
            # 有界终止 force 语义：terminate 1.5s + kill 0.5s。
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
                "[NewsSentiment] Cleanup of quarantined worker failed (%s): %s",
                exc_info=True,
            )

    # ------------------------------------------------------------------ #
    # S2 统一单连接入口（ADR-0014 §6.1）
    # ------------------------------------------------------------------ #
    @log_async_operation(threshold_ms=PerfThreshold.AI_INFERENCE, log_args=False)
    async def score_for_connection(
        self,
        texts: list[str],
        resolved_connection: ResolvedConnection,
        deadline: float,
        attempt_context: AttemptContext,
    ) -> ScoringOutcome:
        """统一单连接情绪打分：不读全局配置、不返回 None。

        - ``resolved_connection.model`` = 模型路径；``adapter_options`` 携带
          head_path / tokenizer_path / n_threads / max_length——配置快照语义由
          调用方（S3 ScenarioExecutor）保证，本方法不读 ConfigHandler；
        - deadline 为单调时钟截止值（loop.time() 口径）：ready 等待 =
          min(180, remaining)，单块轮询 = min(30, remaining)；块循环预算耗尽 →
          已完成块以 partial 返回（零完成 → timeout）；
        - 不可用（adapter_kind 不符 / 依赖缺失 / 路径缺失 / 文件校验失败）→
          unavailable；ready 失败 → worker_error；worker 请求级失败 →
          worker_error + 已完成块；
        - 取消/超时 → worker 隔离 + 异步清理（generation 守卫，见
          ``_score_chunk``）；``CancelledError`` 传播（R2），不包装。

        Args:
            texts: 待打分文本列表（text_id = 全局批内下标的十进制字符串）。
            attempt_context: 尝试归属（S3 Executor 落账语义；scorer 以 req_id
                响应配对兜底，不建 inflight 绑定）。

        Returns:
            ScoringOutcome：usage 仅统计已完成块的 input_tokens（本地无缓存
            语义，uncached == input_total；无 token 计量 → None）。
        """
        if resolved_connection.adapter_kind != "gguf_sentiment":
            return ScoringOutcome(
                status="unavailable",
                results=(),
                reason=f"adapter_kind={resolved_connection.adapter_kind!r} is not gguf_sentiment.",
                usage=None,
            )
        if not texts:
            return ScoringOutcome(
                status="success",
                results=(),
                reason=None,
                usage=None,
                metadata=MappingProxyType({"processed": 0, "input_tokens": (), "truncated": ()}),
            )
        if not _HAS_LLAMA_CPP or not _HAS_TOKENIZERS:
            return ScoringOutcome(
                status="unavailable",
                results=(),
                reason="llama-cpp-python / tokenizers not installed.",
                usage=None,
            )

        adapter_options = resolved_connection.adapter_options
        model_path = resolved_connection.model
        head_path = str(adapter_options.get("head_path", ""))
        tokenizer_path = str(adapter_options.get("tokenizer_path", ""))
        if not model_path or not head_path or not tokenizer_path:
            return ScoringOutcome(
                status="unavailable",
                results=(),
                reason="model/head/tokenizer path missing in connection.",
                usage=None,
            )
        paths = {"model_path": model_path, "head_path": head_path, "tokenizer_path": tokenizer_path}

        loop = asyncio.get_running_loop()
        if deadline - loop.time() <= 0:
            return ScoringOutcome(
                status="timeout",
                results=(),
                reason="Deadline exhausted before scoring dispatch.",
                usage=None,
            )

        # GGUF 魔数 + tokenizer 存在性校验（同步 Path 操作 offload 到 IO 线程池，R16）。
        valid = await ThreadPoolManager().run_async(TaskType.IO, self._validate_input_files, paths)
        if not valid:
            return ScoringOutcome(
                status="unavailable",
                results=(),
                reason="Invalid GGUF model file or missing tokenizer file.",
                usage=None,
            )

        core_config = {
            "n_ctx": _DEFAULT_N_CTX,
            "n_threads": max(1, int(adapter_options.get("n_threads", _DEFAULT_N_THREADS))),
            # 上限 clamp 至 n_ctx-8：max_length 超过上下文容量会使 add_sequence
            # 反复溢出 → worker 崩溃（与 _core_config 同口径）。
            "max_length": min(_DEFAULT_N_CTX - 8, max(8, int(adapter_options.get("max_length", _DEFAULT_MAX_LENGTH)))),
        }

        async with self._get_lock():
            remaining = deadline - loop.time()
            if remaining <= 0:
                return ScoringOutcome(
                    status="timeout",
                    results=(),
                    reason="Deadline exhausted before scorer ready.",
                    usage=None,
                )
            try:
                head = await self._ensure_ready(
                    paths,
                    core_config=core_config,
                    ready_timeout=min(_READY_TIMEOUT_S, remaining),
                )
            except asyncio.CancelledError:
                # 启动/ready 阶段取消：worker 子进程可能正在加载（状态未知）→
                # 隔离 + 异步清理（取消隔离覆盖启动/ready/提交/推理全程，R2 传播）。
                self._quarantine_worker(self._worker_generation)
                raise
            if head is None:
                if deadline - loop.time() <= 0:
                    return ScoringOutcome(
                        status="timeout",
                        results=(),
                        reason="Deadline exhausted while waiting for scorer ready.",
                        usage=None,
                    )
                return ScoringOutcome(
                    status="worker_error",
                    results=(),
                    reason="Scorer failed to become ready (load/start failure).",
                    usage=None,
                )

            results: list[ScoredText] = []
            input_tokens_all: list[int | None] = []
            truncated_all: list[bool] = []
            processed_total = 0
            chunk_offset = 0
            outcome_status: str = "success"
            outcome_reason: str | None = None

            for chunk in _chunked(texts, _SCORE_CHUNK_SIZE):
                chunk_budget = min(_SCORE_CHUNK_TIMEOUT_S, deadline - loop.time())
                if chunk_budget <= 0:
                    outcome_status = "timeout" if not results else "partial"
                    outcome_reason = "Deadline exhausted during chunked scoring."
                    break
                try:
                    scored = await self._score_chunk(
                        chunk,
                        timeout_s=chunk_budget,
                        generation=self._worker_generation,
                    )
                except TimeoutError:
                    outcome_status = "timeout" if not results else "partial"
                    outcome_reason = "Chunk scoring budget exhausted; worker quarantined."
                    break
                if scored is None:
                    outcome_status = "worker_error" if not results else "partial"
                    outcome_reason = "Scoring chunk failed (worker error); worker terminated."
                    break

                vectors, meta = scored
                items = apply_head_and_score(vectors, head)
                for row, item in enumerate(items):
                    text_id = str(chunk_offset + row)
                    if item is None:
                        results.append(ScoredText(text_id=text_id, label=None, probabilities=None, score=None))
                    else:
                        results.append(
                            ScoredText(
                                text_id=text_id,
                                label=item["label"],
                                probabilities=tuple(item["probs"]),
                                score=item["score"],
                            )
                        )
                meta_tokens = meta.get("input_tokens")
                meta_truncated = meta.get("truncated")
                if isinstance(meta_tokens, list):
                    input_tokens_all.extend(meta_tokens)
                if isinstance(meta_truncated, list):
                    truncated_all.extend(meta_truncated)
                processed_total += int(meta.get("processed", 0) or 0)
                chunk_offset += len(chunk)

            token_ints = [t for t in input_tokens_all if isinstance(t, int) and not isinstance(t, bool)]
            usage = None
            if token_ints:
                total = sum(token_ints)
                usage = NormalizedLLMUsage(
                    input_total_tokens=total,
                    uncached_input_tokens=total,
                    cache_read_tokens=None,
                    cache_write_tokens=None,
                    output_tokens=None,
                )
            metadata: Mapping[str, Any] | None = None
            if outcome_status in ("success", "partial"):
                metadata = MappingProxyType(
                    {
                        "processed": processed_total,
                        "input_tokens": tuple(input_tokens_all),
                        "truncated": tuple(truncated_all),
                    }
                )
            return ScoringOutcome(
                status=outcome_status,  # type: ignore[arg-type]  # outcome_status 仅由本方法字面量赋值
                results=tuple(results),
                reason=outcome_reason,
                usage=usage,
                metadata=metadata,
            )
