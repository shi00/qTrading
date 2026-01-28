import os
import base64
import secrets
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from config import APP_ROOT

class SecurityManager:
    """
    Manages AES-GCM encryption for sensitive data.
    Key is stored in .secret.key file in APP_ROOT.
    """
    KEY_FILE = os.path.join(APP_ROOT, ".secret.key")
    _key = None

    @classmethod
    def get_key(cls):
        """Load or generate the 256-bit AES key"""
        if cls._key is not None:
            return cls._key
            
        if os.path.exists(cls.KEY_FILE):
            try:
                with open(cls.KEY_FILE, "rb") as f:
                    encoded_key = f.read()
                    cls._key = base64.b64decode(encoded_key)
                    return cls._key
            except Exception as e:
                print(f"Error loading key: {e}")
                # If loading fails, we might want to regenerate or fail hard.
                # Here we continue to regeneration if file is empty/corrupt? 
                # Better to fail to avoid data loss confusion, but for now allow regen if file gone.
                pass
        
        # Generate new key
        cls._key = AESGCM.generate_key(bit_length=256) # 32 bytes
        try:
            with open(cls.KEY_FILE, "wb") as f:
                f.write(base64.b64encode(cls._key))
            # Hide the file on Windows
            if os.name == 'nt':
                os.system(f'attrib +h "{cls.KEY_FILE}"')
        except Exception as e:
            print(f"Error saving key: {e}")
            
        return cls._key

    @classmethod
    def encrypt_data(cls, plaintext):
        """
        Encrypt plaintext using AES-GCM.
        Returns: base64(nonce + ciphertext + tag)
        """
        if not plaintext:
            return ""
            
        try:
            key = cls.get_key()
            aesgcm = AESGCM(key)
            nonce = secrets.token_bytes(12)  # NIST recommended 96-bit nonce
            
            # encrypt() returns ciphertext + tag
            data = plaintext.encode('utf-8')
            ciphertext = aesgcm.encrypt(nonce, data, None)
            
            return base64.b64encode(nonce + ciphertext).decode('utf-8')
        except Exception as e:
            print(f"Encryption error: {e}")
            return plaintext # Fallback or Raise? Safe to return empty or original? 
            # Returning original is bad for security. Returning empty is safer.
            return ""

    @classmethod
    def decrypt_data(cls, encrypted_text):
        """
        Decrypt base64 encoded string.
        """
        if not encrypted_text:
            return ""
            
        try:
             # Basic sanity check: if it doesn't look like base64, assume plain text (legacy support)
            # But caller usually handles migration.
            decoded = base64.b64decode(encrypted_text.encode('utf-8'))
            
            if len(decoded) < 28: # 12 nonce + 16 tag
                raise ValueError("Data too short")
                
            nonce = decoded[:12]
            ciphertext = decoded[12:]
            
            key = cls.get_key()
            aesgcm = AESGCM(key)
            
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
            return plaintext.decode('utf-8')
        except Exception as e:
            # This is common during migration (trying to decrypt plain text)
            # print(f"Decryption error: {e}") 
            raise e
