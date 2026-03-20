# 超跌反弹策略 LLM 上下文深度分析

## 一、上下文数据流向全景图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    OversoldStrategy.filter()                                 │
│                              ↓                                               │
│                    run_ai_analysis(candidates_df, context)                   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│                    AIStrategyMixin.run_ai_analysis()                         │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │ 预取阶段 (Pre-fetch) - 批量获取所有候选股票的共享数据                      ││
│  │  ├─ history_context    → ReviewManager.get_learning_context()           ││
│  │  ├─ global_context     → NewsFetcher.get_us_major_moves()               ││
│  │  ├─ concepts_map       → dp.cache.get_concepts(all_ts_codes)            ││
│  │  ├─ prefetched_history → dp.cache.get_daily_quotes(all_ts_codes)        ││
│  │  ├─ moneyflow_df       → dp.cache.get_moneyflow(trade_date)             ││
│  │  ├─ top_list_df        → dp.cache.get_top_list(trade_date)              ││
│  │  └─ northbound_df      → dp.cache.get_northbound(trade_date)            ││
│  └─────────────────────────────────────────────────────────────────────────┘│
│                              ↓                                               │
│                    循环处理每只股票 (for row in candidates_df)                │
│                    └─ _mixin_analyze_single(row, ...)                        │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│                    _mixin_analyze_single()                                   │
│  构建以下上下文块:                                                            │
│  ├─ stock_info          ← row (筛选数据) + concepts                          │
│  ├─ tech_context        ← MACD, KDJ, MA Alignment, Volume Trend             │
│  ├─ news                ← NewsFetcher.get_stock_news()                      │
│  ├─ strategy_ctx        ← self.get_ai_context(row) [策略特定]                │
│  ├─ capital_flow_text   ← 主力资金 + 龙虎榜 + 北向资金                        │
│  ├─ financials_text     ← PE, PB, ROE, 毛利率, 负债率...                     │
│  └─ history_text        ← 历史行情特征提取                                   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│                    AIService.analyze_stock()                                 │
│  组装最终 Prompt:                                                            │
│  ├─ <stock_info>         ← 股票基本信息                                      │
│  ├─ <technical_indicators> ← 技术指标                                        │
│  ├─ <recent_news>        ← 最近新闻                                          │
│  ├─ <global_context>     ← 全球市场动态                                      │
│  ├─ <strategy_context>   ← 策略特定上下文                                    │
│  ├─ <recent_price_action>← 历史行情特征                                      │
│  ├─ <capital_flow>       ← 资金流向                                          │
│  └─ <financials>         ← 财务指标                                          │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 二、各上下文块详细分析

### 1. stock_info - 股票基本信息

**来源**: `OversoldStrategy.filter()` 的筛选结果 `candidates_df`

**数据字段** (来自 `screening_data`):

| 字段 | 说明 | 来源 |
|------|------|------|
| ts_code | 股票代码 | Tushare daily_basic |
| name | 股票名称 | Tushare stock_basic |
| close | 收盘价 | Tushare daily |
| pct_chg | 涨跌幅 | Tushare daily |
| pe_ttm | 市盈率TTM | Tushare daily_basic |
| pb | 市净率 | Tushare daily_basic |
| total_mv | 总市值 | Tushare daily_basic |
| rsi_N | RSI值 (动态) | 策略计算 |

**代码路径**:
```
snapshot_df (来自 context["screening_data"])
    ↓ pd.merge with rsi_pdf
candidates_df
    ↓ row._asdict()
stock_info
```

---

### 2. technical_indicators - 技术指标

**来源**: `AIStrategyMixin._mixin_analyze_single()` 计算

**计算逻辑** (ai_mixin.py:460-475):

```python
# MACD 信号
trend_signal, _, _ = TechnicalAnalysis.get_macd(history_df)

# KDJ 信号
kdj_signal, k, d, j = TechnicalAnalysis.get_kdj(history_df)

# MA 均线排列 + 成交量趋势
tech_structure = self._compute_technical_structure(history_df)
```

**输出字段**:

| 字段 | 说明 | 示例值 |
|------|------|--------|
| macd_signal | MACD趋势信号 | "Bullish" / "Bearish" |
| kdj_signal | KDJ信号 | "Overbought" / "Oversold" |
| k, j | K、J值 | k=25.3, j=18.7 |
| ma_alignment | 均线排列 | "Bullish (MA5=10.2 > MA10=9.8 > MA20=9.5)" |
| volume_trend | 成交量趋势 | "Expanding (5d/10d ratio: 1.52)" |
| price_trend_5d | 5日价格趋势 | "+3.2% over 5 days" |
| price_vs_ma20 | 价格偏离MA20 | "-5.3% from MA20" |

**数据来源**: 
- `history_df` ← `dp.cache.get_daily_quotes()` ← `daily_quotes` 表

---

### 3. recent_news - 最近新闻

**来源**: `NewsFetcher.get_stock_news()`

**代码路径** (ai_mixin.py:165-175):

```python
# 后台异步获取新闻 (N+1 优化)
async def bg_fetch_news(code):
    async with news_sem:
        return await NewsFetcher.get_stock_news(code, limit=5)

news_tasks = {code: asyncio.create_task(bg_fetch_news(code)) for code in all_ts_codes}
```

**输出格式**:
```
- [财联社] 2026-03-20 某某股票发布业绩预告
- [同花顺] 2026-03-19 某某股票获得机构调研
```

**数据来源**: 
- 新闻 API (如财联社、同花顺等)
- 或本地 `market_news` 表

---

### 4. global_context - 全球市场动态

**来源**: `NewsFetcher.get_us_major_moves()`

**代码路径** (ai_mixin.py:115-118):

```python
global_context = ""
try:
    global_context = await NewsFetcher.get_us_major_moves()
except Exception as e:
    logger.warning(f"[AIStrategyMixin] Failed to fetch global context: {e}")
```

**内容**: 美股主要指数走势、重要财经新闻

---

### 5. strategy_context - 策略特定上下文

**来源**: `OversoldStrategy.get_ai_context()` (策略重写)

**代码** (oversold_strategy.py:70-82):

```python
def get_ai_context(self, row: dict) -> str:
    period = row.get("_rsi_period", 14)
    rsi = row.get(f"rsi_{period}", "N/A")
    threshold = row.get("_rsi_threshold", 30)
    return (
        f"This stock was selected by the RSI Oversold Rebound strategy. "
        f"Its current RSI({period}) = {rsi} (threshold < {threshold}), "
        f"indicating extreme oversold conditions. "
        f"Please evaluate: is this a 'golden pit' rebound opportunity "
        f"or a fundamental deterioration causing a bottomless decline?"
    )
```

**作用**: 告诉 LLM **为什么**这只股票被选中，避免"上下文真空"

---

### 6. capital_flow - 资金流向

**来源**: `AIStrategyMixin._build_capital_flow_text()` 批量预取

**预取代码** (ai_mixin.py:180-205):

```python
# 批量获取当日资金数据 (O(1) 查询)
if trade_date:
    moneyflow_df = await dp.cache.get_moneyflow(trade_date=trade_date)
    top_list_df = await dp.cache.get_top_list(trade_date=trade_date)
    northbound_df = await dp.cache.get_northbound(trade_date=trade_date)
```

**构建逻辑** (ai_mixin.py:684-745):

```python
def _build_capital_flow_text(ts_code: str, prefetched: dict) -> str:
    # 1. 主力资金 (大单+超大单净流入)
    net_main = (buy_lg + buy_elg) - (sell_lg + sell_elg)
    
    # 2. 龙虎榜 (是否上榜、原因、净买入)
    if stock_tl not empty:
        reason = row.get("reason")
        net_amt = row.get("net_amount")
    
    # 3. 北向资金 (持股量、占流通股比例)
    vol = row.get("vol")
    ratio = row.get("ratio")
```

**输出示例**:
```
主力净流入: 1234.56万元 (大单+超大单)
全市场净流入: 5678.90万元
龙虎榜: 是 (原因: 涨停, 净买入: 890.12万元)
北向持股: 12345678股, 占流通股比例: 2.34%
```

**数据来源**:

| 数据 | 表/API |
|------|--------|
| moneyflow | Tushare `moneyflow` / `moneyflow_hsgt` |
| top_list | Tushare `top_list` |
| northbound | Tushare `hk_hold` |

---

### 7. financials - 财务指标

**来源**: `AIStrategyMixin._build_financials_text()` 从 `row` 提取

**代码** (ai_mixin.py:747-786):

```python
def _build_financials_text(row: dict) -> str:
    parts.append(f"PE(TTM): {fmt(row.get('pe_ttm'))}")
    parts.append(f"PB: {fmt(row.get('pb'))}")
    parts.append(f"ROE: {fmt(row.get('roe'), '%')}")
    parts.append(f"毛利率: {fmt(row.get('grossprofit_margin'), '%')}")
    parts.append(f"资产负债率: {fmt(row.get('debt_to_assets'), '%')}")
    parts.append(f"营收同比增长: {fmt(row.get('or_yoy'), '%')}")
    parts.append(f"净利润同比增长: {fmt(row.get('netprofit_yoy'), '%')}")
    parts.append(f"总市值: {tmv / 10000:.2f}亿元")
    parts.append(f"股息率(TTM): {fmt(row.get('dv_ttm'), '%')}")
    # PEG 计算
    if pe_val and growth_val > 0:
        peg = pe_val / growth_val
        parts.append(f"PEG: {peg:.2f}")
```

**数据来源**: 
- `screening_data` ← Tushare `daily_basic` 表

---

### 8. recent_price_action - 历史行情特征

**来源**: `AIStrategyMixin._build_history_text()` 从 `history_df` 提取

**代码** (ai_mixin.py:523-682):

```python
def _build_history_text(history_df: pd.DataFrame) -> str:
    # 1. 宏观周期 (长期收益 + 最大回撤)
    macro_cagr = ((close.iloc[-1] / first_close) - 1) * 100
    macro_mdd = drawdown.min() * 100
    
    # 2. 趋势 & 波动特征
    pct_all = ((close.iloc[-1] / first_close) - 1) * 100  # 总收益
    pct_5d = ((close.iloc[-1] / fifth_ago_close) - 1) * 100  # 5日收益
    mdd = drawdowns.min() * 100  # 最大回撤
    
    # 3. MA20 偏离度
    bias = ((close.iloc[-1] - ma20) / ma20) * 100
    
    # 4. 连续涨跌天数
    consec_str = f"Consecutive {'Up' if sign_last > 0 else 'Down'} for {consec_days} days"
    
    # 5. 成交量状态
    vol_ratio_5d = vol_5d_avg / vol_older_avg
    vol_desc = "Significant Expansion" if vol_ratio_5d > 1.5 else "Significant Contraction"
    
    # 6. 近3日 K线
    for _, r in df.tail(3).iterrows():
        lines.append(f"{d} | {c} | {p} | {v}")
```

**输出示例**:
```
【Macro Horizon】(Configured Baseline)
- Long-Term: Total Return 45.2%, Max Drawdown -23.5%.

【Trend & Swing Characteristics】(Over last 60 trading days)
- Swing: Total return +12.34%, Max Drawdown -8.56%.
- Short-term Momentum: 5-day return -3.21%, currently Consecutive Down for 3 days.
- MA20 Bias: -5.67%.

【Volume & Price Coordination】
- Volume State: Significant Contraction vs historical baseline, Vol Ratio = 0.65.

【Micro 3-Day Action】
Date | Close | Pct_Chg | Vol
0318 | 10.25 | -1.23% | 1234567
0319 | 10.12 | -1.27% | 987654
0320 | 9.98 | -1.38% | 876543
```

---

### 9. history_context - 学习上下文

**来源**: `ReviewManager.get_learning_context()`

**代码** (ai_mixin.py:108-113):

```python
from data.review_manager import ReviewManager
rm = ReviewManager()
history_context = await rm.get_learning_context()
```

**作用**: 提供历史复盘案例，作为 Few-Shot 学习样本

---

## 三、最终 Prompt 结构

```xml
<stock_info>
  ts_code: 000001.SZ
  name: 平安银行
  close: 10.25
  pe_ttm: 5.67
  pb: 0.65
  ...
</stock_info>

<technical_indicators>
  {
    "macd_signal": "Bearish",
    "kdj_signal": "Oversold",
    "k": 18.5,
    "j": 12.3,
    "ma_alignment": "Bearish (MA5=10.1 < MA10=10.3 < MA20=10.5)",
    "volume_trend": "Shrinking (5d/10d ratio: 0.72)",
    "price_vs_ma20": "-4.8% from MA20"
  }
</technical_indicators>

<recent_news>
  - [财联社] 2026-03-20 平安银行发布年报
  - [同花顺] 2026-03-19 机构调研平安银行
</recent_news>

<global_context>
  美股三大指数涨跌互现，纳指涨0.5%...
</global_context>

<strategy_context>
  This stock was selected by the RSI Oversold Rebound strategy. 
  Its current RSI(14) = 18.5 (threshold < 30), indicating extreme oversold conditions.
  Please evaluate: is this a 'golden pit' rebound opportunity 
  or a fundamental deterioration causing a bottomless decline?
</strategy_context>

<recent_price_action>
  【Macro Horizon】...
  【Trend & Swing Characteristics】...
  【Volume & Price Coordination】...
  【Micro 3-Day Action】...
</recent_price_action>

[Few-Shot Learning Context from ReviewManager]

<capital_flow>
  主力净流入: 1234.56万元
  龙虎榜: 当日未上榜
  北向持股: 12345678股, 占流通股比例: 2.34%
</capital_flow>

<financials>
  PE(TTM): 5.67
  PB: 0.65
  ROE: 11.23%
  毛利率: 45.67%
  资产负债率: 92.34%
  营收同比增长: 5.67%
  净利润同比增长: 12.34%
  总市值: 1987.65亿元
  股息率(TTM): 3.45%
  PEG: 0.45
</financials>
```

---

## 四、性能优化措施

| 优化点 | 实现方式 | 效果 |
|--------|----------|------|
| **N+1 查询优化** | 批量预取 history, concepts, moneyflow | O(N) → O(1) |
| **异步流水线** | 新闻获取与主流程并行 | 减少等待时间 |
| **信号量控制** | `asyncio.Semaphore(1)` 限制并发 | 避免 API 限流 |
| **候选数量控制** | `ConfigHandler.get_ai_max_candidates()` | 成本控制 |

---

## 五、总结

| 上下文块 | 数据来源 | 获取方式 | 作用 |
|----------|----------|----------|------|
| stock_info | 筛选结果 | 直接传递 | 股票基本信息 |
| technical_indicators | 历史行情 | 实时计算 | 技术面分析依据 |
| recent_news | 新闻API/本地表 | 异步获取 | 消息面分析依据 |
| global_context | 美股动态 | 批量预取 | 宏观背景 |
| strategy_context | 策略重写 | 策略提供 | 解释选股原因 |
| capital_flow | 资金数据表 | 批量预取 | 资金面分析依据 |
| financials | 筛选数据 | 直接提取 | 基本面分析依据 |
| recent_price_action | 历史行情 | 特征提取 | 价格行为分析 |
| history_context | 复盘记录 | ReviewManager | Few-Shot 学习 |

---

## 六、相关代码文件索引

| 文件 | 说明 |
|------|------|
| [strategies/oversold_strategy.py](../strategies/oversold_strategy.py) | 超跌反弹策略主逻辑 |
| [strategies/ai_mixin.py](../strategies/ai_mixin.py) | AI 分析混入类，上下文构建 |
| [services/ai_service.py](../services/ai_service.py) | AI 服务，Prompt 组装 |
| [data/news_fetcher.py](../data/news_fetcher.py) | 新闻获取 |
| [data/review_manager.py](../data/review_manager.py) | 复盘管理，学习上下文 |
| [utils/technical_analysis.py](../utils/technical_analysis.py) | 技术指标计算 |
