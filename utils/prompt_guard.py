import re
import logging
import unicodedata

logger = logging.getLogger(__name__)

MAX_PROMPT_LENGTH = 8000

# Zero-width / invisible characters used to obfuscate injection patterns.
# Kept separate from _normalize_unicode to avoid touching validated behavior.
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff\u00ad\u2060\u180e]")

_INJECTION_PATTERNS = [
    re.compile(
        r"ignore\s+(all\s+)?(previous|above|prior|following|below)\s+(instructions?|rules?|prompts?)", re.IGNORECASE
    ),
    re.compile(
        r"disregard\s+(all\s+)?(previous|above|prior|following|below)\s+(instructions?|rules?|prompts?)", re.IGNORECASE
    ),
    re.compile(r"forget\s+(all\s+)?(previous|above|prior)\s+(instructions?|rules?|prompts?)", re.IGNORECASE),
    re.compile(r"bypass\s+(all\s+)?(safety|security|content)\s+(rules?|filters?|checks?)", re.IGNORECASE),
    re.compile(r"override\s+(all\s+)?(safety|security|content)\s+(rules?|filters?|checks?)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(?:a\s+)?(?:DAN|jailbreak|unrestricted|uncensored)", re.IGNORECASE),
    re.compile(
        r"^system\s*:\s*(you\s+are|from\s+now|new\s+instructions?|ignore|disregard|override)",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(r"<\s*/?\s*system\s*>", re.IGNORECASE),
    re.compile(r"pretend\s+you\s+(are|can|have)\s+no\s+(rules?|restrictions?|limits?)", re.IGNORECASE),
    re.compile(r"act\s+as\s+if\s+you\s+(have\s+)?no\s+(rules?|restrictions?|limits?)", re.IGNORECASE),
]

_CN_INJECTION_PATTERNS = [
    re.compile(r"忽略.{0,4}(所有|之前|上面|以上|以下)?的?(指令|规则|提示|设定)"),
    re.compile(r"忘记.{0,4}(所有|之前|上面|以上)?的?(指令|规则|提示|设定)"),
    re.compile(r"绕过.{0,4}(安全|限制|规则|检查)"),
    re.compile(r"无视.{0,4}(所有|之前|上面|以上)?的?(指令|规则|提示|设定)"),
    re.compile(r"你现在是(无限制|不受限|未审查|越狱)"),
    re.compile(r"假装你(没有|不受)(规则|限制|约束)"),
    re.compile(r"不要(遵守|遵循|执行)(任何|之前的)?(规则|指令|限制)"),
]

_INJECTION_WARNING_KEYWORDS = [
    "jailbreak",
    "DAN mode",
    "unrestricted",
    "uncensored",
]


def _normalize_unicode(text: str) -> str:
    """Normalize Unicode to NFKC form to eliminate full-width and zero-width character obfuscation.

    NFKC normalization converts:
    - Full-width Latin: ＩＧＮＯＲＥ → IGNORE
    - Compatibility characters: ﬁ → fi
    - Zero-width characters are stripped separately.
    """
    normalized = unicodedata.normalize("NFKC", text)
    normalized = re.sub(r"[\u200b\u200c\u200d\ufeff\u00ad]", "", normalized)
    return normalized


def validate_prompt(prompt: str) -> tuple[bool, str]:
    """
    Validate a user-provided prompt for potential injection risks.

    Returns:
        (is_valid, warning_message) — is_valid is True if the prompt passes all checks.
        warning_message is empty when valid, or contains a description of the issue.

    Limitation (SEC-003):
        This function is a regex blacklist and only intercepts common jailbreak
        phrases. It is NOT a complete defense — a determined attacker can bypass
        it with paraphrasing, encoding tricks, or novel patterns. Defense in
        depth relies on three layers:
        1. System prompt declares external content as untrusted data (SEC-001).
        2. External content is neutralized before entering the prompt
           (neutralize_external_text in this module).
        3. LLM output is sanitized and length-limited before rendering
           (services/ai_service.validate_ai_analysis_response, SEC-002).
    """
    if not prompt or not prompt.strip():
        return True, ""

    if len(prompt) > MAX_PROMPT_LENGTH:
        return False, "prompt_err_length"

    normalized = _normalize_unicode(prompt)

    warnings = []

    for pattern in _INJECTION_PATTERNS:
        match = pattern.search(normalized)
        if match:
            warnings.append("prompt_err_injection")
            break

    if not warnings:
        for pattern in _CN_INJECTION_PATTERNS:
            match = pattern.search(normalized)
            if match:
                warnings.append("prompt_err_injection")
                break

    if not warnings:
        lower = normalized.lower()
        for kw in _INJECTION_WARNING_KEYWORDS:
            if kw.lower() in lower:
                warnings.append("prompt_err_keyword")
                break

    if warnings:
        logger.warning(f"[PromptGuard] {warnings[0]}")
        return False, warnings[0]

    return True, ""


def sanitize_prompt(prompt: str) -> str:
    """
    Sanitize a user prompt by truncating to max length.
    Does NOT attempt to strip injection patterns — that would give a false sense of security.
    Instead, use validate_prompt() to reject and inform the user.
    """
    if not prompt:
        return ""
    if len(prompt) > MAX_PROMPT_LENGTH:
        logger.warning(f"[PromptGuard] Truncating prompt from {len(prompt)} to {MAX_PROMPT_LENGTH} characters.")
        return prompt[:MAX_PROMPT_LENGTH]
    return prompt


def neutralize_external_text(text: str, max_len: int = 2000) -> str:
    """Neutralize untrusted external text before injecting into LLM prompts (SEC-001).

    Defends against prompt injection via three layers:
    1. Strips zero-width / invisible characters used to obfuscate injection patterns.
    2. Replaces angle brackets ``<`` / ``>`` with single guillemets ``‹`` / ``›``
       so XML-style structural tags (e.g. ``</recent_news>``, ``<system>``) lose
       their tag semantics and cannot close/open prompt structure tags.
    3. Truncates to ``max_len`` to bound prompt size.

    Unlike :func:`validate_prompt`, this is applied to external data fields
    (news titles, stock names, global context) rather than user-typed prompts,
    and does not reject the input — only renders it inert.
    """
    if not text:
        return ""
    if not isinstance(text, str):
        text = str(text)
    text = _ZERO_WIDTH_RE.sub("", text)
    text = text.replace("<", "‹").replace(">", "›")
    if len(text) > max_len:
        text = text[:max_len]
    return text
