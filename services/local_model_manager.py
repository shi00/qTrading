import logging
import asyncio
import hashlib
import os
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
    _is_loading: bool = False
    _load_lock = asyncio.Lock()

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

        async with self._load_lock:
            # Check MD5 to avoid reload if same file
            target_md5 = await ThreadPoolManager().run_async(TaskType.IO, self.calculate_file_md5, model_path)
            
            # Simple check: same file and same config? 
            # Ideally we should check if config changed too. 
            # For now, we assume caller handles reload if config changes.
            if self._llm and self._model_md5 and target_md5 == self._model_md5:
                logger.info(f"[LocalModel] Model already loaded (MD5 match).")
                # TODO: If config changed (e.g. threads), we SHOULD reload.
                # But detecting config change requires storing old config.
                # Since we force reload on settings save, this is acceptable.
                return True
            
            self._is_loading = True
            logger.info(f"[LocalModel] Scheduling model load from {model_path} on TaskType.CPU...")
            start_time = asyncio.get_event_loop().time()
            
            try:
                self._llm = await ThreadPoolManager().run_async(
                    TaskType.CPU,
                    self._create_llama_instance,
                    model_path,
                    config
                )
                self._model_path = model_path
                self._model_md5 = target_md5
                
                elapsed = asyncio.get_event_loop().time() - start_time
                logger.info(f"[LocalModel] Model loaded successfully in {elapsed:.2f}s.")
                return True
            except Exception as e:
                self._llm = None
                logger.error(f"[LocalModel] Failed to load model: {e}", exc_info=True)
                return False
            finally:
                self._is_loading = False

    @staticmethod
    def _create_llama_instance(model_path: str, config: Dict[str, Any]) -> 'Llama':
        """
        Sync factory method to create Llama instance with config.
        """
        import threading
        logger.info(f"[LocalModel] Initializing Llama in thread: {threading.current_thread().name}")
        
        return Llama(
            model_path=model_path,
            n_threads=config.get('n_threads', 4),
            n_batch=config.get('n_batch', 512),
            n_ctx=config.get('n_ctx', 4096),
            n_gpu_layers=config.get('n_gpu_layers', 0),
            flash_attn=config.get('flash_attn', True),
            verbose=False
        )

    async def run_inference(self, prompt: str, max_tokens: int = 512, temperature: float = 0.7, system_prompt: str = "You are a helpful assistant.") -> str:
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
        
        # Check if we need to reload (model path changed)
        if self._llm and self._model_path != path:
            logger.info(f"[LocalModel] Model path changed (Old: {self._model_path}, New: {path}). Reloading...")
            self.unload_model() # Force unload to free memory before loading new one
            
        if not self._llm:
            # Try auto-loading if path is configured
            if path:
                success = await self.load_model(path)
                if not success:
                    raise RuntimeError("Model failed to auto-load.")
            else:
                raise RuntimeError("Model not configured (no path set).")

        if not self._llm:
            raise RuntimeError("No model loaded.")

        logger.info(f"[LocalModel] Scheduling inference on TaskType.CPU. Input len: {len(prompt)}, Max tokens: {max_tokens}, Temp: {temperature}")
        start_time = asyncio.get_event_loop().time()
        
        # Serialize inference to prevent native crash (Llama instance is not thread-safe)
        async with self._load_lock: 
            try:
                # Get timeout from config (default 30s)
                # We use 'local_model_timeout' as standardized in ConfigHandler
                timeout_val = config.get('local_model_timeout', 90) or 90
                
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
            except Exception as e:
                logger.error(f"[LocalModel] Inference error: {e}", exc_info=True)
                raise RuntimeError(f"Inference execution failed: {e}") from e

    def _generate_sync(self, prompt: str, max_tokens: int, temperature: float, system_prompt: str) -> str:
        """
        Sync generation logic.
        """
        import threading
        logger.info(f"[LocalModel] Running generation in thread: {threading.current_thread().name}")

        if not self._llm:
            raise ValueError("Model is None inside worker thread")

        # Using create_chat_completion is safer for instruction tuned models if we follow OpenAI format
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
        
        response = self._llm.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature
        )
        
        return response['choices'][0]['message']['content']

    def unload_model(self):
        """Free memory"""
        if self._llm:
            del self._llm
            self._llm = None
            logger.info("[LocalModel] Model unloaded.")
