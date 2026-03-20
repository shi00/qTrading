# AStockScreener 代码检视报告

> 检视日期：2026-03-19
> 检视依据：`code_review_guidelines.md` 8 大防区、59 条检查项
> 检视范围：全项目代码

---

## 一、检视概况

本次检视按照 `code_review_plan.md` 规划的 9 个阶段执行，覆盖策略引擎、数据访问层、前端工程、AI 集成、系统韧性、代码质量、日期时间类型一致性及静态代码检查工具等方面。

### 统计汇总

| 优先级 | 问题数量 |
|--------|----------|
| 关键 (P0) | 3 |
| 高优先级 (P1) | 8 |
| 中优先级 (P2) | 5 |
| 低优先级 (P3) | 2 |
| **总计** | **18** |

---

## 二、问题清单

### 2.1 关键问题 (P0)

| 序号 | 文件 | 行号 | 问题描述 | 建议修复方案 |
|------|------|------|----------|--------------|
| 1 | `data/daos/base_dao.py` | 215 | 使用 `datetime.now()` 而非 `get_now()`，与时区一致性原则冲突 | 替换为 `get_now().replace(tzinfo=None)` |
| 2 | `data/data_quality.py` | 50-61 | `set(date_series.dt.date)` 与 `set(trade_cal["cal_date"])` 类型可能不一致，导致差集计算失效 | 确保 `trade_cal["cal_date"]` 已转换为 `date` 类型 |
| 3 | `strategies/market.py` | 47-199 | 4 个策略类 (`TechnicalBreakoutStrategy`, `NorthboundStrategy`, `InstitutionalStrategy`, `BlockTradeStrategy`) 继承 `PolarsBaseStrategy`，但未独立添加 `@require_quality` 装饰器 | 虽然父类已有装饰器，但建议每个策略显式声明质量门控要求 |

### 2.2 高优先级问题 (P1)

| 序号 | 文件 | 行号 | 问题描述 | 建议修复方案 |
|------|------|------|----------|--------------|
| 4 | `data/sync_strategies/macro.py` | 85-87 | 直接使用 `strptime` 解析字符串，未使用统一的 `parse_date()` 函数 | 改用 `utils/time_utils.parse_date()` |
| 5 | `data/mixins/calendar_mixin.py` | 101, 170 | 直接使用 `strptime` 解析日期字符串，未使用 `parse_date()` | 改用 `parse_date()` 保持一致性 |
| 6 | `data/data_processor.py` | 311 | 使用 `strptime` 解析时间字符串，未使用统一工具函数 | 改用 `parse_date()` 或 `get_now()` |
| 7 | `strategies/oversold_strategy.py` | 142 | 直接使用 `strptime` 解析日期，存在类型安全风险 | 使用 `parse_date()` 并添加类型检查 |
| 8 | `data/offline_calendar.py` | 38 | 使用 `strptime` 解析日期对象，可能导致 TypeError | 添加类型检查或使用 `parse_date()` |
| 9 | `tests/test_history_horizon.py` | 20, 24-25 | 测试代码使用 `datetime.datetime.now()` 而非 `get_now()` | 测试代码也应遵循时区一致性原则 |
| 10 | `tests/test_data_processor.py` | 353-354, 738, 966 | 测试代码多处使用 `datetime.now()` | 统一使用 `get_now()` |
| 11 | `tests/test_ai_core.py` | 30 | 测试代码使用 `datetime.now()` | 使用 `get_now()` |

### 2.3 中优先级问题 (P2)

| 序号 | 文件 | 行号 | 问题描述 | 建议修复方案 |
|------|------|------|----------|--------------|
| 12 | 项目根目录 | - | 缺少 `pyproject.toml` 配置文件，无法配置 Ruff/MyPy 等静态检查工具 | 创建 `pyproject.toml` 并配置 `[tool.ruff]` 和 `[tool.mypy]` |
| 13 | 项目根目录 | - | 缺少 `.pre-commit-config.yaml`，无法在提交前自动执行代码检查 | 创建 pre-commit 配置，集成 Ruff 和 MyPy |
| 14 | `requirements.txt` | - | 缺少 `ruff` 和 `mypy` 开发依赖 | 添加 `ruff>=0.1.0` 和 `mypy>=1.0.0` 到 requirements.txt 或单独的 dev-requirements.txt |
| 15 | `data/daos/stock_dao.py` | 155, 168 | 使用 `DELETE FROM` 全量删除后插入，存在性能风险 | 对于小表（如 concepts）可接受，大表应考虑增量更新 |
| 16 | `data/daos/stock_dao.py` | 13-50 | DAO 方法缺少类型注解 | 添加参数和返回值类型注解，如 `async def save_stock_basic(self, df: pd.DataFrame, priority: Optional[int] = None) -> int:` |

### 2.4 低优先级问题 (P3)

| 序号 | 文件 | 行号 | 问题描述 | 建议修复方案 |
|------|------|------|----------|--------------|
| 17 | `strategies/ai_mixin.py` | 270, 434, 479-629 | 多处魔术数字（如 `0.7`, `1.3`, `1.5`, `5`, `60`）未提取为常量 | 提取为类常量或配置项 |
| 18 | `data/sync_strategies/financial.py` | 57-730 | `_run_full_sync` 方法超过 100 行，职责过多 | 考虑拆分为多个私有方法 |

---

## 三、各防区详细分析

### 防区一：策略引擎与数据流

**检查结果：基本合格**

- ✅ `PolarsBaseStrategy` 基类已添加 `@require_quality(QualityTier.BRONZE)` 装饰器
- ✅ `AISelectionStrategy` 和 `OversoldStrategy` 已独立添加 `@require_quality` 装饰器
- ⚠️ `market.py` 中的 4 个策略类依赖父类装饰器，建议显式声明
- ✅ 策略注册机制完善，`all_strategies.py` 自动发现
- ✅ 未发现未来函数穿越风险（未使用 `end_date` 进行财报数据关联）
- ✅ Join 操作使用 `ts_code` 单键，未发现笛卡尔爆破风险

### 防区二：本地存储与字典抽象

**检查结果：良好**

- ✅ DAO 层统一使用 `_save_upsert` 实现 UPSERT 语义
- ✅ `base_dao.py` 正确处理日期类型转换
- ✅ 索引定义完整（`daily_quotes`, `daily_indicators`, `moneyflow_daily`, `financial_reports` 等）
- ✅ 数据字典 `data_dictionary.py` 定义完整
- ⚠️ `stock_dao.py` 中 `overwrite_concepts` 使用 DELETE + INSERT 模式，对于小表可接受
- ✅ 维护锁机制完善，`_maintenance_event` 正确触发

### 防区三：Flet 大前端工程

**检查结果：良好**

- ✅ PubSub 订阅/取消订阅配对正确（`data_view.py`, `home_view.py` 等）
- ✅ 异步重绘前检查 `if self.page:` 安全校验
- ✅ 使用 `page.run_task()` 委托异步操作，避免主线程阻塞
- ✅ 大数据量表格使用 `VirtualTable` 组件
- ✅ 未发现硬编码中文文本（使用 I18n 机制）

### 防区四：AI 混合调用边界

**检查结果：良好**

- ✅ AI 调用前有候选数量限制（`self.limit` 来自配置）
- ✅ JSON 解析有完善的容错机制（多层 fallback）
- ✅ Prompt 注入防范：使用 `<stock_info>`, `<news>` 等 XML 标签分隔
- ✅ 文件名安全处理：`re.sub(r'[<>:"/\\|?*]', "_", ...)`

### 防区五：全局系统韧性

**检查结果：良好**

- ✅ 单例模式正确实现（`_initialized` 标志）
- ✅ 优雅关闭机制完善（`cleanup_resources`）
- ✅ 配置文件原子写入（`_save_json_atomically`）
- ✅ 网络请求重试机制（指数退避）
- ✅ Token 不在日志中明文打印（已脱敏）
- ✅ 并发安全：使用 `threading.Lock` 和 `asyncio.Lock`
- ✅ 时区一致性：主要代码使用 `get_now()`

### 防区六：代码坏味道

**检查结果：基本合格**

- ✅ 未发现 `except: pass` 安静吞咽异常
- ✅ UI 层未发现 DataFrame 操作泄漏
- ⚠️ `ai_mixin.py` 存在部分魔术数字
- ⚠️ `financial.py` 的 `_run_full_sync` 方法较长

### 防区七：日期时间类型一致性

**检查结果：需改进**

- ⚠️ 多处直接使用 `strptime` 而非统一的 `parse_date()`
- ⚠️ `base_dao.py` 使用 `datetime.now()` 而非 `get_now()`
- ⚠️ `data_quality.py` 存在 `set[date]` 与 `set[unknown]` 差集风险
- ✅ DAO 层写入前正确转换日期类型

### 防区八：静态代码检查工具

**检查结果：缺失**

- ❌ 缺少 `pyproject.toml` 配置文件
- ❌ 缺少 `.pre-commit-config.yaml` 钩子配置
- ❌ `requirements.txt` 缺少 `ruff` 和 `mypy` 依赖
- ⚠️ DAO 方法缺少类型注解

---

## 四、改进建议

### 4.1 立即修复 (P0)

1. **时区一致性**：将 `data/daos/base_dao.py:215` 的 `datetime.now()` 替换为 `get_now().replace(tzinfo=None)`
2. **类型安全**：修复 `data/data_quality.py` 中的集合类型不一致问题
3. **策略质量门控**：为 `market.py` 中的策略类添加显式 `@require_quality` 装饰器

### 4.2 短期改进 (P1)

1. **统一日期解析**：将所有 `strptime` 调用替换为 `parse_date()`
2. **测试代码规范化**：测试代码也应使用 `get_now()` 保持一致性

### 4.3 中期改进 (P2)

1. **静态检查工具配置**：
   - 创建 `pyproject.toml` 配置 Ruff 和 MyPy
   - 创建 `.pre-commit-config.yaml` 钩子
   - 添加开发依赖

2. **类型注解完善**：为 DAO 层方法添加完整的类型注解

### 4.4 长期优化 (P3)

1. **代码重构**：
   - 提取 `ai_mixin.py` 中的魔术数字为常量
   - 拆分 `financial.py` 的大方法

---

## 五、附录

### 5.1 检视覆盖文件清单

| 目录 | 文件数 | 检视状态 |
|------|--------|----------|
| `strategies/` | 9 | ✅ 完成 |
| `data/daos/` | 9 | ✅ 完成 |
| `data/sync_strategies/` | 5 | ✅ 完成 |
| `data/mixins/` | 3 | ✅ 完成 |
| `services/` | 4 | ✅ 完成 |
| `ui/views/` | 8 | ✅ 完成 |
| `ui/views/settings_tabs/` | 4 | ✅ 完成 |
| `utils/` | 8 | ✅ 完成 |
| `tests/` | 多个 | ✅ 完成 |

### 5.2 参考文档

- [architecture_principles.md](architecture_principles.md) - 架构设计原则
- [code_review_guidelines.md](code_review_guidelines.md) - 代码检视指南
- [code_review_plan.md](code_review_plan.md) - 代码检视计划

---

**检视人**：AI Code Reviewer  
**检视日期**：2026-03-19
