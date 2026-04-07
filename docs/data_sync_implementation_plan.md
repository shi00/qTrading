# 数据同步完整性增强方案 - 落地实施计划

> **版本**: 1.0  
> **日期**: 2026-04-02  
> **状态**: 待执行  
> **预估总工时**: 25-30小时

---

## 📋 一、实施总览

### 1.1 实施阶段划分

```
┌─────────────────────────────────────────────────────────────────┐
│                    数据同步完整性增强实施路线图                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Phase -1: 紧急修复（P0）                                        │
│  ├── 限流退避机制                                               │
│  ├── 退市股票处理                                               │
│  └── 止损保护                                                   │
│  预估工时: 5.5h                                                 │
│                                                                 │
│  Phase 0: 前置条件修复（P-1）                                    │
│  ├── Schema 扩展                                                │
│  ├── 字段映射修复                                               │
│  └── DAO 分层修复                                               │
│  预估工时: 1.5h                                                 │
│                                                                 │
│  Phase 1: 核心功能实现（P0）                                     │
│  ├── AI Prompt 数据注入                                         │
│  ├── 数据完整性检查                                             │
│  └── 批量查询优化                                               │
│  预估工时: 10h                                                  │
│                                                                 │
│  Phase 2: 重要优化（P1）                                         │
│  ├── 边界场景修复                                               │
│  ├── 数据库性能优化                                             │
│  └── 数据迁移                                                   │
│  预估工时: 3h                                                   │
│                                                                 │
│  Phase 3: 性能优化（P2）                                         │
│  ├── 权重配置化                                                 │
│  ├── 缓存表设计                                                 │
│  └── Prompt 声明校验                                            │
│  预估工时: 5h                                                   │
│                                                                 │
│  Phase 4: 测试与验证（P2）                                       │
│  ├── 单元测试                                                   │
│  ├── 集成测试                                                   │
│  └── 端到端测试                                                 │
│  预估工时: 5h                                                   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 实施时间表

| 阶段 | 开始日期 | 结束日期 | 工作日 | 状态 |
|:----:|:--------:|:--------:|:------:|:----:|
| Phase -1 | Day 1 | Day 1 | 1天 | 待开始 |
| Phase 0 | Day 1 | Day 1 | 0.5天 | 待开始 |
| Phase 1 | Day 2 | Day 3 | 2天 | 待开始 |
| Phase 2 | Day 4 | Day 4 | 1天 | 待开始 |
| Phase 3 | Day 5 | Day 5 | 1天 | 待开始 |
| Phase 4 | Day 6 | Day 6 | 1天 | 待开始 |
| **总计** | **Day 1** | **Day 6** | **6.5天** | - |

---

## 🚨 二、Phase -1: 紧急修复（P0）

**优先级**: 🔴 致命  
**预估工时**: 5.5小时  
**目标**: 修复可能导致系统崩溃的致命问题

### 2.1 任务清单

#### 2.1.1 添加限流退避装饰器

**文件**: `data/external/tushare_client.py`

**任务描述**:
- 实现指数退避重试机制
- 检测 HTTP 429 和频次超限错误
- 自动休眠后重试

**代码实现**:
```python
import asyncio
from functools import wraps
from typing import Callable

def rate_limit_adaptive(max_retries: int = 3, base_delay: float = 60.0):
    """限流自适应装饰器"""
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            for attempt in range(max_retries):
                try:
                    result = await func(self, *args, **kwargs)
                    return result
                except Exception as e:
                    error_msg = str(e).lower()
                    
                    if "429" in error_msg or "rate limit" in error_msg or "频次超限" in error_msg:
                        if attempt < max_retries - 1:
                            delay = base_delay * (2 ** attempt)
                            logger.warning(
                                f"[TushareClient] Rate limit hit, "
                                f"retrying in {delay}s (attempt {attempt + 1}/{max_retries})"
                            )
                            await asyncio.sleep(delay)
                            continue
                    
                    raise e
            
            raise Exception(f"Max retries ({max_retries}) exceeded")
        
        return wrapper
    return decorator
```

**验收标准**:
- ✅ 遇到 429 错误时自动重试
- ✅ 重试间隔符合指数退避
- ✅ 日志记录完整

**预估工时**: 1小时

---

#### 2.1.2 修改 get_stock_basic 支持全量同步

**文件**: `data/external/tushare_client.py`

**任务描述**:
- 添加 `list_status` 参数
- 包含 `delist_date` 字段
- 保持向后兼容

**代码实现**:
```python
async def get_stock_basic(self, list_status: str = "L"):
    """
    获取股票基础信息。
    
    Args:
        list_status: 上市状态过滤
            - "L": 仅上市中（默认，保持向后兼容）
            - "D": 仅退市
            - "": 全部（用于数据同步）
    """
    return await self._handle_api_call(
        self.pro.stock_basic,
        exchange="",
        list_status=list_status,
        fields="ts_code,symbol,name,area,industry,list_date,delist_date,market,list_status",
    )

async def get_stock_basic_all(self):
    """获取所有股票（包括退市股票）"""
    return await self.get_stock_basic(list_status="")
```

**验收标准**:
- ✅ 默认行为不变（list_status="L"）
- ✅ 可获取退市股票
- ✅ 包含 delist_date 字段

**预估工时**: 0.5小时

---

#### 2.1.3 添加 NULL 值清洗逻辑

**文件**: `data/sync/base.py`

**任务描述**:
- 清洗空字符串和 "None" 为真实 NULL
- 确保数据库查询正确

**代码实现**:
```python
def _clean_null_values(self, df: pd.DataFrame) -> pd.DataFrame:
    """清洗 NULL 值"""
    import numpy as np
    
    df = df.replace("", np.nan)
    df = df.replace("None", np.nan)
    df = df.replace("nan", np.nan)
    
    return df
```

**验收标准**:
- ✅ 空字符串转换为 NULL
- ✅ "None" 字符串转换为 NULL
- ✅ 数据库存储正确

**预估工时**: 0.5小时

---

#### 2.1.4 实施细粒度补表

**文件**: `data/persistence/daos/quote_dao.py`

**任务描述**:
- 修改 `verify_data_integrity` 返回缺失的表列表
- 只重试缺失的表

**代码实现**:
```python
async def verify_data_integrity(
    self, 
    trade_date: datetime.date | str, 
    tables: list | None = None,
) -> dict:
    """
    验证数据完整性（相对基准法）。
    
    Returns:
        {
            "passed": bool,
            "details": dict,
            "expected_base": int,
            "missing_tables": list[str]  # 新增：缺失的表列表
        }
    """
    # ... 现有逻辑 ...
    
    result = {
        "passed": True, 
        "details": {}, 
        "expected_base": 0,
        "missing_tables": []  # 新增字段
    }
    
    # 检查每个表，记录缺失的表
    for table in tables:
        # ... 检查逻辑 ...
        if not passed:
            result["missing_tables"].append(table)
    
    return result
```

**验收标准**:
- ✅ 返回缺失的表列表
- ✅ 只重试缺失的表
- ✅ 节约 API 调用

**预估工时**: 2小时

---

#### 2.1.5 添加止损阈值配置

**文件**: `utils/config_handler.py`

**任务描述**:
- 添加止损阈值配置
- 限制单次重试天数和股票数

**代码实现**:
```python
DEFAULT_CONFIG = {
    # ... 现有配置 ...
    "sync_integrity": {
        # ... 现有配置 ...
        
        # 止损阈值配置
        "max_retry_days_per_sync": 30,
        "max_retry_stocks_per_sync": 100,
        "enable_adaptive_retry": True,
    }
}
```

**验收标准**:
- ✅ 配置项已添加
- ✅ 默认值合理
- ✅ 可通过配置文件修改

**预估工时**: 0.5小时

---

#### 2.1.6 修复极低频表边界问题

**文件**: `data/persistence/daos/quote_dao.py`

**任务描述**:
- 极低频表（limit_list, suspend_d）不作为拦截条件
- 避免平淡市场无限重试

**代码实现**:
```python
# 对于极低频事件表，只要 daily_quotes 有数据就认为通过
if table in ["limit_list", "suspend_d"]:
    passed = True  # 不作为拦截条件
    result["details"][table] = {
        "count": count,
        "expected": expected,
        "reference": reference_count,
        "tolerance": tolerance,
        "passed": passed,
        "note": "极低频事件表，不触发重试"
    }
```

**验收标准**:
- ✅ 极低频表不触发重试
- ✅ 平淡市场正常处理
- ✅ 日志记录清晰

**预估工时**: 0.5小时

---

### 2.2 Phase -1 验收清单

| 任务 | 文件 | 状态 | 验收人 |
|------|------|:----:|:------:|
| 限流退避装饰器 | tushare_client.py | ⬜ | |
| get_stock_basic 全量同步 | tushare_client.py | ⬜ | |
| NULL 值清洗 | base.py | ⬜ | |
| 细粒度补表 | quote_dao.py | ⬜ | |
| 止损阈值配置 | config_handler.py | ⬜ | |
| 极低频表边界修复 | quote_dao.py | ⬜ | |

---

## 🔧 三、Phase 0: 前置条件修复（P-1）

**优先级**: 🔴 前置条件  
**预估工时**: 1.5小时  
**目标**: 修复 Schema 和字段映射问题

### 3.1 任务清单

#### 3.1.1 扩展 financial_reports 添加 n_cashflow_act 字段

**文件**: 
- `data/constants.py`
- `data/persistence/models.py`
- `data/persistence/daos/financial_dao.py`

**任务描述**:
- 添加 `n_cashflow_act` 字段到 Schema
- 修改数据库模型
- 更新 DAO 层

**代码实现**:

**步骤 1**: 修改 `data/constants.py`
```python
FINANCIAL_REPORT_SCHEMA_COLS = [
    # ... 现有字段 ...
    "goodwill",
    "audit_result",
    "n_cashflow_act",  # 新增
]
```

**步骤 2**: 修改 `data/persistence/models.py`
```python
class FinancialReports(Base):
    __tablename__ = "financial_reports"
    # ... 现有字段 ...
    n_cashflow_act = Column(Float)  # 新增
```

**步骤 3**: Alembic 迁移
```bash
alembic revision --autogenerate -m "add n_cashflow_act to financial_reports"
alembic upgrade head
```

**验收标准**:
- ✅ 字段已添加到模型
- ✅ 数据库迁移成功
- ✅ 历史数据兼容

**预估工时**: 0.5小时

---

#### 3.1.2 扩展 stock_basic 添加 delist_date 字段

**文件**: 
- `data/persistence/models.py`
- `data/persistence/daos/quote_dao.py`

**任务描述**:
- 添加 `delist_date` 字段
- 更新查询逻辑

**代码实现**:

**步骤 1**: 修改 `data/persistence/models.py`
```python
class StockBasic(Base):
    __tablename__ = "stock_basic"
    # ... 现有字段 ...
    delist_date = Column(String)  # 新增
    
    # 添加索引
    __table_args__ = (
        Index('idx_stock_basic_dates', 'list_date', 'delist_date'),
    )
```

**步骤 2**: Alembic 迁移
```bash
alembic revision --autogenerate -m "add delist_date to stock_basic"
alembic upgrade head
```

**验收标准**:
- ✅ 字段已添加
- ✅ 索引已创建
- ✅ 查询逻辑已更新

**预估工时**: 0.5小时

---

#### 3.1.3 修正 fina_mainbz 字段名

**文件**: `data/constants.py`

**任务描述**:
- 修正字段名映射错误

**代码实现**:
```python
FINA_MAINBZ_SCHEMA_COLS = [
    "ts_code",
    "end_date",
    "bz_item",      # 修正字段名
    "bz_sales",     # 修正字段名
    # ...
]
```

**验收标准**:
- ✅ 字段名正确
- ✅ 数据同步正常

**预估工时**: 0.25小时

---

#### 3.1.4 新增 FinancialDao 读取方法

**文件**: `data/persistence/daos/financial_dao.py`

**任务描述**:
- 添加多期财务数据读取方法
- 添加批量获取方法

**代码实现**:
```python
async def get_financial_reports_history(
    self, 
    ts_code: str, 
    periods: int = 8
) -> pd.DataFrame:
    """获取多期财务报告历史"""
    # 实现细节...

async def get_fina_audit_batch(
    self, 
    ts_codes: list[str]
) -> pd.DataFrame:
    """批量获取审计意见"""
    # 实现细节...
```

**验收标准**:
- ✅ 方法已实现
- ✅ 返回数据正确

**预估工时**: 0.25小时

---

### 3.2 Phase 0 验收清单

| 任务 | 文件 | 状态 | 验收人 |
|------|------|:----:|:------:|
| n_cashflow_act 字段 | constants.py, models.py | ⬜ | |
| delist_date 字段 | models.py, quote_dao.py | ⬜ | |
| fina_mainbz 字段修正 | constants.py | ⬜ | |
| FinancialDao 读取方法 | financial_dao.py | ⬜ | |

---

## 🎯 四、Phase 1: 核心功能实现（P0）

**优先级**: 🔴 极高  
**预估工时**: 10小时  
**目标**: 实现核心数据同步增强功能

### 4.1 任务清单

#### 4.1.1 新增 _build_multi_period_financials()

**文件**: `strategies/ai_mixin.py`

**任务描述**:
- 构建多期财务趋势数据
- 包含 ROE、毛利率、营收/利润增速

**代码实现**:
```python
async def _build_multi_period_financials(
    self, 
    ts_code: str, 
    cache: DataCache
) -> str:
    """构建多期财务趋势数据"""
    # 获取 8 季度财务数据
    df = await cache.get_financial_reports_history(ts_code, periods=8)
    
    if df is None or df.empty:
        return "财务数据不足"
    
    # 计算趋势
    text_parts = []
    
    # ROE 趋势
    if "roe" in df.columns:
        roe_values = df["roe"].dropna().tolist()
        if roe_values:
            text_parts.append(f"ROE趋势（近{len(roe_values)}季度）: {', '.join(map(str, roe_values))}")
    
    # 毛利率趋势
    if "grossprofit_margin" in df.columns:
        margin_values = df["grossprofit_margin"].dropna().tolist()
        if margin_values:
            text_parts.append(f"毛利率趋势: {', '.join(map(str, margin_values))}")
    
    # 营收增速
    if "or_yoy" in df.columns:
        or_yoy_values = df["or_yoy"].dropna().tolist()
        if or_yoy_values:
            text_parts.append(f"营收增速趋势: {', '.join(map(str, or_yoy_values))}")
    
    # 净利润增速
    if "netprofit_yoy" in df.columns:
        profit_yoy_values = df["netprofit_yoy"].dropna().tolist()
        if profit_yoy_values:
            text_parts.append(f"净利润增速趋势: {', '.join(map(str, profit_yoy_values))}")
    
    return "\n".join(text_parts) if text_parts else "财务数据不足"
```

**验收标准**:
- ✅ 返回多期财务趋势
- ✅ 数据格式清晰
- ✅ 异常处理完善

**预估工时**: 2小时

---

#### 4.1.2 新增 _build_auxiliary_data_text()

**文件**: `strategies/ai_mixin.py`

**任务描述**:
- 构建辅助数据文本
- 包含审计意见、分红记录、质押比例

**代码实现**:
```python
async def _build_auxiliary_data_text(
    self, 
    ts_code: str, 
    cache: DataCache
) -> str:
    """构建辅助数据文本"""
    text_parts = []
    
    # 审计意见
    audit_df = await cache.get_fina_audit(ts_code)
    if audit_df is not None and not audit_df.empty:
        latest_audit = audit_df.iloc[0]
        text_parts.append(f"审计意见: {latest_audit.get('audit_result', '未知')}")
    
    # 分红记录
    dividend_df = await cache.get_dividend(ts_code)
    if dividend_df is not None and not dividend_df.empty:
        dividend_count = len(dividend_df)
        text_parts.append(f"历史分红次数: {dividend_count}")
    
    # 质押比例
    pledge_df = await cache.get_pledge_stat(ts_code)
    if pledge_df is not None and not pledge_df.empty:
        latest_pledge = pledge_df.iloc[0]
        pledge_ratio = latest_pledge.get('pledge_ratio', 0)
        text_parts.append(f"质押比例: {pledge_ratio}%")
    
    return "\n".join(text_parts) if text_parts else "无辅助数据"
```

**验收标准**:
- ✅ 返回辅助数据
- ✅ 数据来源正确
- ✅ 异常处理完善

**预估工时**: 3小时

---

#### 4.1.3 新增 get_bulk_table_counts()

**文件**: `data/persistence/daos/quote_dao.py`

**任务描述**:
- 批量获取表记录数
- 避免 N+1 查询

**代码实现**:
```python
async def get_bulk_table_counts(
    self, 
    table_name: str, 
    start_date: datetime.date | str,
    end_date: datetime.date | str,
) -> dict[datetime.date, int]:
    """批量获取指定时间范围内每天的记录数"""
    try:
        df = await self._read_db(f"""
            SELECT trade_date, COUNT(*) as cnt 
            FROM {table_name} 
            WHERE trade_date BETWEEN $1 AND $2
            GROUP BY trade_date
        """, (start_date, end_date))
        
        if df is None or df.empty:
            return {}
        
        return dict(zip(df["trade_date"], df["cnt"]))
    except Exception as e:
        logger.warning(f"[QuoteDao] Failed to get bulk counts for {table_name}: {e}")
        return {}
```

**验收标准**:
- ✅ 单次查询返回所有日期数据
- ✅ 性能提升明显
- ✅ 异常处理完善

**预估工时**: 1小时

---

#### 4.1.4 新增 get_bulk_expected_stock_counts()

**文件**: `data/persistence/daos/quote_dao.py`

**任务描述**:
- 批量获取理论存活股票数
- 使用 delist_date 精确计算

**代码实现**:
```python
async def get_bulk_expected_stock_counts(
    self,
    start_date: datetime.date | str,
    end_date: datetime.date | str,
) -> dict[datetime.date, int]:
    """批量获取指定时间范围内每天的理论存活股票数"""
    try:
        df = await self._read_db("""
            WITH trading_days AS (
                SELECT cal_date AS trade_date
                FROM trade_cal
                WHERE cal_date BETWEEN $1 AND $2
                  AND is_open = 1
                  AND exchange = 'SSE'
            ),
            stock_counts AS (
                SELECT 
                    t.trade_date,
                    COUNT(s.ts_code) as expected_count
                FROM trading_days t
                LEFT JOIN stock_basic s ON s.list_date <= t.trade_date 
                    AND (s.delist_date IS NULL OR s.delist_date > t.trade_date)
                    AND (s.list_status = 'L' OR s.list_status = 'D')
                GROUP BY t.trade_date
            )
            SELECT trade_date, expected_count FROM stock_counts
            ORDER BY trade_date
        """, (start_date, end_date))
        
        if df is None or df.empty:
            return {}
        
        return dict(zip(df["trade_date"], df["expected_count"]))
    except Exception as e:
        logger.warning(f"[QuoteDao] Failed to get bulk expected counts: {e}")
        return {}
```

**验收标准**:
- ✅ 使用 delist_date 精确计算
- ✅ 性能优化明显
- ✅ 异常处理完善

**预估工时**: 2小时

---

#### 4.1.5 新增 get_bulk_sync_quality_scores()

**文件**: `data/persistence/daos/quote_dao.py`

**任务描述**:
- 批量计算质量评分
- 避免逐日期查询

**代码实现**:
```python
async def get_bulk_sync_quality_scores(
    self,
    start_date: datetime.date | str,
    end_date: datetime.date | str,
) -> dict[datetime.date, int]:
    """批量计算质量评分"""
    # 获取所有表的批量数据
    all_counts = {}
    for table in ["daily_quotes", "daily_indicators", "moneyflow_daily", ...]:
        all_counts[table] = await self.get_bulk_table_counts(table, start_date, end_date)
    
    # 获取期望值
    expected_counts = await self.get_bulk_expected_stock_counts(start_date, end_date)
    
    # 计算质量评分
    scores = {}
    for date in expected_counts.keys():
        # 计算该日期的质量评分
        score = self._calculate_quality_score_for_date(date, all_counts, expected_counts[date])
        scores[date] = score
    
    return scores
```

**验收标准**:
- ✅ 批量计算质量评分
- ✅ 性能提升明显
- ✅ 评分逻辑正确

**预估工时**: 2小时

---

### 4.2 Phase 1 验收清单

| 任务 | 文件 | 状态 | 验收人 |
|------|------|:----:|:------:|
| _build_multi_period_financials() | ai_mixin.py | ⬜ | |
| _build_auxiliary_data_text() | ai_mixin.py | ⬜ | |
| get_bulk_table_counts() | quote_dao.py | ⬜ | |
| get_bulk_expected_stock_counts() | quote_dao.py | ⬜ | |
| get_bulk_sync_quality_scores() | quote_dao.py | ⬜ | |

---

## 🔨 五、Phase 2: 重要优化（P1）

**优先级**: 🟠 高  
**预估工时**: 3小时  
**目标**: 优化性能和边界场景

### 5.1 任务清单

#### 5.1.1 添加数据库索引

**文件**: `data/persistence/models.py`

**任务描述**:
- 添加复合索引优化查询性能

**代码实现**:
```python
class StockBasic(Base):
    __tablename__ = "stock_basic"
    
    # ... 现有字段 ...
    
    __table_args__ = (
        Index('idx_stock_basic_dates', 'list_date', 'delist_date'),
        Index('idx_stock_basic_status', 'list_status', 'list_date'),
    )
```

**验收标准**:
- ✅ 索引已创建
- ✅ 查询性能提升

**预估工时**: 0.5小时

---

#### 5.1.2 编写数据迁移脚本

**文件**: `scripts/migrate_stock_basic.py`

**任务描述**:
- 补充历史退市股票
- 清洗 NULL 值

**代码实现**:
```python
"""数据迁移脚本：补充历史退市股票"""

import asyncio
from data.external.tushare_client import TushareClient
from data.persistence.database import DatabaseManager

async def migrate_stock_basic():
    """迁移 stock_basic 表，补充退市股票"""
    
    print("开始迁移 stock_basic 表...")
    
    tushare = TushareClient()
    db = DatabaseManager()
    
    # 获取全量股票
    print("从 Tushare 获取全量股票（包括退市股票）...")
    df = await tushare.get_stock_basic_all()
    
    if df is None or df.empty:
        print("错误：未获取到数据")
        return
    
    print(f"获取到 {len(df)} 只股票")
    
    # 数据清洗
    print("清洗 NULL 值...")
    df = df.replace("", None)
    df = df.replace("None", None)
    
    # 统计退市股票数量
    delisted_count = df[df['list_status'] == 'D'].shape[0]
    print(f"其中退市股票 {delisted_count} 只")
    
    # 保存到数据库
    print("保存到数据库...")
    # ... 实现细节 ...
    
    print("迁移完成！")

if __name__ == "__main__":
    asyncio.run(migrate_stock_basic())
```

**验收标准**:
- ✅ 退市股票已补充
- ✅ NULL 值已清洗
- ✅ 数据完整性验证通过

**预估工时**: 2小时

---

#### 5.1.3 权重配置化

**文件**: `utils/config_handler.py`

**任务描述**:
- 将权重移到配置文件
- 支持动态调整

**代码实现**:
```python
DEFAULT_CONFIG = {
    "sync_integrity": {
        # 质量评分权重配置
        "quality_weights": {
            "quotes_weight": 0.4,
            "indicators_weight": 0.15,
            "moneyflow_weight": 0.15,
            "margin_weight": 0.1,
            "northbound_weight": 0.1,
            "others_weight": 0.1,
        },
        
        # 质量评分阈值
        "quality_threshold": 80,
    }
}
```

**验收标准**:
- ✅ 权重已配置化
- ✅ 可动态调整

**预估工时**: 0.5小时

---

### 5.2 Phase 2 验收清单

| 任务 | 文件 | 状态 | 验收人 |
|------|------|:----:|:------:|
| 数据库索引 | models.py | ⬜ | |
| 数据迁移脚本 | migrate_stock_basic.py | ⬜ | |
| 权重配置化 | config_handler.py | ⬜ | |

---

## 🧪 六、Phase 3: 测试与验证（P2）

**优先级**: 🟡 中  
**预估工时**: 5小时  
**目标**: 确保功能正确性和稳定性

### 6.1 测试清单

#### 6.1.1 单元测试

**文件**: `tests/unit/test_quote_dao.py`

**测试用例**:
- ✅ test_get_expected_stock_count_with_delist_date
- ✅ test_get_expected_stock_count_recent_date
- ✅ test_get_bulk_expected_stock_counts
- ✅ test_get_bulk_table_counts
- ✅ test_get_sync_quality_score_full_data
- ✅ test_get_sync_quality_score_missing_data
- ✅ test_empty_stock_basic_fallback
- ✅ test_future_date_handling

**预估工时**: 2小时

---

#### 6.1.2 集成测试

**文件**: `tests/integration/test_historical_sync_integrity.py`

**测试用例**:
- ✅ test_sync_with_interruption_recovery
- ✅ test_low_quality_data_triggers_resync
- ✅ test_historical_data_not_misjudged
- ✅ test_bulk_vs_individual_query_count

**预估工时**: 2小时

---

#### 6.1.3 端到端测试

**文件**: `tests/e2e/test_end_to_end_sync.py`

**测试用例**:
- ✅ test_full_historical_sync_flow
- ✅ test_ai_prompt_data_injection_flow

**预估工时**: 1小时

---

### 6.2 测试覆盖率要求

| 模块 | 最低覆盖率 | 关键路径覆盖 |
|------|:----------:|:------------:|
| `quote_dao.py` | 80% | 100% |
| `financial_dao.py` | 80% | 100% |
| `historical.py` | 75% | 95% |
| `ai_mixin.py` | 75% | 95% |

---

## 📊 七、风险控制

### 7.1 风险识别

| 风险 | 影响 | 概率 | 缓解措施 |
|------|:----:|:----:|----------|
| Tushare API 限流 | 高 | 中 | 限流退避机制、止损阈值 |
| 数据迁移失败 | 中 | 低 | 备份数据库、分批迁移 |
| 测试覆盖不足 | 中 | 低 | 完整测试计划、代码审查 |
| 性能回退 | 低 | 低 | 性能基准测试、监控 |

### 7.2 回滚计划

**数据库回滚**:
```bash
# 回滚到上一个版本
alembic downgrade -1

# 回滚到指定版本
alembic downgrade <revision_id>
```

**代码回滚**:
```bash
# 回滚到上一个提交
git revert HEAD

# 回滚到指定提交
git revert <commit_hash>
```

---

## ✅ 八、验收标准

### 8.1 功能验收

- ✅ 数据同步完整性检查正常工作
- ✅ 质量评分计算正确
- ✅ 断点续传功能正常
- ✅ AI Prompt 数据注入完整
- ✅ 限流保护机制有效

### 8.2 性能验收

- ✅ 批量查询性能提升 100 倍以上
- ✅ API 调用减少 90% 以上
- ✅ 数据库查询时间在毫秒级

### 8.3 质量验收

- ✅ 单元测试覆盖率 ≥ 80%
- ✅ 集成测试全部通过
- ✅ 端到端测试全部通过
- ✅ 无严重 Bug

---

## 📅 九、实施时间表

### 9.1 详细时间安排

| 日期 | 阶段 | 任务 | 预估工时 | 负责人 |
|------|------|------|:--------:|:------:|
| Day 1 上午 | Phase -1 | 限流退避、全量同步、NULL清洗 | 2h | |
| Day 1 下午 | Phase -1 | 细粒度补表、止损阈值、边界修复 | 3.5h | |
| Day 1 晚上 | Phase 0 | Schema 扩展、字段映射、DAO 修复 | 1.5h | |
| Day 2 全天 | Phase 1 | AI Prompt 注入、批量查询 | 5h | |
| Day 3 全天 | Phase 1 | 质量评分、完整性检查 | 5h | |
| Day 4 上午 | Phase 2 | 数据库索引、数据迁移 | 2.5h | |
| Day 4 下午 | Phase 2 | 权重配置化、优化调整 | 0.5h | |
| Day 5 全天 | Phase 3 | 单元测试、集成测试 | 4h | |
| Day 6 上午 | Phase 3 | 端到端测试、验收 | 1h | |

### 9.2 里程碑

| 里程碑 | 日期 | 交付物 |
|--------|------|--------|
| M1: 紧急修复完成 | Day 1 | 限流保护、退市股票处理 |
| M2: 前置条件完成 | Day 1 | Schema 扩展、字段映射 |
| M3: 核心功能完成 | Day 3 | AI Prompt 注入、批量查询 |
| M4: 优化完成 | Day 4 | 性能优化、数据迁移 |
| M5: 测试完成 | Day 6 | 全部测试通过 |

---

## 📝 十、实施检查清单

### 10.1 每日检查清单

- [ ] 代码已提交到版本控制
- [ ] 单元测试已运行并通过
- [ ] 代码已通过 Lint 检查
- [ ] 文档已更新
- [ ] 进度已记录

### 10.2 阶段验收检查清单

- [ ] 所有任务已完成
- [ ] 所有测试已通过
- [ ] 代码已审查
- [ ] 文档已更新
- [ ] 风险已评估

### 10.3 最终验收检查清单

- [ ] 所有功能正常工作
- [ ] 性能达标
- [ ] 测试覆盖率达标
- [ ] 文档完整
- [ ] 无遗留问题

---

## 📞 十一、联系方式

如有问题，请联系：
- 技术负责人: [待填写]
- 项目经理: [待填写]

---

**文档版本**: 1.0  
**最后更新**: 2026-04-02
