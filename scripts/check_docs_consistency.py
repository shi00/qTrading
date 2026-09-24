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
12. 规则集元数据一致性检查（DOC-01）：CLAUDE.md 与 CONTRIBUTING.md 的 ruleset_version 相等、
   last_reviewed 为合法日期且前者不早于后者。
13. 决策树映射一致性检查（DOC-04）：CLAUDE.md §1.8 决策树「必读入口」与 canonical-topics.yml
   canonical 镜像双向一致（同一主题必须指向同一正本）。
14. canonical 路由一致性检查（DOC-05）：声明 workflow 的 canonical 入口必须含指向该 workflow 的链接，
   令入口承担条件路由责任。
15. 文档索引全覆盖检查（DOC-07/DOC-11）：docs/**/*.md 每个文件均被 CONTRIBUTING.md 或
   docs/README.md 引用（目录级引用视为覆盖其下全部文件）。
16. 治理 id 引用一致性检查（DOC-09）：EX-\\d{4} 双向校验——消费文档引用的例外必须已登记，
   已登记例外必须被消费文档引用。
17. 检视方法论文档登记检查（DOC-07）：docs/reviews/README.md 以文件级链接登记全部顶层
   方法论文档（ai-review / appendix / quality-dimensions / scenario-completeness），使检视
   方法论与轮次清单一跳可达（结论正文落根 reviews/ 为本地 gitignored 产物，不入仓库）。
18. core 模块清单完整性检查（GDR-11）：断言 CLAUDE.md §4.2 声明的 core/ 模块列表与实际
   core/*.py 文件一致。
19. ADR 索引完整性检查（GDR-12）：校验 CONTRIBUTING.md 文件级登记全部 docs/adr/*.md
   决策文档。
20. 书名号章节引用一致性检查（GDR-13）：扫描受检 markdown 中形如 `<文档路径>「<章节名>」`
   的引用，断言目标文档存在同名标题（或标题以「：」+ 章节名 结尾，容忍「第三部分：实现规范手册」
   这类前缀修饰）；章节改名或删除时不再无报警。
21. 策略静态描述与可调参数一致性检查（D2-M4）：扫描 `strategy_*_desc` 静态描述中硬编码的
   数字字面量阈值，若含数字则必须配套 `strategy_*_desc_dynamic` 动态模板，避免 UI 展示阈值
   与可调参数脱钩漂移。
22. Flet 徽章版本一致性检查（文档复检 H1 根因）：扫描 README 的 UI 徽章中 Flet 后的版本声明，
   断言与 pyproject.toml 锁定 flet 主版本一致（主版本 `>=N` 形式或补丁 `N.M.P` 的 N 均须对齐），
   避免徽章落后锁定版写版本造成「宣称守护却漏检」。
23. 例外清单数量守卫检查（文档复检 L1 根因）：扫描受检 markdown 中「现存 N 条 R1 例外」
   式数量自述，断言 N 与 exceptions.yml 实际注册 EX 条目数一致，避免清单数量陈旧快照。
24. 治理 ID 对义守卫检查（文档复检 H3 根因）：检测 governance-ids.md 中同一 ID 被登记为多条
   不同语义（同名异义）；行内已声明「双义登记/另义」的视为已披露而豁免，未披露的多义报 WARNING
   （渐进部署，存量清零后翻转 ERROR），弥补仅查「是否登记」不查「是否对义」的守护盲区。
25. 注册单例散文数量守卫检查（文档复检盲1）：解析 docs/architecture/singleton-lifecycle.md 中
   「注册单例（@register_singleton，N 个）」散文 N，与「注册单例」章节表格实际数据行数比对，
   守护单例清单数量随增删同步。
26. ADR supersede 双向链守卫检查（文档复检盲2）：解析 docs/adr/*.md 头元数据的 Supersedes 与
   Superseded by 声明，校验双向对称（X 声明 supersedes Y ⇔ Y 声明被 X 部分/整体 supersede），
   守护 ADR supersede 引用不单向断链。
27. 脚本索引完整性检查（F-08）：校验 docs/guides/ci-cd.md 以 `scripts/<name>.py` 形式登记了
   scripts/*.py 全部工程脚本（含未登记与幽灵引用双向检测），防止新增脚本成为不可发现的暗坑。
28. pre-commit hook 名称级一致性检查（F-09）：从 .pre-commit-config.yaml 提取本地 hook id 集合，
   断言 ci-cd.md「Pre-commit Hooks」节的受控枚举与之一致（无遗漏 + 无幽灵），
   守护"只能数数量、不能核名字"的枚举漂移（数量门禁可被指针式写法绕过）。
29. workflow 枚举无遗漏检查（F-09）：断言 .github/workflows/*.yml 每一文件都在 ci-cd.md 出现，
   守护 CI 前端流水线（docs-ci / flet-nightly / sidecar 等）因仅存在于文件而被文档漏登记。

退出码：0 通过，1 失败。供 pre-commit `docs-consistency` hook 与 pytest 契约测试调用。

第二阶段扩展：
- 3a NOTE(lazy) 三要素检查（已实现：check_note_lazy_format()）。
- 3b 红线 R1~R23 编号 append-only 检查（已实现：check_redlines_yaml_consistency()，见 ADR-0003）。
- 3c enforcement 字段与实际 hook / CI job 映射检查（已实现：check_enforcement_mapping()，见 ADR-0005）。
- Flet 入口完整性检查（已实现：check_flet_hub_completeness()）。
- exceptions.yml 例外注册表一致性检查（已实现：check_exceptions_yaml_consistency()，P1-01）。
- canonical-topics.yml 主题映射一致性检查（已实现：check_canonical_topics_consistency()，P2-12）。
"""

from __future__ import annotations

import ast
import json
import re
import sys
import tomllib
import typing
from dataclasses import dataclass
from datetime import date
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
# workflow 目录（F-09 workflow 枚举门禁的数据源；CI_CD_PATH 在下方与 SCRIPTS_DIR 一并定义）
WORKFLOWS_DIR = ROOT / ".github" / "workflows"
# Flet 徽章版本守卫（文档复检 H1）：README UI 徽章中的 Flet 版本声明须与 pyproject 锁定主版本对齐
README_PATH = ROOT / "README.md"
# man/flet-best-practices.md 现为 stub，指向 docs/flet/README.md（保留历史路径兼容）
FLET_BEST_PRACTICES_PATH = ROOT / "man" / "flet-best-practices.md"
KNOWN_TECHNICAL_DEBT_PATH = ROOT / "docs" / "debt" / "known-technical-debt.md"
REDLINES_YAML_PATH = ROOT / "docs" / "governance" / "redlines.yml"
EXCEPTIONS_YAML_PATH = ROOT / "docs" / "governance" / "exceptions.yml"
CANONICAL_TOPICS_YAML_PATH = ROOT / "docs" / "governance" / "canonical-topics.yml"
RULESET_CHANGELOG_PATH = ROOT / "docs" / "governance" / "ruleset-changelog.md"
AGENTS_PATH = ROOT / "AGENTS.md"
# 生成区块统一结束标记（AGENTS.md 最小安全集 / CLAUDE.md 顶部摘要共用）
_GENERATED_END_TAG = "<!-- /generated -->"
PYPROJECT_PATH = ROOT / "pyproject.toml"
PRECOMMIT_PATH = ROOT / ".pre-commit-config.yaml"
# 单例注册清单文档（盲1 守卫基准）：散文「注册单例（@register_singleton，N 个）」数量 vs 表格实计
SINGLETON_LIFECYCLE_PATH = ROOT / "docs" / "architecture" / "singleton-lifecycle.md"

# 3c: enforcement 字段校验所需的项目配置路径常量（monkeypatch 可注入，禁止内联路径构造）
CI_WORKFLOW_DIR = ROOT / ".github" / "workflows"
CHECK_REDLINES_SCRIPT_PATH = ROOT / "scripts" / "check_redlines.py"
GITLEAKS_CONFIG_PATH = ROOT / ".gitleaks.toml"

# docs/ 顶层目录与导航入口（check_docs_index_completeness 的索引源之一）
DOCS_README_PATH = ROOT / "docs" / "README.md"

# docs/flet/ 目录与导航入口
FLET_DOCS_DIR = ROOT / "docs" / "flet"
FLET_HUB_PATH = FLET_DOCS_DIR / "README.md"

# docs/reviews/ 目录与索引入口（DOC-07 检视方法论文档登记）
REVIEWS_DOCS_DIR = ROOT / "docs" / "reviews"
REVIEWS_README_PATH = REVIEWS_DOCS_DIR / "README.md"

# docs/adr/ 目录（GDR-12 ADR 决策文档文件级登记）
ADR_DOCS_DIR = ROOT / "docs" / "adr"
SCRIPTS_DIR = ROOT / "scripts"
CI_CD_PATH = ROOT / "docs" / "guides" / "ci-cd.md"

# 动态发现 docs/flet/*.md（含 README.md、ui-ux-best-practices.md、canvaskit-rendering-e2e-guide.md 等）
# 新增 Flet 专题文档会自动纳入门禁，无需手动维护清单
FLET_DOCS_PATHS: list[Path] = sorted(FLET_DOCS_DIR.glob("*.md"))

# 受检 markdown 文件清单（锚点死链 + 相对链接死链 + pre-commit hook 数量校验范围）
# P2-06 修复：改为递归发现全部受跟踪 Markdown，再用显式排除清单处理生成物和归档。
# 递归发现范围：根目录 *.md、docs/ 与 man/ 全部 *.md、requirements/ 全部 *.md、PR 模板；
# 排除项必须带原因（_build_doc_excludes）。
# Flet 入口完整性：FLET_DOCS_PATHS 动态发现 docs/flet/*.md，新增专题自动纳入门禁。

# 真实 gitignored 的本地产物 / 归档目录（受 .gitignore 保护，不入版本控制，GDR-07）。
# 统一供两处消费：受检集排除（_build_doc_excludes）与索引完整性扫描豁免
# （check_docs_index_completeness）。本集合严格限定为真实 gitignored 目录。
_GITIGNORED_ARTIFACT_DIRS: tuple[Path, ...] = (
    ROOT / "docs" / "plans" / "archive",
    ROOT / "docs" / "audit",
    ROOT / "docs" / "superpowers",
)

# 兼容别名：保留 _LOCAL_ARTIFACT_DIRS 供外部或现有测试引用
_LOCAL_ARTIFACT_DIRS: tuple[Path, ...] = _GITIGNORED_ARTIFACT_DIRS

# 本地会话计划文件（.gitignore 排除、内容随会话变化或固化历史状态，不入版本控制）：
# 与 _GITIGNORED_ARTIFACT_DIRS 同属 gitignored 本地产物但为根级文件。统一在此登记，
# 受检集构建与治理 ID 扫描共用同一来源（H1：此前 Plans.md 仅在治理 ID 扫描处按名排除，
# 受检集漏排，导致本地 pre-commit 持续假 FAIL）。
_LOCAL_PLAN_FILE_RELS: tuple[str, ...] = ("Plans.md", "Plans-tech-debt.md")


def _build_doc_excludes() -> dict[Path, str]:
    """构建受检集排除清单：gitignored 产物目录 + 本地会话计划文件（逐项带排除原因）。

    按当前 ROOT 动态计算（导入期由 _collect_checked_docs 调用；测试可先 monkeypatch
    ROOT 再调用 _collect_checked_docs 重算，无需真实本地文件在场）。
    """
    excludes: dict[Path, str] = {
        d: "本地 gitignored 产物 / 归档目录（GDR-07），非交付物，不参与文档一致性校验"
        for d in _GITIGNORED_ARTIFACT_DIRS
    }
    excludes.update(
        (ROOT / rel, "本地会话计划文件（.gitignore 排除，内容随会话变化或固化历史状态），不参与一致性门禁")
        for rel in _LOCAL_PLAN_FILE_RELS
    )
    return excludes


def _collect_checked_docs() -> list[Path]:
    """构建受检文档集（导入期执行；测试经 monkeypatch ROOT 后重算以注入临时仓库）。"""
    excludes = _build_doc_excludes()
    return sorted(
        d
        for d in {
            *ROOT.glob("*.md"),
            *(ROOT / "docs").rglob("*.md"),
            *(ROOT / "man").rglob("*.md"),
            *(ROOT / "requirements").rglob("*.md"),
            ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md",
        }
        # 排除支持精确文件与目录前缀（目录下全部子文档一并排除）
        if not any(d == e or e in d.parents for e in excludes)
    )


CHECKED_DOCS: list[Path] = _collect_checked_docs()

# Flet 版本漂移检查范围（治理文档；api-verification-template.md 为 API 核验历史快照，豁免 GDR-06）
FLET_VERSION_DOCS: list[Path] = [
    CLAUDE_PATH,
    CONTRIBUTING_PATH,
    *(p for p in FLET_DOCS_PATHS if p.name != "api-verification-template.md"),
]

# Flet 包名（用于从 pyproject.toml 提取锁定版本）
# flet/flet-desktop/flet-charts/flet-code-editor 在 [project.dependencies]，
# flet-mcp 在 [project.optional-dependencies].dev（开发期 MCP 包，与主包版本对齐，见 CLAUDE.md §1.10）
_FLET_PACKAGES = ("flet", "flet-desktop", "flet-charts", "flet-code-editor", "flet-mcp")


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
    """检查项 2：所有声明「对应版本」字段的受检文档与 pyproject.toml 版本一致。

    原只校验 CLAUDE.md；现遍历 CHECKED_DOCS 中凡含 ``**对应版本**：x.y.z`` 的文档
    （CLAUDE.md / CONTRIBUTING.md / AGENTS.md 等）一并比对，防止散文式版本声明漂移。
    CLAUDE.md 为项目正本，必须声明该字段。
    """
    errors: list[str] = []
    with open(PYPROJECT_PATH, "rb") as f:
        cfg = tomllib.load(f)
    pyproject_ver = cfg["project"]["version"]

    for doc in CHECKED_DOCS:
        content = doc.read_text(encoding="utf-8")
        m = re.search(r"\*\*对应版本\*\*[：:]\s*v?([0-9]+\.[0-9]+\.[0-9]+)", content)
        if not m:
            if doc == CLAUDE_PATH:
                errors.append("CLAUDE.md: 未找到 '**对应版本**' 字段")
            continue
        declared = m.group(1)
        if declared != pyproject_ver:
            line_no = content[: m.start()].count("\n") + 1
            errors.append(f"{doc.name}:{line_no}: 对应版本 {declared} != pyproject.toml 版本 {pyproject_ver}")
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


# 本地 pre-commit hook id：形如反引号包裹的纯小写 kebab（`ruff-check`），
# 排除 `.pre-commit-config.yaml`（含点）、`ci_cd`（含下划线）、`IsolatedAsyncioTestCase`（首字母大写）等非 hook token。
_HOOK_ID_SPAN = re.compile(r"`([a-z][a-z0-9]*(?:-[a-z0-9]+)+)`")


def _local_hook_ids() -> set[str]:
    """提取 .pre-commit-config.yaml 中本地（repo: local）hook 的 id 集合。"""
    content = PRECOMMIT_PATH.read_text(encoding="utf-8")
    return set(re.findall(r"^ {6}- id: (\S+)", content, re.MULTILINE))


def check_precommit_hook_names() -> list[str]:
    """检查项 27：pre-commit hook 名称级一致性（F-09）。

    从 .pre-commit-config.yaml 提取本地 hook id 集合，断言 ci-cd.md「Pre-commit Hooks」节
    以反引号枚举到与配置一致：既有 hook 漏枚举（无遗漏）与文档枚举出配置没有的幽灵 hook
    （子集）双向报错。数量门禁（check_precommit_hook_count）可被「hook 数量见配置文件」的
    指针式写法绕过，此检查改由名称锚定，阻塞枚举漂移。
    """
    errors: list[str] = []
    if not CI_CD_PATH.exists():
        return [f"ci-cd.md 不存在: {CI_CD_PATH}"]
    config_ids = _local_hook_ids()
    doc_content = CI_CD_PATH.read_text(encoding="utf-8")
    # 限定到「### Pre-commit Hooks」节内提取反引号 hook id，避免同文档其他段落
    # 的小写 kebab token（如 `continue-on-error`）被误判为幽灵枚举。
    section = re.search(r"### Pre-commit Hooks(.*?)(?:\n### |\Z)", doc_content, re.DOTALL)
    text = section.group(1) if section else doc_content
    doc_ids = set(_HOOK_ID_SPAN.findall(text))
    for hook_id in sorted(config_ids - doc_ids):
        errors.append(
            f"pre-commit hook 名称一致性: .pre-commit-config.yaml 有本地 hook '{hook_id}' "
            f"未在 ci-cd.md 枚举（无遗漏违规，F-09）"
        )
    for hook_id in sorted(doc_ids - config_ids):
        errors.append(f"pre-commit hook 名称一致性: ci-cd.md 枚举了配置中不存在的 hook '{hook_id}' （幽灵枚举，F-09）")
    return errors


def check_workflow_enum() -> list[str]:
    r"""检查项 28：workflow 枚举无遗漏（F-09）。

    断言 .github/workflows/*.yml 每一文件 base（如 `docs-ci.yml`）都在 ci-cd.md 中出现，
    守护 docs-ci / flet-nightly / sidecar 等流水线因仅在文件系统中存在而未被文档登记
    （CI 前端存在性漂移）。仅做「配置 ⊆ 文档」单向：ci-cd.md 还会引用 audit-allowlist.yml
    等非 workflow 的 .yml，故不做幽灵方向。

    逐文件按文件名断言（F-09 复核修正）：原实现的条件是「文档中存在任意 .yml 引用」，
    未使用循环变量 `wf`，只要 ci-cd.md 提及任一 `.yml` 即对全部 workflow 放行，实为空操作。
    现改为按文件名匹配，并以 `(?<![\w.-])` / `(?![\w.-])` 边界防止 `sidecar.yml` 被
    `pg-sidecar.yml` 之类的长名误命中。
    """
    errors: list[str] = []
    if not WORKFLOWS_DIR.is_dir():
        return [f"workflow 目录不存在: {WORKFLOWS_DIR}"]
    if not CI_CD_PATH.exists():
        return [f"ci-cd.md 不存在: {CI_CD_PATH}"]
    content = CI_CD_PATH.read_text(encoding="utf-8")
    for wf in sorted(p.name for p in WORKFLOWS_DIR.glob("*.yml") if p.is_file()):
        if not re.search(rf"(?<![\w.-]){re.escape(wf)}(?![\w.-])", content):
            errors.append(f"workflow 枚举无遗漏: .github/workflows/{wf} 未在 ci-cd.md 登记（F-09）")
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

    扫描治理文档中「含 Flet 关键词的整行」内的 `\\d+.\\d+.\\d+` 版本号（含可选 `v` 前缀变体，
    如 `v1.2.3`，对抗检视 GDR-06 P1）。
    整行全量拦截消除字距依赖：同一行出现 Flet 关键词即视为 Flet 上下文，任何补丁版本号都报错
    （GOV-08）。`docs/flet/api-verification-template.md` 为 API 核验历史快照（按定义需要
    记录具体版本号），已从 FLET_VERSION_DOCS 范围排除（豁免 GDR-06）。

    报错格式：``{doc.name}:{line_no}: Flet 版本漂移：文档声明 {doc_ver}，pyproject.toml 锁定 {actual_ver}``
    """
    errors: list[str] = []
    locked_versions = _get_flet_locked_versions()
    # 取代表版本（三包通常锁定同一版本）用于报错信息
    actual_ver = next(iter(locked_versions)) if locked_versions else "unknown"

    # GDR-06 P1：`[vV]?` 兼容 "Flet v1.2.3" / "Flet V1.2.3" 形式（`\b` 在 v/V 前，裸 \d+ 或纯小写 v? 漏检）
    version_pattern = re.compile(r"\b[vV]?\d+\.\d+\.\d+\b")
    # Flet 关键词正则：匹配 "Flet" 或 "flet"（word boundary 防止匹配 "fletch" 等）
    flet_keyword_pattern = re.compile(r"\b[Ff]let\b")

    for doc in FLET_VERSION_DOCS:
        content = doc.read_text(encoding="utf-8")
        for line_no, line in enumerate(content.splitlines(), 1):
            if not flet_keyword_pattern.search(line):
                continue
            for v_match in version_pattern.finditer(line):
                doc_ver = v_match.group()
                errors.append(
                    f"{doc.name}:{line_no}: Flet 版本漂移：文档声明 {doc_ver}，pyproject.toml 锁定 {actual_ver}"
                )
    return errors


def check_flet_badge_version() -> list[str]:
    """检查项 22：README UI 徽章中的 Flet 版本声明与 pyproject 锁定主版本对齐（文档复检 H1 根因）。

    README 徽章（https://img.shields.io/badge/...）是当前状态的单点自述，此前无守卫：
    锁定的 flet 升级到 1.0 后徽章仍写 `Flet 0.86.3`（补丁号，主版本 0）不落检。
    规则：提取 `Flet` 之后的版本 token（URL 编码，如 `Flet%200.86.3` / `Flet%20%3E%3D1.0`），
    解码后若是补丁版本 `<major.minor.patch>`（取 major）或主版本 `>=<major>`，其 major 均须等于
    pyproject 锁定 flet 的 major；**README 当前无 Flet 徽章（无 `Flet`+版本 token 行）时静默通过**
    （GOV-09：该检查只在徽章存在时校验，缺失由人工维护，不误报——docstring 与此行为对齐）。
    API 验证记录等历史快照不在此范围，由 check_flet_version_drift 对治理文档另行守护。
    """
    errors: list[str] = []
    locked = _get_flet_locked_versions()
    if not locked:
        return errors
    py_major = next(iter(locked)).split(".")[0]
    # URL 编码还原（`%20` 空格、`%3E%3D` >=）
    import urllib.parse

    content = README_PATH.read_text(encoding="utf-8")
    for line_no, line in enumerate(content.splitlines(), 1):
        m = re.search(r"[Ff]let\s*([0-9%][^\s)\]]*)", line)
        if not m:
            continue
        token = m.group(1)

        raw = urllib.parse.unquote(token)
        raw = raw.strip().strip("()[]")
        # 前缀匹配版本段：徽章版本后常跟颜色后缀（如 `-00d2b4`），故只取语首的
        # 补丁版本 `N.M.P` 或主版本 `>=N`，忽略其后颜色/命名后缀。
        ver = re.match(r"(?:>=?(\d+))|(\d+)\.\d+(?:\.\d+)?", raw)
        if ver is not None:
            declared_major = ver.group(1) or ver.group(2)
        else:
            declared_major = None
        if declared_major is None:
            errors.append(
                f"{README_PATH.name}:{line_no}: Flet 徽章版本无法解析（{token!r}），"
                f"须为 `>=N` 主版本或 `N.M.P` 补丁版本以与 pyproject flet major {py_major} 对齐"
            )
        elif declared_major != py_major:
            errors.append(
                f"{README_PATH.name}:{line_no}: Flet 徽章版本 {raw} 主版本 {declared_major}"
                f" != pyproject 锁定 flet major {py_major}"
            )
    return errors


def _count_exceptions() -> int | None:
    """实计 exceptions.yml 已注册 R1 例外（rule_id == R1）条目数；无法解析返回 None。

    散文守卫匹配的「现存 N 条 *R1* 例外」语义上特指 R1 例外的数量，故只统计
    rule_id == R1 的条目，剔除 R5 等其他规则例外（如 EX-0017/EX-0018），
    避免把非 R1 例外混入 R1 计数造成正文与实计不一致（文档复检 L1）。
    """
    import yaml  # PyYAML 是 transitive 依赖，与 check_exceptions_yaml_consistency 一致延迟 import

    try:
        data = yaml.safe_load(EXCEPTIONS_YAML_PATH.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if isinstance(data, dict):
        items = data.get("exceptions")
        if isinstance(items, list):
            return sum(1 for e in items if isinstance(e, dict) and e.get("rule_id") == "R1")
    return None


def check_exception_count_prose() -> list[str]:
    """检查项 23：受检 markdown 中「现存 N 条 R1 例外」式数量自述与 exceptions.yml 实计一致（文档复检 L1 根因）。

    治理清单数量（如 governance-ids.md 的「现存 N 条 R1 例外」）此前为人工自述无守卫：
    新增 EX id 后若遗漏同步 N，会宣称「现存 N 条」却与实际不符。对齐 check_precommit_hook_count
    的散文数量守卫范式。受检范围限 CHECKED_DOCS（markdown），exceptions.yml 以实计为唯一正本。
    """
    errors: list[str] = []
    actual = _count_exceptions()
    if actual is None:
        return errors  # exceptions.yml 缺失/解析失败由 check_exceptions_yaml_consistency 报告
    for doc in CHECKED_DOCS:
        try:
            content = doc.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in re.finditer(r"现存\s*(\d+)\s*条\s*R1\s*例外", content):
            declared = int(m.group(1))
            if declared != actual:
                line_no = content[: m.start()].count("\n") + 1
                errors.append(
                    f"{doc.name}:{line_no}: 声明现存 {declared} 条 R1 例外，exceptions.yml 实际注册 {actual} 条"
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


# 红线总数散文模式：R1 至 Rn，容忍 ~ / - / – / − 分隔符（如 R1~R23 / R1-R23）
REDLINE_RANGE_PATTERN = re.compile(r"R1\s*[~\-–−]\s*R(\d+)")


def check_redline_range_consistency() -> list[str]:
    """检查项 7b：受检文档中「红线总数」散文（R1~Rxx）与 redlines.yml 实际一致。

    背景（DS）：check_redlines_yaml_consistency() 只守卫 redlines.yml ↔ CLAUDE.md §3.1
    红线「表格行」一致，覆盖不到散文式自述「R1~Rxx 总数」。新增红线时若只加表格行、
    遗漏同步诸如「引用 R1~R22 前确认存在」等散文，此守卫会拦截。

    规则：
    - Rmax 取自 redlines.yml 实际最大红线号（其 id 已由 check_redlines_yaml_consistency 保证连续）。
    - 遍历 CHECKED_DOCS，跳过 docs/adr/ 下文档（ADR 为决策时点历史快照，其中 R1~R18 是当时范围，
      由 ADR-0002 Errata 声明以 redlines.yml 为准；其余快照文档经 _build_doc_excludes 排除，不在 CHECKED_DOCS）。
    :return: 错误信息列表。
    """
    errors: list[str] = []
    try:
        import yaml  # PyYAML 是 transitive 依赖，与 check_redlines_yaml_consistency 一致延迟 import

        data = yaml.safe_load(REDLINES_YAML_PATH.read_text(encoding="utf-8"))
    except (ImportError, OSError, yaml.YAMLError):
        return errors  # yml 缺失/解析失败由 check_redlines_yaml_consistency 报告
    redlines = data.get("redlines") if isinstance(data, dict) else None
    if not isinstance(redlines, list):
        return errors
    rmax = max(int(r["id"][1:]) for r in redlines if isinstance(r, dict) and str(r.get("id", "")).startswith("R"))

    for doc in CHECKED_DOCS:
        if ADR_DOCS_DIR in doc.parents:
            continue  # ADR 为决策时点历史快照，含当时红线范围，不入当前总数守卫
        if doc.name == "CHANGELOG.md":
            continue  # release-please 自动生成，历史提交标题含旧红线范围引文（如"R1~R22 同步为 R1~R23"），
            # 与治理 ID 检查对 CHANGELOG.md 的处理一致（见 check_governance_id_references 注释），不入守卫
        content = doc.read_text(encoding="utf-8")
        for m in REDLINE_RANGE_PATTERN.finditer(content):
            declared = int(m.group(1))
            if declared != rmax:
                line_no = content[: m.start()].count("\n") + 1
                errors.append(f"{doc.name}:{line_no}: 声明红线总数 R1~R{declared}，redlines.yml 实际最大为 R{rmax}")
    return errors


def check_contract_count_prose() -> list[str]:
    """检查项 7c：受检文档中 import-linter「N 条契约」散文与 pyproject.toml 实际契约数一致（H6-b）。

    背景：N2（_check_enforcement_invariants）只校验 redlines.yml 的 enforcement 字段；
    ADR-0006 / known-technical-debt.md 等文档中的同型「N 条契约」散文无守护，OSS E4
    重构（6 条手工 forbidden 拆为 1 条 layers + 2 条 forbidden）后即发生数量漂移。

    规则：
    - 契约数取自 pyproject.toml 的 [[tool.importlinter.contracts]] 节数（与 N2 同源）。
    - 遍历 CHECKED_DOCS，跳过 docs/adr/（决策时点历史快照，当前值以 pyproject.toml 为准，
      由 Errata 声明，与 check_redline_range_consistency 的 ADR 豁免同源）与 CHANGELOG.md
      （release-please 自动生成，历史提交标题含旧数量引文）。
    :return: 错误信息列表。
    """
    errors: list[str] = []
    try:
        pyproject_content = PYPROJECT_PATH.read_text(encoding="utf-8")
    except OSError:
        return [f"pyproject.toml 不存在或不可读: {PYPROJECT_PATH}"]
    actual = len(IMPORT_LINTER_CONTRACT_SECTION_PATTERN.findall(pyproject_content))

    for doc in CHECKED_DOCS:
        if ADR_DOCS_DIR in doc.parents:
            continue  # ADR 为决策时点历史快照，数量以 Errata + pyproject.toml 当前值裁决
        if doc.name == "CHANGELOG.md":
            continue  # release-please 自动生成，历史提交标题含当时数量引文
        content = doc.read_text(encoding="utf-8")
        for m in IMPORT_LINTER_CONTRACT_COUNT_PATTERN.finditer(content):
            declared = int(m.group(1))
            if declared != actual:
                line_no = content[: m.start()].count("\n") + 1
                errors.append(
                    f"{doc.name}:{line_no}: 声明 import-linter {declared} 条契约，pyproject.toml 实际 {actual} 条"
                )
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


# check_ 函数名正则（识别 enforcement 文本中提及的 check_redlines.py 检查函数名）
CHECK_CALL_NAME_PATTERN = re.compile(r"\b(check_[a-zA-Z0-9_]+)\b")


def _extract_redline_check_calls(source: str) -> set[str]:
    """AST 提取 check_redlines.py 的 main() 中所有 check_*() 调用名。

    纯函数，便于单元测试。找到名为 main 的函数后，在其中遍历所有 Call 节点，
    收集 func 为 Name 且以 'check_' 开头的调用名。
    """
    tree = ast.parse(source)
    main_func: ast.FunctionDef | None = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            main_func = node
            break
    if main_func is None:
        return set()
    names: set[str] = set()
    for node in ast.walk(main_func):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id.startswith("check_"):
            names.add(func.id)
    return names


def check_enforcement_reverse_coverage() -> list[str]:
    """检查项: check_redlines.py 实际执行的 check_* 是否全部在 redlines.yml 登记 (DS-11 反向不变量)。

    用 AST 解析 scripts/check_redlines.py 的 main()，提取所有 check_*() 调用名，
    断言每个都能在 redlines.yml 某条红线的 `checks:` 字段（或 enforcement 文本提及）中找到登记。
    这把 ADR-0005 的单向 enforcement 映射（文档声称的机制是否真实存在）补成双向：
    给 check_redlines.py 新增检查而不登记，即触发一致性失败。

    登记来源（取并集，两者任一命中即视为已登记）：
    - 红线条目新增的 `checks:` 可选字段（list[str]）
    - 红线条目 `enforcement` 文本中的 check_* 函数名（R4/R16/R23 已内联提及）

    第二方向（GATE-03/GOV-11）：`checks:` 字段登记的 check_*（仅此集合，不含 enforcement 文本提取）
    必须实际存在于 check_redlines.py 且被 main() 调用——yml 声称有守护但函数被删/未执行即孤儿登记报错。

    基于模块级路径常量 REDLINES_YAML_PATH / CHECK_REDLINES_SCRIPT_PATH 读取，便于测试 monkeypatch。
    解析失败时返回精确错误列表（不抛异常）。
    """
    errors: list[str] = []

    if not CHECK_REDLINES_SCRIPT_PATH.exists():
        errors.append(f"check_redlines.py 不存在: {CHECK_REDLINES_SCRIPT_PATH}")
        return errors
    try:
        source = CHECK_REDLINES_SCRIPT_PATH.read_text(encoding="utf-8")
        call_names = _extract_redline_check_calls(source)
    except (OSError, SyntaxError, UnicodeDecodeError) as e:
        errors.append(f"解析 check_redlines.py 失败: {e}")
        return errors

    registered: set[str] = set()
    checks_names: set[str] = set()
    if not REDLINES_YAML_PATH.exists():
        errors.append(f"redlines.yml 不存在: {REDLINES_YAML_PATH}")
        return errors
    try:
        import yaml  # noqa: F811

        data = yaml.safe_load(REDLINES_YAML_PATH.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as e:
        errors.append(f"redlines.yml 解析失败: {e}")
        return errors

    if isinstance(data, dict) and isinstance(data.get("redlines"), list):
        for entry in data["redlines"]:
            if not isinstance(entry, dict):
                continue
            checks_field = entry.get("checks")
            if isinstance(checks_field, list):
                for c in checks_field:
                    if isinstance(c, str):
                        checks_names.add(c.strip())
                        registered.add(c.strip())
            enforcement = entry.get("enforcement")
            if isinstance(enforcement, str):
                registered.update(CHECK_CALL_NAME_PATTERN.findall(enforcement))

    missing = sorted(call_names - registered)
    if missing:
        errors.append(
            "check_redlines.py 实际执行但未在 redlines.yml 登记的检查: "
            + ", ".join(missing)
            + "（需在某条红线的 checks: 字段补登记，DS-11 反向不变量）"
        )
    # GATE-03/GOV-11 第二方向：yml checks 登记的 check_* 必须存在于脚本且被 main() 调用（无孤儿登记）
    orphan = sorted(checks_names - call_names)
    if orphan:
        errors.append(
            "redlines.yml checks 登记但 check_redlines.py main() 未执行的检查: "
            + ", ".join(orphan)
            + "（需补齐实现；确属『计划中』的守护函数须显式声明，不得只登记不实现）"
        )
    return errors


# 例外注册表必填字段 (P1-01: 集中例外治理, 见 docs/governance/exceptions.yml)
# validity_criteria (F-05): 可执行判据——供 AI 判定"例外是否仍应存在"的正向检查条件。
EXCEPTION_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {"id", "rule_id", "paths", "reason", "owner", "approved_by", "verification", "validity_criteria"}
)
# expires_at 与 removal_trigger 二选一必填
EXCEPTION_EXPIRY_FIELDS: frozenset[str] = frozenset({"expires_at", "removal_trigger"})


def check_exceptions_yaml_consistency() -> list[str]:
    """例外注册表一致性检查 (P1-01)。

    校验 docs/governance/exceptions.yml：
    1. 可被 yaml.safe_load 解析, 顶层为 dict, 含 "exceptions" key (list)。
    2. 每条例外必填字段齐全 (id/rule_id/paths/reason/owner/approved_by/verification/validity_criteria)。
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

    # GATE-02: R1 例外条目数 == pyproject.toml "R1: utils must not import business layers" 契约 ignore_imports 条数（GDR-01 条数唯一事实源，
    # 防 exceptions.yml 自己文件里的头注释漂移）。
    try:
        import tomllib

        with open(PYPROJECT_PATH, "rb") as f:
            proj = tomllib.load(f)
        ignore_count: int | None = None
        for contract in proj.get("tool", {}).get("importlinter", {}).get("contracts", []):
            if str(contract.get("name", "")).startswith("R1: utils"):
                ignores = contract.get("ignore_imports")
                if isinstance(ignores, list):
                    ignore_count = len(ignores)
        r1_exceptions = sum(1 for e in exceptions if isinstance(e, dict) and e.get("rule_id") == "R1")
        if ignore_count is not None and r1_exceptions != ignore_count:
            errors.append(
                f"exceptions.yml R1 例外条目数 {r1_exceptions} != pyproject.toml 'R1: utils must not import business layers' 契约 ignore_imports 条数 {ignore_count}"
            )
    except (OSError, tomllib.TOMLDecodeError):
        pass  # pyproject 解析失败由其他检查报告

    # GATE-04: "R1: utils must not import business layers" 契约 ignore_imports ↔ exceptions.yml EX 交叉回指（GDR-01）
    # pyproject.toml 中 `# EX-XXXX` 注释回指的 EX 集合必须与 exceptions.yml 的 R1 例外 id 集合一致
    # （防 GATE-01 型漂移：yml 自建注释与 pyproject 回指分叉时自动拦截）。
    try:
        pyproject_text = PYPROJECT_PATH.read_text(encoding="utf-8")
        referenced_ex = set(re.findall(r"EX-\d{4}", pyproject_text))
        r1_ids = {str(e.get("id")) for e in exceptions if isinstance(e, dict) and e.get("rule_id") == "R1"}
        if referenced_ex and referenced_ex != r1_ids:
            errors.append(
                "pyproject.toml 「R1: utils must not import business layers」契约 EX 回指与 exceptions.yml R1 例外不一致："
                f"pyproject 引用但 yml 缺失 {sorted(referenced_ex - r1_ids)}；"
                f"yml 有但 pyproject 未回指 {sorted(r1_ids - referenced_ex)}"
            )
    except OSError:
        pass  # pyproject 读取失败由其他检查报告

    return errors


# 反向一致性检查 (P1-04)：技术债表中豁免 EXCEPTIONABLE 红线的条目必须已在 exceptions.yml 登记。
# 检视报告建议：「扫描 known-technical-debt.md 中出现的 R\d+ 引用，若上下文含豁免性措辞而该条目未在
# exceptions.yml 登记则报错」，消除 P1-01 立项要治理的红线豁免漂移（前期只消除了 R1 那一半）。
# 为避免误报（债表大量条目含「保持现状 / 合理降级」但多数仅描述现状或推迟优化，并未豁免红线），
# 本检查做三重收敛：① 只针对 rule_type == EXCEPTIONABLE 的红线（当前 R1 / R5）；
# ② 仅当行内出现「豁免意图词」才视为豁免声明；③ 债目录带稳定行 ID（第一列 `P3-...`）；
# DS-03 起，裸级别 `**P3**` 且含豁免意图的行也报错（防静默绕过例外注册入口）。
# 「推迟优化 / 已落地现状」等非豁免词不触发，故不误报（如 P3-CON04 / P3-M9-EmbeddedPg-TimeoutExpired）。
_DEBT_EXEMPTION_INTENT_WORDS = ("保持现状", "合理设计", "不适用 R", "严格按 R", "豁免")
_DEBT_ROW_ID_PATTERN = re.compile(r"^\|\s*\*\*\s*(P3-[A-Za-z0-9-]+)\s*\*\*")
# DS-03：债目录裸级别行的行首形如 `| **P3** |`（无稳定 ID）。该 pattern 用于识别
# 「有稳定 ID 的债目录行（应含 `**P3-xxx**`）却被写成裸级别」的行，以便无 ID 即报错。
_DEBT_BARE_LEVEL_ROW_PATTERN = re.compile(r"^\|\s*\*\*\s*P3(?![A-Za-z0-9-])\s*\*\*")
_DEBT_REDLINE_REF_PATTERN = re.compile(r"\bR(\d+)\b")


def check_exceptions_reverse_coverage() -> list[str]:
    """反向一致性检查 (P1-04)：技术债表中豁免 EXCEPTIONABLE 红线的条目必须已登记例外。

    通过检查：确保任何「在技术债表中声明豁免某条 EXCEPTIONABLE 红线（如 R5）」的条目，
    都已在 docs/governance/exceptions.yml 中以 rule_id 对应登记（reason 回指该条目标识），
    否则报错——从机制上消除红线豁免绕过例外唯一注册入口的漂移。
    误报防护见函数上方注释，具体豁免清单登记见 EX-0017/EX-0018（R5）。
    """
    errors: list[str] = []

    if not REDLINES_YAML_PATH.exists() or not EXCEPTIONS_YAML_PATH.exists() or not KNOWN_TECHNICAL_DEBT_PATH.exists():
        # 依赖文件缺失由对应一致性检查（check_redlines_yaml_consistency /
        # check_exceptions_yaml_consistency / check_note_lazy_format）fail-closed 守护，
        # 本检查仅在其存在时执行，避免重复报错。
        return errors

    import yaml  # 延迟 import: PyYAML 是 transitive 依赖

    try:
        redlines_data = yaml.safe_load(REDLINES_YAML_PATH.read_text(encoding="utf-8"))
        exc_data = yaml.safe_load(EXCEPTIONS_YAML_PATH.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return errors  # YAML 解析错误由对应检查函数守护，此处静默由它报

    # 1. EXCEPTIONABLE 红线 id 集合
    exceptable_ids: set[str] = set()
    if isinstance(redlines_data, dict) and isinstance(redlines_data.get("redlines"), list):
        for entry in redlines_data["redlines"]:
            if isinstance(entry, dict) and entry.get("rule_type") == "EXCEPTIONABLE" and entry.get("id"):
                exceptable_ids.add(str(entry["id"]))

    # 2. exceptions.yml 已登记：rule_id -> reason 聚合文本集合（用于「reason 回指条目」判定）
    registered: dict[str, list[str]] = {}
    if isinstance(exc_data, dict) and isinstance(exc_data.get("exceptions"), list):
        for entry in exc_data["exceptions"]:
            if not isinstance(entry, dict):
                continue
            rule_id = entry.get("rule_id")
            reason = entry.get("reason")
            if isinstance(rule_id, str) and isinstance(reason, str):
                registered.setdefault(rule_id, []).append(reason)

    # 仅当存在待守护的 EXCEPTIONABLE 红线且已有登记时才需要校验
    if not exceptable_ids:
        return errors

    # 3. 扫描技术债表表格行，校验豁免登记
    for line in KNOWN_TECHNICAL_DEBT_PATH.read_text(encoding="utf-8").splitlines():
        row_id_match = _DEBT_ROW_ID_PATTERN.match(line)
        if not row_id_match:
            # DS-03：债目录若写成裸级别（`| **P3** |` 无稳定 ID）且含豁免意图，
            # 会被「未匹配即跳过」静默漏检——新增无 ID 债目并声明豁免 EXCEPTIONABLE
            # 红线（R1/R5）即可绕过例外唯一注册入口，违反 P1-01 立项意图。
            if _DEBT_BARE_LEVEL_ROW_PATTERN.match(line) and any(word in line for word in _DEBT_EXEMPTION_INTENT_WORDS):
                errors.append(
                    f"known-technical-debt.md 债目录表格行声明了豁免意图措辞"
                    f"（{'/'.join(_DEBT_EXEMPTION_INTENT_WORDS)}），但行首仅用裸级别 **P3** 而无稳定 ID"
                    f"（应含 **P3-xxx**）。豁免 EXCEPTIONABLE 红线的债目录必须带稳定 ID，以防绕过"
                    f"例外唯一注册入口：{line.strip()[:120]}"
                )
            continue  # 非表格行、无豁免意图或无稳定 ID 的行，跳过
        row_id = row_id_match.group(1)
        if not any(word in line for word in _DEBT_EXEMPTION_INTENT_WORDS):
            continue  # 无豁免意图（描述现状/推迟优化），不视为红线豁免
        # 行内引用的 EXCEPTIONABLE 红线
        exempted_ids = {f"R{rid}" for rid in _DEBT_REDLINE_REF_PATTERN.findall(line) if f"R{rid}" in exceptable_ids}
        for rid in sorted(exempted_ids):
            if rid not in registered:
                errors.append(
                    f"known-technical-debt.md 条目 {row_id} 声明豁免 EXCEPTIONABLE 红线 {rid}（含豁免意图措辞），"
                    f"但 exceptions.yml 未登记任何 rule_id={rid} 的例外。请在 exceptions.yml 补录该豁免，"
                    f"或用不含豁免意图的措辞（推迟优化）描述。"
                )
                continue
            if not any(row_id in reason for reason in registered[rid]):
                errors.append(
                    f"known-technical-debt.md 条目 {row_id} 声明的 {rid} 豁免，exceptions.yml 中 rule_id={rid} "
                    f"的例外（{', '.join(registered[rid])[:80]}…）均未在 reason 中回指条目 {row_id}。"
                    f"请将条目 {row_id} 补记入对应例外的 reason，或在 exceptions.yml 新增该豁免。"
                )

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
    行格式 `- R<id>：<title> — <description>`：把判定条件并入跨工具入口，使不自动加载
    CLAUDE.md 的工具也能拿到可执行的最小安全集（GOV-01）。INVARIANT 与 R18 条目在
    redlines.yml 均有 description 字段（缺字段即刻 KeyError 暴露，不做静默回退）。
    """
    import yaml  # 延迟 import: PyYAML 是 transitive 依赖, 避免未安装时影响其他检查

    data = yaml.safe_load(REDLINES_YAML_PATH.read_text(encoding="utf-8"))
    redlines = data["redlines"]
    lines: list[str] = []
    for entry in redlines:
        rule_type = entry.get("rule_type")
        if rule_type != "INVARIANT":
            continue
        lines.append(f"- {entry['id']}：{entry['title']} — {entry['description']}")
    # R18 为 WORKFLOW 但影响工作区整洁，显式追加为区块末项
    for entry in redlines:
        if entry.get("id") == "R18":
            lines.append(f"- {entry['id']}：{entry['title']} — {entry['description']}")
            break
    return lines


def _render_claude_executive_block() -> list[str]:
    """从 redlines.yml 渲染 CLAUDE.md 顶部「本次会话必须遵守」摘要生成区块行（不含包裹标记）。

    与 _render_agents_invariant_lines() 同源，均以 redlines.yml 为唯一事实源，按 rule_type 分组
    全量渲染 INVARIANT（不可豁免安全不变量）与 EXCEPTIONABLE（可豁免）红线，并追加 R18
    （工作区整洁）。修复手工摘要仅列 R5 可豁免而漏 R1（EXCEPTIONABLE，例外 16 条）导致的
    「第一屏」失真（F-04，机制推广 DOC-08）。
    """
    import yaml  # 延迟 import: 与 _render_agents_invariant_lines 保持一致

    data = yaml.safe_load(REDLINES_YAML_PATH.read_text(encoding="utf-8"))
    redlines = data["redlines"]

    def _join(rule_type: str, sep: str) -> str:
        return sep.join(f"{entry['id']} {entry['title']}" for entry in redlines if entry.get("rule_type") == rule_type)

    r18 = next(entry for entry in redlines if entry.get("id") == "R18")
    return [
        f"> - **不可豁免安全不变量（INVARIANT，先读后写）**：{_join('INVARIANT', ' · ')}",
        f"> - **可豁免（EXCEPTIONABLE，经 exceptions.yml 例外注册豁免）**：{_join('EXCEPTIONABLE', ' / ')}",
        f"> - **工作区整洁**：R18 {r18['title']}（跨多文件任务须 git worktree 隔离）",
    ]


def _check_agents_declaration(content: str) -> list[str]:
    """校验 AGENTS.md 最小安全集区块前的声明句是否披露组成规则（含 R18）。

    生成区块内容本身由 _render_agents_invariant_lines + check_agents_md_sync 守护，
    但区块外的声明句（自然语言，AGENTS.md 中为普通段落，非引用块）可能与之脱节。
    本函数取 start_tag 之前**最后一段连续正文**（跨空行分隔，兼容普通段落与 `>` 引用块），
    断言其同时含 `INVARIANT` 与 `R18` 两个关键词（fail-closed：start_tag 前无正文段
    或 start_tag 缺失时显式报错，不静默跳过）。
    """
    start_tag = "<!-- generated:redlines-invariant -->"
    start = content.find(start_tag)
    if start == -1:
        return ["AGENTS.md 缺少生成区块标记（start_tag 缺失），无法核实区块前声明句是否披露组成规则"]
    lines = content[:start].splitlines()
    # 跳过尾随空行，定位最后一段正文的首行边界
    i = len(lines) - 1
    while i >= 0 and not lines[i].strip():
        i -= 1
    if i < 0:
        return ["AGENTS.md 最小安全集区块前缺少声明句（无正文段落），无法核实其披露组成规则"]
    paragraph: list[str] = []
    while i >= 0 and lines[i].strip():
        paragraph.append(lines[i])
        i -= 1
    merged = " ".join(reversed(paragraph))
    errors: list[str] = []
    for keyword in ("INVARIANT", "R18"):
        if keyword not in merged:
            errors.append(
                f"AGENTS.md 声明句未披露区块包含 {keyword}（区块组成 = INVARIANT 全量 + R18），"
                "请与 check_agents_md_sync 组成规则保持同步，勿使声明句与生成区块脱节"
            )
    return errors


def _generated_block_sync(
    content: str,
    start_tag: str,
    expected: list[str],
    mismatch_msg: str,
    missing_msg: str,
) -> list[str]:
    """比对文档中生成区块内容是否等于预期渲染结果（DOC-08，F-04 推广到 CLAUDE.md）。

    定位 start_tag 与 _GENERATED_END_TAG 之间内容，整段 strip 后按行与 expected 比对。
    AGENTS.md 最小安全集与 CLAUDE.md 顶部摘要两个生成区块共用此同一比对机制，避免分叉。
    """
    start = content.find(start_tag)
    # 从 start_tag 之后查找结束标记：同一文档可含多个生成区块（如 AGENTS.md 的红线最小安全集
    # 区块 + 最小验证命令区块，M2），取全文第一个结束标记会误判为「缺少标记」
    end = content.find(_GENERATED_END_TAG, start + len(start_tag))
    if start == -1 or end == -1 or end <= start:
        return [missing_msg]
    block = content[start + len(start_tag) : end].strip().splitlines()
    if block != expected:
        return [mismatch_msg]
    return []


def check_agents_md_sync() -> list[str]:
    """校验 AGENTS.md 生成区块与 redlines.yml 一致性（DOC-08/DOC-13）。

    AGENTS.md 的 `<!-- generated:redlines-invariant -->` 与 `<!-- /generated -->` 之间内容
    必须等于由 redlines.yml 渲染的结果（见 _render_agents_invariant_lines），从机制上消除多源漂移；
    同时校验区块前声明句披露组成规则（见 _check_agents_declaration）。
    """
    errors: list[str] = []
    if not AGENTS_PATH.exists():
        return [f"AGENTS.md 不存在: {AGENTS_PATH}"]
    content = AGENTS_PATH.read_text(encoding="utf-8")
    start_tag = "<!-- generated:redlines-invariant -->"
    errors.extend(
        _generated_block_sync(
            content,
            start_tag,
            _render_agents_invariant_lines(),
            "AGENTS.md 生成区块与 redlines.yml 不一致（INVARIANT 红线 + R18）。"
            "请改正本 redlines.yml 后同步 AGENTS.md，勿手工修改生成区块。",
            f"AGENTS.md 缺少生成区块标记（{start_tag} / {_GENERATED_END_TAG}）",
        )
    )
    errors.extend(_check_agents_declaration(content))
    return errors


# M2（文档体系检视）：AGENTS.md 此前全文无任何命令——只加载它的工具链（Codex CLI 等）改完
# 代码不知道跑什么验证，而「变更类型 → 最小验证子集」恰是 AI 交付质量的关键约束（CONTRIBUTING.md
# canonical_for 含「最小命令入口」）。命令正本为 CONTRIBUTING.md「常用开发与测试命令」；
# 渲染源为下列常量，check_agents_md_min_verify_commands() 同时断言 CONTRIBUTING.md 含每条命令，
# 防「正本 ↔ 生成区块」双向漂移（与 redlines.yml → AGENTS.md 红线区块同机制）。
_MIN_VERIFY_COMMANDS: tuple[str, ...] = (
    "ruff check .",
    "ruff format --check .",
    "pre-commit run --all-files",
    "pyright",
    "python -m pytest tests/unit/ -v --tb=short",
)
_MIN_VERIFY_DOC_COMMANDS: tuple[str, ...] = (
    "python scripts/check_docs_consistency.py",
    "python -m pytest tests/unit/test_docs_consistency.py",
)


def _render_agents_min_verify_lines() -> list[str]:
    """渲染 AGENTS.md「最小验证命令」生成区块内容（不含包裹标记，M2）。"""
    chain = " → ".join(f"`{c}`" for c in _MIN_VERIFY_COMMANDS)
    doc_chain = " + ".join(f"`{c}`" for c in _MIN_VERIFY_DOC_COMMANDS)
    return [
        f"- **变更相关门禁**（提交/PR 前，顺序与 `.github/workflows/ci_cd.yml` 一致）：{chain}",
        "- **最小验证子集**（按变更范围裁剪，勿全量跑）：见 [CONTRIBUTING.md](./CONTRIBUTING.md#变更类型--最小验证子集)",
        f"- **仅 Markdown / 治理文档改动**：{doc_chain}",
        "- **不得声称未运行项已通过**；无法运行的验证需说明原因",
    ]


def check_agents_md_min_verify_commands() -> list[str]:
    """校验 AGENTS.md「最小验证命令」生成区块与正本一致（M2）。

    渲染 `<!-- generated:min-verify-commands -->` 区块并断言与 AGENTS.md 现状一致；
    同时断言正本 CONTRIBUTING.md（最小命令入口 canonical）含每条命令，防双向漂移。
    """
    errors: list[str] = []
    if not AGENTS_PATH.exists():
        return [f"AGENTS.md 不存在: {AGENTS_PATH}"]
    content = AGENTS_PATH.read_text(encoding="utf-8")
    start_tag = "<!-- generated:min-verify-commands -->"
    errors.extend(
        _generated_block_sync(
            content,
            start_tag,
            _render_agents_min_verify_lines(),
            "AGENTS.md 最小验证命令区块与渲染结果不一致。请更新渲染源（_MIN_VERIFY_COMMANDS 常量）后同步 AGENTS.md，勿手工修改生成区块。",
            f"AGENTS.md 缺少最小验证命令生成区块标记（{start_tag} / {_GENERATED_END_TAG}）",
        )
    )
    try:
        contributing = CONTRIBUTING_PATH.read_text(encoding="utf-8")
    except OSError:
        errors.append(f"CONTRIBUTING.md 不存在或不可读: {CONTRIBUTING_PATH}")
        return errors
    for cmd in (*_MIN_VERIFY_COMMANDS, *_MIN_VERIFY_DOC_COMMANDS):
        if cmd not in contributing:
            errors.append(
                f"AGENTS.md 最小验证命令区块引用的命令 '{cmd}' 未出现在正本 CONTRIBUTING.md 中"
                f"（M2：命令正本为最小命令入口 canonical，双向漂移防护）"
            )
    return errors


def check_claude_executive_sync() -> list[str]:
    """校验 CLAUDE.md 顶部「本次会话必须遵守」摘要生成区块与 redlines.yml 一致性（F-04）。

    与 check_agents_md_sync 同机制：CLAUDE.md 的 `<!-- generated:claude-executive -->` 与
    `<!-- /generated -->` 之间内容须等于 redlines.yml 渲染结果（见 _render_claude_executive_block）。
    """
    content = CLAUDE_PATH.read_text(encoding="utf-8")
    start_tag = "<!-- generated:claude-executive -->"
    return _generated_block_sync(
        content,
        start_tag,
        _render_claude_executive_block(),
        "CLAUDE.md 顶部摘要生成区块与 redlines.yml 不一致（INVARIANT + EXCEPTIONABLE + R18）。"
        "请改正本 redlines.yml 后同步生成区块，勿手工修改。",
        f"CLAUDE.md 缺少顶部摘要生成区块标记（{start_tag} / {_GENERATED_END_TAG}）",
    )


# --- DOC-01: 规则集元数据一致性（CLAUDES 与 CONTRIBUTING 的 ruleset_version/last_reviewed 同步）---
# 元数据格式（P2-07 统一格式）：
#   `> - ruleset_version: 1.3.0（...）`  与  `> - last_reviewed: 2026-09-03`
_RULESET_VERSION_PATTERN = re.compile(r"ruleset_version[：:]\s*([0-9]+\.[0-9]+\.[0-9]+)")
_LAST_REVIEWED_PATTERN = re.compile(r"last_reviewed[：:]\s*([0-9]{4}-[0-9]{2}-[0-9]{2})")


def _extract_metadata_versions(content: str) -> tuple[str | None, str | None]:
    """从 markdown 元数据块提取 (ruleset_version, last_reviewed)，缺失返回 None。"""
    m = _RULESET_VERSION_PATTERN.search(content)
    d = _LAST_REVIEWED_PATTERN.search(content)
    return (m.group(1) if m else None, d.group(1) if d else None)


def _extract_changelog_top_date() -> date | None:
    """从 ruleset-changelog.md「变更记录」表首条数据行提取变更日期（DS-05）。

    表头 `| ruleset_version | 变更日期 | 变更摘要 |`，首条数据行如
    `| 1.6.0 | 2026-09-14 | ... |`，日期为第 2 列（YYYY-MM-DD）。文件缺失、
    未找到数据行、或日期非法时返回 None（调用方 fail-closed 报错）。
    顶行判定与 check_ruleset_changelog_version 一致（锚定「变更记录」标题后取首个数据行）。
    """
    if not RULESET_CHANGELOG_PATH.exists():
        return None
    lines = RULESET_CHANGELOG_PATH.read_text(encoding="utf-8").splitlines()
    start = 0
    for _i, line in enumerate(lines):
        if line.strip() == "## 变更记录":
            start = _i + 1
            break
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if re.fullmatch(r"\|[\s\-:]+\|", stripped):
            continue  # 表格分隔行（|---|）
        cols = [c.strip() for c in stripped.strip("|").split("|")]
        if not cols or cols[0] in {"", "ruleset_version"}:
            continue  # 表头或空行
        if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", cols[0]) and len(cols) >= 2:
            try:
                return date.fromisoformat(cols[1])
            except ValueError:
                return None
    return None


def check_ruleset_metadata_consistency() -> list[str]:
    """检查项 12：CLAUDE.md / CONTRIBUTING.md / AGENTS.md 规则集元数据一致性（DOC-01/DOC-13/DS-05）。

    已由 _check_version_consistency（产品版本对应 pyproject.toml）覆盖「对应版本」，
    本检查补齐 `ruleset_version` 与 `last_reviewed` 两个字段——它们**从未被任何检查读取**
    （DOC-01 门禁空白的主因），修复后禁止再漂移。随后 DOC-13 将孤儿文件 AGENTS.md 一并纳入
    ruleset_version 守护（AGENTS 作为跨工具入口，规则集版本必须与宪法同步）。

    断言：
    1. 三份文件均有 ruleset_version，且值相等。
    2. 三份文件均有 last_reviewed，且值为合法日期（AGENTS 为跨工具入口，审核时间与宪法同步）。
    3. CONTRIBUTING.md / AGENTS.md 的 last_reviewed 均不早于 CLAUDE.md（更新自由度不低于宪法）。
    4. 三份文件的 last_reviewed 均不早于 ruleset-changelog.md 顶行变更日期（治理变更后须重新审核同步，DS-05）。
    """
    errors: list[str] = []
    claude_ver, claude_reviewed = _extract_metadata_versions(CLAUDE_PATH.read_text(encoding="utf-8"))
    contributing_ver, contributing_reviewed = _extract_metadata_versions(CONTRIBUTING_PATH.read_text(encoding="utf-8"))
    # AGENTS.md 为跨工具入口：纳入 ruleset_version 与 last_reviewed 双重同步（DS-05）。存在性由
    # check_agents_md_sync fail-closed 守护，故此处不存在时跳过 ruleset 校验。
    agents_ver: str | None = None
    agents_reviewed: str | None = None
    if AGENTS_PATH.exists():
        agents_ver, agents_reviewed = _extract_metadata_versions(AGENTS_PATH.read_text(encoding="utf-8"))

    if claude_ver is None:
        errors.append("CLAUDE.md 缺少 ruleset_version 元数据字段")
    if contributing_ver is None:
        errors.append("CONTRIBUTING.md 缺少 ruleset_version 元数据字段")
    if agents_ver is None:
        errors.append("AGENTS.md 缺少 ruleset_version 元数据字段")
    if claude_ver is not None and contributing_ver is not None and claude_ver != contributing_ver:
        errors.append(
            f"ruleset_version 漂移: CLAUDE.md={claude_ver} != CONTRIBUTING.md={contributing_ver} "
            "(两文件须同步，规则集版本规则变更时递增)"
        )
    if claude_ver is not None and agents_ver is not None and claude_ver != agents_ver:
        errors.append(
            f"ruleset_version 漂移: CLAUDE.md={claude_ver} != AGENTS.md={agents_ver} "
            "(AGENTS 为跨工具入口，规则集版本须与宪法同步)"
        )

    if claude_reviewed is None:
        errors.append("CLAUDE.md 缺少 last_reviewed 元数据字段")
    if contributing_reviewed is None:
        errors.append("CONTRIBUTING.md 缺少 last_reviewed 元数据字段")
    if agents_ver is not None and agents_reviewed is None:
        errors.append("AGENTS.md 缺少 last_reviewed 元数据字段")

    # DS-05：last_reviewed 不得早于 ruleset-changelog 顶行变更日期（治理变更后须重新审核同步）。
    changelog_date = _extract_changelog_top_date()
    if changelog_date is None:
        errors.append("ruleset-changelog.md「变更记录」表未找到合法顶行变更日期 (YYYY-MM-DD)")

    if claude_reviewed is not None and contributing_reviewed is not None and agents_reviewed is not None:
        try:
            claude_date = date.fromisoformat(claude_reviewed)
            contributing_date = date.fromisoformat(contributing_reviewed)
            agents_date = date.fromisoformat(agents_reviewed)
        except ValueError:
            errors.append("CLAUDE.md/CONTRIBUTING.md/AGENTS.md 的 last_reviewed 不是合法日期 (YYYY-MM-DD)")
        else:
            if contributing_date < claude_date:
                errors.append(
                    f"CONTRIBUTING.md last_reviewed({contributing_reviewed}) 早于 "
                    f"CLAUDE.md last_reviewed({claude_reviewed})"
                )
            if agents_date < claude_date:
                errors.append(
                    f"AGENTS.md last_reviewed({agents_reviewed}) 早于 CLAUDE.md last_reviewed({claude_reviewed})"
                )
            if changelog_date is not None:
                for _name, _date in (
                    ("CLAUDE.md", claude_date),
                    ("CONTRIBUTING.md", contributing_date),
                    ("AGENTS.md", agents_date),
                ):
                    if _date < changelog_date:
                        errors.append(
                            f"{_name} last_reviewed({_date.isoformat()}) 早于 "
                            f"ruleset-changelog 顶行变更日期({changelog_date.isoformat()}) "
                            "(治理变更后须重新审核并同步 last_reviewed)"
                        )
    return errors


def check_ruleset_changelog_version() -> list[str]:
    """检查项：规则集变更日志顶行版本与宪法正本一致（DS-10）。

    ruleset-changelog.md「变更记录」表在每次 ruleset_version 递增时于顶部追加一行，
    首条数据行应为当前正本最新版本。断言其与 CLAUDE.md 的 ruleset_version 一致
    （CLAUDE/CONTRIBUTING/AGENTS 三文件 ruleset_version 同步已由
    check_ruleset_metadata_consistency 守护，故取 CLAUDE.md 为正本即可）。
    变更日志缺失、首行无合法版本号、或版本漂移时一律 fail-closed 报错。
    """
    errors: list[str] = []
    if not RULESET_CHANGELOG_PATH.exists():
        try:
            display = RULESET_CHANGELOG_PATH.relative_to(ROOT)
        except ValueError:
            display = RULESET_CHANGELOG_PATH  # 注入/外部路径不在 ROOT 下
        errors.append(f"规则集变更日志不存在: {display}")
        return errors

    changelog_ver: str | None = None
    changelog_lines = RULESET_CHANGELOG_PATH.read_text(encoding="utf-8").splitlines()
    # 锚定「变更记录」标题后的片段：避免文件其它位置出现 x.y.z 首列行干扰取行（对抗检视 P2）
    start = 0
    for _i, _line in enumerate(changelog_lines):
        if _line.strip() == "## 变更记录":
            start = _i + 1
            break
    for line in changelog_lines[start:]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if re.fullmatch(r"\|[\s\-:]+\|", stripped):
            continue  # 表格分隔行（|---|）
        cols = [c.strip() for c in stripped.strip("|").split("|")]
        if not cols or cols[0] in {"", "ruleset_version"}:
            continue  # 表头（首列固定为 ruleset_version 标题）或空行
        if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", cols[0]):
            changelog_ver = cols[0]
            break  # 首条数据行即最新版本

    if changelog_ver is None:
        errors.append("ruleset-changelog.md「变更记录」表未找到首行合法 x.y.z 版本号")
        return errors

    claude_ver, _ = _extract_metadata_versions(CLAUDE_PATH.read_text(encoding="utf-8"))
    if claude_ver is None:
        errors.append("CLAUDE.md 缺少 ruleset_version 元数据字段")
        return errors
    if claude_ver != changelog_ver:
        errors.append(
            f"ruleset-changelog 顶行版本({changelog_ver}) != CLAUDE.md ruleset_version({claude_ver}) "
            "(ruleset_version 递增须在 ruleset-changelog.md 变更记录表顶部追加一行)"
        )
    return errors


# --- DOC-04: 决策树与其机器可读镜像的一致性（CLAUDE.md §1.8 ↔ canonical-topics.yml）---
# CLAUDE.md §1.8「必读入口」列既可能以 markdown 链接 `[text](./docs/x.md)` 出现，
# 也可能以裸路径文本出现（如 `docs/guides/how-to.md「7. 新增回测配置」`、`CONTRIBUTING.md「...」`）。
# canonical-topics.yml 的 canonical 值统一为仓库相对路径（如 `docs/patterns/mvvm.md` / `CONTRIBUTING.md`）。
_CANONICAL_TOPICS_REQUIRED = frozenset({"id", "title", "canonical"})

# 宪法 §1.8 采用「合并行」：一行任务类型承载 yml 拆分的多个主题（共享同一 canonical）。
# 集合级双向断言会压平该分布，使「主题归属被指到别的主体」或「合并行承载主题被删」静默漏报。
# 本白名单按 yml 稳定 id 声明每个共享 canonical 的期望主题归属，逐主题绑定仅升级方向 2。
# 新增共享 canonical（同一路径被多个 topic 引用）时须在此登记，否则方向 2 视为单主题 canonical。
_DECISION_TREE_MERGED_IDS: dict[str, set[str]] = {
    "docs/flet/README.md": {"ui-view", "ui-layout", "i18n"},
    "docs/patterns/config-quality-perf.md": {"performance", "config"},
    "docs/guides/testing.md": {"testing", "e2e-testing"},
    "docs/guides/how-to.md": {"backtest", "embedded-pg"},
    "docs/guides/ci-cd.md": {"ci-deps", "release"},
}

# 决策树元条目（非具体任务路由，承载「未列类型 → 按层选最接近入口」的兜底规则）。
# 其 canonical 指向 CLAUDE.md §3/§4（红线 + 架构边界），不参与任务到正本的路由映射，
# DOC-04 方向 2（canonical 必须在 §1.8 决策树出现）据此豁免（F-11）。
_DECISION_TREE_META_IDS: frozenset[str] = frozenset({"fallback"})


def _load_canonical_topics() -> list[dict] | None:
    """加载 canonical-topics.yml 的 topics 列表；无法解析或结构非法时返回 None。"""
    try:
        import yaml  # 延迟 import: PyYAML 是 transitive 依赖

        data = yaml.safe_load(CANONICAL_TOPICS_YAML_PATH.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("topics"), list):
        return None
    return [t for t in data["topics"] if isinstance(t, dict)]


def _extract_decision_tree_targets(claude_content: str) -> set[str]:
    """从 CLAUDE.md §1.8 决策树表格「必读入口」列提取目标路径集合（去 ./ 前缀、去重）。

    表格行格式：`| 任务类型 | 必读入口 |`。仅处理以 `|` 开头、含第二列的表格行，
    从第二列提取所有形如 `docs/x/y.md` 或 `CONTRIBUTING.md` 的路径 token。
    归一化：剥离前导 `./`，保留仓库相对路径。
    """
    targets: set[str] = set()
    in_decision_table = False
    for line in claude_content.splitlines():
        stripped = line.strip()
        if not in_decision_table:
            if stripped.startswith("|") and "必读入口" in line:
                in_decision_table = True
            else:
                continue
        if not stripped.startswith("|"):
            break  # 决策树表格结束（其后的引用块说明行不含 `|` 前缀）
        cols = stripped.split("|")
        if len(cols) < 2:
            continue
        entry_col = cols[2] if len(cols) >= 3 else cols[1]
        for token in re.findall(r"(?:\./)?(?:(?:docs|requirements)/[\w.\-/]+\.md|CONTRIBUTING\.md)", entry_col):
            targets.add(token.removeprefix("./"))
    return targets


def check_decision_tree_mapping() -> list[str]:
    """检查项 13：CLAUDE.md §1.8 决策树与 canonical-topics.yml 镜像双向一致（DOC-04）。

    宪法 §1.8 是「本体」，canonical-topics.yml 是「机器可读镜像」，二者对同一主题必须指向
    同一正本。双向断言（允许一对多：宪法合并行 ↔ yml 拆分主题，只要 canonical 值相同）：
    1. 宪法 §1.8 表格「必读入口」列出现的每个目标路径，必须能在 yml canonical 中找到。
    2. yml 每个 canonical 值，必须能在宪法 §1.8 表格「必读入口」列中找到；共享 canonical
       额外按 _DECISION_TREE_MERGED_IDS 白名单逐主题绑定，防止主题归属被指到别的主体。
    """
    errors: list[str] = []
    claude_content = CLAUDE_PATH.read_text(encoding="utf-8")
    claude_targets = _extract_decision_tree_targets(claude_content)

    topics = _load_canonical_topics()
    if topics is None:
        errors.append("canonical-topics.yml 无法解析或无 topics 列表，跳过决策树映射校验")
        return errors

    yml_canonical_map: dict[str, set[str]] = {}
    missing_fields = []
    for idx, topic in enumerate(topics, 1):
        missing = _CANONICAL_TOPICS_REQUIRED - topic.keys()
        if missing:
            missing_fields.append(f"topics[{idx}] 缺字段 {sorted(missing)}")
        canonical = topic.get("canonical")
        topic_id = topic.get("id")
        if isinstance(canonical, str) and isinstance(topic_id, str):
            yml_canonical_map.setdefault(canonical.removeprefix("./"), set()).add(topic_id)
    errors.extend(missing_fields)

    # 方向 1: 宪法入口都应在 yml 中登记（不含 canonical-topics.yml 自身引用）
    for target in sorted(claude_targets):
        if target == "docs/governance/canonical-topics.yml":
            continue
        if target not in yml_canonical_map:
            errors.append(f"决策树映射: CLAUDE.md §1.8 引用目标 '{target}' 未在 canonical-topics.yml 中登记")

    # 方向 2: 逐主题绑定 canonical 归属（补齐集合级丢失「分布/归属」的交叉错配与归属丢失）
    for canonical, topic_ids in sorted(yml_canonical_map.items()):
        # 元条目（fallback 兜底行）的 canonical 不是任务路由目标，豁免跨引用校验（DOC-04 方向 2）
        if topic_ids == _DECISION_TREE_META_IDS:
            continue
        if canonical in _DECISION_TREE_MERGED_IDS:
            # 共享 canonical：须在宪法出现，且 yml 归属与白名单（宪法合并行承载主题集）一致
            if canonical not in claude_targets:
                errors.append(f"决策树映射: canonical '{canonical}' 已登记合并但未在 CLAUDE.md §1.8 决策树中出现")
            expected = _DECISION_TREE_MERGED_IDS[canonical]
            if topic_ids != expected:
                errors.append(
                    f"决策树映射: canonical '{canonical}' 归属 {sorted(topic_ids)} 与宪法 §1.8 "
                    f"合并行承载 {sorted(expected)} 不一致（同步 yml 归属或登记合并白名单）"
                )
            continue
        # 单主题 canonical：须在宪法出现，且不得被多主题共享（如确需合并须登记白名单）
        if canonical not in claude_targets:
            errors.append(
                f"决策树映射: canonical-topics.yml 的 canonical '{canonical}' 未在 CLAUDE.md §1.8 决策树中出现"
            )
        if len(topic_ids) != 1:
            errors.append(
                f"决策树映射: canonical '{canonical}' 被多个主题 {sorted(topic_ids)} 共享但未登记 "
                f"_DECISION_TREE_MERGED_IDS，如属宪法 §1.8 合并行请登记白名单"
            )
    return errors


def _canonical_routes_to_workflow(doc_content: str, doc_dir: Path, workflow_target: Path) -> bool:
    """canonical 文档中是否存在一条真实 markdown 链接，经相对解析后指向 workflow 目标文档。

    纯文本/反引号提及、代码块内示例链接不算真路由：DOC-05 要求 canonical 入口文档完整承担
    条件路由责任，必须以可点击的相对路径链接指向 workflow 文档。
    """
    in_code_block = False
    for line in doc_content.splitlines():
        if line.lstrip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        for m in _MD_LINK_PATTERN.finditer(line):
            url = m.group(2).strip()
            if url.startswith(("http://", "https://", "mailto:")):
                continue
            path_part = url.split("#", 1)[0].split("?", 1)[0]
            if not path_part:
                continue
            try:
                target = (doc_dir / path_part).resolve()
            except Exception:
                continue
            if target == workflow_target:
                return True
    return False


def check_canonical_routing() -> list[str]:
    """检查项 14：canonical 入口承担条件路由责任（DOC-05）。

    canonical-topics.yml 的字段说明声明「入口文档负责条件路由」。本检查将对「带工作流入口」
    主题（yml 中声明了 `workflow` 字段）做可执行断言：其 canonical 文档中必须存在指向该
    workflow 文档的真实 markdown 链接（纯文本/反引号提及不算真路由）。

    说明：backtest / embedded-pg 两主题的 canonical 直接就是 how-to.md 本身，无需 workflow
    字段，故不在校验范围（它们天然承担操作步骤承载）。
    """
    errors: list[str] = []
    topics = _load_canonical_topics()
    if topics is None:
        errors.append("canonical-topics.yml 无法解析或无 topics 列表，跳过 canonical 路由校验")
        return errors

    for idx, topic in enumerate(topics, 1):
        workflow = topic.get("workflow")
        canonical = topic.get("canonical")
        # 仅校验显式声明 workflow 的主题（strategy / dao / ui-view 等）
        if not isinstance(workflow, str) or not isinstance(canonical, str):
            continue
        canonical_path = ROOT / canonical.removeprefix("./")
        if not canonical_path.exists():
            errors.append(f"topics[{idx}] canonical 路径不存在: {canonical}")
            continue
        workflow_target = (ROOT / workflow.removeprefix("./")).resolve()
        doc_content = canonical_path.read_text(encoding="utf-8")
        # 真链接断言：必须存在指向 workflow 文档的 markdown 链接（纯文本/反引号提及不算真路由）
        if not _canonical_routes_to_workflow(doc_content, canonical_path.parent, workflow_target):
            errors.append(
                f"topics[{idx}] (id={topic.get('id')}) canonical '{canonical}' 未路由到 "
                f"workflow '{workflow}'（真链接断言：应含指向 {Path(workflow).name} 的 markdown 链接）"
            )
    return errors


# 含『路由（必读 / 条件触发 / 完成判定）』三段式或『完成判定』章节的 canonical 反例断言（不匹配即报错）：
# 主题正本须承载『完成判定』，否则 AI 交付『做没做完』只能自行推断，交付质量随任务类型随机波动（F-03）。
# 与 check_canonical_routing()（workflow 条件路由）同一思路，实现成本低。
def _completion_section(content: str) -> str | None:
    """「完成判定」章节的正文块：截取首个含「完成判定」标题之后、下一个同级或更高级标题之前的行。"""
    lines = content.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m and "完成判定" in m.group(2):
            level = len(m.group(1))
            block: list[str] = []
            for j in range(i + 1, len(lines)):
                mm = re.match(r"^(#{1,6})\s+", lines[j])
                if mm and len(mm.group(1)) <= level:
                    break
                block.append(lines[j])
            return "\n".join(block)
    return None


def check_canonical_completion_criteria() -> list[str]:
    """检查项：canonical 入口文档必须承载「完成判定」判据（F-03）。

    canonical-topics.yml 登记为任务入口（canonical）的文档须承载「完成判定」可打勾判据
    与最小验证命令，否则 AI 完成质量随任务类型随机波动（CLAUDE.md §1.5/§1.9 的落地依赖
    入口文档给出判据）。除断言 heading 含「完成判定」外，还校验该章节正文含「最小验证命令」
    字样与至少一条 `- ` 判据，防只挂空标题不写判据（内容门禁）。
    遍历每个 distinct canonical 文档，跳过 fallback 元条目（指向 CLAUDE.md §3/§4 兜底，
    非具体任务正本，DOC-04 方向 2 / F-11 豁免）。
    """
    errors: list[str] = []
    topics = _load_canonical_topics()
    if topics is None:
        errors.append("canonical-topics.yml 无法解析或无 topics 列表，跳过完成判定校验")
        return errors

    seen_canonical: set[str] = set()
    for idx, topic in enumerate(topics, 1):
        if topic.get("id") in _DECISION_TREE_META_IDS:
            continue  # fallback 元条目（CLAUDE.md），非具体任务正本，豁免
        canonical = topic.get("canonical")
        if not isinstance(canonical, str):
            continue
        norm = canonical.removeprefix("./")
        if norm in seen_canonical:
            continue  # 共享 canonical 只校验一次
        seen_canonical.add(norm)
        canonical_path = ROOT / norm
        if not canonical_path.exists():
            # 路径有效性已由 check_canonical_topics_consistency() 承担，此处不重复报
            continue
        content = canonical_path.read_text(encoding="utf-8")
        block = _completion_section(content)
        if block is None:
            errors.append(
                f"topics[{idx}] (id={topic.get('id')}) canonical '{norm}' 缺「完成判定」章节"
                f"（F-03：canonical 入口须承载可打勾完成判定 + 最小验证命令）"
            )
            continue
        if "最小验证命令" not in block:
            errors.append(
                f"topics[{idx}] (id={topic.get('id')}) canonical '{norm}' 的「完成判定」章节"
                f"缺「最小验证命令」段（F-03：对接 CONTRIBUTING 变更类型 → 最小验证子集）"
            )
        if not any(re.match(r"^\s*-\s+", ln) for ln in block.splitlines()):
            errors.append(
                f"topics[{idx}] (id={topic.get('id')}) canonical '{norm}' 的「完成判定」章节"
                f"无可打勾判据列表（至少一条 `- ` 项）"
            )
    return errors


def check_canonical_docs_are_gated() -> list[str]:
    """检查项 N：被登记为 canonical 正本的文档必须落在文档门禁受检范围内（F-13）。

    canonical-topics.yml 把若干文档登记为正本；正本若不在 CHECKED_DOCS 内，就得不到
    锚点死链 / 相对链接死链 / 版本一致 / 治理 ID / 书名号引用等任何门禁保护，准确性只靠
    人工维护。本检查断言：每个 canonical 值都必须落在受检范围内。

    与既有反向覆盖检查（check_enforcement_reverse_coverage）同思路：配置/代码里被声明为
    正本的东西，在门禁中必须受保护。
    """
    errors: list[str] = []

    # 受检集合：CHECKED_DOCS 解析后的绝对路径（含小写归一，规避 Windows 盘符/大小写差异）
    checked: set[str] = set()
    for d in CHECKED_DOCS:
        try:
            resolved = str(d.resolve())
        except OSError:
            continue
        checked.add(resolved)
        checked.add(resolved.lower())

    topics = _load_canonical_topics()
    if topics is None:
        errors.append("canonical-topics.yml 无法解析或无 topics 列表，跳过 canonical 受检范围校验")
        return errors

    for idx, topic in enumerate(topics, 1):
        canonical = topic.get("canonical")
        if not isinstance(canonical, str):
            continue
        canonical_path = ROOT / canonical.removeprefix("./").strip()
        if not canonical_path.exists():
            errors.append(f"topics[{idx}] (id={topic.get('id')}) canonical 路径不存在: {canonical}")
            continue
        try:
            resolved = str(canonical_path.resolve())
        except OSError:
            errors.append(f"topics[{idx}] (id={topic.get('id')}) canonical 无法解析: {canonical}")
            continue
        if resolved not in checked and resolved.lower() not in checked:
            errors.append(
                f"topics[{idx}] (id={topic.get('id')}) 正本 '{canonical}' 不在文档门禁受检范围 "
                f"CHECKED_DOCS 内：声明为正本却不受保护 (F-13)"
            )
    return errors


# --- DOC-07/DOC-11: 文档索引全覆盖（CONTRIBUTING.md 或 docs/README.md 目录级引用覆盖 docs/**/*.md）---
# 职责单一：断言 `docs/**/*.md` 中每个文件都能被索引源引用，目录级引用视为覆盖其下全部文件。
# 固定双索引源（CONTRIBUTING_PATH / DOCS_README_PATH），不依赖手动维护的清单。
# docs 根目录由 DOCS_README_PATH 推导，便于单测 monkeypatch 注入临时树。


def _docs_doc_covered(doc: Path, index_sources: list[Path]) -> bool:
    """判断 doc 是否被任一索引源的链接（文件级或目录级）覆盖。"""
    docs_root = DOCS_README_PATH.parent
    for src in index_sources:
        content = src.read_text(encoding="utf-8")
        for m in _MD_LINK_PATTERN.finditer(content):
            url = m.group(2).strip()
            if url.startswith(("http://", "https://", "mailto:")):
                continue
            target = url.split("#", 1)[0].split("?", 1)[0].rstrip("/")
            if not target:
                continue
            resolved = (src.parent / target).resolve()
            try:
                resolved.relative_to(docs_root)
            except ValueError:
                continue  # 目标在 docs/ 外，不构成覆盖
            if resolved.is_dir():
                # 目录级引用覆盖其下全部文件（含嵌套子目录）
                if doc.is_relative_to(resolved):
                    return True
            else:
                if resolved == doc:
                    return True
    return False


def check_docs_index_completeness() -> list[str]:
    """检查项 15：文档索引全覆盖（DOC-07 / DOC-11）。

    断言 `docs/` 下每个治理文件（markdown + yml/yaml/json）都能在
    `CONTRIBUTING.md`「文档索引」或 `docs/README.md`「目录结构」中被引用，
    目录级引用（指向目录的链接）视为覆盖其下全部文件。

    这是 check_flet_hub_completeness() 向全 docs 目录的推广，同时闭合
    DOC-07（reviews 检视报告不可发现）与 DOC-11（索引自称全覆盖实缺目录）。
    DS-10：扫描范围由仅 `.md` 扩展为 `.md`/`.yml`/`.yaml`/`.json`，使
    docs/ 下非 markdown 治理文件（redlines.yml / canonical-topics.yml 等）
    同样纳入索引全覆盖门禁，避免新增治理文件成为不可发现的暗坑。
    """
    errors: list[str] = []
    if not DOCS_README_PATH.exists():
        return [f"docs 索引入口不存在: {DOCS_README_PATH}"]

    docs_root = DOCS_README_PATH.parent
    index_sources = [CONTRIBUTING_PATH, DOCS_README_PATH]
    covered_suffixes = (".md", ".yml", ".yaml", ".json")
    candidates = sorted(p for p in docs_root.rglob("*") if p.is_file() and p.suffix in covered_suffixes)
    for doc in candidates:
        if doc == DOCS_README_PATH:
            continue
        # 真实 gitignored 本地产物 / 归档目录豁免：Path.rglob 不识别 .gitignore，未跟踪/被忽略产物
        # 不属于「需进索引的治理活文档」，跳过避免误报「未进索引」（兼容 _LOCAL_ARTIFACT_DIRS 别名）。
        if any(doc.is_relative_to(d) for d in _GITIGNORED_ARTIFACT_DIRS) or any(
            doc.is_relative_to(d) for d in _LOCAL_ARTIFACT_DIRS
        ):
            continue
        if _docs_doc_covered(doc, index_sources):
            continue
        errors.append(
            f"文档索引全覆盖: {doc.relative_to(docs_root).as_posix()} "
            "未被 CONTRIBUTING.md 或 docs/README.md 引用（目录级引用视为覆盖其下文件）"
        )
    return errors


# --- DOC-07: 检视方法论文档登记（docs/reviews/README.md 文件级登记顶层方法论）---
# check_docs_index_completeness 的目录级引用（`[reviews/](./reviews/)`）只保证「顶层目录可达」，
# 无法要求「新增方法论文档必须被登记」。本检查仿 check_flet_hub_completeness，把 docs/reviews/
# 顶层方法论文档的登记从「目录级覆盖」收紧到「README 文件级链接」，使新增方法论对新会话可达。
# 仅枚举 docs/reviews/*.md 顶层文件（排除 README 自身与子目录）；子目录（review-profiles/、
# evals/）由各自子 README 与 check_docs_index_completeness 兜底。轮次报告落根 reviews/
# （gitignored 本地产物）不在仓库扫描范围。


def check_reviews_index_completeness() -> list[str]:
    """检查项 17：检视方法论文档登记完整性（DOC-07）。

    确保 docs/reviews/README.md 以文件级链接登记全部顶层方法论文档
    （ai-review / appendix / quality-dimensions / scenario-completeness 等），
    使「检视方法论 / 轮次清单」对新会话一跳可达（结论正文为本地 gitignored 产物，不入仓库）。
    """
    errors: list[str] = []
    if not REVIEWS_README_PATH.exists():
        return [f"reviews 索引入口不存在: {REVIEWS_README_PATH}"]

    readme_content = REVIEWS_README_PATH.read_text(encoding="utf-8")

    # 枚举 docs/reviews/ 顶层 .md（排除 README 自身）
    actual_files: set[str] = set()
    for path in REVIEWS_README_PATH.parent.glob("*.md"):
        if path.name == "README.md":
            continue
        actual_files.add(path.name)

    # 提取 README 中指向 docs/reviews/ 目录内 .md 文件的链接的文件名集合
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
            if url.startswith(("http://", "https://", "mailto:")):
                continue
            url_path = url.split("#", 1)[0].split("?", 1)[0]
            if not url_path:
                continue
            target_path = (REVIEWS_README_PATH.parent / url_path).resolve()
            try:
                rel = target_path.relative_to(REVIEWS_DOCS_DIR)
            except ValueError:
                continue  # 目标在 docs/reviews/ 外，跳过（如 ../../CLAUDE.md、../debt/...）
            if len(rel.parts) != 1:
                # 仅统计顶层（`./xxx.md` 形式）引用；轮次表根 reviews/ 路径/子目录引用不纳入登记集
                continue
            url_basename = rel.name
            if url_basename.endswith(".md") and url_basename != "README.md":
                referenced_files.add(url_basename)

    # 未登记的顶层文档（按前缀区分方法论文档与轮次报告摘要）
    for fname in sorted(actual_files - referenced_files):
        if re.match(r"^\d{4}-\d{2}-\d{2}", fname):
            errors.append(
                f"检视文档登记: docs/reviews/README.md 轮次表未登记顶层检视文件 '{fname}'"
                "（过程报告正文请落根目录 reviews/，如确需入库请在 README.md 登记）"
            )
        else:
            errors.append(f"检视方法论文档登记: docs/reviews/README.md 未登记顶层方法论文档 '{fname}'")
    # 幽灵链接（README 引用不存在的 docs/reviews/ 内文档）
    for fname in sorted(referenced_files - actual_files):
        errors.append(f"检视方法论文档登记: docs/reviews/README.md 引用了不存在的文档 '{fname}'")

    # GOV-04: 检视结论结构化登记索引（findings/README.md）须存在且被本 README 文件级登记
    findings_readme = REVIEWS_DOCS_DIR / "findings" / "README.md"
    if not findings_readme.exists():
        errors.append("检视结论登记: docs/reviews/findings/README.md 不存在（GOV-04 结论结构化入库机制缺失）")
    elif not any("./findings/README.md" in m.group(0) for m in _MD_LINK_PATTERN.finditer(readme_content)):
        errors.append("检视方法论文档登记: docs/reviews/README.md 未登记 'findings/README.md'（GOV-04 结论索引）")
    return errors


def check_reviews_findings_index() -> list[str]:
    """检查项：检视结论登记索引（GOV-04）。

    docs/reviews/findings/ 下每个结论文件（*.json / *.md，排除 README.md 自身）必须被
    findings/README.md 以文件级链接登记；README 引用不存在的结论文件即幽灵链接报错。
    新增轮次结论未登记 → 新会话不可溯源，等同 GOV-04 结论丢失回归。
    """
    errors: list[str] = []
    findings_dir = REVIEWS_DOCS_DIR / "findings"
    readme = findings_dir / "README.md"
    if not findings_dir.is_dir() or not readme.exists():
        # 目录缺失由 check_reviews_index_completeness 报告
        return errors
    readme_content = readme.read_text(encoding="utf-8")

    suffixes = (".json", ".md")
    actual = {p.name for p in findings_dir.glob("*") if p.is_file() and p.suffix in suffixes and p.name != "README.md"}
    referenced = {
        m.group(2).strip().split("/")[-1]
        for m in _MD_LINK_PATTERN.finditer(readme_content)
        if m.group(2).strip().endswith(suffixes)
        and m.group(2).strip().split("/")[-1] != "README.md"
        and not m.group(2).strip().startswith("../")
    }
    for fname in sorted(actual - referenced):
        errors.append(f"检视结论登记: docs/reviews/findings/README.md 未登记结论文件 '{fname}'")
    for fname in sorted(referenced - actual):
        errors.append(f"检视结论登记: docs/reviews/findings/README.md 引用了不存在的结论文件 '{fname}'")
    return errors


# --- GDR-12: ADR 文件级索引完整性（CONTRIBUTING.md 文件级登记全部 docs/adr/*.md）---
def check_adr_index_completeness() -> list[str]:
    """检查项：ADR 决策文档文件级索引完整性（GDR-12）。

    确保 CONTRIBUTING.md「docs/adr/」小节文件级登记了 docs/adr/*.md 全部 ADR 文档，
    防止新增 ADR 因仅有目录级引用而成为不可发现的暗坑。
    """
    errors: list[str] = []
    if not ADR_DOCS_DIR.is_dir():
        return [f"ADR 目录不存在: {ADR_DOCS_DIR}"]
    if not CONTRIBUTING_PATH.exists():
        return [f"CONTRIBUTING.md 不存在: {CONTRIBUTING_PATH}"]

    contributing_content = CONTRIBUTING_PATH.read_text(encoding="utf-8")

    # 枚举 docs/adr/ 下全部 *.md（排除 README 自身）
    actual_files: set[str] = set()
    for path in ADR_DOCS_DIR.glob("*.md"):
        if path.name == "README.md":
            continue
        actual_files.add(path.name)

    # 提取 CONTRIBUTING.md 中指向 docs/adr/*.md 文件的链接的文件名集合
    referenced_files: set[str] = set()
    in_code_block = False
    for line in contributing_content.splitlines():
        if line.lstrip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        for m in _MD_LINK_PATTERN.finditer(line):
            url = m.group(2).strip()
            if url.startswith(("http://", "https://", "mailto:")):
                continue
            url_path = url.split("#", 1)[0].split("?", 1)[0]
            if not url_path:
                continue
            target_path = (CONTRIBUTING_PATH.parent / url_path).resolve()
            try:
                rel = target_path.relative_to(ADR_DOCS_DIR)
            except ValueError:
                continue  # 目标在 docs/adr/ 外，跳过
            if len(rel.parts) == 1 and rel.name.endswith(".md"):
                referenced_files.add(rel.name)

    # 未登记的 ADR 文档
    for fname in sorted(actual_files - referenced_files):
        errors.append(f"ADR 索引完整性: CONTRIBUTING.md 未登记 ADR 文档 '{fname}'")
    # 幽灵链接（CONTRIBUTING.md 引用不存在的 docs/adr/ 内文档）
    for fname in sorted(referenced_files - actual_files):
        errors.append(f"ADR 索引完整性: CONTRIBUTING.md 引用了不存在的 ADR 文档 '{fname}'")
    return errors


# scripts/ Python 脚本路径引用（F-08：工程脚本清单完整性）。仅匹配仓库根 `scripts/<name>.py`
# 顶层脚本，排除 `.rs`/`.ps1` 及子目录引用与带 `#`/`?` 后缀的链接形态。
_SCRIPT_FILE_REF = re.compile(r"scripts/([A-Za-z0-9_][A-Za-z0-9_.-]*\.py)")


def check_scripts_index_completeness() -> list[str]:
    """检查项：工程脚本清单完整性（F-08）。

    确保 docs/guides/ci-cd.md 以 `scripts/<name>.py` 形式登记了 scripts/*.py 全部工程脚本，
    防止新增脚本（pre-commit hook / CI 步骤 / 维护工具）因仅有文件存在而成为 AI 不可发现的暗坑
    （CLAUDE.md §3.2「复用优先」要求 AI 能通过文档发现既有脚本及其触发时机/是否 CI 强制）。

    参照既有 check_adr_index_completeness / check_flet_hub_completeness 的「目录实际文件 ⊆ 文档引用」
    双向模式：未登记（实际有文件、文档没写）与幽灵引用（文档写了、实际无文件）均报错。
    """
    errors: list[str] = []
    if not SCRIPTS_DIR.is_dir():
        return [f"脚本目录不存在: {SCRIPTS_DIR}"]
    if not CI_CD_PATH.exists():
        return [f"ci-cd.md 不存在: {CI_CD_PATH}"]

    content = CI_CD_PATH.read_text(encoding="utf-8")
    actual_files: set[str] = {p.name for p in SCRIPTS_DIR.glob("*.py") if p.is_file()}

    referenced_files: set[str] = set()
    in_code_block = False
    for line in content.splitlines():
        if line.lstrip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        for m in _SCRIPT_FILE_REF.finditer(line):
            referenced_files.add(m.group(1))

    # 未登记的工程脚本
    for fname in sorted(actual_files - referenced_files):
        errors.append(f"脚本索引完整性: ci-cd.md 未登记工程脚本 '{fname}'")
    # 幽灵引用（ci-cd.md 引用不存在的 scripts/*.py）
    for fname in sorted(referenced_files - actual_files):
        errors.append(f"脚本索引完整性: ci-cd.md 引用了不存在的工程脚本 '{fname}'")
    return errors


# --- DOC-09: 治理 id 引用一致性（EX-\d{4} 双向：注册表 ↔ 消费文档）---
# 宪法曾引用未登记的 EX-0001（复现 GOV-01，DOC-09）。本检查把「引用必须落在注册表、
# 登记必须被消费」沉淀为机制：任一方向漂移即报错，防止悬空/孤儿例外长期存活。
# 消费语料 = CLAUDE.md + CONTRIBUTING.md + docs/**/*.md（exceptions.yml 自身是注册表非消费者）。
_EX_ID_PATTERN = re.compile(r"\bEX-\d{4}\b")

# EX 引用语料豁免目录（用于 check_governance_id_references()，GDR-07）：
# 包含全部 gitignored 目录，另加受跟踪但属评测用例/注入测试语料的目录（evals/），
# 防止评测负例中的历史/演示 EX 编号充当活引用掩盖孤儿判定。
_EX_REF_EXCLUDED_DIRS: tuple[Path, ...] = (
    *_GITIGNORED_ARTIFACT_DIRS,
    ROOT / "docs" / "reviews" / "evals",
)


def _load_exception_ids() -> list[str] | None:
    """加载 exceptions.yml 已登记例外 id 列表；无法解析或结构非法返回 None。"""
    try:
        import yaml  # 延迟 import: PyYAML 是 transitive 依赖

        data = yaml.safe_load(EXCEPTIONS_YAML_PATH.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("exceptions"), list):
        return None
    return [e["id"] for e in data["exceptions"] if isinstance(e, dict) and isinstance(e.get("id"), str)]


def _load_exception_r1_count() -> int:
    """统计 exceptions.yml 中 rule_id=R1 的例外数；解析失败或结构非法返回 -1（调用方跳过断言）。"""
    try:
        import yaml  # 延迟 import: PyYAML 是 transitive 依赖

        data = yaml.safe_load(EXCEPTIONS_YAML_PATH.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return -1
    if not isinstance(data, dict) or not isinstance(data.get("exceptions"), list):
        return -1
    return sum(1 for e in data["exceptions"] if isinstance(e, dict) and e.get("rule_id") == "R1")


def _load_r1_ignore_imports() -> tuple[list[str], list[str]] | None:
    """加载 pyproject.toml 全部 R1 契约（名称以 'R1:' 开头）的 ignore_imports 条目。

    GDR-01 数量闭环：回指注释（方向 1）只校验「引用的 EX id 已登记」，新增 ignore 条目但忘写
    EX 注释时不产生 EX 文本引用、方向 1/2 均不报，本加载器配合方向 3 断言强制条目级同步。
    解析失败返回 None（调用方跳过断言）；返回 (ignore_entries, duplicate_errors)。
    """
    try:
        with open(PYPROJECT_PATH, "rb") as f:
            cfg = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None

    all_entries: list[str] = []
    duplicate_errors: list[str] = []
    seen: set[str] = set()

    for contract in cfg.get("tool", {}).get("importlinter", {}).get("contracts", []):
        name = str(contract.get("name", ""))
        if name.startswith("R1:"):
            for item in contract.get("ignore_imports", []):
                s = str(item)
                if s in seen:
                    duplicate_errors.append(f"治理 id 引用: pyproject.toml 契约中 ignore_imports 存在重复条目: '{s}'")
                else:
                    seen.add(s)
                all_entries.append(s)

    return all_entries, duplicate_errors


def _scan_exception_refs() -> set[str]:
    """扫描消费语料中出现的全部 EX-\\d{4} 引用 id（排除注册表自身与归档/记录/评测豁免目录）。

    consumer 为 CLAUDE.md + CONTRIBUTING.md + pyproject.toml + docs/**/*.md（活文档）；
    pyproject.toml 「R1: utils must not import business layers」契约注释回指 EX id（GDR-01），纳入消费语料后方向 2
    「登记必须被消费」对真实登记的例外不再误报孤儿；归档/记录/评测目录由
    _EX_REF_EXCLUDED_DIRS 豁免，其引用不构成治理消费。
    """
    refs: set[str] = set()
    consumers = [CLAUDE_PATH, CONTRIBUTING_PATH, PYPROJECT_PATH]
    for path in DOCS_README_PATH.parent.rglob("*.md"):
        if path == EXCEPTIONS_YAML_PATH:
            continue
        if any(path.is_relative_to(excluded) for excluded in _EX_REF_EXCLUDED_DIRS):
            continue
        consumers.append(path)
    for path in consumers:
        refs.update(_EX_ID_PATTERN.findall(path.read_text(encoding="utf-8")))
    return refs


def check_governance_id_references() -> list[str]:
    """检查项 16：治理 id 引用一致性（DOC-09，`EX-\\d{4}` 双向）。

    1. 方向 1（悬空）：消费文档引用的每个 EX-\\d{4} 必须已在 exceptions.yml 登记。
       宪法中的 EX 引用若指向未登记例外，会导致 AI 认为存在一条已批准例外——**直接误导**。
    2. 方向 2（孤儿）：exceptions.yml 登记的唯一 id（EX-\\d{4}）必须被至少一个消费文档引用。
       登记但从不被引用说明例外已过期，应按 append-only + 移除规则清理。

    仅校验 EX-\\d{4}（架构边界例外，rule_id=R1，见 exceptions.yml 字段说明）。
    """
    errors: list[str] = []
    loaded = _load_exception_ids()
    if loaded is None:
        errors.append("exceptions.yml 无法解析或无 exceptions 列表，跳过治理 id 引用校验")
        return errors
    registered = set(loaded)

    refs = _scan_exception_refs()

    # 方向 1: 悬空引用（被引用但未登记）
    for ref in sorted(refs - registered):
        errors.append(f"治理 id 引用: {ref} 被消费文档引用，但未在 exceptions.yml 登记")
    # 方向 2: 孤儿登记（已登记但从未被消费文档引用）
    for ex_id in sorted(registered - refs):
        errors.append(f"治理 id 引用: {ex_id} 已在 exceptions.yml 登记，但从未被任何消费文档引用")
    # 方向 3（GDR-01 数量闭环）: 全部以 R1: 开头的契约 ignore_imports 条目数必须等于 exceptions.yml 中
    # rule_id=R1 登记数。新增 ignore 条目忘写 EX 注释时不产生 EX 文本引用（方向 1/2 均不报），
    # 本断言强制条目级同步，堵住「例外游离于注册表之外」的复发路径。同时检查 ignore_imports 重复条目。
    r1_res = _load_r1_ignore_imports()
    if r1_res is not None:
        ignore_entries, duplicate_errors = r1_res
        errors.extend(duplicate_errors)
        r1_count = _load_exception_r1_count()
        if r1_count >= 0 and len(ignore_entries) != r1_count:
            errors.append(
                f"治理 id 引用: pyproject.toml R1 契约 ignore_imports 条目数 {len(ignore_entries)} "
                f"!= exceptions.yml 中 rule_id=R1 登记数 {r1_count}（新增/删除 ignore 条目必须同步登记或回指 EX id）"
            )

    return errors


def check_core_modules_completeness() -> list[str]:
    """断言 CLAUDE.md §4.2 声明的 core/ 模块列表与实际 core/*.py 文件一致 (GDR-11)."""
    errors: list[str] = []
    claude_path = ROOT / "CLAUDE.md"
    if not claude_path.exists():
        return ["CLAUDE.md 不存在"]
    claude_text = claude_path.read_text(encoding="utf-8")
    m = re.search(r"core/`\s*是架构核心层.*?目前含\s*(.*?)[)）]", claude_text, re.DOTALL)
    if not m:
        return ["CLAUDE.md §4.2 未找到 core/ 模块清单声明（如 '目前含 `...`'）"]
    declared_str = m.group(1)
    declared_modules = set(re.findall(r"`([a-zA-Z0-9_]+)`", declared_str))
    core_dir = ROOT / "core"
    if not core_dir.is_dir():
        return ["core/ 目录不存在"]
    actual_modules = {p.stem for p in core_dir.glob("*.py") if p.name != "__init__.py" and not p.name.startswith(".")}
    missing_in_doc = sorted(actual_modules - declared_modules)
    extra_in_doc = sorted(declared_modules - actual_modules)
    if missing_in_doc:
        errors.append(f"CLAUDE.md §4.2 漏声明 core/ 实际模块: {missing_in_doc}，实际模块包括: {sorted(actual_modules)}")
    if extra_in_doc:
        errors.append(
            f"CLAUDE.md §4.2 声明了不存在的 core/ 模块: {extra_in_doc}，实际模块包括: {sorted(actual_modules)}"
        )
    return errors


# --- GDR-09: 治理 ID 对照表一致性（自动加载文档中的治理 ID 必须已登记）---
# 自动加载文档（CLAUDE.md / AGENTS.md）中的治理 ID（P2-07 / DOC-04 / review01-A2 / GDR-06 等）
# 对每个新会话都是上下文噪声：指向的检视报告正文多为 gitignored 本地产物，读不到。GDR-09
# 建立 docs/governance/governance-ids.md 对照表，本检查守护「自动加载文档中出现的 ID 必须已
# 登记」，防止新增 ID 不登记（GDR-11 批评的「无门禁事实性漂移」）。
# H6-c（文档体系检视）：原前缀白名单枚举（P/DOC/GDR/GOV/UIX/UX/review）无法跟上新增的治理 ID
# 命名空间——业务域 ID（BT/AI/DAT/DATA/SYNC/CON 等）全部落在白名单外，等效于「出现即必须登记」
# 的门禁对它们不生效。改为通用形态（大写字母前缀 + 短横 + 数字）+ 显式豁免清单（外部编号体系
# 与检视协议内部规则 ID，均为封闭集合，不随项目治理命名空间增长）。
_GOVERNANCE_ID_PATTERN = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9]*-\d+)(?![A-Za-z0-9])")
# 豁免前缀（封闭集合，非本项目治理 ID 命名空间）：
#   - ADR / EX / CVE：架构决策记录编号（check_adr_index_completeness 守护）、架构例外 ID
#     （exceptions.yml + GDR-01 双向引用守护）、安全公告编号（SECURITY.md 引用）；
#   - UTF / SHA / AES / TLS / RFC / ISO / HTTP / HTTPS / SQL / FIPS / PCI / IEEE / ANSI / ASCII：
#     行业标准与规范代号；
#   - SAFE / INPUT / MODE / ROUND1 / ROUND2 / ROUND3 / STOP / FIND / EVID / SEV / OUT / CHECK：
#     AI 检视协议内部规则 ID（由 docs/reviews/ai-review.md 定义、CONTRIBUTING.md「docs/reviews/」节声明前缀）。
_GOVERNANCE_ID_EXEMPT_PREFIXES: frozenset[str] = frozenset(
    {
        "ADR",
        "EX",
        "CVE",
        "UTF",
        "SHA",
        "AES",
        "TLS",
        "RFC",
        "ISO",
        "HTTP",
        "HTTPS",
        "SQL",
        "FIPS",
        "PCI",
        "IEEE",
        "ANSI",
        "ASCII",
        "SAFE",
        "INPUT",
        "MODE",
        "ROUND1",
        "ROUND2",
        "ROUND3",
        "STOP",
        "FIND",
        "EVID",
        "SEV",
        "OUT",
        "CHECK",
    }
)
# ERROR 级扫描范围（H6-c 分级）：「入口级」文档——AI 每次会话自动加载（CLAUDE.md / AGENTS.md）
# 或进入项目的命令/流程入口（CONTRIBUTING.md）。这些文档中的未登记 ID 会让读者无从解析
# （GDR-09 原始诉求），故为硬性 error；其余受检文档与 .py 扫描同为 WARNING（渐进部署，存量
# 清零后翻转 ERROR，与 R20/R21 报告模式、DS-02 同范式）。按文件名判定，便于单测注入临时文档。
_GOVERNANCE_ERROR_DOC_NAMES: frozenset[str] = frozenset({"CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md"})
GOVERNANCE_IDS_PATH = ROOT / "docs" / "governance" / "governance-ids.md"


def _collect_governance_ids(text: str) -> set[str]:
    """从文本提取治理 ID 引用（通用形态），过滤豁免项（外部编号体系 / 检视协议 / 报告发现编号）。"""
    return {m.group(1) for m in _GOVERNANCE_ID_PATTERN.finditer(text) if not _is_exempt_governance_id(m.group(1))}


# 检视报告发现编号形态（F-09 / M9-010 / D6-1 / L111-134 / C5-5 等）：前缀为单字母或
# 「单字母 + 数字」（报告序号）。这些是各轮检视报告内部的发现编号（报告正文 gitignored），
# 不构成跨文档治理 ID 命名空间；跨文档溯源由报告级 ID（review01-A2 / BT-03 等）承载。
# 豁免该形态可避免「数百条永不登记的 WARNING」永久停留（GOV-10 反模式）。
# P 系列（P0-1 / P2-07 / P3-20）是治理 ID，显式排除在形态豁免之外。
_REPORT_FINDING_PREFIX_FORM = re.compile(r"[A-Z]\d*")


def _is_exempt_governance_id(gov_id: str) -> bool:
    """判定是否豁免：显式前缀清单（外部体系 / 检视协议）或检视报告发现编号形态。"""
    prefix = gov_id.split("-", 1)[0]
    if prefix in _GOVERNANCE_ID_EXEMPT_PREFIXES:
        return True
    return _REPORT_FINDING_PREFIX_FORM.fullmatch(prefix) is not None and re.fullmatch(r"P\d+", prefix) is None


def _load_glossary_entries() -> dict[str, list[tuple[str, int]]] | None:
    """加载 governance-ids.md 对照表已登记 ID → [(语义描述, 行号)] 列表；文件缺失/无法解析返回 None。

    保留同名同 ID 的多行语义（比对 registered set 更细，供 check_governance_id_dual_meaning 检测对义）。
    """
    path = GOVERNANCE_IDS_PATH
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    entries: dict[str, list[tuple[str, int]]] = {}
    for line_no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        m = _GOVERNANCE_ID_PATTERN.search(cells[0])
        if m:
            entries.setdefault(m.group(1), []).append((cells[1], line_no))
    return entries


def _load_glossary_ids() -> set[str] | None:
    """加载 governance-ids.md 对照表已登记 ID；文件缺失或无法解析返回 None。

    仅返回注册 ID 集合，供 check_governance_id_glossary 查询「是否登记」。
    """
    entries = _load_glossary_entries()
    return None if entries is None else set(entries)


def check_governance_id_dual_meaning() -> list[str]:
    """检查项 24：治理 ID 同名异义（对义）守卫（文档复检 H3 根因）。

    现有 check_governance_id_glossary 只查「是否登记」，查不出「同名异义」：同一 ID 在对照表
    登记为两条不同语义时，引用方无从确定所指，且新会话会误读。检测规则：
    - 同一 ID 在对照表出现 ≥2 行（语义不同）→ 视为潜在对义；
    - 行内已声明「双义登记」或「另义（同名异义」子串 → 视为已披露而豁免（如 P1-04 双义登记）；
    - 未披露的对义报 WARNING（渐进部署，与 .py 扫描 WARNING 分级一致，存量清零后翻转 ERROR）。
    """
    warnings: list[str] = []
    entries = _load_glossary_entries()
    if entries is None:
        return warnings
    for gov_id in sorted(entries):
        rows = entries[gov_id]
        if len(rows) < 2:
            continue
        # 任一行已披露双义则豁免：批次2 登记 P1-04 时在语义首列标注「另义（同名异义双义登记）」
        if any("双义登记" in sem or "另义（同名异义" in sem for sem, _ in rows):
            continue
        meanings = [sem for sem, _ in rows]
        warnings.append(
            f"治理 ID 对义(WARNING): {gov_id} 在 governance-ids.md 有多条不同语义未声明双义登记"
            f"（{'; '.join(meanings)}）；请在对应登记行标注「另义（同名异义双义登记）」或拆分 ID（渐进部署，存量清零后翻转为 ERROR）"
        )
    return warnings


_REGISTERED_SINGLETON_PROSE = re.compile(r"注册单例（`@register_singleton`，(\d+) 个）")


def check_singleton_count_prose() -> list[str]:
    """检查项 25：注册单例散文数量守卫（文档复检盲1）。

    现有测试只守护注册单例**类名集合**与代码白名单一致，不守护散文「注册单例（N 个）」的
    N 是否随表格增减同步。新增/移除注册单例后若漏同步 N，会宣称「19 个」却与实际表格行数不符。
    本守卫对齐 check_exception_count_prose 的散文数量守卫范式：解析 SINGLETON_LIFECYCLE_PATH
    中「注册单例（@register_singleton，N 个）」的散文 N，与「注册单例」章节实际表格数据行数比对。
    """
    errors: list[str] = []
    try:
        content = SINGLETON_LIFECYCLE_PATH.read_text(encoding="utf-8")
    except OSError:
        return [f"单例注册清单文档不存在: {SINGLETON_LIFECYCLE_PATH}"]

    m = _REGISTERED_SINGLETON_PROSE.search(content)
    if not m:
        return [f"未找到「注册单例（@register_singleton，N 个）」散文声明: {SINGLETON_LIFECYCLE_PATH.name}"]
    declared = int(m.group(1))

    # 统计「注册单例」章节到「非注册单例」章节之间的表格数据行
    # （首列被反引号包裹的类名行即数据行，表头「类名」与分隔行不匹配）
    section_start = m.end()
    next_section = content.find("**非注册单例", section_start)
    section_end = next_section if next_section != -1 else len(content)
    actual = 0
    for line in content[section_start:section_end].splitlines():
        if re.match(r"^\|\s*`([^`]+)`", line):
            actual += 1

    if declared != actual:
        prose_line = content[: m.start()].count("\n") + 1
        errors.append(
            f"{SINGLETON_LIFECYCLE_PATH.name}:{prose_line}: 散文声明注册单例 {declared} 个，"
            f"「注册单例」章节表格实计 {actual} 行"
        )
    return errors


# ADR supersede 双向链守卫（盲2）：ADR 头元数据中的 supersedes 与 superseded-by 声明须互相印证。
# 提取声明（收窄匹配避免解释性引用误报，如 ADR-0002 `Supersedes: CONTRIBUTING.md...（3b 由 ADR-0003 单独推翻）`）：
#   - sup 行：`> Partial Supersedes: ADR-0003 ...` / `> Supersedes: ADR-0002 ...`
#     目标须紧跟在「Supersedes:」关键字后的首 token 即为 ADR-NNNN，才作为真声明捕获；
#   - 被 sup 行（限 Status）：`> Status: Partial Superseded by ADR-0005 ... and ADR-0006 ...`
#     捕获 Status 行中「Superseded by」之后全部 ADR-NNNN。
_ADR_SUPERSEDES = re.compile(
    r"^>\s*(?:Partial\s+)?Supersedes:\s*"
    r"ADR-\d{4}(?:\s*[（(][^）)]*[）)])?"
    r"(?:\s*;\s*(?:and\s+)?ADR-\d{4}(?:\s*[（(][^）)]*[）)])?)*"
)
_ADR_SUPERSEDED_BY_STATUS = re.compile(r"^>\s*Status:.*?Superseded\s+by\s+(.+)$")
_ADR_ID_INLINE = re.compile(r"ADR-(\d{4})")


def check_adr_supersede_chain() -> list[str]:
    """检查项 26：ADR supersede 双向链守卫（文档复检盲2）。

    ADR X 声明「Supersedes ADR-Y」，则 ADR-Y 的 Status 应声明「Superseded by ADR-X」，反之亦然。
    检视发现 ADR-0002 声明「Partial Superseded by ADR-0005」，但 ADR-0005 仅回指 ADR-0003 未回指
    ADR-0002，构成单向断链。本守卫对 docs/adr/*.md 头元数据建立双向映射并校验对称性。
    """
    errors: list[str] = []
    if not ADR_DOCS_DIR.is_dir():
        return errors  # 目录缺失由 check_adr_index_completeness 报告

    supersedes: dict[str, set[str]] = {}
    superseded_by: dict[str, set[str]] = {}
    for path in ADR_DOCS_DIR.glob("*.md"):
        if path.name == "README.md":
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        adr_id = path.name.split("-", 1)[0]  # 0001
        supersedes.setdefault(adr_id, set())
        superseded_by.setdefault(adr_id, set())
        for line in content.splitlines():
            sm = _ADR_SUPERSEDES.match(line)
            if sm:
                # 正则已按「首 token + 圆括号注释 + 分号/and 并列目标」限界整个 ADR 目标段，
                # 只从该段提取 ADR 编号，避免把其后解释性文本中的 ADR 引用误当 supersede 目标。
                supersedes[adr_id].update(_ADR_ID_INLINE.findall(sm.group(0)))
                continue
            dm = _ADR_SUPERSEDED_BY_STATUS.match(line)
            if dm:
                superseded_by[adr_id].update(_ADR_ID_INLINE.findall(dm.group(1)))

    # 双向校验：X 声明 supersedes Y ⇒ Y 必须声明 superseded by X；反之亦然。
    for x, targets in supersedes.items():
        for y in targets:
            if y not in superseded_by:
                continue
            if x not in superseded_by[y]:
                errors.append(f"ADR-{x} 声明 Supersedes ADR-{y}，但 ADR-{y} 未声明 Superseded by ADR-{x}（单向断链）")
    for y, sources in superseded_by.items():
        for x in sources:
            if x not in supersedes:
                continue
            if y not in supersedes[x]:
                errors.append(f"ADR-{y} 声明被 ADR-{x} Supersede，但 ADR-{x} 未声明 Supersedes ADR-{y}（单向断链）")
    return errors


def check_governance_id_glossary() -> tuple[list[str], list[str]]:
    """检查项 19：治理 ID 对照表一致性（GDR-09 + H6-c 分级）。

    入口级文档（CLAUDE.md / AGENTS.md / CONTRIBUTING.md）中出现的治理 ID 必须全部已在
    docs/governance/governance-ids.md 登记——新会话读不到 gitignored 检视报告，
    未登记 ID 即纯上下文噪声且诱发臆测（违反 §1.10 反幻觉护栏精神）。

    返回 (errors, warnings)：
    - errors：入口级文档（_GOVERNANCE_ERROR_DOC_NAMES）中未登记的 ID（硬性，读者无从解析）；
    - warnings：其余受检文档（docs/**、SECURITY.md、man/**）、docs/governance/*.yml 与
      scripts/、tests/ 的 .py 中未登记的 ID（渐进部署，存量清零后翻转 ERROR，
      参照 check_redlines 的分级先例）。
    """
    errors: list[str] = []
    warnings: list[str] = []
    registered = _load_glossary_ids()
    if registered is None:
        errors.append("治理 ID 对照表: governance-ids.md 不存在或无法解析，跳过登记校验")
        return errors, warnings
    # 扩展扫描范围到受检治理文档：CHANGELOG.md（release-please 自动生成，含历史提交标题
    # 里的治理 ID 噪声）不属于治理溯源目标，显式排除；本地会话计划文件（Plans*.md）已由
    # 受检集构建（_build_doc_excludes）统一排除；
    # 登记正本 governance-ids.md 自身同样排除——其文本除登记行外还含说明文字（别名/夹具
    # 示例如 P1-4、DOC-99，以及嵌入式非治理编号如 Q-P2-7），这些不是「引用需登记」对象。
    # 其余 CHECKED_DOCS 全部纳入。另补扫 docs/governance/ 下的机器可读治理文件
    # （exceptions.yml / redlines.yml / canonical-topics.yml 等，非 markdown，不在 CHECKED_DOCS）。
    governance_yml = [
        p for p in (ROOT / "docs" / "governance").rglob("*") if p.is_file() and p.suffix in (".yml", ".yaml")
    ]
    # 需求正本（requirements/*.md）整体排除：其内容使用需求编号（FR-UX-xxx，通用形态会命中
    # 其 UX-xxx 片段）与阶段工作码（P3-7~P3-20），属非治理 ID 噪声，登记会污染治理对照表；
    # 与 CHANGELOG.md 的同类噪声排除同源（GDR-09 仅治理溯源目标）。
    scan_paths = [
        p
        for p in CHECKED_DOCS
        # 本地会话计划文件（_LOCAL_PLAN_FILE_RELS）已由受检集统一排除；此处按名排除 CHANGELOG.md
        # （release-please 自动生成，含历史提交标题里的治理 ID 噪声）与登记正本 governance-ids.md
        # 自身（其文本含别名/夹具示例等非「引用需登记」对象）。requirements/*.md 整体排除同下。
        if p.name not in ("CHANGELOG.md", "governance-ids.md") and ROOT / "requirements" not in p.parents
    ] + governance_yml
    warn_refs: set[str] = set()
    for path in scan_paths:
        if not path.exists():
            continue
        refs = _collect_governance_ids(path.read_text(encoding="utf-8"))
        if path.name in _GOVERNANCE_ERROR_DOC_NAMES:
            for gov_id in sorted(refs - registered):
                errors.append(f"治理 ID 对照表: {gov_id} 出现在入口文档 {path.name} 中，但未在 governance-ids.md 登记")
        else:
            warn_refs.update(refs - registered)

    # DS-02 + H6-c：scripts/ 与 tests/ 的 .py 亦为 WARNING 面。测试注释中的治理 ID 是「这个断言
    # 为什么存在」的高价值线索（如 review03-C1 标注守护性断言的来源检视发现）；存量未登记 ID
    # 先以 WARNING 落地（渐进部署，不阻断），存量清零后再翻转 ERROR。
    for py_dir in ("scripts", "tests"):
        for path in (ROOT / py_dir).rglob("*.py"):
            if path.is_file():
                collected = _collect_governance_ids(path.read_text(encoding="utf-8", errors="replace"))
                warn_refs.update(collected - registered)
    for gov_id in sorted(warn_refs):
        warnings.append(
            f"治理 ID 对照表(WARNING): {gov_id} 未在 governance-ids.md 登记（渐进部署，存量清零后翻转为 ERROR）"
        )
    return errors, warnings


# GDR-13: 书名号式章节引用——形如 `<文档路径>「<章节名>」`（如 `CONTRIBUTING.md「错误处理标准模式」`）。
# 这类引用不是 markdown 链接，check_anchor_dead_links / check_relative_dead_links 均扫不到；
# 章节改名或删除时若无本检查，将无任何报警。引用路径以仓库根相对形式书写（不含 `./` 前缀），
# 与 CLAUDE.md §1.8 决策树「必读入口」列的裸路径文本形态一致（见 _extract_decision_tree_targets）。
_GUILLEMET_REF_PATTERN = re.compile(r"(?<![\w./-])([\w./-]+\.md)\s*「([^」]+)」")


def _extract_heading_texts(content: str) -> set[str]:
    """提取 markdown 全部标题的原文（保留序号/前缀，如「### 7. 新增回测配置」）。"""
    headings: set[str] = set()
    for line in content.splitlines():
        m = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if m:
            headings.add(m.group(2))
    return headings


def _strip_english_suffix(heading: str) -> str:
    """剥离标题末尾的英文括注（如「已知架构技术债 (Known Technical Debt)」→「已知架构技术债」）。

    仅剥离含 ASCII 字母的圆括号后缀，避免误删中文括注或关键语义；不匹配则原样返回。
    """
    return re.sub(r"\s*\([^)]*[A-Za-z][^)]*\)$", "", heading)


# 书名号引用目标为裸文件名（无目录前缀）时的回落搜索范围（H1）：引用者常省略目录前缀
# （如 `testing.md「测试资产地图」`），仅按仓库根相对解析会把正确引用误报为
# 「目标文档不存在」。搜索根按当前 ROOT 动态派生（与 CHECKED_DOCS 递归发现范围一致；
# 根目录文件本身由 `ROOT / raw_path` 首查覆盖，无需重复列出）。


def _has_guillemet_heading(target: Path, section: str) -> bool:
    """目标文档是否存在与章节引用同名的标题（含「：」前缀修饰与英文括注归一化）。"""
    headings = _extract_heading_texts(target.read_text(encoding="utf-8"))
    if any(h == section or h.endswith(f"：{section}") for h in headings):
        return True
    normalized = {_strip_english_suffix(h) for h in headings}
    return any(h == section or h.endswith(f"：{section}") for h in normalized)


def _resolve_bare_doc_name(raw_path: str) -> list[Path]:
    """把无目录前缀的裸文件名解析为 docs/、man/、requirements/ 下的同名文档候选。

    仅处理不含目录分隔符的路径；返回全部同名候选（可能为空，或多个——如同名 README.md）。
    """
    if "/" in raw_path or "\\" in raw_path:
        return []
    roots = (ROOT / "docs", ROOT / "man", ROOT / "requirements")
    return sorted(p for base in roots for p in base.rglob(raw_path) if p.is_file())


def check_guillemet_references() -> list[str]:
    """检查项 20：书名号式章节引用一致性（GDR-13）。

    扫描 CHECKED_DOCS 中形如 `<文档路径>「<章节名>」` 的引用，断言目标文档存在同名标题。
    匹配规则：目标标题等于章节名，或目标标题以「：」+ 章节名 结尾（容忍「第三部分：实现规范手册」
    这类「第 N 部分」前缀修饰）。markdown 链接 `[text「章节」](./path#anchor)` 内的书名号
    由锚点门禁 check_anchor_dead_links 覆盖，本检查跳过（避免重复报警与「链接文本 ≠ 标题」误报）。
    目标解析：先按仓库根相对解析；裸文件名（无目录前缀）解析不到时回落到 docs/、man/、
    requirements/ 内同名候选，多候选时任一候选含同名标题即视为有效引用（H1）。
    """
    errors: list[str] = []
    for doc in CHECKED_DOCS:
        if not doc.exists():
            continue
        content = doc.read_text(encoding="utf-8")
        # 剔除 markdown 链接 `[text](url)`，链接内书名号由锚点门禁覆盖
        text = re.sub(r"\[[^\]]*\]\([^)]*\)", "", content)
        for m in _GUILLEMET_REF_PATTERN.finditer(text):
            raw_path, section = m.group(1), m.group(2).strip()
            target = ROOT / raw_path
            candidates = [target] if target.exists() else _resolve_bare_doc_name(raw_path)
            if not candidates:
                errors.append(f"书名号引用: {doc.name} 引用 {raw_path}「{section}」，目标文档 {raw_path} 不存在")
                continue
            if any(_has_guillemet_heading(candidate, section) for candidate in candidates):
                continue
            if len(candidates) == 1:
                headings = _extract_heading_texts(candidates[0].read_text(encoding="utf-8"))
                errors.append(
                    f"书名号引用: {doc.name} 引用 {raw_path}「{section}」，目标文档无同名标题"
                    f"（现有标题示例: {sorted(headings)[:6]}）"
                )
            else:
                errors.append(
                    f"书名号引用: {doc.name} 引用 {raw_path}「{section}」，"
                    f"docs/、man/、requirements/ 下存在 {len(candidates)} 个同名文档但均无该标题"
                )
    return errors


def check_strategy_desc_dynamic_consistency() -> list[str]:
    """检查项 21：策略静态描述与可调参数的一致性（D2-M4）。

    扫描 ``locales/*/strings.json`` 中形如 ``strategy_*_desc`` 的静态描述 key，
    若其值含数字字面量（硬编码阈值，如 ``> 3000万``），则该策略存在可调参数，
    必须同时提供动态描述 ``strategy_*_desc_dynamic``，避免 UI 展示的阈值与
    用户拖动的滑块参数脱钩而漂移。
    """
    errors: list[str] = []
    number_re = re.compile(r"\d")
    for locale_dir in ("zh_CN", "en_US"):
        path = ROOT / "locales" / locale_dir / "strings.json"
        if not path.exists():
            continue
        try:
            strings = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            errors.append(f"策略描述动态一致性: {locale_dir}/strings.json JSON 解析失败: {e}")
            continue
        for key, value in strings.items():
            if not key.startswith("strategy_") or not key.endswith("_desc"):
                continue
            # 排除已带 _dynamic 后缀的动态 key（其为最终消费形态，不要求再嵌套）
            if key.endswith("_desc_dynamic") or key.endswith("_dynamic_desc"):
                continue
            # 静态 desc 仅当含硬编码数字字面量时才触发门禁
            if not isinstance(value, str) or not number_re.search(value):
                continue
            # 兼容两种动态模板命名：基类派生 `strategy_*_desc_dynamic` 与手工覆写 `strategy_*_dynamic_desc`
            dynamic_key = f"{key}_dynamic"
            alt_dynamic_key = key.replace("_desc", "_dynamic_desc")
            if dynamic_key not in strings and alt_dynamic_key not in strings:
                errors.append(
                    f"策略描述动态一致性: {locale_dir}「{key}」含数字字面量 '{value}'，"
                    f"但缺少动态模板 '{dynamic_key}' / '{alt_dynamic_key}'"
                    f"（硬编码阈值与可调参数脱钩，见 D2-M4）"
                )
    return errors


def main() -> int:
    """运行全部检查，返回退出码。"""
    all_errors: list[str] = []
    all_errors.extend(check_anchor_dead_links())
    all_errors.extend(check_relative_dead_links())
    all_errors.extend(check_version_consistency())
    all_errors.extend(check_precommit_hook_count())
    all_errors.extend(check_precommit_hook_names())
    all_errors.extend(check_workflow_enum())
    all_errors.extend(check_flet_version_drift())
    all_errors.extend(check_note_lazy_format())
    all_errors.extend(check_redlines_yaml_consistency())
    # 7b：红线总数散文（R1~Rxx）守卫，紧随表格式一致性之后，守护散文式自述漏同步（DS 根因）
    all_errors.extend(check_redline_range_consistency())
    # 7c：import-linter 契约数量散文（「N 条契约」）守卫，与 7b 同族，守护 E4 重构后的数量漂移（H6-b）
    all_errors.extend(check_contract_count_prose())
    # 3c 紧随 3b 之后：3b 守护 yml schema 完整性，3c 守护 enforcement 与实际配置一致
    # 3c 独立解析 yml，不依赖 3b 执行结果，顺序仅为可读性
    all_errors.extend(check_enforcement_mapping())
    # enforcement 反向覆盖（DS-11）：check_redlines.py 实际执行的 check_* 必须全部在 redlines.yml 登记
    all_errors.extend(check_enforcement_reverse_coverage())
    # 例外注册表一致性：紧随红线一致性之后，守护集中例外治理 (P1-01)
    all_errors.extend(check_exceptions_yaml_consistency())
    # 例外反向覆盖：技术债表中豁免 EXCEPTIONABLE 红线的条目必须已登记例外 (P1-04)
    all_errors.extend(check_exceptions_reverse_coverage())
    # 主题 → canonical 正本映射一致性：守护决策树机器可读镜像的路径有效性 (P2-12)
    all_errors.extend(check_canonical_topics_consistency())
    # Flet 入口完整性：紧随 Flet 版本漂移检查之后，守护 docs/flet/README.md 覆盖全部专题
    all_errors.extend(check_flet_hub_completeness())
    # AGENTS.md 生成区块与 redlines.yml 一致性：守护跨工具入口的最小安全集导出镜像 (DOC-08/DOC-13)
    all_errors.extend(check_agents_md_sync())
    # AGENTS.md 最小验证命令生成区块（M2）：跨工具入口补齐「改完代码跑什么验证」
    all_errors.extend(check_agents_md_min_verify_commands())
    # CLAUDE.md 顶部摘要生成区块与 redlines.yml 一致性：守护自动加载文档第一屏 (F-04)
    all_errors.extend(check_claude_executive_sync())

    # 分支E 机制补全（DOC-01/04/05/07/09/11）：规则集元数据、决策树镜像、canonical 路由、
    # docs 索引全覆盖、治理 id（EX-\d{4}）双向引用。补齐「字段存在」之外的「语义正确」守卫。
    all_errors.extend(check_ruleset_metadata_consistency())
    all_errors.extend(check_ruleset_changelog_version())
    all_errors.extend(check_decision_tree_mapping())
    all_errors.extend(check_canonical_routing())
    all_errors.extend(check_canonical_completion_criteria())
    all_errors.extend(check_canonical_docs_are_gated())
    all_errors.extend(check_docs_index_completeness())
    all_errors.extend(check_reviews_index_completeness())
    all_errors.extend(check_reviews_findings_index())
    all_errors.extend(check_adr_index_completeness())
    all_errors.extend(check_scripts_index_completeness())
    all_errors.extend(check_governance_id_references())
    all_errors.extend(check_core_modules_completeness())
    # 治理 ID 对照表一致性：守护自动加载文档中的 ID 全部登记（GDR-09），紧随 EX 引用一致性之后。
    # .py 扫描（scripts/ + tests/）的未登记 ID 暂为 WARNING，不阻断（DS-02 渐进部署）。
    glossary_errors, glossary_warnings = check_governance_id_glossary()
    all_errors.extend(glossary_errors)
    if glossary_warnings:
        print("::warning::治理 ID 对照表（渐进部署，不阻断）存在未在 governance-ids.md 登记的治理 ID：")
        for w in glossary_warnings[:40]:
            print(f"  - {w}")
        if len(glossary_warnings) > 40:
            print(f"  - ...另有 {len(glossary_warnings) - 40} 条未列出（存量清零后翻转为 ERROR）")
    # 书名号式章节引用：补上锚点/相对链接门禁之外的最后一类跨文档引用（GDR-13）
    all_errors.extend(check_guillemet_references())
    # 策略静态描述与可调参数一致性（D2-M4）：硬编码数字阈值必须配套 _desc_dynamic 动态模板
    all_errors.extend(check_strategy_desc_dynamic_consistency())

    # 散文式自述元信息守卫（文档复检 H1/L1/H3 根因治理，补「对比型/锚点型」之外的漏检单点）
    all_errors.extend(check_flet_badge_version())  # H1：README UI 徽章 Flet 版本对齐
    all_errors.extend(check_exception_count_prose())  # L1：「现存 N 条 R1 例外」数量守卫
    # 盲1：注册单例散文数量守卫 + 盲2：ADR supersede 双向链守卫（文档复检指标盲区治理）
    all_errors.extend(check_singleton_count_prose())  # 盲1：散文 N vs 表格行数
    all_errors.extend(check_adr_supersede_chain())  # 盲2：ADR supersede 双向链对称性
    all_errors.extend(check_governance_id_dual_meaning())  # GOV-06：治理 ID 同名异义（双义已清零，翻转 ERROR）

    if all_errors:
        print("[FAIL] 文档一致性检查失败：", file=sys.stderr)
        for err in all_errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(
        "[PASS] 文档一致性检查通过（锚点死链 / 相对链接死链 / 版本一致 / "
        "pre-commit hook 数量 / hook 名称一致性 / workflow 枚举 / Flet 版本漂移 / NOTE(lazy) 三要素 / redlines.yml 一致性 / "
        "红线总数散文一致性 / enforcement 字段映射一致性 / exceptions.yml 一致性 / 例外反向覆盖一致性 / canonical-topics.yml 一致性 / "
        "Flet 入口完整性 / AGENTS/CLAUDE 顶部生成区块一致性 / 规则集元数据一致性 / "
        "决策树映射一致性 / canonical 路由一致性 / canonical 完成判定覆盖 / 文档索引全覆盖 / canonical 受检范围完整性 / 检视方法论文档登记 / 检视结论登记索引（GOV-04） / "
        "治理 id 引用一致性 / core 模块清单完整性 / 治理 ID 对照表一致性 / 书名号章节引用一致性 / "
        "规则集变更日志版本一致 / ADR 索引完整性 / 脚本索引完整性 / 策略描述动态一致性 / "
        "Flet 徽章版本一致性 / 例外清单数量守卫 / 治理 ID 对义守卫 / "
        "注册单例散文数量守卫 / ADR supersede 双向链守卫）"
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
