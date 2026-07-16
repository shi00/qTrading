"""文档一致性检查（C5 第一阶段 + 第二阶段 3a）。

检查项：
1. Markdown 锚点死链校验：扫描 CLAUDE.md、CONTRIBUTING.md、man/flet-best-practices.md 中带 `#anchor` 的 markdown 链接，
   确认目标标题存在（支持同文件 `#anchor` 与跨文件 `./file.md#anchor`）。
2. CLAUDE.md 顶部版本与 pyproject.toml `[project].version` 一致。
3. 文档中"项目使用 N 个 pre-commit hook"的数量与 `.pre-commit-config.yaml` 本地 hook 数量一致。
4. NOTE(lazy) 三要素格式检查：扫描所有 .py 文件中的 `NOTE(lazy):` 标记，
   校验后续块内是否含 `ceiling:` 与 `upgrade:` 两个关键字（CLAUDE.md §3.3 要求）。
5. Flet 版本漂移检查：扫描治理文档中 Flet 关键词附近的具体补丁版本号
   （CLAUDE.md §3.2「文档 SHALL NOT 硬编码 Flet 补丁版本号」）。
6. 相对链接死链检查：扫描受检 markdown 中不含锚点的相对路径链接，确认目标文件存在。

退出码：0 通过，1 失败。供 pre-commit `docs-consistency` hook 与 pytest 契约测试调用。

第二阶段扩展（未实现，登记于 CONTRIBUTING.md 已知技术债）：
- 红线 R1~R17 编号 append-only 检查。
- "强制状态"与实际 hook / CI job 的映射检查。
"""

from __future__ import annotations

import re
import sys
import tomllib
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
FLET_BEST_PRACTICES_PATH = ROOT / "man" / "flet-best-practices.md"
PYPROJECT_PATH = ROOT / "pyproject.toml"
PRECOMMIT_PATH = ROOT / ".pre-commit-config.yaml"

# 受检 markdown 文件清单（锚点死链 + 相对链接死链 + pre-commit hook 数量校验范围）
CHECKED_DOCS: list[Path] = [CLAUDE_PATH, CONTRIBUTING_PATH, FLET_BEST_PRACTICES_PATH]

# Flet 版本漂移检查范围（治理文档）
FLET_VERSION_DOCS: list[Path] = [CLAUDE_PATH, CONTRIBUTING_PATH, FLET_BEST_PRACTICES_PATH]

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


def main() -> int:
    """运行全部检查，返回退出码。"""
    all_errors: list[str] = []
    all_errors.extend(check_anchor_dead_links())
    all_errors.extend(check_relative_dead_links())
    all_errors.extend(check_version_consistency())
    all_errors.extend(check_precommit_hook_count())
    all_errors.extend(check_flet_version_drift())
    all_errors.extend(check_note_lazy_format())

    if all_errors:
        print("[FAIL] 文档一致性检查失败：", file=sys.stderr)
        for err in all_errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(
        "[PASS] 文档一致性检查通过（锚点死链 / 相对链接死链 / 版本一致 / "
        "pre-commit hook 数量 / Flet 版本漂移 / NOTE(lazy) 三要素）"
    )
    return 0


if __name__ == "__main__":
    # 兜底：Windows PYTHONIOENCODING=gbk 等非 UTF-8 环境下，emoji/中文输出会触发
    # UnicodeEncodeError。reconfigure stdout/stderr 为 UTF-8（errors="replace" 容错），
    # 避免主输出 emoji（已改为 ASCII [PASS]/[FAIL]）之外的非 ASCII 字符崩溃。
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
