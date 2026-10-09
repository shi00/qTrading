"""语料文本清洗纯逻辑（N1-4，规格 §4.2）。

按规格 §4.2 执行与源无关的通用清洗（各源解析内核已完成结构解析与字段归一，见
``sina_7x24`` / ``sina_stock`` / ``cninfo``）：

1. 去 HTML 标签与实体（含巨潮 ``<em>`` 高亮）、``【栏目名】``、「新浪财经讯」等模板语；
2. 长度：标题原样；**快讯**（flash）截断至 :data:`FLASH_MAX_CHARS`（120 字）；
3. 过滤纯例行、无信息量公告条目（如「H股公告-翌日披露报表」）；
4. 清洗后为空的条目丢弃。

巨潮公告标题的公司全称前缀 / 「关于……的公告」外壳在解析内核 :func:`cninfo.clean_title`
已处理，本模块不重复剥离。

本模块位于 ``data/`` 层，仅依赖标准库。
"""

import html
import re
from typing import Any

FLASH_MAX_CHARS = 120
TEXT_KIND_FLASH = "flash"
TEXT_KIND_TITLE = "title"

# 丢弃原因（:func:`clean_document` 返回）
CLEAN_REASON_EMPTY = "empty"
CLEAN_REASON_ROUTINE = "routine"

_HTML_TAG_RE = re.compile(r"<[^>]*>")
_WS_RE = re.compile(r"\s+")
# 前导栏目名标记：【财经早报】 / [公告] 等（最多 12 字，避免吞掉正文主体）
_LEADING_BRACKET_RE = re.compile(r"^[【\[](?P<label>[^】\]]{1,12})[】\]]\s*")
# 模板语（来源署名）；逐条精确移除，不做宽泛正则以免误伤正文
_TEMPLATE_PHRASES = ("新浪财经讯", "（新浪财经）", "【新浪财经】", "新浪财经APP")
# 纯例行、无信息量公告标题片段（规格 §4.2.5；保守白名单，命中即剔除）
_ROUTINE_PATTERNS = ("翌日披露报表", "翌日披露報表", "股份发行人的证券变动月报表")


def strip_html_tags(text: object) -> str:
    """去 HTML 标签（含 ``<em>``）、反转义实体、归一空白（含 ``&nbsp;`` / 全角空格）。"""
    if text is None:
        return ""
    stripped = _HTML_TAG_RE.sub("", str(text))
    unescaped = html.unescape(stripped).replace("\u3000", " ")
    return _WS_RE.sub(" ", unescaped).strip()


def strip_template_phrases(text: str) -> str:
    """去模板语：① 前导 ``【栏目名】`` 标记（至多剥离一次）；② 已知来源署名短语。

    NOTE(lazy): 前导 ``【…】`` 一律视作栏目名标记剥离.
    ceiling: 若某源把「【公司名】」置于正文开头，会被一并剥离而丢失主语.
    upgrade: 语料库实测出现大面积「【公司名】」开头的快讯且影响标注，改为按已知栏目名词表匹配.
    """
    result = _LEADING_BRACKET_RE.sub("", text, count=1)
    for phrase in _TEMPLATE_PHRASES:
        result = result.replace(phrase, "")
    return result.strip()


def is_routine_title(text: str | None) -> bool:
    """是否为纯例行、无信息量公告标题（规格 §4.2.5）；None 视为非例行。"""
    return any(pattern in (text or "") for pattern in _ROUTINE_PATTERNS)


def truncate(text: str, max_chars: int = FLASH_MAX_CHARS) -> str:
    """按字符数截断（快讯正文，规格 §4.2.4）；不追加省略号，保留模型输入原貌。"""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars]


def clean_text(text: object, *, kind: str = TEXT_KIND_TITLE, max_chars: int = FLASH_MAX_CHARS) -> str:
    """清洗单个文本：去 HTML → 去模板语 → （快讯时）截断。

    ``kind == TEXT_KIND_FLASH``（快讯）时才截断至 ``max_chars``；标题（``TEXT_KIND_TITLE``）原样保留。
    """
    result = strip_template_phrases(strip_html_tags(text))
    if kind == TEXT_KIND_FLASH:
        return truncate(result, max_chars)
    return result


def clean_document(
    doc: dict[str, Any],
    *,
    text_kind: str,
    drop_routine: bool = False,
    text_field: str = "text",
    max_chars: int = FLASH_MAX_CHARS,
) -> tuple[dict[str, Any] | None, str | None]:
    """清洗一条归一化文档。

    返回 ``(清洗后文档, 丢弃原因)``：保留时原因为 ``None``；丢弃原因见
    :data:`CLEAN_REASON_EMPTY`（清洗后为空）/ :data:`CLEAN_REASON_ROUTINE`（例行条目，
    仅当 ``drop_routine=True``）。原文其余字段原样透传。
    """
    cleaned = clean_text(doc.get(text_field), kind=text_kind, max_chars=max_chars)
    if not cleaned:
        return None, CLEAN_REASON_EMPTY
    if drop_routine and is_routine_title(cleaned):
        return None, CLEAN_REASON_ROUTINE
    return {**doc, text_field: cleaned}, None
