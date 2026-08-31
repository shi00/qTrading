"""
S5-1 fix: Error classification utility.
Moved from ui/i18n.py to avoid DDD reverse dependency.
Services should not import from ui package.

S5-4 fix: Added severity classification to distinguish recoverable
business errors from system-level errors that should not be swallowed.

A-P0-1 fix: Removed reverse dependency on ui.i18n.
classify_error now returns message_key instead of translated message.
Callers in the UI layer should use I18n.get(error_info["message_key"]) to get
the translated message. For db context with format args, use message_key + format_args.

P1-17 fix: Added explicit handling for LiteLLM permanent errors.
Permanent errors (AuthenticationError, ContentPolicyViolationError, etc.)
should not be retried, while transient errors (RateLimitError, ServiceUnavailableError)
can be retried.
"""

from __future__ import annotations

import logging
from typing import Any

from core.errors import AppError
from core.i18n import Message

SYSTEM_LEVEL_EXCEPTIONS = (
    MemoryError,
    SystemExit,
    KeyboardInterrupt,
)

SYSTEM_LEVEL_ERROR_TYPES = {
    "MemoryError",
    "SystemExit",
    "KeyboardInterrupt",
    "RecursionError",
    # R5: EngineDisposedError 表示引擎已释放，继续操作属于僵尸引擎操作。
    # 用类名字符串匹配避免 utils 反向依赖 data 层（与 TushareAPIPermissionError 模式一致）。
    "EngineDisposedError",
}

RECOVERABLE_CODES = {
    "timeout",
    "network",
    "rate_limit",
    "server_error",
    "server",
    "dns",
    "ssl",
    "connection",
    "refused",
}

PERMANENT_ERROR_CODES = {
    "auth_failed",
    "forbidden",
    "not_found",
    "model_not_found",
    "content_policy",
    "insufficient_quota",
}

try:
    import asyncpg  # type: ignore[import-untyped]

    _ASYNCPG_AVAILABLE = True
except ImportError:
    _ASYNCPG_AVAILABLE = False

try:
    import httpx  # type: ignore[import-untyped]

    _HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover -- httpx 是项目直接依赖，import 失败仅限可选环境
    _HTTPX_AVAILABLE = False

# R16: litellm 是重库（首次 import 可达 18s+）。将模块级 `from litellm.exceptions import`
# 改为惰性加载，避免 error_classifier 被 services.ai_service 等模块导入时同步触发
# litellm 加载，阻塞 UI 启动链。异常类仅在 classify_error(context="llm") 时才需要。
_LITELLM_IMPORT_ATTEMPTED = False
_LITELLM_AVAILABLE = False
LiteLLMAuthenticationError = None  # type: ignore[misc,assignment]
ContentPolicyViolationError = None  # type: ignore[misc,assignment]
PermissionDeniedError = None  # type: ignore[misc,assignment]
LiteLLMNotFoundError = None  # type: ignore[misc,assignment]
RateLimitError = None  # type: ignore[misc,assignment]
ServiceUnavailableError = None  # type: ignore[misc,assignment]
LiteLLMInternalServerError = None  # type: ignore[misc,assignment]
LiteLLMAPIConnectionError = None  # type: ignore[misc,assignment]


def _load_litellm_exceptions() -> bool:
    """惰性加载 litellm 异常类并返回是否可用（R16）。

    仅当 classify_error(context="llm") 需要 isinstance 判定时才触发 import。
    加载失败后以 _LITELLM_IMPORT_ATTEMPTED 阻止重复 import。
    """
    global _LITELLM_AVAILABLE, _LITELLM_IMPORT_ATTEMPTED
    global LiteLLMAuthenticationError, ContentPolicyViolationError, PermissionDeniedError
    global LiteLLMNotFoundError, RateLimitError, ServiceUnavailableError
    global LiteLLMInternalServerError, LiteLLMAPIConnectionError
    if _LITELLM_IMPORT_ATTEMPTED:
        return _LITELLM_AVAILABLE
    _LITELLM_IMPORT_ATTEMPTED = True
    try:
        from litellm.exceptions import (  # type: ignore[import-untyped]
            APIConnectionError as LiteLLMAPIConnectionError,
            AuthenticationError as LiteLLMAuthenticationError,
            ContentPolicyViolationError,
            InternalServerError as LiteLLMInternalServerError,
            NotFoundError as LiteLLMNotFoundError,
            PermissionDeniedError,
            RateLimitError,
            ServiceUnavailableError,
        )

        _LITELLM_AVAILABLE = True
    except ImportError:  # pragma: no cover -- litellm 是项目直接依赖，import 失败仅限可选环境
        _LITELLM_AVAILABLE = False
    return _LITELLM_AVAILABLE


def classify_severity(e: Exception, context: str = "general") -> str:
    """
    S5-4 fix: Classify exception severity.

    Returns:
        "system" - System-level error, must not be swallowed as warning.
                   Should propagate or log at CRITICAL/ERROR.
        "recoverable" - Business-recoverable error (network, timeout, rate limit).
                        Safe to log as WARNING and retry.
        "operational" - Operational error (bad input, missing data).
                        Log as WARNING, no retry needed.
    """
    error_type = type(e).__name__
    error_str = str(e).lower()

    if error_type in SYSTEM_LEVEL_ERROR_TYPES or isinstance(e, SYSTEM_LEVEL_EXCEPTIONS):
        return "system"

    if isinstance(e, (OSError,)) and ("disk" in error_str or "space" in error_str):
        return "system"

    if isinstance(e, PermissionError):
        return "system"

    # 识别 Tushare API 权限错误（token 失效/接口无权限）：
    # 这是用户配置问题，不应刷屏 ERROR + traceback；归为 recoverable 走 WARNING 路径。
    # 用类名字符串匹配而非 isinstance，避免 utils 反向依赖 data 层（R1 架构边界）。
    if error_type == "TushareAPIPermissionError":
        return "recoverable"

    classified = classify_error(e, context)
    code = classified.get("code", "unknown")

    if code in RECOVERABLE_CODES:
        return "recoverable"

    return "operational"


def classify_error(e: Exception, context: str = "general") -> dict:
    # review05-E3: AppError 携带结构化信息，语义无需事后推断，直接返回。
    if isinstance(e, AppError):
        return e.to_error_info()

    error_str = str(e).lower()
    error_type = type(e).__name__

    if context == "token":
        if "token" in error_str and ("invalid" in error_str or "not set" in error_str):
            return {"code": "invalid", "message_key": "wizard_err_token_invalid"}
        # HTTP auth failure status codes (Tushare returns 403 for bad token)
        if "401" in error_str or "403" in error_str:
            return {"code": "invalid", "message_key": "wizard_err_token_invalid"}
        # Common Tushare Chinese auth error messages
        if any(kw in error_str for kw in ("权限不足", "鉴权失败", "认证失败", "未授权", "非法token", "无效token")):
            return {"code": "invalid", "message_key": "wizard_err_token_invalid"}
        # English auth-related keywords
        if any(kw in error_str for kw in ("unauthorized", "forbidden", "auth", "permission denied")):
            return {"code": "invalid", "message_key": "wizard_err_token_invalid"}
        if "timeout" in error_str or "timed out" in error_str:
            return {"code": "timeout", "message_key": "wizard_err_token_timeout"}
        if "connection" in error_str or "network" in error_str or "connect" in error_str:
            return {"code": "network", "message_key": "wizard_err_token_network"}
        if "抱歉" in error_str or "每分钟" in error_str or "限制" in error_str:
            return {"code": "server", "message_key": "wizard_err_token_server"}
        return {"code": "invalid", "message_key": "wizard_err_token_invalid"}

    if context == "llm":
        if _load_litellm_exceptions():
            if LiteLLMAuthenticationError is not None and isinstance(e, LiteLLMAuthenticationError):
                return {"code": "auth_failed", "message_key": "llm_err_auth_failed", "should_retry": False}
            if ContentPolicyViolationError is not None and isinstance(e, ContentPolicyViolationError):
                return {"code": "content_policy", "message_key": "llm_err_content_policy", "should_retry": False}
            if PermissionDeniedError is not None and isinstance(e, PermissionDeniedError):
                return {"code": "forbidden", "message_key": "llm_err_forbidden", "should_retry": False}
            if LiteLLMNotFoundError is not None and isinstance(e, LiteLLMNotFoundError):
                return {"code": "not_found", "message_key": "llm_err_not_found", "should_retry": False}
            if RateLimitError is not None and isinstance(e, RateLimitError):
                return {"code": "rate_limit", "message_key": "llm_err_rate_limit", "should_retry": True}
            if ServiceUnavailableError is not None and isinstance(e, ServiceUnavailableError):
                return {"code": "server_error", "message_key": "llm_err_server", "should_retry": True}
            if LiteLLMInternalServerError is not None and isinstance(e, LiteLLMInternalServerError):
                return {"code": "server_error", "message_key": "llm_err_server", "should_retry": True}
            if LiteLLMAPIConnectionError is not None and isinstance(e, LiteLLMAPIConnectionError):
                return {"code": "network", "message_key": "llm_err_network", "should_retry": True}

        if _HTTPX_AVAILABLE:
            if isinstance(e, httpx.TimeoutException):
                return {"code": "timeout", "message_key": "llm_err_timeout", "should_retry": True}
            if isinstance(e, (httpx.ConnectError, httpx.ReadError, httpx.NetworkError)):
                return {"code": "network", "message_key": "llm_err_network", "should_retry": True}

        if isinstance(e, TimeoutError):
            return {"code": "timeout", "message_key": "llm_err_timeout", "should_retry": True}
        if isinstance(e, (ConnectionError, OSError)):
            return {"code": "network", "message_key": "llm_err_network", "should_retry": True}

        if "insufficient_quota" in error_str or "quota" in error_str or "402" in error_str:
            return {"code": "insufficient_quota", "message_key": "llm_err_insufficient_quota", "should_retry": False}
        if "content policy" in error_str or "content violation" in error_str:
            return {"code": "content_policy", "message_key": "llm_err_content_policy", "should_retry": False}
        if "401" in error_str or "unauthorized" in error_str or "invalid api key" in error_str:
            return {"code": "auth_failed", "message_key": "llm_err_auth_failed", "should_retry": False}
        if "403" in error_str or "forbidden" in error_str:
            return {"code": "forbidden", "message_key": "llm_err_forbidden", "should_retry": False}
        if "404" in error_str or "not found" in error_str:
            return {"code": "not_found", "message_key": "llm_err_not_found", "should_retry": False}
        if "429" in error_str or "rate limit" in error_str or "too many requests" in error_str:
            return {"code": "rate_limit", "message_key": "llm_err_rate_limit", "should_retry": True}
        if "500" in error_str or "502" in error_str or "503" in error_str or "504" in error_str:
            return {"code": "server_error", "message_key": "llm_err_server", "should_retry": True}
        if "timeout" in error_str or "timed out" in error_str:
            return {"code": "timeout", "message_key": "llm_err_timeout", "should_retry": True}
        if "connection" in error_str or "network" in error_str or "connect" in error_str:
            return {"code": "network", "message_key": "llm_err_network", "should_retry": True}
        if "dns" in error_str or "getaddrinfo" in error_str:
            return {"code": "dns", "message_key": "llm_err_dns", "should_retry": True}
        if "ssl" in error_str or "certificate" in error_str:
            return {"code": "ssl", "message_key": "llm_err_ssl", "should_retry": True}
        if "model" in error_str and ("not found" in error_str or "unsupported" in error_str):
            return {
                "code": "model_not_found",
                "message_key": "llm_err_model_not_found",
                "should_retry": False,
            }
        return {"code": "unknown", "message_key": "llm_err_unknown", "should_retry": False}

    if context == "db":
        if error_type == "EmbeddedPostgresStartError" or "sidecar" in error_str or "embedded_pg" in error_str:
            import re

            if "not found" in error_str:
                match = re.search(r"not found:\s*(.+)$", str(e), re.IGNORECASE)
                path = match.group(1).strip() if match else ""
                return {
                    "code": "sidecar_not_found",
                    "message_key": "db_err_sidecar_not_found",
                    "format_args": {"path": path or "sidecars/qtrading-pg-sidecar.exe"},
                }
            if "not executable" in error_str:
                match = re.search(r"not executable:\s*(.+)$", str(e), re.IGNORECASE)
                path = match.group(1).strip() if match else ""
                return {
                    "code": "sidecar_not_executable",
                    "message_key": "db_err_sidecar_not_executable",
                    "format_args": {"path": path or "sidecars/qtrading-pg-sidecar.exe"},
                }
            if "sha256" in error_str:
                return {
                    "code": "sha256_mismatch",
                    "message_key": "db_err_sidecar_sha256_mismatch",
                }
            if "initdb failed" in error_str or "exit=11" in error_str:
                return {
                    "code": "initdb_failed",
                    "message_key": "db_err_embedded_initdb_failed",
                }
            if "disk full" in error_str or "exit=15" in error_str:
                return {
                    "code": "disk_space",
                    "message_key": "common_err_disk_space",
                }
            if "password error" in error_str or "exit=16" in error_str:
                return {
                    "code": "password_error",
                    "message_key": "db_err_embedded_password_error",
                }
            if "already running" in error_str or "exit=50" in error_str:
                return {
                    "code": "already_running",
                    "message_key": "db_err_embedded_already_running",
                }
            if "crashed" in error_str or "exit=60" in error_str:
                return {
                    "code": "crashed",
                    "message_key": "db_err_embedded_crashed",
                }
            return {
                "code": "embedded_start_failed",
                "message_key": "db_err_embedded_start_failed",
            }
        if error_type == "ValueError":
            return {
                "code": "format",
                "message_key": "db_err_format",
                "format_args": {"error": error_str},
            }
        if _ASYNCPG_AVAILABLE and isinstance(e, asyncpg.InvalidPasswordError):
            return {"code": "auth", "message_key": "db_err_auth"}
        if _ASYNCPG_AVAILABLE and isinstance(e, asyncpg.InvalidCatalogNameError):
            return {
                "code": "not_found",
                "message_key": "db_err_not_found",
                "format_args": {"database": str(e)},
            }
        if _ASYNCPG_AVAILABLE and isinstance(e, asyncpg.exceptions.PostgresConnectionError):
            return {"code": "refused", "message_key": "db_err_refused"}
        if "password" in error_str or "authentication" in error_str:
            return {"code": "auth", "message_key": "db_err_auth"}
        if "timeout" in error_str:
            return {"code": "timeout", "message_key": "db_err_timeout"}
        # P3-M5-ClassifyError-System-Gap: 扩展 interrupted 识别（原 base_dao.py 手写字符串匹配移除）
        # 注意：必须在 refused 分支之前匹配，因为 "no active connection" 含 "connect" 子串
        if (
            "closed in the middle of operation" in error_str
            or "connection was closed" in error_str
            or "interrupted" in error_str
            or "no active connection" in error_str
            or "database is closed" in error_str
            or "connectiondoesnotexisterror" in error_str
        ):
            return {"code": "interrupted", "message_key": "db_err_interrupted"}
        if "winerror 64" in error_str:
            return {"code": "proxy", "message_key": "db_err_proxy"}
        if "does not exist" in error_str or "no such table" in error_str:
            import re

            match = re.search(r'database\s+["\']?([^"\'\s]+)["\']?\s+does\s+not\s+exist', error_str)
            db_name = match.group(1) if match else ""
            return {
                "code": "not_found",
                "message_key": "db_err_not_found",
                "format_args": {"database": db_name or "目标数据库"},
            }
        if "can't locate revision" in error_str or "no such revision" in error_str:
            return {
                "code": "orphaned_revision",
                "message_key": "db_err_orphaned_revision",
                "format_args": {"error": str(e)},
            }
        if "refused" in error_str or "connect" in error_str:
            return {"code": "refused", "message_key": "db_err_refused"}
        return {"code": "unknown", "message_key": "db_err_unknown"}

    if context == "chart":
        if "timeout" in error_str or "timed out" in error_str:
            return {"code": "timeout", "message_key": "detail_err_chart_timeout"}
        if "connection" in error_str or "network" in error_str or "connect" in error_str:
            return {"code": "network", "message_key": "detail_err_chart_network"}
        if "data" in error_str or "empty" in error_str or "null" in error_str:
            return {"code": "data", "message_key": "detail_err_chart_data"}
        return {"code": "unknown", "message_key": "detail_err_chart_unknown"}

    if error_type == "JSONDecodeError":
        return {"code": "json_parse", "message_key": "common_err_json_parse"}

    if error_type in ("FileNotFoundError", "FileExistsError"):
        return {
            "code": "file_not_found",
            "message_key": "common_err_file_not_found",
        }
    if error_type == "PermissionError":
        return {"code": "permission", "message_key": "common_err_permission"}
    if error_type == "OSError" and ("disk" in error_str or "space" in error_str):
        return {"code": "disk_space", "message_key": "common_err_disk_space"}

    if "timeout" in error_str or "timed out" in error_str:
        return {"code": "timeout", "message_key": "common_err_timeout"}
    if "connection" in error_str or "network" in error_str or "connect" in error_str:
        return {"code": "network", "message_key": "common_err_network"}
    if "500" in error_str or "502" in error_str or "503" in error_str:
        return {"code": "server", "message_key": "common_err_server"}

    return {"code": "unknown", "message_key": "common_err_unknown"}


def get_error_message(error_info: dict) -> str:
    """
    Translate error_info from classify_error into a human-readable message.

    This function bridges utils → core: callers depend on core.i18n
    (the allowed single-direction dependency, see core/__init__.py).
    Pure utils/strategies code that does not need localized messages should
    just read error_info["message_key"] and pass it up.

    VM 层禁止调用本函数（产出已翻译字符串、感知 locale）；
    ViewModel 必须使用 get_error_message_key() 产出 Message(key, params)。
    """
    from core.i18n import I18n

    message_key = error_info.get("message_key", "common_err_unknown")
    format_args = error_info.get("format_args")
    if format_args:
        return I18n.get(message_key, **format_args)
    return I18n.get(message_key)


def get_error_message_key(error_info: dict) -> Message:
    """classify_error 结果 → Message(key, params)，供 VM 层产出 i18n key（不感知 locale）。

    符合 CLAUDE.md §3.2「VM 不感知 locale，只产出 i18n key」：
    VM 将本函数返回值直接存入 state（Message），View 渲染时按当前 locale 翻译。
    View 层 / 非 VM 调用方仍可使用 get_error_message()。
    """
    message_key = error_info.get("message_key", "common_err_unknown")
    return Message(message_key, error_info.get("format_args") or {})


def log_classified(
    logger: logging.Logger,
    exc: Exception,
    context: str = "general",
    msg: str = "",
    *args: Any,
    **kwargs: Any,
) -> dict:
    """分类异常、按严重度选级别记录、返回 error_info 供调用方使用。

    review01-A13：收口全项目 30+ 文件重复的
    「classify_error + classify_severity + 三分支选级别」样板代码。
    msg 的前两个 ``%s`` 由本函数自动填充 ``error_info["code"]`` 与
    ``DataSanitizer.sanitize_error(exc)``，其余格式参数经 ``*args`` 透传，
    日志级别按严重度选择（system→critical / recoverable→warning / 其他→error）。

    示例（原 8 行 → 1 行）：
        error_info = log_classified(
            logger, e, context="general",
            msg="[Bootstrap] DB init failed (%s): %s",
            exc_info=True,
        )
    """
    from utils.sanitizers import DataSanitizer

    error_info = classify_error(exc, context=context)
    severity = classify_severity(exc, context=context)
    if severity == "system":
        _log = logger.critical
    elif severity == "recoverable":
        _log = logger.warning
    else:
        _log = logger.error
    _log(msg, error_info["code"], DataSanitizer.sanitize_error(exc), *args, **kwargs)
    return error_info
