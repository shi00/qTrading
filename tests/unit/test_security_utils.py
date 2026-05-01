import base64

import pytest

from utils.security_utils import DecryptionError, SecurityManager


class TestEncryptSuccess:
    """SecurityManager.encrypt_data 正常路径"""

    def test_encrypt_returns_ciphertext_on_success(self):
        SecurityManager._key = None
        result = SecurityManager.encrypt_data("hello")
        assert isinstance(result, str) and len(result) > 0

    def test_encrypt_empty_string_returns_empty(self):
        result = SecurityManager.encrypt_data("")
        assert result == ""


class TestEncryptFailure:
    """SecurityManager.encrypt_data 异常路径"""

    def test_encrypt_with_invalid_key_raises(self):
        from unittest.mock import patch

        with patch.object(SecurityManager, "get_key", return_value=b"short"):
            with pytest.raises(DecryptionError):
                SecurityManager.encrypt_data("test_data")


class TestDecryptSuccess:
    """SecurityManager.decrypt_data 正常路径"""

    def test_decrypt_empty_string_returns_empty(self):
        result = SecurityManager.decrypt_data("")
        assert result == ""

    def test_encrypt_decrypt_roundtrip(self):
        SecurityManager._key = None
        plaintext = "test_roundtrip_测试中文"
        encrypted = SecurityManager.encrypt_data(plaintext)
        decrypted = SecurityManager.decrypt_data(encrypted)
        assert decrypted == plaintext


class TestDecryptFailure:
    """SecurityManager.decrypt_data 异常路径"""

    def test_decrypt_corrupted_data_raises(self):
        with pytest.raises(DecryptionError):
            SecurityManager.decrypt_data("not_valid_base64!!!")

    def test_decrypt_too_short_data_raises(self):
        short_data = base64.b64encode(b"short").decode("utf-8")
        with pytest.raises(DecryptionError):
            SecurityManager.decrypt_data(short_data)
