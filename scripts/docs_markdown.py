"""docs_markdown.py — 文档 Markdown 统一解析视图（检视 §5.1，工作包 D3）。

职责边界（检视 §5.1 职责表）：一次解析形成标题/锚点、标准与引用式链接、HTML ID、
代码块/行号映射的不可变视图；缓存限本次调用。**不承担**：编辑文档、读秘密、联网、
业务导入 —— 本模块仅依赖标准库与 docs 组（markdown-it-py / github-slugger），
保证「只装 docs 组」的独立 hook 环境可运行（检视 §6.4 依赖/测试负例、AC15）。

错误处理：本模块为纯静态解析，外部 IO 仅文件读取（load_document_view）。按项目
错误处理标准，文件读取属直接传播边界，异常（FileNotFoundError / UnicodeDecodeError
等）原样向上传播，由调用方（check_docs_consistency.py）负责 classify_error 分级。

解析配置（检视 §5.1）：CommonMark + table/strikethrough；允许读取原文 HTML token
（raw_html_tokens 供原验证者核验 fragment）；不启用 linkify / typographer / 宏 /
远程 include，不新增插件栈。未知或解析器无法语义化的结构不静默采样：要么原样保留
在 raw_html_tokens（「未核」通道），要么异常显式传播（显式失败）。

公共 API（预留供 docs_generated.py / check_docs_consistency.py 复用）：
    parse_text(text) -> DocumentView
    load_document_view(path) -> DocumentView
    create_slugger() -> Slugger        # GitHub 锚点语义
    parse_link_target(raw) -> LinkTarget  # 共享 URL 分类与 path/query/fragment 分离
    slugger_backend_diagnostic() -> str | None  # 环境偏离固定 slugger 依赖时的具名诊断
    SLUGGER_BACKEND                    # 当前锚点实现后端声明
    SLUGGER_BACKEND_DEVIATION          # 偏离类别（None / missing-package / import-failure / unicode-error）

环境偏离（D37）：固定依赖 github-slugger 不可用时本模块回退 CompatGithubSlugger 以保证解析
继续运行，但该回退**不构成固定后端验收**。CLI 入口经 ``slugger_backend_diagnostic()`` 输出
具名诊断（缺包 / 导入失败 / UnicodeEncodeError 分别具名），正常固定后端可用时不输出。
"""

from __future__ import annotations

import re
import urllib.parse
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Protocol

from markdown_it import MarkdownIt
from markdown_it.token import Token

__all__ = [
    "SLUGGER_BACKEND",
    "SLUGGER_BACKEND_DEVIATION",
    "SLUGGER_BACKEND_DEVIATION_DETAIL",
    "CodeBlockInfo",
    "CodeSpanInfo",
    "CompatGithubSlugger",
    "DocumentView",
    "HeadingInfo",
    "HtmlIdInfo",
    "LinkInfo",
    "LinkTarget",
    "ReferenceDefInfo",
    "Slugger",
    "UnresolvedRef",
    "create_slugger",
    "load_document_view",
    "parse_link_target",
    "parse_text",
    "slugger_backend_diagnostic",
]

# 解析模式固定：CommonMark + table/strikethrough（检视 §5.1），模块级单例，
# parse 无共享可变状态（env 由每次调用新建），解析结果不做跨调用缓存。
_PARSER = MarkdownIt("commonmark").enable(["table", "strikethrough"])

# 引用式链接残余形态（仅作用于解析器判定未成链的 text token 内）：
# full 形态 `[text][label]` 与 collapsed 形态 `[text][]`，兼容图片引用 `![alt][label]`。
_REFERENCE_RE = re.compile(r"!?\[([^\[\]]*)\]\[([^\[\]]*)\]")


# ---------------------------------------------------------------------------
# 锚点生成（github-slugger 语义）
# ---------------------------------------------------------------------------


class Slugger(Protocol):
    """GitHub 风格锚点生成器协议（结构化兼容 github_slugger.GithubSlugger）。"""

    def slug(self, value: str, maintain_case: bool = False) -> str:
        """生成唯一 slug；重复标题自动追加序号。"""
        raise NotImplementedError  # Protocol 结构化声明，运行期由具体后端实现


_DEVIATION_DETAIL_LIMIT = 200


def _format_import_error(exc: BaseException) -> str:
    """偏离诊断的异常摘要（类型 + 消息，限长；不携带堆栈）。"""
    detail = f"{type(exc).__name__}: {exc}"
    return detail if len(detail) <= _DEVIATION_DETAIL_LIMIT else detail[:_DEVIATION_DETAIL_LIMIT] + "…"


try:  # 权威来源 pin github-slugger==0.0.3：可用则优先（未来修复版自动切回）
    from github_slugger import GithubSlugger as _GithubSlugger  # type: ignore[reportMissingImports]  # 包在 Python 3.13 环境导入即崩（见 CompatGithubSlugger docstring），未安装时经 except 回退 compat 实现，pyright 环境未必安装该包

    SLUGGER_BACKEND = "github-slugger"
    SLUGGER_BACKEND_DEVIATION: str | None = None
    SLUGGER_BACKEND_DEVIATION_DETAIL = ""
except (ImportError, UnicodeEncodeError) as _slugger_import_error:
    # D37：保留回退以保证解析继续运行，但记录具名偏离类别，供 CLI 显式诊断
    # （兼容模式运行不等同固定后端验收）。三类分别具名：
    # missing-package=固定包本身未安装；import-failure=已安装但导入/依赖初始化失败；
    # unicode-error=CPython ≥3.13 编译包内孤立代理转义 docstring 触发（上游 0.0.3 已知缺陷）。
    _GithubSlugger = None
    SLUGGER_BACKEND = "compat"
    if isinstance(_slugger_import_error, UnicodeEncodeError):
        SLUGGER_BACKEND_DEVIATION = "unicode-error"
    elif isinstance(_slugger_import_error, ModuleNotFoundError) and _slugger_import_error.name == "github_slugger":
        SLUGGER_BACKEND_DEVIATION = "missing-package"
    else:
        SLUGGER_BACKEND_DEVIATION = "import-failure"
    SLUGGER_BACKEND_DEVIATION_DETAIL = _format_import_error(_slugger_import_error)


_DEVIATION_LABELS = {
    "missing-package": "未安装固定依赖 github-slugger",
    "import-failure": "固定依赖 github-slugger 已安装但导入失败",
    "unicode-error": "固定依赖 github-slugger 在 Python ≥3.13 导入触发 UnicodeEncodeError（上游 0.0.3 已知缺陷）",
}


def slugger_backend_diagnostic() -> str | None:
    """环境偏离固定 slugger 依赖时的单行诊断；固定后端可用时返回 None。

    仅供 CLI 输出（stderr），不进入机器可读结果；诊断存在与否只反映依赖/导入环境，
    不证明 compat 与固定后端锚点语义等价，也不得作为固定后端可用性的验收证据。
    """
    if SLUGGER_BACKEND_DEVIATION is None:
        return None
    label = _DEVIATION_LABELS.get(SLUGGER_BACKEND_DEVIATION, "固定依赖 github-slugger 不可用")
    detail = f"（{SLUGGER_BACKEND_DEVIATION_DETAIL}）" if SLUGGER_BACKEND_DEVIATION_DETAIL else ""
    return (
        f"文档锚点后端偏离固定依赖合同：{label}{detail}；"
        f"已回退 CompatGithubSlugger（SLUGGER_BACKEND={SLUGGER_BACKEND}）。"
        "兼容模式运行不等同固定后端验收：请安装 docs 组依赖后重跑，以固定后端验证锚点语义。"
    )


class CompatGithubSlugger:
    """github-slugger 的等价 GitHub slug 语义实现（Python 3.13 环境回退用）。

    背景：github-slugger==0.0.3 的 replace.py docstring 含孤立代理转义（\\uD80A），
    CPython 3.13 编译 docstring 时抛 UnicodeEncodeError，包在 requires-python>=3.13
    环境导入即崩（PyPI 上 0.0.1-0.0.3 无修复版本），故以等价语义实现替代并显式声明
    后端（SLUGGER_BACKEND），不静默冒充包实现。

    语义主干与 github-slugger 一致：小写化；保留字母数字（任意语言，含 CJK）、
    ``-``、``_``；空白转 ``-``；其余字符剔除；重复 slug 追加 ``-1``、``-2`` 序号。
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """遗忘全部历史 slug（与 github_slugger.GithubSlugger.reset 对齐）。"""
        self._occurrences: dict[str, int] = {}

    @staticmethod
    def _base_slug(value: str, maintain_case: bool) -> str:
        if not isinstance(value, str):
            return ""
        # 对齐 github-slugger：toLowerCase -> trim -> 剔除/替换（首尾空白不产生连字符）
        text = value.strip()
        if not maintain_case:
            text = text.lower()
        out: list[str] = []
        for ch in text:
            if ch.isalnum() or ch in "-_":
                out.append(ch)
            elif ch.isspace():
                out.append("-")
            # 其余字符剔除（GitHub 渲染时不进入锚点）
        return "".join(out)

    def slug(self, value: str, maintain_case: bool = False) -> str:
        """生成唯一 slug；重复输入自动追加序号（与 GithubSlugger.slug 行为一致）。"""
        original = self._base_slug(value, maintain_case)
        result = original
        count = self._occurrences.get(original, 0)
        while result in self._occurrences:
            count += 1
            result = f"{original}-{count}"
        self._occurrences[original] = count
        self._occurrences[result] = 0
        return result


def create_slugger() -> Slugger:
    """构造锚点生成器：github-slugger 可用则用包实现，否则回退等价实现。"""
    if _GithubSlugger is not None:
        return _GithubSlugger()
    return CompatGithubSlugger()


# ---------------------------------------------------------------------------
# 不可变视图数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HeadingInfo:
    """标题：可见文本、锚点 slug、级别（1-6）、起始行与标题块末行（1-based）。

    ``line`` 为标题可见文本起始行；``end_line`` 为标题块最后一行（ATX 标题与
    文本同行，Setext 标题含下划线行）。消费者据此切分章节正文，避免行级正则
    再次解析 Markdown 标题。
    """

    text: str
    slug: str
    level: int
    line: int
    end_line: int = 0


@dataclass(frozen=True)
class LinkInfo:
    """已解析链接：可见文本、目标、形态、所在行（1-based）。

    kind 为 "inline"（标准式 ``[t](u)``）或 "reference"（引用式 ``[t][label]``），
    由两轮 parse 对照法精确判定（见 parse_text），无行级正则启发式；HTML
    ``<a href>`` 以 kind="html" 纳入同一链接视图，与 Markdown 链接共用检查。
    """

    text: str
    target: str
    kind: str
    line: int


@dataclass(frozen=True)
class LinkTarget:
    """链接目标的 URL 分类结果：外部 / 本地路径 / query / 锚点（均已解码）。

    - ``external``：带 scheme（http/https/mailto/ftp 等）或协议相对 ``//host``，
      不参与本地文件/锚点检查。
    - ``path``：本地相对/绝对路径的 percent-decode 结果；反斜杠统一为正斜杠
      （Windows 路径分隔符在跨平台检查中同语义）。fragment-only 链接为空串。
    - ``query``：``?`` 之后的原始 query（未解码，仅作存在性区分），无则 None。
    - ``fragment``：``#`` 之后的 percent-decode 锚点，无则 None。
    """

    raw: str
    external: bool
    path: str
    query: str | None
    fragment: str | None

    @property
    def is_fragment_only(self) -> bool:
        """仅同文件锚点（``#anchor``）：无本地路径、非外部。"""
        return not self.external and not self.path and self.fragment is not None


# URL scheme（RFC 3986）：出现在 path/query/fragment 拆分前的路径段中。
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")


def parse_link_target(raw: str) -> LinkTarget:
    """共享 URL 分类：先按原文拆分 fragment/query，再分别 percent-decode。

    拆分顺序保证编码后的 ``%23`` / ``%3F`` 不会被误当成分隔符：先以**字面**
    ``#`` 切出 fragment，再在 fragment 之前以**字面** ``?`` 切出 query，最后
    仅对路径与锚点解码。外部 scheme 在解码前判定（``https://`` 等）。
    """
    text = raw.strip()
    before_fragment, has_fragment, fragment_raw = text.partition("#")
    path_raw, has_query, query_raw = before_fragment.partition("?")
    external = path_raw.startswith("//") or bool(_SCHEME_RE.match(path_raw))
    path = "" if external else urllib.parse.unquote(path_raw).replace("\\", "/")
    return LinkTarget(
        raw=text,
        external=external,
        path=path,
        query=query_raw if has_query else None,
        fragment=urllib.parse.unquote(fragment_raw) if has_fragment else None,
    )


@dataclass(frozen=True)
class UnresolvedRef:
    """引用式死链：未定义引用的 label / 可见文本 / 所在行 / 形态。"""

    label: str
    text: str
    line: int
    form: str  # "full" 或 "collapsed"


@dataclass(frozen=True)
class HtmlIdInfo:
    """HTML 元素 id：标签名、来源 token 形态、所在行（1-based）。

    与标题锚点分开存储（可区分）；anchor_ids() 提供二者并集。
    """

    id: str
    tag: str
    source: str  # "inline" 或 "block"
    line: int


@dataclass(frozen=True)
class CodeBlockInfo:
    """代码块：围栏记号（``` / ~~~，缩进块为空串）、info 串、1-based 闭区间行号。"""

    fence: str
    info: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class ReferenceDefInfo:
    """引用定义：normalize 后 label（大写折叠）、目标 URL、所在行（1-based）。"""

    label: str
    href: str
    line: int


@dataclass(frozen=True)
class CodeSpanInfo:
    """行内代码 span：原文文本、所在行（1-based）。

    行内代码内的假链接/伪标题由本视图承载（token 判定 code_inline，天然排除
    链接与标题语义），供消费者做「代码示例文本不冒正文」的负例判定。
    """

    text: str
    line: int


@dataclass(frozen=True)
class DocumentView:
    """一次解析形成的不可变文档视图（frozen dataclass，全部字段为 tuple）。"""

    source: tuple[str, ...]
    headings: tuple[HeadingInfo, ...]
    links: tuple[LinkInfo, ...]
    unresolved_references: tuple[UnresolvedRef, ...]
    html_ids: tuple[HtmlIdInfo, ...]
    code_blocks: tuple[CodeBlockInfo, ...]
    code_spans: tuple[CodeSpanInfo, ...]
    reference_definitions: tuple[ReferenceDefInfo, ...]
    raw_html_tokens: tuple[tuple[str, int], ...]  # (原 HTML 内容, 所在行)

    def anchor_ids(self) -> frozenset[str]:
        """全部可用锚点 = 标题 slug ∪ HTML id。"""
        return frozenset({h.slug for h in self.headings}) | frozenset(i.id for i in self.html_ids)

    def has_anchor(self, anchor: str) -> bool:
        """锚点存在性显式判定：不存在返回 False（不伪造、不从全文取样代替）。"""
        return anchor in self.anchor_ids()

    def code_line_numbers(self) -> frozenset[int]:
        """代码块覆盖的全部行号（1-based，含围栏标记行与内容行，不含行内代码）。

        供行级消费者（日志表/完成判定正文）排除代码示例，避免围栏内伪标题、
        伪表格冒认正文结构。
        """
        lines: set[int] = set()
        for block in self.code_blocks:
            lines.update(range(block.start_line, block.end_line + 1))
        return frozenset(lines)

    def code_block_text(self, block: CodeBlockInfo) -> str:
        """代码块内容文本（不含围栏标记行；缩进块原样保留内容行）。"""
        if block.fence:
            content = self.source[block.start_line : block.end_line - 1]
        else:
            content = self.source[block.start_line - 1 : block.end_line]
        return "\n".join(content)


# ---------------------------------------------------------------------------
# 解析实现
# ---------------------------------------------------------------------------


class _HtmlIdParser(HTMLParser):
    """标准库 HTMLParser：收集标签 id 属性与 ``<a href>`` 目标（不执行内嵌 HTML 语义）。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[tuple[str, str]] = []
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value for key, value in attrs}
        element_id = values.get("id")
        if element_id:
            self.ids.append((tag, element_id))
        href = values.get("href")
        if tag.lower() == "a" and href:
            self.hrefs.append(href)


def _normalize_label(label: str) -> str:
    """CommonMark 引用 label normalize：折叠空白 + 大小写折叠（对齐 env 键）。"""
    return re.sub(r"\s+", " ", label).strip().upper()


def _token_line(token: object) -> int:
    """token.map 为 [start, end) 0-based 行区间，返回 1-based 起始行。"""
    mapping = getattr(token, "map", None)
    if not mapping:
        return 0
    return mapping[0] + 1


def _iter_links(tokens: list[Token]) -> list[tuple[int, str, str]]:
    """按出现顺序收集已解析链接 (line, text, href)；image 独立 token 不计入链接。"""
    links: list[tuple[int, str, str]] = []
    for token in tokens:
        if token.type != "inline":
            continue
        children = token.children or []
        index = 0
        while index < len(children):
            child = children[index]
            if child.type == "link_open":
                attrs = dict(child.attrs) if child.attrs else {}
                href = str(attrs.get("href", ""))
                parts: list[str] = []
                cursor = index + 1
                while cursor < len(children) and children[cursor].type != "link_close":
                    if children[cursor].type in ("text", "code_inline"):
                        parts.append(children[cursor].content)
                    cursor += 1
                links.append((_token_line(token), "".join(parts), href))
                index = cursor + 1
            else:
                index += 1
    return links


def _collect_headings(tokens: list[Token], slugger: Slugger) -> tuple[HeadingInfo, ...]:
    """标题：可见文本取 children 中 text/code_inline（跳过 HTML 等内联标记）。"""
    headings: list[HeadingInfo] = []
    pending_level: int | None = None
    pending_end = 0
    for token in tokens:
        if token.type == "heading_open" and token.tag.startswith("h"):
            pending_level = int(token.tag[1])
            pending_end = token.map[1] if token.map else 0
        elif token.type == "inline" and pending_level is not None:
            parts = [child.content for child in (token.children or []) if child.type in ("text", "code_inline")]
            text = "".join(parts)
            headings.append(
                HeadingInfo(
                    text=text,
                    slug=slugger.slug(text),
                    level=pending_level,
                    line=_token_line(token),
                    end_line=pending_end,
                )
            )
            pending_level = None
    return tuple(headings)


def _parse_html(content: str, source: str, line: int) -> tuple[list[HtmlIdInfo], list[str]]:
    parser = _HtmlIdParser()
    parser.feed(content)
    parser.close()
    ids = [HtmlIdInfo(id=value, tag=tag, source=source, line=line) for tag, value in parser.ids]
    return ids, parser.hrefs


def _collect_html(tokens: list[Token]) -> tuple[list[HtmlIdInfo], list[tuple[str, int]], list[LinkInfo]]:
    """从 html_block / html_inline token 原文提取 id、保留未核 HTML，并收集 ``<a href>``。

    html_inline 位于 inline token 的 children 中，须下钻一层收集；
    html_block 在顶层 token（注意 CommonMark type 6 块仅由空行结束）。
    ``<a href>`` 以 kind="html" 加入链接视图，与 Markdown 链接走同一死链/锚点检查，
    不再静默放过 HTML 目标。
    """
    ids: list[HtmlIdInfo] = []
    raw: list[tuple[str, int]] = []
    links: list[LinkInfo] = []
    for token in tokens:
        if token.type == "html_block":
            line = _token_line(token)
            raw.append((token.content, line))
            parsed_ids, hrefs = _parse_html(token.content, "block", line)
            ids.extend(parsed_ids)
            links.extend(LinkInfo(text="", target=href, kind="html", line=line) for href in hrefs)
        elif token.type == "inline":
            line = _token_line(token)
            for child in token.children or []:
                if child.type == "html_inline":
                    raw.append((child.content, line))
                    parsed_ids, hrefs = _parse_html(child.content, "inline", line)
                    ids.extend(parsed_ids)
                    links.extend(LinkInfo(text="", target=href, kind="html", line=line) for href in hrefs)
    return ids, raw, links


def _collect_code_spans(tokens: list[Token]) -> tuple[CodeSpanInfo, ...]:
    """行内代码 span（code_inline）：文本与所在行（1-based，段落起始行粒度）。"""
    spans: list[CodeSpanInfo] = []
    for token in tokens:
        if token.type != "inline":
            continue
        line = _token_line(token)
        for child in token.children or []:
            if child.type == "code_inline":
                spans.append(CodeSpanInfo(text=child.content, line=line))
    return tuple(spans)


def _collect_code_blocks(tokens: list[Token]) -> tuple[CodeBlockInfo, ...]:
    """代码块：围栏（fence，含 markup ```/~~~）与缩进块（code_block）。"""
    blocks: list[CodeBlockInfo] = []
    for token in tokens:
        if token.type == "fence" and token.map:
            mapping = token.map
            blocks.append(
                CodeBlockInfo(
                    fence=token.markup,
                    info=token.info.strip(),
                    start_line=mapping[0] + 1,
                    end_line=mapping[1],
                )
            )
        elif token.type == "code_block" and token.map:
            mapping = token.map
            blocks.append(
                CodeBlockInfo(
                    fence="",
                    info="",
                    start_line=mapping[0] + 1,
                    end_line=mapping[1],
                )
            )
    return tuple(blocks)


def _strip_definition_lines(source_lines: tuple[str, ...], env: dict) -> tuple[str, tuple[int, ...]]:
    """按 env.references 的 map 行区间删除引用定义行，返回（新文本, 保留原行号）。"""
    removed: set[int] = set()
    for definition in env.get("references", {}).values():
        span = definition.get("map")
        if span:
            removed.update(range(span[0], span[1]))
    kept: list[int] = []
    lines: list[str] = []
    for index, line in enumerate(source_lines):
        if index not in removed:
            kept.append(index + 1)
            lines.append(line)
    return "\n".join(lines) + ("\n" if lines else ""), tuple(kept)


# 语法角色占位符：转义方括号 / 反引号与行内代码在「原始源语法流」中以 NUL 表示，
# 保证其不再参与引用形态匹配（NUL 不可能是文档正文的方括号字符）。
_INERT_SYNTAX = "\x00"


def _find_closing_backticks(raw: str, run: str, start: int) -> int | None:
    """定位与 ``run`` 等长的闭合反引号串（排除更长反引号串的一部分）。"""
    index = raw.find(run, start)
    while index != -1:
        after = index + len(run)
        before_ok = index == 0 or raw[index - 1] != "`"
        after_ok = after >= len(raw) or raw[after] != "`"
        if before_ok and after_ok:
            return index
        index = raw.find(run, index + 1)
    return None


def _syntax_stream(raw: str) -> str:
    """按原始 inline 源（未反转义）重建引用语法判定流。

    text token 的 ``content`` 已丢失转义来源（``\\[ok\\]`` 与 ``[ok]`` 同形），
    因此不能从显示文本倒猜语法。这里只做词法级还原：反斜杠转义按其字面字符
    还原，但转义方括号 / 反引号与行内代码以 ``_INERT_SYNTAX`` 占位，使其不再
    构成引用形态；不做第二次 Markdown 解析。
    """
    out: list[str] = []
    index = 0
    length = len(raw)
    while index < length:
        char = raw[index]
        if char == "\\" and index + 1 < length:
            escaped = raw[index + 1]
            out.append(_INERT_SYNTAX if escaped in "[]`\\" else escaped)
            index += 2
            continue
        if char == "`":
            run_end = index
            while run_end < length and raw[run_end] == "`":
                run_end += 1
            run = raw[index:run_end]
            close = _find_closing_backticks(raw, run, run_end)
            if close is not None:
                out.append(_INERT_SYNTAX)
                index = close + len(run)
                continue
            out.append(run)
            index = run_end
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _source_reference_forms(raw: str) -> Counter[tuple[str, str]]:
    """原始 inline 源中真实语法角色的 full/collapsed 引用形态计数（含 text/label 原文）。"""
    forms: Counter[tuple[str, str]] = Counter()
    for match in _REFERENCE_RE.finditer(_syntax_stream(raw)):
        forms[(match.group(1), match.group(2))] += 1
    return forms


def _collect_unresolved(tokens: list[Token], defined_labels: set[str]) -> tuple[UnresolvedRef, ...]:
    """引用式死链：解析器判定未成链的 text token 中 full/collapsed 引用形态。

    shortcut 形态 ``[label]`` 不检（CommonMark 语义为纯文本，检之会对普通方括号
    文本误报）。行内代码/围栏内的假链接天然不会进入本路径（markdown-it 已将其
    判定为 code_inline / fence，见检视 §6.4 不误报负例）。

    D30：显示文本已反转义，无法区分 ``\\[ok\\]\\[undefined\\]``（转义文字）与真实
    未定义引用。因此候选仅在「原始 inline 源语法流」中同形出现时才成立：
    转义方括号、行内代码被占位，不会贡献语法形态；格式化文本（如
    ``[**x**][undefined]``）仍因强调拆成多个 text token 而不产生候选，避免扩大
    正则去拒绝 CommonMark 视为普通文字的输入。
    """
    unresolved: list[UnresolvedRef] = []
    for token in tokens:
        if token.type != "inline":
            continue
        line = _token_line(token)
        source_forms = _source_reference_forms(token.content or "")
        for child in token.children or []:
            if child.type != "text":
                continue
            for match in _REFERENCE_RE.finditer(child.content):
                text_part, label_part = match.group(1), match.group(2)
                form = "collapsed" if label_part == "" else "full"
                label = text_part if form == "collapsed" else label_part
                key = (text_part, label_part)
                if source_forms[key] <= 0:
                    # 显示文本候选来自转义 / 行内代码语境，非真实引用语法
                    continue
                source_forms[key] -= 1
                if _normalize_label(label) not in defined_labels:
                    unresolved.append(UnresolvedRef(label=label, text=text_part, line=line, form=form))
    return tuple(unresolved)


def parse_text(text: str) -> DocumentView:
    """解析 Markdown 文本，形成不可变文档视图（缓存限本次调用）。"""
    source_lines = tuple(text.splitlines())
    env: dict = {}
    tokens = _PARSER.parse(text, env)

    slugger = create_slugger()
    headings = _collect_headings(tokens, slugger)
    html_ids, raw_html, html_links = _collect_html(tokens)
    code_blocks = _collect_code_blocks(tokens)
    code_spans = _collect_code_spans(tokens)
    defined_labels = {
        str(label)
        for label in env.get("references", {})  # 键已为大写折叠形态
    }
    unresolved = _collect_unresolved(tokens, defined_labels)

    definitions = tuple(
        ReferenceDefInfo(
            label=str(label),
            href=str(definition.get("href", "")),
            line=int(definition["map"][0]) + 1,
        )
        for label, definition in sorted(env.get("references", {}).items())
        if definition.get("map")
    )

    # kind 判定（两轮 parse 对照法，无行级正则启发式）：
    # 第一轮收集全部已解析链接；第二轮删除引用定义行后重新 parse ——
    # 依赖定义的引用式链接会退化为 text，而标准式内联链接不变。
    # 二轮仍出现的链接为 inline，消失者为 reference（按多重出现计数对齐）。
    round_one = _iter_links(tokens)
    stripped, kept_lines = _strip_definition_lines(source_lines, env)
    round_two_env: dict = {}
    round_two = _PARSER.parse(stripped, round_two_env)
    round_two_mapped = Counter(
        (kept_lines[line - 1], link_text, link_href)
        for line, link_text, link_href in _iter_links(round_two)
        if 0 < line <= len(kept_lines)
    )
    counts = Counter(round_one)
    links: list[LinkInfo] = []
    for line, link_text, link_href in round_one:
        kind = (
            "inline"
            if counts[(line, link_text, link_href)] <= round_two_mapped[(line, link_text, link_href)]
            else "reference"
        )
        links.append(LinkInfo(text=link_text, target=link_href, kind=kind, line=line))
    links.extend(html_links)

    return DocumentView(
        source=source_lines,
        headings=headings,
        links=tuple(links),
        unresolved_references=unresolved,
        html_ids=tuple(html_ids),
        code_blocks=code_blocks,
        code_spans=code_spans,
        reference_definitions=definitions,
        raw_html_tokens=tuple(raw_html),
    )


def load_document_view(path: str | Path) -> DocumentView:
    """读取文档并解析为不可变视图；文件读取异常按直接传播边界原样上抛。"""
    content = Path(path).read_text(encoding="utf-8")
    return parse_text(content)
