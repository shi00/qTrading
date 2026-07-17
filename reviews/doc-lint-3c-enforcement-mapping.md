# doc-lint 自动化第二阶段 3c 映射检查修复方案

> 主题：落地 redlines.yml `enforcement` 字段与实际 hook/CI job 映射一致性检查
> 状态：设计完成，待实施
> 日期：2026-07-17
> 关联：[ADR-0003](../docs/adr/0003-overturn-3b-3c-deferral.md)（3b 落地 + 3c 不做）→ 本方案部分推翻 3c 决策
> 落地分支：`fix/doc-lint-3c-enforcement-mapping`

---

## 一、方案总览

### 1.1 决策摘要

| 决策点 | 选型 | 理由 |
|---|---|---|
| 检查粒度 | **关键不变量检查** | 提取 9 个高价值不变量覆盖漂移高发场景，符合 YAGNI；R16 单点特例集中管理 |
| yml schema 变更 | **不改 schema，文本解析** | 复用现有 `enforcement` 字段，正则匹配关键词；符合 ADR-0004「5 字段精简」原则 |
| R3 不精确处理 | **不处理，登记漏检** | 3c 只负责「enforcement 声明的守护机制配置存在且粗粒度可达」，不负责「enforcement 字段是否足够精确」 |
| 实现位置 | 扩展 `check_docs_consistency.py` | 与 3b 的 `check_redlines_yaml_consistency()` 同位置，复用 `docs-consistency` hook |
| ADR 关系 | 新增 ADR-0005 **部分推翻** ADR-0003 | 仅推翻 3c 部分，3b 决策不变 |

### 1.2 核心思路

3c 只负责「enforcement 字段中声称的守护机制实际配置存在且粗粒度可达」，不负责「enforcement 字段是否足够精确」。提取 9 个高价值不变量，覆盖漂移高发场景，R16 单点特例集中管理。

### 1.3 触发条件与决策口径

ADR-0003 的原始升级条件包含「CI 自动化专项迭代」与红线治理复核；`docs/debt/known-technical-debt.md` 后续将 3c 的复核条件收窄为「强制状态字段发生漂移事故或红线违规频发」。本次由架构维护者主动要求重新评估 3c，视为一次人工触发的治理决策；ADR-0005 需明确记录该触发来源，避免误写成已发生漂移事故。

---

## 二、不变量清单（v2，9 项）

### 2.1 正向不变量（enforcement 关键词 → 实际配置存在性）

| # | 不变量 | 触发红线 | 校验目标 | 修订说明 |
|---|---|---|---|---|
| N1 | enforcement 含「check_redlines.py」⇒ `redline-check` hook 存在 **且 `entry` 含 `check_redlines.py`** 且脚本文件存在 | R4/R12/R13/R14/R15 | `.pre-commit-config.yaml` hook id + entry + 脚本文件 | 新增 entry 校验 |
| N2 | enforcement 含「import-linter」⇒ `lint-imports` hook 存在 **且 `entry` 含 `lint-imports`** 且 `pyproject.toml` 含 `[[tool.importlinter.contracts]]` **且契约数量与 enforcement 描述一致** | R1 | hook id + entry + pyproject 契约数 | 新增 entry + 契约数量校验 |
| N3 | enforcement 含 `\bruff\b` ⇒ `ruff-check` hook 存在 **且 `entry` 含 `ruff`** | R6 | hook id + entry | 新增 entry 校验 + word boundary |
| N4 | enforcement 含「安全扫描」⇒ **`.github/workflows/*.yml` / `*.yaml` 任一**配置 Gitleaks secret scan，且 `.gitleaks.toml` 存在 | R9/R10 | 全部 workflow 文件 + Gitleaks 配置文件 | 从 `pip-audit` 改为 Gitleaks，贴合密钥/敏感信息红线 |
| N5 | enforcement 含「CI-test」⇒ **`.github/workflows/*.yml` / `*.yaml` 任一 `run:` 命令块**含 `python -m pytest` 或 `pytest` 命令 | R2/R7/R8 | 全部 workflow 文件的 `run:` 命令块 | 避免 `Cache pytest` 等非测试步骤误命中 |
| N6 | enforcement 含「仅人工评审」⇒ `human_review_required == true` | R5/R9/R10/R11/R17/R18 | yml 字段 | `.get()` 防御性访问 |
| N7 | enforcement 含「待实现」/「暂缓」⇒ `human_review_required == false` | R16 | yml 字段 | `.get()` 防御性访问 |

### 2.2 反向不变量（human_review_required 一致性）

| # | 不变量 | 校验目标 | 修订说明 |
|---|---|---|---|
| N8 | `human_review_required == true` ⇒ enforcement 含「仅人工评审」 | yml 字段 | `.get()` 防御性访问 |
| N9 | `human_review_required == false` ⇒ enforcement 不含「仅人工评审」 | yml 字段 | `.get()` 防御性访问 |

### 2.3 关键词匹配规则

- **中文关键词**（如「安全扫描」「仅人工评审」「待实现」「暂缓」）：使用 `in` 子串匹配
- **英文关键词**（如 `ruff`）：使用 `re.search(r"\bruff\b", enforcement)` word boundary 匹配，避免误匹配 `scruffian` 等
- **含特殊字符关键词**（如 `check_redlines.py` / `import-linter` / `CI-test`）：使用 `in` 子串匹配（特殊字符本身已起到 boundary 作用）

---

## 三、漏检场景登记（v2）

### 3.1 已知漏检（3c 范围外）

| 漏检场景 | 性质 | 处理 |
|---|---|---|
| R3 enforcement = "pre-commit"（无具体 hook 名） | 已知技术债 | 3c 范围外；R3 精确化后**需新增对应不变量**（非 N1 自然覆盖） |
| 删除 `docs-consistency` hook 本身 | meta 悖论 | 人工评审兜底（守护者无法守护自己） |
| R2/R7/R8 的特定守护测试用例被删除 | 3c 根本限制 | 机器无法校验特定测试存在而不引入巨大维护成本；由测试覆盖率门控 + 人工评审兜底 |
| Hook `files` 过滤器收窄导致 hook 不触发 | 细粒度漂移 | 3c 范围外；属 hook 配置审查范畴 |
| CI job `if:` 条件禁用 | 细粒度漂移 | 3c 范围外；属 CI 配置审查范畴 |

### 3.2 R3 「自然覆盖」承诺的更正

原方案曾声称「若未来 R3 enforcement 改为『pre-commit（type-ignore-reason）』，则 N1 模式可自然覆盖」。

**事实核查**：N1 只校验 `check_redlines.py` 关键词，**不会**覆盖 `type-ignore-reason`。原方案此声明是事实错误，已更正。

---

## 四、实现方案

### 4.1 文件变更清单

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `scripts/check_docs_consistency.py` | 扩展 | 新增 `check_enforcement_mapping()` + 辅助函数；更新 docstring 与 `main()` 调用 |
| `tests/unit/test_docs_consistency.py` | 扩展 | 新增 `TestEnforcementMapping` 测试类（TDD） |
| `docs/debt/known-technical-debt.md` | 更新 | 第一条 P3 条目更新为「3a/3b/3c 已落地」 |
| `docs/adr/0005-enforcement-mapping-check.md` | 新增 | 推翻 ADR-0003 中 3c 仍不做的决定 |

### 4.2 核心函数设计

**位置**：`scripts/check_docs_consistency.py`（与 3b 的 `check_redlines_yaml_consistency()` 同位置）

**新增常量**：

```python
from dataclasses import dataclass

# 3c: enforcement 字段关键词 → 实际守护配置校验
ENFORCEMENT_KEYWORD_CHECK_REDLINES = "check_redlines.py"
ENFORCEMENT_KEYWORD_IMPORT_LINTER = "import-linter"
ENFORCEMENT_KEYWORD_SECURITY_SCAN = "安全扫描"
ENFORCEMENT_KEYWORD_CI_TEST = "CI-test"
ENFORCEMENT_KEYWORD_HUMAN_REVIEW = "仅人工评审"
ENFORCEMENT_KEYWORD_PENDING = ("待实现", "暂缓")  # R16 特例

# import-linter 契约数量正则（enforcement 文本解析期望数量）
IMPORT_LINTER_CONTRACT_COUNT_PATTERN = re.compile(r"(\d+)\s*条契约")
# CI-test 命令正则：仅匹配 workflow run: 命令块，不匹配 step 名称 / cache key
PYTEST_COMMAND_PATTERN = re.compile(r"(^|\s)(python\s+-m\s+pytest|pytest)(\s|$)")

# CI workflow glob 模式（扫描全部 workflow 文件，GitHub Actions 同时支持 .yml / .yaml）
CI_WORKFLOW_GLOBS = (".github/workflows/*.yml", ".github/workflows/*.yaml")


@dataclass(frozen=True)
class EnforcementEnvironment:
    """3c 不变量校验所需的项目配置快照。"""

    precommit_content: str
    workflow_contents: tuple[str, ...]
    pyproject_content: str
    check_redlines_script_exists: bool
    gitleaks_config_exists: bool
```

**新增函数签名**：

```python
def _extract_enforcement_keywords(enforcement: str) -> set[str]:
    """从 enforcement 文本中提取守护机制关键词集合。

    纯函数，便于单元测试。
    """

def _collect_enforcement_environment() -> EnforcementEnvironment:
    """读取 .pre-commit-config.yaml、workflow、pyproject.toml 与脚本存在性，生成配置快照。"""

def _check_precommit_hook(
    precommit_content: str,
    hook_id: str,
    entry_keyword: str | None = None,
) -> bool:
    """检查 pre-commit 内容是否含指定 id 的 local hook。

    若 entry_keyword 非空，同时校验 hook 的 entry 字段含该关键词。
    """

def _extract_workflow_run_blocks(workflow_content: str) -> list[str]:
    """提取 GitHub Actions workflow 中的 run: 命令块。

    用轻量缩进扫描而非完整 YAML 解析，避免 GitHub Actions 表达式带来的解析兼容成本。
    """

def _check_workflow_command_exists_any_workflow(
    command_pattern: re.Pattern[str],
    workflow_contents: tuple[str, ...],
) -> bool:
    """检查任一 workflow 的 run: 命令块匹配指定命令正则。"""

def _check_gitleaks_scan_exists(workflow_contents: tuple[str, ...], gitleaks_config_exists: bool) -> bool:
    """检查 Gitleaks secret scan workflow 与 .gitleaks.toml 配置同时存在。"""

def _count_importlinter_contracts(pyproject_content: str) -> int:
    """统计 pyproject.toml 内容中 [[tool.importlinter.contracts]] 数量。"""

def _check_enforcement_invariants(redlines: list[dict], env: EnforcementEnvironment) -> list[str]:
    """纯函数：对已解析的 redlines 列表与配置快照校验 9 个不变量，返回错误列表。

    纯函数设计便于单元测试（传入构造的 redlines 数据与 env 数据）。
    使用 .get() 防御性访问 human_review_required 字段。
    """

def check_enforcement_mapping() -> list[str]:
    """检查项 8: enforcement 字段与实际 hook/CI job 映射一致性（3c 落地）。

    读取 redlines.yml + .pre-commit-config.yaml + .github/workflows/*.yml/*.yaml + pyproject.toml,
    校验 enforcement 字段中声称的守护机制配置存在且粗粒度可达。

    独立解析 yml，不依赖 check_redlines_yaml_consistency() 的执行顺序。
    yml 解析失败时返回精确错误（允许与 3b 重复报错）。
    """
```

**`main()` 集成**：

```python
def main() -> int:
    all_errors: list[str] = []
    # ... 现有检查 ...
    all_errors.extend(check_redlines_yaml_consistency())
    # 3c 紧随 3b 之后：3b 守护 yml schema 完整性，3c 守护 enforcement 与实际配置一致
    # 3c 独立解析 yml，不依赖 3b 执行结果，顺序仅为可读性
    all_errors.extend(check_enforcement_mapping())
    # ...
```

### 4.3 关键逻辑要点

1. **独立 yml 解析**：`check_enforcement_mapping()` 内部独立调用 `yaml.safe_load(REDLINES_YAML_PATH.read_text(encoding="utf-8"))`，与 `check_redlines_yaml_consistency()` 共享解析规则但**不共享执行结果**。解析失败时返回精确错误，允许与 3b 重复报错，消除顺序依赖。

2. **pre-commit hook 检测**：复用现有 `_count_local_hooks()` 的正则风格，匹配 `^ {6}- id: <hook_id>` 行；同时提取 hook 块内的 `entry:` 行校验关键词。

3. **CI / 安全扫描检测**：扫描 `.github/workflows/*.yml` 与 `.github/workflows/*.yaml` **全部**文件（glob）。安全扫描只把 Gitleaks secret scan 作为 R9/R10 的机器映射，并要求 `.gitleaks.toml` 存在；`pip-audit` 仍属于依赖安全审计，但不再作为 R9/R10 的证据。CI-test 只扫描 workflow 的 `run:` 命令块，匹配 `python -m pytest` / `pytest` 命令，避免 `Cache pytest`、artifact 名称、cache key 等非执行文本导致假阳性。不做完整 YAML 解析（GitHub Actions YAML 含表达式与多种简写，文本扫描更贴合现有脚本风格）。

4. **import-linter 契约数量校验**：
   - 从 enforcement 文本用正则 `(\d+)\s*条契约` 解析期望数量（R1 enforcement = "pre-commit（import-linter 4 条契约）" → 期望 4）
   - 统计 `pyproject.toml` 中 `[[tool.importlinter.contracts]]` 出现次数
   - 若 enforcement 未含数量描述则跳过数量校验（避免过度约束）

5. **纯函数设计**：`_check_enforcement_invariants(redlines, env)` 接受已解析的 redlines 列表与 `EnforcementEnvironment` 配置快照，不读文件，便于单元测试构造正例/反例。实际文件读取集中在 `_collect_enforcement_environment()`，避免“纯函数”与文件 IO 混杂。

6. **R16 特例处理**：N7 不变量单独处理「待实现」/「暂缓」关键词，与 N6 的「仅人工评审」互斥。R16 的 enforcement = "可自动化待实现（AST 检查，暂缓：误报风险高）" 同时含「待实现」和「暂缓」，但不含「仅人工评审」，`human_review_required: false`，符合 N7。

7. **防御性编程**：N6~N9 使用 `entry.get("human_review_required")` 访问字段。若字段缺失，由 `check_redlines_yaml_consistency()` 的字段完整性校验报错（已存在），`check_enforcement_mapping()` 跳过该条目的 N6~N9 校验。

8. **关键词匹配规则**：
   - 中文关键词（「安全扫描」「仅人工评审」「待实现」「暂缓」）：`in` 子串匹配
   - 英文关键词（`ruff`）：`re.search(r"\bruff\b", enforcement)` word boundary 匹配
   - 含特殊字符关键词（`check_redlines.py` / `import-linter` / `CI-test`）：`in` 子串匹配

9. **脚本存在性校验**：N1 除 hook id + entry 外，还必须校验 `scripts/check_redlines.py` 存在。该布尔值由 `EnforcementEnvironment.check_redlines_script_exists` 提供，避免在不变量函数中直接读文件系统。

10. **安全扫描语义边界**：N4 只证明 secret scanning 配置存在，不能证明所有 R9/R10 场景都被自动发现；R9/R10 仍保留 `human_review_required: true`，人工评审继续覆盖日志脱敏、异常消息与运行时泄露路径。

---

## 五、测试方案（TDD）

### 5.1 测试类：`TestEnforcementMapping`

**位置**：`tests/unit/test_docs_consistency.py`

**测试用例清单**（先写测试，后实现，无 xFail）：

```python
class TestEnforcementMapping:
    """C5 第二阶段 3c: enforcement 字段与实际 hook/CI job 映射一致性校验。"""

    # --- 纯函数测试（_extract_enforcement_keywords）---
    def test_extract_keywords_check_redlines(self):
        """enforcement 含 'check_redlines.py' 关键词被正确提取。"""

    def test_extract_keywords_multiple(self):
        """enforcement 含多个关键词（如 '安全扫描 + 仅人工评审'）被全部提取。"""

    def test_extract_keywords_pending(self):
        """enforcement 含 '待实现' 和 '暂缓' 被识别为 pending 关键词。"""

    def test_extract_keywords_ruff_word_boundary(self):
        """'ruff' 关键词使用 word boundary 匹配，不误匹配 'scruffian'。"""

    # --- 不变量 N1~N9 正向校验（_check_enforcement_invariants 纯函数）---
    def test_n1_check_redlines_keyword_without_hook(self):
        """N1: enforcement 含 'check_redlines.py' 但 redline-check hook 不存在 → 报错。"""

    def test_n1_check_redlines_keyword_with_hook_but_wrong_entry(self):
        """N1: enforcement 含 'check_redlines.py' 且 hook 存在但 entry 指向其他脚本 → 报错。"""

    def test_n1_check_redlines_keyword_with_hook_and_correct_entry(self):
        """N1: enforcement 含 'check_redlines.py' 且 hook + entry + 脚本文件均正确 → 通过。"""

    def test_n2_import_linter_contract_count_mismatch(self):
        """N2: enforcement 声明 '4 条契约' 但 pyproject.toml 实际 3 条 → 报错。"""

    def test_n4_security_scan_requires_gitleaks_and_config(self):
        """N4: 安全扫描需同时存在 Gitleaks workflow 与 .gitleaks.toml。"""

    def test_n4_pip_audit_alone_not_security_scan_evidence(self):
        """N4: 仅存在 pip-audit 不应被当作 R9/R10 安全扫描证据。"""

    def test_n5_pytest_only_matches_run_command_block(self):
        """N5: pytest 只在 run: 命令块中出现时才算 CI-test 证据。"""

    def test_n6_human_review_keyword_mismatch(self):
        """N6: enforcement 含 '仅人工评审' 但 human_review_required=false → 报错。"""

    def test_n7_pending_keyword_mismatch(self):
        """N7: enforcement 含 '待实现' 但 human_review_required=true → 报错。"""

    def test_n8_reverse_invariant_violation(self):
        """N8: human_review_required=true 但 enforcement 不含 '仅人工评审' → 报错。"""

    def test_n9_reverse_invariant_violation(self):
        """N9: human_review_required=false 但 enforcement 含 '仅人工评审' → 报错。"""

    def test_missing_human_review_required_field_skipped(self):
        """yml 条目缺 human_review_required 字段时 N6~N9 跳过（由 3b 守护字段完整性）。"""

    # --- 真实文件集成测试 ---
    def test_check_enforcement_mapping_passes(self):
        """当前项目配置下 check_enforcement_mapping() 应返回空错误列表。"""

    def test_detects_deleted_redline_check_hook(self, monkeypatch, tmp_path):
        """构造缺失 redline-check hook 的 .pre-commit-config.yaml → 应报错。"""

    def test_detects_wrong_hook_entry(self, monkeypatch, tmp_path):
        """构造 redline-check hook 但 entry 指向其他脚本 → 应报错。"""

    def test_detects_gitleaks_removed_from_all_workflows(self, monkeypatch, tmp_path):
        """构造全部 workflow 文件缺失 Gitleaks → 应报错（R9/R10 enforcement 含 '安全扫描'）。"""

    def test_detects_gitleaks_moved_to_security_workflow(self, monkeypatch, tmp_path):
        """Gitleaks 从 ci_cd.yml 迁移到 security.yml/security.yaml → 不应报错（glob 扫描全部 workflow）。"""

    def test_detects_pytest_removed_from_all_workflows(self, monkeypatch, tmp_path):
        """构造全部 workflow run: 命令块缺失 pytest → 应报错（R2/R7/R8 enforcement 含 'CI-test'）。"""

    def test_pytest_in_cache_step_name_does_not_satisfy_ci_test(self, monkeypatch, tmp_path):
        """pytest 只出现在 step 名称或 cache key 中时，不应满足 CI-test 映射。"""
```

### 5.2 测试设计要点

- **纯函数优先**：`_check_enforcement_invariants(redlines, env)` 接受构造数据，不读文件，覆盖 N1~N9 正例/反例
- **集成测试**：`test_check_enforcement_mapping_passes()` 确保当前项目配置通过
- **漂移检测**：用 `monkeypatch` 替换模块级路径常量（`PRECOMMIT_PATH` / `CI_WORKFLOW_GLOBS` / `REDLINES_YAML_PATH` / `PYPROJECT_PATH`）指向 `tmp_path` 构造的临时文件
- **不使用 xFail**：符合用户偏好「e2e test cases must all pass, no xFail cases allowed」
- **glob 扫描验证**：`test_detects_gitleaks_moved_to_security_workflow` 验证 workflow 迁移场景不被误报
- **假阳性验证**：`test_pytest_in_cache_step_name_does_not_satisfy_ci_test` 验证非命令文本中的 `pytest` 不会误满足 N5

---

## 六、场景覆盖矩阵

| 漂移场景 | 触发的不变量 | 检测能力 |
|---|---|---|
| 删除 `redline-check` hook | N1 | ✅ 检测 |
| `redline-check` hook 的 `entry` 改为错误脚本 | N1 | ✅ 检测 |
| 删除 `scripts/check_redlines.py` 文件 | N1 | ✅ 检测 |
| 删除 `lint-imports` hook | N2 | ✅ 检测 |
| `lint-imports` hook 的 `entry` 改为其他命令 | N2 | ✅ 检测 |
| `pyproject.toml` 删除 1 条 importlinter 契约（剩 3 条） | N2 | ✅ 检测 |
| 删除 `ruff-check` hook | N3 | ✅ 检测 |
| `ruff-check` hook 的 `entry` 改为其他命令 | N3 | ✅ 检测 |
| CI 全部 workflow 删除 Gitleaks secret scan | N4 | ✅ 检测 |
| 删除 `.gitleaks.toml` 配置 | N4 | ✅ 检测 |
| Gitleaks 从 `ci_cd.yml` 迁移到 `security.yml` / `security.yaml` | N4 | ✅ 不误报（glob 扫描） |
| 仅保留 `pip-audit`，删除 Gitleaks | N4 | ✅ 检测（pip-audit 不视为 R9/R10 证据） |
| CI 全部 workflow 的 `run:` 命令块删除 `pytest` 命令 | N5 | ✅ 检测 |
| `pytest` 只出现在 step name / cache key / artifact 名称 | N5 | ✅ 检测（不误判为 CI-test） |
| `human_review_required` 与 enforcement 矛盾 | N6/N7/N8/N9 | ✅ 检测 |
| R16 误标为 `human_review_required: true` | N7 | ✅ 检测 |
| R3 删除 `type-ignore-reason` hook | 无 | ❌ 漏检（R3 enforcement 不精确，3c 范围外，已登记） |
| 删除 `docs-consistency` hook 本身 | 无 | ❌ 漏检（meta 悖论，人工评审兜底） |
| R2/R7/R8 特定守护测试用例被删除 | 无 | ❌ 漏检（3c 根本限制，测试覆盖率门控兜底） |
| Hook `files` 过滤器收窄导致 hook 不触发 | 无 | ❌ 漏检（hook 配置审查范畴） |
| CI job `if:` 条件禁用 | 无 | ❌ 漏检（CI 配置审查范畴） |

---

## 七、风险评估

| 风险 | 严重度 | 缓解措施 |
|---|---|---|
| CI workflow 文件拆分/重命名 | 低 | N4/N5 改为 `.yml` / `.yaml` glob 扫描后，新增 workflow 文件自动纳入 |
| enforcement 关键词变更 | 中 | 关键词集合集中在模块顶部常量；不变量失败时强制同步 |
| Hook `entry` 变更 | 中 | N1/N2/N3 entry 校验捕获 |
| import-linter 契约数量漂移 | 中 | N2 数量校验捕获 |
| `run:` 命令块解析漏掉罕见 YAML 写法 | 中 | `_extract_workflow_run_blocks()` 增加单测覆盖单行 `run: pytest` 与块状 `run: |`；无法覆盖的复杂写法登记为人工评审 |
| Gitleaks action 名称升级或迁移到自定义 wrapper | 中 | 失败时要求同步 `_check_gitleaks_scan_exists()` 的匹配规则；R9/R10 仍有人审兜底 |
| 新增 R19+ 用新 enforcement 措辞 | 中 | N8/N9 反向不变量仍守护；新增 R19 需人工评审是否扩展关键词 |
| yml 字段缺失 | 低 | `.get()` 防御性访问；字段完整性由 3b 守护 |
| 关键词子串误匹配 | 低 | word boundary 正则（英文关键词） |
| 3b/3c 顺序依赖 | 低 | 3c 独立解析 yml，允许重复报错，无顺序依赖 |

---

## 八、宪法合规性检查

| 宪法条款 | 合规性 |
|---|---|
| §1.3 极简设计 | ✅ 9 个不变量是覆盖漂移高发场景的最小集；纯函数设计无过度抽象 |
| §1.4 微创修改 | ✅ 仅扩展 check_docs_consistency.py，新增函数不影响现有调用方；redlines.yml schema 不变 |
| §1.5 目标驱动 | ✅ 明确成功标准（9 不变量 + 测试通过）；先理解后精简（已核对当前脚本、redlines.yml、pre-commit、CI、ADR 与技术债） |
| §1.6 复用优先 | ✅ 复用 `_count_local_hooks()` 正则风格、`check_redlines_yaml_consistency()` yml 解析逻辑 |
| §3.1 R3 反幻觉 | ✅ 所有 API（yaml.safe_load / re 正则）均已在现有代码中验证；事实错误承诺已更正 |
| §3.2 复用优先 | ✅ 不引入新依赖（yaml 已是依赖）；不封装成熟库 |
| R18 worktree 隔离 | ✅ 实施时将创建 worktree（当前为设计阶段，无需 worktree） |
| ADR-0003 决策 | ⚠️ 需新增 ADR-0005 **部分推翻** ADR-0003 中「3c 仍不做」的决定（3b 部分不变） |
| ADR-0004 字段精简 | ✅ 不改 yml schema，符合「5 字段精简」原则 |

---

## 九、业界最佳实践对比

| 维度 | 本方案 | 业界最佳实践 | 评估 |
|---|---|---|---|
| 配置校验方式 | 文本匹配（正则） | Schema 校验（JSON Schema / Pydantic） | 文本匹配符合项目现有风格（`_count_local_hooks`），YAGNI 优先可接受 |
| CI workflow 扫描 | glob `.github/workflows/*.yml` / `*.yaml`，并限定 `pytest` 在 `run:` 命令块中匹配 | 同左 | 符合 |
| ADR 推翻关系 | 部分推翻（`Partial Supersedes`） | Nygard 模板支持部分推翻 | 符合 |
| 防御性编程 | `.get()` + 类型检查 | 同左 | 符合 |
| 关键词匹配 | word boundary 正则（英文）+ 子串（中文） | 同左 | 符合 |
| 不变量测试覆盖 | 每不变量正例/反例 + 边界用例 | 同左 | 符合 |
| 安全扫描映射 | Gitleaks secret scan + `.gitleaks.toml` | secret scanning 工具守护密钥/敏感信息泄露 | 符合 |
| 已知限制登记 | 完整登记 + 代码注释 | 同左 | 符合 |
| 守护机制 entry 校验 | hook id + entry 双重校验 | 同左 | 符合 |
| 数量约束校验 | import-linter 契约数量 | 同左 | 符合 |

---

## 十、实施步骤

1. **创建 worktree**（R18）：`git worktree add .worktrees/fix-doc-lint-3c -b fix/doc-lint-3c-enforcement-mapping`
2. **TDD 红-绿-重构**：
   - 先写 `TestEnforcementMapping` 测试类（红）
   - 实现 `_extract_enforcement_keywords` / `_collect_enforcement_environment` / `_check_precommit_hook` / `_extract_workflow_run_blocks` / `_check_workflow_command_exists_any_workflow` / `_check_gitleaks_scan_exists` / `_count_importlinter_contracts` / `_check_enforcement_invariants` / `check_enforcement_mapping`（绿）
   - 重构：提取公共常量、补充 docstring（重构）
3. **集成到 `main()`**：在 `check_redlines_yaml_consistency()` 后调用 `check_enforcement_mapping()`，添加顺序注释
4. **更新 docstring**：`check_docs_consistency.py` 顶部 docstring 移除「3c 仍不做」标注，更新为「3c 已落地」
5. **新增 ADR-0005**：`docs/adr/0005-enforcement-mapping-check.md`，部分推翻 ADR-0003 中 3c 仍不做的决定
6. **更新技术债表**：`docs/debt/known-technical-debt.md` 第一条 P3 更新为「3a/3b/3c 已落地」
7. **验证**（按 CLAUDE.md §1.9 最小验证子集）：
   - `python scripts/check_docs_consistency.py`（直接运行脚本）
   - `python -m pytest tests/unit/test_docs_consistency.py -v`（契约测试）
   - `ruff check scripts/check_docs_consistency.py tests/unit/test_docs_consistency.py`
   - `ruff format --check scripts/check_docs_consistency.py tests/unit/test_docs_consistency.py`
   - `pre-commit run docs-consistency --all-files`
8. **提交 PR**：分支 `fix/doc-lint-3c-enforcement-mapping`，Squash Merge

---

## 十一、验收标准（DoD）

- [ ] `check_enforcement_mapping()` 实现 9 个不变量校验（含 entry 校验、契约数量校验、Gitleaks secret scan 校验、workflow `run:` pytest 命令校验、glob 扫描）
- [ ] `TestEnforcementMapping` 测试类覆盖纯函数正例/反例 + 集成测试（≥15 测试用例，无 xFail）
- [ ] 当前项目配置下 `check_enforcement_mapping()` 返回空错误列表
- [ ] `pre-commit run docs-consistency --all-files` 通过
- [ ] ADR-0005 新增，部分推翻 ADR-0003 中 3c 仍不做的决定（3b 部分不变）
- [ ] `docs/debt/known-technical-debt.md` 第一条 P3 更新为「3a/3b/3c 已落地」
- [ ] 漏检场景（R3 不精确 + meta 悖论 + R2/R7/R8 弱校验 + hook files 收窄 + CI if 禁用）在设计文档与代码注释中明确登记
- [ ] 关键词匹配使用 word boundary 正则（英文）+ 子串匹配（中文）
- [ ] `human_review_required` 字段使用 `.get()` 防御性访问
- [ ] `check_enforcement_mapping()` 独立解析 yml，无顺序依赖
- [ ] `_check_enforcement_invariants(redlines, env)` 不读文件，所有 hook/CI/pyproject/脚本存在性由 `EnforcementEnvironment` 注入

---

## 十二、ADR-0005 草案

```markdown
# ADR-0005: 落地 3c enforcement 字段映射检查

> Status: Accepted
> Date: 2026-07-17
> Owner: 架构维护者
> Partial Supersedes: ADR-0003 (3c portion only; 3b portion remains valid)

## Context

ADR-0003 决策「3b 落地 + 3c 不做」，3c 不做的理由为：
1. 强制状态字段语义复杂，机器校验需大量特例
2. 当前强制状态字段未发生漂移事故
3. 收益成本比不及 3b

本次设计评审对 3c 重新评估，发现 ADR-0003 拒绝理由可化解：
1. 语义复杂 → 提取 9 个高价值不变量，不追求全量精确映射；R16 单点特例集中管理
2. 决策触发 → 架构维护者主动要求重新评估 3c；未宣称已发生漂移事故
3. 收益成本比 → 3b 已落地（redlines.yml schema 稳定），3c 是 3b 的自然延伸，边际成本低

## Decision

1. **部分推翻 ADR-0003 的 3c 决策**：3c 从「不做」改为「落地」。
   - 扩展 `scripts/check_docs_consistency.py` 新增 `check_enforcement_mapping()` 校验 9 个不变量
   - 新增 `EnforcementEnvironment` 配置快照，保证核心不变量校验可纯函数测试
   - 新增 `tests/unit/test_docs_consistency.py::TestEnforcementMapping` 类（TDD）
2. **维持 ADR-0003 的 3b 决策**：3b（红线 R1~R18 编号 append-only 检查）不变。
3. **已知漏检场景登记**：R3 enforcement 不精确、meta 悖论、R2/R7/R8 弱校验、hook files 收窄、CI if 禁用均在设计文档与代码注释中登记。

## Consequences

- **正向**：enforcement 字段声称的守护机制从纯人工评审升级为机器守护 + 人工兜底；hook 删除/entry 篡改/契约数量漂移/Gitleaks 删除/CI 测试命令删除等漂移场景可被检测。
- **负向**：新增 9 个不变量的维护负担；enforcement 关键词变更时需同步不变量常量。
- **缓解**：关键词集合集中在模块顶部常量，维护成本低；不变量失败时提供精确报错。

## Alternatives

- **维持 3c 不做**：拒绝。架构维护者已主动要求重新评估；在 3b 已落地后继续靠人工评审守护 enforcement 漂移风险不可控。
- **全量精确映射**：拒绝。18 套签名维护成本高，违反 YAGNI；签名变更频繁。
- **粗粒度类别映射**：拒绝。无法检测「删除 redline-check hook 而保留 ruff-check hook」这类具体漂移。
```

---

## 十三、设计修订历史

### v1（初版）→ v2（对抗审查修订）

| 修订点 | v1 缺陷 | v2 修订 |
|---|---|---|
| N1/N2/N3 | 仅校验 hook id，未校验 entry | 新增 entry 关键词校验 |
| N2 | 未校验 import-linter 契约数量 | 新增契约数量校验（从 enforcement 文本解析期望数量） |
| N4/N5 | 硬编码 `ci_cd.yml` 单文件；N4 误用 `pip-audit`；N5 裸匹配 `pytest` | 改为 glob 扫描 `.github/workflows/*.yml` / `*.yaml`；N4 映射 Gitleaks secret scan；N5 只匹配 `run:` 命令块中的 pytest 命令 |
| N6~N9 | 直接字典访问 `entry["human_review_required"]` | 改用 `.get()` 防御性访问 |
| 关键词匹配 | 子串 `in` 匹配 | 英文关键词改用 word boundary 正则 |
| R3 漏检承诺 | 声称「R3 精确化后 N1 自然覆盖」 | 更正为事实错误；R3 精确化后需新增不变量 |
| ADR 关系 | 笼统「推翻 ADR-0003」 | 明确为「部分推翻」（3b 部分不变） |
| 漏检登记 | 未登记 R2/R7/R8 弱校验限制 | 登记为 3c 根本限制 |
| 3b/3c 顺序 | 隐式依赖 3b 解析 yml | 3c 独立解析 yml，允许重复报错 |
| 纯函数边界 | `_check_enforcement_invariants(redlines)` 无法测试 hook/CI 漂移 | 新增 `EnforcementEnvironment` 注入配置快照，核心校验不读文件 |

---

## 十四、v3 修订（多 subagent 三视角检视后采纳）

> 修订日期：2026-07-17
> 触发：Architecture / QA / Skeptic 三 subagent 视角检视后采纳的修订点
> 性质：保留 v2 全部 9 个不变量，仅修订实现细节与测试覆盖

### 14.1 模块级常量补全（Architecture Required-1）

v2 §4.2 模块级常量清单遗漏两个测试可注入路径常量。v3 显式新增：

```python
# 3c: enforcement 校验所需的项目配置路径常量（monkeypatch 可注入）
CI_WORKFLOW_DIR = ROOT / ".github" / "workflows"
CHECK_REDLINES_SCRIPT_PATH = ROOT / "scripts" / "check_redlines.py"
GITLEAKS_CONFIG_PATH = ROOT / ".gitleaks.toml"
```

`_collect_enforcement_environment()` 必须且仅通过上述常量 + 已有 `PRECOMMIT_PATH` / `PYPROJECT_PATH` / `REDLINES_YAML_PATH` 访问文件系统，禁止内联路径构造（确保 monkeypatch 生效）。

`CI_WORKFLOW_GLOBS` 元组保留为 glob 模式字符串，与 `CI_WORKFLOW_DIR` 配合使用：`tuple(CI_WORKFLOW_DIR.glob(pattern) for pattern in CI_WORKFLOW_GLOBS)`。

### 14.2 测试覆盖补全（QA Required 1~7）

v2 §5.1 测试清单约 23 个，存在系统性覆盖缺口。v3 补全至约 32-35 个测试用例：

#### 14.2.1 N3 不变量测试完全补齐（v2 缺失）

```python
def test_n3_ruff_hook_missing(self):
    """N3: enforcement 含 'ruff' 但 ruff-check hook 不存在 → 报错。"""

def test_n3_ruff_hook_wrong_entry(self):
    """N3: enforcement 含 'ruff' 且 hook 存在但 entry 不含 ruff → 报错。"""

def test_n3_ruff_hook_correct(self):
    """N3: enforcement 含 'ruff' 且 hook + entry 正确 → 通过。"""
```

#### 14.2.2 N1 第三腿反例（v2 缺失）

```python
def test_n1_check_redlines_script_missing(self):
    """N1: hook + entry 正确但 scripts/check_redlines.py 文件不存在 → 报错。"""
```

#### 14.2.3 N2 多象限补齐（v2 缺失）

```python
def test_n2_import_linter_hook_missing(self):
    """N2: enforcement 含 'import-linter' 但 lint-imports hook 不存在 → 报错。"""

def test_n2_import_linter_wrong_entry(self):
    """N2: enforcement 含 'import-linter' 且 hook 存在但 entry 不含 lint-imports → 报错。"""

def test_n2_import_linter_no_contract_count_in_enforcement_skipped(self):
    """N2: enforcement 含 'import-linter' 但未含『N 条契约』描述 → 跳过数量校验（不报错）。"""

def test_n2_import_linter_contract_count_match(self):
    """N2: enforcement 声明 '4 条契约' 且 pyproject.toml 实际 4 条 → 通过。"""
```

#### 14.2.4 N4 半配置反例（v2 缺失）

```python
def test_n4_gitleaks_workflow_exists_but_config_missing(self):
    """N4: Gitleaks workflow 存在但 .gitleaks.toml 缺失 → 报错。"""

def test_n4_gitleaks_config_exists_but_workflow_missing(self):
    """N4: .gitleaks.toml 存在但所有 workflow 均无 Gitleaks → 报错。"""
```

#### 14.2.5 .yaml 扩展名独立测试（v2 缺失）

```python
def test_detects_gitleaks_moved_to_yaml_extension_workflow(self, monkeypatch, tmp_path):
    """Gitleaks 迁移到 security.yaml（非 .yml）文件 → 不应报错（glob 双模式扫描）。"""
```

#### 14.2.6 pytest 命令匹配假阳性 + _extract_workflow_run_blocks 独立单测（v2 缺失）

```python
def test_pip_install_pytest_in_run_block_does_not_satisfy_ci_test(self, monkeypatch, tmp_path):
    """run: 块中含 'pip install pytest' → 不应被误判为满足 CI-test 映射。

    真实场景：ci_cd.yml:314 含 'pip install playwright pytest-playwright'。
    虽然此场景因 '-playwright' 后缀不会误匹配，但裸 'pip install pytest' 会。
    """
    # 注：实际验证 'pip install pytest' 末尾的 'pytest' 后跟行尾或空格
    # 需要确认正则是否区分 'pytest' 作为独立命令 vs 'pytest' 作为 pip install 参数

def test_extract_workflow_run_blocks_excludes_step_names(self):
    """_extract_workflow_run_blocks() 不应纳入 step name 行（只含 run: 命令块）。"""

def test_extract_workflow_run_blocks_handles_four_yaml_styles(self):
    """_extract_workflow_run_blocks() 必须覆盖 4 种 YAML 写法：
    1. run: pytest（单行无引号）
    2. run: python -m pytest tests/unit/（单行带参数）
    3. run: | + 多行命令块（块状字面量）
    4. run: >- + 多行折叠块（折叠去尾换行）
    """
```

**重要发现**：`pip install pytest` 中的 `pytest` 后跟空格或行尾，会被正则 `(^|\s)(python\s+-m\s+pytest|pytest)(\s|$)` 匹配。这是真实假阳性场景。v3 修订 N5 正则为**否定后顾**：

```python
# 排除 'pip install pytest' / 'pip install pytest-playwright' 等安装命令
PYTEST_COMMAND_PATTERN = re.compile(
    r"(?<!pip install )(?:python\s+-m\s+)?pytest(?=\s|$)"
)
```

或更简洁的方案：在 `_check_workflow_command_exists_any_workflow()` 中先排除 `pip install` 行再匹配。

#### 14.2.7 R9 多关键词 + R16 双 pending 特例（v2 缺失）

```python
def test_n4_and_n6_both_checked_for_r9_style_enforcement(self):
    """R9 风格 enforcement='安全扫描 + 仅人工评审' 触发 N4 + N6 双重校验。
    构造 Gitleaks 缺失场景：N4 报错，N6 通过（human_review_required=true）。
    """

def test_r16_dual_pending_keywords_passes_n7(self):
    """R16 enforcement='可自动化待实现（AST 检查，暂缓：误报风险高）'
    同时含「待实现」和「暂缓」，human_review_required=false → N7 通过（不报错）。
    """
```

### 14.3 异常处理策略明示（Skeptic Required）

v2 §4.2 未明示 `_collect_enforcement_environment()` 抛异常时的行为。v3 明确：

```python
def check_enforcement_mapping() -> list[str]:
    """检查项 8: enforcement 字段与实际 hook/CI job 映射一致性（3c 落地）。

    异常处理策略：
    - 环境收集失败（PermissionError / OSError）时**硬失败**：抛异常传播到 main()，
      脚本以非零退出码退出。禁止 try/except 吞没异常（避免漂移静默漏检）。
    - yml 解析失败时返回精确错误列表（与 3b 一致，允许重复报错）。
    - 不变量校验失败时返回错误列表（不抛异常）。
    """
```

实施约束：`_collect_enforcement_environment()` 内**禁止** `try/except` 吞没 `OSError` / `PermissionError`。仅 `yaml.safe_load` 可捕获 `yaml.YAMLError` 转为错误列表（与 3b 一致）。

### 14.4 ADR-0005 扩展触发条件 + 同日推翻说明 + R3 跟进任务（Skeptic Required）

v2 §12 ADR-0005 草案需补充以下三点：

#### 14.4.1 扩展触发条件

技术债表 P3 原触发条件「强制状态字段发生漂移事故或红线违规频发」不足以覆盖本次主动触发场景。v3 在 ADR-0005 Context 段明示扩展：

> 触发来源扩展：除原「漂移事故 / 违规频发」事件驱动触发外，新增「架构维护者主动触发」作为合法触发源。本次 3c 落地即由架构维护者主动要求重新评估，非已发生漂移事故。技术债表 P3 的 upgrade 触发条件同步更新为「强制状态字段发生漂移事故、红线违规频发、或架构维护者主动重新评估时」。

#### 14.4.2 同日推翻说明

ADR-0005 Date 与 ADR-0003 同为 2026-07-17。v3 在 ADR-0005 Context 段补充说明：

> 同日推翻理由：ADR-0003 落地 3b 后，架构维护者立即对 3c 重新评估。本次推翻基于 3b 落地后的即时评估，认为 3c 是 3b 的自然延伸（共享 yml 解析、模块级路径常量、测试模式），边际成本低。虽与 ADR-0003 同日，但本次推翻非草率决策，而是 3b 落地验证后的连续治理动作。ADR append-only 不可变性：ADR-0003 保留作为历史记录不修改，仅由 ADR-0005 标注 Partial Supersedes 关系。

#### 14.4.3 R3 yml 精确化跟进任务

R3 enforcement = "pre-commit"（无具体 hook 名）是 yml schema 不精确问题，非 3c 范围限制。v3 在 ADR-0005 Consequences 段登记跟进任务：

> 跟进任务（不在本次 3c 范围内）：R3 enforcement 文本精确化为 "pre-commit（type-ignore-reason）"，然后扩展 N1 或新增 N10 守护 type-ignore-reason hook 存在。此任务属 yml schema 精确化，由独立 PR 处理，不阻塞本次 3c 落地。

### 14.5 技术债表触发条件同步更新

`docs/debt/known-technical-debt.md` 第一条 P3 条目需同步更新：

- v2 描述：「3a 已落地，3b 已落地（见 ADR-0003），3c 仍不做」
- v3 描述：「3a/3b/3c 已全部落地（见 ADR-0003 + ADR-0005）」
- v3 upgrade 触发条件：「强制状态字段发生漂移事故、红线违规频发、或架构维护者主动重新评估时重新扩展不变量覆盖范围」

### 14.6 测试文件位置（QA Recommended）

`TestEnforcementMapping` 类置于 `TestRedlinesYamlConsistency` 之后（与 main() 调用顺序 3b → 3c 一致）。

### 14.7 不采纳的 Skeptic 建议（含理由）

| Skeptic 建议 | 不采纳理由 |
|---|---|
| N6~N9 迁移至 3b | redlines.yml 注释已声明该约束（line 13），作为 enforcement 语义守护留在 3c 合理；用户方案明确将其纳入 3c |
| 砍 N1/N2/N3（CI 已守护） | Skeptic 分析有误：删除 `redline-check` hook 会导致 R4/R12/R13/R14/R15 检查被**静默跳过**（CI 不报错，因 hook 不运行）。3c 守护的是"hook 被意外删除"漂移，非纯冗余 |
| 砍 N7（推测性设计） | N7 成本极低（一个 .get() 检查），与 N9 配合形成完整守护；保留防止 R16 误标 human_review_required: true |
| ADR-0005 先 Proposed 再 Accepted | 用户明确要求本次落地；ADR 允许直接 Accepted；同日推翻理由已在 §14.4.2 明示 |

### 14.8 实施步骤（v3 更新）

1. **创建分支**（已完成）：`fix/doc-lint-3c-enforcement-mapping`（基于 main）
2. **更新方案文档 v3**（当前步骤）：追加本章节
3. **TDD 实施**：
   - 阶段 A：实现纯函数（`_extract_enforcement_keywords` / `_check_precommit_hook` / `_extract_workflow_run_blocks` / `_check_workflow_command_exists_any_workflow` / `_check_gitleaks_scan_exists` / `_count_importlinter_contracts` / `_check_enforcement_invariants`）+ 补充模块级常量（§14.1）
   - 阶段 B：写纯函数测试（覆盖 §14.2 全部用例）→ 红 → 实现 → 绿
   - 阶段 C：实现 `check_enforcement_mapping()` + `_collect_enforcement_environment()` → 集成 main() → 集成测试
4. **新增 ADR-0005**（含 §14.4 全部补充）+ 更新技术债表（§14.5）+ 更新 docstring
5. **多 subagent 实施后检视**：Architecture + QA + Skeptic 三视角
6. **本地验证**：`python scripts/check_docs_consistency.py` + `pytest tests/unit/test_docs_consistency.py -v` + `ruff check` + `ruff format --check` + `pre-commit run docs-consistency --all-files`
7. **提交 + 推送 + 创建 PR**（Squash Merge）