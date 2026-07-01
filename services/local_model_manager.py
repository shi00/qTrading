from __future__ import annotations

import asyncio
import hashlib
import importlib
import logging
import multiprocessing
import os
import queue
import threading
import time
import traceback
from typing import Any

from utils.config_handler import ConfigHandler
from utils.loop_local import del_loop_local, get_loop_local
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)


class LocalInferenceTimeoutError(RuntimeError):
    """Raised when local model inference exceeds the configured timeout."""

    pass


_SENTINEL = "__SHUTDOWN__"
_VERIFICATION_TIMEOUT_SECONDS = 300


def _persistent_worker(  # pragma: no cover — runs in subprocess, not coverable by unit tests
    model_path: str,
    config: dict,
    request_queue: multiprocessing.Queue,
    result_queue: multiprocessing.Queue,
):
    """Persistent subprocess worker that loads the model once and serves requests.

    Communication protocol:
    - Parent sends a tuple via request_queue:
        (prompt, max_tokens, temperature, system_prompt)
    - Worker puts result via result_queue:
        ("ok", output_text) or ("error", error_message)
    - Parent sends _SENTINEL to request graceful shutdown.
    - On model load failure, worker puts ("error", ...) and exits.
    """
    try:
        llama_cpp_module = importlib.import_module("llama_cpp")
        Llama = llama_cpp_module.Llama
        llama_types_module = importlib.import_module("llama_cpp.llama_types")
        ChatCompletionRequestSystemMessage = llama_types_module.ChatCompletionRequestSystemMessage
        ChatCompletionRequestUserMessage = llama_types_module.ChatCompletionRequestUserMessage
    except (ImportError, AttributeError) as e:
        result_queue.put(("error", f"llama-cpp-python import failed: {e}\n{traceback.format_exc()}"))
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
        result_queue.put(("ready", model_path))
    except Exception as e:
        result_queue.put(("error", f"Model load failed: {e}\n{traceback.format_exc()}"))
        return

    while True:
        try:
            request = request_queue.get(timeout=1.0)
        except queue.Empty:
            continue
        except Exception as e:
            logger.debug("[LocalModel] Worker queue get error: %s", e, exc_info=True)
            continue

        if request == _SENTINEL:
            result_queue.put(("shutdown", "ok"))
            break

        if not isinstance(request, tuple) or len(request) != 4:
            result_queue.put(("error", f"Invalid request format: {type(request)}"))
            continue

        prompt, max_tokens, temperature, system_prompt = request
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
            result_queue.put(("ok", output))
        except Exception as e:
            result_queue.put(("error", f"{e}\n{traceback.format_exc()}"))


try:
    importlib.import_module("llama_cpp")
    _HAS_LLAMA_CPP = True
except (ImportError, AttributeError):
    _HAS_LLAMA_CPP = False
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
                    inst._shutdown_worker()
                except Exception as e:
                    logger.warning("[LocalModel] Error during reset shutdown: %s", e, exc_info=True)
            cls._instance = None
            cls._initialized = False
        del_loop_local("local_load_lock")

    @classmethod
    def _atexit_cleanup(cls):
        """Release subprocess and queues on process exit. Called by singleton_registry."""
        inst = cls._instance
        if inst is not None:
            try:
                inst._shutdown_worker()
            except Exception as e:
                logger.warning("[LocalModel] Error during atexit shutdown: %s", e, exc_info=True)

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
        self._initialized = True

    def _shutdown_worker(self, *, force: bool = False):
        """Shut down the persistent worker subprocess (thread-safe).

        Args:
            force: True 时跳过 sentinel 直接 terminate。用于关机路径——
                   worker 可能正忙于推理，sentinel 要等推理完成才会被处理。
        """
        with self._worker_lock:
            self._shutdown_worker_locked(force=force)

    def _shutdown_worker_locked(self, *, force: bool = False):
        """Internal: shut down worker. Caller MUST hold _worker_lock.

        Args:
            force: True 时跳过 sentinel 直接 terminate（关机路径使用）。
        """
        if self._request_queue is not None and self._worker_proc is not None and self._worker_proc.is_alive():
            if not force:
                try:
                    self._request_queue.put(_SENTINEL, timeout=2.0)
                except Exception as e:
                    logger.debug("[LocalModel] Failed to send sentinel to worker: %s", e, exc_info=True)
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
                self._worker_proc.terminate()
                self._worker_proc.join(timeout=3)
                if self._worker_proc.is_alive():
                    self._worker_proc.kill()
                    self._worker_proc.join(timeout=1)
            logger.info("[LocalModel] Persistent worker shut down.")

        if self._request_queue is not None:
            try:
                self._request_queue.close()
                self._request_queue.join_thread()
            except Exception as e:
                logger.debug("[LocalModel] Failed to close request queue: %s", e, exc_info=True)
        if self._result_queue is not None:
            try:
                self._result_queue.close()
                self._result_queue.join_thread()
            except Exception as e:
                logger.debug("[LocalModel] Failed to close result queue: %s", e, exc_info=True)

        self._worker_proc = None
        self._request_queue = None
        self._result_queue = None
        self._worker_ready = False

    def _ensure_worker(self, model_path: str, core_config: dict) -> bool:
        """Start a persistent worker subprocess with the given model.

        Returns True if worker process was started, False on failure.
        Thread-safe: protected by _worker_lock.
        Call _await_worker_ready() after this to wait for model loading.
        """
        with self._worker_lock:
            if (
                self._worker_proc is not None
                and self._worker_proc.is_alive()
                and self._worker_ready
                and self._model_path == model_path
            ):
                return True

            self._shutdown_worker_locked()

            self._request_queue = multiprocessing.Queue()
            self._result_queue = multiprocessing.Queue()

            proc = multiprocessing.Process(
                target=_persistent_worker,
                args=(model_path, core_config, self._request_queue, self._result_queue),
                daemon=True,
            )
            proc.start()
            self._worker_proc = proc
            logger.info("[LocalModel] Persistent worker started. pid=%s", proc.pid)
            return True

    async def _await_worker_ready(self, timeout: float = 180.0) -> bool:
        """Wait for the persistent worker to become ready without blocking the event loop.

        Uses a polling approach (like run_inference) so we can detect a crashed
        subprocess early instead of blocking on result_queue.get() for the full timeout.
        """
        result_queue = self._result_queue
        if result_queue is None:
            logger.error("[LocalModel] Result queue not initialized.")
            return False

        result = None
        worker_died = False
        deadline = asyncio.get_running_loop().time() + timeout

        while True:
            if self._cancel_event.is_set():
                logger.info("[LocalModel] Cancel event detected while waiting for worker ready.")
                self._worker_ready = False
                self._shutdown_worker()
                return False

            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break

            # Non-blocking check for result (use captured ref to avoid TOCTOU race)
            try:
                result = result_queue.get_nowait()
                break
            except queue.Empty:
                pass
            except OSError:
                logger.warning("[LocalModel] OSError reading result queue while waiting for worker ready.")
                pass

            # Detect subprocess crash early (capture proc locally to avoid TOCTOU race)
            proc = self._worker_proc
            if proc is not None and not proc.is_alive():
                # One last attempt to read any residual result
                try:
                    result = result_queue.get_nowait()
                except (queue.Empty, OSError):
                    pass
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
            self._shutdown_worker()
            return False

        status, payload = result
        if status == "ready":
            self._worker_ready = True
            logger.info("[LocalModel] Persistent worker ready with model: %s", payload)
            return True

        logger.error("[LocalModel] Persistent worker failed: %s", payload)
        self._worker_ready = False
        self._shutdown_worker()
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
            logger.error("[LocalModel] Failed to calculate SHA-256: %s", DataSanitizer.sanitize_error(e))
            return ""

    @staticmethod
    def calculate_file_md5(file_path: str) -> str:
        """Backward-compatible alias for calculate_file_sha256."""
        return LocalModelManager.calculate_file_sha256(file_path)

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

        if not os.path.exists(model_path):
            logger.error("Model file not found: %s", model_path)
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
                    if not self._ensure_worker(model_path, core_config):
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
                            logger.warning("[LocalModel] Failed to persist model SHA-256: %s", e, exc_info=True)

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
                    logger.error("[LocalModel] Failed to load model: %s", e, exc_info=True)
                    return False
                finally:
                    self._is_loading = False
        finally:
            if is_verification and self._model_path != model_path:
                self._verification_mode = False
                self._verification_start_time = 0.0
                logger.warning("[LocalModel] Verification failed, exiting verification mode.")

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
            self.cancel_verification()

        core_config = {
            "n_threads": config.get("n_threads", 4),
            "n_batch": config.get("n_batch", 1024),
            "n_ctx": config.get("n_ctx", 4096),
            "n_gpu_layers": config.get("n_gpu_layers", 0),
            "flash_attn": config.get("flash_attn", True),
        }

        if not self._ensure_worker(path, core_config):
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

        try:
            loop = asyncio.get_running_loop()
            if self._request_queue is None:
                raise RuntimeError("Request queue not initialized")
            request_queue = self._request_queue
            await loop.run_in_executor(
                None,
                lambda: request_queue.put((prompt, max_tokens, temperature, system_prompt), timeout=5),
            )
        except Exception as e:
            logger.error("[LocalModel] Failed to send request to worker: %s", e, exc_info=True)
            self._worker_ready = False
            raise RuntimeError(f"Failed to send request to worker: {e}") from e

        result = None
        worker_died = False
        # Capture result_queue locally to avoid TOCTOU race with _shutdown_worker
        result_queue = self._result_queue
        if result_queue is None:
            raise RuntimeError("Result queue not initialized")
        try:
            deadline = asyncio.get_running_loop().time() + float(timeout_val)
            while True:
                if self._cancel_event.is_set():
                    logger.info("[LocalModel] Cancel event detected during inference polling, aborting.")
                    self._shutdown_worker()
                    raise RuntimeError("Inference cancelled by user (unload_model called).")
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    result = result_queue.get_nowait()
                    break
                except queue.Empty:
                    pass
                # Capture proc locally to avoid TOCTOU race
                proc = self._worker_proc
                if proc is not None and not proc.is_alive():
                    try:
                        result = result_queue.get_nowait()
                    except queue.Empty:
                        pass
                    worker_died = True
                    break
                await asyncio.sleep(min(0.2, remaining))

            if result is None:
                try:
                    result = result_queue.get_nowait()
                except queue.Empty:
                    pass

            if result is None and not worker_died:
                raise TimeoutError()
        except TimeoutError as te:
            elapsed = asyncio.get_running_loop().time() - start_time
            logger.error(
                "[LocalModel] Persistent worker inference timed out after %ss (elapsed: %.1fs). Terminating worker.",
                timeout_val,
                elapsed,
            )
            self._shutdown_worker()
            raise LocalInferenceTimeoutError(
                f"Local inference timed out ({timeout_val}s). Worker terminated, will restart on next call.",
            ) from te

        if result is None:
            self._worker_ready = False
            raise RuntimeError("Inference worker exited without producing a result.")

        status, payload = result
        elapsed = asyncio.get_running_loop().time() - start_time

        if status == "error":
            logger.error("[LocalModel] Inference error: %s", payload)
            raise RuntimeError(f"Inference execution failed: {payload}")

        if status == "shutdown":
            self._worker_ready = False
            raise RuntimeError("Worker shut down unexpectedly during inference.")

        logger.info(
            "[LocalModel] Persistent worker inference completed in %.2fs. Output len: %s",
            elapsed,
            len(payload),
        )
        return payload

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
        """Synchronously cancel verification. Safe to call from sync code (e.g. will_unmount)."""
        inst = cls._instance
        if inst is not None:
            inst.cancel_verification()
