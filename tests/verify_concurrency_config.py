
import sys
import os
import json
import logging

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.config_handler import ConfigHandler
from data.data_processor import DataProcessor

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def verify_concurrency_config():
    logger.info("Starting verification of concurrency configuration...")
    
    # 1. Check initial value
    initial_concurrency = ConfigHandler.get_sync_concurrency()
    logger.info(f"Initial sync_concurrency: {initial_concurrency}")
    
    # 2. Modify value
    new_value = 3
    logger.info(f"Setting sync_concurrency to {new_value}...")
    ConfigHandler.set_sync_concurrency(new_value)
    
    # 3. Verify persistence in file
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "user_settings.json")
    with open(config_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        saved_value = data.get("sync_concurrency")
        
    logger.info(f"Value in user_settings.json: {saved_value}")
    
    if saved_value != new_value:
        logger.error(f"❌ Verification Failed: Expected {new_value}, got {saved_value}")
        return False
        
    # 4. Verify DataProcessor picks it up (ConfigHandler is static, so it should fetch latest)
    # We can't easily check private variables inside a running DataProcessor instance 
    # without deeper introspection or mocking, but picking it up from ConfigHandler is the key.
    read_back_value = ConfigHandler.get_sync_concurrency()
    if read_back_value != new_value:
        logger.error(f"❌ Verification Failed: ConfigHandler.get_sync_concurrency() returned {read_back_value}")
        return False

    # 5. Restore original value (optional, but good practice if we want to leave it at 5)
    # The user wanted it configurable, 5 is a good default.
    logger.info("Restoring sync_concurrency to 5...")
    ConfigHandler.set_sync_concurrency(5)
    
    logger.info("✅ Verification Successful!")
    return True

if __name__ == "__main__":
    success = verify_concurrency_config()
    sys.exit(0 if success else 1)
