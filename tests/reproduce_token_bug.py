
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.config_handler import ConfigHandler
from utils.security_utils import SecurityManager
from unittest.mock import patch, MagicMock

def test_behavior():
    print("Testing ConfigHandler.get_token() behavior when decryption fails...")
    
    with patch('utils.config_handler.ConfigHandler.load_config') as mock_load:
        # Simulate loading a config with an encrypted token (Base64-like string)
        # This string represents "Encrypted data that fails to decrypt"
        # Must be long enough to fail the _is_valid_tushare_format check (e.g. > 60 chars) which real encrypted tokens are (> 100 chars)
        fake_encrypted_token = "r+QtTLoPxVao9WiLgd33MBgqfB7S8jI7wDMVI71a6EsH9pEzfI7n38GasfOsiOYpmRv+f0Ic7HeNc2WDdgi7ceWSjtY2djpvt3yC249GQDVFc0kQ" 
        mock_load.return_value = {"ts_token": fake_encrypted_token}
        
        with patch('utils.security_utils.SecurityManager.decrypt_data') as mock_decrypt:
            # Simulate decryption failure (e.g. wrong key)
            mock_decrypt.side_effect = Exception("Decryption failed")
            
            # Mock save_config so we don't actually write to disk
            with patch('utils.config_handler.ConfigHandler.save_config') as mock_save:
                
                # Mock SecurityManager.encrypt_data to just return "ENCRYPTED_" + data
                with patch('utils.security_utils.SecurityManager.encrypt_data') as mock_encrypt:
                    mock_encrypt.side_effect = lambda x: "ENCRYPTED_" + x

                    with patch('utils.security_utils.SecurityManager.get_key') as mock_get_key:
                        
                        result = ConfigHandler.get_token()
                        
                        print(f"Input Token (from config): {fake_encrypted_token}")
                        print(f"Result from get_token(): {result}")
                        
                        if result == fake_encrypted_token:
                            print("BUG CONFIRMED: ConfigHandler returns the encrypted blob when decryption fails, assuming it is plaintext.")
                        elif result == "":
                            print("Result is empty string. (Desired behavior for invalid tokens)")
                        else:
                            print(f"Result is something else: {result}")

if __name__ == "__main__":
    test_behavior()
