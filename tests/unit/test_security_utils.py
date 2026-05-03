import base64
import pytest
from unittest.mock import patch, MagicMock

from utils.security_utils import SecurityManager, DecryptionError


class TestSecurityManagerGetKey:
    def setup_method(self):
        SecurityManager._key = None

    def test_cached_key_returned_immediately(self):
        SecurityManager._key = b"cached_key_32bytes_long_enough!!"
        result = SecurityManager.get_key()
        assert result == b"cached_key_32bytes_long_enough!!"

    @patch("utils.security_utils.os.path.exists")
    @patch("utils.security_utils.AESGCM")
    def test_generate_new_key_when_no_files(self, mock_aesgcm, mock_exists):
        mock_exists.side_effect = lambda p: False
        mock_aesgcm.generate_key.return_value = b"new_key_32bytes_generated!"
        SecurityManager._key = None

        with patch.object(SecurityManager, "_save_key"):
            result = SecurityManager.get_key()
            assert result == b"new_key_32bytes_generated!"

    @patch("utils.security_utils.os.path.exists")
    def test_load_existing_key(self, mock_exists):
        key_bytes = b"existing_key_32bytes_long!!"

        mock_exists.side_effect = lambda p: p == SecurityManager.KEY_FILE
        SecurityManager._key = None

        with (
            patch.object(SecurityManager, "_load_key_file", return_value=key_bytes),
            patch.object(SecurityManager, "_copy_file"),
        ):
            result = SecurityManager.get_key()
            assert result == key_bytes

    @patch("utils.security_utils.os.path.exists")
    def test_corrupt_key_recovers_from_backup(self, mock_exists):
        key_bytes = b"backup_key_32bytes_long!!!"
        SecurityManager._key = None

        mock_exists.side_effect = lambda p: p in (SecurityManager.KEY_FILE, SecurityManager.KEY_FILE_BAK)

        with (
            patch.object(SecurityManager, "_load_key_file", side_effect=[Exception("corrupt"), key_bytes]),
            patch.object(SecurityManager, "_copy_file"),
        ):
            result = SecurityManager.get_key()
            assert result == key_bytes

    @patch("utils.security_utils.os.path.exists")
    def test_both_keys_corrupt_raises(self, mock_exists):
        SecurityManager._key = None
        mock_exists.return_value = True

        with patch.object(SecurityManager, "_load_key_file", side_effect=Exception("corrupt")):
            with pytest.raises(RuntimeError, match="CRITICAL"):
                SecurityManager.get_key()

    @patch("utils.security_utils.os.path.exists")
    def test_key_file_exists_but_unreadable_no_backup(self, mock_exists):
        SecurityManager._key = None

        mock_exists.side_effect = lambda p: p == SecurityManager.KEY_FILE

        with patch.object(SecurityManager, "_load_key_file", side_effect=Exception("unreadable")):
            with pytest.raises(RuntimeError, match="unreadable"):
                SecurityManager.get_key()


class TestSecurityManagerEncryptDecrypt:
    def setup_method(self):
        SecurityManager._key = None

    def test_encrypt_empty_string(self):
        result = SecurityManager.encrypt_data("")
        assert result == ""

    def test_decrypt_empty_string(self):
        result = SecurityManager.decrypt_data("")
        assert result == ""

    def test_encrypt_decrypt_roundtrip(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        real_key = AESGCM.generate_key(bit_length=256)
        SecurityManager._key = real_key

        with patch.object(SecurityManager, "get_key", return_value=real_key):
            encrypted = SecurityManager.encrypt_data("hello world")
            decrypted = SecurityManager.decrypt_data(encrypted)
            assert decrypted == "hello world"

    def test_decrypt_invalid_base64(self):
        SecurityManager._key = b"x" * 32
        with pytest.raises(DecryptionError, match="Invalid Base64"):
            SecurityManager.decrypt_data("!!!not-base64!!!")

    def test_decrypt_data_too_short(self):
        SecurityManager._key = b"x" * 32
        short_data = base64.b64encode(b"short").decode()
        with pytest.raises(DecryptionError, match="too short"):
            SecurityManager.decrypt_data(short_data)

    def test_encrypt_failure_raises_decryption_error(self):
        SecurityManager._key = b"x" * 32
        with patch.object(SecurityManager, "get_key", side_effect=Exception("key error")):
            with pytest.raises(DecryptionError, match="Encryption failed"):
                SecurityManager.encrypt_data("test")


class TestSecurityManagerSaveKey:
    def test_save_key_atomic_write(self):
        SecurityManager._key = None
        with (
            patch("builtins.open", MagicMock()),
            patch("utils.security_utils.os.replace"),
            patch("utils.security_utils.os.fsync"),
            patch.object(SecurityManager, "_copy_file"),
            patch("utils.security_utils.os.path.exists", return_value=False),
        ):
            SecurityManager._save_key(b"test_key_32bytes_long_enough!!!")

    def test_save_key_cleanup_on_failure(self):
        tmp_file = SecurityManager.KEY_FILE + ".tmp"
        with (
            patch("builtins.open", side_effect=OSError("write error")),
            patch("utils.security_utils.os.path.exists", return_value=True),
            patch("utils.security_utils.os.remove") as mock_remove,
        ):
            with pytest.raises(OSError, match="write error"):
                SecurityManager._save_key(b"test_key_32bytes_long_enough!!!")
            mock_remove.assert_any_call(tmp_file)


class TestSecurityManagerCopyFile:
    def test_copy_file_success(self):
        with patch("utils.security_utils.shutil.copy2"):
            SecurityManager._copy_file("/src", "/dst")

    def test_copy_file_failure_logs_warning(self):
        with patch("utils.security_utils.shutil.copy2", side_effect=OSError("copy error")):
            SecurityManager._copy_file("/src", "/dst")


class TestDecryptionError:
    def test_is_exception(self):
        err = DecryptionError("test")
        assert isinstance(err, Exception)
        assert str(err) == "test"
