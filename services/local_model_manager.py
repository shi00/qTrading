import asyncio
import hashlib
import logging
import os
import threading
from typing import Optional, Dict, Any

from utils.config_handler import ConfigHandler
from utils.thread_pool import ThreadPoolManager, TaskType

logger = logging.getLogger(__name__)

# Try importing llama_cpp, handle missing dependency gracefully
try:
    from llama_cpp import Llama

    _HAS_LLAMA_CPP = True
except ImportError:
    _HAS_LLAMA_CPP = False
    logger.warning("llama-cpp-python not installed. Embedded AI features will be disabled.")

if _HAS_LLAMA_CPP:
    try:
        from llama_cpp.llama_types import ChatCompletionRequestSystemMessage, ChatCompletionRequestUserMessage
    except ImportError:
        # Fallback or handle older versions if strictly necessary, but requirements say >=0.3.2
        ChatCompletionRequestSystemMessage = dict
        ChatCompletionRequestUserMessage = dict


class LocalModelManager:
    """
    Manages the lifecycle of the embedded Llama.cpp model.
    - Singleton instance of Llama model.
    - Thread-safe inference using ThreadPoolManager (CPU/GPU bound).
    """
    _instance: Optional['LocalModelManager'] = None
    _llm: Optional['Llama'] = None
    _model_path: str = ""
    _model_md5: str = ""  # MD5 hash of loaded model file
    _model_stat: tuple = (0, 0)  # (mtime, size)
    _is_loading: bool = False
    _load_lock = None  # Lazy init to avoid cross-event-loop binding

    @classmethod
    def _get_load_lock(cls):
        """Get or create load lock dynamically per event loop to avoid cross-loop binding deadlocks."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("[LocalModel] No running event loop for load lock. Using dummy lock.")
            class DummyLock:
                async def __aenter__(self): return
                async def __aexit__(self, *args): return
            return DummyLock()

        if not hasattr(current_loop, '_local_load_lock'):
            setattr(current_loop, '_local_load_lock', asyncio.Lock())
            
        return getattr(current_loop, '_local_load_lock')

    @classmethod
    async def get_instance(cls):
        if cls._instance is None:
            cls._instance = LocalModelManager()
        return cls._instance

    def __init__(self):
        self._llm = None

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
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(8192 * 1024), b""):  # 8MB chunks
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except Exception as e:
            logger.error(f"[LocalModel] Failed to calculate MD5: {e}")
            return ""

    async def load_model(self, model_path: str, config: Optional[Dict[str, Any]] = None) -> bool:
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

        async with self._get_load_lock():
            # OPTIMIZATION: Check path equality AND file modification/size to avoid expensive MD5.
            try:
                stat = os.stat(model_path)
                current_stat = (stat.st_mtime, stat.st_size)
            except OSError:
                current_stat = (0, 0)

            if self._llm and self._model_path == model_path and self._model_stat == current_stat:
                # Path is same AND file matches cached timestamp/size -> Skip reload
                return True

            # If path different OR file changed (mtime/size mismatch) -> Load it
            # We calculate MD5 during load for integrity logging, but don't rely on it for "change detection"
            # because calculating it is the bottleneck we want to avoid.

            logger.info(f"[LocalModel] Loading model from {model_path} (Stat: {current_stat})...")

            self._is_loading = True
            start_time = asyncio.get_event_loop().time()

            try:
                # 1. Calculate MD5 (Async IO) - Optional but good for logs
                # We do this in parallel or before loading? 
                # Let's do it before, as verification. It takes time, but only happens ONCE per file change now.
                logger.info("[LocalModel] Verifying file integrity (MD5)...")
                target_md5 = await ThreadPoolManager().run_async(TaskType.IO, self.calculate_file_md5, model_path)

                # 2. Load Model (Async CPU)
                logger.info(f"[LocalModel] Scheduling model load on TaskType.CPU...")
                self._llm = await ThreadPoolManager().run_async(
                    TaskType.CPU,
                    self._create_llama_instance,
                    model_path,
                    config
                )

                # Update State
                self._model_path = model_path
                self._model_md5 = target_md5
                self._model_stat = current_stat

                elapsed = asyncio.get_event_loop().time() - start_time
                logger.info(f"[LocalModel] Model loaded successfully in {elapsed:.2f}s.")
                return True
            except Exception as e:
                self._llm = None
                self._model_path = ""  # Reset on failure
                self._model_stat = (0, 0)
                logger.error(f"[LocalModel] Failed to load model: {e}", exc_info=True)
                return False
            finally:
                self._is_loading = False

    @staticmethod
    def _create_llama_instance(model_path: str, config: Dict[str, Any]) -> 'Llama':
        """
        Sync factory method to create Llama instance with config.
        """
        logger.info(f"[LocalModel] Initializing Llama in thread: {threading.current_thread().name}")

        return Llama(
            model_path=model_path,
            n_threads=config.get('n_threads', 4),
            n_batch=config.get('n_batch', 1024),
            n_ctx=config.get('n_ctx', 4096),
            n_gpu_layers=config.get('n_gpu_layers', 0),
            flash_attn=config.get('flash_attn', True),
            verbose=False
        )

    async def run_inference(self, prompt: str, max_tokens: int = 150, temperature: float = 0.7,
                            system_prompt: str = "You are a helpful assistant.") -> str:
        """
        Run inference on the loaded model.
        Blocking call, must run in Executor.
        Raises:
            RuntimeError: If model cannot be loaded or inference fails.
            ImportError: If llama-cpp-python is missing.
        """
        if not _HAS_LLAMA_CPP:
            raise ImportError("llama-cpp-python not installed.")

        config = ConfigHandler.get_local_ai_config()
        path = config.get("local_model_path", "")

        # UNIFIED LOADING LOGIC:
        # We delegate everything to load_model(), which now has the smart "stat" check.
        # This replaces the old weak logic that only checked path string and didn't detect file changes.
        if path:
            success = await self.load_model(path)
            if not success:
                raise RuntimeError("Model failed to load/reload.")
        else:
            raise RuntimeError("Model not configured (no path set).")

        if not self._llm:
            raise RuntimeError("No model loaded.")

        # Get timeout from config (default 90s)
        timeout_val = config.get('local_model_timeout', 90) or 90

        logger.info(
            f"[LocalModel] Scheduling inference on TaskType.CPU. Input len: {len(prompt)}, Max tokens: {max_tokens}, Temp: {temperature}, Timeout: {timeout_val}s")
        start_time = asyncio.get_event_loop().time()

        # Serialize inference to prevent native crash (Llama instance is not thread-safe)
        async with self._get_load_lock():
            try:
                # Run in thread pool with timeout
                output = await asyncio.wait_for(
                    ThreadPoolManager().run_async(
                        TaskType.CPU,
                        self._generate_sync,
                        prompt,
                        max_tokens,
                        temperature,
                        system_prompt
                    ),
                    timeout=float(timeout_val)
                )
                elapsed = asyncio.get_event_loop().time() - start_time
                logger.info(f"[LocalModel] Inference completed in {elapsed:.2f}s. Output len: {len(output)}")
                return output
            except asyncio.TimeoutError as te:
                logger.error(f"[LocalModel] Inference timed out after {timeout_val}s.", exc_info=False)
                # FATAL: The underlying thread is still running _generate_sync synchronously!
                # We MUST destroy the model instance to free memory and forcefully crash the C++ generation loop,
                # otherwise we leak a fully-loaded CPU thread that will spin until it finishes.
                self.unload_model()
                self._model_path = ""
                self._model_stat = (0, 0)
                raise RuntimeError(f"Local inference timed out ({timeout_val}s). Memory freed.") from te
            except Exception as e:
                logger.error(f"[LocalModel] Inference error: {e}", exc_info=True)
                # Cleanup on unexpected errors as well just to be safe
                self.unload_model()
                self._model_path = ""
                self._model_stat = (0, 0)
                raise RuntimeError(f"Inference execution failed: {e}") from e

    def _generate_sync(self, prompt: str, max_tokens: int, temperature: float, system_prompt: str) -> str:
        """
        Sync generation logic.
        """
        logger.info(f"[LocalModel] Running generation in thread: {threading.current_thread().name}")

        if not self._llm:
            raise ValueError("Model is None inside worker thread")

        # Using create_chat_completion is safer for instruction tuned models if we follow OpenAI format
        messages = [
            ChatCompletionRequestSystemMessage(role="system", content=system_prompt),
            ChatCompletionRequestUserMessage(role="user", content=prompt)
        ]

        response = self._llm.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature
            # Removed dangerous custom stop words (like "```" or "\n\n") 
            # which caused valid JSON truncation and len=0 outputs.
        )

        return response['choices'][0]['message']['content']

    def unload_model(self):
        """Free memory"""
        if self._llm:
            del self._llm
            self._llm = None
            logger.info("[LocalModel] Model unloaded.")
