import re
import logging
import unicodedata
from dataclasses import dataclass

from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)

MAX_PROMPT_LENGTH = 8000

#: 模型可见投影的默认长度上限（外部资料单段）；公开导出不按此上限截断。
EXTERNAL_TEXT_MAX_LEN = 2000

# 零宽/不可见字符清单唯一正本（UTILS-B-05）：validate_prompt 的归一化与
# neutralize_external_text 的中和层共用同一清单。此前两处各自维护（校验层曾缺
# \u2060/\u180e），注入文本夹带 \u2060（如 "ig\u2060nore previous instructions"）
# 可残存零宽字符绕过注入正则，形成校验层弱于中和层的覆盖倒挂；现确认对齐后
# 收敛为单点定义，防止清单再次漂移。
# \u2061-\u2064（FUNCTION APPLICATION / INVISIBLE TIMES / INVISIBLE SEPARATOR /
# INVISIBLE PLUS）为数学排版专用不可见 Format 字符，NFKC 不剥除（Cf 类别保留），
# 夹带注入短语（如 "ig\u2061nore"）可同时绕过校验层与中和层，故纳入同一清单。
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff\u00ad\u2060-\u2064\u180e]")

#: ASCII 控制字符（保留 \t \n \r）：外部文本入投影前剥除，避免不可见控制序列
#: 破坏结构化标记或日志/导出。
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

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
    - Zero-width characters are stripped via _ZERO_WIDTH_RE (shared with
      neutralize_external_text, 见模块顶部对齐说明).
    """
    normalized = unicodedata.normalize("NFKC", text)
    # 经 _ZERO_WIDTH_RE 统一剥除（含 \u2060-\u2064/\u180e），与中和层同一清单（UTILS-B-05）
    return _ZERO_WIDTH_RE.sub("", normalized)


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
        logger.warning("[PromptGuard] %s", warnings[0])
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
        logger.warning(
            "[PromptGuard] Truncating prompt from %s to %s characters.",
            len(prompt),
            MAX_PROMPT_LENGTH,
        )
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
    4. PII detection: masks phone numbers, ID cards, and email addresses.

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
    # PII 检测: 手机号/身份证/邮箱
    text = DataSanitizer._sanitize_pii_text(text)
    if len(text) > max_len:
        text = text[:max_len]
    return text


@dataclass(frozen=True, slots=True)
class UntrustedTextRepresentations:
    """外部/用户文本三表示（I76）：原值、模型可见投影、公开导出投影分列记录。

    - ``raw``：落库原值，仅供精确回读/摘要，**不得**直接作为模型输入或公开导出；
    - ``model_visible``：进入模型上下文的投影（外部文本经中和，用户自述保持原值）；
    - ``public_export``：公开历史/索引/审计/导出的投影（中和 + R9 凭证脱敏）。
    """

    raw: str
    model_visible: str
    public_export: str


def _neutralize_and_sanitize(text: str) -> str:
    """中和（控制字符/零宽/尖括号/PII）后按 R9 做文本级凭证脱敏，不截断。"""

    cleaned = _CONTROL_CHARS_RE.sub("", text)
    neutralized = neutralize_external_text(cleaned, max_len=max(len(cleaned), 1))
    return DataSanitizer.sanitize_error(neutralized)


def project_public_text(text: str) -> str:
    """公开导出投影：中和 + ``DataSanitizer`` 脱敏（R9），保留完整已保存内容。"""

    return _neutralize_and_sanitize(text if isinstance(text, str) else str(text))


def convert_untrusted_text(text: str, *, max_len: int = EXTERNAL_TEXT_MAX_LEN) -> UntrustedTextRepresentations:
    """外部不可信文本 → 三表示的唯一转换点（§2.1；SEC-001 + R9）。

    外部资料、网页、工具/MCP 返回、日志等一律按不可信处理：先剥除控制字符与
    零宽字符、中和尖括号（XML/Markdown 包裹不构成安全边界）、脱敏 PII 与凭证，
    模型可见投影再截断到 ``max_len``；公开导出投影不截断（索引覆盖完整正文）。
    """

    raw = text if isinstance(text, str) else str(text)
    public_export = _neutralize_and_sanitize(raw)
    return UntrustedTextRepresentations(
        raw=raw,
        model_visible=public_export[:max_len],
        public_export=public_export,
    )


def convert_user_text(text: str) -> UntrustedTextRepresentations:
    """用户自述文本 → 三表示（用户即指令来源，模型可见投影保持原值）。

    用户消息是可信指令，不做尖括号/prompt 中和（否则会改写用户意图），但公开
    导出与索引面仍须经同一脱敏点（R9：用户可能粘贴凭据/个人信息）。
    """

    raw = text if isinstance(text, str) else str(text)
    return UntrustedTextRepresentations(raw=raw, model_visible=raw, public_export=project_public_text(raw))
