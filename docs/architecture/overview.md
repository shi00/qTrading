# 架构原则总览（架构正文唯一正本）

> **角色与来源**：本文件为架构正文（分层架构 / 依赖规则 / core 层隔离 / 单例模式）的**唯一正本**，自 CLAUDE.md §4 完整迁入（ruleset 1.10.0，文档体系检视 D6 / ADR-0018 §4.1「架构正文 → architecture/overview.md」）。CLAUDE.md §4 仅保留与本文语义一致的最小摘要 + 指针；本文与摘要冲突时以本文为准。
> **执行配置优先**：import-linter 配置（`pyproject.toml` 3 条契约，pre-commit 强制）仍是架构边界的实际执行配置；本文是语义正本与守护范围说明，不替代配置本身。
> **维护流程**：修改本文按 [docs/guides/documentation-maintenance.md](../guides/documentation-maintenance.md) 的六类变更协议执行；一致性门禁 `python scripts/check_docs_consistency.py --check --scope all`。

## 4.1 分层架构

分层自外向内为 `app/` (引导层) → `ui/` (表现层) → `strategies/` (策略层) → `services/` (应用服务) → `data/` (数据层) → `core/` (架构核心)，`utils/` 为横切关注点。完整目录树见 CONTRIBUTING.md「完整目录结构」。

**依赖规则 (严格单向):**

```text
禁止导入方向（X 不得导入 Y）：
  core       ✗→ data / services / strategies / ui / utils / app
  data       ✗→ services / strategies / ui / app
  services   ✗→ strategies / ui / app
  strategies ✗→ ui / app
  utils      ✗→ data / services / strategies / ui / app   （横切叶子：可被任意层引用，但不引用任何业务层）
  ui         ✗→ app
  app        →  可编排所有层，仅被 main.py 调用
```

**绝对禁止反向依赖：** `core` 导入 `data`/`services`/`strategies`/`ui`/`utils`/`app` 中的任何模块；`data` 导入 `ui`/`services`/`strategies`；`services` 导入 `ui`；`strategies` 导入 `ui`。

**架构守护范围（P2-03）：**
- **import-linter（pre-commit）** 守护 R1 表内方向。由 1 条 `layers` 契约（层序从高到低：`app → ui → strategies → services → data → core`）一次覆盖全部层级禁止方向：`core` 禁入 `data/services/strategies/ui/app`；`data` 禁入 `services/strategies/ui/app`；`services` 禁入 `strategies/ui/app`；`strategies` 禁入 `ui/app`；`ui` 禁入 `app`（`app` 为最高编排层、可依赖所有层、仅被 main.py 调用）。`utils` 无法纳入单向线性层序，故另由 2 条 `forbidden` 契约守护：`utils`（横切叶子）禁入 `data/services/strategies/ui/app`（"R1: utils must not import business layers"，含函数体内 import，见下）；`core` 禁入 `utils`（"R1: core must not import utils"）。合计 3 条契约。
- **AST 静态测试（`tests/unit/test_architecture_boundaries.py`）** 已随 E4 退役——所有方向被上述 3 条 import-linter 契约覆盖（layers 契约含函数体内 import，语义不减）；文件仅保留例外注册表路径存在性校验（详见测试内 docstring）。<del>在 import-linter 基础上额外守护：`data/services/strategies` 禁入 `app`；`ui` 禁入 `app`；`utils` 禁入 `ui/strategies/services/app/data`（模块级 import 视角，与 "R1: utils must not import business layers" 互补）。</del>
- **豁免范围**：`if TYPE_CHECKING:` 块内导入（仅类型检查，非运行时依赖）被 import-linter 豁免（`exclude_type_checking_imports = true`）；函数体内 lazy import **import-linter layers/forbidden 契约仍会分析**——涉及 `utils` 层的函数内跨层 import 须带 `# lazy-import: <原因>` 注释并经契约级 ignore_imports 白名单登记（见 `pyproject.toml` 中 "R1: utils must not import business layers" 契约注释）。
- **例外唯一注册入口**：架构边界例外统一登记于 [docs/governance/exceptions.yml](../governance/exceptions.yml)（rule_id=R1），测试仅从注册表读取，不各自维护。

> **同层内文件合并原则**：在不违反分层架构的前提下，同一职责的多个小函数可合并到一个文件，不为单次使用的辅助函数创建独立模块。但跨层合并禁止（如 `data/` 与 `ui/` 不可合并）。

> **循环依赖治理现状（review01-A6）**：两条 MAJOR 结构性循环已通过依赖注入消除，不再靠延迟 import 压制——
> - `CacheManager ↔ BaseDao`：已由中立模块 `data/persistence/engine_provider.py` 切断（review03-C11），`BaseDao._check_engine` 经 `engine_provider.is_disposed()` 查询引擎状态，不再反向查询 `CacheManager._instance`。
> - `utils → strategies → services → utils`：已由夜间预测编排下沉到 `services/scheduled_jobs/nightly_prediction.py` 消除（review01-A2），SchedulerService 经 `register_job` 依赖注入（app 层注入 `AISelectionRunner`），不再感知具体策略类。
> 剩余 `ConfigHandler ↔ DatabaseConfigService` 为合法循环打破（函数内 lazy import 带 `# lazy-import:` 注释 + "R1: utils must not import business layers" 白名单登记）；`TaskManager → CacheManager` 实为合法单向依赖（services → data），非循环。

## 4.2 core 层隔离原则

`core/` 是架构核心层，只包含被所有层共享的基础设施 (目前含 `errors`、`i18n`、`model_management_types`、`prompt_base` 与 `startup_types`)，不得依赖 `data/`、`services/`、`strategies/`、`ui/`、`utils/` 中的任何模块（`utils/` 虽标注为"任意层可引用"，但 `core/` 作为最内层不可反向导入 `utils/`，否则形成循环依赖）；如果某个模块被多层引用且产生循环依赖，应考虑提升到 `core/`（`model_management_types` 即按此原则自 `services/ai_service/` 下沉，见 ADR-0014）；`ui/i18n.py` 是 UI 层对 `core.i18n` 的薄封装 (Flet 文本绑定)，不要直接修改 `core.i18n` 来满足 UI 需求。

> 上句「目前含 …」的 core/ 模块清单由 `scripts/check_docs_consistency.py::check_core_modules_completeness()`（GDR-11）对本文断言：实际 `core/*.py` 模块集合与本清单双向一致，漏声明/幽灵声明均 fail-closed（ruleset 1.10.0 起检查对象自 CLAUDE.md §4.2 迁至本文）。

## 4.3 单例模式

使用 `@register_singleton` 装饰器统一管理单例生命周期。**所有单例必须**：① 使用 `@register_singleton` 注册；② 实现 `_reset_singleton()` 方法 (测试隔离)；③ 支持参数依赖注入 (DI) 或注入可选时钟，避免难以测试的隐式全局状态依赖。完整代码模板、锁保护/`_initialized`/`_atexit_cleanup` 实现细节、注册清单（含注册/非注册单例、非单例服务）见 [docs/architecture/singleton-lifecycle.md](./singleton-lifecycle.md)（完整注册清单的唯一正本，新增单例只更新该文件）。

## 完成判定

- 最小验证命令：`python scripts/check_docs_consistency.py --check --scope all`（含 GDR-11 core 模块清单一致性、架构正文锚点/链接死链门禁）+ `pre-commit run --all-files`（import-linter 3 条契约实际执行架构边界）。
- [ ] 本文与 `pyproject.toml` import-linter 契约、CLAUDE.md §4 摘要三方语义一致；
- [ ] 新增/调整分层规则时已同步 R1 红线描述（redlines.yml）与 exceptions.yml 例外登记；
- [ ] core/ 模块增删时已同步 §4.2「目前含 …」清单（GDR-11 门禁通过）。
