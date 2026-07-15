# Plans-residual-tech-debt.md — 剩余技术债清理计划

> **Worktree**: `.worktrees/chore-tech-debt-residual` | **分支**: `chore/tech-debt-residual`
> **基线**: `origin/main` (91565468, PR #170 已合并)
> **创建日期**: 2026-07-15
> **方法论**: superpowers (brainstorming → worktree → writing-plans → TDD → review)

---

## 执行结果总结（2026-07-15 完成）

| Phase | 任务 | 状态 | Commit | 说明 |
|-------|------|------|--------|------|
| 1 | R9 合规修复（strategies/ 16 处） | ✅ 完成 | 7baa898d | 16 处 `str(e)` → `DataSanitizer.sanitize_error(e)` |
| 2 | 测试顺序污染（caplog 失败） | ✅ 完成 | 8c9a7111 | 根因: `Logger.disabled` 实例属性残留。3 次随机 seed + 固定顺序全绿 |
| 3 | classify_error 接入（E-3 决策） | ✅ 完成 | — | E-3: 保持现状，NOTE(lazy) 标记已记录 upgrade 条件 |
| 4 | 红线自动化 R4/R12/R13/R14/R15 | ✅ 完成 | 6cffce50 | `scripts/check_redlines.py` + 44 单测 + pre-commit hook。R16 暂缓 |
| 5 | MAX_CONTENT_WIDTH 技术债 | ✅ 完成 | — | 删除技术债条目（用户决策：非真需求，A股量化用户主流 1080p/2K） |
| 6 | 文档同步 + PR | ✅ 完成 | 本提交 | 更新 CLAUDE.md 红线表格 + CONTRIBUTING.md 技术债表格 |

**验证结果**：ruff check + ruff format + pre-commit（11 hooks）+ pyright 全部通过；pytest 3 次随机 seed + 固定顺序全绿（8121 tests）。

---

## 整体范围与风险矩阵

| Phase | 技术债 | 工作量 | 风险 | 依赖 | 推荐执行顺序 |
|-------|--------|--------|------|------|-------------|
| 1 | **A: R9 合规修复**（strategies/ 16 处） | 小 | 低 | 无 | 1（机械替换，建立基线） |
| 2 | **B: 测试顺序污染**（42 个 caplog 失败） | 中 | 中 | 无 | 2（独立排查，不影响其他） |
| 3 | **C: MAX_CONTENT_WIDTH**（ui/app_layout.py） | 大 | 高 | 无 | 5（需详细设计 + 多 subagent 检视） |
| 4 | **D: 红线自动化**（R4/R12/R13/R14/R15/R16） | 大 | 中 | 无 | 4（逐个实现，易误报） |
| 5 | **E: classify_error 接入**（strategies 38 + utils 39 = 77 处） | 巨大 | 高 | 无 | 3（需重新评估，可能拒绝） |

**执行顺序**：1 → 2 → 5（评估） → 4 → 3

---

## Phase 1: R9 合规修复（A）— strategies/ 16 处

### 1.1 范围

16 处 `except Exception` 未调用 `DataSanitizer.sanitize_error(e)` 的位置（R9 红线：日志/异常消息直接打印明文敏感信息）：

| 文件 | 行号 | 所在方法 | 当前代码 | 改造 |
|------|------|---------|---------|------|
| ai_mixin.py | 405 | `run_ai_analysis` | `logger.warning(..., e)` | `logger.warning(..., DataSanitizer.sanitize_error(e))` |
| oversold_strategy.py | 356 | `_math_filter` | `raise RuntimeError(f"... {e}") from e` | `raise RuntimeError(f"... {DataSanitizer.sanitize_error(e)}") from e` |
| oversold_strategy.py | 399 | `_prefetch_strategy_specific` | `logger.warning(..., e)` | `logger.warning(..., DataSanitizer.sanitize_error(e))` |
| oversold_strategy.py | 407 | `_prefetch_strategy_specific` | `logger.warning(..., e)` | `logger.warning(..., DataSanitizer.sanitize_error(e))` |
| oversold_strategy.py | 464 | `_prefetch_strategy_specific` | `logger.warning(..., e)` | `logger.warning(..., DataSanitizer.sanitize_error(e))` |
| prompt_validator.py | 101 | `check_field_populous` | `logger.debug(..., e)` | `logger.debug(..., DataSanitizer.sanitize_error(e))` |
| prompt_validator.py | 109 | `check_field_populous` | `logger.debug(..., e)` | `logger.debug(..., DataSanitizer.sanitize_error(e))` |
| prompt_validator.py | 139 | `check_field_exists` | `logger.debug(..., e)` | `logger.debug(..., DataSanitizer.sanitize_error(e))` |
| prompt_validator.py | 147 | `check_field_exists` | `logger.debug(..., e)` | `logger.debug(..., DataSanitizer.sanitize_error(e))` |
| polars_base.py | 100 | `PolarsBaseStrategy.filter` | `raise RuntimeError(f"... {e}") from e` | `raise RuntimeError(f"... {DataSanitizer.sanitize_error(e)}") from e` |
| market.py | 215 | `NorthboundFlowStrategy.filter` | `logger.warning(..., e)` | `logger.warning(..., DataSanitizer.sanitize_error(e))` |
| data_provider.py | 90 | `preload_range` | `logger.error(..., e)` | `logger.error(..., DataSanitizer.sanitize_error(e))` |
| data_provider.py | 168 | `preload_range` | `logger.error(..., e)` | `logger.error(..., DataSanitizer.sanitize_error(e))` |
| data_provider.py | 338 | `_get_screening_data` | `logger.warning(..., e)` | `logger.warning(..., DataSanitizer.sanitize_error(e))` |
| data_provider.py | 359 | `_get_fundamental_screening_data` | `logger.warning(..., e)` | `logger.warning(..., DataSanitizer.sanitize_error(e))` |
| data_provider.py | 385 | `get_stock_meta` | `logger.warning(..., e)` | `logger.warning(..., DataSanitizer.sanitize_error(e))` |

### 1.2 改造原则

- **仅替换日志/异常消息中的 `e` 为 `DataSanitizer.sanitize_error(e)`**
- 不改变控制流（不添加/删除 raise/return）
- 不改变日志级别
- 不引入 classify_error（Phase 5 评估为 YAGNI）

### 1.3 DoD

- 16 处全部替换
- `ruff check .` 通过
- `pytest tests/unit/strategies/ -v` 通过（现有测试不回归）
- 新增守护测试：验证关键位置调用 sanitize_error（至少 3 处抽样）

---

## Phase 2: 测试顺序污染（B）— 42 个 caplog 失败

### 2.1 现象

- `pytest tests/unit/ -p no:randomly`：全绿
- `pytest tests/unit/`（默认随机顺序）：42 个失败，全是 `caplog.records` 空断言
- 失败分布：test_sw_industry_sync.py / test_singleton_registry.py / test_database_tab.py / test_loop_local.py 等

### 2.2 根因假设

日志 handler 配置跨测试泄漏：
- 某些测试修改全局日志配置（如 `setup_logging()` 添加 handler），未在 teardown 清理
- 后续测试的 `caplog` fixture 无法捕获日志（因为日志被泄漏的 handler 拦截）

### 2.3 排查计划

1. 用 `pytest --pdb` 在第一个失败处停下，检查 `logging.getLogger().handlers`
2. 用 `pytest-timestamp` 或 `pytest-repeat` 确定最小复现顺序
3. 排查 `conftest.py` 是否有 autouse fixture 清理日志 handler
4. 修复：添加 autouse fixture 在每个测试前后清理日志 handler

### 2.4 DoD

- `pytest tests/unit/ -v`（默认随机顺序）全绿
- `pytest tests/unit/ -p no:randomly` 仍全绿（不回归）
- 新增 autouse fixture 有单元测试守护

---

## Phase 3: classify_error 接入（E）— 重新评估

### 3.1 冲突分析

**CLAUDE.md §3.2 强制要求**：`except Exception` 必须走 `classify_error + classify_severity`

**Phase 6.1 评估结论**：0/38 处适合走 classify_error（均无业务消费方，属基础设施兜底或合理降级）

**77 处改造风险**：

| 类别 | 数量 | 接入 classify_error 后的行为变化 | 风险 |
|------|------|-------------------------------|------|
| **system severity → raise** | 估算 15+ 处 | 当前兜底不 raise，接入后 system severity 会 raise，导致系统崩溃 | **高** |
| **recoverable → warning** | 估算 30+ 处 | 当前已 warning，行为不变 | 低 |
| **operational → error** | 估算 20+ 处 | 当前 warning，接入后 error，日志级别升高 | 中 |

**关键问题**：utils/ 的 keyring fallback / 文件 IO / 配置管理整体兜底，如果 classify_severity 返回 "system"（如 PermissionError），当前是兜底不 raise，接入后会 raise，**改变兜底行为，可能引入新 bug**。

### 3.2 方案选择

| 方案 | 描述 | 风险 | 推荐 |
|------|------|------|------|
| **E-1: 全量接入** | 77 处全部走 classify_error + classify_severity | 高（system → raise 改变行为） | ❌ 不推荐 |
| **E-2: 仅 strategies/ 接入** | strategies/ 38 处接入，utils/ 39 处保持 NOTE(lazy) | 中（strategies 也有 fail_fast 位置） | ⚠️ 谨慎 |
| **E-3: 保持现状** | 77 处保持 NOTE(lazy)，upgrade 条件未满足 | 无 | ✅ 推荐 |
| **E-4: 仅接入有业务消费方的位置** | 77 处中筛选有业务消费方的位置接入 | 低 | ✅ 推荐（但 Phase 6.1 评估为 0 处） |

### 3.3 推荐：E-3 + E-4

- **E-3**：保持现状，NOTE(lazy) 标记已记录 upgrade 条件
- **E-4**：如果 Phase 6.1 评估有遗漏（0 处可能不准确），重新筛选有业务消费方的位置

**理由**：
1. Phase 6.1 评估为 YAGNI（0/38 调用，upgrade 未兑现）
2. 77 处改造不是机械替换，需逐处评估行为变化
3. NOTE(lazy) 的 upgrade 条件是"策略层/utils 层异常处理统一改造时"，说明项目认可暂时不走 classify_error
4. 强行接入会改变行为（system severity → raise），可能引入新 bug，违反 §1.4 "不做无益重构"

---

## Phase 4: 红线自动化（D）— R4/R12/R13/R14/R15/R16

### 4.1 范围

| 红线 | 描述 | 自动化方案 | 难度 | 误报风险 |
|------|------|----------|------|---------|
| R4 | SQL 注入（`%s` 占位符） | 正则扫描 `asyncpg` 原生查询中的 `%s` | 低 | 低 |
| R12 | 数据表未注册（`TABLE_DEFINITIONS`） | 对比 `models.py` 与 `data_dictionary.py` | 中 | 低 |
| R13 | DAO 未注册（`CacheManager.__init__`） | 对比 `daos/` 目录与 `CacheManager.__init__` | 中 | 中 |
| R14 | 策略未注册（`@register_strategy`） | 扫描 `strategies/` 中未装饰的策略类 | 中 | 低 |
| R15 | 单例未注册（`@register_singleton`） | 扫描 `_instance` 类属性的单例类 | 中 | 中 |
| R16 | UI 阻塞主循环 | AST 扫描 `ui/` 事件处理器中的同步阻塞调用 | 高 | 高 |

### 4.2 实现策略

- 扩展 `scripts/check_docs_consistency.py` 或新建 `scripts/check_redlines.py`
- 每个检查独立函数，可单独启用/禁用
- 接入 `.pre-commit-config.yaml` 的 `redline-check` hook

### 4.3 DoD

- R4/R12/R13/R14/R15 检查实现（R16 暂缓，误报风险高）
- 每个检查有单元测试
- `pre-commit run redline-check --all-files` 通过

---

## Phase 5: MAX_CONTENT_WIDTH（C）— 详细设计 + 多 subagent 检视

### 5.1 现状

- `ui/app_layout.py` 未实现居中容器（`body_wrapper`）与 `MAX_CONTENT_WIDTH` 宽度逻辑
- Flet 0.85.3 `Container` 不支持 `max_width` 属性（需返工重设计）
- 需求存疑：A股量化用户主流 1080p/2K，4K 居中是否真需求？

### 5.2 执行流程（用户要求）

1. **设计文档**：详细方案设计（含 Flet 0.85.3 兼容方案、响应式断点、测试策略）
2. **多 subagent 检视**：启动 3 个 subagent 从不同角度检视设计文档
   - 架构合规性检视（CLAUDE.md §4 分层、MVVM、声明式 UI）
   - Flet 0.85.3 API 兼容性检视
   - 用户体验/场景完整性检视
3. **实施**：按确认的设计文档实施
4. **多 subagent 代码检视**：启动 3 个 subagent 从不同角度检视实现代码
5. **测试回归**：`pytest tests/unit/ui/ -v` + 手动验证 1080p/2K/4K 场景

### 5.3 DoD

- 设计文档通过 3 个 subagent 检视
- 实现通过 3 个 subagent 代码检视
- `pytest tests/unit/ui/ -v` 通过
- `ruff check .` + `pyright` 通过

---

## 待用户确认

1. **Phase 3 (E)**：推荐 E-3 + E-4（保持现状 + 重新筛选），是否同意？还是强制 E-1（全量接入，风险高）？
2. **Phase 4 (D)**：R16 是否暂缓？（误报风险高，实现难度最大）
3. **Phase 5 (C)**：4K 居中是否真需求？还是仅支持 1080p/2K？
