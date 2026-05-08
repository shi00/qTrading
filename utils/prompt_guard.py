import re
import logging

logger = logging.getLogger(__name__)

MAX_PROMPT_LENGTH = 8000

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

_INJECTION_WARNING_KEYWORDS = [
    "ignore all",
    "disregard all",
    "forget all",
    "bypass safety",
    "override safety",
    "jailbreak",
    "DAN mode",
    "unrestricted",
    "uncensored",
]


def validate_prompt(prompt: str) -> tuple[bool, str]:
    """
    Validate a user-provided prompt for potential injection risks.

    Returns:
        (is_valid, warning_message) — is_valid is True if the prompt passes all checks.
        warning_message is empty when valid, or contains a description of the issue.
    """
    if not prompt or not prompt.strip():
        return True, ""

    if len(prompt) > MAX_PROMPT_LENGTH:
        return False, "prompt_err_length"

    warnings = []

    for pattern in _INJECTION_PATTERNS:
        match = pattern.search(prompt)
        if match:
            warnings.append("prompt_err_injection")
            break

    if not warnings:
        lower = prompt.lower()
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
