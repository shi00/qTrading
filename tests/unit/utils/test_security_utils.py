import base64
import pytest
from unittest.mock import patch, MagicMock

from utils.security_utils import (
    SecurityManager,
    DecryptionError,
    EncryptionError,
    _derive_key_from_machine,
    _get_machine_fingerprint,
    _hide_file_windows,
)

pytestmark = pytest.mark.unit


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
    def test_non_windows_sets_permissions(self):
        with (
            patch("utils.security_utils.os.name", "posix"),
            patch("utils.security_utils.os.chmod") as mock_chmod,
        ):
            _hide_file_windows("/some/path")
            mock_chmod.assert_called_once_with("/some/path", 0o600)

    def test_non_windows_chmod_error_handled(self):
        with (
            patch("utils.security_utils.os.name", "posix"),
            patch(
                "utils.security_utils.os.chmod",
                side_effect=OSError("permission denied"),
            ),
        ):
            _hide_file_windows("/some/path")

    def test_windows_ctypes_call(self):
        with patch("utils.security_utils.os.name", "nt"):
            mock_kernel32 = MagicMock()
            mock_kernel32.SetFileAttributesW.return_value = True
            with patch("ctypes.windll", create=True) as mock_windll:
                mock_windll.kernel32 = mock_kernel32
                _hide_file_windows("C:\\test\\file.key")

    def test_no_subprocess_run_usage(self):
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
    def test_no_key_files_raises_security_error(self, mock_exists):
        from utils.security_utils import SecurityError

        mock_exists.side_effect = lambda p: False
        SecurityManager._key = None

        with pytest.raises(SecurityError):
            SecurityManager.get_key()

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
            patch.object(
                SecurityManager,
                "_load_key_file",
                side_effect=[Exception("corrupt"), key_bytes],
            ),
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

    def test_encrypt_failure_raises_encryption_error(self):
        SecurityManager._key = b"x" * 32
        with patch.object(SecurityManager, "get_key", side_effect=Exception("key error")):
            with pytest.raises(EncryptionError, match="Encryption failed"):
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


class TestEncryptionError:
    def test_is_exception(self):
        err = EncryptionError("test")
        assert isinstance(err, Exception)
        assert str(err) == "test"

    def test_is_security_error(self):
        """EncryptionError must be a subclass of SecurityError so existing except SecurityError blocks still catch it"""
        from utils.security_utils import SecurityError

        err = EncryptionError("test")
        assert isinstance(err, SecurityError)


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
        from utils.security_utils import _LEGACY_MARKER, SecurityError

        SecurityManager._key = None
        mock_exists.side_effect = lambda p: p == _LEGACY_MARKER

        with pytest.raises(SecurityError):
            SecurityManager.get_key()


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
    def test_migration_saves_new_key_and_cleans_up(self, mock_remove, mock_exists):
        mock_exists.return_value = True
        with (
            patch.object(SecurityManager, "_load_key_file", return_value=b"x" * 32),
            patch.object(SecurityManager, "_get_or_create_salt", return_value=b"s" * 32),
            patch.object(SecurityManager, "_save_key"),
        ):
            result = SecurityManager.migrate_to_derived_key()
            assert result is True
            from utils.security_utils import _LEGACY_MARKER

            called_paths = [call.args[0] for call in mock_remove.call_args_list]
            assert _LEGACY_MARKER in called_paths
            assert SecurityManager.KEY_FILE_BAK in called_paths

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

        spec_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "AStockScreener.spec")
        if not os.path.exists(spec_path):
            pytest.skip("AStockScreener.spec not found")
        with open(spec_path, encoding="utf-8") as f:
            content = f.read()
        assert "*.key" in content, "S-P1-4: .spec should exclude *.key files"
        assert "*.salt" in content, "S-P1-4: .spec should exclude *.salt files"

    def test_spec_datas_filtered(self):
        import os

        spec_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "AStockScreener.spec")
        if not os.path.exists(spec_path):
            pytest.skip("AStockScreener.spec not found")
        with open(spec_path, encoding="utf-8") as f:
            content = f.read()
        assert "_key_patterns" in content, "S-P1-4: .spec should define _key_patterns for exclusion"
        assert "_datas_filtered" in content, "S-P1-4: .spec should filter datas list"


class TestSecurityError:
    """P0-7: SecurityError 异常类测试"""

    def test_security_error_is_exception(self):
        from utils.security_utils import SecurityError

        err = SecurityError()
        assert isinstance(err, Exception)

    def test_security_error_default_message(self):
        from utils.security_utils import SecurityError

        err = SecurityError()
        assert "keyring unavailable" in str(err) or "secure" in str(err).lower()
        assert "environment variable" in str(err).lower() or "env" in str(err).lower()

    def test_security_error_custom_message(self):
        from utils.security_utils import SecurityError

        err = SecurityError("custom error message")
        assert str(err) == "custom error message"


class TestSecurityManagerNoFallback:
    """P0-7: 禁止 PBKDF2 降级测试"""

    def setup_method(self):
        SecurityManager._key = None

    @patch("utils.security_utils.os.path.exists")
    def test_no_key_files_raises_security_error(self, mock_exists):
        """无密钥文件时抛出 SecurityError，而非静默降级到 PBKDF2"""
        from utils.security_utils import SecurityError

        mock_exists.side_effect = lambda p: False
        SecurityManager._key = None

        with pytest.raises(SecurityError):
            SecurityManager.get_key()

    @patch("utils.security_utils.os.path.exists")
    def test_legacy_marker_with_no_key_raises_security_error(self, mock_exists):
        """有 legacy 标记但无密钥文件时抛出 SecurityError"""
        from utils.security_utils import SecurityError, _LEGACY_MARKER

        SecurityManager._key = None
        mock_exists.side_effect = lambda p: p == _LEGACY_MARKER

        with pytest.raises(SecurityError):
            SecurityManager.get_key()

    @patch("utils.security_utils.os.path.exists")
    def test_encrypt_without_key_raises_encryption_error(self, mock_exists):
        """无密钥时 encrypt_data 抛出 EncryptionError（包装 SecurityError）"""
        from utils.security_utils import EncryptionError

        mock_exists.return_value = False
        SecurityManager._key = None

        with pytest.raises(EncryptionError, match="Encryption failed"):
            SecurityManager.encrypt_data("secret_data")

    @patch("utils.security_utils.os.path.exists")
    def test_decrypt_without_key_raises_decryption_error(self, mock_exists):
        """无密钥时 decrypt_data 抛出 DecryptionError（包装 SecurityError）"""
        from utils.security_utils import DecryptionError

        mock_exists.return_value = False
        SecurityManager._key = None

        fake_encrypted = base64.b64encode(b"x" * 28).decode()

        with pytest.raises(DecryptionError, match="Decryption failed"):
            SecurityManager.decrypt_data(fake_encrypted)


class TestHasLegacyEncryptedData:
    """P0-7: 检测 legacy 加密数据测试"""

    @patch("utils.security_utils.os.path.exists")
    def test_returns_true_when_legacy_marker_exists(self, mock_exists):
        from utils.security_utils import _LEGACY_MARKER

        mock_exists.side_effect = lambda p: p == _LEGACY_MARKER
        result = SecurityManager.has_legacy_encrypted_data()
        assert result is True

    @patch("utils.security_utils.os.path.exists")
    def test_returns_false_when_no_legacy_marker(self, mock_exists):
        mock_exists.return_value = False
        result = SecurityManager.has_legacy_encrypted_data()
        assert result is False


# ============================================================================
# 以下为 Task 5.2 补充测试：覆盖 security_utils.py 剩余路径，目标覆盖率 ≥80%
# ============================================================================


class TestMachineFingerprintPlatforms:
    """覆盖 _get_machine_fingerprint 在不同平台的输出组合"""

    def test_windows_platform_components(self):
        """Windows 平台：使用 USERNAME 环境变量（模拟无 getuid）"""
        _real_hasattr = hasattr

        def _mock_hasattr(obj, name):
            if name == "getuid":
                return False  # 模拟 Windows 无 getuid
            return _real_hasattr(obj, name)

        with (
            patch("builtins.hasattr", side_effect=_mock_hasattr),
            patch("utils.security_utils.platform.node", return_value="WIN-PC"),
            patch("utils.security_utils.platform.system", return_value="Windows"),
            patch("utils.security_utils.platform.machine", return_value="AMD64"),
            patch.dict("os.environ", {"USERNAME": "alice"}, clear=False),
        ):
            fp = _get_machine_fingerprint()
            assert b"WIN-PC" in fp
            assert b"Windows" in fp
            assert b"AMD64" in fp
            assert b"alice" in fp

    def test_linux_platform_with_getuid(self):
        """Linux 平台：使用 os.getuid()"""
        with (
            patch("utils.security_utils.platform.node", return_value="linux-host"),
            patch("utils.security_utils.platform.system", return_value="Linux"),
            patch("utils.security_utils.platform.machine", return_value="x86_64"),
            patch("utils.security_utils.os.getuid", return_value=1000, create=True),
        ):
            fp = _get_machine_fingerprint()
            assert b"linux-host" in fp
            assert b"Linux" in fp
            assert b"x86_64" in fp
            assert b"1000" in fp

    def test_macos_platform_with_getuid(self):
        """macOS 平台：使用 os.getuid()"""
        with (
            patch("utils.security_utils.platform.node", return_value="macbook"),
            patch("utils.security_utils.platform.system", return_value="Darwin"),
            patch("utils.security_utils.platform.machine", return_value="arm64"),
            patch("utils.security_utils.os.getuid", return_value=501, create=True),
        ):
            fp = _get_machine_fingerprint()
            assert b"macbook" in fp
            assert b"Darwin" in fp
            assert b"arm64" in fp
            assert b"501" in fp

    def test_fallback_when_username_missing(self):
        """Windows 上 USERNAME 缺失时回退到 'unknown'"""
        _real_hasattr = hasattr

        def _mock_hasattr(obj, name):
            if name == "getuid":
                return False
            return _real_hasattr(obj, name)

        with (
            patch("builtins.hasattr", side_effect=_mock_hasattr),
            patch("utils.security_utils.platform.node", return_value="node"),
            patch("utils.security_utils.platform.system", return_value="Windows"),
            patch("utils.security_utils.platform.machine", return_value="AMD64"),
            patch.dict("os.environ", {}, clear=True),
        ):
            fp = _get_machine_fingerprint()
            assert b"unknown" in fp


class TestDeriveKeyPbkdf2Params:
    """覆盖 _derive_key_from_machine 的 PBKDF2 参数与组合"""

    def test_pbkdf2_iteration_count_and_dklen(self):
        """验证 PBKDF2 使用 600_000 次迭代、sha256、dklen=32"""
        with patch("utils.security_utils.hashlib.pbkdf2_hmac") as mock_pbkdf2:
            mock_pbkdf2.return_value = b"k" * 32
            salt = b"a" * 32
            _derive_key_from_machine(salt)
            call = mock_pbkdf2.call_args
            assert call.args[0] == "sha256"
            assert call.kwargs["iterations"] == 600_000
            assert call.kwargs["dklen"] == 32

    def test_salt_and_fingerprint_passed_to_pbkdf2(self):
        """验证 salt 与 fingerprint 正确组合传入 PBKDF2"""
        fingerprint = b"machine|fingerprint"
        salt = b"s" * 32
        with (
            patch(
                "utils.security_utils._get_machine_fingerprint",
                return_value=fingerprint,
            ),
            patch("utils.security_utils.hashlib.pbkdf2_hmac") as mock_pbkdf2,
        ):
            mock_pbkdf2.return_value = b"k" * 32
            _derive_key_from_machine(salt)
            call = mock_pbkdf2.call_args
            assert call.args[1] == fingerprint  # password
            assert call.args[2] == salt  # salt


class TestHideFileWindowsFailure:
    """覆盖 _hide_file_windows 在 Windows 上调用失败容错"""

    def test_windows_set_file_attributes_returns_false(self):
        """SetFileAttributesW 返回 False 时记录 debug 日志，不抛出"""
        mock_kernel32 = MagicMock()
        mock_kernel32.SetFileAttributesW.return_value = False
        with (
            patch("utils.security_utils.os.name", "nt"),
            patch("ctypes.windll", create=True) as mock_windll,
        ):
            mock_windll.kernel32 = mock_kernel32
            _hide_file_windows("C:\\test\\file.key")
            mock_kernel32.SetFileAttributesW.assert_called_once()

    def test_windows_ctypes_exception_handled(self):
        """ctypes 调用抛出异常时容错处理，不向外传播"""
        mock_kernel32 = MagicMock()
        mock_kernel32.SetFileAttributesW.side_effect = OSError("access denied")
        with (
            patch("utils.security_utils.os.name", "nt"),
            patch("ctypes.windll", create=True) as mock_windll,
        ):
            mock_windll.kernel32 = mock_kernel32
            # 不应抛出
            _hide_file_windows("C:\\test\\file.key")


class TestSecurityManagerGetKeyConcurrency:
    """覆盖 get_key 并发调用时的 _key_lock 保护与多次缓存"""

    def setup_method(self):
        SecurityManager._key = None

    @patch("utils.security_utils.os.path.exists")
    def test_concurrent_get_key_calls_only_one_inner(self, mock_exists):
        """并发调用时 _get_key_inner 只被调用一次（双重检查锁）"""
        import threading
        import time

        mock_exists.side_effect = lambda p: p == SecurityManager.KEY_FILE
        key_bytes = b"key_32bytes_long_enough_for_aes!!!"

        def slow_inner():
            time.sleep(0.05)
            # 模拟真实 _get_key_inner 的副作用：设置 _key 缓存
            SecurityManager._key = key_bytes
            return key_bytes

        with (
            patch.object(SecurityManager, "_load_key_file", return_value=key_bytes),
            patch.object(SecurityManager, "_copy_file"),
            patch.object(SecurityManager, "_get_key_inner", side_effect=slow_inner) as mock_inner,
        ):
            results: list[bytes] = []
            threads: list[threading.Thread] = []

            def worker():
                results.append(SecurityManager.get_key())

            for _ in range(5):
                t = threading.Thread(target=worker)
                threads.append(t)
                t.start()

            for t in threads:
                t.join()

            assert all(r == key_bytes for r in results)
            assert mock_inner.call_count == 1

    @patch("utils.security_utils.os.path.exists")
    def test_multiple_sequential_calls_return_cached(self, mock_exists):
        """多次顺序调用返回同一缓存值"""
        mock_exists.side_effect = lambda p: p == SecurityManager.KEY_FILE
        key_bytes = b"cached_key_32bytes_long_enough!!"
        with (
            patch.object(SecurityManager, "_load_key_file", return_value=key_bytes) as mock_load,
            patch.object(SecurityManager, "_copy_file"),
        ):
            r1 = SecurityManager.get_key()
            r2 = SecurityManager.get_key()
            r3 = SecurityManager.get_key()
            assert r1 == r2 == r3 == key_bytes
            # _load_key_file 只应被调用一次（缓存生效）
            assert mock_load.call_count == 1


class TestGetKeyInnerBackupScenarios:
    """覆盖 _get_key_inner 的备份恢复与一致性场景"""

    def setup_method(self):
        SecurityManager._key = None

    @patch("utils.security_utils.os.path.exists")
    def test_key_file_missing_but_backup_exists_recovers(self, mock_exists):
        """KEY_FILE 不存在但 KEY_FILE_BAK 存在 → 从备份恢复"""
        backup_key = b"backup_key_32bytes_long_enough!!"
        SecurityManager._key = None
        mock_exists.side_effect = lambda p: p == SecurityManager.KEY_FILE_BAK

        with (
            patch.object(SecurityManager, "_load_key_file", return_value=backup_key) as mock_load,
            patch.object(SecurityManager, "_copy_file") as mock_copy,
            patch("utils.security_utils._hide_file_windows"),
        ):
            result = SecurityManager.get_key()
            assert result == backup_key
            mock_load.assert_called_once_with(SecurityManager.KEY_FILE_BAK)
            mock_copy.assert_called_once_with(SecurityManager.KEY_FILE_BAK, SecurityManager.KEY_FILE)

    @patch("utils.security_utils.os.path.exists")
    def test_both_files_consistent_loads_primary(self, mock_exists):
        """两者都存在且内容一致 → 从 KEY_FILE 加载"""
        key = b"consistent_key_32bytes_long_!!"
        SecurityManager._key = None
        mock_exists.side_effect = lambda p: p in (SecurityManager.KEY_FILE, SecurityManager.KEY_FILE_BAK)

        with (
            patch.object(SecurityManager, "_load_key_file", return_value=key) as mock_load,
            patch.object(SecurityManager, "_copy_file"),
        ):
            result = SecurityManager.get_key()
            assert result == key
            mock_load.assert_called_once_with(SecurityManager.KEY_FILE)

    @patch("utils.security_utils.os.path.exists")
    def test_primary_corrupt_recovers_from_backup(self, mock_exists):
        """两者都存在但主文件损坏 → 从备份恢复"""
        backup_key = b"backup_key_32bytes_long_enough!!"
        SecurityManager._key = None
        mock_exists.side_effect = lambda p: p in (SecurityManager.KEY_FILE, SecurityManager.KEY_FILE_BAK)

        with (
            patch.object(
                SecurityManager,
                "_load_key_file",
                side_effect=[Exception("primary corrupt"), backup_key],
            ) as mock_load,
            patch.object(SecurityManager, "_copy_file"),
            patch("utils.security_utils._hide_file_windows"),
        ):
            result = SecurityManager.get_key()
            assert result == backup_key
            assert mock_load.call_count == 2


class TestMigrateToDerivedKeyRollback:
    """覆盖 migrate_to_derived_key 迁移失败回滚"""

    def setup_method(self):
        SecurityManager._key = None

    @patch("utils.security_utils.os.path.exists")
    def test_migration_rollback_on_save_failure(self, mock_exists):
        """_save_key 失败时返回 False 且 _key 未切换"""
        mock_exists.return_value = True
        with (
            patch.object(SecurityManager, "_load_key_file", return_value=b"x" * 32),
            patch.object(SecurityManager, "_get_or_create_salt", return_value=b"s" * 32),
            patch.object(SecurityManager, "_save_key", side_effect=OSError("disk full")),
        ):
            result = SecurityManager.migrate_to_derived_key()
            assert result is False
            assert SecurityManager._key is None

    @patch("utils.security_utils.os.path.exists")
    @patch("utils.security_utils.os.remove")
    def test_migration_with_custom_decrypt_encrypt_funcs(self, mock_remove, mock_exists):
        """提供 decrypt_fn/encrypt_fn 时迁移仍成功（调用方负责 re-encrypt）"""
        mock_exists.return_value = True

        def decrypt_fn(value):
            return f"plain_{value}"

        def encrypt_fn(value):
            return f"enc_{value}"

        with (
            patch.object(SecurityManager, "_load_key_file", return_value=b"x" * 32),
            patch.object(SecurityManager, "_get_or_create_salt", return_value=b"s" * 32),
            patch.object(SecurityManager, "_save_key"),
        ):
            result = SecurityManager.migrate_to_derived_key(
                decrypt_fn=decrypt_fn,
                encrypt_fn=encrypt_fn,
            )
            assert result is True


class TestGetOrCreateSaltCorrupted:
    """覆盖 _get_or_create_salt 文件损坏重新创建路径"""

    def setup_method(self):
        SecurityManager._key = None

    @patch("utils.security_utils.os.path.exists", return_value=True)
    def test_short_salt_regenerates(self, mock_exists):
        """salt 文件内容过短（<16 bytes）→ 重新生成"""
        from unittest.mock import mock_open

        m = mock_open(read_data=b"short")
        with (
            patch("builtins.open", m),
            patch("utils.security_utils.secrets.token_bytes", return_value=b"n" * 32),
            patch("utils.security_utils.os.replace"),
            patch("utils.security_utils.os.fsync"),
            patch("utils.security_utils._hide_file_windows"),
        ):
            salt = SecurityManager._get_or_create_salt()
            assert salt == b"n" * 32

    @patch("utils.security_utils.os.path.exists", return_value=True)
    def test_salt_read_oserror_regenerates(self, mock_exists):
        """salt 文件读取 OSError → 重新生成"""
        from unittest.mock import mock_open

        write_mock = mock_open()

        def open_side_effect(path, mode="r", *args, **kwargs):
            if "rb" in str(mode):
                raise OSError("io error")
            return write_mock.return_value

        with (
            patch("builtins.open", side_effect=open_side_effect),
            patch("utils.security_utils.secrets.token_bytes", return_value=b"m" * 32),
            patch("utils.security_utils.os.replace"),
            patch("utils.security_utils.os.fsync"),
            patch("utils.security_utils._hide_file_windows"),
        ):
            salt = SecurityManager._get_or_create_salt()
            assert salt == b"m" * 32


class TestLoadKeyFile:
    """覆盖 _load_key_file 三种状态：不存在/损坏/有效"""

    def test_load_valid_key_file(self, tmp_path):
        """文件存在且有效 → 返回解码后的 key"""
        key = b"a" * 32
        key_file = tmp_path / "key"
        key_file.write_bytes(base64.b64encode(key))
        result = SecurityManager._load_key_file(str(key_file))
        assert result == key

    def test_load_empty_key_file_raises_value_error(self, tmp_path):
        """文件存在但为空 → 抛出 ValueError"""
        key_file = tmp_path / "empty"
        key_file.write_bytes(b"")
        with pytest.raises(ValueError, match="empty"):
            SecurityManager._load_key_file(str(key_file))

    def test_load_corrupt_key_file_raises(self, tmp_path):
        """文件存在但内容非合法 base64 → 抛出 binascii.Error"""
        import binascii

        key_file = tmp_path / "corrupt"
        key_file.write_bytes(b"!!!not base64!!!")
        with pytest.raises((binascii.Error, ValueError)):
            SecurityManager._load_key_file(str(key_file))

    def test_load_nonexistent_file_raises(self, tmp_path):
        """文件不存在 → FileNotFoundError"""
        key_file = tmp_path / "missing"
        with pytest.raises(FileNotFoundError):
            SecurityManager._load_key_file(str(key_file))


class TestSaveKeyBackup:
    """覆盖 _save_key 同步备份到 KEY_FILE_BAK"""

    def test_save_key_writes_main_and_backup(self, tmp_path):
        """_save_key 应同时写入 KEY_FILE 与 KEY_FILE_BAK，内容一致"""
        key = b"k" * 32
        key_file = tmp_path / "key"
        bak_file = tmp_path / "key.bak"

        with (
            patch.object(SecurityManager, "KEY_FILE", str(key_file)),
            patch.object(SecurityManager, "KEY_FILE_BAK", str(bak_file)),
            patch("utils.security_utils._hide_file_windows"),
        ):
            SecurityManager._save_key(key)
            assert key_file.exists()
            assert bak_file.exists()
            assert key_file.read_bytes() == bak_file.read_bytes()
            assert key_file.read_bytes() == base64.b64encode(key)


class TestCopyFileErrors:
    """覆盖 _copy_file 错误场景：源不存在/目标目录不存在"""

    def test_copy_nonexistent_source_no_raise(self, tmp_path):
        """源文件不存在 → 内部捕获，不向外抛出"""
        src = tmp_path / "nonexistent"
        dst = tmp_path / "dst"
        SecurityManager._copy_file(str(src), str(dst))

    def test_copy_to_nonexistent_destination_dir_no_raise(self, tmp_path):
        """目标目录不存在 → 内部捕获，不向外抛出"""
        src = tmp_path / "src"
        src.write_bytes(b"data")
        dst = tmp_path / "nonexistent_dir" / "dst"
        SecurityManager._copy_file(str(src), str(dst))


class TestEncryptDecryptAdditional:
    """覆盖 encrypt_data/decrypt_data 额外场景"""

    def setup_method(self):
        SecurityManager._key = None

    def test_encrypt_none_returns_empty(self):
        """data=None → 返回空字符串"""
        assert SecurityManager.encrypt_data(None) == ""

    def test_decrypt_none_returns_empty(self):
        """data=None → 返回空字符串"""
        assert SecurityManager.decrypt_data(None) == ""

    def test_large_data_roundtrip(self):
        """大数据往返测试（AESGCM 单次加密，验证非分块也能处理大数据）"""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        real_key = AESGCM.generate_key(bit_length=256)
        SecurityManager._key = real_key
        large_data = "A" * 100_000  # 100KB
        with patch.object(SecurityManager, "get_key", return_value=real_key):
            encrypted = SecurityManager.encrypt_data(large_data)
            decrypted = SecurityManager.decrypt_data(encrypted)
            assert decrypted == large_data

    def test_decrypt_with_wrong_key_raises_decryption_error(self):
        """密钥不匹配 → DecryptionError"""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        key1 = AESGCM.generate_key(bit_length=256)
        key2 = AESGCM.generate_key(bit_length=256)
        SecurityManager._key = key1

        with patch.object(SecurityManager, "get_key", return_value=key1):
            encrypted = SecurityManager.encrypt_data("secret")

        SecurityManager._key = key2
        with patch.object(SecurityManager, "get_key", return_value=key2):
            with pytest.raises(DecryptionError):
                SecurityManager.decrypt_data(encrypted)

    def test_decrypt_tampered_ciphertext_raises(self):
        """密文被篡改 → DecryptionError"""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        real_key = AESGCM.generate_key(bit_length=256)
        SecurityManager._key = real_key

        with patch.object(SecurityManager, "get_key", return_value=real_key):
            encrypted = SecurityManager.encrypt_data("secret")
            # 篡改末尾 4 个字符
            corrupted = encrypted[:-4] + "AAAA"
            with pytest.raises(DecryptionError):
                SecurityManager.decrypt_data(corrupted)


class TestEnsureLegacyMarkerFailure:
    """覆盖 _ensure_legacy_marker 创建失败容错"""

    def setup_method(self):
        SecurityManager._key = None

    @patch("utils.security_utils.os.path.exists", return_value=False)
    def test_create_marker_oserror_no_raise(self, mock_exists):
        """创建 marker 失败（OSError）→ 记录 debug，不抛出"""
        with (
            patch("builtins.open", side_effect=OSError("permission denied")),
            patch("utils.security_utils.logger") as mock_logger,
        ):
            SecurityManager._ensure_legacy_marker()
            mock_logger.debug.assert_called()

    @patch("utils.security_utils.os.path.exists", return_value=False)
    def test_create_marker_permission_error_no_raise(self, mock_exists):
        """创建 marker 失败（PermissionError）→ 记录 debug，不抛出"""
        with (
            patch("builtins.open", side_effect=PermissionError("denied")),
            patch("utils.security_utils._hide_file_windows"),
        ):
            SecurityManager._ensure_legacy_marker()


class TestNoSensitiveDataInLogs:
    """R9 守卫：验证密钥/明文不被日志打印"""

    def setup_method(self):
        SecurityManager._key = None

    def test_encrypt_failure_does_not_log_plaintext(self):
        """加密失败时日志参数不含明文"""
        plaintext = "super_secret_value_12345"
        with (
            patch.object(SecurityManager, "get_key", side_effect=Exception("key error")),
            patch("utils.security_utils.logger") as mock_logger,
        ):
            with pytest.raises(EncryptionError):
                SecurityManager.encrypt_data(plaintext)
            for call in (
                mock_logger.error.call_args_list
                + mock_logger.warning.call_args_list
                + mock_logger.critical.call_args_list
            ):
                for arg in call.args:
                    assert plaintext not in str(arg), f"R9 违反：日志中包含明文 plaintext: {arg}"

    def test_decrypt_failure_does_not_log_key(self):
        """解密失败时日志参数不含密钥 hex"""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        real_key = AESGCM.generate_key(bit_length=256)
        SecurityManager._key = real_key
        key_hex = real_key.hex()
        bad_data = base64.b64encode(b"x" * 28).decode()

        with (
            patch.object(SecurityManager, "get_key", return_value=real_key),
            patch("utils.security_utils.logger") as mock_logger,
        ):
            with pytest.raises(DecryptionError):
                SecurityManager.decrypt_data(bad_data)
            for call in (
                mock_logger.error.call_args_list
                + mock_logger.warning.call_args_list
                + mock_logger.critical.call_args_list
            ):
                for arg in call.args:
                    assert key_hex not in str(arg), f"R9 违反：日志中包含密钥 hex: {arg}"


class TestNoHardcodedSecrets:
    """R10 守卫：验证源码无硬编码密钥/密码模式"""

    def test_source_has_no_hardcoded_api_key_pattern(self):
        """源码不应包含 api_key = "xxx" 等硬编码模式"""
        import re
        import utils.security_utils as sec_mod

        with open(sec_mod.__file__, encoding="utf-8") as f:
            content = f.read()
        patterns = [
            r'api_key\s*=\s*["\'][a-zA-Z0-9]{8,}["\']',
            r'password\s*=\s*["\'][a-zA-Z0-9]{8,}["\']',
            r'db_password\s*=\s*["\'][a-zA-Z0-9]{8,}["\']',
        ]
        for pattern in patterns:
            matches = re.findall(pattern, content)
            assert matches == [], f"R10 违反：发现硬编码密钥模式 {matches}"


class TestKeyFilePathFromConfig:
    """R9/R10 守卫：KEY_FILE 等路径应从 APP_ROOT 派生，不硬编码绝对路径"""

    def test_key_file_paths_derived_from_app_root(self):
        """KEY_FILE / KEY_FILE_BAK / _MACHINE_SALT_FILE / _LEGACY_MARKER 基于 APP_ROOT"""
        from config import APP_ROOT

        from utils.security_utils import _LEGACY_MARKER, _MACHINE_SALT_FILE

        assert SecurityManager.KEY_FILE.startswith(APP_ROOT)
        assert SecurityManager.KEY_FILE_BAK.startswith(APP_ROOT)
        assert _MACHINE_SALT_FILE.startswith(APP_ROOT)
        assert _LEGACY_MARKER.startswith(APP_ROOT)
