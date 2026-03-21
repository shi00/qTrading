# 测试体系补充计划

> **创建日期**: 2026-03-21 | **目标**: 系统化补充测试覆盖，确保代码质量

---

## 一、当前测试覆盖现状

### 1.1 测试统计

| 维度 | 数量 | 评价 |
|------|------|------|
| 源代码模块 | 39 个文件 | - |
| 测试文件 | 22 个 | - |
| 测试用例 | 306 个 | - |
| 测试覆盖率 | ~40% | ⚠️ 不足 |

### 1.2 测试分布分析

| 模块 | 测试文件 | 测试用例数 | 覆盖评价 |
|------|----------|------------|----------|
| `TradeCalendarService` | test_trade_calendar_service.py | 83 | ✅ 优秀 |
| `OversoldStrategy Context` | test_oversold_context.py | 40 | ✅ 良好 |
| `DataProcessor` | test_data_processor.py | 部分 | ⚠️ 不足 |
| `CacheManager` | test_cache_manager.py | 部分 | ⚠️ 不足 |
| `Logger/LogDecorators` | test_logger.py, test_log_decorators.py | 部分 | ⚠️ 不足 |
| `ConfigHandler` | test_config_handler.py | 部分 | ⚠️ 不足 |
| `DatabaseManager` | test_database_manager.py | 部分 | ⚠️ 不足 |
| `FundamentalStrategy` | 无 | 0 | ❌ 缺失 |
| `MarketStrategy` | 无 | 0 | ❌ 缺失 |
| `TechnicalBreakoutStrategy` | 无 | 0 | ❌ 缺失 |
| `NorthboundStrategy` | 无 | 0 | ❌ 缺失 |
| `TaskManager` | 无 | 0 | ❌ 缺失 |
| `TechnicalAnalysis` | test_oversold_strategy.py | 部分 | ⚠️ 不足 |
| `QualityGate` | 无 | 0 | ❌ 缺失 |
| `RateLimiter` | 无 | 0 | ❌ 缺失 |
| `ProxyManager` | 无 | 0 | ❌ 缺失 |
| `SchedulerService` | 无 | 0 | ❌ 缺失 |
| `NewsFetcher` | test_ai_core.py | 部分 | ⚠️ 不足 |
| `ReviewManager` | test_ai_core.py | 部分 | ⚠️ 不足 |

### 1.3 核心问题

1. **测试集中在"新开发"模块** - 历史代码缺乏测试覆盖
2. **策略层测试薄弱** - 多个策略类无测试
3. **工具类测试缺失** - 限流器、代理管理器等无测试
4. **边界条件覆盖不足** - 现有测试多为"快乐路径"

---

## 二、测试补充优先级矩阵

```
┌─────────────────────────────────────────────────────────────────────────┐
│  P0 - 高优先级（核心业务逻辑，影响交易决策）                               │
├─────────────────────────────────────────────────────────────────────────┤
│  □ strategies/fundamental.py      - 价值/成长策略                        │
│  □ strategies/market.py           - 技术突破/北向资金策略                 │
│  □ utils/technical_analysis.py    - 技术指标计算（MACD/KDJ/RSI/趋势）    │
│  □ data/quality_gate.py           - 质量门控装饰器                        │
├─────────────────────────────────────────────────────────────────────────┤
│  P1 - 中优先级（基础设施，影响系统稳定性）                                 │
├─────────────────────────────────────────────────────────────────────────┤
│  □ services/task_manager.py       - 任务管理器                           │
│  □ utils/rate_limiter.py          - 限流器                               │
│  □ data/news_fetcher.py           - 新闻获取                             │
│  □ data/review_manager.py         - 复盘管理                             │
├─────────────────────────────────────────────────────────────────────────┤
│  P2 - 低优先级（辅助工具）                                                │
├─────────────────────────────────────────────────────────────────────────┤
│  □ utils/proxy_manager.py         - 代理管理                             │
│  □ utils/scheduler_service.py     - 调度服务                             │
│  □ utils/thread_pool.py           - 线程池                               │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 三、详细测试用例规划

### 3.1 P0: strategies/fundamental.py

**测试文件**: `tests/test_fundamental_strategy.py`

| 测试类 | 测试用例 | 覆盖功能 |
|--------|----------|----------|
| **TestValueStrategy** | test_value_strategy_normal | 正常价值筛选 |
| | test_value_strategy_pe_range | PE 范围过滤 |
| | test_value_strategy_pb_filter | PB 过滤 |
| | test_value_strategy_dividend_yield | 股息率过滤 |
| | test_value_strategy_empty_result | 无匹配结果 |
| | test_value_strategy_missing_columns | 缺失列处理 |
| **TestGrowthStrategy** | test_growth_strategy_normal | 正常成长筛选 |
| | test_growth_strategy_revenue_growth | 营收增长过滤 |
| | test_growth_strategy_profit_growth | 利润增长过滤 |
| | test_growth_strategy_roe_filter | ROE 过滤 |
| | test_growth_strategy_empty_result | 无匹配结果 |
| **TestDividendStrategy** | test_dividend_strategy_normal | 正常红利筛选 |
| | test_dividend_strategy_yield_range | 股息率范围 |
| | test_dividend_strategy_payout_ratio | 分红比例 |

**预计用例数**: 15 个

---

### 3.2 P0: strategies/market.py

**测试文件**: `tests/test_market_strategy.py`

| 测试类 | 测试用例 | 覆盖功能 |
|--------|----------|----------|
| **TestTechnicalBreakoutStrategy** | test_breakout_normal | 正常突破筛选 |
| | test_breakout_pct_chg_range | 涨跌幅范围 |
| | test_breakout_turnover_filter | 换手率过滤 |
| | test_breakout_empty_result | 无匹配结果 |
| **TestNorthboundStrategy** | test_northbound_normal | 正常北向筛选 |
| | test_northbound_ratio_filter | 持股比例过滤 |
| | test_northbound_missing_data | 北向数据缺失 |
| | test_northbound_exchange_filter | 交易所过滤 |
| **TestLimitUpStrategy** | test_limit_up_normal | 正常涨停筛选 |
| | test_limit_up_first_board | 首板筛选 |
| | test_limit_up_continuous | 连板筛选 |

**预计用例数**: 12 个

---

### 3.3 P0: utils/technical_analysis.py

**测试文件**: `tests/test_technical_analysis.py`

| 测试类 | 测试用例 | 覆盖功能 |
|--------|----------|----------|
| **TestQfqCalculation** | test_qfq_normal | 正常前复权计算 |
| | test_qfq_no_adj_factor | 无复权因子 |
| | test_qfq_all_factors_same | 因子全部相同 |
| | test_qfq_empty_df | 空 DataFrame |
| **TestMACD** | test_macd_golden_cross | 金叉检测 |
| | test_macd_death_cross | 死叉检测 |
| | test_macd_bullish | 多头状态 |
| | test_macd_bearish | 空头状态 |
| | test_macd_insufficient_data | 数据不足 |
| **TestKDJ** | test_kdj_overbought | 超买检测 |
| | test_kdj_oversold | 超卖检测 |
| | test_kdj_neutral | 中性状态 |
| | test_kdj_insufficient_data | 数据不足 |
| **TestRSI** | test_rsi_calculation | RSI 计算 |
| | test_rsi_overbought | 超买区 |
| | test_rsi_oversold | 超卖区 |
| | test_rsi_insufficient_data | 数据不足 |
| **TestTrendAnalysis** | test_trend_up | 上升趋势 |
| | test_trend_down | 下降趋势 |
| | test_trend_insufficient_data | 数据不足 |
| **TestRSIFeatures** | test_rsi_consecutive_oversold | 连续超卖天数 |
| | test_rsi_days_since_healthy | 距健康状态天数 |
| | test_rsi_stagnation_detection | 钝化检测 |
| | test_rsi_polars_expr | Polars 表达式 |

**预计用例数**: 24 个

---

### 3.4 P0: data/quality_gate.py

**测试文件**: `tests/test_quality_gate.py`

| 测试类 | 测试用例 | 覆盖功能 |
|--------|----------|----------|
| **TestQualityTier** | test_tier_comparison | 层级比较 |
| | test_tier_int_conversion | 整数转换 |
| **TestRequireQuality** | test_require_quality_pass | 质量达标通过 |
| | test_require_quality_fail | 质量不足拒绝 |
| | test_require_quality_async | 异步方法装饰 |
| | test_require_quality_sync | 同步方法装饰 |
| | test_require_quality_no_processor | 无处理器降级 |
| **TestQualityGateError** | test_error_message | 错误消息 |
| | test_error_inheritance | 异常继承 |

**预计用例数**: 10 个

---

### 3.5 P1: services/task_manager.py

**测试文件**: `tests/test_task_manager.py`

| 测试类 | 测试用例 | 覆盖功能 |
|--------|----------|----------|
| **TestAppTask** | test_task_creation | 任务创建 |
| | test_task_default_values | 默认值 |
| | test_task_status_transition | 状态转换 |
| **TestTaskManager** | test_singleton | 单例模式 |
| | test_submit_task | 任务提交 |
| | test_cancel_task | 任务取消 |
| | test_task_progress | 进度更新 |
| | test_task_completion | 任务完成 |
| | test_task_failure | 任务失败 |
| | test_subscriber_notification | 订阅通知 |
| | test_concurrent_tasks | 并发任务 |
| | test_terminal_status | 终态处理 |

**预计用例数**: 14 个

---

### 3.6 P1: utils/rate_limiter.py

**测试文件**: `tests/test_rate_limiter.py`

| 测试类 | 测试用例 | 覆盖功能 |
|--------|----------|----------|
| **TestRateLimiter** | test_rate_limit_pass | 限流通过 |
| | test_rate_limit_block | 限流阻塞 |
| | test_rate_limit_reset | 限流重置 |
| | test_rate_limit_concurrent | 并发限流 |
| | test_rate_limit_burst | 突发流量 |
| **TestTokenBucket** | test_token_bucket_refill | 令牌补充 |
| | test_token_bucket_consume | 令牌消耗 |
| | test_token_bucket_empty | 令牌耗尽 |

**预计用例数**: 10 个

---

### 3.7 P1: data/news_fetcher.py

**测试文件**: `tests/test_news_fetcher.py`

| 测试类 | 测试用例 | 覆盖功能 |
|--------|----------|----------|
| **TestNewsFetcher** | test_get_stock_news | 股票新闻获取 |
| | test_get_hot_concepts | 热门概念 |
| | test_get_us_major_moves | 美股动态 |
| | test_news_cache | 新闻缓存 |
| | test_news_empty_result | 空结果处理 |
| | test_news_api_failure | API 失败处理 |

**预计用例数**: 8 个

---

### 3.8 P1: data/review_manager.py

**测试文件**: `tests/test_review_manager.py`

| 测试类 | 测试用例 | 覆盖功能 |
|--------|----------|----------|
| **TestReviewManager** | test_save_review | 保存复盘 |
| | test_get_learning_context | 获取学习上下文 |
| | test_review_pagination | 分页查询 |
| | test_review_filter | 过滤查询 |
| | test_review_statistics | 统计信息 |

**预计用例数**: 6 个

---

## 四、实施计划

### 4.1 阶段一：核心策略测试（P0）

| 周次 | 任务 | 预计用例 |
|------|------|----------|
| Week 1 | test_fundamental_strategy.py | 15 |
| Week 1 | test_market_strategy.py | 12 |
| Week 2 | test_technical_analysis.py | 24 |
| Week 2 | test_quality_gate.py | 10 |

**阶段目标**: 61 个测试用例，覆盖核心交易决策逻辑

---

### 4.2 阶段二：基础设施测试（P1）

| 周次 | 任务 | 预计用例 |
|------|------|----------|
| Week 3 | test_task_manager.py | 14 |
| Week 3 | test_rate_limiter.py | 10 |
| Week 4 | test_news_fetcher.py | 8 |
| Week 4 | test_review_manager.py | 6 |

**阶段目标**: 38 个测试用例，覆盖基础设施稳定性

---

### 4.3 阶段三：辅助工具测试（P2）

| 周次 | 任务 | 预计用例 |
|------|------|----------|
| Week 5 | test_proxy_manager.py | 8 |
| Week 5 | test_scheduler_service.py | 6 |
| Week 5 | test_thread_pool.py | 8 |

**阶段目标**: 22 个测试用例，覆盖辅助工具

---

## 五、测试规范

### 5.1 测试命名规范

```python
class Test<功能模块>(unittest.TestCase):
    """测试<功能模块>"""
    
    def test_<功能点>_<场景>(self):
        """<场景描述>"""
        pass
```

### 5.2 测试结构规范

```python
def test_example(self):
    """测试描述"""
    # 1. Arrange - 准备测试数据
    input_data = ...
    
    # 2. Act - 执行被测方法
    result = method_under_test(input_data)
    
    # 3. Assert - 验证结果
    self.assertEqual(result, expected)
```

### 5.3 边界条件必测项

每个功能点应覆盖以下场景：

| 场景类型 | 示例 |
|----------|------|
| 正常输入 | 合法数据 |
| 空输入 | `None`, `[]`, `{}` |
| 边界值 | 最小值、最大值 |
| 异常输入 | 类型错误、格式错误 |
| 并发场景 | 多线程/多协程访问 |

---

## 六、质量指标

### 6.1 目标指标

| 指标 | 当前值 | 目标值 |
|------|--------|--------|
| 测试用例总数 | 306 | 427+ |
| 代码覆盖率 | ~40% | 70%+ |
| 核心模块覆盖率 | ~50% | 85%+ |
| 边界条件覆盖 | ~30% | 60%+ |

### 6.2 验收标准

- [ ] 所有新增测试用例通过
- [ ] 无测试用例跳过（除可选依赖）
- [ ] 核心模块覆盖率 ≥ 85%
- [ ] 边界条件测试覆盖 ≥ 60%

---

## 七、附录：测试模板

### 7.1 策略测试模板

```python
"""
Tests for <StrategyName> strategy.
"""

import unittest
import pandas as pd
import polars as pl

from strategies.<module> import <StrategyClass>


class Test<StrategyClass>(unittest.TestCase):
    """测试 <StrategyClass> 策略"""

    def setUp(self):
        self.strategy = <StrategyClass>()
        self.sample_data = pd.DataFrame([
            {"ts_code": "000001.SZ", "name": "测试股票", ...},
        ])

    def test_strategy_normal(self):
        """正常筛选"""
        context = {
            "screening_data": self.sample_data,
            "params": {...},
        }
        result = await self.strategy.filter(context)
        self.assertFalse(result.empty)

    def test_strategy_empty_result(self):
        """无匹配结果"""
        context = {
            "screening_data": self.sample_data,
            "params": {"extreme_param": 999},
        }
        result = await self.strategy.filter(context)
        self.assertTrue(result.empty)
```

### 7.2 工具类测试模板

```python
"""
Tests for <ToolName> utility.
"""

import unittest

from utils.<module> import <ToolClass>


class Test<ToolClass>(unittest.TestCase):
    """测试 <ToolClass> 工具类"""

    def test_normal_case(self):
        """正常输入"""
        result = <ToolClass>.method(...)
        self.assertEqual(result, expected)

    def test_empty_input(self):
        """空输入"""
        result = <ToolClass>.method(None)
        self.assertEqual(result, default_value)

    def test_edge_case(self):
        """边界条件"""
        result = <ToolClass>.method(boundary_value)
        self.assertIn(result, valid_results)
```
