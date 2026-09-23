# review08-D1 修复方案：抽取新闻抓取归一化内核、统一时间口径

> 分支：`fix/review08-d1-news-dedup`（基于 main）
> 范围：`data/external/news_fetcher.py` 两公开方法 + 唯一下游消费点 + 配套单测
> 状态：已定稿（2026-09-23）

## 1. 问题背景（review08-D1）

`NewsFetcher.get_stock_news` 与 `NewsFetcher.get_stock_news_documents` 约 100 行近乎逐行重复：

- 相同的 CNINFO `market="沪深京"` 解析与 180 天窗口；
- 相同的 `stock_zh_a_disclosure_report_cninfo` / `stock_news_em` 调用；
- 相同的列名回退逻辑（`"公告标题" if ... else cols[2]`、`"新闻标题" if ... else "新闻内容"`）；
- 相同的两层 try + `_run_with_python_string_storage` 包裹。

**差异**：输出结构与时间口径——

| 方法 | publish_time 形态 | 口径 |
|------|------------------|------|
| `get_stock_news` | 字符串 `f"{pub_date} 00:00:00"` 或 `str(pub_time)` | CST 本地时间文本，**无时区转换** |
| `get_stock_news_documents` | `_parse_news_time()` 产出 | CST 本地化 → UTC tz-naive datetime |

同一源数据（如公告日期 `2024-08-30`），两方法产出分别为字符串 `"2024-08-30 00:00:00"` 与
`datetime(2024,8,29,16,0)`（UTC naive）。下游若混用两接口做时间比较，出现 8 小时（公告跨日 16 小时）偏差。
代码重复是次要问题，**时间口径歧义是主要关切**。

## 2. 调查结论

### 2.1 get_stock_news 调用方（5 处真实调用，均在 `strategies/ai_mixin.py`）

| 位置 | 场景 |
|------|------|
| `ai_mixin.py:640` | `bg_fetch_news` 批量预取（`limit=5, as_of=news_as_of`） |
| `ai_mixin.py:1121 / 1155` | `retry_single` 重取新闻 |
| `ai_mixin.py:1568` | `_mixin_analyze_single` 兜底拉取 |

消费链：`news_list` → `_mixin_analyze_single(news=...)` → `AIService.analyze_stock(news_list)`
→ `services/ai_service/stock_analysis.py:125-130` 渲染 LLM prompt：

```python
f"- [{n.get('source', '')}] {n.get('publish_time', '')[:10]} {n.get('title', '')}"
```

**唯一真实下游消费点**：`stock_analysis.py:127`，`publish_time` 按**字符串**切片 `[:10]`（取日期）。
其余调用方（测试 mock `[{"title": "n"}]` 无 publish_time 键 → `.get` 默认 `''` → 切片安全）。

### 2.2 get_stock_news_documents 调用方

- `services/news_insight_service.py:209`：实时抓取落库 + 展示（`publish_time` 为 UTC naive datetime，
  展示经 `from_utc_to_cst` 转 CST）；
- `data/news_match.py:136` `_time_near`：datetime 比较口径；
- 测试多处 mock。

### 2.3 契约结论

`get_stock_news` 的字符串 `publish_time` 契约**被下游依赖**（`stock_analysis.py:127` 切片），
且 EM 源时间可能为非标准格式（如 `09-20 10:30`），`_parse_news_time` 解析失败返回 `None`——
若直接改输出为 datetime，`None[:10]` 会 TypeError。故统一口径属**行为变更**，必须同步适配下游。

## 3. 方案设计

### 3.1 决策：方案 A（报告建议）— 统一 `_parse_news_time` 口径

- 共享内核一次「抓取 + 归一化」，两公开方法只做输出整形；
- `get_stock_news` 的 `publish_time` 从字符串改为 UTC tz-naive datetime（解析失败为 `None`）；
- 同步适配唯一下游 `stock_analysis.py:127`（新增 `_news_date_label` 辅助函数）；
- 消除重复 + 彻底消除口径歧义（两方法输出同一条 UTC 时间轴）。

放弃方案 B（内核统一、两方法各自整形保持字符串契约）的理由：若 `get_stock_news` 保持 CST 日期字符串，
口径歧义未消除；若转 UTC 字符串则同样需要下游适配，且比 datetime 多一层无谓转换——方案 A 更彻底且适配成本低（仅 1 处）。

### 3.2 共享内核签名

```python
def _fetch_stock_news_core(
    symbol: str,
    ts_code: str,
    *,
    log_prefix: str = "",
) -> tuple[list[dict], list[dict], dict[str, str]]:
    """一次抓取并归一化 CNINFO 公告 + EM 新闻（review08-D1 共享内核）。

    返回 (announcement_docs, news_docs, coverage)：
    - 内部 doc 结构：{"title", "publish_time"(UTC naive datetime|None), "url", "content", "source_label"}
    - 不做 limit / 窗口过滤 / 空 title 过滤（由公开方法整形）；
    - 单层失败返回该层空列表 + coverage 对应 "fail"（日志前缀经 log_prefix 区分两方法原日志文本）。
    """
```

要点：

- 内核 = 现状两个 `_fetch_locked` 的合并体（公告层 + 新闻层两层 try，`_log_with_severity` 日志文本
  经 `log_prefix` 区分：`get_stock_news` 传 `""`、`get_stock_news_documents` 传 `"documents "`，
  与现状日志**逐字一致**）；
- `market = "沪深京"`（B3 已修复）保留在内核中，两方法不再各自声明；
- 时间统一经 `_parse_news_time`：公告 `day_only=True`（仅日期补 00:00:00 后转 UTC），新闻标准格式直解；
- 空 title **保留**（现状 `get_stock_news` 不过滤空 title；`documents` 整形时自行过滤，行为不变）；
- `source_label`：公告固定 `"巨潮公告"`；新闻取 `row.get("文章来源", "东财新闻")`（供 `get_stock_news.source` 字段）。

### 3.2.1 取舍：eager 两源全抓

内核**总是**抓取公告 + 新闻两源。现状 `get_stock_news` 公告层成功时**不调用** `stock_news_em`；
重构后公告成功场景多一次 EM 网络调用。取舍依据：

1. 审查报告建议即「一次抓取 + 归一化」内核（重复的消除以统一抓取为前提）；
2. EM 慢场景由两方法既有的 15s `asyncio.wait_for` 总超时兜底，行为与现状「公告失败 + EM 慢」一致
   （超时返回空列表，监控告警已存在）；
3. EM 失败在公告成功场景出现 warning 日志——属数据源降级可观测信号，无害；
4. `documents` 现状本就两源全抓，该调用已是常态路径。

该取舍在 PR 描述中声明。若未来需要恢复「公告成功跳过 EM」，可在内核加 lazy 参数另行演进。

### 3.3 两公开方法整形（签名、返回结构、as_of / 窗口语义均不变）

`get_stock_news`（`_fetch` 内）：

```python
ann_docs, news_docs, _coverage = _run_with_python_string_storage(
    lambda: _fetch_stock_news_core(symbol, ts_code)
)
if ann_docs:
    return [{"title": d["title"], "publish_time": d["publish_time"], "source": d["source_label"]}
            for d in ann_docs[:limit]]
return [{"title": d["title"], "publish_time": d["publish_time"], "source": d["source_label"]}
        for d in news_docs[:limit]]
```

- 公告优先、EM 回退语义等价现状（现状 `if news_list: return` ⟺ 公告层有 title_col 且 df 非空 ⟺ `ann_docs` 非空，
  因空 title 也 append）；
- `limit=None` 时 `[:None]` 全量切片，等价现状 `head(len(df))`；
- `as_of` 历史回放返回 `[]` 的判断保留在方法开头（不进入内核）；
- 超时/分发异常分支、日志文本、15s `asyncio.wait_for` 完全不变。

`get_stock_news_documents`（`_fetch` 内）：

```python
ann_docs, news_docs, coverage = _run_with_python_string_storage(
    lambda: _fetch_stock_news_core(symbol, ts_code, log_prefix="documents ")
)
# 外层异常时（如 string_storage 锁超时）回退初始值：([], [], {"announcement": "fail", "news": "fail"})
# 合并整形：过滤空 title → 保持 公告在前、新闻在后 → 时间降序 → 窗口过滤 → 限量
```

- 外层异常降级语义与现状闭包初始值一致（现状注释"docs/coverage 闭包保留已部分收集的结果"）；
- `docs/coverage` 结构、排序（`None` 置尾）、窗口过滤（`publish_time is None or >= cutoff`）、`limit` 全部不变。

### 3.4 下游适配（行为变更，必须同步）

`services/ai_service/stock_analysis.py`：

```python
def _news_date_label(item: dict) -> str:
    """新闻日期标签（LLM prompt 用）：UTC naive datetime → YYYY-MM-DD；字符串/缺失 → 原样截取或空。"""
    pt = item.get("publish_time")
    if isinstance(pt, datetime.datetime):
        return pt.strftime("%Y-%m-%d")
    return str(pt)[:10] if pt else ""
```

- `stock_analysis.py:127` 改为 `f"- [{n.get('source', '')}] {_news_date_label(n)} {n.get('title', '')}"`；
- 保留字符串兜底：测试 mock 与历史调用方传字符串时行为不变（不崩溃）。

**行为变更说明**：公告仅含日期（CST 00:00 语义），转 UTC 后 naive 日期提前一天（如 `2024-08-30` →
`2024-08-29 16:00` UTC naive），prompt 中公告日期标签从 `2024-08-30` 变为 `2024-08-29`。
这是口径统一的预期结果，与 documents 落库链路（DB 存 UTC naive）完全一致；对 LLM 分析影响可忽略
（仅日期标签提前一天），并在 PR 描述中声明。

## 4. 测试计划（R19）

`tests/unit/test_news_fetcher.py`：

- 更新 `TestGetStockNews.test_cninfo_success` / `test_cninfo_fails_em_fallback` 的 mock 返回值
  `publish_time` 为 datetime（与真实 `_fetch` 输出一致，保持透传语义测试真实性）；
- `TestGetStockNewsDirectExecution` 新增断言：`publish_time` 为 `datetime.datetime`、`tzinfo is None`、
  公告 `day_only` 转 UTC 精确值（`"2024-08-30"` → `2024-08-29 16:00:00`）；
- 新增 `TestNewsTimeCaliberConsistency`：同一 mock 数据源下调用两方法，断言两方法 `publish_time`
  值相等且为 UTC naive（消除口径歧义的回归护栏）。

`tests/unit/test_news_fetcher_documents.py`：

- 现有用例不变（已断言 `tzinfo is None`）；`test_merges_both_sources` 补充公告跨日精确值断言。

`tests/unit/test_ai_service.py`：

- 新增 `TestNewsDateLabel`：datetime → 日期标签；字符串 → 截取；缺失/None → 空串。

## 5. 不变更项（禁止改动）

- 模块顶层 `import akshare as ak`（B5 另项处理）；
- `get_hot_concepts`（B2 另项、同文件不同区域，避免 merge 冲突）；
- `get_latest_global_news` / `get_us_major_moves`（A1/A2 另项）；
- `_parse_news_time` / `_run_with_python_string_storage` / `_ensure_dataframe` / `_log_with_severity` 现有实现。

## 6. 验证命令

- `python -m pytest tests/unit/test_news_fetcher.py tests/unit/test_news_fetcher_documents.py tests/unit/test_ai_service.py -v --tb=short -n0`
- 受影响调用方：`python -m pytest tests/unit/test_ai_mixin.py tests/unit/test_ai_mixin_retry_single.py tests/unit/services/test_news_insight_service.py -v --tb=short -n0`
- `ruff check` / `ruff format --check` / `pyright`（变更文件）
- 可行时 `pre-commit run --all-files`
