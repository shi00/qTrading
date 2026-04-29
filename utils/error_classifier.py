"""
S5-1 fix: Error classification utility.
Moved from ui/i18n.py to avoid DDD reverse dependency.
Services should not import from ui package.

S5-4 fix: Added severity classification to distinguish recoverable
business errors from system-level errors that should not be swallowed.
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

    if isinstance(e, (OSError,)) and "disk" in error_str or "space" in error_str:
        return "system"

    if isinstance(e, PermissionError):
        return "system"

    classified = classify_error(e, context)
    code = classified.get("code", "unknown")

    if code in RECOVERABLE_CODES:
        return "recoverable"

    return "operational"


def classify_error(e: Exception, context: str = "general") -> dict:
    """
    Classify exceptions into user-friendly i18n messages.

    Args:
        e: The exception to classify
        context: Error context - "token", "db", "llm", "chart", "general"

    Returns:
        {"code": str, "message": str} where message is translated i18n text
    """
    from ui.i18n import I18n

    error_str = str(e).lower()
    error_type = type(e).__name__

    if context == "token":
        if "token" in error_str and ("invalid" in error_str or "not set" in error_str):
            return {"code": "invalid", "message": I18n.get("wizard_err_token_invalid")}
        if "timeout" in error_str or "timed out" in error_str:
            return {"code": "timeout", "message": I18n.get("wizard_err_token_timeout")}
        if "connection" in error_str or "network" in error_str or "connect" in error_str:
            return {"code": "network", "message": I18n.get("wizard_err_token_network")}
        if "抱歉" in error_str or "每分钟" in error_str or "限制" in error_str:
            return {"code": "server", "message": I18n.get("wizard_err_token_server")}
        return {"code": "unknown", "message": I18n.get("wizard_err_token_unknown")}

    if context == "llm":
        if "401" in error_str or "unauthorized" in error_str or "invalid api key" in error_str:
            return {"code": "auth_failed", "message": I18n.get("llm_err_auth_failed")}
        if "403" in error_str or "forbidden" in error_str:
            return {"code": "forbidden", "message": I18n.get("llm_err_forbidden")}
        if "404" in error_str or "not found" in error_str:
            return {"code": "not_found", "message": I18n.get("llm_err_not_found")}
        if "429" in error_str or "rate limit" in error_str or "too many requests" in error_str:
            return {"code": "rate_limit", "message": I18n.get("llm_err_rate_limit")}
        if "500" in error_str or "502" in error_str or "503" in error_str or "504" in error_str:
            return {"code": "server_error", "message": I18n.get("llm_err_server")}
        if "timeout" in error_str or "timed out" in error_str:
            return {"code": "timeout", "message": I18n.get("llm_err_timeout")}
        if "connection" in error_str or "network" in error_str or "connect" in error_str:
            return {"code": "network", "message": I18n.get("llm_err_network")}
        if "dns" in error_str or "getaddrinfo" in error_str:
            return {"code": "dns", "message": I18n.get("llm_err_dns")}
        if "ssl" in error_str or "certificate" in error_str:
            return {"code": "ssl", "message": I18n.get("llm_err_ssl")}
        if "model" in error_str and ("not found" in error_str or "unsupported" in error_str):
            return {
                "code": "model_not_found",
                "message": I18n.get("llm_err_model_not_found"),
            }
        return {"code": "unknown", "message": I18n.get("llm_err_unknown")}

    if context == "db":
        if error_type == "ValueError":
            return {
                "code": "format",
                "message": I18n.get("db_err_format").format(error=error_str),
            }
        if "password" in error_str or "authentication" in error_str:
            return {"code": "auth", "message": I18n.get("db_err_auth")}
        if "timeout" in error_str:
            return {"code": "timeout", "message": I18n.get("db_err_timeout")}
        if "refused" in error_str or "connect" in error_str:
            return {"code": "refused", "message": I18n.get("db_err_refused")}
        return {"code": "unknown", "message": I18n.get("db_err_unknown")}

    if context == "chart":
        if "timeout" in error_str or "timed out" in error_str:
            return {"code": "timeout", "message": I18n.get("detail_err_chart_timeout")}
        if "connection" in error_str or "network" in error_str or "connect" in error_str:
            return {"code": "network", "message": I18n.get("detail_err_chart_network")}
        if "data" in error_str or "empty" in error_str or "null" in error_str:
            return {"code": "data", "message": I18n.get("detail_err_chart_data")}
        return {"code": "unknown", "message": I18n.get("detail_err_chart_unknown")}

    # JSON parsing errors
    if error_type == "JSONDecodeError":
        return {"code": "json_parse", "message": I18n.get("common_err_json_parse")}

    # File system errors
    if error_type in ("FileNotFoundError", "FileExistsError"):
        return {
            "code": "file_not_found",
            "message": I18n.get("common_err_file_not_found"),
        }
    if error_type == "PermissionError":
        return {"code": "permission", "message": I18n.get("common_err_permission")}
    if error_type == "OSError" and ("disk" in error_str or "space" in error_str):
        return {"code": "disk_space", "message": I18n.get("common_err_disk_space")}

    # General errors
    if "timeout" in error_str or "timed out" in error_str:
        return {"code": "timeout", "message": I18n.get("common_err_timeout")}
    if "connection" in error_str or "network" in error_str or "connect" in error_str:
        return {"code": "network", "message": I18n.get("common_err_network")}
    if "500" in error_str or "502" in error_str or "503" in error_str:
        return {"code": "server", "message": I18n.get("common_err_server")}

    return {"code": "unknown", "message": I18n.get("common_err_unknown")}
