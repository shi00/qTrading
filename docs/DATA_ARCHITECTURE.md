# A股量化数据子系统 - 架构设计文档 (Architecture Design Document)
> **状态**: 生产就绪 (Step 4 已完工)
> **作者**: Antigravity (资深量化工程师)
> **日期**: 2026-02-02

## 1. 系统概览 (System Overview)
本文档详细阐述了 `AStockScreener` 平台高保真数据子系统的架构设计。本系统旨在为 AI 驱动的选股模型提供**机构级**的数据底座，重点解决**数据完备性**、**时间点准确性 (Point-in-Time)** 以及**风险因子覆盖**等核心需求。

### 1.1 核心目标
*   **深度基本面覆盖**: 超越简单的 OHLCV 行情，囊括“安全因子”（如审计意见、商誉、质押率）。
*   **精准回测支持**: 原生支持前复权 (QFQ) 价格，彻底消除除权除息带来的价格断层。
*   **高吞吐同步**: 通过“个股并发循环”策略 (9路并发)，最大化利用 Tushare API 配额。
*   **容错设计**: 具备颗粒度精细的断点续传与熔断机制，实现“防崩溃”运行。

---

## 2. 数据模型设计 (Data Model Design)

持久层基于 SQLite (WAL 模式) 构建，采用星型模型 (Star Schema)，优化高并发读取性能。

### 2.1 市场微观结构 (日频/高频)
| 表名 | 描述 | 关键字段 | 用途 |
| :--- | :--- | :--- | :--- |
| `daily_quotes` | 核心日线行情 | `qfq_close`, `adj_factor`, `vol`, `amount` | 趋势分析、技术指标计算 |
| `limit_list` | 涨跌停榜 | `limit_type`, `strth` (封单强度) | 情绪分析 (游资热度追踪) |
| `suspend_d` | 停牌列表 | `suspend_type`, `suspend_timing` | **消除幸存者偏差** (过滤不可交易标的) |
| `margin_daily` | 两融数据 | `rzye` (融资余额), `rqye` (融券余额) | 情绪反转指标 |
| `index_daily` | 宽基指数 | `close`, `pe` (估值) | 市场择时 (Beta 控制) |

### 2.2 深度基本面 (季频/事件驱动)
*专为 AI 风险模型设计。*

| 表名 | 描述 | 关键字段 | 核心用例 |
| :--- | :--- | :--- | :--- |
| `financial_reports` | 合并财报 | **`goodwill` (商誉)**, **`audit_result` (审计)**, `roe` | **排雷** (商誉减值、非标审计意见) |
| `fina_forecast` | 业绩预告 | `type` (预增/减), `net_profit_min` | **事件驱动** (业绩超预期 Alpha) |
| `pledge_stat` | 股权质押 | `pledge_ratio` (质押率) | **安全因子** (流动性危机预警) |
| `repurchase` | 股份回购 | `amount`, `proc` (进度) | **信心信号** (低估值信号) |
| `dividend` | 分红历史 | `cash_div`, `ex_date` | 红利策略基础 |
| `fina_mainbz` | 主营业务 | `bz_item`, `bz_profit` | 行业精准分类对齐 |

---

## 3. 同步管道架构 (Synchronization Pipeline)

系统构建了 **"混合同步策略" (Hybrid Synchronization Strategy)**，在 API 限制（流控 vs 数据量）之间寻求最佳平衡。

### 3.1 五步初始化流程
由 `DataProcessor.initialize_system()` 统一编排：

*   **Step 1: 基础元数据 (Stock List)**
    *   *数据源*: `stock_basic`
    *   *逻辑*: 全量替换。定义约 5000 只股票的基础池 (状态 'L')。

*   **Step 2: 日历与元数据**
    *   *数据源*: `trade_cal` (近 3 年)
    *   *逻辑*: 数据对齐的基础，支持离线模式下的交易日判断。

*   **Step 3: 市场行情 (按日期批处理)**
    *   *策略*: **时间切片并发 (Time-Slicing Concurrency)**。
    *   *方法*: `sync_historical_data`
    *   *并发*: 按日批量拉取 (例如：并发抓取 2024-02-01 全市场快照)。
    *   *原因*: Tushare `daily` 接口针对“单日获取全市场”进行了优化，此步骤效率最高。

*   **Step 4: 深度基本面 (按个股循环)**
    *   *策略*: **以股票为中心的并发 (Stock-Centric Parallelism)**。
    *   *方法*: `sync_comprehensive_fundamentals`
    *   *并发控制*: `asyncio.Semaphore(5)`。
    *   *粒度*: 针对 *每只* 股票，并发发起 **9 个异步请求**：
        1.  `get_income` (利润表)
        2.  `get_balancesheet` (资产负债表)
        3.  `get_cashflow` (现金流量表)
        4.  `get_fina_indicator` (财务指标)
        5.  `get_fina_audit` (审计意见)
        6.  `get_forecast` (业绩预告)
        7.  `get_pledge_stat` (股权质押)
        8.  `get_repurchase` (回购)
        9.  `get_dividend` (分红)
    *   *原因*: 大多数基本面接口（如审计、质押）不支持高效的“全市场单日”查询。个股循环能确保 100% 的数据覆盖率。

*   **Step 5: 数据健康度检查 (Health Check)**
    *   *策略*: **完整性校验**。
    *   *方法*: `check_data_health`
    *   *逻辑*: 校验行情覆盖率与财报完整度 (>98%)，生成最终质检报告 (Green/Red)。

---

## 4. 健壮性与工程控制 (Robustness)

### 4.1 流量整形 (TokenBucket Rate Limiter)
严格遵守 Tushare 的 QPS 限制（根据用户积分动态调整）：
-   **算法**: 令牌桶 (Token Bucket) - 客户端实现。
-   **配置**: `user_settings.json` -> `tushare_api_limit` (默认: 0/不限速)。
-   **行为**: 平滑 Step 4 高并发带来的流量尖峰。

### 4.2 错误处理与弹性
-   **抖动退避 (Jittered Backoff)**: 遇到 `TcpTimeout` 或 `500` 错误时，休眠 `2^n + random()` 秒。防止 API 恢复瞬间发生“惊群效应”。
-   **熔断机制 (Circuit Breaker)**: 如果遇到权限不足 (Error 2000)，自动跳过特定数据类型（如 `margin_detail`），并记录警告，确保主流程不中断。
-   **原子检查点 (Atomic Checkpoints)**: Step 4 每完成一个批次（50 只股票）即持久化进度到 `sync_checkpoint_step4.json`。支持进程被杀后的无损断点续传。

### 4.3 数据一致性
-   **前复权计算**: `daily_quotes` 数据包含 `adj_factor` (复权因子)。
-   **去重逻辑**: 财务报表以 `(ts_code, end_date, ann_date)` 为主键。逻辑优先保留最新的 `ann_date` (公告日)，以确保捕获财报修正/重述数据。

---

## 5. 接口定义 (API Surface)

### 5.1 Python API (`DataProcessor`)
```python
async def initialize_system(progress_callback=None, cancel_event=None):
    """
    主入口函数。
    阻塞执行约 45-90 分钟以完成全量初始化。
    """
    pass
```

### 5.2 UI 集成 (`SystemTab`)
-   **触发器**: 红色 "[系统初始化]" 按钮。
-   **反馈**: 模态对话框，显示实时文本日志 + 进度条。
-   **取消**: 支持通过 `asyncio.Event` 信号传播进行优雅中断。

---

## 6. 未来扩展规划 (Roadmap)
1.  **分钟级数据**: 架构支持通过类似的 "Step 5" 逻辑添加 `min_quotes`。
2.  **因子引擎**: 基于原始 `daily_quotes` 表，利用 `pandas-ta` 预计算 Alpha 101/191 因子。
3.  **向量库同步**: 将 `financial_reports` 中的文本字段（如审计意见、经营分析）流式传输至 ChromaDB，支持 RAG 分析。

---
*设计文档结束*
*基于代码库 v2.4.0 验证*
