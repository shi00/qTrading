import base64
import contextlib
import hashlib
import logging
import os
import platform
import secrets
import shutil

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from config import APP_ROOT

logger = logging.getLogger(__name__)

_MACHINE_SALT_FILE = os.path.join(APP_ROOT, ".secret.salt")
_LEGACY_MARKER = os.path.join(APP_ROOT, ".secret.legacy")


def _get_machine_fingerprint():
    """Derive a machine-specific fingerprint for key derivation."""
    parts = [
        platform.node(),
        platform.system(),
        platform.machine(),
        str(os.getuid() if hasattr(os, "getuid") else os.environ.get("USERNAME", "unknown")),
    ]
    return "|".join(parts).encode("utf-8")


def _derive_key_from_machine(salt: bytes) -> bytes:
    """Derive a 256-bit key from machine fingerprint using PBKDF2."""
    fingerprint = _get_machine_fingerprint()
    return hashlib.pbkdf2_hmac("sha256", fingerprint, salt, iterations=600_000, dklen=32)


def _hide_file_windows(filepath):
    """Set hidden attribute on Windows, or owner-only permissions on Linux/macOS."""
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.SetFileAttributesW.restype = ctypes.c_bool
            kernel32.SetFileAttributesW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
            FILE_ATTRIBUTE_HIDDEN = 0x2
            result = kernel32.SetFileAttributesW(filepath, FILE_ATTRIBUTE_HIDDEN)
            if not result:
                logger.debug(f"SetFileAttributesW returned False for {filepath}")
        except Exception as e:
            logger.debug(f"Failed to hide file via Win32 API: {e}")
    else:
        try:
            os.chmod(filepath, 0o600)
        except OSError as e:
            logger.debug(f"Failed to set permissions on {filepath}: {e}")


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
        Fallback: derives key from machine fingerprint using PBKDF2.
        """
        if cls._key is not None:
            return cls._key

        # 1. Try to load existing key file
        if os.path.exists(cls.KEY_FILE):
            try:
                cls._key = cls._load_key_file(cls.KEY_FILE)
                if not os.path.exists(cls.KEY_FILE_BAK):
                    cls._copy_file(cls.KEY_FILE, cls.KEY_FILE_BAK)
                    _hide_file_windows(cls.KEY_FILE_BAK)
                cls._ensure_legacy_marker()
                return cls._key
            except Exception as e:
                logger.error(f"Failed to load primary key file: {e}")

        # 2. Try to recover from backup
        if os.path.exists(cls.KEY_FILE_BAK):
            logger.warning("Attempting to recover key from backup...")
            try:
                cls._key = cls._load_key_file(cls.KEY_FILE_BAK)
                cls._copy_file(cls.KEY_FILE_BAK, cls.KEY_FILE)
                _hide_file_windows(cls.KEY_FILE)
                logger.info("Key successfully recovered from backup.")
                cls._ensure_legacy_marker()
                return cls._key
            except Exception as e:
                logger.critical(f"Failed to load backup key file: {e}")
                raise RuntimeError(
                    "CRITICAL: Both primary and backup key files are corrupt. Cannot proceed.",
                ) from e

        # 3. If key file exists but is unreadable, bail out
        if os.path.exists(cls.KEY_FILE):
            raise RuntimeError(
                "CRITICAL: Key file exists but is unreadable (and no backup found). "
                "Manual intervention required to prevent data loss. "
                "Delete '.secret.key' manually to reset (WARNING: All encrypted data will be lost).",
            )

        # 4. No key file exists: check for legacy marker (data may be undecryptable)
        if os.path.exists(_LEGACY_MARKER):
            logger.warning(
                "Legacy key marker found but key file is missing. "
                "Previously encrypted data (API keys, tokens) may be undecryptable. "
                "Run SecurityManager.migrate_to_derived_key() to re-encrypt with the new key, "
                "or delete .secret.legacy to suppress this warning (data will be lost)."
            )

        # 5. Derive key from machine fingerprint (PBKDF2)
        logger.info("Deriving security key from machine fingerprint (PBKDF2)...")
        salt = cls._get_or_create_salt()
        cls._key = _derive_key_from_machine(salt)
        return cls._key

    @classmethod
    def _ensure_legacy_marker(cls):
        """Create a marker file when using file-based key to aid future migration."""
        if not os.path.exists(_LEGACY_MARKER):
            try:
                with open(_LEGACY_MARKER, "w", encoding="utf-8") as f:
                    f.write("legacy-file-key\n")
                _hide_file_windows(_LEGACY_MARKER)
            except (OSError, PermissionError) as exc:
                logger.debug(f"[SecurityManager] Failed to write legacy marker: {exc}")

    @classmethod
    def migrate_to_derived_key(cls, decrypt_fn=None, encrypt_fn=None):
        """
        Migrate from file-based key to PBKDF2-derived key.

        Args:
            decrypt_fn: Callable that decrypts a value using the old key.
                        Should accept (encrypted_text) and return plaintext.
            encrypt_fn: Callable that encrypts a value using the new key.
                        Should accept (plaintext) and return encrypted_text.

        Returns:
            True if migration succeeded, False otherwise.

        Usage:
            # Example: migrate config values
            def re_encrypt(value):
                plain = SecurityManager.decrypt_data(value)
                return SecurityManager.encrypt_data(plain)

            SecurityManager.migrate_to_derived_key(
                decrypt_fn=SecurityManager.decrypt_data,
                encrypt_fn=re_encrypt,
            )
        """
        if not os.path.exists(cls.KEY_FILE):
            logger.info("No file-based key found. Already using derived key.")
            return True

        try:
            cls._load_key_file(cls.KEY_FILE)
        except Exception as e:
            logger.error(f"Cannot read old key file for migration: {e}")
            return False

        salt = cls._get_or_create_salt()
        new_key = _derive_key_from_machine(salt)

        if decrypt_fn and encrypt_fn:
            logger.info("Migrating encrypted data to new key...")
            logger.warning(
                "Custom decrypt/encrypt functions provided but automatic re-encryption "
                "of config values must be handled by the caller. "
                "Use ConfigHandler to re-encrypt ts_token, db_password, ai_api_key after migration."
            )

        for path in (cls.KEY_FILE, cls.KEY_FILE_BAK, _LEGACY_MARKER):
            if os.path.exists(path):
                with contextlib.suppress(OSError):
                    os.remove(path)

        cls._key = new_key
        logger.info("Migration complete: switched to PBKDF2-derived key.")
        return True

    @classmethod
    def _get_or_create_salt(cls):
        """Load or generate a random salt for PBKDF2 key derivation."""
        if os.path.exists(_MACHINE_SALT_FILE):
            try:
                with open(_MACHINE_SALT_FILE, "rb") as f:
                    salt = f.read()
                    if len(salt) >= 16:
                        return salt
            except (OSError, ValueError) as exc:
                logger.debug(f"[SecurityManager] Salt file read failed, will generate: {exc}")

        salt = secrets.token_bytes(32)
        tmp_file = _MACHINE_SALT_FILE + ".tmp"
        try:
            with open(tmp_file, "wb") as f:
                f.write(salt)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, _MACHINE_SALT_FILE)
            _hide_file_windows(_MACHINE_SALT_FILE)
        except Exception as e:
            logger.error(f"Error saving salt file: {e}")
            if os.path.exists(tmp_file):
                with contextlib.suppress(OSError):
                    os.remove(tmp_file)
            raise
        return salt

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

            # Hide the files on Windows
            _hide_file_windows(cls.KEY_FILE)
            _hide_file_windows(cls.KEY_FILE_BAK)
        except Exception as e:
            logger.error(f"Error saving key: {e}")
            if os.path.exists(tmp_file):
                with contextlib.suppress(OSError):
                    os.remove(tmp_file)
            raise

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
