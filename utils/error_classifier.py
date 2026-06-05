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
    from litellm.exceptions import (  # type: ignore[import-untyped]
        AuthenticationError as LiteLLMAuthenticationError,
        ContentPolicyViolationError,
        NotFoundError as LiteLLMNotFoundError,
        PermissionDeniedError,
        RateLimitError,
        ServiceUnavailableError,
    )

    _LITELLM_AVAILABLE = True
except ImportError:
    _LITELLM_AVAILABLE = False
    LiteLLMAuthenticationError = None  # type: ignore[misc,assignment]
    ContentPolicyViolationError = None  # type: ignore[misc,assignment]
    PermissionDeniedError = None  # type: ignore[misc,assignment]
    LiteLLMNotFoundError = None  # type: ignore[misc,assignment]
    RateLimitError = None  # type: ignore[misc,assignment]
    ServiceUnavailableError = None  # type: ignore[misc,assignment]

LITELLM_PERMANENT_EXCEPTIONS = (
    (
        LiteLLMAuthenticationError,
        ContentPolicyViolationError,
        PermissionDeniedError,
        LiteLLMNotFoundError,
    )
    if _LITELLM_AVAILABLE
    else ()
)

LITELLM_TRANSIENT_EXCEPTIONS = (
    (
        RateLimitError,
        ServiceUnavailableError,
    )
    if _LITELLM_AVAILABLE
    else ()
)


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

    classified = classify_error(e, context)
    code = classified.get("code", "unknown")

    if code in RECOVERABLE_CODES:
        return "recoverable"

    return "operational"


def classify_error(e: Exception, context: str = "general") -> dict:
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
        if _LITELLM_AVAILABLE and isinstance(e, LITELLM_PERMANENT_EXCEPTIONS):
            if isinstance(e, LiteLLMAuthenticationError):
                return {"code": "auth_failed", "message_key": "llm_err_auth_failed", "should_retry": False}
            if isinstance(e, ContentPolicyViolationError):
                return {"code": "content_policy", "message_key": "llm_err_content_policy", "should_retry": False}
            if isinstance(e, PermissionDeniedError):
                return {"code": "forbidden", "message_key": "llm_err_forbidden", "should_retry": False}
            if isinstance(e, LiteLLMNotFoundError):
                return {"code": "not_found", "message_key": "llm_err_not_found", "should_retry": False}

        if _LITELLM_AVAILABLE and isinstance(e, LITELLM_TRANSIENT_EXCEPTIONS):
            if isinstance(e, RateLimitError):
                return {"code": "rate_limit", "message_key": "llm_err_rate_limit", "should_retry": True}
            if isinstance(e, ServiceUnavailableError):
                return {"code": "server_error", "message_key": "llm_err_server", "should_retry": True}

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

    This function bridges utils → ui: only callers that already depend on
    ui.i18n should use this helper.  Pure utils/strategies code should
    just read error_info["message_key"] and pass it up.
    """
    from core.i18n import I18n

    message_key = error_info.get("message_key", "common_err_unknown")
    format_args = error_info.get("format_args")
    if format_args:
        return I18n.get(message_key, **format_args)
    return I18n.get(message_key)
