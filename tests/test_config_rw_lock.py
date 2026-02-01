import threading
import time
import sys
import os

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.config_handler import ConfigHandler

def test_config_thread_safety():
    print("Starting Config Thread Safety Test...")
    
    # Initialize config
    ConfigHandler.save_config({"test_counter": 0})
    
    NUM_WRITERS = 20
    NUM_READERS = 40
    
    errors = []
    
    def writer_task(idx):
        try:
            # Read-Modify-Write cycle
            # In a real app, this would be inside a lock or we accept last-write-wins.
            # But ConfigHandler.save_config handles the merge internally now with a lock?
            # actually save_config merges into current state.
            # But we need to ensure the "get current -> increment" logic is atomic if we want strict counting.
            # ConfigHandler doesn't provide "atomic increment", it provides "atomic save".
            # So if 2 threads read 0, and both save 1, the result is 1, not 2.
            # This test specifically tests that save_config doesn't CORRUPT the file.
            # To test locking, we can rely on the fact that save_config holds the write lock.
            
            # Use atomic update simulation
            # We can't actually easily test "increment" without an atomic-increment API.
            # But we can test that we don't crash and file is valid JSON at end.
            
            ConfigHandler.save_config({f"w_{idx}": idx})
        except Exception as e:
            errors.append(e)

    def reader_task():
        try:
            cfg = ConfigHandler.load_config()
            # Just read something
            _ = cfg.get("test_counter")
        except Exception as e:
            errors.append(e)

    threads = []
    for i in range(NUM_WRITERS):
        t = threading.Thread(target=writer_task, args=(i,))
        threads.append(t)
        
    for i in range(NUM_READERS):
        t = threading.Thread(target=reader_task)
        threads.append(t)

    start = time.time()
    for t in threads:
        t.start()
        
    for t in threads:
        t.join()
    end = time.time()
    
    print(f"Finished in {end - start:.4f}s")
    
    if errors:
        print(f"Errors occurred: {errors}")
        sys.exit(1)
        
    # Verify file integrity
    final_config = ConfigHandler.load_config()
    print("Final config loaded successfully.")
    
    # Check if all writers succeeded
    success_count = 0
    for i in range(NUM_WRITERS):
        if final_config.get(f"w_{i}") == i:
            success_count += 1
            
    print(f"Writer success rate: {success_count}/{NUM_WRITERS}")
    
    if success_count == NUM_WRITERS:
        print("TEST PASSED: Integrity maintained and all writes succeeded.")
    else:
        print("TEST FAILED: Some writes lost (expected via merge logic) or corrupted.")

if __name__ == "__main__":
    test_config_thread_safety()
