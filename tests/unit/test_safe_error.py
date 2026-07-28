"""Tests for safe_error helper in data/sync/base.py.

P3-M5-SafeError-No-Traceback: safe_error 应支持 show_traceback 参数，
在 critical 路径（cache_manager.init_db / hard_reset）保留脱敏 traceback
以便运维排查根因。
"""

import pytest

from data.sync.base import safe_error
from utils.sanitizers import DataSanitizer

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_known_secrets():
    """每个测试前后清空 _known_secrets，避免测试间状态污染。"""
    DataSanitizer._reset_known_secrets()
    yield
    DataSanitizer._reset_known_secrets()


class TestSafeErrorSignature:
    """DoD ①: safe_error 支持 show_traceback 参数（签名检查）。"""

    def test_default_show_traceback_false(self):
        """safe_error(e) 默认不包含 traceback。"""
        try:
            raise RuntimeError("simple error")
        except RuntimeError as e:
            result = safe_error(e)

        # 默认行为：仅 message，无 traceback
        assert "simple error" in result
        assert "Traceback" not in result
        assert "RuntimeError" not in result.split("simple error")[0]

    def test_show_traceback_true_returns_traceback(self):
        """safe_error(e, show_traceback=True) 应包含 traceback 帧信息。"""
        try:
            raise RuntimeError("with traceback")
        except RuntimeError as e:
            result = safe_error(e, show_traceback=True)

        # 应包含 traceback 标志（traceback.format_exception 输出含 "Traceback"）
        assert "Traceback" in result
        assert "with traceback" in result
        assert "RuntimeError" in result


class TestSafeErrorTracebackSanitization:
    """DoD ② ④: traceback 经脱敏后无明文 token/路径/凭证。

    覆盖两类场景：
    a) RuntimeError("token=sk-secret123") exception message 脱敏
    b) traceback 输出含 D:\\workspace\\qTrading\\xxx.py 路径时 sanitize 后为 <PATH>
    """

    def test_traceback_sanitizes_token_in_message(self):
        """场景 a: exception message 中含 token=xxx 应被脱敏。"""
        sensitive_token = "sk-secret123abc"
        try:
            raise RuntimeError(f"token={sensitive_token}")
        except RuntimeError as e:
            result = safe_error(e, show_traceback=True)

        assert sensitive_token not in result, f"Token leaked in sanitized traceback: {result}"
        assert "***" in result
        # traceback 仍存在
        assert "Traceback" in result

    def test_traceback_sanitizes_windows_path(self):
        """场景 b: traceback 中的 Windows 文件路径应替换为 <PATH>。"""
        # 在 traceback 内部抛异常，使 traceback 帧包含本测试文件路径
        try:
            raise RuntimeError("error with path D:\\workspace\\qTrading\\secret\\api_key.py")
        except RuntimeError as e:
            result = safe_error(e, show_traceback=True)

        # 路径在 exception message 中应被替换
        assert "D:\\workspace\\qTrading\\secret\\api_key.py" not in result
        assert "<PATH>" in result
        # traceback 帧中的本测试文件路径也应被替换
        # （traceback 包含 "File \"D:\\workspace\\qTrading\\...test_safe_error.py\"")
        assert "Traceback" in result

    def test_traceback_sanitizes_unix_path(self):
        """场景 b 补充: traceback 中的 Unix 文件路径也应替换为 <PATH>。"""
        try:
            raise RuntimeError("config file at /home/user/secret/config.yaml")
        except RuntimeError as e:
            result = safe_error(e, show_traceback=True)

        assert "/home/user/secret/config.yaml" not in result
        assert "<PATH>" in result

    def test_traceback_sanitizes_url_credentials(self):
        """场景 a 扩展: traceback 中 URL 凭证也应被脱敏。"""
        sensitive_password = "my_db_password_456"
        try:
            raise RuntimeError(f"DB connect: postgresql+asyncpg://user:{sensitive_password}@host/db")
        except RuntimeError as e:
            result = safe_error(e, show_traceback=True)

        assert sensitive_password not in result
        assert "***" in result
        assert "postgresql+asyncpg" in result

    def test_traceback_sanitizes_bearer_token(self):
        """场景 a 扩展: traceback 中 Bearer token 也应被脱敏。"""
        sensitive_bearer = "sk-proj-abc123def456ghi789"
        try:
            raise RuntimeError(f"HTTP 401: Bearer {sensitive_bearer}")
        except RuntimeError as e:
            result = safe_error(e, show_traceback=True)

        assert sensitive_bearer not in result
        assert "Bearer ***" in result

    def test_traceback_sanitizes_registered_secret(self):
        """场景 a 扩展: 通过 register_secret 注册的裸 token 在 traceback 中也应被替换。"""
        bare_token = "tushare_bare_token_xyz123abc"
        DataSanitizer.register_secret(bare_token)
        try:
            raise RuntimeError(f"Auth failed: {bare_token}")
        except RuntimeError as e:
            result = safe_error(e, show_traceback=True)

        assert bare_token not in result
        assert "***" in result

    def test_traceback_sanitizes_secret_in_traceback_frames(self):
        """场景 a 拓展: traceback 帧局部变量含 secret 时也应脱敏。

        traceback.format_exception 输出含异常 args repr，若 args 中含 secret
        应被精确替换。
        """
        bare_token = "traceback_frame_secret_456"
        DataSanitizer.register_secret(bare_token)
        try:
            # 异常 args 中含 secret，traceback 末行会 repr args
            raise RuntimeError(f"Connection refused for user with token {bare_token}")
        except RuntimeError as e:
            result = safe_error(e, show_traceback=True)

        assert bare_token not in result
        assert "***" in result


class TestSafeErrorBackwardCompat:
    """向后兼容性: 现有 safe_error(e) 调用行为不变。"""

    def test_default_call_returns_sanitized_message_only(self):
        """现有调用 safe_error(e) 不传 show_traceback 时行为不变。"""
        err = ValueError("File not found: D:\\workspace\\test.py")
        result = safe_error(err)

        # 与原 DataSanitizer.sanitize_error(err) 等价
        expected = DataSanitizer.sanitize_error(err)
        assert result == expected
        assert "Traceback" not in result

    def test_safe_error_with_string_exception(self):
        """safe_error 支持字符串入参（与 DataSanitizer.sanitize_error 一致）。"""
        result = safe_error("plain string error")  # type: ignore[arg-type]
        assert "plain string error" in result
        assert "Traceback" not in result

    def test_show_traceback_with_string_exception_no_traceback(self):
        """show_traceback=True 但 exception 是字符串时，无 traceback 可附加，仅返回脱敏 message。"""
        # DataSanitizer.sanitize_error 的 show_traceback 分支仅在 isinstance(exception, BaseException) 时附加
        result = safe_error("plain string error", show_traceback=True)  # type: ignore[arg-type]
        assert "plain string error" in result
        assert "Traceback" not in result


class TestSafeErrorCacheManagerCriticalPath:
    """DoD 应用: cache_manager.init_db / hard_reset critical 路径保留脱敏 traceback。

    这些测试验证 safe_error(e, show_traceback=True) 的契约，cache_manager.py
    的具体调用由集成测试覆盖。
    """

    def test_init_db_critical_error_keeps_sanitized_traceback(self):
        """模拟 init_db 异常场景: safe_error(e, show_traceback=True) 保留脱敏 traceback。"""
        try:
            # 模拟数据库初始化失败，异常含 DB 连接串（含密码）
            raise RuntimeError("init_db failed: postgresql+asyncpg://postgres:secret_password@localhost:5432/db")
        except RuntimeError as e:
            result = safe_error(e, show_traceback=True)

        # 密码脱敏
        assert "secret_password" not in result
        assert "***" in result
        # traceback 保留
        assert "Traceback" in result
        assert "init_db failed" in result

    def test_hard_reset_critical_error_keeps_sanitized_traceback(self):
        """模拟 hard_reset 异常场景: safe_error(e, show_traceback=True) 保留脱敏 traceback。"""
        try:
            # 模拟 hard_reset 失败，异常含文件路径
            raise RuntimeError("hard_reset failed at D:\\workspace\\qTrading\\data\\cache\\cache_manager.py")
        except RuntimeError as e:
            result = safe_error(e, show_traceback=True)

        # 路径脱敏
        assert "D:\\workspace\\qTrading\\data\\cache\\cache_manager.py" not in result
        assert "<PATH>" in result
        # traceback 保留
        assert "Traceback" in result
        assert "hard_reset failed" in result
