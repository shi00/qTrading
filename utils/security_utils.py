import base64
import contextlib
import logging
import os
import secrets
import shutil
import subprocess

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from config import APP_ROOT

logger = logging.getLogger(__name__)


class DecryptionError(Exception):
    """Raised when data cannot be decrypted (wrong key or corrupted data)"""

    pass


class SecurityManager:
    """
    Manages AES-GCM encryption for sensitive data.
    Key is stored in .secret.key file in APP_ROOT.

    Improvements:
    - Atomic writes to prevent key corruption.
    - Automatic backup (.secret.key.bak).
    - Strict error handling (no accidental overwrites).
    """

    KEY_FILE = os.path.join(APP_ROOT, ".secret.key")
    KEY_FILE_BAK = os.path.join(APP_ROOT, ".secret.key.bak")
    _key = None

    @classmethod
    def get_key(cls):
        """
        Load or generate the 256-bit AES key.
        Prioritizes safety: tries to recover from backup if main key is corrupt.
        """
        if cls._key is not None:
            return cls._key

        # 1. Try to load existing key
        if os.path.exists(cls.KEY_FILE):
            try:
                cls._key = cls._load_key_file(cls.KEY_FILE)
                # Success! Create backup if not exists
                if not os.path.exists(cls.KEY_FILE_BAK):
                    cls._copy_file(cls.KEY_FILE, cls.KEY_FILE_BAK)
                return cls._key
            except Exception as e:
                logger.error(f"Failed to load primary key file: {e}")
                # Fallthrough to try backup

        # 2. Try to recover from backup
        if os.path.exists(cls.KEY_FILE_BAK):
            logger.warning("Attempting to recover key from backup...")
            try:
                cls._key = cls._load_key_file(cls.KEY_FILE_BAK)
                # Restore main file
                cls._copy_file(cls.KEY_FILE_BAK, cls.KEY_FILE)
                logger.info("Key successfully recovered from backup.")
                return cls._key
            except Exception as e:
                logger.critical(f"Failed to load backup key file: {e}")
                raise RuntimeError(
                    "CRITICAL: Both primary and backup key files are corrupt. Cannot proceed.",
                ) from e

        # 3. If neither exists, and we are sure main file doesn't exist (not just failed to load), generate new.
        # Note: The first check 'os.path.exists(cls.KEY_FILE)' handles the "file exists" case.
        # If we reached here, it means main file is missing AND backup is missing.
        # Double check if main file exists (to avoid race/logic errors where we overwrite a corrupt file we failed to read above)
        if os.path.exists(cls.KEY_FILE):
            # We failed to read it above, so it must be corrupt.
            # Check file size. If 0, maybe safe to overwrite?
            # But safer to bail out and let user decide.
            raise RuntimeError(
                "CRITICAL: Key file exists but is unreadable (and no backup found). "
                "Manual intervention required to prevent data loss. "
                "Delete '.secret.key' manually to reset (WARNING: All encrypted data will be lost).",
            )

        # Generate new key
        logger.info("Generating new security key...")
        cls._key = AESGCM.generate_key(bit_length=256)  # 32 bytes
        cls._save_key(cls._key)
        return cls._key

    @staticmethod
    def _load_key_file(path):
        """Read and decode key from path"""
        with open(path, "rb") as f:
            encoded_key = f.read()
            if not encoded_key:
                raise ValueError("Key file is empty")
            return base64.b64decode(encoded_key)

    @classmethod
    def _save_key(cls, key_bytes):
        """Atomic write of key file"""
        encoded = base64.b64encode(key_bytes)

        # Atomic write: Write to tmp -> Flush -> Sync -> Rename
        tmp_file = cls.KEY_FILE + ".tmp"
        try:
            with open(tmp_file, "wb") as f:
                f.write(encoded)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_file, cls.KEY_FILE)

            # Create backup immediately
            cls._copy_file(cls.KEY_FILE, cls.KEY_FILE_BAK)

            # Hide the file on Windows
            if os.name == "nt":
                subprocess.run(
                    ["attrib", "+h", cls.KEY_FILE],
                    check=False,
                    capture_output=True,
                )
                subprocess.run(
                    ["attrib", "+h", cls.KEY_FILE_BAK],
                    check=False,
                    capture_output=True,
                )
        except Exception as e:
            logger.error(f"Error saving key: {e}")
            if os.path.exists(tmp_file):
                with contextlib.suppress(Exception):
                    os.remove(tmp_file)
            raise e

    @staticmethod
    def _copy_file(src, dst):
        try:
            shutil.copy2(src, dst)
        except Exception as e:
            logger.warning(f"Failed to copy {src} to {dst}: {e}")

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
            data = plaintext.encode("utf-8")
            ciphertext = aesgcm.encrypt(nonce, data, None)

            return base64.b64encode(nonce + ciphertext).decode("utf-8")
        except Exception as e:
            logger.error(f"Encryption error: {e}")
            raise DecryptionError(f"Encryption failed: {e}") from e

    @classmethod
    def decrypt_data(cls, encrypted_text):
        """
        Decrypt base64 encoded string.
        Raises DecryptionError on failure.
        """
        if not encrypted_text:
            return ""

        try:
            # Basic validation
            try:
                decoded = base64.b64decode(encrypted_text.encode("utf-8"))
            except Exception as e:
                raise DecryptionError("Invalid Base64 encoding") from e

            if len(decoded) < 28:  # 12 nonce + 16 tag
                raise DecryptionError("Data too short")

            nonce = decoded[:12]
            ciphertext = decoded[12:]

            key = cls.get_key()
            aesgcm = AESGCM(key)

            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
            return plaintext.decode("utf-8")

        except (ValueError, TypeError) as e:
            raise DecryptionError(f"Data corruption: {e}") from e
        except Exception as e:
            # cryptography library raises built-in exceptions like InvalidTag
            raise DecryptionError(f"Decryption failed (Wrong Key?): {e}") from e
