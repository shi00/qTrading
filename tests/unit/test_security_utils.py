import base64
import pytest
from unittest.mock import patch, MagicMock

from utils.security_utils import (
    SecurityManager,
    DecryptionError,
    _derive_key_from_machine,
    _get_machine_fingerprint,
    _hide_file_windows,
)


class TestMachineFingerprint:
    def test_fingerprint_is_bytes(self):
        fp = _get_machine_fingerprint()
        assert isinstance(fp, bytes)
        assert len(fp) > 0

    def test_fingerprint_deterministic(self):
        fp1 = _get_machine_fingerprint()
        fp2 = _get_machine_fingerprint()
        assert fp1 == fp2


class TestDeriveKeyFromMachine:
    def test_derived_key_length(self):
        salt = b"a" * 32
        key = _derive_key_from_machine(salt)
        assert len(key) == 32

    def test_different_salt_different_key(self):
        key1 = _derive_key_from_machine(b"a" * 32)
        key2 = _derive_key_from_machine(b"b" * 32)
        assert key1 != key2

    def test_same_salt_same_key(self):
        salt = b"x" * 32
        key1 = _derive_key_from_machine(salt)
        key2 = _derive_key_from_machine(salt)
        assert key1 == key2


class TestHideFileWindows:
    def test_non_windows_noop(self):
        with patch("utils.security_utils.os.name", "posix"):
            _hide_file_windows("/some/path")

    def test_windows_ctypes_call(self):
        with patch("utils.security_utils.os.name", "nt"):
            mock_kernel32 = MagicMock()
            mock_kernel32.SetFileAttributesW.return_value = True
            with patch("ctypes.windll", create=True) as mock_windll:
                mock_windll.kernel32 = mock_kernel32
                _hide_file_windows("C:\\test\\file.key")

    def test_no_subprocess_run_usage(self):
        """S-P1-6: _hide_file_windows must use ctypes, not subprocess.run."""
        import utils.security_utils as sec_mod

        assert not hasattr(sec_mod, "subprocess"), (
            "_hide_file_windows should use ctypes.windll instead of subprocess.run"
        )


class TestSecurityManagerGetKey:
    def setup_method(self):
        SecurityManager._key = None

    def test_cached_key_returned_immediately(self):
        SecurityManager._key = b"cached_key_32bytes_long_enough!!"
        result = SecurityManager.get_key()
        assert result == b"cached_key_32bytes_long_enough!!"

    @patch("utils.security_utils.os.path.exists")
    def test_derive_key_when_no_files(self, mock_exists):
        mock_exists.side_effect = lambda p: False
        SecurityManager._key = None

        with patch.object(SecurityManager, "_get_or_create_salt", return_value=b"s" * 32):
            result = SecurityManager.get_key()
            assert result is not None
            assert len(result) == 32

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


class TestSecurityManagerSalt:
    def setup_method(self):
        SecurityManager._key = None

    @patch("utils.security_utils.os.path.exists", return_value=True)
    def test_load_existing_salt(self, mock_exists):
        from unittest.mock import mock_open

        salt_content = b"existing_salt_16bytes"
        with patch("builtins.open", mock_open(read_data=salt_content)):
            result = SecurityManager._get_or_create_salt()
            assert result == salt_content

    @patch("utils.security_utils.os.path.exists")
    def test_create_new_salt_when_missing(self, mock_exists):
        mock_exists.return_value = False

        with (
            patch("builtins.open", MagicMock()),
            patch("utils.security_utils.os.replace"),
            patch("utils.security_utils.os.fsync"),
            patch("utils.security_utils._hide_file_windows"),
        ):
            SecurityManager._key = None
            salt = SecurityManager._get_or_create_salt()
            assert isinstance(salt, bytes)
            assert len(salt) == 32


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


class TestLegacyMarker:
    def setup_method(self):
        SecurityManager._key = None

    @patch("utils.security_utils.os.path.exists")
    def test_ensure_legacy_marker_creates_file(self, mock_exists):
        mock_exists.return_value = False
        with (
            patch("builtins.open", MagicMock()) as mock_open,
            patch("utils.security_utils._hide_file_windows"),
        ):
            SecurityManager._ensure_legacy_marker()
            mock_open.assert_called_once()

    @patch("utils.security_utils.os.path.exists")
    def test_ensure_legacy_marker_skips_if_exists(self, mock_exists):
        mock_exists.return_value = True
        with patch("builtins.open", MagicMock()) as mock_open:
            SecurityManager._ensure_legacy_marker()
            mock_open.assert_not_called()

    @patch("utils.security_utils.os.path.exists")
    def test_legacy_marker_warning_when_key_missing(self, mock_exists):
        from utils.security_utils import _LEGACY_MARKER

        SecurityManager._key = None
        mock_exists.side_effect = lambda p: p == _LEGACY_MARKER

        with (
            patch.object(SecurityManager, "_get_or_create_salt", return_value=b"s" * 32),
            patch("utils.security_utils.logger") as mock_logger,
        ):
            SecurityManager.get_key()
            mock_logger.warning.assert_any_call(
                "Legacy key marker found but key file is missing. "
                "Previously encrypted data (API keys, tokens) may be undecryptable. "
                "Run SecurityManager.migrate_to_derived_key() to re-encrypt with the new key, "
                "or delete .secret.legacy to suppress this warning (data will be lost)."
            )


class TestMigrateToDerivedKey:
    def setup_method(self):
        SecurityManager._key = None

    @patch("utils.security_utils.os.path.exists")
    def test_no_file_key_returns_true(self, mock_exists):
        mock_exists.return_value = False
        result = SecurityManager.migrate_to_derived_key()
        assert result is True

    @patch("utils.security_utils.os.path.exists")
    @patch("utils.security_utils.os.remove")
    def test_migration_deletes_key_files(self, mock_remove, mock_exists):
        mock_exists.return_value = True
        with (
            patch.object(SecurityManager, "_load_key_file", return_value=b"x" * 32),
            patch.object(SecurityManager, "_get_or_create_salt", return_value=b"s" * 32),
        ):
            result = SecurityManager.migrate_to_derived_key()
            assert result is True
            assert mock_remove.call_count == 3

    @patch("utils.security_utils.os.path.exists")
    def test_migration_fails_if_key_unreadable(self, mock_exists):
        mock_exists.return_value = True
        with patch.object(SecurityManager, "_load_key_file", side_effect=Exception("unreadable")):
            result = SecurityManager.migrate_to_derived_key()
            assert result is False


class TestPyInstallerSpecExcludesKeyFiles:
    """S-P1-4: Verify .spec file excludes sensitive key files from packaging."""

    def test_spec_has_key_exclusion_patterns(self):
        import os

        spec_path = os.path.join(os.path.dirname(__file__), "..", "..", "AStockScreener.spec")
        if not os.path.exists(spec_path):
            pytest.skip("AStockScreener.spec not found")
        with open(spec_path, encoding="utf-8") as f:
            content = f.read()
        assert "*.key" in content, "S-P1-4: .spec should exclude *.key files"
        assert "*.salt" in content, "S-P1-4: .spec should exclude *.salt files"

    def test_spec_datas_filtered(self):
        import os

        spec_path = os.path.join(os.path.dirname(__file__), "..", "..", "AStockScreener.spec")
        if not os.path.exists(spec_path):
            pytest.skip("AStockScreener.spec not found")
        with open(spec_path, encoding="utf-8") as f:
            content = f.read()
        assert "_key_patterns" in content, "S-P1-4: .spec should define _key_patterns for exclusion"
        assert "_datas_filtered" in content, "S-P1-4: .spec should filter datas list"
