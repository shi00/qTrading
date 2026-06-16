"""
数据脱敏工具集 - Data Sanitizers

提供敏感数据脱敏功能,确保日志中不泄露:
- API Token
- 加密密钥
- 文件路径
- DataFrame实际数据
"""

import re

import pandas as pd


class DataSanitizer:
    """统一的数据脱敏工具类"""

    @staticmethod
    def sanitize_token(token: str) -> str:
        """
        Token脱敏处理

        输入: "tushare_abc123xyz789"
        输出: "tus***789"

        Args:
            token: 原始token字符串

        Returns:
            脱敏后的token
        """
        if not token or not isinstance(token, str):
            return "***"

        # 短token直接全部隐藏
        if len(token) < 8:
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
        r"([?&])(api_key|key|token|secret|password|apikey|access_token|refresh_token)=[^\s&\"']+",
        re.IGNORECASE,
    )

    _PATTERN_STANDALONE_KEY_VALUE = re.compile(
        r"\b(api_key|apikey|api-key|secret|token|password|access_token|refresh_token)\s*=\s*[^\s,;\"']+",
        re.IGNORECASE,
    )

    _PATTERN_BEARER = re.compile(r"Bearer\s+[^\s\"']+", re.IGNORECASE)

    _PATTERN_COLON_KEY_VALUE = re.compile(
        r"\b(api_key|apikey|api-key|secret|token|password|access_token|refresh_token)\s*[:：]\s*[^\s,;\"']+",
        re.IGNORECASE,
    )

    _PATTERN_JSON_KEY_VALUE = re.compile(
        r"""["']?(api_key|apikey|api-key|secret|token|password|access_token|refresh_token)["']?\s*:\s*["'][^"']+["']""",
        re.IGNORECASE,
    )

    # Space-separated key-value with known secret prefixes (sk-, pk-, key-, eyJ for JWT)
    _PATTERN_SPACE_KEY_VALUE = re.compile(
        r"\b(api_key|apikey|api-key|secret|token|password|access_token|refresh_token)\s+(sk-|pk-|key-|eyJ)[^\s,;\"']+",
        re.IGNORECASE,
    )

    _PATTERN_URL_CREDENTIALS = re.compile(
        r"(postgresql|postgres|mysql|mongodb|redis|amqp|http|https|ftp)://([^:@\s]+):([^@\s]+)@",
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
            sensitive_keys = ["token", "password", "api_key", "secret", "key"]

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
            if isinstance(arg, pd.DataFrame):
                clean_args.append(DataSanitizer.sanitize_dataframe(arg))
            elif isinstance(arg, str) and len(arg) > 100:
                clean_args.append(f"{arg[:50]}...(truncated)")
            else:
                clean_args.append(repr(arg)[:100])

        return tuple(clean_args), clean_kwargs
