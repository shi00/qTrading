import asyncio
import time
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from services.local_model_manager import LocalModelManager
from utils.config_handler import ConfigHandler
from utils.thread_pool import ThreadPoolManager

async def run_benchmark():
    print("--- Starting Qwen Benchmark ---")
    
    # Force GPU layers if available (User said they have issues, maybe try enabling it?)
    # But first, let's test current performance
    current_config = ConfigHandler.get_local_ai_config()
    print(f"Current Config: {current_config}")
    
    manager = await LocalModelManager.get_instance()
    
    # Ensure model is loaded
    print("Loading model...")
    s = time.time()
    path = current_config['local_model_path']
    if not os.path.exists(path):
        print(f"Error: Model not found at {path}")
        return

    # Reload with different settings for benchmark
    # Test 1: CPU Only (Current)
    print("\n--- Test 1: Baseline (Current Settings) ---")
    await manager.load_model(path, current_config)
    
    prompt = "Analyzing the impact of recent Fed rate cuts on gold prices."
    print(f"Prompt: {prompt}")
    
    s = time.time()
    try:
        res = await manager.run_inference(prompt, max_tokens=100)
        e = time.time()
        print(f"Result: {res[:50]}...")
        print(f"Time: {e-s:.2f}s")
    except Exception as e:
        print(f"Failed: {e}")

    # Test 2: Disable Flash Attention (Often buggy on CPU)
    print("\n--- Test 2: CPU + No Flash Attn ---")
    new_config = current_config.copy()
    new_config['flash_attn'] = False
    
    await manager.load_model(path, new_config) # Force reload? logic in manager might skip if path same
    # We need to force reload. Manager checks stat.
    # We can't easily force reload without modifying Manager or touching file.
    # Actually load_model takes config argument. 
    # But LocalModelManager._create_llama_instance uses the passed config.
    # The check `if self._llm and ...` might prevent re-creation if path is same.
    # We need to unload first.
    manager.unload_model()
    await manager.load_model(path, new_config)
    
    s = time.time()
    try:
        res = await manager.run_inference(prompt, max_tokens=100)
        e = time.time()
        print(f"Time: {e-s:.2f}s")
    except Exception as e:
        print(f"Failed: {e}")

    # Test 3: GPU Layers (if supported)
    print("\n--- Test 3: GPU Optimized (n_gpu_layers=-1) ---")
    new_config = current_config.copy()
    new_config['n_gpu_layers'] = -1
    new_config['flash_attn'] = False 
    
    manager.unload_model()
    await manager.load_model(path, new_config)
    
    s = time.time()
    try:
        res = await manager.run_inference(prompt, max_tokens=100)
        e = time.time()
        print(f"Time: {e-s:.2f}s")
    except Exception as e:
        print(f"Failed (GPU likely not available or configured): {e}")

if __name__ == "__main__":
    asyncio.run(run_benchmark())
