import asyncio
import hashlib
import importlib
import logging
import multiprocessing
import os
import threading
from typing import Any, Optional

from utils.config_handler import ConfigHandler
from utils.loop_local import get_loop_local
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)


class LocalInferenceTimeoutError(RuntimeError):
    """Raised when local model inference exceeds the configured timeout."""

    pass


def _subprocess_inference_worker(
    model_path: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    system_prompt: str,
    config: dict,
    result_queue: multiprocessing.Queue,
):
    """Run LLM inference in an isolated subprocess.

    This function is the entry point for multiprocessing.Process.
    On timeout, the parent process terminates this subprocess, which
    guarantees that all native memory (llama.cpp mmap, CUDA buffers)
    is released by the OS — no leaked threads or GPU allocations.
    """
    try:
        llama_cpp_module = importlib.import_module("llama_cpp")
        Llama = llama_cpp_module.Llama
        llama_types_module = importlib.import_module("llama_cpp.llama_types")
        ChatCompletionRequestSystemMessage = llama_types_module.ChatCompletionRequestSystemMessage
        ChatCompletionRequestUserMessage = llama_types_module.ChatCompletionRequestUserMessage
    except (ImportError, AttributeError) as e:
        result_queue.put(("error", f"llama-cpp-python import failed: {e}"))
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
        result_queue.put(("error", str(e)))


# Try importing llama_cpp dynamically, handle missing dependency gracefully.
# Dynamic import avoids hard static dependency for type check in CI.
try:
    llama_cpp_module = importlib.import_module("llama_cpp")
    Llama = llama_cpp_module.Llama  # type: ignore[attr-defined]
    _HAS_LLAMA_CPP = True
except (ImportError, AttributeError):
    Llama = Any  # type: ignore[assignment]
    _HAS_LLAMA_CPP = False
    logger.warning(
        "llama-cpp-python not installed. Embedded AI features will be disabled.",
    )

if _HAS_LLAMA_CPP:
    try:
        llama_types_module = importlib.import_module("llama_cpp.llama_types")
        ChatCompletionRequestSystemMessage = llama_types_module.ChatCompletionRequestSystemMessage  # type: ignore[attr-defined]
        ChatCompletionRequestUserMessage = llama_types_module.ChatCompletionRequestUserMessage  # type: ignore[attr-defined]
    except (ImportError, AttributeError):
        ChatCompletionRequestSystemMessage = dict
        ChatCompletionRequestUserMessage = dict
else:
    ChatCompletionRequestSystemMessage = dict
    ChatCompletionRequestUserMessage = dict


from utils.singleton_registry import register_singleton


@register_singleton
class LocalModelManager:
    """
    Manages the lifecycle of the embedded Llama.cpp model.
    - Singleton instance of Llama model.
    - Thread-safe inference using ThreadPoolManager (CPU/GPU bound).
    """

    _instance: Optional["LocalModelManager"] = None
    _initialized: bool = False
    _lock = threading.Lock()
    _llm: Any | None = None
    _model_path: str = ""
    _model_md5: str = ""
    _model_stat: tuple = (0, 0)
    _last_config: dict = {}
    _is_loading: bool = False
    _cancel_event: threading.Event = threading.Event()

    @classmethod
    def _reset_singleton(cls):
        """Reset singleton for testing only. NEVER call in production."""
        with cls._lock:
            if cls._instance is not None and cls._instance._llm is not None:
                cls._instance.unload_model()
            cls._instance = None
            cls._initialized = False

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
                cls._initialized = True
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._llm = None
        self._last_config = {}

    def get_loaded_model_path(self) -> str:
        """Return the path of the currently loaded model, or empty string if none."""
        return self._model_path

    def get_loaded_model_md5(self) -> str:
        """Return the MD5 hash of the currently loaded model, or empty string if none."""
        return self._model_md5

    @staticmethod
    def calculate_file_md5(file_path: str) -> str:
        """
        Calculate MD5 hash of a file. Runs synchronously, should be called from thread pool.
        Uses chunked reading to handle large files efficiently.
        """
        hash_md5 = hashlib.md5()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192 * 1024), b""):  # 8MB chunks
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except Exception as e:
            logger.error(f"[LocalModel] Failed to calculate MD5: {e}")
            return ""

    async def load_model(self, model_path: str, config: dict[str, Any] | None = None) -> bool:
        """
        Load the model from GGUF file.
        Run in CPU thread pool to avoid blocking main loop.
        """
        if not _HAS_LLAMA_CPP:
            logger.error("Cannot load model: llama-cpp-python is not installed.")
            return False

        if not os.path.exists(model_path):
            logger.error(f"Model file not found: {model_path}")
            return False

        # Get config if not provided
        if config is None:
            config = ConfigHandler.get_local_ai_config()

        core_config = {
            "n_threads": config.get("n_threads", 4),
            "n_batch": config.get("n_batch", 1024),
            "n_ctx": config.get("n_ctx", 4096),
            "n_gpu_layers": config.get("n_gpu_layers", 0),
            "flash_attn": config.get("flash_attn", True),
        }

        async with self._get_load_lock():
            # OPTIMIZATION: Check path equality AND file modification/size to avoid expensive MD5.
            try:
                stat = os.stat(model_path)
                current_stat = (stat.st_mtime, stat.st_size)
            except OSError:
                current_stat = (0, 0)

            if (
                self._llm
                and self._model_path == model_path
                and self._model_stat == current_stat
                and self._last_config == core_config
            ):
                # Path is same, file unchanged, and config unchanged -> Skip reload
                return True

            # If path different OR file changed (mtime/size mismatch) -> Load it
            # We calculate MD5 during load for integrity logging, but don't rely on it for "change detection"
            # because calculating it is the bottleneck we want to avoid.

            logger.info(
                f"[LocalModel] Loading model from {model_path} (Stat: {current_stat})...",
            )

            self._is_loading = True
            start_time = asyncio.get_running_loop().time()

            try:
                # 1. Calculate MD5 (Async IO) - Optional but good for logs
                # We do this in parallel or before loading?
                # Let's do it before, as verification. It takes time, but only happens ONCE per file change now.
                logger.info("[LocalModel] Verifying file integrity (MD5)...")
                target_md5 = await ThreadPoolManager().run_async(
                    TaskType.IO,
                    self.calculate_file_md5,
                    model_path,
                )

                # 2. Load Model (Async CPU)
                logger.info("[LocalModel] Scheduling model load on TaskType.CPU...")
                self._llm = await ThreadPoolManager().run_async(
                    TaskType.CPU,
                    self._create_llama_instance,
                    model_path,
                    config,
                )

                # Update State
                self._model_path = model_path
                self._model_md5 = target_md5
                self._model_stat = current_stat
                self._last_config = core_config

                elapsed = asyncio.get_running_loop().time() - start_time
                logger.info(
                    f"[LocalModel] Model loaded successfully in {elapsed:.2f}s.",
                )
                return True
            except Exception as e:
                self._llm = None
                self._model_path = ""  # Reset on failure
                self._model_stat = (0, 0)
                self._last_config = {}
                logger.error(f"[LocalModel] Failed to load model: {e}", exc_info=True)
                return False
            finally:
                self._is_loading = False

    @staticmethod
    def _create_llama_instance(model_path: str, config: dict[str, Any]) -> Any:
        """
        Sync factory method to create Llama instance with config.
        """
        logger.info(
            f"[LocalModel] Initializing Llama in thread: {threading.current_thread().name}",
        )

        return Llama(  # type: ignore[operator]
            model_path=model_path,
            n_threads=config.get("n_threads", 4),
            n_batch=config.get("n_batch", 1024),
            n_ctx=config.get("n_ctx", 4096),
            n_gpu_layers=config.get("n_gpu_layers", 0),
            flash_attn=config.get("flash_attn", True),
            verbose=False,
        )

    async def run_inference(
        self,
        prompt: str,
        max_tokens: int = 150,
        temperature: float = 0.7,
        system_prompt: str = "You are a helpful assistant.",
    ) -> str:
        """
        Run inference on the loaded model using subprocess isolation.

        The LLM runs in a child process so that on timeout the parent can
        terminate() it and the OS reclaims all native memory (mmap, CUDA
        buffers) immediately — no leaked threads or GPU allocations.

        Raises:
            RuntimeError: If model cannot be loaded or inference fails.
            ImportError: If llama-cpp-python is missing.
            LocalInferenceTimeoutError: If inference exceeds the configured timeout.
        """
        if not _HAS_LLAMA_CPP:
            raise ImportError("llama-cpp-python not installed.")

        config = ConfigHandler.get_local_ai_config()
        path = config.get("local_model_path", "")

        if path:
            success = await self.load_model(path)
            if not success:
                raise RuntimeError("Model failed to load/reload.")
        else:
            raise RuntimeError("Model not configured (no path set).")

        if not self._llm:
            raise RuntimeError("No model loaded.")

        timeout_val = config.get("local_model_timeout", 90) or 90

        core_config = {
            "n_threads": config.get("n_threads", 4),
            "n_batch": config.get("n_batch", 1024),
            "n_ctx": config.get("n_ctx", 4096),
            "n_gpu_layers": config.get("n_gpu_layers", 0),
            "flash_attn": config.get("flash_attn", True),
        }

        logger.info(
            f"[LocalModel] Scheduling subprocess inference. "
            f"Input len: {len(prompt)}, Max tokens: {max_tokens}, "
            f"Temp: {temperature}, Timeout: {timeout_val}s",
        )
        start_time = asyncio.get_running_loop().time()

        result_queue = multiprocessing.Queue()
        proc = multiprocessing.Process(
            target=_subprocess_inference_worker,
            args=(
                self._model_path,
                prompt,
                max_tokens,
                temperature,
                system_prompt,
                core_config,
                result_queue,
            ),
            daemon=True,
        )
        proc.start()

        result = None
        try:
            deadline = asyncio.get_running_loop().time() + float(timeout_val)
            while proc.is_alive():
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    result = result_queue.get_nowait()
                    if result is not None:
                        break
                except Exception:
                    pass
                await asyncio.sleep(min(0.2, remaining))

            if result is None:
                try:
                    result = result_queue.get_nowait()
                except Exception:
                    pass

            if result is None and proc.is_alive():
                raise TimeoutError()
        except TimeoutError as te:
            proc.terminate()
            proc.join(timeout=5)
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=2)
            elapsed = asyncio.get_running_loop().time() - start_time
            logger.error(
                f"[LocalModel] Subprocess inference timed out after {timeout_val}s "
                f"(elapsed: {elapsed:.1f}s). Process terminated.",
            )
            raise LocalInferenceTimeoutError(
                f"Local inference timed out ({timeout_val}s). Subprocess terminated, memory freed by OS.",
            ) from te
        finally:
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=5)
                if proc.is_alive():
                    proc.kill()
                    proc.join(timeout=2)
            result_queue.close()
            result_queue.join_thread()

        if result is None:
            raise RuntimeError("Inference subprocess exited without producing a result.")

        status, payload = result
        elapsed = asyncio.get_running_loop().time() - start_time

        if status == "error":
            logger.error(f"[LocalModel] Inference error: {payload}")
            raise RuntimeError(f"Inference execution failed: {payload}")

        logger.info(
            f"[LocalModel] Subprocess inference completed in {elapsed:.2f}s. Output len: {len(payload)}",
        )
        return payload

    def _generate_sync(self, prompt: str, max_tokens: int, temperature: float, system_prompt: str) -> str:
        """
        Sync generation logic using streaming to enable cooperative cancellation.

        .. deprecated::
            This method is retained for backward compatibility only.
            run_inference() now uses subprocess isolation via
            _subprocess_inference_worker, which guarantees memory cleanup
            on timeout. Prefer subprocess isolation for all new code.
        """
        logger.info(
            f"[LocalModel] Running generation in thread: {threading.current_thread().name}",
        )

        if not self._llm:
            raise ValueError("Model is None inside worker thread")

        # Using create_chat_completion is safer for instruction tuned models if we follow OpenAI format
        messages = [
            ChatCompletionRequestSystemMessage(role="system", content=system_prompt),
            ChatCompletionRequestUserMessage(role="user", content=prompt),
        ]

        # Stream=True enables cooperative cancellation: each token yield is a
        # checkpoint where we can inspect _cancel_event and break out.
        stream = self._llm.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=True,
        )

        collected_content: list[str] = []
        for chunk in stream:
            # Cooperative cancellation: check between token yields
            if self._cancel_event.is_set():
                logger.warning(
                    "[LocalModel] Generation cancelled by timeout signal. "
                    f"Partial output: {len(collected_content)} chunks collected.",
                )
                break

            choices = chunk.get("choices", [])
            if choices:
                delta = choices[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    collected_content.append(content)

        return "".join(collected_content)

    def unload_model(self):
        """Free memory and signal any running inference to stop."""
        # Ensure any in-flight streaming generation sees the cancel signal
        self._cancel_event.set()
        if self._llm:
            del self._llm
            self._llm = None
            logger.info("[LocalModel] Model unloaded.")
