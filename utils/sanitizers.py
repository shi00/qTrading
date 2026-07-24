"""
数据脱敏工具集 - Data Sanitizers

提供敏感数据脱敏功能,确保日志中不泄露:
- API Token
- 加密密钥
- 文件路径
- DataFrame实际数据
"""

import logging
import re

import pandas as pd

_logger = logging.getLogger(__name__)


class DataSanitizer:
    """统一的数据脱敏工具类"""

    # 已注册的 secret 值集合，用于 sanitize_error 中的精确替换。
    # 设有上限以避免内存泄漏（token 轮换/测试场景下重复注册不同值）。
    _known_secrets: set[str] = set()
    _MAX_KNOWN_SECRETS = 50
    # 注册的最小长度阈值，过短的值易在 str.replace 时产生误替换。
    _MIN_SECRET_LEN = 8

    @classmethod
    def register_secret(cls, value: str) -> None:
        """注册已知 secret 值，供 sanitize_error 做精确替换。

        Args:
            value: secret 原始值（如 token、API key、DB 密码）
        """
        if not value or not isinstance(value, str) or len(value) < cls._MIN_SECRET_LEN:
            return
        if len(cls._known_secrets) >= cls._MAX_KNOWN_SECRETS:
            # R9: 达上限时 emit warning 让运维可感知（避免静默失败导致后续密码泄露）
            _logger.warning(
                "DataSanitizer._known_secrets reached cap %d, skip register new secret; "
                "existing secrets still protected",
                cls._MAX_KNOWN_SECRETS,
            )
            return
        cls._known_secrets.add(value)

    @classmethod
    def _reset_known_secrets(cls) -> None:
        """清空已注册 secret 集合。仅供测试隔离使用。"""
        cls._known_secrets.clear()

    @staticmethod
    def sanitize_token(token: str) -> str:
        """
        Token脱敏处理

        长度 < 32 的 token 全部隐藏为 "***"；长度 >= 32 的部分脱敏。

        输入: "tushare_abc123xyz78901234567890123456789"  (40字符)
        输出: "tus***6789"

        Args:
            token: 原始token字符串

        Returns:
            脱敏后的token
        """
        if not token or not isinstance(token, str):
            return "***"

        # 短token直接全部隐藏
        if len(token) < 32:
            return "***"

        # 标准格式: 前3位 + *** + 后4位
        return f"{token[:3]}***{token[-4:]}"

    @staticmethod
    def sanitize_dataframe(df: pd.DataFrame | None, max_cols: int = 5) -> str:
        """
        DataFrame安全摘要

        仅记录形状和列名,不泄露实际数据

        Args:
            df: DataFrame对象
            max_cols: 最多显示的列数

        Returns:
            安全的摘要字符串
        """
        if df is None:
            return "None"

        if not isinstance(df, pd.DataFrame):
            return f"{type(df).__name__}"

        if df.empty:
            return "DataFrame(empty)"

        # 仅显示前N列
        cols_display = list(df.columns[:max_cols])
        if len(df.columns) > max_cols:
            cols_display.append("...")

        return f"DataFrame(shape={df.shape}, cols={cols_display})"

    # Pre-compile regex patterns for better performance
    # Matches Windows paths: D:\path\to\file.py, c:\users\... (Case insensitive drive)
    _PATTERN_WIN_PATH = re.compile(r'[a-zA-Z]:\\[^\'"\s]+')

    # Matches Unix paths: /path/to/file (broader match, not just .py)
    # Match absolute paths starting with /, containing word chars, dots, dashes, slashes
    _PATTERN_UNIX_PATH = re.compile(r"/(?:[\w\.\-]+/)+[\w\.\-]+")

    _PATTERN_URL_QUERY_KEY = re.compile(
        r"([?&])(api_key|api-key|key|token|secret|password|apikey|access_token|refresh_token|credentials|credential|private_key|passphrase)=[^\s&\"']+",
        re.IGNORECASE,
    )

    _PATTERN_STANDALONE_KEY_VALUE = re.compile(
        r"\b(api_key|apikey|api-key|secret|token|password|access_token|refresh_token|credentials|credential|private_key|passphrase)\s*=\s*[^\s,;\"']+",
        re.IGNORECASE,
    )

    _PATTERN_BEARER = re.compile(r"Bearer\s+[^\s\"']+", re.IGNORECASE)

    _PATTERN_COLON_KEY_VALUE = re.compile(
        r"\b(api_key|apikey|api-key|secret|token|password|access_token|refresh_token|credentials|credential|private_key|passphrase)\s*[:：]\s*[^\s,;\"']+",
        re.IGNORECASE,
    )

    _PATTERN_JSON_KEY_VALUE = re.compile(
        r"""["']?(api_key|apikey|api-key|secret|token|password|access_token|refresh_token|credentials|credential|private_key|passphrase)["']?\s*:\s*["'][^"']+["']""",
        re.IGNORECASE,
    )

    # Space-separated key-value with known secret prefixes (sk-, pk-, key-, eyJ for JWT)
    _PATTERN_SPACE_KEY_VALUE = re.compile(
        r"\b(api_key|apikey|api-key|secret|token|password|access_token|refresh_token|credentials|credential|private_key|passphrase)\s+(sk-|pk-|key-|eyJ)[^\s,;\"']+",
        re.IGNORECASE,
    )

    _PATTERN_URL_CREDENTIALS = re.compile(
        r"([a-z][a-z0-9+.\-]*)://([^:@\s]*):([^@\s]+)@",
        re.IGNORECASE,
    )

    @staticmethod
    def sanitize_error(exception: Exception | str, show_traceback: bool = False) -> str:
        """
        异常信息脱敏

        移除文件路径和敏感凭证,避免暴露系统结构和API密钥

        Args:
            exception: 异常对象或字符串
            show_traceback: 是否包含堆栈(仅用于DEBUG)

        Returns:
            脱敏后的错误信息
        """
        msg = str(exception)

        msg = DataSanitizer._PATTERN_URL_QUERY_KEY.sub(r"\1\2=***", msg)

        msg = DataSanitizer._PATTERN_STANDALONE_KEY_VALUE.sub(r"\1=***", msg)

        msg = DataSanitizer._PATTERN_COLON_KEY_VALUE.sub(r"\1: ***", msg)

        msg = DataSanitizer._PATTERN_JSON_KEY_VALUE.sub(r'"\1": "***"', msg)

        msg = DataSanitizer._PATTERN_SPACE_KEY_VALUE.sub(r"\1 ***", msg)

        msg = DataSanitizer._PATTERN_URL_CREDENTIALS.sub(r"\1://\2:***@", msg)

        msg = DataSanitizer._PATTERN_BEARER.sub("Bearer ***", msg)

        msg = DataSanitizer._PATTERN_WIN_PATH.sub("<PATH>", msg)

        msg = DataSanitizer._PATTERN_UNIX_PATH.sub("<PATH>", msg)

        # 邻接模式匹配后，对已注册 secret 做精确替换（兜底裸 token 泄露）
        # 使用 list() 快照避免迭代期间其他线程 register_secret 修改集合
        for secret in list(DataSanitizer._known_secrets):
            if secret in msg:
                msg = msg.replace(secret, "***")

        # 如果需要堆栈,也要脱敏
        if show_traceback and isinstance(exception, BaseException):
            import traceback

            tb_lines = traceback.format_exception(
                type(exception),
                exception,
                exception.__traceback__,
            )
            # 先做凭证脱敏（与主流程一致），再做路径脱敏
            tb_clean = []
            for line in tb_lines:
                line = DataSanitizer._PATTERN_URL_QUERY_KEY.sub(r"\1\2=***", line)
                line = DataSanitizer._PATTERN_STANDALONE_KEY_VALUE.sub(r"\1=***", line)
                line = DataSanitizer._PATTERN_COLON_KEY_VALUE.sub(r"\1: ***", line)
                line = DataSanitizer._PATTERN_JSON_KEY_VALUE.sub(r'"\1": "***"', line)
                line = DataSanitizer._PATTERN_SPACE_KEY_VALUE.sub(r"\1 ***", line)
                line = DataSanitizer._PATTERN_URL_CREDENTIALS.sub(r"\1://\2:***@", line)
                line = DataSanitizer._PATTERN_BEARER.sub("Bearer ***", line)
                line = DataSanitizer._PATTERN_WIN_PATH.sub("<PATH>", line)
                line = DataSanitizer._PATTERN_UNIX_PATH.sub("<PATH>", line)
                # 精确替换已注册 secret（与主流程一致）
                for secret in list(DataSanitizer._known_secrets):
                    if secret in line:
                        line = line.replace(secret, "***")
                tb_clean.append(line)
            return "\n".join(tb_clean)

        return msg

    @staticmethod
    def sanitize_dict(data: dict, sensitive_keys: list[str] | None = None) -> dict:
        """
        字典脱敏处理

        Args:
            data: 原始字典
            sensitive_keys: 需要脱敏的键列表

        Returns:
            脱敏后的字典副本
        """
        if sensitive_keys is None:
            sensitive_keys = [
                "token",
                "password",
                "api_key",
                "secret",
                "key",
                "apikey",
                "api-key",
                "credential",
                "credentials",
                "access_token",
                "refresh_token",
                "private_key",
                "passphrase",
            ]

        result = {}
        for k, v in data.items():
            # 检查key是否敏感
            if any(sensitive in k.lower() for sensitive in sensitive_keys):
                if isinstance(v, str):
                    result[k] = DataSanitizer.sanitize_token(v)
                else:
                    result[k] = "***"
            # DataFrame特殊处理
            elif isinstance(v, pd.DataFrame):
                result[k] = DataSanitizer.sanitize_dataframe(v)
            elif isinstance(v, dict):
                result[k] = DataSanitizer.sanitize_dict(v, sensitive_keys)
            elif isinstance(v, list):
                result[k] = [
                    DataSanitizer.sanitize_dict(item, sensitive_keys) if isinstance(item, dict) else item for item in v
                ]
            else:
                # R9: 非敏感 key 的 str 值也跑一遍 _known_secrets 精确替换，
                # 避免已注册 secret 经非敏感 key 名（如自定义环境变量）泄露
                if isinstance(v, str):
                    for _secret in list(DataSanitizer._known_secrets):
                        if _secret in v:
                            v = v.replace(_secret, "***")
                result[k] = v

        return result

    @staticmethod
    def sanitize_args(*args, sensitive_patterns: list[str] | None = None, **kwargs) -> tuple:
        """
        函数参数脱敏

        用于装饰器自动脱敏参数

        Args:
            *args: 位置参数
            sensitive_patterns: 敏感参数名模式
            **kwargs: 关键字参数

        Returns:
            (脱敏后的args, 脱敏后的kwargs)
        """
        if sensitive_patterns is None:
            sensitive_patterns = ["token", "password", "key", "secret"]

        # 关键字参数脱敏
        clean_kwargs = DataSanitizer.sanitize_dict(kwargs, sensitive_patterns)

        # 位置参数转为安全表示(避免大对象)
        clean_args = []
        for arg in args:
            # R9: 位置字符串参数追加 _known_secrets 精确替换（与 sanitize_error 同源兜底），
            # 避免裸 token 经 repr()[:100] 原样记录到日志
            if isinstance(arg, str):
                for _secret in list(DataSanitizer._known_secrets):
                    if _secret in arg:
                        arg = arg.replace(_secret, "***")
            if isinstance(arg, pd.DataFrame):
                clean_args.append(DataSanitizer.sanitize_dataframe(arg))
            elif isinstance(arg, str) and len(arg) > 100:
                clean_args.append(f"{arg[:50]}...(truncated)")
            else:
                # R9: 非字符串位置参数（dict/list 等）的 repr 也跑一遍 _known_secrets 精确替换，
                # 避免含 secret 字段的容器经 repr()[:100] 泄露明文。
                # 先在完整 repr 上替换再截断，防止 secret 跨 [:100] 边界时前缀泄露
                full_repr = repr(arg)
                for _secret in list(DataSanitizer._known_secrets):
                    if _secret in full_repr:
                        full_repr = full_repr.replace(_secret, "***")
                clean_args.append(full_repr[:100])

        return tuple(clean_args), clean_kwargs
