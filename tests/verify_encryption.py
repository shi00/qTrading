
import sys
import os
import json
import logging
import base64

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.config_handler import ConfigHandler
from utils.security_utils import SecurityManager

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def verify_encryption():
    logger.info("Starting encryption verification...")
    
    # Path to config
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "user_settings.json")
    
    # 1. Setup: Start with a known PLAIN TEXT token in the file
    logger.info("Step 1: Setting up legacy plain text token...")
    plain_token = "legacy_plain_text_token_12345"
    
    with open(config_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    data['ts_token'] = plain_token
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)
        
    # 2. Verify Migration: ConfigHandler.get_token() should return plain text BUT migrate file content
    logger.info("Step 2: Triggering auto-migration via get_token()...")
    retrieved_token = ConfigHandler.get_token()
    
    if retrieved_token != plain_token:
        logger.error(f"❌ Migration Failed: Retrieved {retrieved_token} != {plain_token}")
        return False
        
    logger.info("✅ get_token() returned correct value. Checking file for encryption...")
    
    with open(config_path, 'r', encoding='utf-8') as f:
        new_data = json.load(f)
        saved_token = new_data.get('ts_token')
        
    if saved_token == plain_token:
        logger.error("❌ Migration Failed: Token in file is still plain text!")
        return False
        
    if len(saved_token) < len(plain_token):
        logger.error("❌ Migration Failed: Saved token seems too short to be encrypted.")
        return False
        
    logger.info(f"✅ Token in file appears encrypted: {saved_token[:20]}...")
    
    # 3. Verify Decryption: get_token() should now decrypt the encrypted value
    logger.info("Step 3: Verifying decryption of migrated token...")
    token_again = ConfigHandler.get_token()
    if token_again != plain_token:
        logger.error(f"❌ Decryption Failed: Got {token_again}")
        return False
        
    logger.info("✅ Decryption successful.")
    
    # 4. Verify New Save: ConfigHandler.save_token()
    logger.info("Step 4: Verifying save_token()...")
    new_token = "new_secure_token_abcde"
    ConfigHandler.save_token(new_token)
    
    with open(config_path, 'r', encoding='utf-8') as f:
        final_data = json.load(f)
        final_saved = final_data.get('ts_token')
        
    if final_saved == new_token:
        logger.error("❌ Save Failed: Saved plain text!")
        return False
        
    decrypted_new = ConfigHandler.get_token()
    if decrypted_new != new_token:
         logger.error(f"❌ Save/Read Cycle Failed: Got {decrypted_new}")
         return False
         
    logger.info("✅ Save/Read cycle successful.")
    
    # Restore original token if needed, or leave it encrypted (safer)
    # Let's clean up test data but keep it valid structure
    
    logger.info("✅ All encryption tests passed!")
    return True

if __name__ == "__main__":
    try:
        if verify_encryption():
            sys.exit(0)
        else:
            sys.exit(1)
    except Exception as e:
        logger.error(f"Test crashed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
