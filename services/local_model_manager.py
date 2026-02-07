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
        
    async def load_model(self, model_path: str) -> bool:
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

        async with self._load_lock:
            # ---------------------------------------------------------------
            # Defensive Check: Prevent redundant model loading
            # ---------------------------------------------------------------
            # In current design, load_model() is called once at startup.
            # This MD5 check guards against:
            #   1. Accidental multiple calls with same file (skip reload)
            #   2. Different paths pointing to identical file (skip reload)
            #   3. Future hot-reload support (logic already in place)
            # ---------------------------------------------------------------
            target_md5 = await ThreadPoolManager().run_async(
                TaskType.IO,
                self.calculate_file_md5,
                model_path
            )
            
            if self._llm and self._model_md5 and target_md5 == self._model_md5:
                logger.info(f"[LocalModel] Model already loaded (MD5 match: {target_md5[:16]}...)")
                return True
            
            self._is_loading = True
            logger.info(f"[LocalModel] Loading model from {model_path}...")
            start_time = asyncio.get_event_loop().time()
            
            try:
                # Offload heavy loading to thread pool
                self._llm = await ThreadPoolManager().run_async(
                    TaskType.CPU,
                    self._create_llama_instance,
                    model_path
                )
                self._model_path = model_path
                self._model_md5 = target_md5
                
                if self._model_md5:
                    logger.info(f"[LocalModel] Model MD5: {self._model_md5[:16]}...")
                else:
                    logger.warning("[LocalModel] Failed to calculate MD5, change detection may be unreliable.")

                elapsed = asyncio.get_event_loop().time() - start_time
                logger.info(f"[LocalModel] Model loaded successfully in {elapsed:.2f}s. (Path: {model_path})")
                return True
            except Exception as e:
                logger.error(f"[LocalModel] Failed to load model: {e}", exc_info=True)
                self._llm = None
                return False
            finally:
                self._is_loading = False

    @staticmethod
    def _create_llama_instance(model_path: str) -> 'Llama':
        """
        Sync factory method to create Llama instance.
        Running in Executor.
        """
        # Tune these parameters based on requirements
        # n_gpu_layers=-1 attempts to offload all layers to GPU if configured
        # n_ctx=2048 default context window
        return Llama(
            model_path=model_path,
            n_gpu_layers=-1, 
            n_ctx=4096,
            verbose=False
        )

    async def run_inference(self, prompt: str, max_tokens: int = 512, temperature: float = 0.7, system_prompt: str = "You are a helpful assistant.") -> str:
        """
        Run inference on the loaded model.
        Blocking call, must run in Executor.
        """
        if not _HAS_LLAMA_CPP:
            return "Error: llama-cpp-python not installed."
        
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
                    return "Error: Model not loaded and failed to auto-load."
            else:
                return "Error: Model not loaded and no path configured."

        if not self._llm:
            logger.warning("[LocalModel] No model loaded and auto-load failed/disabled.")
            return "Error: Model not loaded."

        logger.info(f"[LocalModel] Starting inference. Input len: {len(prompt)}, Max tokens: {max_tokens}, Temp: {temperature}")
        start_time = asyncio.get_event_loop().time()
        
        # Serialize inference to prevent native crash (Llama instance is not thread-safe)
        async with self._load_lock: 
            try:
                # Run in thread pool
                output = await ThreadPoolManager().run_async(
                    TaskType.CPU,
                    self._generate_sync,
                    prompt,
                    max_tokens,
                    temperature,
                    system_prompt
                )
                elapsed = asyncio.get_event_loop().time() - start_time
                logger.info(f"[LocalModel] Inference completed in {elapsed:.2f}s. Output len: {len(output)}")
                logger.debug(f"[LocalModel] Output snippet: {output[:100]}...")
                return output
            except Exception as e:
                logger.error(f"[LocalModel] Inference error: {e}", exc_info=True)
                return f"Error: {str(e)}"

    def _generate_sync(self, prompt: str, max_tokens: int, temperature: float, system_prompt: str) -> str:
        """
        Sync generation logic.
        """
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
