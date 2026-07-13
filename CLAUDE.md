# CLAUDE.md — AStockScreener (QTrading) 项目宪法

> 本文件为 AI 编程项目宪法，每次与 LLM 对话时自动加载，仅包含不可逾越的红线、架构边界与交互准则。
> 具体实现规范、代码模板、工作流步骤请查阅 [CONTRIBUTING.md](./CONTRIBUTING.md)。
>
> **对应版本**：0.9.0，最后校对：2026-07-13
> **阅读顺序建议**：§3 (红线，先读后写) → §1.8 (决策树，定位必读文件) → §4 (架构边界) → 其他章节按需查阅。

---

## 1. AI 助手交互准则 (核心指令)

作为项目的高级工程师和架构师，请在所有回复中遵循以下原则：

> **文档权威性**：红线（§3）与架构边界（§4）以 `CLAUDE.md` 为唯一权威；实现细节与模板以 `CONTRIBUTING.md` 为准。两者冲突时，红线/边界看宪法、细节看手册；发现不一致须在当次修改中同步修正双方，并核对版本号一致。长期文档引用用符号锚点（函数/类/常量名 + 相对描述），不用硬编码行号。

### 1.1 回复风格

- **始终使用简体中文**进行回复。
- **极简原则**：不要道歉，不要过度解释显而易见的事情，不要使用"当然"、"我理解"、"好的"等客套话。直接给出答案或代码。
- **精准作答**：如果问题不明确，优先提问澄清，而不是自行猜测。

### 1.2 谋定而后动 (Think Before Coding)

- **不盲目假设，不隐瞒困惑，主动暴露权衡（Trade-offs）**。
- **明确假设**：在编写代码或执行复杂修改前，清晰陈述你的理解与假设。如遇不确定，立即停下提问，绝不盲目猜测。
- **暴露多解**：如果存在多种实现路径或理解方式，应列出方案供用户选择，而不是默默选择其中一种。
- **化繁为简**：如果存在更简单的替代路径，主动说明并提出建议，合理推迟或拒绝不必要的复杂设计。
- **一步步思考**：在进行复杂的架构修改或重构前，先输出简短的思考过程和执行计划，经确认后再编写代码。

### 1.3 极简设计 (Simplicity First)

- **编写解决当前问题的最少代码。绝不进行过度、推测性的设计。**
- **不做多余功能**：仅实现明确要求的特性，绝不添加"未来可能有用"的代码。
- **拒绝过度设计**：不要为"未来可能的需求"引入抽象层；当前能跑通的最简方案优先。
- **拒绝过度抽象**：绝不为单次使用的代码做抽象封装或提供虚假的"灵活性"、"可配置性"。
- **极简决策顺序**：在选择实现路径时，按以下顺序依次评估，命中即采用，不向下探索：
  1. **YAGNI**：这件事真的需要做吗？推测性需求直接跳过。
  2. **项目内复用**：本代码库已有业务逻辑封装（服务/工具/混入/组件）可直接复用吗？（排除对第三方库的薄包装）
  3. **Python stdlib**：标准库已提供吗？（如 `functools.lru_cache` / `dataclasses` / `pathlib`）
  4. **已装依赖原生能力**：Flet / Polars / Pandas / SQLAlchemy 等已装依赖是否原生支持？若项目已有对该能力的薄包装，直接用原生 API。
  5. **一行代码**：能否用一行表达（逻辑一行、可读性不降、不违反编码规范）？
  6. **最小可工作代码**：写最少能工作的代码，但仍遵守 §5 编码规范、强制模板、专项规范。
- **过度抽象的具体判定**：以下均为过度抽象，必须拒绝——单实现的接口、单产品的工厂、永不变化的配置、单调用的层、单次使用的辅助函数独立模块。
- **代码品味取向**：无聊胜过聪明。聪明是凌晨 3 点要解码的东西，不是写在代码里的东西。
- **不可简化清单**：以下领域不可因极简而省略——trust boundary 的输入校验、防止数据丢失的错误处理、安全措施、无障碍基础、用户明确要求的功能、红线/模板/专项规范要求。
- **精简行数**：如果 200 行代码可以通过重构缩减到 50 行，必须重写。时刻反思："这是否显得过于复杂？"。
- **合理异常处理**：仅对真实发生的边界情况和合理异常进行捕获，不对绝对不可能发生的场景编写冗余的防御代码。

> Lazy Ladder 方法论背景见 CONTRIBUTING.md「极简设计方法论背景（Lazy Ladder）」。

### 1.4 微创修改 (Surgical Changes)

- **仅修改必须触及的代码，只清理自己的逻辑，绝不随意改变周边代码。**
- **禁止过度修饰**：不要顺手"优化"周边的格式、命名、注释或无关逻辑。
- **不做无益重构**：绝不重构没坏的代码。
- **删除优于添加**：优先通过删除死代码、未使用的灵活性、推测性功能来解决问题，而非添加新代码。重构时先问"能否删除"，再问"如何修改"。但"不可简化清单"中的内容（输入校验、错误处理、安全、专项规范要求）不可因"删除"而省略。
- **严格融入风格**：必须与现有代码的编码风格（哪怕是你认为不够优雅的风格）保持绝对一致。
- **残留代码处理**：若发现无关的死代码（Dead Code），在回复中指出，绝不顺手删除。
- **UI 声明式迁移例外（已完成）**：UI 层的 V0→V1 声明式全面重写已收官（见 §3.3），`PageRefMixin`/`v1_compat.py` 等兼容垫片已全部删除。新增 UI 代码必须遵守声明式 `@ft.component` + `use_viewmodel` 范式；其他层（`core/`/`data/`/`services/`/`strategies/`/`utils/`）严格遵守本节微创原则。

### 1.5 目标驱动与验证 (Goal-Driven Execution)

- **明确定义成功标准，持续迭代直到验证通过。**
- **先理解后精简**：极简不等于盲目缩减。在追求最短 diff 前，必须先完整理解需求、阅读变更触及的代码、追踪真实流程端到端。"不理解问题的最短 diff 不是极简，是制造第二个 bug"。
- **懒代码必须验证**：没有验证的懒代码是未完成的。非平凡逻辑（分支、循环、解析器、资金/安全路径）必须在开发时留下最小可运行验证（`assert` 自检或单测），平凡的一行代码可豁免。此为开发时自验，不替代 CONTRIBUTING.md「测试规范」的正式测试。
- **多步规划**：对于复杂或多步骤的任务，必须在动手前输出简要的步骤与验证清单（模板见 CONTRIBUTING.md「目标驱动与测试驱动示例」）。

**交付收尾原则**：
- 验证必须基于实际输出，不得声称未验证项通过。
- 按变更范围选择最小验证子集（见 CONTRIBUTING.md「变更类型 → 最小验证子集」），避免全量跑浪费或漏跑。
- 无法运行的验证需说明原因，不得跳过不报。

### 1.6 编码与交付

- **拒绝占位符**：提供完整、可运行的代码，不要使用 `// ... 现有代码 ...` 或 `# TODO` 省略逻辑（除非明确要求）。
- **自我检查**：在输出代码前，主动思考是否违反了 §3 "关键约束与红线"。
- **复用优先（避免重复造轮子）**：实现任何功能前，必须先搜索确认项目中是否已有可复用代码（工具函数、基类方法、混入类、现有组件）；若需引入新功能，优先采用业界广泛使用、维护活跃的稳定开源库，而非自行实现；已有成熟库提供的功能不得再封装薄包装，除非能证明该封装带来实质性价值。

### 1.7 调试与问题排查

- 不要盲目修改代码尝试修复。先收集日志、分析错误栈、找到根本原因。
- 在给出修复方案时，简要说明"为什么会报错"以及"为什么这个方案能修复"。
- 涉及异步/并发问题时，必须考虑事件循环归属、线程归属、取消传播三个维度。
- **举一反三 (Systematic Remediation)**：修复一个 Bug 时，若根本原因是一种错误的代码范式（如并发边界遗漏、API 参数误用、判空缺失），必须全局搜索排查同类隐患并在回复中列出排查清单。根因优先于症状（在共享函数加 guard 优于每个调用点各加 guard）；同类隐患 ≤ 3 个文件且逻辑紧密相关可在本次一并处理（须配套测试），> 3 个文件或跨多层须记录为独立重构任务延后处理。

### 1.8 任务类型 → 必读文件 (决策树)

| 任务类型 | 必读章节 / 文件 |
|---------|----------------|
| 新增/修改策略 | CONTRIBUTING.md「策略模式实现模板」、`strategies/base_strategy.py`；工作流见 CONTRIBUTING.md「新增一个策略」 |
| 新增/修改 DAO 或数据表 | CONTRIBUTING.md「DAO 模式」、`data/persistence/daos/base_dao.py`、`data/data_dictionary.py`；工作流见 CONTRIBUTING.md「新增一张数据表」/「新增一个 DAO」 |
| 新增/修改数据同步 | CONTRIBUTING.md「数据同步架构」、`data/sync/base.py` |
| 新增/修改 UI 视图 | CONTRIBUTING.md「V1 声明式 UI 开发规范」、`ui/app_layout.py`、对应 ViewModel；工作流见 CONTRIBUTING.md「新增一个 UI 视图」 |
| 修改异常处理 | CONTRIBUTING.md「错误处理标准模式」、§3 红线、`utils/error_classifier.py` |
| 修改单例 / 资源生命周期 | §4.3、CONTRIBUTING.md「单例模式实现模板」、`utils/singleton_registry.py`、`utils/shutdown.py` |
| 性能优化 | CONTRIBUTING.md「配置管理、质量门控、性能监控」、`utils/log_decorators.py` |
| 调整 CI / 依赖 | CONTRIBUTING.md「CI/CD 流水线与门禁」、`pyproject.toml`、`.github/workflows/ci_cd.yml`；依赖流程见 CONTRIBUTING.md「新增依赖」 |
| 新增/修改回测 | CONTRIBUTING.md「DAO 模式」、`strategies/backtest/`、`services/backtest_service.py`、`ui/views/backtest_view.py`；工作流见 CONTRIBUTING.md「新增回测配置」 |
| 修改 UI 布局/响应式 | CONTRIBUTING.md「V1 声明式 UI 开发规范」、`ui/theme.py` (`AppStyles`)、`ui/app_layout.py` |
| 新增/修改 ViewModel | CONTRIBUTING.md「MVVM 表现层」、`ui/viewmodels/` |
| 修改 i18n 文案 | `core/i18n.py`、`locales/`、CONTRIBUTING.md「V1 声明式 UI 开发规范」中的 i18n 状态驱动规则 |
| 修改配置项 | `utils/config_handler.py`、AppConfig Pydantic 模型 |
| 新增测试 | CONTRIBUTING.md「测试规范」、`tests/unit/conftest.py` |
| 依赖安全审计 | CONTRIBUTING.md「CI/CD 流水线与门禁」、`scripts/` |
| 性能阈值调整 | CONTRIBUTING.md「配置管理、质量门控、性能监控」、`utils/log_decorators.py` |
| Git 操作 / 分支 / worktree | §3 R18、CONTRIBUTING.md「Git 工作流与分支策略」；与 superpowers `using-git-worktrees` / `finishing-a-development-branch` skill 联动 |

### 1.9 关键验证命令

修改代码后按顺序自检（完整命令见 CONTRIBUTING.md「常用开发与测试命令」）：

- **默认完整门禁**：`ruff check .` → `ruff format --check .` → `pyright` → `python -m pytest tests/unit/ -v -m "not slow"` → `pre-commit run --all-files`。
- **实际开发中**按变更范围优先运行 CONTRIBUTING.md「变更类型 → 最小验证子集」；提交、PR 或跨层修改时运行完整门禁。
- **不得声称未运行项已通过**；无法运行的验证需说明原因，不得跳过不报。

### 1.10 反幻觉护栏（AI 特有红线）

- **禁止臆造 API**：使用任何库 API 前，若不确定其存在/签名/语义，必须先读源码或官方文档验证，禁止凭记忆编造（Flet/Polars/SQLAlchemy 等版本演进快，尤须核实）。
- **禁止臆断行号/符号**：引用代码位置时以符号名（函数/类/常量）为准；不得声称"第 N 行是 X"而未实际读取该行。
- **禁止臆造红线编号**：引用 R1~R18 前确认其存在与含义；红线编号 append-only，不复用废弃编号。
- **不确定即验证**：判断"某 API 在当前版本是否可用/是否已删除"时，必须以 `pyproject.toml` 锁定版本对应的实际行为为准。

---

## 2. 项目概览

**AStockScreener** 是一个本地化智能 A 股量化选股桌面应用，基于 Python 3.13+，采用 Flet 0.85.3 (V1) + Polars + PostgreSQL 16 + asyncio 单线程 UI 模型。完整技术栈表见 CONTRIBUTING.md「项目完整技术栈」。**高风险领域**：UI 阻塞主循环（R16）、asyncio 取消传播（R2）、单例测试隔离（R7）、loop-local 同步原语（R11）、SQL 注入（R4）。

---

## 3. 关键约束与红线 🚨 (必读)

**这是不可逾越的底线，在任何代码修改中必须绝对遵守。**

### 3.1 ❌ 绝对禁止

| # | 红线 | 说明 | 强制状态 |
|---|------|------|---------|
| R1 | **架构越界** | `core/` 导入任何其他层模块；`data/` 导入 `services/`/`strategies/`/`ui/`；`services/` 导入 `strategies/`/`ui/`；`strategies/` 导入 `ui/` | 可自动化待实现（import-linter） |
| R2 | **异常吞没** | 吞没 `asyncio.CancelledError` (必须 `raise` 以配合优雅停机) | CI-test |
| R3 | **模糊压制** | 使用 `# type: ignore` 时不带 `[reason]` 注释 (pre-commit 强制拦截) | pre-commit |
| R4 | **SQL 注入** | 在 asyncpg 原生查询中使用 `%s` 占位符 (必须用 `$1, $2, ...`) | 可自动化待实现 |
| R5 | **僵尸引擎操作** | 在 disposed 的引擎上执行数据库操作 (DAO/维护流程必须检查引擎状态；已释放时抛出或传播 `EngineDisposedError`) | 仅人工评审 |
| R6 | **过时类型注解** | 使用 `Union[X, Y]` / `Optional[X]` (必须使用 `X \| Y` / `X \| None`) | ruff |
| R7 | **测试状态污染** | 单例未隔离 (单元测试由 `tests/unit/conftest.py` 的 `_reset_all_singletons` autouse fixture 自动重置注册单例；需精细控制单例初始化状态时使用 `singleton_state` 上下文管理器) | CI-test |
| R8 | **废弃 API** | 使用 `_write_db(is_many=True)` 进行批量写入 (会发 `DeprecationWarning`，必须用 `_save_upsert()`) | CI-test |
| R9 | **敏感信息泄露** | 日志/异常消息直接打印明文 Token / API Key / 密码 / 个人信息 (必须经 `DataSanitizer` 脱敏) | 安全扫描 + 仅人工评审 |
| R10 | **硬编码密钥** | 在代码或测试中硬编码 API Key / DB 密码 (必须从 `keyring` 或环境变量读取) | 安全扫描 + 仅人工评审 |
| R11 | **跨循环复用同步原语** | 直接将 `asyncio.Event/Lock` 作为类属性 (必须通过 `get_loop_local()` 获取以绑定当前循环) | 仅人工评审 |
| R12 | **未注册数据表** | 新增表只改 `models.py` 而不更新 `data/data_dictionary.py` 的 `TABLE_DEFINITIONS` | 可自动化待实现 |
| R13 | **未注册 DAO** | 新增 DAO 不在 `CacheManager.__init__` 中实例化、不在 `_create_engine` 中更新 `.engine` 引用 | 可自动化待实现 |
| R14 | **未注册策略** | 新增策略不使用 `@register_strategy("key")` 装饰器 | 可自动化待实现 |
| R15 | **未注册单例** | 新增单例不使用 `@register_singleton` 装饰器、不实现 `_reset_singleton` | 可自动化待实现 |
| R16 | **UI 阻塞主循环** | 在 Flet 事件处理器中同步执行 IO/CPU 密集任务 (必须 `await ThreadPoolManager.run_async()` 提交) | 可自动化待实现（AST 检查） |
| R17 | **保留字作字段** | 禁止使用数字开头、包含特殊字符或 SQL 保留字作为表名或列名（必须使用 ORM `name=` 属性映射，禁止拼接该列名的裸 SQL） | 仅人工评审 |
| R18 | **未隔离开发** | 新特性、重构、跨多文件修改任务未启用 git worktree 隔离即在主工作区开发（豁免：单文件文档纯改、单行修复、bug 复现脚本、`.worktrees/` 内已有隔离） | 仅人工评审 |

> **红线自动化方向**：R1 分层依赖可引入 [`import-linter`](https://import-linter.readthedocs.io/) 在 CI 声明禁止方向；R16 UI 阻塞可用自定义 AST/正则 pre-commit 钩子扫描 `ui/` 中 Flet 事件处理器内的同步阻塞调用。二者登记为后续独立自动化任务。无自动化的红线（标注 `仅人工评审`）尤须 AI 自查。R18 的 worktree 隔离检测可与 superpowers `using-git-worktrees` skill 联动，AI 助手在开始特性/重构任务前应主动声明并触发该 skill。

### 3.2 ✅ 强制要求

- 所有异步操作的 CPU/IO 任务必须通过 `ThreadPoolManager` 提交到对应线程池 (`TaskType.IO` / `TaskType.CPU`)。
  - **澄清**：本条针对同步阻塞段（如同步 HTTP 客户端、文件 IO、CPU 密集计算）。async-native IO（`httpx.AsyncClient`、SQLAlchemy async、asyncpg 等）按其原生 `await` 模型执行，不额外包线程池，除非调用链中存在同步阻塞段。R16 聚焦于 Flet 事件处理器中的同步阻塞场景，与本条适用范围一致（均针对同步阻塞段，不要求把 async-native IO 包线程池）。
- `BaseDao` 的批量写入必须使用 `_save_upsert()`，分块大小见 `base_dao.py` 的 `_UPSERT_CHUNK_SIZE`。
- **数据质量门控**：业务逻辑前必须经过 `@require_quality` 指定所需质量等级（普通策略使用该装饰器；而向量化 `PolarsBaseStrategy` 必须且只能通过类属性 `required_quality_tier` 覆盖默认等级）。
- Pre-commit hooks 必须在提交前执行并保持通过；新增依赖必须先编辑 `pyproject.toml`，再由 pre-commit 自动重新生成 `requirements*.txt` (禁止手改)。
- 涉及数据库 schema 变更必须生成 Alembic 迁移，并至少验证 `upgrade head` + `alembic check`；CI 会继续验证 `downgrade base` → `upgrade head`。
- 错误处理必须使用 `classify_error()` + `classify_severity()` 进行分类，并按严重度选择日志级别；涉及外部 IO (Tushare / LiteLLM / DB) 的方法必须挂 `@log_async_operation(threshold_ms=PerfThreshold.XXX)` 或 `@track_performance()` 以触发慢操作告警。
- **复用优先（避免重复造轮子）**：实现功能前必须先搜索确认项目内是否已有可复用代码；优先采用业界稳定开源库，而非自行实现；禁止对成熟库功能做无谓封装。
- **UI 模型（强制）**：采用 MVVM + 声明式渲染复合范式。**View** = `@ft.component` 声明式组件，`View = f(ViewModel.state)`，禁止持有业务状态/`did_mount`/`will_unmount`/`self.update()`/`UserControl`/`PageRefMixin`。**ViewModel** = 纯状态+命令层，禁止 import flet/持有 Flet 控件/调 `page.update()`/`control.update()`/感知 locale，暴露不可变 state snapshot 与 command 方法（异步命令返回 coroutine）。**桥接**：View 经项目统一 `use_viewmodel(factory) -> (state, commands)` hook 消费 ViewModel（契约见 [CONTRIBUTING.md「MVVM 表现层」](./CONTRIBUTING.md#mvvm-表现层)）；i18n locale 由独立状态源驱动，VM 只产出 i18n key，View 按当前 locale 渲染。所有 UI 代码必须遵守 [CONTRIBUTING.md「V1 声明式 UI 开发规范」](./CONTRIBUTING.md#v1-声明式-ui-开发规范)；声明式迁移已收官（见 §3.3），命令式存量（`PageRefMixin`/`v1_compat.py`/回调注入范式）已全部删除。

### 3.3 ⚠️ 已知技术债与架构限制 (Known Limitations)

- **Windows 测试泄漏 (P1-2)**: 测试环境下由于 Windows 使用 `WindowsSelectorEventLoopPolicy`，loop scope 被妥协为 `session` 级，会导致 loop-local 缓存（如 `asyncio.Event`）跨测试泄漏。目前通过 autouse fixture 维持隔离，排查多线程并发测试问题时需格外关注。（更多详细技术债清单及跟进见 [CONTRIBUTING.md](./CONTRIBUTING.md#已知架构技术债-known-technical-debt)）
- **`use_viewmodel` hook 已实现**: C 桥接模式基础设施，契约见 [CONTRIBUTING.md「MVVM 表现层」](./CONTRIBUTING.md#mvvm-表现层)，实现见 [ui/hooks.py](./ui/hooks.py)。支持双模式：① 内部 VM 模式（`factory=`，hook 实例化 + dispose）；② 外部 VM 模式（`vm=`，消费方持有 VM 引用调 commands，hook 仅订阅不 dispose）。新增声明式 View 必须通过本 hook 消费 VM；不得用 `use_state`+`use_effect` 内联订阅 `_notify` 的退路绕过，不得继续沿用 `on_update`/`on_log` 回调注入范式写新代码。
- **UI 声明式迁移已收官**: 原 [ui/viewmodels/](./ui/viewmodels/) 下 7 个 ViewModel 的 `on_update`/`on_log` 回调注入 + 命令式 View（`did_mount`/`will_unmount`/`self.update()`/`PageRefMixin`）已全部重写为 state snapshot + commands + `use_viewmodel` 目标范式。5 个历史控件（`AppLayout`/`TaskCenterView`/`FailoverConfigPanel`/`ProviderCredentialDialog`/`ResizableSplitter`）的 `PageRefMixin` 依赖已消除，`ui/v1_compat.py` 兼容桩已删除。

> **有意识简化的代码现场标记**：对有意识的简化（如已知上限的权宜之计、推迟的优化），使用 `# NOTE(lazy):` 注释标记，格式为 `# NOTE(lazy): <简化内容>. ceiling: <已知上限>. upgrade: <升级触发条件>.`。三要素必须齐全。缺少 `upgrade` 的标记视为 **no-trigger 高风险**，PR 评审时必须补充升级触发条件或拒绝合并。积累到 3 处以上或 `upgrade` 条件触发时，应升级为 [CONTRIBUTING.md](./CONTRIBUTING.md#已知架构技术债-known-technical-debt) 中的技术债表格条目。可用 `grep -rn "NOTE(lazy):"` 汇集。禁止用此标记掩盖真正的 TODO（应用 `# TODO:`）、业务逻辑简化、红线/模板/专项规范的省略。

---

## 4. 架构原则

### 4.1 分层架构

分层为 `core/` (架构核心) → `app/` (引导层) → `data/` (数据层) → `services/` (应用服务) → `strategies/` (策略层) → `ui/` (表现层)，`utils/` 为横切关注点。完整目录树见 CONTRIBUTING.md「完整目录结构」。

**依赖规则 (严格单向):**

```text
core ← data / services / strategies / utils / ui / app
data ← services / strategies / ui / app
services ← strategies / ui / app
strategies ← ui / app
utils ← 任意层可引用 (横切关注点)
app → 编排所有层，仅被 main.py 调用
```

**绝对禁止反向依赖：** `core` 导入 `data`/`services`/`ui`；`data` 导入 `ui`/`services`；`services` 导入 `ui`；`strategies` 导入 `ui`。

> **同层内文件合并原则**：在不违反分层架构的前提下，同一职责的多个小函数可合并到一个文件，不为单次使用的辅助函数创建独立模块。但跨层合并禁止（如 `data/` 与 `ui/` 不可合并）。

### 4.2 core 层隔离原则

`core/` 是架构核心层，只包含被所有层共享的基础设施 (目前仅 `i18n`)，不得依赖 `data/`、`services/`、`strategies/`、`ui/`、`utils/` 中的任何模块（`utils/` 虽标注为"任意层可引用"，但 `core/` 作为最内层不可反向导入 `utils/`，否则形成循环依赖）；如果某个模块被多层引用且产生循环依赖，应考虑提升到 `core/`；`ui/i18n.py` 是 UI 层对 `core.i18n` 的薄封装 (Flet 文本绑定)，不要直接修改 `core.i18n` 来满足 UI 需求。

### 4.3 单例模式

使用 `@register_singleton` 装饰器统一管理单例生命周期。**所有单例必须**：① 使用 `@register_singleton` 注册；② 实现 `_reset_singleton()` 方法 (测试隔离)；③ 支持参数依赖注入 (DI) 或注入可选时钟，避免难以测试的隐式全局状态依赖。完整代码模板、锁保护/`_initialized`/`_atexit_cleanup` 实现细节、注册清单（含 CacheManager/ThreadPoolManager/TaskManager/AIService/SchedulerService/DataProcessor/MarketDataService/NewsSubscriptionService/TushareClient/AkshareConceptClient/LocalModelManager/StrategyManager）、非注册单例 (`ConfigHandler`/`ProxyManager`)、非单例服务 (`BacktestService`) 见 [CONTRIBUTING.md「单例模式实现模板」](./CONTRIBUTING.md#单例模式实现模板)。

---

## 5. 按需查阅索引

详细规范、模板、工作流步骤集中承载于 [CONTRIBUTING.md](./CONTRIBUTING.md)，按需查阅：

| 主题 | CONTRIBUTING.md 锚点 |
|------|---------------------|
| Python 风格 / 类型标注 / 导入顺序 / 日志规范 | 「实现规范手册」各小节 |
| 异步编程规范 / 数据库操作规范 / 错误处理标准模式 | 对应小节 |
| V1 声明式 UI 开发规范 / i18n 状态驱动 / 响应式断点 | 对应小节 |
| 策略模式 / Polars 向量化基类 / AI 策略混入 / DAO 模式 / 数据同步 / TaskManager | 对应小节 |
| MVVM 表现层 (View / ViewModel / Component) / 配置管理 / 质量门控 / 性能监控 / 单例模式实现模板 | 对应小节 |
| 测试规范 / CI/CD 流水线与门禁 | 对应小节 |
| Git 工作流与分支策略（GitHub Flow + worktree 隔离、分支命名、原子提交、Squash Merge） | 「Git 工作流与分支策略」 |
| 常用开发与测试命令 / 交付前 DoD / 变更类型→最小验证子集 | 「常用开发与测试命令」 |
| 完整技术栈表 / 完整目录结构 / 同层合并原则 | 「AI 助手方法论与项目概览」 |
| 已知架构技术债 / Flet 0.85.3 (V1) API 约束 / 升级协同机制 | 对应小节 |
| 通用 Flet v1 开发参考（man/flet-best-practices.md） | 「通用 Flet v1 开发参考」 |
