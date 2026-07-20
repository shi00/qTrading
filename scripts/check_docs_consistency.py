"""文档一致性检查（C5 第一阶段 + 第二阶段 3a + 3b + 3c）。

检查项：
1. Markdown 锚点死链校验：扫描 CHECKED_DOCS 全部受检文件中带 `#anchor` 的 markdown 链接,
   确认目标标题存在（支持同文件 `#anchor` 与跨文件 `./file.md#anchor`）。
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
   实际配置存在且粗粒度可达（9 个不变量 N1~N9，见 ADR-0005）。

退出码：0 通过，1 失败。供 pre-commit `docs-consistency` hook 与 pytest 契约测试调用。

第二阶段扩展：
- 3a NOTE(lazy) 三要素检查（已实现：check_note_lazy_format()）。
- 3b 红线 R1~R18 编号 append-only 检查（已实现：check_redlines_yaml_consistency()，见 ADR-0003）。
- 3c enforcement 字段与实际 hook / CI job 映射检查（已实现：check_enforcement_mapping()，见 ADR-0005）。
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
FLET_BEST_PRACTICES_PATH = ROOT / "man" / "flet-best-practices.md"  # 现为 stub，链接到 docs/flet/
FLET_V1_API_CONSTRAINTS_PATH = ROOT / "docs" / "flet" / "v1-api-constraints.md"
FLET_PROJECT_DIFFERENCES_PATH = ROOT / "docs" / "flet" / "project-differences.md"
FLET_UPGRADE_CHECKLIST_PATH = ROOT / "docs" / "flet" / "upgrade-checklist.md"
FLET_API_VERIFICATION_TEMPLATE_PATH = ROOT / "docs" / "flet" / "api-verification-template.md"
FLET_ACCESSIBILITY_BASELINE_PATH = ROOT / "docs" / "flet" / "accessibility-baseline.md"
KNOWN_TECHNICAL_DEBT_PATH = ROOT / "docs" / "debt" / "known-technical-debt.md"
REDLINES_YAML_PATH = ROOT / "docs" / "governance" / "redlines.yml"
PYPROJECT_PATH = ROOT / "pyproject.toml"
PRECOMMIT_PATH = ROOT / ".pre-commit-config.yaml"

# 3c: enforcement 字段校验所需的项目配置路径常量（monkeypatch 可注入，禁止内联路径构造）
CI_WORKFLOW_DIR = ROOT / ".github" / "workflows"
CHECK_REDLINES_SCRIPT_PATH = ROOT / "scripts" / "check_redlines.py"
GITLEAKS_CONFIG_PATH = ROOT / ".gitleaks.toml"

# docs/flet/ 子文档清单（Phase 2.5 迁移后 Flet 内容分散于此）
FLET_DOCS_PATHS: list[Path] = [
    FLET_BEST_PRACTICES_PATH,
    FLET_V1_API_CONSTRAINTS_PATH,
    FLET_PROJECT_DIFFERENCES_PATH,
    FLET_UPGRADE_CHECKLIST_PATH,
    FLET_API_VERIFICATION_TEMPLATE_PATH,
    FLET_ACCESSIBILITY_BASELINE_PATH,
]

# 受检 markdown 文件清单（锚点死链 + 相对链接死链 + pre-commit hook 数量校验范围）
# P3-7 修复：纳入 docs/guides/、docs/patterns/、docs/architecture/、docs/README.md 全部 markdown，
# 防止从 CONTRIBUTING.md 迁移后的 `./` 死链逃逸门禁
# docs-quality-review 扩展：纳入 root README/CHANGELOG/SECURITY、PR 模板、ADR 全部、man/ 全部
CHECKED_DOCS: list[Path] = [
    CLAUDE_PATH,
    CONTRIBUTING_PATH,
    *FLET_DOCS_PATHS,
    KNOWN_TECHNICAL_DEBT_PATH,
    *(ROOT / "docs" / "guides").glob("*.md"),
    *(ROOT / "docs" / "patterns").glob("*.md"),
    *(ROOT / "docs" / "architecture").glob("*.md"),
    *(ROOT / "docs" / "adr").glob("*.md"),
    ROOT / "docs" / "README.md",
    ROOT / "README.md",
    ROOT / "CHANGELOG.md",
    ROOT / "SECURITY.md",
    ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md",
    ROOT / "man" / "database-account-separation.md",
    ROOT / "man" / "table-partitioning-strategy.md",
]

# Flet 版本漂移检查范围（治理文档）
FLET_VERSION_DOCS: list[Path] = [CLAUDE_PATH, CONTRIBUTING_PATH, *FLET_DOCS_PATHS]

# Flet 包名（用于从 pyproject.toml 提取锁定版本）
_FLET_PACKAGES = ("flet", "flet-desktop", "flet-charts")

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


def _resolve_target_doc(link_url: str, source_doc: Path) -> Path | None:
    """解析 markdown 链接 url 的目标文件路径。

    返回 None 表示非受检文件（外部链接或不在 CHECKED_DOCS 中的目标）。

    解析规则：
    - 同文件锚点（`#anchor`）：返回 source_doc
    - 跨文件链接：从 source_doc 所在目录解析相对路径，若指向 CHECKED_DOCS 中的文件则返回该文件。
      例如 man/flet-best-practices.md 中的 `../CLAUDE.md` 解析为 ROOT/CLAUDE.md。
    """
    if "#" in link_url:
        target_path_part = link_url.split("#", 1)[0]
    else:
        target_path_part = link_url

    # 同文件锚点
    if not target_path_part:
        return source_doc

    # 跨文件链接：从 source_doc 所在目录解析相对路径
    target_doc = (source_doc.parent / target_path_part).resolve()
    if target_doc in CHECKED_DOCS:
        return target_doc
    return None


def check_anchor_dead_links() -> list[str]:
    """检查项 1：markdown 锚点死链。

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

                target_doc = _resolve_target_doc(url, doc)
                if target_doc is None:
                    # 目标不在受检范围（如 README.md、pyproject.toml 等），跳过
                    continue

                if anchor not in doc_headings.get(target_doc, set()):
                    errors.append(
                        f"{doc.name}:{line_no}: 锚点死链 '{url}' (锚点 '{anchor}' 在 {target_doc.name} 中不存在)"
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
    """从 pyproject.toml `[project.dependencies]` 读取 flet/flet-desktop/flet-charts 锁定版本。

    返回版本号集合（三包通常锁定同一版本，如 {"0.86.0"}）。
    """
    with open(PYPROJECT_PATH, "rb") as f:
        cfg = tomllib.load(f)
    versions: set[str] = set()
    for dep in cfg["project"]["dependencies"]:
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


# redlines.yml 字段完整性校验常量
REDLINE_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {"id", "title", "description", "enforcement", "human_review_required"}
)
# R 编号格式正则: R1 ~ R999 (append-only, 不复用废弃编号)
REDLINE_ID_PATTERN = re.compile(r"^R(\d+)$")
# CLAUDE.md §3.1 红线表行匹配: 以 `| R\d+ |` 开头的 markdown 表格行
CLAUDE_REDLINE_TABLE_ROW_PATTERN = re.compile(r"^\|\s*R\d+\s*\|")


def check_redlines_yaml_consistency() -> list[str]:
    """检查项 7：redlines.yml 与 CLAUDE.md §3.1 红线表一致性（ADR-0003 决策落地）。

    校验:
    1. redlines.yml 可被 yaml.safe_load 解析, 顶层为 dict, 含 "redlines" key (list)
    2. 每条红线含 5 字段: id/title/description/enforcement/human_review_required
    3. id 格式为 R\\d+, 连续 append-only (R1, R2, ..., R_N, 无缺号/重号/跳号)
    4. CLAUDE.md §3.1 红线表行数 (以 ``| R\\d+ |`` 开头的行) = yml 条目数

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

    return errors


# =============================================================================
# 3c: enforcement 字段与实际 hook/CI job 映射一致性检查（ADR-0005）
#
# 9 个不变量 N1~N9 守护 enforcement 字段声称的守护机制配置存在且粗粒度可达。
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
ENFORCEMENT_KEYWORD_HUMAN_REVIEW = "仅人工评审"
ENFORCEMENT_KEYWORD_PENDING: tuple[str, ...] = ("待实现", "暂缓")  # R16 特例

# ruff 关键词使用 word boundary 匹配，避免误匹配 'scruffian' 等
RUFF_KEYWORD_PATTERN = re.compile(r"\bruff\b", re.IGNORECASE)

# import-linter 契约数量正则（从 enforcement 文本解析期望数量，如 "4 条契约"）
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

    不变量清单（v3，8 项；原 N9 在实施后检视中删除——与 N6 触发条件等价仅操作数顺序不同）：
    - N1: enforcement 含 'check_redlines.py' ⇒ redline-check hook 存在 + entry 含 check_redlines.py + 脚本文件存在
    - N2: enforcement 含 'import-linter' ⇒ lint-imports hook 存在 + entry 含 lint-imports + 契约数量一致
    - N3: enforcement 含 'ruff' ⇒ ruff-check hook 存在 + entry 含 ruff
    - N4: enforcement 含 '安全扫描' ⇒ Gitleaks workflow + .gitleaks.toml 同时存在
    - N5: enforcement 含 'CI-test' ⇒ workflow run: 命令块含 pytest 命令
    - N6: enforcement 含 '仅人工评审' ⇒ human_review_required == true
    - N7: enforcement 含 '待实现'/'暂缓' ⇒ human_review_required == false（R16 特化守护，与 N8 子集关系见 ADR-0005）
    - N8: human_review_required == true ⇒ enforcement 含 '仅人工评审'

    N6 + N8 共同构成 `human_review_required == true ⇔ enforcement 含「仅人工评审」` 双向一致性。
    N7 是 N8 的 R16 特化版（含 pending 关键词时 N8 也会触发，但 N7 报错更精确指向 R16 误标）。

    使用 .get() 防御性访问 human_review_required 字段；字段缺失时跳过 N6~N8（由 3b 守护字段完整性）。
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

        # N6~N8: human_review_required 一致性校验
        # 字段缺失时跳过（由 3b check_redlines_yaml_consistency() 守护字段完整性）
        if human_review is not None:
            # N6: 仅人工评审 ⇒ human_review_required == true
            if ENFORCEMENT_KEYWORD_HUMAN_REVIEW in keywords and not human_review:
                errors.append(f"{rid}: N6 enforcement 含 '仅人工评审' 但 human_review_required=false")
            # N7: 待实现/暂缓 ⇒ human_review_required == false（R16 特化守护）
            if any(p in keywords for p in ENFORCEMENT_KEYWORD_PENDING) and human_review:
                errors.append(f"{rid}: N7 enforcement 含 '待实现/暂缓' 但 human_review_required=true")
            # N8: human_review_required == true ⇒ enforcement 含 '仅人工评审'
            if human_review and ENFORCEMENT_KEYWORD_HUMAN_REVIEW not in keywords:
                errors.append(f"{rid}: N8 human_review_required=true 但 enforcement 不含 '仅人工评审'")

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

    if all_errors:
        print("[FAIL] 文档一致性检查失败：", file=sys.stderr)
        for err in all_errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(
        "[PASS] 文档一致性检查通过（锚点死链 / 相对链接死链 / 版本一致 / "
        "pre-commit hook 数量 / Flet 版本漂移 / NOTE(lazy) 三要素 / redlines.yml 一致性 / "
        "enforcement 字段映射一致性）"
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
