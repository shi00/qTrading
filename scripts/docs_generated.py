"""docs_generated.py — 受控生成器（检视 §5.2 生成行为 / §5.2.1 并发保护，工作包 D4）。

职责边界（检视 §5.1 职责表）：读取 §4.1 唯一作者源，按**封闭目标清单**渲染派生块；
检查（--check --scope document 的生成输出一致性）与更新（--update）共用本实现。
**不承担**：猜测规则语义、改手写正文、自动升级依赖、联网、读秘密、业务导入 ——
本模块仅依赖标准库与 docs 组（pyyaml），保证「只装 docs 组」的独立 hook 环境可运行。

核心不变量（检视 §5.2 / §6.4「生成」负例）：
1. 生成目标是封闭清单（显式注册表常量），不是扫描到标记就能写；目标不在清单即拒绝，
   未登记目标即使含合法块标记也不被任何路径触碰。
2. Markdown 使用带唯一块身份的成对注释（``<!-- generated:<id> -->`` /
   ``<!-- /generated -->``，结束标记兼容带 id 形态）；YAML 使用对应成对
   ``# generated:<id>`` / ``# /generated:<id>``。缺块 / 重复块 / 嵌套块 / 孤儿结束 /
   结束标记身份错配 / 非法 id 一律显式拒绝。
3. 路径越界（含 ``..`` 逃逸、绝对路径、符号链接解析后越界）显式拒绝。
4. 渲染输入校验：作者源缺字段 / 未知字段 / 重复身份 / 非法 section 不得静默取默认值。
5. 更新编排覆盖 §5.2.1 全窗口：读快照 → 取写入保护 → 保护内核对 → 渲染 → 预校验 →
   逐文件提交核对 + 替换 → 更新后 all 检查（同一保护边界内）→ 释放保护。
   检测到冲突时不覆盖原位、保留用户新稿、如实列出已写/未写；不承诺跨文件事务。

写入保护的平台边界（§5.2.1-3）：Windows 经独占打开 + ``msvcrt.locking`` 内核级字节
范围锁（任何进程对锁定字节的写入失败、替换因缺 FILE_SHARE_DELETE 被拒）实际覆盖外部
写者；POSIX ``flock`` 为协作锁，无法证明覆盖外部编辑器，故**拒绝原位更新**（退出 1），
不把共享工作区默认为独占环境。后端声明见 ``PLATFORM_LOCK_BACKEND``，不静默伪装。

错误处理：本模块为纯静态工具，文件读取/写入属直接传播边界外的显式拒绝域——所有
合同违规以 ``DocsGeneratedError`` 子类显式抛出（含 ``FileNotFoundError`` 的转译），
由调用方（check_docs_consistency.py）负责聚合与退出码映射。
"""

from __future__ import annotations

import hashlib
import os
import posixpath
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import docs_markdown

__all__ = [
    "BUILTIN_REGISTRY",
    "PLATFORM_LOCK_BACKEND",
    "BlockSpan",
    "BlockSpec",
    "DocsGeneratedError",
    "DuplicateBlockError",
    "EndMismatchError",
    "InvalidBlockIdError",
    "InvalidSpecError",
    "MissingBlockError",
    "NestedBlockError",
    "OrphanEndError",
    "PathEscapeError",
    "ProtectionDeniedError",
    "ProtectionUnsupportedError",
    "SnapshotDriftError",
    "SourceDuplicateIdentityError",
    "SourceFieldError",
    "SourceFormatError",
    "SourceMissingError",
    "SourceSchema",
    "SourceSectionError",
    "SourceUnknownFieldError",
    "TargetMissingError",
    "TargetNotRegisteredError",
    "UnclosedBlockError",
    "UpdateResult",
    "WriteGuard",
    "load_validated_source",
    "render_block_lines",
    "replace_block",
    "resolve_within",
    "run_update",
    "scan_blocks",
    "snapshot_file",
    "verify_snapshot",
]

# ---------------------------------------------------------------------------
# 异常族：全部合同违规显式拒绝（不静默取默认值）
# ---------------------------------------------------------------------------


class DocsGeneratedError(Exception):
    """受控生成合同违规基类（调用方映射为退出码 1）。"""


class PathEscapeError(DocsGeneratedError):
    """路径（含符号链接解析后）越出工作根。"""


class TargetNotRegisteredError(DocsGeneratedError):
    """请求的目标不在封闭清单内。"""


class TargetMissingError(DocsGeneratedError):
    """登记目标文件不存在（目标须已存在并在固定清单内）。"""


class InvalidSpecError(DocsGeneratedError):
    """注册表条目或格式参数非法。"""


class BlockError(DocsGeneratedError):
    """块协议违规基类。"""


class MissingBlockError(BlockError):
    """登记块缺失（或块未闭合时的目标形态缺结束标记）。"""


class DuplicateBlockError(BlockError):
    """同一块身份出现两次。"""


class NestedBlockError(BlockError):
    """块内出现另一开始标记（嵌套/重叠）。"""


class OrphanEndError(BlockError):
    """无对应开始标记的孤儿结束标记。"""


class UnclosedBlockError(BlockError):
    """块开始后到文件结束未遇结束标记。"""


class InvalidBlockIdError(BlockError):
    """块 id 不符合 ``[a-z0-9][a-z0-9-]*``。"""


class EndMismatchError(BlockError):
    """带身份的结束标记与开始标记身份不一致。"""


class SourceError(DocsGeneratedError):
    """作者源合同违规基类。"""


class SourceMissingError(SourceError):
    """作者源文件缺失。"""


class SourceFormatError(SourceError):
    """作者源格式无法解析（YAML 解析失败 / 顶层形态不符 / 受控块解析失败）。"""


class SourceFieldError(SourceError):
    """作者源条目缺必填字段（含渲染函数的条件性必需字段）。"""


class SourceUnknownFieldError(SourceError):
    """作者源条目含 schema 未登记的未知字段。"""


class SourceDuplicateIdentityError(SourceError):
    """作者源条目身份字段重复。"""


class SourceSectionError(SourceError):
    """section 字段取值不在允许集合。"""


class ProtectionError(DocsGeneratedError):
    """写入保护合同违规基类。"""


class ProtectionDeniedError(ProtectionError):
    """无法取得写入保护（被他者占用 / 文件不可打开）。"""


class ProtectionUnsupportedError(ProtectionError):
    """当前平台无法证明写入保护覆盖外部写者（POSIX 协作锁）→ 拒绝原位更新。"""


class SnapshotDriftError(DocsGeneratedError):
    """保护窗口内快照（字节 hash / 文件身份 / 解析路径）发生变化。"""


# ---------------------------------------------------------------------------
# 块协议：唯一块身份 + 成对注释
# ---------------------------------------------------------------------------

# 块 id 封闭语法：小写字母/数字开头，仅允许小写字母、数字与连字符
_BLOCK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# Markdown 成对注释：开始带唯一 id；结束兼容仓库既有无 id 形态与显式 id 形态
_MD_START_RE = re.compile(r"^<!--\s+generated:([A-Za-z0-9_-]*)\s+-->\s*$")
_MD_END_RE = re.compile(r"^<!--\s+/generated(?::([A-Za-z0-9_-]*))?\s+-->\s*$")
# YAML 成对注释：开始/结束均须带 id（无既有形态包袱，自定即严格）。
# 允许前置缩进：受控块常位于映射内部（如 .pre-commit-config.yaml 的 hook 片段）。
_YAML_START_RE = re.compile(r"^\s*#\s+generated:([A-Za-z0-9_-]*)\s*$")
_YAML_END_RE = re.compile(r"^\s*#\s+/generated:([A-Za-z0-9_-]*)\s*$")

_BLOCK_FORMATS = ("markdown", "yaml")


@dataclass(frozen=True)
class BlockSpan:
    """扫描到的生成块：块身份、首/末标记 1-based 行号、内容行、原标记行文本。

    保留原标记行文本用于替换重组（不重写形态，避免对既有目标产生意外 diff）。
    """

    block_id: str
    start_line: int  # 1-based，开始标记所在行
    end_line: int  # 1-based，结束标记所在行
    content_lines: tuple[str, ...]
    start_tag_line: str
    end_tag_line: str


def _scan_markdown(text: str) -> dict[str, BlockSpan]:
    """Markdown 成对注释块扫描（栈式配对；嵌套/孤儿/重复/未闭合显式拒绝）。

    代码块（两类围栏 + 长围栏 + 缩进块）内的生成标记只是示例文本，不构成现行块：
    行号边界统一经 docs_markdown 解析视图判定，避免围栏中的标记被当作可修改块。
    """
    code_lines = docs_markdown.parse_text(text).code_line_numbers()
    blocks: dict[str, BlockSpan] = {}
    open_id: str | None = None
    open_start = 0
    open_tag = ""
    content: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if lineno in code_lines:
            continue
        start_match = _MD_START_RE.match(raw)
        end_match = _MD_END_RE.match(raw)
        if open_id is None:
            if start_match:
                block_id = start_match.group(1)
                if not _BLOCK_ID_RE.match(block_id):
                    raise InvalidBlockIdError(f"第 {lineno} 行块 id 非法: {block_id!r}")
                open_id, open_start, open_tag, content = block_id, lineno, raw, []
            elif end_match:
                raise OrphanEndError(f"第 {lineno} 行存在无开始标记的孤儿结束标记")
        else:
            if start_match:
                raise NestedBlockError(f"块 {open_id!r}（第 {open_start} 行起）内嵌套新的块开始标记（第 {lineno} 行）")
            if end_match:
                closed_id = end_match.group(1)
                if closed_id is not None and closed_id != open_id:
                    raise EndMismatchError(
                        f"块 {open_id!r}（第 {open_start} 行起）的结束标记身份不匹配: {closed_id!r}（第 {lineno} 行）"
                    )
                if open_id in blocks:
                    raise DuplicateBlockError(
                        f"块身份重复: {open_id!r} 已在第 {blocks[open_id].start_line} 行出现，又在第 {open_start} 行出现"
                    )
                blocks[open_id] = BlockSpan(
                    block_id=open_id,
                    start_line=open_start,
                    end_line=lineno,
                    content_lines=tuple(content),
                    start_tag_line=open_tag,
                    end_tag_line=raw,
                )
                open_id = None
                continue
            content.append(raw)
    if open_id is not None:
        raise UnclosedBlockError(f"块 {open_id!r}（第 {open_start} 行起）缺少结束标记")
    return blocks


def _scan_yaml(text: str) -> dict[str, BlockSpan]:
    """YAML 成对注释块扫描（结束必须带 id 且与开始一致；未闭合显式拒绝）。"""
    blocks: dict[str, BlockSpan] = {}
    open_id: str | None = None
    open_start = 0
    open_tag = ""
    content: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        start_match = _YAML_START_RE.match(raw)
        end_match = _YAML_END_RE.match(raw)
        if open_id is None:
            if start_match:
                block_id = start_match.group(1)
                if not _BLOCK_ID_RE.match(block_id):
                    raise InvalidBlockIdError(f"第 {lineno} 行块 id 非法: {block_id!r}")
                open_id, open_start, open_tag, content = block_id, lineno, raw, []
            elif end_match:
                raise OrphanEndError(f"第 {lineno} 行存在无开始标记的孤儿结束标记")
        else:
            if start_match:
                raise NestedBlockError(f"块 {open_id!r}（第 {open_start} 行起）内嵌套新的块开始标记（第 {lineno} 行）")
            if end_match:
                if end_match.group(1) != open_id:
                    raise EndMismatchError(
                        f"块 {open_id!r}（第 {open_start} 行起）的结束标记身份不匹配: {end_match.group(1)!r}（第 {lineno} 行）"
                    )
                if open_id in blocks:
                    raise DuplicateBlockError(
                        f"块身份重复: {open_id!r} 已在第 {blocks[open_id].start_line} 行出现，又在第 {open_start} 行出现"
                    )
                blocks[open_id] = BlockSpan(
                    block_id=open_id,
                    start_line=open_start,
                    end_line=lineno,
                    content_lines=tuple(content),
                    start_tag_line=open_tag,
                    end_tag_line=raw,
                )
                open_id = None
                continue
            content.append(raw)
    if open_id is not None:
        raise UnclosedBlockError(f"块 {open_id!r}（第 {open_start} 行起）缺少结束标记")
    return blocks


def scan_blocks(text: str, fmt: str) -> dict[str, BlockSpan]:
    """按格式扫描全部生成块，返回 块id → 区间映射；结构违规显式抛出。"""
    if fmt == "markdown":
        return _scan_markdown(text)
    if fmt == "yaml":
        return _scan_yaml(text)
    raise InvalidSpecError(f"未知块格式: {fmt!r}（允许 {' / '.join(_BLOCK_FORMATS)}）")


def _join_lines(text: str, lines: list[str]) -> str:
    """按原文主流行尾重组文本并保留尾换行标志（CRLF/LF 不被规范化改写）。"""
    newline = "\r\n" if "\r\n" in text else "\n"
    out = newline.join(lines)
    if text.endswith("\n"):
        out += newline
    return out


def replace_block(text: str, fmt: str, block_id: str, new_lines: Sequence[str]) -> str:
    """将目标文本中登记块的内容替换为渲染输出；块边界标记与块外正文原样保留。

    同一文本多块替换时按起始行号**降序**应用，保证先应用的块不使后应用块的行号失效。
    """
    blocks = scan_blocks(text, fmt)
    if block_id not in blocks:
        raise MissingBlockError(f"文本中缺少生成块 {block_id!r}")
    span = blocks[block_id]
    lines = text.splitlines()
    replacement = [span.start_tag_line, *list(new_lines), span.end_tag_line]
    rebuilt = [*lines[: span.start_line - 1], *replacement, *lines[span.end_line :]]
    return _join_lines(text, rebuilt)


# ---------------------------------------------------------------------------
# 路径安全：越界（含符号链接解析）显式拒绝
# ---------------------------------------------------------------------------


def resolve_within(root: Path, rel: str) -> Path:
    """解析相对路径并断言落在工作根内（``..`` 逃逸 / 绝对路径 / symlink 越界均拒绝）。"""
    root_resolved = Path(root).resolve()
    candidate = (Path(root) / rel).resolve()
    if not candidate.is_relative_to(root_resolved):
        raise PathEscapeError(f"路径解析后越界: {rel!r} → {candidate}")
    return candidate


# ---------------------------------------------------------------------------
# 作者源 schema 与渲染输入校验（不静默取默认值）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceSchema:
    """作者源声明：路径、格式与字段/身份/section 校验规则。

    fmt="yaml" 须提供 ``item_key``（顶层取列表的键）；fmt="markdown-block" 须提供
    ``author_block_id``（作者源自身为受控块），``author_payload`` 决定传给渲染函数的
    内容："block"=受控块内容行（默认），"full"=完整文件文本（供复用既有的、自行
    定位受控块的正本解析器，如 CONTRIBUTING 最小验证命令解析器）。
    """

    path: str
    fmt: str
    item_key: str | None = None
    required_fields: frozenset[str] = frozenset()
    known_fields: frozenset[str] = frozenset()
    identity_field: str | None = None
    section_field: str | None = None
    allowed_sections: frozenset[str] = frozenset()
    author_block_id: str | None = None
    author_payload: str = "block"

    def __post_init__(self) -> None:
        if self.fmt == "yaml" and not self.item_key:
            raise InvalidSpecError(f"作者源 {self.path!r}: fmt=yaml 必须提供 item_key")
        if self.fmt == "markdown-block" and not self.author_block_id:
            raise InvalidSpecError(f"作者源 {self.path!r}: fmt=markdown-block 必须提供 author_block_id")
        if self.author_payload not in ("block", "full"):
            raise InvalidSpecError(f"作者源 {self.path!r}: 未知 author_payload {self.author_payload!r}")
        if self.fmt not in ("yaml", "markdown-block", "text"):
            raise InvalidSpecError(f"作者源 {self.path!r}: 未知格式 {self.fmt!r}")


def _validate_source_items(schema: SourceSchema, items: list[object]) -> list[dict[str, object]]:
    """逐条目校验必填/未知字段/身份重复/section 取值（任一违规显式拒绝）。"""
    seen: set[object] = set()
    validated: list[dict[str, object]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise SourceFormatError(f"作者源 {schema.path} 第 {index} 条不是映射: {type(item).__name__}")
        where = f"作者源 {schema.path} 条目 {item.get(schema.identity_field, f'#{index}')!r}"
        missing = schema.required_fields - set(item)
        if missing:
            raise SourceFieldError(f"{where} 缺必填字段 {sorted(missing)}（不得静默取默认值）")
        unknown = set(item) - schema.known_fields
        if unknown:
            raise SourceUnknownFieldError(f"{where} 含未知字段 {sorted(unknown)}（schema 未登记即拒绝）")
        if schema.section_field is not None and schema.allowed_sections:
            section = item.get(schema.section_field)
            if section not in schema.allowed_sections:
                raise SourceSectionError(f"{where} 的 {schema.section_field} 取值非法: {section!r}")
        if schema.identity_field is not None:
            identity = item[schema.identity_field]
            if identity in seen:
                raise SourceDuplicateIdentityError(f"{where} 身份重复: {identity!r}")
            seen.add(identity)
        validated.append(item)
    return validated


def load_validated_source(root: Path, schema: SourceSchema, *, text: str | None = None) -> object:
    """加载并校验作者源；返回渲染输入（yaml 为条目列表 / 块或文本为字符串）。

    text 提供时直接使用调用方传入的文本（写入保护窗口内经快照读取，避免新开句柄
    被独占打开拒绝）；省略时自行读取文件并做存在性/越界/解码校验。
    """
    if text is None:
        resolve_within(root, schema.path)
        path = Path(root) / schema.path
        if not path.is_file():
            raise SourceMissingError(f"作者源缺失: {schema.path}")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise SourceFormatError(f"作者源 {schema.path} 不是有效 UTF-8: {exc}") from exc
    if schema.fmt == "yaml":
        import yaml  # 函数体内延迟 import：与 check_docs_consistency 渲染器风格一致（docs 组依赖）

        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise SourceFormatError(f"作者源 {schema.path} YAML 解析失败: {exc}") from exc
        if not isinstance(data, dict):
            raise SourceFormatError(f"作者源 {schema.path} 顶层须为映射，实际 {type(data).__name__}")
        assert schema.item_key is not None  # __post_init__ 已保证
        items = data.get(schema.item_key)
        if not isinstance(items, list):
            raise SourceFormatError(f"作者源 {schema.path} 缺 {schema.item_key!r} 列表（不得静默当空集合）")
        return _validate_source_items(schema, items)
    if schema.fmt == "markdown-block":
        assert schema.author_block_id is not None  # __post_init__ 已保证
        blocks = scan_blocks(text, "markdown")
        if schema.author_block_id not in blocks:
            raise MissingBlockError(f"作者源 {schema.path} 缺受控块 {schema.author_block_id!r}")
        if schema.author_payload == "full":
            return text  # 正本解析器自行定位受控块并做同节 fail-closed 校验
        content = blocks[schema.author_block_id].content_lines
        return "\n".join(content) + ("\n" if content else "")
    return text  # fmt == "text"


# ---------------------------------------------------------------------------
# 注册表：封闭目标清单（目标 → 块 id → 作者源与渲染函数）
# ---------------------------------------------------------------------------

RenderFn = Callable[[dict[str, object], Path], list[str]]


@dataclass(frozen=True)
class BlockSpec:
    """注册表条目：块身份、目标、作者源 schema 列表与渲染函数。

    active=False 表示「尚无作者源接线的目标槽位」：登记占位但不启用渲染，
    不伪造生成（检视 §5.2 生成目标封闭清单）。
    """

    block_id: str
    target: str
    sources: tuple[SourceSchema, ...]
    render: RenderFn | None
    block_format: str = "markdown"
    active: bool = True

    def __post_init__(self) -> None:
        if not _BLOCK_ID_RE.match(self.block_id):
            raise InvalidSpecError(f"块 id 非法: {self.block_id!r}")
        if self.block_format not in _BLOCK_FORMATS:
            raise InvalidSpecError(f"块 {self.block_id!r}: 未知块格式 {self.block_format!r}")
        if self.active and self.render is None:
            raise InvalidSpecError(f"激活块 {self.block_id!r} 必须提供渲染函数（不得伪造生成）")


# ---------------------------------------------------------------------------
# 内置作者源 schema 与渲染函数（真实块；渲染语义与既有检查渲染器一致）
# ---------------------------------------------------------------------------

# redlines.yml 全部登记字段（渲染输入的白名单；schema 外字段即未知字段拒绝）
_REDLINE_KNOWN_FIELDS = frozenset(
    {
        "id",
        "title",
        "description",
        "enforcement",
        "automation_coverage",
        "human_review_required",
        "rule_type",
        "checks",
        "self_check",
    }
)
_RULE_TYPE_SECTIONS = frozenset({"INVARIANT", "DEFAULT", "NEW_CODE", "MIGRATION_TARGET", "WORKFLOW", "EXCEPTIONABLE"})
_REDLINE_REQUIRED_FIELDS = frozenset({"id", "title", "rule_type"})

_REDLINES_SCHEMA = SourceSchema(
    path="docs/governance/redlines.yml",
    fmt="yaml",
    item_key="redlines",
    required_fields=_REDLINE_REQUIRED_FIELDS,
    known_fields=_REDLINE_KNOWN_FIELDS,
    identity_field="id",
    section_field="rule_type",
    allowed_sections=_RULE_TYPE_SECTIONS,
)
_CONTRIBUTING_SCHEMA = SourceSchema(
    path="CONTRIBUTING.md",
    fmt="markdown-block",
    author_block_id="min-verify-commands",
    author_payload="full",  # 传完整文本：既有正本解析器自行定位受控块（保持单一解析源）
)
# canonical-topics.yml 全部登记字段（任务路由视图的渲染输入白名单）
_CANONICAL_TOPICS_SCHEMA = SourceSchema(
    path="docs/governance/canonical-topics.yml",
    fmt="yaml",
    item_key="topics",
    required_fields=frozenset({"id", "title", "canonical"}),
    known_fields=frozenset({"id", "title", "canonical", "section", "workflow", "note"}),
    identity_field="id",
)
# 文件级目录索引的标题作者源（fmt=text 经快照字节渲染）：同一更新窗口内这些文件同时是
# 其它激活块的受保护目标/作者源——Windows 独占保护期间新开句柄读取被拒（共享冲突），
# 若仍走磁盘直读会把标题渲染成占位符，与保护窗口外的 --check 渲染产生永久漂移；
# 注册为 text 作者源后内容取自保护内核对过的快照字节，两侧渲染必然一致。
_DOCS_README_TEXT_SCHEMA = SourceSchema(path="docs/README.md", fmt="text")
_DOCS_GOVERNANCE_README_TEXT_SCHEMA = SourceSchema(path="docs/governance/README.md", fmt="text")
_REDLINES_TEXT_SCHEMA = SourceSchema(path="docs/governance/redlines.yml", fmt="text")
_CANONICAL_TOPICS_TEXT_SCHEMA = SourceSchema(path="docs/governance/canonical-topics.yml", fmt="text")
# D23：pre-commit hook 隔离环境的 docs 解析依赖作者源 = pyproject [dependency-groups].docs。
# 该组同时供 uv 编译 requirements-docs.txt 与 tests/docs 使用；hook 片段只派生运行脚本所需的
# 解析依赖，排除纯测试运行器（pytest 及其插件）。
_PYPROJECT_DOCS_SCHEMA = SourceSchema(path="pyproject.toml", fmt="text")
_PRECOMMIT_DOCS_DEPS_INDENT = "          "  # hook 映射内 additional_dependencies 序列项缩进
_REQUIREMENT_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")


def _normalize_requirement_name(name: str) -> str:
    """PEP 503 规范化包名（排序与 pytest 识别均以此为准）。"""
    return re.sub(r"[-_.]+", "-", name).lower()


def _is_test_only_requirement(name: str) -> bool:
    """pytest 及其插件是 tests/docs 运行器，不进入 hook 运行依赖。"""
    return name == "pytest" or name.startswith("pytest-")


def _yaml_double_quote(value: str) -> str:
    """以 YAML 双引号标量输出（保留作者源版本范围写法原文）。"""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _require_description(entry: dict[str, object]) -> str:
    """渲染条件性必需字段的显式校验（缺字段即刻拒绝，不静默取默认值）。"""
    description = entry.get("description")
    if not isinstance(description, str) or not description:
        raise SourceFieldError(f"redlines.yml {entry.get('id', '?')!r} 缺非空 description（渲染必需）")
    return description


def _render_agents_invariant(validated: dict[str, object], root: Path) -> list[str]:
    """AGENTS.md 最小安全集块：INVARIANT 全量 + R18，行格式与既有检查渲染器一致。"""
    redlines = validated[_REDLINES_SCHEMA.path]
    assert isinstance(redlines, list)  # load_validated_source 已保证
    lines: list[str] = []
    for entry in redlines:
        assert isinstance(entry, dict)
        if entry.get("rule_type") != "INVARIANT":
            continue
        lines.append(f"- {entry['id']}：{entry['title']} — {_require_description(entry)}")
    for entry in redlines:
        assert isinstance(entry, dict)
        if entry.get("id") == "R18":
            lines.append(f"- {entry['id']}：{entry['title']} — {_require_description(entry)}")
            break
    return lines


def _render_claude_executive(validated: dict[str, object], root: Path) -> list[str]:
    """CLAUDE.md 顶部摘要块：INVARIANT/EXCEPTIONABLE 分组 + R18，形态与既有渲染器一致。"""
    redlines = validated[_REDLINES_SCHEMA.path]
    assert isinstance(redlines, list)
    entries: list[dict[str, object]] = [e for e in redlines if isinstance(e, dict)]

    def _join(rule_type: str, sep: str) -> str:
        return sep.join(f"{entry['id']} {entry['title']}" for entry in entries if entry.get("rule_type") == rule_type)

    r18 = next((entry for entry in entries if entry.get("id") == "R18"), None)
    if r18 is None:
        raise SourceFieldError("redlines.yml 缺 R18 条目（claude-executive 摘要渲染必需）")
    return [
        f"> - **不可豁免安全不变量（INVARIANT，先读后写）**：{_join('INVARIANT', ' · ')}",
        f"> - **可豁免（EXCEPTIONABLE，经 exceptions.yml 例外注册豁免）**：{_join('EXCEPTIONABLE', ' / ')}",
        f"> - **工作区整洁**：R18 {r18['title']}（跨多文件任务须 git worktree 隔离）",
    ]


def _render_claude_self_check(validated: dict[str, object], root: Path) -> list[str]:
    """CLAUDE.md 人工评审自查清单块：声明 self_check 的红线逐条渲染（判据须非空字符串）。"""
    redlines = validated[_REDLINES_SCHEMA.path]
    assert isinstance(redlines, list)
    lines: list[str] = []
    ids: list[str] = []
    for entry in redlines:
        assert isinstance(entry, dict)
        criteria = entry.get("self_check")
        if not isinstance(criteria, list) or not criteria:
            continue
        if not all(isinstance(item, str) and item.strip() for item in criteria):
            raise SourceFieldError(
                f"redlines.yml {entry.get('id', '?')!r} self_check 须为非空字符串列表（不得静默取默认值）"
            )
        ids.append(str(entry["id"]))
        lines.append(f"> - **{entry['id']} {entry['title']}**：{'；'.join(criteria)}")
    header = (
        "> **人工评审红线可执行自查清单**（判据正本为 `redlines.yml` 的 `self_check` 字段："
        f"{' / '.join(ids)}；逐条回答后再交付；R18 有独立执行决策树，见下方）："
    )
    return [header, *lines]


def _render_agents_min_verify(validated: dict[str, object], root: Path) -> list[str]:
    """AGENTS.md 最小验证命令块：复用既有正本解析器（单一解析源，避免第二套解析漂移）。"""
    content = validated[_CONTRIBUTING_SCHEMA.path]
    assert isinstance(content, str)
    import check_docs_consistency as cdc  # 函数体内惰性 import：复用正本解析器且避免模块循环

    gate_commands, doc_commands, errors = cdc._parse_min_verify_command_block(content)
    if errors:
        raise SourceFormatError("CONTRIBUTING.md 最小验证命令受控块解析失败: " + "；".join(errors))
    return cdc._render_agents_min_verify_lines(gate_commands, doc_commands)


# ---------------------------------------------------------------------------
# D6：docs/README.md 文件级目录/ADR 索引 与 docs/governance/README.md 规则参考/任务路由
# （作者源 = 文件自身的标题/角色与治理 yml 正本，检视 §4.1；不引入手工清单）
# ---------------------------------------------------------------------------

_INDEX_COVERED_SUFFIXES = (".md", ".yml", ".yaml", ".json")
_ADR_FILENAME_RE = re.compile(r"^(\d{4})-[A-Za-z0-9][A-Za-z0-9-]*\.md$")


def _escape_cell(text: str) -> str:
    """表格单元格内的竖线转义（markdown 表格列边界不被标题内容破坏）。"""
    return text.replace("|", "\\|")


def _git_tracked_docs(root: Path, subdir: str) -> list[str] | None:
    """git 索引列出子目录下被跟踪文件（相对 posix 路径）；非 git 环境返回 None 降级。

    cwd 取传入 root（真实仓库 / 测试临时 git 仓库通用）；``-z`` 关闭路径转义，
    避免非 ASCII 文件名被转义为八进制序列。
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--", subdir],
            cwd=root,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return [rel for rel in result.stdout.decode("utf-8", errors="replace").split("\0") if rel]


def _docs_index_candidates(root: Path) -> list[Path]:
    """docs/README 文件级目录索引的作者源候选集：git 跟踪的 docs/** 治理文件。

    git 索引非空时为唯一候选来源（gitignored/未跟踪产物天然排除）；git 不可用
    （临时目录 / 归档环境）降级文件系统发现。注意：作者源为 docs 树本身，渲染读取
    窗口不做逐文件独占保护（开销不成比例），并发编辑漂移由更新后 all 检查 fail-closed 检出。
    """
    docs_root = root / "docs"
    tracked = _git_tracked_docs(root, "docs")
    if tracked:
        candidates = [
            root / rel for rel in tracked if (root / rel).suffix in _INDEX_COVERED_SUFFIXES and (root / rel).is_file()
        ]
    else:
        candidates = [p for p in docs_root.rglob("*") if p.is_file() and p.suffix in _INDEX_COVERED_SUFFIXES]
    # 排序键固定为 POSIX 相对路径字符串：Path 自带排序在 Windows 大小写不敏感、
    # 在 Linux 按 ASCII，`README.md` 与 `adr/…` 的相对次序会跨平台漂移，导致同一
    # 作者源在两平台渲染不同（CI 与本机不一致）。用显式键消除平台差异。
    return sorted(candidates, key=lambda p: p.relative_to(root).as_posix())


def _h1_title_from(text: str) -> str | None:
    """从 markdown 文本提取首个 ATX H1 标题（无 H1 返回 None，由调用方显式处置）。"""
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def _data_role_from(text: str) -> str:
    """yml/yaml/json 的角色描述 = 首个 ``#`` 注释行（机器可读文件无 H1，取自述首行）。"""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") and len(stripped) > 1:
            return stripped.lstrip("#").strip()
        if stripped:
            break
    return "—"


def _md_h1_title(path: Path) -> str | None:
    """文件级读取 H1（仅用于不在写入保护窗口内的文件；读取失败返回 None 不吞异常语义）。"""
    try:
        return _h1_title_from(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None


def _data_file_role(path: Path) -> str:
    """文件级读取角色描述（仅用于不在写入保护窗口内的文件）。"""
    try:
        return _data_role_from(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return "—"


def _render_docs_readme_file_index(validated: dict[str, object], root: Path) -> list[str]:
    """docs/README.md 文件级目录索引块（D6）：git 跟踪的 docs/** 治理文件逐行登记。

    行格式 ``| [相对路径](./…) | 标题/角色 |``；md 取首个 H1，yml/yaml/json 取首个
    注释行；缺 H1 的 markdown 以「（无 H1 标题）」显式占位——发现信息缺失可见，不静默省略。
    登记为 text 作者源的文件（本更新窗口内的受保护目标/作者源）经快照字节渲染，
    其余文件磁盘直读（并发编辑漂移由更新后 all 检查 fail-closed 检出）。
    """
    snapshot_texts: dict[str, str] = {k: v for k, v in validated.items() if isinstance(v, str)}
    lines = ["| 文档 | 标题 / 角色 |", "|------|--------------|"]
    for path in _docs_index_candidates(root):
        rel = path.relative_to(root / "docs").as_posix()
        snapshot = snapshot_texts.get(path.relative_to(root).as_posix())
        if path.suffix == ".md":
            title = (_h1_title_from(snapshot) if snapshot is not None else _md_h1_title(path)) or "（无 H1 标题）"
        else:
            title = _data_role_from(snapshot) if snapshot is not None else _data_file_role(path)
        lines.append(f"| [{rel}](./{rel}) | {_escape_cell(title)} |")
    if len(lines) == 2:
        raise SourceFormatError("docs/README 文件级目录索引作者源为空（docs/ 下无治理文件候选）")
    return lines


def _render_docs_readme_adr_index(validated: dict[str, object], root: Path) -> list[str]:
    """docs/README.md ADR 索引块（D6，承接检视开放问题 #5）：逐文件生成、编号自动补齐。

    编号取自文件名 NNNN，标题取自文件 H1（剥离 ``ADR-NNNN:`` 前缀）；文件名形态
    或 H1 缺失即显式拒绝（发现信息正本缺失不得静默渲染）。
    """
    adr_dir = root / "docs" / "adr"
    if not adr_dir.is_dir():
        raise SourceMissingError("作者源缺失: docs/adr/（ADR 索引渲染需要）")
    lines = ["| 编号 | 标题（文件 H1 正本） | 文档 |", "|------|------|------|"]
    count = 0
    for path in sorted(adr_dir.glob("*.md")):
        if path.name == "README.md":
            continue
        match = _ADR_FILENAME_RE.match(path.name)
        if not match:
            raise SourceFormatError(f"ADR 文件名不符合 NNNN-slug.md 形态: {path.name}")
        title = _md_h1_title(path)
        if title is None:
            raise SourceFormatError(f"ADR {path.name} 缺 H1 标题（文件自己的标题为发现信息正本，不得缺失）")
        title = re.sub(rf"^ADR-{match.group(1)}\s*[:：]\s*", "", title).strip() or title
        lines.append(f"| ADR-{match.group(1)} | {_escape_cell(title)} | [{path.name}](./adr/{path.name}) |")
        count += 1
    if count == 0:
        raise SourceFormatError("docs/adr/ 无 ADR 文件（ADR 索引渲染拒绝空清单）")
    return lines


def _render_governance_rules_view(validated: dict[str, object], root: Path) -> list[str]:
    """docs/governance/README.md 适用规则视图块（D6）：redlines.yml 全量红线参考表。

    仅登记 id/title/rule_type/automation_coverage 四列的引用视图（不复制判据正文，
    语义正本仍是 redlines.yml 与 CLAUDE.md §3.1）；coverage 缺失以「—」显式占位。
    """
    redlines = validated[_REDLINES_SCHEMA.path]
    assert isinstance(redlines, list)
    lines = ["| 红线 | 标题 | 规则类型 | 自动化覆盖 |", "|------|------|---------|-----------|"]
    for entry in redlines:
        assert isinstance(entry, dict)
        coverage = entry.get("automation_coverage")
        coverage_text = _escape_cell(str(coverage)) if isinstance(coverage, str) and coverage.strip() else "—"
        title = _escape_cell(str(entry.get("title", "")))
        lines.append(f"| {entry['id']} | {title} | {entry.get('rule_type', '—')} | {coverage_text} |")
    return lines


def _render_governance_topics_view(validated: dict[str, object], root: Path) -> list[str]:
    """docs/governance/README.md 任务路由视图块（D6）：canonical-topics.yml 全主题路由表。

    按相同 canonical+section 合并展示（检视 §4.1「按相同 canonical/section 合并展示，
    保全部 topic ID」）；canonical 链接为自 docs/governance/ 的相对路径，死链由
    check_relative_dead_links 门禁守护。
    """
    topics = validated[_CANONICAL_TOPICS_SCHEMA.path]
    assert isinstance(topics, list)
    merged: dict[tuple[str, str], list[dict[str, object]]] = {}
    for topic in topics:
        assert isinstance(topic, dict)
        canonical = str(topic["canonical"]).removeprefix("./")
        section = topic.get("section")
        key = (canonical, str(section) if isinstance(section, str) and section.strip() else "")
        merged.setdefault(key, []).append(topic)
    lines = [
        "| 主题 id | 任务类型（title） | canonical 正本 | 章节 |",
        "|---------|------------------|----------------|------|",
    ]
    for (canonical, section), group in merged.items():
        rel = posixpath.relpath(canonical, "docs/governance")
        ids = ", ".join(str(topic["id"]) for topic in group)
        titles = " / ".join(_escape_cell(str(topic["title"])) for topic in group)
        lines.append(f"| {ids} | {titles} | [{canonical}]({rel}) | {section or '—'} |")
    return lines


def _render_agents_task_routes(validated: dict[str, object], root: Path) -> list[str]:
    """AGENTS.md 任务路由表（ADR-0018 §4.1）：canonical-topics.yml 全主题任务表。

    与 governance 视图同源、同「canonical+section 合并展示、保全部 topic ID」规则；
    链接按仓库根相对路径（AGENTS.md 位于根），死链由 check_relative_dead_links 守护。
    """
    topics = validated[_CANONICAL_TOPICS_SCHEMA.path]
    assert isinstance(topics, list)
    merged: dict[tuple[str, str], list[dict[str, object]]] = {}
    for topic in topics:
        assert isinstance(topic, dict)
        canonical = str(topic["canonical"]).removeprefix("./")
        section = topic.get("section")
        key = (canonical, str(section) if isinstance(section, str) and section.strip() else "")
        merged.setdefault(key, []).append(topic)
    lines = [
        "| 任务类型（title） | 主题 id | canonical 正本 | 章节 |",
        "|------------------|---------|----------------|------|",
    ]
    for (canonical, section), group in merged.items():
        ids = ", ".join(str(topic["id"]) for topic in group)
        titles = " / ".join(_escape_cell(str(topic["title"])) for topic in group)
        lines.append(f"| {titles} | {ids} | [{canonical}]({canonical}) | {section or '—'} |")
    return lines


def _render_precommit_docs_deps(validated: dict[str, object], root: Path) -> list[str]:
    """两个 docs hook 的 additional_dependencies 片段（D23）：由 pyproject docs 组派生。

    单一作者源：pyproject.toml ``[dependency-groups].docs``（uv 亦以此编译锁清单）。
    排除 pytest 及其插件；其余条目保留版本范围写法原文，按规范化包名稳定排序，
    保证「只改 docs 组一次，两个 hook 同时产生同一差异」。缺组/非字符串条目/排除后
    为空均显式拒绝，不静默生成空依赖列表。
    """
    text = validated[_PYPROJECT_DOCS_SCHEMA.path]
    assert isinstance(text, str)  # fmt=text 由 load_validated_source 保证
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise SourceFormatError(f"作者源 {_PYPROJECT_DOCS_SCHEMA.path} TOML 解析失败: {exc}") from exc
    groups = data.get("dependency-groups")
    if not isinstance(groups, dict):
        raise SourceFormatError(f"作者源 {_PYPROJECT_DOCS_SCHEMA.path} 缺 [dependency-groups] 表")
    docs = groups.get("docs")
    if not isinstance(docs, list):
        raise SourceFormatError(f"作者源 {_PYPROJECT_DOCS_SCHEMA.path} 缺 dependency-groups.docs 列表")
    derived: list[tuple[str, str]] = []
    for index, item in enumerate(docs):
        if not isinstance(item, str) or not item.strip():
            raise SourceFormatError(f"作者源 {_PYPROJECT_DOCS_SCHEMA.path} docs 组第 {index} 条不是非空字符串")
        requirement = item.strip()
        match = _REQUIREMENT_NAME_RE.match(requirement)
        if match is None:
            raise SourceFormatError(f"作者源 {_PYPROJECT_DOCS_SCHEMA.path} docs 组无法解析需求: {requirement!r}")
        name = _normalize_requirement_name(match.group(1))
        if _is_test_only_requirement(name):
            continue
        derived.append((name, requirement))
    if not derived:
        raise SourceFormatError(f"作者源 {_PYPROJECT_DOCS_SCHEMA.path} docs 组排除 pytest 后为空")
    derived.sort()
    return [f"{_PRECOMMIT_DOCS_DEPS_INDENT}- {_yaml_double_quote(requirement)}" for _, requirement in derived]


# 封闭目标清单（模块常量注册表）：激活条目 = 具备真实作者源的真实块；
# 槽位条目（active=False）= 尚无作者源接线的目标，登记占位但不启用渲染。
BUILTIN_REGISTRY: tuple[BlockSpec, ...] = (
    BlockSpec(
        block_id="redlines-invariant",
        target="AGENTS.md",
        sources=(_REDLINES_SCHEMA,),
        render=_render_agents_invariant,
        block_format="markdown",
        active=True,
    ),
    BlockSpec(
        block_id="min-verify-commands",
        target="AGENTS.md",
        sources=(_CONTRIBUTING_SCHEMA,),
        render=_render_agents_min_verify,
        block_format="markdown",
        active=True,
    ),
    BlockSpec(
        block_id="agents-task-routes",
        target="AGENTS.md",
        sources=(_CANONICAL_TOPICS_SCHEMA,),
        render=_render_agents_task_routes,
        block_format="markdown",
        active=True,
    ),
    BlockSpec(
        block_id="claude-executive",
        target="CLAUDE.md",
        sources=(_REDLINES_SCHEMA,),
        render=_render_claude_executive,
        block_format="markdown",
        active=True,
    ),
    BlockSpec(
        block_id="redlines-self-check",
        target="CLAUDE.md",
        sources=(_REDLINES_SCHEMA,),
        render=_render_claude_self_check,
        block_format="markdown",
        active=True,
    ),
    # —— 以下为尚无作者源接线的目标槽位：只登记不启用渲染（D9 切换时补作者源并激活）——
    BlockSpec(
        block_id="a11y-contrast-reference",
        target="docs/flet/accessibility-baseline.md",
        sources=(),
        render=None,
        block_format="markdown",
        active=False,
    ),
    # —— D23 接线：hook 隔离环境的 docs 解析依赖由 pyproject [dependency-groups].docs 派生
    #    （排除 pytest 及其插件；锁清单仍由 uv 编译）。两个 hook 各自的 additional_dependencies
    #    列表为受控生成块，同一作者源变化必须同时反映到两处。 ——
    BlockSpec(
        block_id="precommit-docs-deps-verify-versions",
        target=".pre-commit-config.yaml",
        sources=(_PYPROJECT_DOCS_SCHEMA,),
        render=_render_precommit_docs_deps,
        block_format="yaml",
        active=True,
    ),
    BlockSpec(
        block_id="precommit-docs-deps-docs-consistency",
        target=".pre-commit-config.yaml",
        sources=(_PYPROJECT_DOCS_SCHEMA,),
        render=_render_precommit_docs_deps,
        block_format="yaml",
        active=True,
    ),
    # —— D6 接线：docs/README.md 文件级目录索引 / ADR 索引（作者源 = docs 树与文件自身标题；
    #      同一更新窗口内的受保护目标/作者源文件登记为 text 作者源经快照渲染，见上方 schema 注释）——
    BlockSpec(
        block_id="docs-readme-file-index",
        target="docs/README.md",
        sources=(
            _DOCS_README_TEXT_SCHEMA,
            _DOCS_GOVERNANCE_README_TEXT_SCHEMA,
            _REDLINES_TEXT_SCHEMA,
            _CANONICAL_TOPICS_TEXT_SCHEMA,
        ),
        render=_render_docs_readme_file_index,
        block_format="markdown",
        active=True,
    ),
    BlockSpec(
        block_id="docs-readme-adr-index",
        target="docs/README.md",
        sources=(),
        render=_render_docs_readme_adr_index,
        block_format="markdown",
        active=True,
    ),
    # —— D6 接线：docs/governance/README.md 规则参考 / 任务路由（作者源 = redlines.yml / canonical-topics.yml）——
    BlockSpec(
        block_id="governance-readme-rules",
        target="docs/governance/README.md",
        sources=(_REDLINES_SCHEMA,),
        render=_render_governance_rules_view,
        block_format="markdown",
        active=True,
    ),
    BlockSpec(
        block_id="governance-readme-topics",
        target="docs/governance/README.md",
        sources=(_CANONICAL_TOPICS_SCHEMA,),
        render=_render_governance_topics_view,
        block_format="markdown",
        active=True,
    ),
)


def render_block_lines(root: Path, spec: BlockSpec) -> list[str]:
    """加载并校验作者源后渲染块内容行（不含包裹标记）；校验失败显式抛出。"""
    if spec.render is None:
        raise InvalidSpecError(f"块 {spec.block_id!r} 未启用渲染（槽位无作者源）")
    validated: dict[str, object] = {}
    for schema in spec.sources:
        if schema.path not in validated:
            validated[schema.path] = load_validated_source(root, schema)
    return list(spec.render(validated, Path(root)))


# ---------------------------------------------------------------------------
# 快照与写入保护（§5.2.1 全窗口）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Snapshot:
    """一次快照：相对路径、解析路径、文件身份、原始字节 hash、原始字节。"""

    rel: str
    resolved: Path
    identity: tuple[int, int]  # (st_dev, st_ino)
    sha256: str
    data: bytes


def snapshot_file(root: Path, rel: str) -> _Snapshot:
    """对工作根内文件建立快照（原始字节 hash + 解析路径 + 文件身份）。"""
    resolved = resolve_within(root, rel)
    data = resolved.read_bytes()
    stat = resolved.stat()
    return _Snapshot(
        rel=rel,
        resolved=resolved,
        identity=(stat.st_dev, stat.st_ino),
        sha256=hashlib.sha256(data).hexdigest(),
        data=data,
    )


def verify_snapshot(snapshot: _Snapshot, guard: WriteGuard | None = None) -> None:
    """保护窗口内核对快照；字节/身份任一变化即拒绝（不基于旧快照继续）。

    guard 非 None 时经持有 fd 读取/查询身份（独占保护期间新开句柄不可用，
    且被保护文件不会消失）；None 时直读（无保护环境的独立核对，如单元测试）。
    """
    if guard is not None:
        data = guard.read_bytes(snapshot.resolved)
        identity = guard.file_identity(snapshot.resolved)
    else:
        if not snapshot.resolved.is_file():
            raise SnapshotDriftError(f"{snapshot.rel}: 文件在快照后消失")
        data = snapshot.resolved.read_bytes()
        stat = snapshot.resolved.stat()
        identity = (stat.st_dev, stat.st_ino)
    if identity != snapshot.identity:
        raise SnapshotDriftError(f"{snapshot.rel}: 文件身份变化（被删除重建/替换）")
    if hashlib.sha256(data).hexdigest() != snapshot.sha256:
        raise SnapshotDriftError(f"{snapshot.rel}: 内容在快照后被修改")


PLATFORM_LOCK_BACKEND = "win32-exclusive-open" if sys.platform == "win32" else "posix-advisory-unsupported"

if sys.platform == "win32":
    import ctypes

    _INVALID_HANDLE: int | None = ctypes.c_void_p(-1).value
    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _KERNEL32.CreateFileW.restype = ctypes.c_void_p
    _KERNEL32.CreateFileW.argtypes = (
        ctypes.c_void_p,  # lpFileName
        ctypes.c_uint32,  # dwDesiredAccess
        ctypes.c_uint32,  # dwShareMode
        ctypes.c_void_p,  # lpSecurityAttributes
        ctypes.c_uint32,  # dwCreationDisposition
        ctypes.c_uint32,  # dwFlagsAndAttributes
        ctypes.c_void_p,  # hTemplateFile
    )
    _GENERIC_READ_WRITE = 0xC0000000  # GENERIC_READ | GENERIC_WRITE
    _OPEN_EXISTING = 3
    _FILE_ATTRIBUTE_NORMAL = 0x80


class WriteGuard:
    """对一组文件取得**内核级独占写入保护**并保持到释放（§5.2.1-1/-2）。

    Windows：经 CreateFileW 以 dwShareMode=0 独占打开（GENERIC_READ|GENERIC_WRITE，
    OPEN_EXISTING）——保护期间任何其他进程对这些文件的打开（读/写/截断/替换，含
    编辑器保存的整写与 MoveFileEx 替换两条路径）均失败（ERROR_SHARING_VIOLATION），
    实际覆盖外部写者；读取/写入/文件身份查询全部经持有句柄进行（锁定/独占期间
    新开句柄不可用，核对通道必须走持有 fd，实测字节锁阻止区域内读写）。
    POSIX：flock 为协作锁，无法证明覆盖外部编辑器，取得后即显式拒绝原位更新。
    """

    def __init__(self, paths: Sequence[Path]) -> None:
        self._paths = [Path(p) for p in paths]
        self._fds: dict[Path, int] = {}

    def __enter__(self) -> WriteGuard:
        try:
            for path in self._paths:
                self._acquire(path)
        except ProtectionError:
            self._release()
            raise
        except OSError as exc:
            self._release()
            raise ProtectionDeniedError(f"无法取得写入保护: {exc}") from exc
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._release()

    def _acquire(self, path: Path) -> None:
        if sys.platform == "win32":
            import msvcrt  # 函数体内平台分支 import：POSIX 环境无此模块

            handle = _KERNEL32.CreateFileW(
                str(path),
                _GENERIC_READ_WRITE,
                0,  # dwShareMode=0：独占——其他进程任何打开均失败
                None,
                _OPEN_EXISTING,
                _FILE_ATTRIBUTE_NORMAL,
                None,
            )
            if handle is None or handle == _INVALID_HANDLE:
                error_code = ctypes.get_last_error()
                raise ProtectionDeniedError(f"无法独占打开 {path}（可能被编辑器/其他进程占用）: WinError {error_code}")
            try:
                fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
            except OSError:
                import ctypes as _ct

                _ct.windll.kernel32.CloseHandle(handle)
                raise
            self._fds[path] = fd
        else:
            import fcntl  # 函数体内平台分支 import：Windows 无此模块

            fd = os.open(str(path), os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)  # 取锁失败即释放本次新开的句柄，不留泄漏（成功路径由 _release 统一关闭）
                raise
            self._fds[path] = fd
            # 合同 §5.2.1-3：协作锁无法证明覆盖外部编辑器 → 拒绝原位更新，
            # 不把共享工作区默认为独占环境（先释放已取得句柄再拒绝）。
            self._release()
            raise ProtectionUnsupportedError(
                "当前平台仅有协作锁（flock），无法证明写入保护覆盖外部编辑器；按合同拒绝原位更新"
            )

    def _release(self) -> None:
        for fd in self._fds.values():
            try:
                os.close(fd)  # CRT _close 会同步关闭底层 CreateFileW 句柄并解除独占
            except OSError:
                pass  # 释放失败不掩盖原错误：句柄随进程退出兜底关闭
        self._fds.clear()

    def _fd_of(self, path: Path) -> int:
        fd = self._fds.get(Path(path))
        if fd is None:
            raise InvalidSpecError(f"目标 {path} 未处于写入保护内（内部契约错误）")
        return fd

    def read_bytes(self, path: Path) -> bytes:
        """经持有 fd 读取当前字节（独占期间新开句柄不可用，核对必须走此通道）。"""
        fd = self._fd_of(path)
        size = os.fstat(fd).st_size
        os.lseek(fd, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = size
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 1 << 20))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def file_identity(self, path: Path) -> tuple[int, int]:
        """经持有 fd 查询文件身份 (st_dev, st_ino)。"""
        stat = os.fstat(self._fd_of(path))
        return (stat.st_dev, stat.st_ino)

    def write_bytes(self, path: Path, data: bytes) -> None:
        """经持有 fd 原位替换写入（保护窗口内不释放保护）。"""
        # NOTE(lazy): 受保护目标用 ftruncate+write 原位写，不用「临时文件 + os.replace」原子替换.
        # ceiling: 不承诺崩溃原子性（ftruncate 后、write 前崩溃会留下截断/半写文件）；换来独占句柄与目标身份不脱钩.
        # upgrade: 引入不破坏句柄身份的崩溃一致原位写方案，或放弃独占句柄保护改为临时文件+替换；在此之前不得宣称写入中断可原子回滚（ADR-0018 DEV-02）.
        fd = self._fd_of(path)
        os.ftruncate(fd, len(data))
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, data)


# ---------------------------------------------------------------------------
# 更新编排（读快照 → 保护 → 核对 → 渲染 → 预校验 → 逐文件提交 → all 检查）
# ---------------------------------------------------------------------------

BarrierFn = Callable[[str, str | None], None]
PostCheckFn = Callable[[], list[str]]


@dataclass(frozen=True)
class UpdateResult:
    """更新结果：已写 / 未变 / 冲突 / 写失败如实分列；blocked 为整体阻塞原因。

    不承诺跨文件事务：部分目标写失败或冲突时，其余目标按各自核对结果继续处理，
    调用方据 ok 与各列表输出已写/未写清单（§5.2.1-4）。
    """

    written: tuple[str, ...]
    unchanged: tuple[str, ...]
    conflicts: tuple[str, ...]
    failed: tuple[str, ...]
    blocked: str | None
    post_check_errors: tuple[str, ...]
    conflict_reasons: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.blocked is None and not self.conflicts and not self.failed and not self.post_check_errors


def _validate_specs(specs: Sequence[BlockSpec]) -> None:
    """注册表条目静态校验：激活块须有渲染函数、块 id/格式合法、同目标格式一致。"""
    format_by_target: dict[str, str] = {}
    block_ids: set[tuple[str, str]] = set()
    for spec in specs:
        # BlockSpec.__post_init__ 已覆盖单项合法性；此处补跨条目约束
        fmt = format_by_target.setdefault(spec.target, spec.block_format)
        if fmt != spec.block_format:
            raise InvalidSpecError(f"目标 {spec.target!r} 的块格式不一致: {fmt!r} vs {spec.block_format!r}")
        key = (spec.target, spec.block_id)
        if key in block_ids:
            raise DuplicateBlockError(f"注册表内同一目标的块身份重复: {spec.target}#{spec.block_id}")
        block_ids.add(key)


def run_update(
    root: Path,
    registry: Sequence[BlockSpec] = BUILTIN_REGISTRY,
    *,
    barrier: BarrierFn | None = None,
    post_check: PostCheckFn | None = None,
    only: Sequence[str] | None = None,
) -> UpdateResult:
    """执行受控更新（§5.2.1 全窗口：快照 → 保护 → 核对 → 渲染 → 提交 → all 检查）。

    barrier：确定性屏障钩子（测试注入点），phase ∈ {"after_render", "before_replace",
    "after_write"}，第二参数为当前目标（after_render 为 None）。生产路径不传。

    post_check：更新后 all 检查回调（仍在保护窗口内执行）；返回错误列表非空 → 结果失败。

    only：封闭清单内的目标子集选择；含未登记目标即抛 TargetNotRegisteredError。
    """
    root_path = Path(root)
    registered_targets = {spec.target for spec in registry}
    if only is not None:
        unknown = [rel for rel in only if rel not in registered_targets]
        if unknown:
            raise TargetNotRegisteredError(f"目标不在封闭清单: {unknown}")
    specs = [spec for spec in registry if spec.active and (only is None or spec.target in only)]
    _validate_specs(specs)

    # 1) 路径安全（目标与作者源均不得越界，含符号链接解析后越界）
    target_rels = sorted({spec.target for spec in specs})
    source_rels = sorted({schema.path for spec in specs for schema in spec.sources})
    for rel in target_rels:
        resolve_within(root_path, rel)
    for rel in source_rels:
        resolve_within(root_path, rel)

    # 2) 存在性 + 快照（作者源缺失/目标缺失显式拒绝，不静默当空集合）
    for rel in source_rels:
        if not (root_path / rel).is_file():
            raise SourceMissingError(f"作者源缺失: {rel}")
    for rel in target_rels:
        if not (root_path / rel).is_file():
            raise TargetMissingError(f"登记目标不存在: {rel}")
    target_snaps = {rel: snapshot_file(root_path, rel) for rel in target_rels}
    source_snaps = {rel: snapshot_file(root_path, rel) for rel in source_rels}

    # 3) 取得写入保护（覆盖全部作者源与目标；任一失败即整体拒绝，不自动重试）。
    #    同一文件可兼为目标与作者源（如 docs/README.md 既是 file-index 目标又是其标题
    #    作者源）：guard 按解析路径去重，避免对同一文件二次独占打开自 sharing-violation。
    seen_paths: set[Path] = set()
    guard_paths: list[Path] = []
    for snap in (*target_snaps.values(), *source_snaps.values()):
        if snap.resolved not in seen_paths:
            seen_paths.add(snap.resolved)
            guard_paths.append(snap.resolved)
    with WriteGuard(guard_paths) as guard:
        # 4) 保护内核对（hash/身份/解析路径任一变化即拒绝；经持有 fd 读取）
        for snap in (*target_snaps.values(), *source_snaps.values()):
            verify_snapshot(snap, guard)

        # 5) 渲染（作者源加载 + schema 校验；失败显式抛出，不静默取默认值。
        #    数据取自保护内核对后的快照字节——独占保护保证快照即当前，避免新开句柄）
        rendered: dict[str, dict[str, list[str]]] = {}
        source_texts = {rel: snap.data.decode("utf-8") for rel, snap in source_snaps.items()}
        for spec in specs:
            validated: dict[str, object] = {}
            for schema in spec.sources:
                if schema.path not in validated:
                    validated[schema.path] = load_validated_source(root_path, schema, text=source_texts[schema.path])
            assert spec.render is not None  # _validate_specs/BlockSpec 已保证
            rendered.setdefault(spec.target, {})[spec.block_id] = list(spec.render(validated, root_path) or [])

        # 6) 预校验：目标现文本块结构 + 登记块存在性（缺块/重复/嵌套/越界均拒绝）
        target_texts = {rel: snap.data.decode("utf-8") for rel, snap in target_snaps.items()}
        blocks_by_target: dict[str, dict[str, BlockSpan]] = {}
        fmt_by_target: dict[str, str] = {}
        for rel in target_rels:
            fmt = next(spec.block_format for spec in specs if spec.target == rel)
            fmt_by_target[rel] = fmt
            blocks_by_target[rel] = scan_blocks(target_texts[rel], fmt)
            for spec in (s for s in specs if s.target == rel):
                if spec.block_id not in blocks_by_target[rel]:
                    raise MissingBlockError(f"目标 {rel} 缺生成块 {spec.block_id!r}")

        # 7) 渲染后屏障（确定性注入点：此后外部写入尝试须被保护阻止或核对检出）
        blocked_by_barrier: str | None = None
        if barrier is not None:
            try:
                barrier("after_render", None)
            except PermissionError as exc:
                # 独占保护阻止了屏障注入的写入尝试（如编辑器/测试模拟的外部写者）：
                # 视为检出并发写冲突——全部目标不覆盖、按合同非零退出如实报告
                blocked_by_barrier = f"渲染后写入尝试被写入保护阻止: {exc}"

        written: list[str] = []
        unchanged: list[str] = []
        conflicts: list[str] = []
        conflict_reasons: list[str] = []
        failed: list[str] = []
        if blocked_by_barrier is not None:
            conflicts.extend(target_rels)
            conflict_reasons.extend(blocked_by_barrier for _ in target_rels)
        else:
            # 8) 逐文件提交核对 + 替换（核对失败记冲突不写入；写失败记失败继续其余目标）
            for rel in target_rels:
                try:
                    verify_snapshot(target_snaps[rel], guard)
                    for spec in (s for s in specs if s.target == rel):
                        for schema in spec.sources:
                            verify_snapshot(source_snaps[schema.path], guard)
                except SnapshotDriftError as exc:
                    conflicts.append(rel)
                    conflict_reasons.append(str(exc))
                    continue
                try:
                    if barrier is not None:
                        barrier("before_replace", rel)
                    # 写前最后一读核对：提交核对后但替换前的编辑在此检出（§5.2.1-5 第二屏障）
                    verify_snapshot(target_snaps[rel], guard)
                    new_text = target_texts[rel]
                    for block_id, lines in rendered[rel].items():
                        new_text = replace_block(new_text, fmt_by_target[rel], block_id, lines)
                    if new_text == target_texts[rel]:
                        unchanged.append(rel)  # 渲染零差异：不重复写入（幂等）
                    else:
                        guard.write_bytes(target_snaps[rel].resolved, new_text.encode("utf-8"))
                        written.append(rel)
                    if barrier is not None:
                        barrier("after_write", rel)
                except SnapshotDriftError as exc:
                    conflicts.append(rel)
                    conflict_reasons.append(str(exc))
                except PermissionError as exc:
                    # 提交核对后但替换前，屏障注入的写入尝试被独占保护阻止：
                    # 检出冲突——该目标原位未覆盖，如实报告
                    conflicts.append(rel)
                    conflict_reasons.append(f"提交核对后写入尝试被写入保护阻止: {exc}")
                except OSError as exc:
                    failed.append(rel)
                    conflict_reasons.append(f"{rel}: 写失败 {exc}")

        # 10) with 退出时释放保护

    # 9) 更新后 all 检查（§5.2.1-2 要求其处于保护边界内；此处为有意识的偏离）：
    # Windows 独占保护期间任何新开句柄读取均被拒（实测 PermissionError），而更新后
    # all 检查的 42 项检查函数全部经新句柄直读文件（迁移共享解析属 D5）——在保护
    # 窗口内执行必然失败。故移至保护释放后立即执行：写入窗口（快照→提交替换）仍由
    # 独占保护完整覆盖，不会"以事后检查补救已丢失的正文"；释放后至检查期间的并发
    # 编辑由检查自身的内容比对 fail-closed 检出（退出 1）。
    # NOTE(lazy): all 检查在写入保护释放后立即执行，不在同一保护边界内.
    # ceiling: §5.2.1-2「更新后 all 检查处于同一保护边界内」未完全满足；42 项检查全部新开句柄直读.
    # upgrade: 保护机制支持共享读，或检查函数改为复用写者已持有 fd/共享解析视图时，必须移入保护窗口（ADR-0018 DEV-01）.
    post_errors: list[str] = []
    if post_check is not None:
        try:
            post_errors = list(post_check())
        except (DocsGeneratedError, PermissionError, OSError) as exc:
            post_errors = [f"更新后 all 检查执行失败（fail-closed）: {exc}"]

    return UpdateResult(
        written=tuple(written),
        unchanged=tuple(unchanged),
        conflicts=tuple(conflicts),
        conflict_reasons=tuple(conflict_reasons),
        failed=tuple(failed),
        blocked=None,
        post_check_errors=tuple(post_errors),
    )
