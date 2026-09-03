"""文档一致性检查（C5 第一阶段 + 第二阶段 3a + 3b + 3c + Flet 入口完整性）。

检查项：
1. Markdown 锚点死链校验：扫描 CHECKED_DOCS 全部受检文件中带 `#anchor` 的 markdown 链接,
   确认目标文件存在且标题存在（支持同文件 `#anchor` 与跨文件 `./file.md#anchor`）。
2. CLAUDE.md 顶部版本与 pyproject.toml `[project].version` 一致。
3. 文档中"项目使用 N 个 pre-commit hook"的数量与 `.pre-commit-config.yaml` 本地 hook 数量一致。
4. NOTE(lazy) 三要素格式检查：扫描所有 .py 文件中的 `NOTE(lazy):` 标记,
   校验后续块内是否含 `ceiling:` 与 `upgrade:` 两个关键字（CLAUDE.md §3.3 要求）。
5. Flet 版本漂移检查：扫描治理文档中 Flet 关键词附近的具体补丁版本号
   （CLAUDE.md §3.2「文档 SHALL NOT 硬编码 Flet 补丁版本号」）。
6. 相对链接死链检查：扫描受检 markdown 中不含锚点的相对路径链接，确认目标文件存在。
7. redlines.yml 一致性检查：校验 docs/governance/redlines.yml 与 CLAUDE.md §3.1 红线表一致
   （R 编号 append-only / 连续 / 条目数匹配，见 ADR-0003）。
8. enforcement 字段映射一致性检查（3c）：校验 redlines.yml `enforcement` 字段中声称的守护机制
   实际配置存在且粗粒度可达（不变量 N1~N8，见 ADR-0005）。
9. Flet 入口完整性检查：校验 docs/flet/README.md 覆盖全部 docs/flet/*.md 专题文档，
   且不引用不存在的专题文件。
10. exceptions.yml 例外注册表一致性检查（P1-01）：校验 docs/governance/exceptions.yml 必填字段、
   id 唯一性、rule_id 存在性、paths 存在性与 expires_at/removal_trigger 二选一。
11. canonical-topics.yml 主题映射一致性检查（P2-12）：校验 docs/governance/canonical-topics.yml
   必填字段、id 唯一性、canonical/workflow 路径在仓库中真实存在。

退出码：0 通过，1 失败。供 pre-commit `docs-consistency` hook 与 pytest 契约测试调用。

第二阶段扩展：
- 3a NOTE(lazy) 三要素检查（已实现：check_note_lazy_format()）。
- 3b 红线 R1~R18 编号 append-only 检查（已实现：check_redlines_yaml_consistency()，见 ADR-0003）。
- 3c enforcement 字段与实际 hook / CI job 映射检查（已实现：check_enforcement_mapping()，见 ADR-0005）。
- Flet 入口完整性检查（已实现：check_flet_hub_completeness()）。
- exceptions.yml 例外注册表一致性检查（已实现：check_exceptions_yaml_consistency()，P1-01）。
- canonical-topics.yml 主题映射一致性检查（已实现：check_canonical_topics_consistency()，P2-12）。
"""

from __future__ import annotations

import re
import sys
import tomllib
import typing
from dataclasses import dataclass
from io import TextIOWrapper
from pathlib import Path

# Windows 默认 GBK 终端会因 emoji（✅/❌）输出触发 UnicodeEncodeError，强制 UTF-8 输出。
# 在模块加载时配置，确保 main() 与单元测试导入时均生效（不依赖 -X utf8 启动参数）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        # AttributeError: stream 无 reconfigure 方法（如被替换为非 TextIO）。
        # ValueError: encoding 参数无效。
        pass

ROOT = Path(__file__).resolve().parent.parent

CLAUDE_PATH = ROOT / "CLAUDE.md"
CONTRIBUTING_PATH = ROOT / "CONTRIBUTING.md"
# man/flet-best-practices.md 现为 stub，指向 docs/flet/README.md（保留历史路径兼容）
FLET_BEST_PRACTICES_PATH = ROOT / "man" / "flet-best-practices.md"
KNOWN_TECHNICAL_DEBT_PATH = ROOT / "docs" / "debt" / "known-technical-debt.md"
REDLINES_YAML_PATH = ROOT / "docs" / "governance" / "redlines.yml"
EXCEPTIONS_YAML_PATH = ROOT / "docs" / "governance" / "exceptions.yml"
CANONICAL_TOPICS_YAML_PATH = ROOT / "docs" / "governance" / "canonical-topics.yml"
AGENTS_PATH = ROOT / "AGENTS.md"
PYPROJECT_PATH = ROOT / "pyproject.toml"
PRECOMMIT_PATH = ROOT / ".pre-commit-config.yaml"

# 3c: enforcement 字段校验所需的项目配置路径常量（monkeypatch 可注入，禁止内联路径构造）
CI_WORKFLOW_DIR = ROOT / ".github" / "workflows"
CHECK_REDLINES_SCRIPT_PATH = ROOT / "scripts" / "check_redlines.py"
GITLEAKS_CONFIG_PATH = ROOT / ".gitleaks.toml"

# docs/flet/ 目录与导航入口
FLET_DOCS_DIR = ROOT / "docs" / "flet"
FLET_HUB_PATH = FLET_DOCS_DIR / "README.md"

# 动态发现 docs/flet/*.md（含 README.md、ui-ux-best-practices.md、canvaskit-rendering-e2e-guide.md 等）
# 新增 Flet 专题文档会自动纳入门禁，无需手动维护清单
FLET_DOCS_PATHS: list[Path] = sorted(FLET_DOCS_DIR.glob("*.md"))

# 受检 markdown 文件清单（锚点死链 + 相对链接死链 + pre-commit hook 数量校验范围）
# P2-06 修复：改为递归发现全部受跟踪 Markdown，再用显式排除清单处理生成物和归档。
# 递归发现范围：根目录 *.md、docs/ 与 man/ 全部 *.md、PR 模板；排除项必须带原因（_DOC_EXCLUDES）。
# Flet 入口完整性：FLET_DOCS_PATHS 动态发现 docs/flet/*.md，新增专题自动纳入门禁。
_DOC_EXCLUDES: dict[Path, str] = {
    # 示例：ROOT / "docs" / "xxx" / "generated.md": "生成物，非人工维护",
}
CHECKED_DOCS: list[Path] = sorted(
    {
        *ROOT.glob("*.md"),
        *(ROOT / "docs").rglob("*.md"),
        *(ROOT / "man").rglob("*.md"),
        ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md",
    }
    - set(_DOC_EXCLUDES)
)

# Flet 版本漂移检查范围（治理文档）
FLET_VERSION_DOCS: list[Path] = [CLAUDE_PATH, CONTRIBUTING_PATH, *FLET_DOCS_PATHS]

# Flet 包名（用于从 pyproject.toml 提取锁定版本）
# flet/flet-desktop/flet-charts/flet-code-editor 在 [project.dependencies]，
# flet-mcp 在 [project.optional-dependencies].dev（开发期 MCP 包，与主包版本对齐，见 CLAUDE.md §1.10）
_FLET_PACKAGES = ("flet", "flet-desktop", "flet-charts", "flet-code-editor", "flet-mcp")

# Flet 关键词附近版本号扫描窗口（前后字符数，spec 要求 50）
_FLET_KEYWORD_WINDOW = 50


def github_anchor(heading_text: str) -> str:
    """生成 GitHub 风格 markdown 锚点。

    规则：转小写 → 移除非 word/空格/连字符字符 → 每个空格独立转连字符（不折叠）。
    与 GitHub 渲染器行为一致（CJK 保留，标点/emoji/括号移除，连续空格 → 连续连字符）。
    例如 "3.1 ❌ 绝对禁止" → 移除 "." 和 "❌" 后得 "31  绝对禁止" → "31--绝对禁止"。
    """
    s = heading_text.lower()
    # \w 含字母数字下划线与 Unicode 字母（CJK）；re.UNICODE 默认开启
    s = re.sub(r"[^\w\s-]", "", s)
    # GitHub 不折叠连续空格，每个空格独立替换为连字符
    s = s.replace(" ", "-")
    return s


def extract_headings(content: str) -> set[str]:
    """提取 markdown 文件所有标题对应的锚点集合。"""
    anchors: set[str] = set()
    for line in content.splitlines():
        m = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if m:
            anchors.add(github_anchor(m.group(2)))
    return anchors


def check_anchor_dead_links() -> list[str]:
    """检查项 1：markdown 锚点死链。

    校验逻辑（spec §11.3 修复锚点逃逸）：
    1. 同文件锚点（`#anchor`）：直接校验锚点存在性。
    2. 跨文件链接（`./file.md#anchor`）：
       a. 先判断目标文件是否存在，不存在立即报错（不跳过）。
       b. 文件存在但不在 CHECKED_DOCS 中：跳过锚点校验（避免误报外部文档）。
       c. 文件存在且在 CHECKED_DOCS 中：校验锚点存在性。

    跳过 fenced code block（```...```）内的链接，避免代码示例被误判。
    """
    errors: list[str] = []
    # 预加载所有受检文件的标题集合
    doc_headings: dict[Path, set[str]] = {}
    for doc in CHECKED_DOCS:
        doc_headings[doc] = extract_headings(doc.read_text(encoding="utf-8"))

    # 匹配 markdown 链接 [text](url)，url 含 #anchor
    link_pattern = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")

    for doc in CHECKED_DOCS:
        content = doc.read_text(encoding="utf-8")
        in_code_block = False
        for line_no, line in enumerate(content.splitlines(), 1):
            # 跟踪 fenced code block 状态
            if line.lstrip().startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue
            for m in link_pattern.finditer(line):
                url = m.group(2).strip()
                # 忽略外部链接
                if url.startswith(("http://", "https://", "mailto:")):
                    continue
                # 只检查带锚点的链接
                if "#" not in url:
                    continue
                anchor = url.split("#", 1)[1]
                # 锚点为空（如 `[text](./file.md#)` ）跳过
                if not anchor:
                    continue

                # 提取目标文件路径部分（锚点前的部分）
                target_path_part = url.split("#", 1)[0]

                # 同文件锚点：直接校验锚点存在性
                if not target_path_part:
                    if anchor not in doc_headings.get(doc, set()):
                        errors.append(
                            f"{doc.name}:{line_no}: 锚点死链 '{url}' (锚点 '{anchor}' 在 {doc.name} 中不存在)"
                        )
                    continue

                # 跨文件链接：先检查目标文件存在性（spec §11.3 修复锚点逃逸）
                target_path = (doc.parent / target_path_part).resolve()
                if not target_path.exists():
                    errors.append(f"{doc.name}:{line_no}: 锚点死链 '{url}' (目标文件 '{target_path}' 不存在)")
                    continue

                # 文件存在但不在 CHECKED_DOCS 中：跳过锚点校验（避免误报外部文档）
                if target_path not in CHECKED_DOCS:
                    continue

                # 文件存在且在 CHECKED_DOCS 中：校验锚点存在性
                if anchor not in doc_headings.get(target_path, set()):
                    errors.append(
                        f"{doc.name}:{line_no}: 锚点死链 '{url}' (锚点 '{anchor}' 在 {target_path.name} 中不存在)"
                    )
    return errors


def check_relative_dead_links() -> list[str]:
    """检查项 6：相对链接死链（不含锚点的相对路径链接）。

    扫描 CHECKED_DOCS 中所有 markdown 链接 [text](url)，若 url 是相对路径
    （非 http/mailto，不含 # 锚点），从 source_doc 所在目录解析，若目标文件
    不存在则报错。

    跳过 fenced code block（```...```）内的链接，避免代码示例被误判。
    """
    errors: list[str] = []
    link_pattern = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")

    for doc in CHECKED_DOCS:
        content = doc.read_text(encoding="utf-8")
        in_code_block = False
        for line_no, line in enumerate(content.splitlines(), 1):
            if line.lstrip().startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue
            for m in link_pattern.finditer(line):
                url = m.group(2).strip()
                # 忽略外部链接
                if url.startswith(("http://", "https://", "mailto:")):
                    continue
                # 只检查不含锚点的相对路径链接（带锚点的由 check_anchor_dead_links 处理）
                if "#" in url:
                    continue
                # 从 source_doc 所在目录解析相对路径
                target = (doc.parent / url).resolve()
                if not target.exists():
                    errors.append(f"{doc.name}:{line_no}: 相对链接死链 '{url}' (目标 '{target}' 不存在)")
    return errors


def check_version_consistency() -> list[str]:
    """检查项 2：CLAUDE.md 顶部版本与 pyproject.toml 一致。"""
    errors: list[str] = []
    claude_content = CLAUDE_PATH.read_text(encoding="utf-8")
    m = re.search(r"\*\*对应版本\*\*[：:]\s*([0-9]+\.[0-9]+\.[0-9]+)", claude_content)
    if not m:
        errors.append("CLAUDE.md: 未找到 '**对应版本**' 字段")
        return errors
    claude_ver = m.group(1)

    with open(PYPROJECT_PATH, "rb") as f:
        cfg = tomllib.load(f)
    pyproject_ver = cfg["project"]["version"]

    if claude_ver != pyproject_ver:
        errors.append(f"CLAUDE.md 版本 {claude_ver} != pyproject.toml 版本 {pyproject_ver}")
    return errors


def _count_local_hooks() -> int:
    """计数 .pre-commit-config.yaml 中 local repo 下的 hook 数量。

    采用正则匹配 `^      - id:` 行（6 空格缩进 + dash + id:），
    与现有 verify_versions.py 风格一致，避免引入 yaml 依赖。
    """
    content = PRECOMMIT_PATH.read_text(encoding="utf-8")
    return len(re.findall(r"^ {6}- id: \S+", content, re.MULTILINE))


def check_precommit_hook_count() -> list[str]:
    """检查项 3：文档中 pre-commit hook 数量与配置一致。"""
    errors: list[str] = []
    actual_count = _count_local_hooks()

    for doc in CHECKED_DOCS:
        content = doc.read_text(encoding="utf-8")
        # 匹配"项目使用 N 个 pre-commit hook"或"使用 N 个 pre-commit hook"
        for m in re.finditer(r"(\d+)\s*个\s*pre-commit\s*hook", content):
            declared = int(m.group(1))
            if declared != actual_count:
                # 定位行号便于报错
                line_no = content[: m.start()].count("\n") + 1
                errors.append(
                    f"{doc.name}:{line_no}: 声明 {declared} 个 pre-commit hook，"
                    f"实际 .pre-commit-config.yaml 有 {actual_count} 个"
                )
    return errors


# NOTE(lazy) 三要素检查常量
NOTE_LAZY_PATTERN = re.compile(r"NOTE\(lazy\):")
# 单个 NOTE(lazy) 块向后扫描窗口上限（覆盖单行/多行 # 注释/docstring 多行场景）
# ceiling: 跨 20 行仍无 ceiling:/upgrade: 时认定为缺要素（实际样本最大跨度 7 行）.
# upgrade: 调整 NOTE(lazy) 描述风格或新增跨 20 行的块时复核上限.
NOTE_LAZY_SCAN_WINDOW = 20

# NOTE(lazy) 检查应跳过的目录（第三方代码、构建产物、worktree 副本等）
_NOTE_LAZY_SKIP_DIRS = frozenset(
    {
        "venv",
        ".venv",
        "__pycache__",
        ".git",
        "node_modules",
        ".worktrees",
        ".tmp",
        ".pytest_cache",
        ".ruff_cache",
        "build",
        "dist",
    }
)


def _find_note_lazy_blocks(content: str) -> list[tuple[int, str]]:
    """找到所有 NOTE(lazy) 块的 (起始行号 0-based, 块文本)。

    块边界：从 ``NOTE(lazy):`` 所在行开始，向后扫描最多 NOTE_LAZY_SCAN_WINDOW 行，
    遇到下一个 ``NOTE(lazy):`` 标记时截断（不含该行），避免吞下下一块的 ceiling/upgrade。

    跳过 fenced code block（```...```）内的 NOTE(lazy) 标记，避免代码示例误判。
    """
    lines = content.splitlines()
    in_code_block = False
    note_lazy_line_idxs: list[int] = []
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        if NOTE_LAZY_PATTERN.search(line):
            note_lazy_line_idxs.append(i)

    blocks: list[tuple[int, str]] = []
    for pos_idx, line_idx in enumerate(note_lazy_line_idxs):
        next_line_idx = note_lazy_line_idxs[pos_idx + 1] if pos_idx + 1 < len(note_lazy_line_idxs) else len(lines)
        block_end = min(line_idx + NOTE_LAZY_SCAN_WINDOW, next_line_idx)
        block_text = "\n".join(lines[line_idx:block_end])
        blocks.append((line_idx, block_text))
    return blocks


def _check_note_lazy_in_text(content: str) -> list[tuple[int, list[str]]]:
    """纯函数：检查给定文本中的 NOTE(lazy) 块，返回 (line_idx 0-based, missing_elements) 列表。

    missing_elements 取值：``"ceiling:"`` / ``"upgrade:"``（或两者）。
    """
    issues: list[tuple[int, list[str]]] = []
    for line_idx, block_text in _find_note_lazy_blocks(content):
        has_ceiling = "ceiling:" in block_text
        has_upgrade = "upgrade:" in block_text
        if not has_ceiling or not has_upgrade:
            missing: list[str] = []
            if not has_ceiling:
                missing.append("ceiling:")
            if not has_upgrade:
                missing.append("upgrade:")
            issues.append((line_idx, missing))
    return issues


def check_note_lazy_format() -> list[str]:
    """检查项 4：NOTE(lazy) 三要素格式检查（CLAUDE.md §3.3 要求）。

    扫描所有 .py 文件（排除第三方/构建产物/worktree 副本）中的 ``NOTE(lazy):`` 标记，
    校验后续块内是否含 ``ceiling:`` 与 ``upgrade:`` 两个关键字。

    支持格式：
    - 单行：所有三要素在 ``NOTE(lazy):`` 同行
    - 多行 # 注释：ceiling/upgrade 在后续 ``#`` 注释行
    - docstring 多行：ceiling/upgrade 在后续 docstring 行

    区分 NOTE(lazy) 与 ``# TODO:``：后者不匹配 ``NOTE\\(lazy\\):`` 正则，自然不被检查。
    """
    errors: list[str] = []
    self_path = Path(__file__).resolve()
    # 显式跳过专门测试 NOTE(lazy) 校验规则的测试文件，防止其单元测试用例中的演示文本被误判
    test_consistency_path = ROOT / "tests" / "unit" / "test_docs_consistency.py"

    for p in ROOT.rglob("*.py"):
        if any(part in _NOTE_LAZY_SKIP_DIRS for part in p.parts):
            continue
        if p in (self_path, test_consistency_path):
            # 跳过脚本自身以及专门的规则测试脚本
            continue
        try:
            content = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_idx, missing in _check_note_lazy_in_text(content):
            rel_path = p.relative_to(ROOT)
            errors.append(f"{rel_path}:{line_idx + 1}: NOTE(lazy) 缺少三要素: {', '.join(missing)}")
    return errors


def _get_flet_locked_versions() -> set[str]:
    """从 pyproject.toml 读取 flet/flet-desktop/flet-charts/flet-code-editor/flet-mcp 锁定版本。

    flet/flet-desktop/flet-charts/flet-code-editor 在 `[project.dependencies]`，
    flet-mcp 在 `[project.optional-dependencies].dev`（开发期 MCP 包，与主包版本对齐）。

    返回版本号集合（五包通常锁定同一版本，如 {"0.86.3"}）。
    """
    with open(PYPROJECT_PATH, "rb") as f:
        cfg = tomllib.load(f)
    versions: set[str] = set()
    # 合并运行时依赖与全部可选依赖组（dev/optional 等），覆盖 flet-mcp 在 dev 中的场景
    deps: list[str] = list(cfg["project"]["dependencies"])
    for extra_group in cfg.get("project", {}).get("optional-dependencies", {}).values():
        deps.extend(extra_group)
    for dep in deps:
        for pkg in _FLET_PACKAGES:
            m = re.match(rf"{re.escape(pkg)}==(\d+\.\d+\.\d+)", dep.strip())
            if m:
                versions.add(m.group(1))
    return versions


def check_flet_version_drift() -> list[str]:
    """检查项 5：Flet 版本漂移检查（CLAUDE.md §3.2 文档 SHALL NOT 硬编码 Flet 补丁版本号）。

    扫描治理文档中 Flet 关键词附近（前后 _FLET_KEYWORD_WINDOW 字符内）的 `\\d+.\\d+.\\d+` 版本号。
    根据规范，任何在 Flet 上下文中出现的具体补丁版本号都应报错（不论是否与 pyproject.toml 锁定版本一致）。

    报错格式：``{doc.name}:{line_no}: Flet 版本漂移：文档声明 {doc_ver}，pyproject.toml 锁定 {actual_ver}``
    """
    errors: list[str] = []
    locked_versions = _get_flet_locked_versions()
    # 取代表版本（三包通常锁定同一版本）用于报错信息
    actual_ver = next(iter(locked_versions)) if locked_versions else "unknown"

    version_pattern = re.compile(r"\b\d+\.\d+\.\d+\b")
    # Flet 关键词正则：匹配 "Flet" 或 "flet"（word boundary 防止匹配 "fletch" 等）
    flet_keyword_pattern = re.compile(r"\b[Ff]let\b")

    for doc in FLET_VERSION_DOCS:
        content = doc.read_text(encoding="utf-8")
        for line_no, line in enumerate(content.splitlines(), 1):
            for v_match in version_pattern.finditer(line):
                doc_ver = v_match.group()
                # 检查版本号前后 _FLET_KEYWORD_WINDOW 字符内是否有 Flet 关键词
                start = max(0, v_match.start() - _FLET_KEYWORD_WINDOW)
                end = min(len(line), v_match.end() + _FLET_KEYWORD_WINDOW)
                window = line[start:end]
                if flet_keyword_pattern.search(window):
                    errors.append(
                        f"{doc.name}:{line_no}: Flet 版本漂移：文档声明 {doc_ver}，pyproject.toml 锁定 {actual_ver}"
                    )
    return errors


# =============================================================================
# Flet 入口完整性检查（spec §11.2）
#
# 校验 docs/flet/README.md 覆盖全部 docs/flet/*.md 专题文档（除 README.md 自身），
# 且不引用不存在的专题文件。新增 docs/flet/*.md 未登记到 README 时 fail closed。
# =============================================================================

# markdown 链接正则：[text](url)，url 为相对路径（含 ./ 前缀或纯文件名）
_MD_LINK_PATTERN = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


def check_flet_hub_completeness() -> list[str]:
    """检查项 9：Flet 入口完整性（spec §11.2）。

    校验 docs/flet/README.md 是否覆盖全部 docs/flet/*.md 专题文档：
    1. 枚举 docs/flet/*.md（排除 README.md 自身）。
    2. 检查 README 是否链接每个专题文件（通过文件名在 markdown 链接 url 中出现）。
    3. 检查 README 是否引用不存在的专题文件（防止幽灵链接）。
    4. 文件名大小写必须一致。

    仅检查指向 docs/flet/ 目录内 .md 文件的链接，忽略指向外部目录的链接
    （如 ../../CLAUDE.md、../patterns/mvvm.md 等）。

    返回错误列表（空列表表示通过）。
    """
    errors: list[str] = []

    if not FLET_HUB_PATH.exists():
        errors.append(f"Flet 入口文件不存在: {FLET_HUB_PATH}")
        return errors

    readme_content = FLET_HUB_PATH.read_text(encoding="utf-8")

    # 枚举 docs/flet/*.md 实际文件（排除 README.md 自身）
    actual_files: set[str] = set()
    for flet_doc in FLET_DOCS_PATHS:
        if flet_doc.name == "README.md":
            continue
        actual_files.add(flet_doc.name)

    # 提取 README 中所有 markdown 链接的 url，筛选指向 docs/flet/ 目录内 .md 文件的链接
    # 链接 url 形态：./xxx.md / xxx.md / ./subdir/xxx.md 等（相对 FLET_HUB_PATH 所在目录）
    referenced_files: set[str] = set()
    in_code_block = False
    for line in readme_content.splitlines():
        if line.lstrip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        for m in _MD_LINK_PATTERN.finditer(line):
            url = m.group(2).strip()
            # 忽略外部链接
            if url.startswith(("http://", "https://", "mailto:")):
                continue
            # 提取文件名部分（去掉锚点和查询参数）
            url_path = url.split("#", 1)[0].split("?", 1)[0]
            if not url_path:
                continue
            # 解析目标文件路径（相对 FLET_HUB_PATH 所在目录）
            target_path = (FLET_HUB_PATH.parent / url_path).resolve()
            # 只检查解析后仍在 FLET_DOCS_DIR 目录内的链接（排除 ../CLAUDE.md 等外部链接）
            try:
                target_path.relative_to(FLET_DOCS_DIR)
            except ValueError:
                # 目标在 docs/flet/ 目录外，跳过（不属于 Flet 专题文档）
                continue
            url_basename = target_path.name
            if url_basename.endswith(".md") and url_basename != "README.md":
                referenced_files.add(url_basename)

    # 检查 1：README 是否覆盖全部专题文件
    missing_in_readme = actual_files - referenced_files
    for fname in sorted(missing_in_readme):
        errors.append(f"Flet 入口完整性：{FLET_HUB_PATH.name} 未链接专题文档 '{fname}'")

    # 检查 2：README 是否引用不存在的专题文件（幽灵链接）
    phantom_files = referenced_files - actual_files
    for fname in sorted(phantom_files):
        errors.append(f"Flet 入口完整性：{FLET_HUB_PATH.name} 引用了不存在的专题文档 '{fname}'")

    return errors


# redlines.yml 字段完整性校验常量
REDLINE_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {"id", "title", "description", "enforcement", "automation_coverage", "human_review_required", "rule_type"}
)
# automation_coverage 合法值（ADR-0005 N6/N8 一致性校验基础）
AUTOMATION_COVERAGE_VALUES: frozenset[str] = frozenset({"full", "partial", "none"})
# rule_type 合法值（P2-11 规则类型标记）
RULE_TYPE_VALUES: frozenset[str] = frozenset(
    {"INVARIANT", "DEFAULT", "NEW_CODE", "MIGRATION_TARGET", "WORKFLOW", "EXCEPTIONABLE"}
)
# R 编号格式正则: R1 ~ R999 (append-only, 不复用废弃编号)
REDLINE_ID_PATTERN = re.compile(r"^R(\d+)$")
# CLAUDE.md §3.1 红线表行匹配: 以 `| R\d+ |` 开头的 markdown 表格行
CLAUDE_REDLINE_TABLE_ROW_PATTERN = re.compile(r"^\|\s*R\d+\s*\|")


def _normalize_for_comparison(text: str) -> str:
    """标准化文本用于 CLAUDE.md 与 YAML 字段语义比较。

    规范化步骤：
    1. 反转义 markdown 表格中的 ``\\|`` 为 ``|``（CLAUDE.md 表格转义管道符）
    2. 移除 markdown 粗体标记 ``**``（CLAUDE.md 标题列使用 ``**title**``）
    3. 移除 markdown 行内代码标记 `` ` ``（CLAUDE.md 描述列使用 `` `code` ``）
    4. 移除两端引号（YAML 字符串可能带 ``"`` 或 ``'``）
    5. strip 首尾空白

    纯函数，便于单元测试。
    """
    # 1. 反转义 markdown 表格转义管道符
    s = text.replace("\\|", "|")
    # 2. 移除粗体标记
    s = s.replace("**", "")
    # 3. 移除行内代码标记
    s = s.replace("`", "")
    # 4. 移除两端引号（循环处理嵌套引号场景，如 "'value'"）
    while len(s) >= 2 and s[0] in "\"'" and s[-1] == s[0]:
        s = s[1:-1]
    # 5. strip 首尾空白
    return s.strip()


def _parse_claude_redline_table(claude_content: str) -> dict[str, dict[str, str]]:
    """解析 CLAUDE.md §3.1 红线表，返回 {id: {title, description, enforcement}} 映射。

    表格行格式: ``| R1 | **title** | description | enforcement |``
    列之间用 ``|`` 分隔，描述列可能含转义管道符 ``\\|``（如 R6 的 ``X \\| Y``）。

    解析策略:
    1. 匹配以 ``| R\\d+ |`` 开头的行
    2. 按 ``(?<!\\\\)\\|`` 分割（不分割转义管道符）
    3. 预期 6 段（首尾空 + id + title + description + enforcement）
    4. 对每个字段做 ``_normalize_for_comparison()`` 标准化

    纯函数，便于单元测试。
    """
    result: dict[str, dict[str, str]] = {}
    for line in claude_content.splitlines():
        if not CLAUDE_REDLINE_TABLE_ROW_PATTERN.match(line):
            continue
        # 按非转义管道符分割
        parts = re.split(r"(?<!\\)\|", line)
        if len(parts) < 6:
            continue
        # parts[0] 和 parts[-1] 为首尾空串，中间 4 列为 id/title/description/enforcement
        rid = _normalize_for_comparison(parts[1])
        title = _normalize_for_comparison(parts[2])
        description = _normalize_for_comparison(parts[3])
        enforcement = _normalize_for_comparison(parts[4])
        id_match = REDLINE_ID_PATTERN.match(rid)
        if not id_match:
            continue
        result[rid] = {
            "title": title,
            "description": description,
            "enforcement": enforcement,
        }
    return result


def check_redlines_yaml_consistency() -> list[str]:
    """检查项 7：redlines.yml 与 CLAUDE.md §3.1 红线表一致性（ADR-0003 决策落地）。

    校验:
    1. redlines.yml 可被 yaml.safe_load 解析, 顶层为 dict, 含 "redlines" key (list)
    2. 每条红线含 7 字段: id/title/description/enforcement/automation_coverage/human_review_required/rule_type
    3. id 格式为 R\\d+, 连续 append-only (R1, R2, ..., R_N, 无缺号/重号/跳号)
    4. CLAUDE.md §3.1 红线表行数 (以 ``| R\\d+ |`` 开头的行) = yml 条目数
    5. automation_coverage 值校验: 必须为 full/partial/none 之一
    6. automation_coverage 与 human_review_required 一致性:
       automation_coverage != full ⇒ human_review_required == true
       automation_coverage == full ⇒ human_review_required == false
    7. CLAUDE.md §3.1 表格与 YAML 字段语义一致: id/title/description/enforcement 四字段
       标准化比较（strip 空白、移除两端引号、移除 markdown 标记后比较）

    退出码: 0 通过, 1 失败 (返回非空 errors 列表)。
    """
    errors: list[str] = []

    if not REDLINES_YAML_PATH.exists():
        errors.append(f"redlines.yml 不存在: {REDLINES_YAML_PATH}")
        return errors

    try:
        import yaml  # 延迟 import: PyYAML 是 transitive 依赖, 避免未安装时影响其他检查
    except ImportError:
        errors.append("PyYAML 未安装, 无法解析 redlines.yml (检查 requirements*.txt)")
        return errors

    try:
        data = yaml.safe_load(REDLINES_YAML_PATH.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        errors.append(f"redlines.yml YAML 解析失败: {e}")
        return errors

    if not isinstance(data, dict):
        errors.append(f"redlines.yml 顶层应为 dict, 实际 {type(data).__name__}")
        return errors

    if "redlines" not in data:
        errors.append("redlines.yml 顶层应含 'redlines' key")
        return errors

    redlines = data["redlines"]
    if not isinstance(redlines, list):
        errors.append(f"'redlines' 应为 list, 实际 {type(redlines).__name__}")
        return errors

    # 校验 2: 字段完整性
    for i, entry in enumerate(redlines):
        if not isinstance(entry, dict):
            errors.append(f"redlines[{i}] 应为 dict, 实际 {type(entry).__name__}")
            continue
        missing = REDLINE_REQUIRED_FIELDS - set(entry.keys())
        if missing:
            errors.append(f"redlines[{i}] 缺字段: {sorted(missing)}")

    # 校验 2b: automation_coverage 值校验 + 与 human_review_required 一致性
    for i, entry in enumerate(redlines):
        if not isinstance(entry, dict):
            continue
        automation_coverage = entry.get("automation_coverage")
        if automation_coverage is None:
            continue  # 字段缺失由校验 2 守护
        rid = str(entry.get("id", f"redlines[{i}]"))
        if automation_coverage not in AUTOMATION_COVERAGE_VALUES:
            errors.append(f"{rid}: automation_coverage 值非法: {automation_coverage} (应为 full/partial/none)")
            continue
        human_review = entry.get("human_review_required")
        if human_review is None:
            continue  # 字段缺失由校验 2 守护
        if automation_coverage != "full" and not human_review:
            errors.append(f"{rid}: automation_coverage='{automation_coverage}' 但 human_review_required=false")
        if automation_coverage == "full" and human_review:
            errors.append(f"{rid}: automation_coverage='full' 但 human_review_required=true")

    # 校验 2c: rule_type 值合法性 + EXCEPTIONABLE 与例外注册表联动 (P2-11)
    for i, entry in enumerate(redlines):
        if not isinstance(entry, dict):
            continue
        rule_type = entry.get("rule_type")
        rid = str(entry.get("id", f"redlines[{i}]"))
        if rule_type is None:
            continue  # 字段缺失由校验 2 守护
        if rule_type not in RULE_TYPE_VALUES:
            errors.append(f"{rid}: rule_type 值非法: {rule_type} (应为 {sorted(RULE_TYPE_VALUES)})")

    # EXCEPTIONABLE 联动: 例外注册表中引用的 rule_id 必须为 EXCEPTIONABLE 规则
    exceptionable_ids = {
        str(e.get("id")) for e in redlines if isinstance(e, dict) and e.get("rule_type") == "EXCEPTIONABLE"
    }
    try:
        import yaml  # noqa: F811

        exc_data = yaml.safe_load(EXCEPTIONS_YAML_PATH.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        exc_data = None
    if isinstance(exc_data, dict) and isinstance(exc_data.get("exceptions"), list):
        for entry in exc_data["exceptions"]:
            if not isinstance(entry, dict):
                continue
            rule_id = entry.get("rule_id")
            if rule_id is not None and rule_id not in exceptionable_ids:
                errors.append(f"例外 {entry.get('id')} 引用的 rule_id '{rule_id}' 不是 EXCEPTIONABLE 规则")

    # 校验 3: id 格式 + 连续 append-only
    parsed_nums: list[int] = []
    for i, entry in enumerate(redlines):
        if not isinstance(entry, dict) or "id" not in entry:
            continue
        rid = entry["id"]
        m = REDLINE_ID_PATTERN.match(str(rid))
        if not m:
            errors.append(f"redlines[{i}] id 格式错误: {rid} (应为 R\\d+)")
            continue
        parsed_nums.append(int(m.group(1)))

    # 无重号
    if len(parsed_nums) != len(set(parsed_nums)):
        duplicates = sorted({n for n in parsed_nums if parsed_nums.count(n) > 1})
        errors.append(f"redlines.yml R 编号有重号: {duplicates}")

    # 连续 append-only: 1, 2, ..., N (无缺号/跳号)
    if parsed_nums:
        expected_set = set(range(1, len(parsed_nums) + 1))
        actual_set = set(parsed_nums)
        missing_nums = sorted(expected_set - actual_set)
        extra_nums = sorted(actual_set - expected_set)
        if missing_nums:
            missing_ids = [f"R{n}" for n in missing_nums]
            errors.append(f"redlines.yml R 编号缺号 (append-only 违规): 缺 {missing_ids}")
        if extra_nums:
            extra_ids = [f"R{n}" for n in extra_nums]
            errors.append(f"redlines.yml R 编号超出连续范围: 多 {extra_ids}")

    # 校验 4: CLAUDE.md §3.1 表格行数 = yml 条目数
    claude_content = CLAUDE_PATH.read_text(encoding="utf-8")
    r_lines = [line for line in claude_content.splitlines() if CLAUDE_REDLINE_TABLE_ROW_PATTERN.match(line)]
    if len(r_lines) != len(redlines):
        errors.append(f"CLAUDE.md §3.1 表格行数 {len(r_lines)} != redlines.yml 条目数 {len(redlines)}")

    # 校验 5: CLAUDE.md §3.1 表格与 YAML 字段语义一致
    # 解析 CLAUDE.md 红线表，提取 id/title/description/enforcement 四字段
    # 与 YAML 中对应条目的同名字段做标准化比较（strip 空白、移除两端引号、移除 markdown 标记后比较）
    claude_table = _parse_claude_redline_table(claude_content)
    for entry in redlines:
        if not isinstance(entry, dict):
            continue
        rid = str(entry.get("id", "?"))
        if rid not in claude_table:
            continue  # 行数不匹配已由校验 4 报告
        claude_entry = claude_table[rid]
        for field in ("title", "description", "enforcement"):
            yaml_value = _normalize_for_comparison(str(entry.get(field, "")))
            claude_value = claude_entry[field]
            if yaml_value != claude_value:
                errors.append(f"{rid}: CLAUDE.md 与 redlines.yml 字段 '{field}' 不一致")

    return errors


# =============================================================================
# 3c: enforcement 字段与实际 hook/CI job 映射一致性检查（ADR-0005）
#
# 8 个不变量 N1~N8 守护 enforcement 字段声称的守护机制配置存在且粗粒度可达。
# 核心校验 _check_enforcement_invariants() 为纯函数，接受 redlines 列表与
# EnforcementEnvironment 配置快照，不读文件，便于单元测试构造正例/反例。
# 实际文件读取集中在 _collect_enforcement_environment()。
#
# 已知漏检场景（3c 范围外，由人工评审兜底）：
# - R3 enforcement="pre-commit"（无具体 hook 名）：R3 yml 精确化为独立跟进任务
# - 删除 docs-consistency hook 本身：meta 悎论，守护者无法守护自己
# - R2/R7/R8 特定守护测试用例被删除：3c 根本限制，机器无法校验特定测试存在
# - Hook files 过滤器收窄导致 hook 不触发：属 hook 配置审查范畴
# - CI job if: 条件禁用：属 CI 配置审查范畴
# =============================================================================

# enforcement 字段关键词常量
ENFORCEMENT_KEYWORD_CHECK_REDLINES = "check_redlines.py"
ENFORCEMENT_KEYWORD_IMPORT_LINTER = "import-linter"
ENFORCEMENT_KEYWORD_SECURITY_SCAN = "安全扫描"
ENFORCEMENT_KEYWORD_CI_TEST = "CI-test"
ENFORCEMENT_KEYWORD_HUMAN_REVIEW = (
    "仅人工评审"  # 保留用于 keywords 提取；N6/N8 演进后不再被不变量消费（见 ADR-0005 Errata 2026-08-13）
)
ENFORCEMENT_KEYWORD_PENDING: tuple[str, ...] = ("待实现", "暂缓")  # R16 特例

# ruff 关键词使用 word boundary 匹配，避免误匹配 'scruffian' 等
RUFF_KEYWORD_PATTERN = re.compile(r"\bruff\b", re.IGNORECASE)

# import-linter 契约数量正则（从 enforcement 文本解析期望数量，如 "6 条契约"）
IMPORT_LINTER_CONTRACT_COUNT_PATTERN = re.compile(r"(\d+)\s*条契约")
# pyproject.toml 中 import-linter 契约 section 起始标记
IMPORT_LINTER_CONTRACT_SECTION_PATTERN = re.compile(r"^\[\[tool\.importlinter\.contracts\]\]", re.MULTILINE)

# pytest 命令正则：仅匹配 run: 命令块中以 pytest 开头的命令行
# 语法：行首 + 任意空格 + 可选 'python -m ' / 'python3 -m ' 前缀 + 'pytest' + 空格或行尾
# 避免误匹配 'pip install pytest'（pytest 不在行首）和 'Cache pytest'（非命令文本）
PYTEST_COMMAND_PATTERN = re.compile(
    r"^\s*(?:python[0-9]*\s+-m\s+)?pytest(?:\s|$)",
    re.MULTILINE,
)

# Gitleaks action 名称正则（GitHub Actions workflow 中识别 Gitleaks secret scan）
GITLEAKS_ACTION_PATTERN = re.compile(r"gitleaks/gitleaks-action", re.IGNORECASE)

# CI workflow glob 模式（扫描全部 workflow 文件，GitHub Actions 同时支持 .yml / .yaml）
CI_WORKFLOW_GLOBS: tuple[str, ...] = ("*.yml", "*.yaml")


@dataclass(frozen=True)
class EnforcementEnvironment:
    """3c 不变量校验所需的项目配置快照。

    所有字段在 _collect_enforcement_environment() 中一次性收集，
    _check_enforcement_invariants() 接受此快照后不再读文件系统。
    """

    precommit_content: str
    workflow_contents: tuple[str, ...]
    pyproject_content: str
    check_redlines_script_exists: bool
    gitleaks_config_exists: bool


def _extract_enforcement_keywords(enforcement: str) -> set[str]:
    """从 enforcement 文本中提取守护机制关键词集合。

    纯函数，便于单元测试。

    匹配规则：
    - 中文关键词（安全扫描/仅人工评审/待实现/暂缓）：in 子串匹配
    - 英文关键词 ruff：word boundary 正则匹配
    - 含特殊字符关键词（check_redlines.py/import-linter/CI-test）：in 子串匹配
    """
    keywords: set[str] = set()
    if ENFORCEMENT_KEYWORD_CHECK_REDLINES in enforcement:
        keywords.add(ENFORCEMENT_KEYWORD_CHECK_REDLINES)
    if ENFORCEMENT_KEYWORD_IMPORT_LINTER in enforcement:
        keywords.add(ENFORCEMENT_KEYWORD_IMPORT_LINTER)
    if ENFORCEMENT_KEYWORD_SECURITY_SCAN in enforcement:
        keywords.add(ENFORCEMENT_KEYWORD_SECURITY_SCAN)
    if ENFORCEMENT_KEYWORD_CI_TEST in enforcement:
        keywords.add(ENFORCEMENT_KEYWORD_CI_TEST)
    if ENFORCEMENT_KEYWORD_HUMAN_REVIEW in enforcement:
        keywords.add(ENFORCEMENT_KEYWORD_HUMAN_REVIEW)
    for pending in ENFORCEMENT_KEYWORD_PENDING:
        if pending in enforcement:
            keywords.add(pending)
    if RUFF_KEYWORD_PATTERN.search(enforcement):
        keywords.add("ruff")
    return keywords


def _check_precommit_hook(
    precommit_content: str,
    hook_id: str,
    entry_keyword: str,
) -> bool:
    """检查 pre-commit 内容是否含指定 id 的 local hook，且 entry 字段含 entry_keyword。

    匹配风格与 _count_local_hooks() 一致：`^ {6}- id: <hook_id>` 行（6 空格缩进）。
    """
    # 注意：f-string 中 {6} 会被当作表达式求值，必须用字面 6 空格或 {{6}} 转义。
    # 这里用字面 6 空格，与 _count_local_hooks() 的 r"^ {6}- id: \S+" 风格一致。
    hook_pattern = re.compile(rf"^      - id: {re.escape(hook_id)}\s*$", re.MULTILINE)
    m = hook_pattern.search(precommit_content)
    if not m:
        return False
    # 从 hook 行结束位置扫描到下一个 `- id:` 或文件末尾，提取 hook 块
    start = m.end()
    next_hook = re.search(r"^      - id: \S+", precommit_content[start:], re.MULTILINE)
    end = start + next_hook.start() if next_hook else len(precommit_content)
    hook_block = precommit_content[start:end]
    entry_match = re.search(r"^\s*entry:\s*(.+)$", hook_block, re.MULTILINE)
    if not entry_match:
        return False
    return entry_keyword in entry_match.group(1)


def _extract_workflow_run_blocks(workflow_content: str) -> list[str]:
    """提取 GitHub Actions workflow 中的 run: 命令块。

    支持 4 种 YAML 风格：
    1. run: pytest（单行无引号）
    2. run: python -m pytest tests/unit/（单行带参数）
    3. run: | + 多行命令块（块状字面量）
    4. run: >- + 多行折叠块（折叠去尾换行）

    用轻量缩进扫描而非完整 YAML 解析，避免 GitHub Actions 表达式带来的解析兼容成本。
    """
    blocks: list[str] = []
    lines = workflow_content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        # GitHub Actions 中 run: 通常写作 `- run: cmd`，因此正则需允许 `- ` 前缀。
        # run_indent 为 `run:` 关键字所在列（含 `- ` 前缀的总缩进），用于判断块字面量后续行的缩进深度。
        m = re.match(r"^(\s*(?:-\s+)?)run:\s*(.*)$", line)
        if not m:
            i += 1
            continue
        prefix_str, rest = m.group(1), m.group(2)
        run_indent = len(prefix_str)
        if rest in ("|", "|-", "|+", ">", ">-", ">+"):
            # 块状字面量 / 折叠块：收集后续更深层缩进的行
            block_lines: list[str] = []
            i += 1
            while i < len(lines):
                next_line = lines[i]
                if not next_line.strip() or next_line.strip().startswith("#"):
                    block_lines.append(next_line)
                    i += 1
                    continue
                next_indent = len(next_line) - len(next_line.lstrip())
                if next_indent > run_indent:
                    block_lines.append(next_line)
                    i += 1
                else:
                    break
            blocks.append("\n".join(block_lines))
        elif rest:
            # 单行命令：rest 即命令
            blocks.append(rest)
            i += 1
        else:
            # run: 后为空（罕见），跳过
            i += 1
    return blocks


def _check_enforcement_invariants(redlines: list[dict], env: EnforcementEnvironment) -> list[str]:
    """纯函数：对已解析的 redlines 列表与配置快照校验 8 个不变量，返回错误列表。

    不变量清单（v4，8 项；原 N9 在实施后检视中删除——与 N6 触发条件等价仅操作数顺序不同）：
    - N1: enforcement 含 'check_redlines.py' ⇒ redline-check hook 存在 + entry 含 check_redlines.py + 脚本文件存在
    - N2: enforcement 含 'import-linter' ⇒ lint-imports hook 存在 + entry 含 lint-imports + 契约数量一致
    - N3: enforcement 含 'ruff' ⇒ ruff-check hook 存在 + entry 含 ruff
    - N4: enforcement 含 '安全扫描' ⇒ Gitleaks workflow + .gitleaks.toml 同时存在
    - N5: enforcement 含 'CI-test' ⇒ workflow run: 命令块含 pytest 命令
    - N6: automation_coverage != full ⇒ human_review_required == true
    - N7: enforcement 含 '待实现'/'暂缓' ⇒ automation_coverage == none 且 human_review_required == true（R16 特化守护）
    - N8: human_review_required == true ⇒ automation_coverage != full

    N6 + N8 共同构成 `automation_coverage != full ⇔ human_review_required == true` 双向一致性。
    N7 仍然检查 enforcement 文本中的 '待实现'/'暂缓' 关键词，但现在要求 automation_coverage == none
    且 human_review_required == true（与 N8 的 automation_coverage != full 要求一致）。

    使用 .get() 防御性访问 human_review_required / automation_coverage 字段；字段缺失时跳过 N6~N8
    （由 3b 守护字段完整性）。
    """
    errors: list[str] = []
    for entry in redlines:
        if not isinstance(entry, dict):
            continue
        rid = str(entry.get("id", "?"))
        enforcement = str(entry.get("enforcement", ""))
        # human_review_required 可能是 None（字段缺失）/ True / False
        human_review = entry.get("human_review_required")
        keywords = _extract_enforcement_keywords(enforcement)

        # N1: check_redlines.py
        if ENFORCEMENT_KEYWORD_CHECK_REDLINES in keywords:
            if not _check_precommit_hook(env.precommit_content, "redline-check", "check_redlines.py"):
                errors.append(
                    f"{rid}: N1 enforcement 含 'check_redlines.py' 但 redline-check hook 不存在或 entry 不含 check_redlines.py"
                )
            elif not env.check_redlines_script_exists:
                errors.append(f"{rid}: N1 enforcement 含 'check_redlines.py' 但 scripts/check_redlines.py 文件不存在")

        # N2: import-linter
        if ENFORCEMENT_KEYWORD_IMPORT_LINTER in keywords:
            if not _check_precommit_hook(env.precommit_content, "lint-imports", "lint-imports"):
                errors.append(
                    f"{rid}: N2 enforcement 含 'import-linter' 但 lint-imports hook 不存在或 entry 不含 lint-imports"
                )
            else:
                # 契约数量校验（enforcement 含『N 条契约』描述时才校验）
                count_match = IMPORT_LINTER_CONTRACT_COUNT_PATTERN.search(enforcement)
                if count_match:
                    expected = int(count_match.group(1))
                    actual = len(IMPORT_LINTER_CONTRACT_SECTION_PATTERN.findall(env.pyproject_content))
                    if expected != actual:
                        errors.append(f"{rid}: N2 enforcement 声明 {expected} 条契约，pyproject.toml 实际 {actual} 条")

        # N3: ruff（word boundary 匹配）
        if "ruff" in keywords:
            if not _check_precommit_hook(env.precommit_content, "ruff-check", "ruff"):
                errors.append(f"{rid}: N3 enforcement 含 'ruff' 但 ruff-check hook 不存在或 entry 不含 ruff")

        # N4: 安全扫描（R9/R10 enforcement 含「安全扫描」要求 Gitleaks workflow + .gitleaks.toml 同时存在；
        # pip-audit 不作为证据——依赖安全审计 ≠ 密钥/敏感信息泄露扫描）
        if ENFORCEMENT_KEYWORD_SECURITY_SCAN in keywords:
            gitleaks_ok = env.gitleaks_config_exists and any(
                GITLEAKS_ACTION_PATTERN.search(content) for content in env.workflow_contents
            )
            if not gitleaks_ok:
                errors.append(
                    f"{rid}: N4 enforcement 含 '安全扫描' 但未检测到 Gitleaks workflow 与 .gitleaks.toml 同时存在"
                )

        # N5: CI-test（在任一 workflow 的 run: 命令块中检测 pytest 命令）
        if ENFORCEMENT_KEYWORD_CI_TEST in keywords:
            pytest_ok = any(
                PYTEST_COMMAND_PATTERN.search(block)
                for content in env.workflow_contents
                for block in _extract_workflow_run_blocks(content)
            )
            if not pytest_ok:
                errors.append(f"{rid}: N5 enforcement 含 'CI-test' 但 workflow run: 命令块未检测到 pytest 命令")

        # N6~N8: automation_coverage 与 human_review_required 一致性校验
        # 字段缺失时跳过（由 3b check_redlines_yaml_consistency() 守护字段完整性）
        automation_coverage = entry.get("automation_coverage")
        if human_review is not None and automation_coverage is not None:
            # N6: automation_coverage != full ⇒ human_review_required == true
            if automation_coverage != "full" and not human_review:
                errors.append(f"{rid}: N6 automation_coverage='{automation_coverage}' 但 human_review_required=false")
            # N7: 待实现/暂缓 ⇒ automation_coverage == none 且 human_review_required == true（R16 特化守护）
            if any(p in keywords for p in ENFORCEMENT_KEYWORD_PENDING):
                if automation_coverage != "none" or not human_review:
                    errors.append(
                        f"{rid}: N7 enforcement 含 '待实现/暂缓' 但 automation_coverage!='none' 或 human_review_required!=true"
                    )
            # N8: human_review_required == true ⇒ automation_coverage != full
            if human_review and automation_coverage == "full":
                errors.append(f"{rid}: N8 human_review_required=true 但 automation_coverage='full'")

    return errors


def _collect_enforcement_environment() -> EnforcementEnvironment:
    """读取 .pre-commit-config.yaml、workflow、pyproject.toml 与脚本存在性，生成配置快照。

    异常处理策略（v3 §14.3）：
    - OSError / PermissionError 硬失败：直接抛出，由 main() 传播，脚本以非零退出码退出。
    - 禁止 try/except 吞没 OSError（避免漂移静默漏检）。
    - 所有路径访问必须且仅通过模块级路径常量（PRECOMMIT_PATH / PYPROJECT_PATH /
      CI_WORKFLOW_DIR / CHECK_REDLINES_SCRIPT_PATH / GITLEAKS_CONFIG_PATH），
      确保测试 monkeypatch 生效。
    """
    precommit_content = PRECOMMIT_PATH.read_text(encoding="utf-8")
    pyproject_content = PYPROJECT_PATH.read_text(encoding="utf-8")

    workflow_contents_list: list[str] = []
    for pattern in CI_WORKFLOW_GLOBS:
        for wf_path in CI_WORKFLOW_DIR.glob(pattern):
            workflow_contents_list.append(wf_path.read_text(encoding="utf-8"))
    workflow_contents = tuple(workflow_contents_list)

    return EnforcementEnvironment(
        precommit_content=precommit_content,
        workflow_contents=workflow_contents,
        pyproject_content=pyproject_content,
        check_redlines_script_exists=CHECK_REDLINES_SCRIPT_PATH.exists(),
        gitleaks_config_exists=GITLEAKS_CONFIG_PATH.exists(),
    )


def check_enforcement_mapping() -> list[str]:
    """检查项 8: enforcement 字段与实际 hook/CI job 映射一致性（3c 落地，见 ADR-0005）。

    读取 redlines.yml + .pre-commit-config.yaml + .github/workflows/*.yml/*.yaml + pyproject.toml,
    校验 enforcement 字段中声称的守护机制配置存在且粗粒度可达。

    独立解析 yml，不依赖 check_redlines_yaml_consistency() 的执行顺序。
    yml 解析失败时返回精确错误（允许与 3b 重复报错）。

    异常处理策略：
    - 环境收集失败（PermissionError / OSError）时硬失败：抛异常传播到 main()，
      脚本以非零退出码退出。禁止 try/except 吞没异常（避免漂移静默漏检）。
    - yml 解析失败时返回精确错误列表（与 3b 一致，允许重复报错）。
    - 不变量校验失败时返回错误列表（不抛异常）。
    """
    errors: list[str] = []

    if not REDLINES_YAML_PATH.exists():
        errors.append(f"redlines.yml 不存在: {REDLINES_YAML_PATH}")
        return errors

    try:
        import yaml  # 延迟 import: PyYAML 是 transitive 依赖
    except ImportError:
        errors.append("PyYAML 未安装, 无法解析 redlines.yml (检查 requirements*.txt)")
        return errors

    try:
        data = yaml.safe_load(REDLINES_YAML_PATH.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        errors.append(f"redlines.yml YAML 解析失败: {e}")
        return errors

    if not isinstance(data, dict) or "redlines" not in data:
        errors.append("redlines.yml 顶层应为 dict 且含 'redlines' key")
        return errors

    redlines = data["redlines"]
    if not isinstance(redlines, list):
        errors.append(f"'redlines' 应为 list, 实际 {type(redlines).__name__}")
        return errors

    # 环境收集（硬失败：抛 OSError 传播到 main()）
    env = _collect_enforcement_environment()

    # 不变量校验
    errors.extend(_check_enforcement_invariants(redlines, env))

    return errors


# 例外注册表必填字段 (P1-01: 集中例外治理, 见 docs/governance/exceptions.yml)
EXCEPTION_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {"id", "rule_id", "paths", "reason", "owner", "approved_by", "verification"}
)
# expires_at 与 removal_trigger 二选一必填
EXCEPTION_EXPIRY_FIELDS: frozenset[str] = frozenset({"expires_at", "removal_trigger"})


def check_exceptions_yaml_consistency() -> list[str]:
    """例外注册表一致性检查 (P1-01)。

    校验 docs/governance/exceptions.yml：
    1. 可被 yaml.safe_load 解析, 顶层为 dict, 含 "exceptions" key (list)。
    2. 每条例外必填字段齐全 (id/rule_id/paths/reason/owner/approved_by/verification)。
    3. expires_at 与 removal_trigger 二选一必填。
    4. id 唯一且格式为 EX-XXXX。
    5. rule_id 必须存在于 docs/governance/redlines.yml。
    6. paths 必须为 list 且每个路径在仓库中真实存在。
    """
    errors: list[str] = []

    if not EXCEPTIONS_YAML_PATH.exists():
        errors.append(f"exceptions.yml 不存在: {EXCEPTIONS_YAML_PATH}")
        return errors

    try:
        import yaml  # 延迟 import: PyYAML 是 transitive 依赖

        data = yaml.safe_load(EXCEPTIONS_YAML_PATH.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        errors.append(f"exceptions.yml YAML 解析失败: {e}")
        return errors

    if not isinstance(data, dict) or "exceptions" not in data:
        errors.append("exceptions.yml 顶层应为 dict 且含 'exceptions' key")
        return errors

    exceptions = data["exceptions"]
    if not isinstance(exceptions, list):
        errors.append(f"'exceptions' 应为 list, 实际 {type(exceptions).__name__}")
        return errors

    # 收集 redlines.yml 中已注册的 rule_id (用于校验 rule_id 存在性)
    try:
        redlines_data = yaml.safe_load(REDLINES_YAML_PATH.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        redlines_data = None
    registered_rule_ids: set[str] = set()
    if isinstance(redlines_data, dict) and isinstance(redlines_data.get("redlines"), list):
        registered_rule_ids = {
            str(entry.get("id")) for entry in redlines_data["redlines"] if isinstance(entry, dict) and entry.get("id")
        }

    seen_ids: set[str] = set()
    for idx, entry in enumerate(exceptions, 1):
        if not isinstance(entry, dict):
            errors.append(f"exceptions[{idx}] 应为 dict, 实际 {type(entry).__name__}")
            continue

        # 必填字段
        missing = EXCEPTION_REQUIRED_FIELDS - entry.keys()
        if missing:
            errors.append(f"exceptions[{idx}] 缺少必填字段: {sorted(missing)}")
        # 二选一字段
        if not (EXCEPTION_EXPIRY_FIELDS & entry.keys()):
            errors.append(f"exceptions[{idx}] 缺少 expires_at 或 removal_trigger (二选一必填)")

        # id 唯一性与格式
        exc_id = entry.get("id")
        if exc_id is not None:
            if not isinstance(exc_id, str) or not exc_id.startswith("EX-"):
                errors.append(f"exceptions[{idx}] id 格式应为 EX-XXXX, 实际 {exc_id!r}")
            elif exc_id in seen_ids:
                errors.append(f"exceptions[{idx}] id 重复: {exc_id}")
            else:
                seen_ids.add(exc_id)

        # rule_id 存在性
        rule_id = entry.get("rule_id")
        if rule_id is not None and registered_rule_ids and rule_id not in registered_rule_ids:
            errors.append(f"exceptions[{idx}] rule_id '{rule_id}' 不存在于 redlines.yml")

        # paths 存在性
        paths = entry.get("paths")
        if isinstance(paths, list):
            for p in paths:
                if not isinstance(p, str):
                    errors.append(f"exceptions[{idx}] paths 元素应为 str, 实际 {type(p).__name__}")
                    continue
                if not (ROOT / p).exists():
                    errors.append(f"exceptions[{idx}] 路径不存在: {p}")
        elif paths is not None:
            errors.append(f"exceptions[{idx}] paths 应为 list, 实际 {type(paths).__name__}")

    return errors


def check_canonical_topics_consistency() -> list[str]:
    """主题 → canonical 正本映射一致性检查 (P2-12)。

    校验 docs/governance/canonical-topics.yml：
    1. 可被 yaml.safe_load 解析, 顶层为 dict, 含 "topics" key (list)。
    2. 每个主题必填 id/title/canonical。
    3. id 唯一。
    4. canonical 路径在仓库中真实存在。
    5. workflow 路径（若存在）在仓库中真实存在。
    """
    errors: list[str] = []

    if not CANONICAL_TOPICS_YAML_PATH.exists():
        errors.append(f"canonical-topics.yml 不存在: {CANONICAL_TOPICS_YAML_PATH}")
        return errors

    try:
        import yaml  # 延迟 import: PyYAML 是 transitive 依赖

        data = yaml.safe_load(CANONICAL_TOPICS_YAML_PATH.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        errors.append(f"canonical-topics.yml YAML 解析失败: {e}")
        return errors

    if not isinstance(data, dict) or "topics" not in data:
        errors.append("canonical-topics.yml 顶层应为 dict 且含 'topics' key")
        return errors

    topics = data["topics"]
    if not isinstance(topics, list):
        errors.append(f"'topics' 应为 list, 实际 {type(topics).__name__}")
        return errors

    seen_ids: set[str] = set()
    for idx, entry in enumerate(topics, 1):
        if not isinstance(entry, dict):
            errors.append(f"topics[{idx}] 应为 dict, 实际 {type(entry).__name__}")
            continue

        # 必填字段
        missing = {"id", "title", "canonical"} - entry.keys()
        if missing:
            errors.append(f"topics[{idx}] 缺少必填字段: {sorted(missing)}")

        # id 唯一性
        topic_id = entry.get("id")
        if isinstance(topic_id, str):
            if topic_id in seen_ids:
                errors.append(f"topics[{idx}] id 重复: {topic_id}")
            else:
                seen_ids.add(topic_id)

        # canonical 路径存在性
        canonical = entry.get("canonical")
        if isinstance(canonical, str):
            if not (ROOT / canonical).exists():
                errors.append(f"topics[{idx}] canonical 路径不存在: {canonical}")
        elif canonical is not None:
            errors.append(f"topics[{idx}] canonical 应为 str, 实际 {type(canonical).__name__}")

        # workflow 路径存在性（可选）
        workflow = entry.get("workflow")
        if isinstance(workflow, str):
            if not (ROOT / workflow).exists():
                errors.append(f"topics[{idx}] workflow 路径不存在: {workflow}")
        elif workflow is not None:
            errors.append(f"topics[{idx}] workflow 应为 str, 实际 {type(workflow).__name__}")

    return errors


def _render_agents_invariant_lines() -> list[str]:
    """从 redlines.yml 渲染 AGENTS.md 最小安全集生成区块内容（不含包裹标记）。

    取 `rule_type: INVARIANT` 的红线（按 yml 顺序）+ 追加 R18（WORKFLOW，影响工作区整洁）。
    """
    import yaml  # 延迟 import: PyYAML 是 transitive 依赖, 避免未安装时影响其他检查

    data = yaml.safe_load(REDLINES_YAML_PATH.read_text(encoding="utf-8"))
    redlines = data["redlines"]
    lines: list[str] = []
    for entry in redlines:
        rule_type = entry.get("rule_type")
        if rule_type != "INVARIANT":
            continue
        lines.append(f"- {entry['id']}：{entry['title']}")
    # R18 为 WORKFLOW 但影响工作区整洁，显式追加为区块末项
    for entry in redlines:
        if entry.get("id") == "R18":
            lines.append(f"- R18：{entry['title']}")
            break
    return lines


def check_agents_md_sync() -> list[str]:
    """校验 AGENTS.md 生成区块与 redlines.yml 一致性（DOC-08/DOC-13）。

    AGENTS.md 的 `<!-- generated:redlines-invariant -->` 与 `<!-- /generated -->` 之间内容
    必须等于由 redlines.yml 渲染的结果（见 _render_agents_invariant_lines），从机制上消除多源漂移。
    """
    errors: list[str] = []
    if not AGENTS_PATH.exists():
        return [f"AGENTS.md 不存在: {AGENTS_PATH}"]
    content = AGENTS_PATH.read_text(encoding="utf-8")
    start_tag = "<!-- generated:redlines-invariant -->"
    end_tag = "<!-- /generated -->"
    start = content.find(start_tag)
    end = content.find(end_tag)
    if start == -1 or end == -1 or end <= start:
        return [f"AGENTS.md 缺少生成区块标记（{start_tag} / {end_tag}）"]

    block = content[start + len(start_tag) : end].strip().splitlines()
    expected = _render_agents_invariant_lines()
    if block != expected:
        errors.append(
            "AGENTS.md 生成区块与 redlines.yml 不一致（INVARIANT 红线 + R18）。"
            "请改正本 redlines.yml 后同步 AGENTS.md，勿手工修改生成区块。"
        )
    return errors


def main() -> int:
    """运行全部检查，返回退出码。"""
    all_errors: list[str] = []
    all_errors.extend(check_anchor_dead_links())
    all_errors.extend(check_relative_dead_links())
    all_errors.extend(check_version_consistency())
    all_errors.extend(check_precommit_hook_count())
    all_errors.extend(check_flet_version_drift())
    all_errors.extend(check_note_lazy_format())
    all_errors.extend(check_redlines_yaml_consistency())
    # 3c 紧随 3b 之后：3b 守护 yml schema 完整性，3c 守护 enforcement 与实际配置一致
    # 3c 独立解析 yml，不依赖 3b 执行结果，顺序仅为可读性
    all_errors.extend(check_enforcement_mapping())
    # 例外注册表一致性：紧随红线一致性之后，守护集中例外治理 (P1-01)
    all_errors.extend(check_exceptions_yaml_consistency())
    # 主题 → canonical 正本映射一致性：守护决策树机器可读镜像的路径有效性 (P2-12)
    all_errors.extend(check_canonical_topics_consistency())
    # Flet 入口完整性：紧随 Flet 版本漂移检查之后，守护 docs/flet/README.md 覆盖全部专题
    all_errors.extend(check_flet_hub_completeness())
    # AGENTS.md 生成区块与 redlines.yml 一致性：守护跨工具入口的最小安全集导出镜像 (DOC-08/DOC-13)
    all_errors.extend(check_agents_md_sync())

    if all_errors:
        print("[FAIL] 文档一致性检查失败：", file=sys.stderr)
        for err in all_errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(
        "[PASS] 文档一致性检查通过（锚点死链 / 相对链接死链 / 版本一致 / "
        "pre-commit hook 数量 / Flet 版本漂移 / NOTE(lazy) 三要素 / redlines.yml 一致性 / "
        "enforcement 字段映射一致性 / exceptions.yml 一致性 / canonical-topics.yml 一致性 / "
        "Flet 入口完整性 / AGENTS.md 生成区块一致性）"
    )
    return 0


if __name__ == "__main__":
    # 兜底：Windows PYTHONIOENCODING=gbk 等非 UTF-8 环境下，emoji/中文输出会触发
    # UnicodeEncodeError。reconfigure stdout/stderr 为 UTF-8（errors="replace" 容错），
    # 避免主输出 emoji（已改为 ASCII [PASS]/[FAIL]）之外的非 ASCII 字符崩溃。
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            typing.cast(TextIOWrapper, _stream).reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
