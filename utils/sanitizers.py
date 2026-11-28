"""
数据脱敏工具集 - Data Sanitizers

提供敏感数据脱敏功能,确保日志中不泄露:
- API Token
- 加密密钥
- 文件路径
- DataFrame实际数据
"""

from __future__ import annotations

import logging
import re
import sys
from typing import TYPE_CHECKING, Any, ClassVar, TypeGuard, cast

if TYPE_CHECKING:
    import pandas as pd

_logger = logging.getLogger(__name__)


def _is_dataframe(obj: object) -> TypeGuard[pd.DataFrame]:
    """查询式判断 obj 是否为 pandas DataFrame，而不触发 pandas 导入。

    因 pandas DataFrame 实例存在必然意味着 pandas 已加载（存在于 ``sys.modules``），
    故 pandas 未加载时本函数返回 False 与 ``isinstance(obj, pd.DataFrame)`` 完全等价
    （PRF-01：避免 ``utils.sanitizers`` 因 pandas 顶层导入而成为横切重依赖枢纽）。
    ``TypeGuard`` 让调用点得以在真分支内收窄 obj 为 DataFrame（pandas 仅类型引用）。
    """
    pd_mod = sys.modules.get("pandas")
    return pd_mod is not None and isinstance(obj, pd_mod.DataFrame)


class DataSanitizer:
    """统一的数据脱敏工具类"""

    # 已注册的 secret 值集合，用于 sanitize_error 中的精确替换。
    # 设有上限以避免内存泄漏（token 轮换/测试场景下重复注册不同值）。
    _known_secrets: ClassVar[set[str]] = set()
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
            # 注：传 len(cls._known_secrets)（当前数量，int）而非 cls._MAX_KNOWN_SECRETS，
            # 因后者变量名含 "SECRET" 子串会触发 CodeQL py/clear-text-logging-sensitive-data
            # 误报（实际值是整数常量 50，非 secret 值）。
            _logger.warning(
                "DataSanitizer._known_secrets reached cap (current size=%d); "
                "skip registering new entry, existing entries still protected",
                len(cls._known_secrets),
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

        if not _is_dataframe(df):
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

    # review05-E8: 兜底裸 token 检测（无 key= / URL / JSON 前缀时的最后防线）。
    # 匹配长度 32-64 的连续字母数字串。采用部分遮蔽（保留首尾各 4 位）以降低
    # 误报率（可能命中 hash、UUID、长标识符等正常值）。register_secret 精确匹配
    # 优先，此规则只兜底未注册的裸 token。
    _PATTERN_BARE_TOKEN = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z0-9]{32,64}(?![A-Za-z0-9_])")

    # Space-separated key-value with known secret prefixes (sk-, pk-, key-, eyJ for JWT)
    _PATTERN_SPACE_KEY_VALUE = re.compile(
        r"\b(api_key|apikey|api-key|secret|token|password|access_token|refresh_token|credentials|credential|private_key|passphrase)\s+(sk-|pk-|key-|eyJ)[^\s,;\"']+",
        re.IGNORECASE,
    )

    # URL userinfo：`user:pass@` 遮蔽密码（保留 user），仅 `user@`（token 常置于
    # 用户名位置，如 http://token@proxy:8080）遮蔽整个 userinfo，host:port/path 保留。
    # userinfo 限定在 `scheme://` 与首个 `@` 之间且不含 `/ ? #` 与空白，故
    # `http://host/path@x`、`http://host?q=a@b` 等路径/查询中的 @ 不会被误判。
    _PATTERN_URL_CREDENTIALS = re.compile(
        r"([a-z][a-z0-9+.\-]*)://([^:@/?#\s]*)(?::([^@/?#\s]*))?@",
        re.IGNORECASE,
    )

    # D1-06: SQLAlchemy DBAPI 异常字符串中的绑定参数块（`[parameters: ...]`，长参数时
    # 可跨多行）与官方文档链接。绑定参数常含业务明文/凭据，且值域不可穷举，任何
    # 值级脱敏规则都无法保证不漏，故整段剔除——这是 R9 的最小可信边界。
    _PATTERN_DB_PARAMETERS_BLOCK = re.compile(r"\[parameters:.*?(?=\(Background on this error at:|\Z)", re.DOTALL)
    _PATTERN_DB_ERROR_BACKGROUND = re.compile(r"\(Background on this error at:.*?\)", re.DOTALL)
    # 对外 DB 错误摘要的最大字符数（脱敏后截断，避免刷屏）。
    _DB_ERROR_TEXT_MAX_LEN = 600

    # PII 检测: 手机号、身份证号、邮箱、银行卡号、统一社会信用代码
    # 参考: https://blog.csdn.net/weixin_40369899/article/details/135644486 (手机号)
    # 参考: https://cloud.tencent.com/developer/article/2275911 (身份证)
    # 参考: https://www.regular-expressions.info/email.html (邮箱)
    # 边界检查: (?<!\d)...(?!\d) 避免匹配股票代码/订单号等正常数字串
    _PATTERN_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
    _PATTERN_ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
    _PATTERN_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
    # 境内银行卡（银联标准 16-19 位）：首 1 位卡 BIN 前缀 4/5/6/9 + 15-18 位数字。
    # BIN 前缀限制降低误报（不以 4/5/6/9 开头的 16-19 位纯数字串如订单号/流水号不命中）。
    _PATTERN_BANK_CARD = re.compile(r"(?<!\d)(?:4|5|6|9)\d{15,18}(?!\d)")
    # 统一社会信用代码（18 位）：登记管理部门+机构类别 2 位 + 行政区划 6 位数字 +
    # 主体标识+校验码 10 位；字符集排除 I/O/S/V/Z（GB 32100-2015）。
    _PATTERN_CREDIT_CODE = re.compile(
        r"(?<![0-9A-Za-z])[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}(?![0-9A-Za-z])"
    )

    # 短且歧义的敏感键关键字：`pass` 作为子串会误伤 COMPASS / PASSPORT / BYPASS，
    # `pwd` 亦不宜裸子串匹配；两者仅在「按分隔符/camelCase 拆出的词段整体相等」时
    # 生效（MYSQL_PWD / DB_PASS / dbPass 命中，COMPASS 不命中）。
    _AMBIGUOUS_SENSITIVE_KEYWORDS: ClassVar[frozenset[str]] = frozenset({"pwd", "pass"})
    _KEY_SEGMENT_SPLIT = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z])(?=[A-Z])")

    @staticmethod
    def _sanitize_pii_text(text: str) -> str:
        """检测并替换文本中的 PII（手机号/身份证/邮箱/银行卡/统一社会信用代码）。

        Args:
            text: 待检测文本

        Returns:
            PII 已替换为 *** 的文本
        """
        text = DataSanitizer._PATTERN_PHONE.sub("***", text)
        text = DataSanitizer._PATTERN_ID_CARD.sub("***", text)
        text = DataSanitizer._PATTERN_BANK_CARD.sub("***", text)
        text = DataSanitizer._PATTERN_CREDIT_CODE.sub("***", text)
        text = DataSanitizer._PATTERN_EMAIL.sub("***", text)
        return text

    @staticmethod
    def _mask_bare_token(match: re.Match[str]) -> str:
        """裸 token 部分遮蔽回调：保留首尾各 4 位（review05-E8）。"""
        token = match.group(0)
        if len(token) <= 8:
            return "***"
        return f"{token[:4]}***{token[-4:]}"

    @staticmethod
    def _mask_url_userinfo(match: re.Match[str]) -> str:
        """URL userinfo 遮蔽回调：有冒号只遮蔽密码，无冒号遮蔽整个 userinfo。

        无冒号形态（`http://token@host`）的 token 位于用户名位置，故整体替换为
        `***`，保留 scheme 与 host:port/path 便于定位问题。
        """
        scheme = match.group(1)
        if match.group(3) is None:
            return f"{scheme}://***@"
        return f"{scheme}://{match.group(2)}:***@"

    @staticmethod
    def sanitize_paths(text: str) -> str:
        """对给定文本应用 Windows/Unix 路径脱敏，将路径替换为 <PATH>。

        与 sanitize_error 中的路径脱敏行为一致，用于独立处理
        traceback 等包含完整文件路径（可能含用户名）的日志文本。

        Args:
            text: 待检测文本

        Returns:
            Windows/Unix 路径已被替换为 <PATH> 的文本
        """
        text = DataSanitizer._PATTERN_WIN_PATH.sub("<PATH>", text)
        text = DataSanitizer._PATTERN_UNIX_PATH.sub("<PATH>", text)
        return text

    @staticmethod
    def sanitize_error(exception: BaseException | str, show_traceback: bool = False) -> str:
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

        # D1-06: 先整段剔除 SQLAlchemy 绑定参数与文档链接（参数值可能含业务明文/凭据）。
        msg = DataSanitizer._PATTERN_DB_PARAMETERS_BLOCK.sub("", msg)
        msg = DataSanitizer._PATTERN_DB_ERROR_BACKGROUND.sub("", msg)

        msg = DataSanitizer._PATTERN_URL_QUERY_KEY.sub(r"\1\2=***", msg)

        msg = DataSanitizer._PATTERN_STANDALONE_KEY_VALUE.sub(r"\1=***", msg)

        msg = DataSanitizer._PATTERN_COLON_KEY_VALUE.sub(r"\1: ***", msg)

        msg = DataSanitizer._PATTERN_JSON_KEY_VALUE.sub(r'"\1": "***"', msg)

        msg = DataSanitizer._PATTERN_SPACE_KEY_VALUE.sub(r"\1 ***", msg)

        msg = DataSanitizer._PATTERN_URL_CREDENTIALS.sub(DataSanitizer._mask_url_userinfo, msg)

        msg = DataSanitizer._PATTERN_BEARER.sub("Bearer ***", msg)

        msg = DataSanitizer._PATTERN_WIN_PATH.sub("<PATH>", msg)

        msg = DataSanitizer._PATTERN_UNIX_PATH.sub("<PATH>", msg)

        # 邻接模式匹配后，对已注册 secret 做精确替换（兜底裸 token 泄露）
        # 使用 list() 快照避免迭代期间其他线程 register_secret 修改集合
        for secret in list(DataSanitizer._known_secrets):
            if secret in msg:
                msg = msg.replace(secret, "***")

        # PII 检测: 手机号/身份证/邮箱
        msg = DataSanitizer._sanitize_pii_text(msg)

        # 兜底裸 token 检测（review05-E8，最后防线，部分遮蔽降低误报）
        msg = DataSanitizer._PATTERN_BARE_TOKEN.sub(DataSanitizer._mask_bare_token, msg)

        # 如果需要堆栈,也要脱敏
        if show_traceback and isinstance(exception, BaseException):
            import traceback

            tb_text = "".join(
                traceback.format_exception(
                    type(exception),
                    exception,
                    exception.__traceback__,
                )
            )
            # D1-06: 参数块可能跨多行，须在整体文本上剔除（逐行正则无法覆盖续行）。
            tb_text = DataSanitizer._PATTERN_DB_PARAMETERS_BLOCK.sub("", tb_text)
            tb_text = DataSanitizer._PATTERN_DB_ERROR_BACKGROUND.sub("", tb_text)
            # 先做凭证脱敏（与主流程一致），再做路径脱敏
            tb_clean = []
            for line in tb_text.splitlines():
                line = DataSanitizer._PATTERN_URL_QUERY_KEY.sub(r"\1\2=***", line)
                line = DataSanitizer._PATTERN_STANDALONE_KEY_VALUE.sub(r"\1=***", line)
                line = DataSanitizer._PATTERN_COLON_KEY_VALUE.sub(r"\1: ***", line)
                line = DataSanitizer._PATTERN_JSON_KEY_VALUE.sub(r'"\1": "***"', line)
                line = DataSanitizer._PATTERN_SPACE_KEY_VALUE.sub(r"\1 ***", line)
                line = DataSanitizer._PATTERN_URL_CREDENTIALS.sub(DataSanitizer._mask_url_userinfo, line)
                line = DataSanitizer._PATTERN_BEARER.sub("Bearer ***", line)
                line = DataSanitizer._PATTERN_WIN_PATH.sub("<PATH>", line)
                line = DataSanitizer._PATTERN_UNIX_PATH.sub("<PATH>", line)
                # 精确替换已注册 secret（与主流程一致）
                for secret in list(DataSanitizer._known_secrets):
                    if secret in line:
                        line = line.replace(secret, "***")
                # PII 检测
                line = DataSanitizer._sanitize_pii_text(line)
                # 兜底裸 token（review05-E8，与主流程一致）
                line = DataSanitizer._PATTERN_BARE_TOKEN.sub(DataSanitizer._mask_bare_token, line)
                tb_clean.append(line)
            return "\n".join(tb_clean)

        return msg

    @staticmethod
    def _extract_sqlstate(exception: object, max_depth: int = 6) -> str | None:
        """沿异常链提取 SQLSTATE（``sqlstate`` / ``pgcode``），仅用于脱敏诊断（D1-06）。

        asyncpg 原生异常暴露 ``sqlstate``，psycopg 暴露 ``pgcode``；SQLAlchemy 把
        驱动异常挂在 ``orig`` 上。SQLSTATE 是出错后仍可安全外发的诊断要素。
        """
        current: object = exception
        seen: set[int] = set()
        for _ in range(max_depth):
            if not isinstance(current, BaseException) or id(current) in seen:
                break
            seen.add(id(current))
            for attr in ("sqlstate", "pgcode"):
                value = getattr(current, attr, None)
                if isinstance(value, str) and value:
                    return value
            orig = getattr(current, "orig", None)
            if isinstance(orig, BaseException):
                current = orig
            else:
                current = current.__cause__ or current.__context__
        return None

    @staticmethod
    def sanitize_db_error(exception: BaseException | str) -> str:
        """DB 异常安全摘要：异常类名 + SQLSTATE + 脱敏描述（R9 / D1-06）。

        与 :meth:`sanitize_error` 的差别：
        1. 在脱敏前整段剔除 SQLAlchemy 的 ``[parameters: ...]`` 绑定参数块与
           ``(Background on this error ...)`` 链接——参数值域不可穷举，值级脱敏
           无法保证不漏；
        2. 显式保留异常类名与 SQLSTATE，使断开原始异常链后仍具备定因能力；
        3. 结果压缩空白并截断，避免日志刷屏（截断发生在脱敏之后，不会新增泄露）。

        Args:
            exception: 异常对象（或异常字符串）。

        Returns:
            形如 ``DBAPIError (sqlstate=23505): ...`` 的安全摘要。
        """
        sqlstate = DataSanitizer._extract_sqlstate(exception)
        type_name = type(exception).__name__ if isinstance(exception, BaseException) else "str"
        text = " ".join(DataSanitizer.sanitize_error(exception).split())
        if len(text) > DataSanitizer._DB_ERROR_TEXT_MAX_LEN:
            text = text[: DataSanitizer._DB_ERROR_TEXT_MAX_LEN] + "...(truncated)"
        label = f"{type_name} (sqlstate={sqlstate})" if sqlstate else type_name
        return f"{label}: {text}" if text else label

    @classmethod
    def _is_sensitive_key(cls, key: str, sensitive_keys: list[str]) -> bool:
        """判断字典键是否敏感：长关键字子串匹配，pwd/pass 仅按词段精确匹配。

        Args:
            key: 字典键名
            sensitive_keys: 敏感键关键字列表

        Returns:
            True 表示该键的值需要脱敏
        """
        lowered = key.lower()
        ambiguous = [keyword for keyword in sensitive_keys if keyword in cls._AMBIGUOUS_SENSITIVE_KEYWORDS]
        if any(keyword in lowered for keyword in sensitive_keys if keyword not in cls._AMBIGUOUS_SENSITIVE_KEYWORDS):
            return True
        if not ambiguous:
            return False
        segments = {segment.lower() for segment in cls._KEY_SEGMENT_SPLIT.split(key) if segment}
        return any(keyword in segments for keyword in ambiguous)

    # D20-004: 容器递归脱敏的最大深度（防御构造式深层嵌套/循环引用），
    # 以及位置参数超长字符串仅需清洗的截断窗口（输出被 [:100] 截断，无需全量正则）。
    _MAX_SANITIZE_DEPTH = 12
    _SANITIZE_ELIDED = "***"
    _ARGS_SCAN_WINDOW = 4096

    @classmethod
    def _replace_known_secrets(cls, text: str) -> str:
        """精确替换已注册 secret（最先执行，保持既有替换优先级）。"""
        for secret in list(cls._known_secrets):
            if secret in text:
                text = text.replace(secret, "***")
        return text

    @classmethod
    def _apply_text_rules(cls, text: str) -> str:
        """对字符串叶子应用 PII 与 URL 规则。

        顺序为 PII 先于 URL：保持 sanitize_dict 既有脱敏结果（诊断包 env 导出
        依赖 "PII 邮箱规则先于 URL 凭证规则" 的既有口径）；不做路径清洗。
        """
        text = cls._sanitize_pii_text(text)
        text = cls._PATTERN_URL_CREDENTIALS.sub(cls._mask_url_userinfo, text)
        text = cls._PATTERN_URL_QUERY_KEY.sub(r"\1\2=***", text)
        return text

    @classmethod
    def _sanitize_string_leaf(cls, text: str) -> str:
        return cls._apply_text_rules(cls._replace_known_secrets(text))

    @classmethod
    def _sanitize_value(
        cls,
        value: Any,
        sensitive_keys: list[str],
        *,
        depth: int = 0,
        seen: set[int] | None = None,
    ) -> Any:
        """统一递归脱敏任意值：dict/list/tuple/set/frozenset 与字符串叶子。

        非字符串标量（bool/int/float/None/bytes 等）原样返回以保持语义。
        """
        if _is_dataframe(value):
            return cls.sanitize_dataframe(value)
        if isinstance(value, dict):
            return cls._sanitize_mapping(value, sensitive_keys, depth=depth, seen=seen)
        if isinstance(value, (list, tuple, set, frozenset)):
            return cls._sanitize_sequence(value, sensitive_keys, depth=depth, seen=seen)
        if isinstance(value, str):
            return cls._sanitize_string_leaf(value)
        return value

    @classmethod
    def _sanitize_mapping(
        cls,
        data: dict,
        sensitive_keys: list[str],
        *,
        depth: int,
        seen: set[int] | None,
    ) -> Any:
        if depth > cls._MAX_SANITIZE_DEPTH:
            return cls._SANITIZE_ELIDED
        if seen is None:
            seen = set()
        marker = id(data)
        if marker in seen:
            return cls._SANITIZE_ELIDED
        seen.add(marker)
        try:
            result: dict = {}
            for key, item in data.items():
                if isinstance(key, str) and cls._is_sensitive_key(key, sensitive_keys):
                    result[key] = cls.sanitize_token(item) if isinstance(item, str) else "***"
                else:
                    result[key] = cls._sanitize_value(item, sensitive_keys, depth=depth + 1, seen=seen)
            return result
        finally:
            seen.discard(marker)

    @classmethod
    def _sanitize_sequence(
        cls,
        value: list | tuple | set | frozenset,
        sensitive_keys: list[str],
        *,
        depth: int,
        seen: set[int] | None,
    ) -> Any:
        if depth > cls._MAX_SANITIZE_DEPTH:
            return cls._SANITIZE_ELIDED
        if seen is None:
            seen = set()
        marker = id(value)
        if marker in seen:
            return cls._SANITIZE_ELIDED
        seen.add(marker)
        try:
            items = [cls._sanitize_value(item, sensitive_keys, depth=depth + 1, seen=seen) for item in value]
        finally:
            seen.discard(marker)
        if isinstance(value, tuple):
            return tuple(items)
        if isinstance(value, frozenset):
            return frozenset(items)
        if isinstance(value, set):
            return set(items)
        return items

    @staticmethod
    def sanitize_dict(data: dict, sensitive_keys: list[str] | None = None) -> dict:
        """
        字典脱敏处理

        递归处理 dict/list/tuple/set 容器内的字符串叶子（D20-004）：已注册
        secret 精确替换、PII 与 URL 规则对所有深度生效，避免仅顶层与非敏感
        key 的字符串被脱敏。

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
                "pwd",
                "pass",
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

        return cast(dict, DataSanitizer._sanitize_value(data, sensitive_keys))

    @staticmethod
    def sanitize_args(*args, sensitive_patterns: list[str] | None = None, **kwargs) -> tuple:
        """
        函数参数脱敏

        用于装饰器自动脱敏参数。关键字参数经 ``sanitize_dict`` 递归脱敏；
        位置参数（字符串与容器）应用同一字符串规则链（已注册 secret 精确替换
        → PII → URL），容器按 dict/list/tuple/set 递归（D20-004）。

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
            if _is_dataframe(arg):
                clean_args.append(DataSanitizer.sanitize_dataframe(arg))
                continue
            # R9: 位置字符串参数应用与 sanitize_dict 相同的字符串规则链
            # （已注册 secret 精确替换 → PII → URL），而非仅精确替换（D20-004）。
            # 长字符串先做 secret 全量替换（防止 secret 跨截断边界），再仅对
            # 截断窗口应用正则，避免超长参数触发全量正则扫描。
            if isinstance(arg, str):
                sanitized = DataSanitizer._replace_known_secrets(arg)
                if len(arg) > 100:
                    sanitized = DataSanitizer._apply_text_rules(sanitized[: DataSanitizer._ARGS_SCAN_WINDOW])
                    clean_args.append(f"{sanitized[:50]}...(truncated)")
                else:
                    clean_args.append(DataSanitizer._apply_text_rules(sanitized))
                continue
            # R9: 非字符串位置参数（dict/list/tuple/set 等）先按容器递归脱敏
            # （敏感键遮蔽 + 字符串叶子规则），再对 repr 应用字符串规则并截断，
            # 避免含 secret/PII 的容器经 repr()[:100] 泄露明文（D20-004）。
            sanitized_value = DataSanitizer._sanitize_value(arg, sensitive_patterns)
            full_repr = DataSanitizer._replace_known_secrets(repr(sanitized_value))
            clean_args.append(DataSanitizer._apply_text_rules(full_repr[: DataSanitizer._ARGS_SCAN_WINDOW])[:100])

        return tuple(clean_args), clean_kwargs
