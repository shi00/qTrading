# 方案A: 彻底类型迁移 (String -> PostgreSQL 原生 Date/Timestamp) 实施细节

本方案基于**“端到端原生时间” (End-to-End Native Date)** 架构理念，旨在彻底抛弃依赖 `String` 作为各类 `trade_date`, `updated_at` 存储及运算的落后方案。我们将自底向上，从 ORM 定义、DAO 数据灌入流转核心、策略层高效向量化运算，并最终到前后端传递的边界处，进行全线重构成强类型体系。

绝对禁止在数据库交互层 (`DAO._read_db`) 重新转回字符串，以免引发大规模的 CPU 序列化与反序列化性能灾难。

## 1. ORM 层面 (`data/models.py`)

## 1. ORM 层面 (`data/models.py`)

引入 SQLAlchemy 的原生时间类型。所有 `Column(String)` 存储的日期需要严格变更为以下两类：

*   **`Date`**: 凡是仅需到“日”级别的业务时间（如交易日、财报截止日等）。
*   **`DateTime(timezone=False)`** (显式无时区，即 Timezone-Naive):
    - 用于记录生命周期的时间戳（如 `created_at`, `updated_at`）。
    - **架构取舍 (Trade-off)**：对于国内 A 股量化系统，所有的业务数据天然强绑定北京时间 (`Asia/Shanghai`)。如果明确在全局所有层面（PostgreSQL、Python 运行环境、Pandas处理环境）都**统一为北京时间**，那么使用 `timezone=False` 可以**极大简化开发心智模型**。
    - **优势**：这意味着我们无需在 DAO 层做复杂的 `.dt.tz_localize()` 转换，拿出来的 Pandas Datetime 也是干干净净的 Naive 对象，可以直接与 `datetime.now()` 无缝比较而不报错。对于单机/国内单地域部署的量化系统，这是最务实、最稳定的选择。

```python
# 修改前示例
trade_date = Column(String, primary_key=True)
updated_at = Column(String)

# 修改后示例
from sqlalchemy import Date, DateTime
trade_date = Column(Date, primary_key=True)
# 明确禁用时区，全系统默认北京时间
updated_at = Column(DateTime(timezone=False))
```
**影响范围**: `models.py` 中至少 20+ 个实体表的日期/时间字段定义。

## 2. Alembic 数据迁移层面 (`alembic/versions`)

由于目标是**彻底重建 baseline 测试环境**（方案 A 适用场景），我们不需要编写复杂的原生 SQL 进行数据升级。
操作路径为：
1. 删除 `alembic/versions` 下现有的所有脚本。
2. 删除本机 PostgreSQL 中相关的 `public` 下所有业务表（或 Drop schema 重建）。
3. 重新执行生成命令：`alembic revision --autogenerate -m "initial_baseline_native_types"`
4. 在开发与测试环境中重新拉取初始化数据。

## 3. 持久化数据入库极速清洗层 (`data/daos/base_dao.py`)

这是防御 Tushare/AKShare 等第三方 API 返回的非标字符串 (如 `"20230101"`, `"2024-02-02"`) 直接写入 `asyncpg` 时抛出 `DatatypeMismatchError` 的最关键防线。我们要充分利用 `pandas.to_datetime` 在底层的 C 语言向量化加速能力。

为了提升系统的可维护性，避免全局出现无法区分的同名列（例如有的表 `date` 列是日期，有些表是时间戳），我们将在 `data/models.py` 底部（或单独的 `meta.py`配置文件中）**显式且规范地定义日期列元数据映射**：

```python
# data/models.py (日期列元数据字典)
# --- 必须做到 100% 覆盖 models.py 中声明的所有表 ---
DATE_COLUMNS = {
    "stock_basic": ["list_date"],
    "daily_quotes": ["trade_date"],
    "daily_indicators": ["trade_date"],
    "moneyflow_daily": ["trade_date"],
    "northbound_holding": ["trade_date"],
    "top_list": ["trade_date"],
    "screening_history": ["trade_date"],
    "block_trade": ["trade_date"],
    "trade_cal": ["cal_date", "pretrade_date"],
    "financial_reports": ["end_date", "ann_date"],
    "index_daily": ["trade_date"],
    "index_dailybasic": ["trade_date"],
    "margin_daily": ["trade_date"],
    "suspend_d": ["trade_date"],
    "limit_list": ["trade_date"],
    "fina_forecast": ["end_date", "ann_date"],
    "fina_mainbz": ["end_date"],
    "pledge_stat": ["end_date"],
    "repurchase": ["ann_date", "end_date", "exp_date"],
    "dividend": ["end_date", "ann_date", "record_date", "ex_date"],
    "fina_audit": ["end_date", "ann_date"],
    "stk_holdernumber": ["end_date", "ann_date"],
    "top10_holders": ["end_date", "ann_date"],
    "index_weight": ["trade_date"],
    "moneyflow_hsgt": ["trade_date"],
    "shibor_daily": ["date"],
    "macro_economy": ["period"],
    # 特别注意 sync_status 的 date 字段目前设计为 String 还是 DateTime 视业务而定
    "sync_status": ["last_sync_date", "last_data_date"] 
}

DATETIME_COLUMNS = {
    "market_news": ["publish_time", "created_at"],
    "screening_history": ["created_at"],
    "task_history": ["created_at", "started_at", "completed_at"],
    "stock_sync_status": ["step4_completed_at", "updated_at"],
    # 下方统一为各表的 updated_at 生命周期跟踪字段
    "stock_basic": ["updated_at"],
    "stock_concepts": ["updated_at"],
    "daily_quotes": ["updated_at"],
    "daily_indicators": ["updated_at"],
    "moneyflow_daily": ["updated_at"],
    "northbound_holding": ["updated_at"],
    "top_list": ["updated_at"],
    "sync_status": ["updated_at"],
    "block_trade": ["updated_at"],
    "trade_cal": ["updated_at"],
    "financial_reports": ["updated_at"],
    "index_daily": ["updated_at"],
    "index_dailybasic": ["updated_at"],
    "margin_daily": ["updated_at"],
    "suspend_d": ["updated_at"],
    "limit_list": ["updated_at"],
    "fina_forecast": ["updated_at"],
    "fina_mainbz": ["updated_at"],
    "pledge_stat": ["updated_at"],
    "repurchase": ["updated_at"],
    "dividend": ["updated_at"],
    "fina_audit": ["updated_at"],
    "shibor_daily": ["updated_at"],
    "stk_holdernumber": ["updated_at"],
    "top10_holders": ["updated_at"],
    "index_weight": ["updated_at"],
    "moneyflow_hsgt": ["updated_at"],
}
```

随后，在 DAO 层我们改造 `BaseDao._save_upsert` 预处理。此时我们可以利用 `table_name` 这个绝佳的上下文参数来进行精准打击。

**特别注意**：现有的 `updated_at` 自动注入逻辑必须从生成字符串改为生成原生 `datetime` 对象。

```python
# data/daos/base_dao.py

def _save_upsert(self, df, table_name, columns, pk_columns, ...):
    ...
    # --- 修复 1：updated_at 必须注入原生 datetime ---
    if has_updated_at and "updated_at" not in columns:
        columns = list(columns) + ["updated_at"]
        from datetime import datetime
        
        # 以前是: now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 现在必须是：
        now = datetime.now() # 依赖我们在 ORM 定义的 timezone=False 架构
        df_slice = df.assign(updated_at=now)[columns]
    else:
        df_slice = df[columns]
    
    # --- 修复 2：基于元数据字典的按需清洗 ---
    from data.models import DATE_COLUMNS, DATETIME_COLUMNS
    target_date_cols = DATE_COLUMNS.get(table_name, [])
    target_datetime_cols = DATETIME_COLUMNS.get(table_name, [])

    def _prepare_records(df_slice):
        # 【核心：切记一定要对 df_slice 显式 copy，防止 Pandas 链式赋值警告及污染外部调用方数据】
        df_clean = df_slice.copy()
        
        # 使用 Pandas 的向量化方法统一转换，以释放 CPU 性能
        for col in df_clean.columns:
            if col in target_date_cols:
                # errors='coerce' 可将无法解析的转为 NaT
                df_clean[col] = pd.to_datetime(df_clean[col], format='mixed', errors='coerce').dt.date
            elif col in target_datetime_cols:
                df_clean[col] = pd.to_datetime(df_clean[col], format='mixed', errors='coerce')

        records = df_clean.to_dict(orient="records")
        # 后续保持对 NaN/NaT 的清洗和数值类型转换逻辑不变
        ...
```

### 3.2 修复底层 `asyncpg` 数据类型适配器 (`BaseDao._prepare_data_params` & `_to_native`)

在全局扫描中，我们发现了另外两个**极其致命、必定导致 asyncpg 崩溃**的底层隐患，必须同时修复：

1. **`_prepare_data_params` 的暴力转字符串 BUG:**
   - 现状: 源码第 53 行 `df[col] = df[col].astype(str)`。如果有些 DAO 绕过 `_save_upsert` 使用原生 SQL 插入数据，这里会把 Date 强转成 String，导致 asyncpg 底层报类型不匹配错误。
   - 修复: `df[col] = pd.to_datetime(df[col], errors='coerce').dt.date`。
2. **`_to_native` 漏判 `pd.Timestamp` BUG:**
   - 当 Pandas 的列类型为 `datetime64[ns]` 时，`to_dict('records')` 产出的每一行实际上是 `pd.Timestamp` 对象。asyncpg 对 `pd.Timestamp` 的兼容性存在边缘风险。
   - 修复: 在 `_to_native` 类型转换器中，明确追加防御：
     ```python
     import pandas as pd
     if isinstance(val, pd.Timestamp):
         return val.to_pydatetime()
     ```

### 3.3 彻底清除各子 DAO 的冗余硬编码
在 `stock_dao.py`, `sync_dao.py`, `macro_dao.py` 等各个子 DAO 类中，散落着大量的冗余代码，例如：
`df["updated_at"] = get_now().strftime("%Y-%m-%d %H:%M:%S")`
**修复标准**：既然强大的 `BaseDao._save_upsert` 已经被赋予了拦截并自动注入原生 `datetime.now()` 的能力，各个子 DAO 凡是在调用 `_save_upsert` 前手工切片或组装字符串时间的操作，必须**全部连根拔除，一句不留**，由基类统一托管。如果是拼装原生 SQL 插入的，也要确保换成原生 `get_now()` 而非 `.strftime()`。

### 3.4 `_read_db` 返回值处理说明
按照“端到端原生时间”架构：**严禁在 `_read_db` 中将从 PostgreSQL 取回的 Date/Timestamp 转换为 String。**
* 当 `asyncpg` 执行 `conn.exec_driver_sql(sql)` 时，它会自动将表中的 `Date` 列解码为 Python 原生的 `datetime.date` 对象，将 `TIMESTAMP` 转化为 `datetime.datetime`。
* 当 `ThreadPoolManager` 通过 `pd.DataFrame(rows, columns=cols)` 构造 DataFrame 时，Pandas 会自动识别这些 `date`/`datetime` 对象，将其包装为对应的强时间类型列。
* **结论**：`_read_db` 代码本体**无需增加任何修改和额外的转换逻辑**，它天然且高效地将包含原生时间对象的 DataFrame 向上层（也就是下述的第 4 步“策略引擎运算层”）输送。上游必须无缝消费这些 Date 对象。

## 4. 策略引擎运算层 (端到端 `Datetime` 接入)

这是此方案区别于伪重构的核心：**必须让策略引擎全盘接受 `Date` 对象运算！** 抛弃形如 `.filter(pl.col("trade_date") == "20230101")` 的落后字符串比较。
在 Polars/Pandas 内部，原生的时间戳类型（`datetime64[ns]` / `Date`）其底层的内存连续性和比较速度是完爆 String 字符串的。

**改造策略 (`strategies/*.py` & `ai_mixin.py`)**:
*   所有的锚点时间（例如 `get_latest_trade_date()`）现在返回的将是 `datetime.date` 对象。
*   所有的条件过滤无需任何改动，只要右侧的值是真实的 `datetime.date` 对象，Polars 会极速完成原生 Date 类型的 `==`, `>`, `<` 的筛选。

### 已知待修复的硬编码冲突点 (Hardcoded String Conflicts)
在全线改造时，需要对以下已知（及类似）的代码模式进行排查和替换：
1. **策略等值过滤 (`strategies/oversold_strategy.py:196` 等)**:
   - 现状: 假设 `end_date` 为字符串进行等值过滤。
   - 修复: 只有当源头 `get_latest_trade_date` 变为 `date` 时，确保 `end_date` 不做强转，直接代入 `pl.col("trade_date") == end_date`。
2. **策略年份提取 (`strategies/ai_mixin.py:655`)**:
   - 现状: `d = str(r.get("trade_date", ""))[-4:]` (基于字符串切片的假设)。
   - 修复: `dt_val = r.get("trade_date"); d = str(dt_val.year) if getattr(dt_val, "year", None) else ""`。
3. **UI 模型数据组装 (`ui/screener_view_model.py:529`)**:
   - 现状: `str(row["trade_date"])`。
   - 修复: `row["trade_date"].strftime("%Y-%m-%d") if isinstance(row["trade_date"], (datetime.date, datetime.datetime)) else str(row["trade_date"])`。
4. **UI 层日期组件深度格式化 (`ui/screener_view.py:542, 627, 1167` 及 `ui/data_view.py:542, 981`)**:
   - 现状: 充斥着极其依赖格式为 `YYYYMMDD` 的硬编码切片渲染模式，如 `date_str[:4]`, `date_str[4:6]`, `val[6:8]`。
   - 修复: **所有前端 UI View 必须假定收到的数据已经是基于 `strftime("%Y-%m-%d")` 规范化过的标准字符串格式**，不能再基于 `YYYYMMDD` 做裸切片；或者更好的是，ViewModel 层直接传递格式化好的结果字符串。
5. **交易日历工具层 (`utils/calendar_mixin.py:156, 165`)**:
   - 现状: `end_date[:4]`, `e[:4]` 取年份。
   - 修复: 向上游要求传入 `datetime.date` 实例，强类型提取 `.year` 进行操作。
6. **外部数据源通讯边界 (`data/tushare_client.py:251`)**:
   - 现状: `date_str[:4]` 处理输入/输出。
   - 修复: 这里是系统的边界防线。所有发往 Tushare 的查询参数应该接收原生的 `datetime.date` 实例，在发送前统一通过 `.strftime("%Y%m%d")` 编码为请求字符串。不允许在客户端内部进行游击战式的切片。

**全局审查机制 (Global Code Audit)**：
在启动任何修改前，必定使用 `grep` 全局搜索 `trade_date.*\[`, `str\(.*date.*\)`, `date_str` 等特征模式，将上述长尾 BUG 连根拔起。

## 5. UI 展现层隔离与序列化 (`utils/log_decorators.py`, `ai_service.py` & UI 层)

**核心冲突点**: 当系统试图用 `json.dumps()` 渲染字典，或者向 `Flet` 的 DataTable 传入原生 `datetime.date` 对象时，会抛出 `TypeError` 或导致前端 UI 崩溃（因为 JSON 规范和部分前端组件不认识 Python 的 `date` 对象）。

**解决方案与排查点**:
唯独只在跨进程通讯 (API / Tushare)、或者 GUI View 渲染层、以及日志文件落库前，做一次 JSON 的 `ISO8601` (`strftime`) 序列化包装。

### 统一序列化函数 (JSON Encoder)
```python
from datetime import date, datetime
import json

def _json_serial(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")
```

### 已知待修复的序列化切入点
必须全局检查所有调用 `json.dumps` 且 payload 中含有 DataFrame 行数据或数据库原生行对象的地方：

1. **装饰器层 (`utils/log_decorators.py:305`)**:
   - 现状: `json.dumps(self.metrics)` 可能会因为 metrics 里带了最近交易日而报错。
   - 修复: `json.dumps(self.metrics, default=_json_serial)`。
2. **AI 服务通讯层 (`services/ai_service.py:398`)**:
   - 现状: `json.dumps(tech_info)` 准备发往 LLM。由于 `tech_info` 源自于 `ScreeningHistory` 或策略计算结果，内部极大概率含有 `trade_date` (现在是原生 `date` 对象)。
   - 修复: `json.dumps(tech_info, default=_json_serial)`。**（极其重要，否则大模型上下文组装直接崩溃）**
3. **前端数据渲染层 (`ui/data_view.py:542, 981` 及 `ui/screener_view.py:542, 627, 1167` 等)**:
   - 现状: 大量存在依赖原生字符串进行切片的渲染代码，例如 `display_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"`。当 `date_str` 底层变为 `datetime.date` 对象时，会直接抛出 `TypeError: 'datetime.date' object is not subscriptable` 导致页面白屏。
   - **修复标准 (必须用这种向下兼容模式)**:
     ```python
     from datetime import date, datetime
     
     # 在所有做类似 date_str[:4] 切片的地方替换为：
     if isinstance(date_str, (date, datetime)):
         display_date = date_str.strftime("%Y-%m-%d")
     else:
         # 兜底：万一数据库里还有历史遗留的 "YYYYMMDD" 字符串
         date_str = str(date_str)
         if len(date_str) == 8:
             display_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
         else:
             display_date = date_str
     ```

## 6. 实施影响评估总结
* **开发阵痛期**：中度。需要全局排查一次硬编码的字符串日期用法。
* **数据库 I/O 性能**：极高提升。由于原生的 `Date` (4 bytes) 和 `TIMESTAMP` (8 bytes) 占据的页空间远小于变长 `String`，索引树更矮，命中率极高。
* **业务计算性能**：极高提升。Polars 和 Pandas 将享受基于 C 语言数组的原生时间对比和窗口滚动函数，无需做字符串 `==` 的低效匹配。
