# AStockScreener (QTrading) - 智能 A 股 AI 量化交易员

[![Build Status](https://img.shields.io/badge/build-passing-brightgreen)]() 
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)]() 
[![UI](https://img.shields.io/badge/UI-Flet-00d2b4)]() 
[![Data Engine](https://img.shields.io/badge/Data-Polars-orange)]()
[![AI Engine](https://img.shields.io/badge/AI-Local%20%2B%20Cloud-blueviolet)]()

**AStockScreener** 是一个极速、隐私优先的本地化量化选股与深度分析平台。它通过将 **高性能 Polars 向量化计算引擎** 与 **大语言模型 (LLM)** 深度结合，提供从"海量指标毫秒级初筛"到"AI 逻辑深度回顾"的全链路工业级投研协作能力。

---

## 🚀 核心特性

### 1. 🧠 漏斗式智能选股架构
采用二级联动筛选机制，在处理海量数据的同时提供极高的研报智商：

* **L1 数学策略**: 基于 **Polars 惰性求值**，在毫秒级内完成对全市场股票的技术面（超跌、动量）、基本面（PE/ROE/净利增长）及情绪面过滤。支持多维度参数实时交互调节。
* **L2 AI 深度思维**: 对 L1 选出的候选项进行"人脑化"审读。UI 流式展示思维链；自动聚合个股新闻、龙虎榜、北向资金，生成具有深度见解的分析报告。

### 2. 🔄 自进化 AI 闭环
内置自动化回顾机制，让 AI 越选越准：

* **结果回顾**: 自动跟踪 T+1/T+5 实际回报，计算相对于基准（CSI300/上证指数）的 **Alpha 收益**。
* **经验学习**: 自动标记"成功案例"与"失误陷阱"，并将历史经验动态注入后续筛选的 Prompt 中，实现策略的自进化。

### 3. 🛡️ 工业级数据质量网关
三级严苛校验与动态基准评估，确保量化决策底座的绝对可靠：

* **Tier 1 (Bronze)**: 数据可用性检查 — 数据库表存在性、基于 `stock_basic` 上市与退市日历的**相对基准法 (Relative Baseline)** 理论存活股票数盘点。
* **Tier 2 (Silver)**: 连续性与时效性检查 — 交易日历连续性验证、数据同步时效，并引入 0-100 数据质量打分系统支持智能断点续传。
* **Tier 3 (Gold)**: 跨源一致性与 AI 注入校验 — 动态验证 AI System Prompt 中声明的辅助数据（如宏观指标、多期财务趋势）与数据库真实情况的绝对一致性（`prompt_validator` 拦截器）。

### 4. 🔒 隐私优先设计

* **本地 AI 推理**: 支持 GGUF 格式模型（如 DeepSeek-R1/Llama-3），实现**核心投研逻辑不离本地**。具备智能内存自动卸载、并发锁控制及推理超时强行熔断保护。
* **全本地存储**: 所有交易流水、分析报告、配置信息均通过本地 PostgreSQL 及加密存储管理。
* **安全凭证**: Token 使用系统 Keyring 或 AES-GCM 加密存储，密钥自动备份恢复。

### 5. 🎨 现代桌面交互设计

* **响应式 Flet 架构**: 组件化桌面应用，支持主题热切换与虚拟化表格，流畅展示 5000+ 数据行。
* **弹性任务中心**: 基于 `ThreadPoolManager` 的高可靠任务调度，IO/CPU 线程池分离，任务状态持久化，支持异常断点续传。
* **国际化支持**: 内置中英文双语切换。

---

## 🛠️ 技术栈

| 类别 | 技术 |
|------|------|
| **前端框架** | [Flet](https://flet.dev/) (Flutter 驱动) |
| **计算引擎** | [Polars](https://pola.rs/) + Pandas |
| **数据库** | [PostgreSQL](https://www.postgresql.org/) + [SQLAlchemy 2.0](https://www.sqlalchemy.org/) |
| **数据迁移** | [Alembic](https://alembic.sqlalchemy.org/) |
| **AI 推理** | OpenAI API (云端) / [llama-cpp-python](https://github.com/abetlen/llama-cpp-python) (本地) |
| **数据源** | [Tushare Pro](https://tushare.pro/) (核心行情) + [Akshare](https://akshare.akfamily.xyz/) (补充) |
| **任务调度** | [APScheduler](https://apscheduler.readthedocs.io/) |

---

## 🏗️ 项目架构

采用 **领域驱动设计 (DDD)** 原则，清晰分层：

```
astock_screener/
├── main.py                 # 应用入口，服务编排与生命周期管理
├── config.py               # 全局配置（数据库连接等）
│
├── data/                   # 数据层
│   ├── cache/              # 缓存管理
│   │   └── cache_manager.py    # 单例缓存管理器，DAO 统一入口
│   ├── domain_services/    # 领域服务
│   │   ├── trade_calendar_service.py  # 交易日历服务（三级降级）
│   │   └── market_data_service.py     # 市场数据后台服务
│   ├── external/           # 外部数据源
│   │   ├── tushare_client.py   # Tushare API 客户端（限流、重试）
│   │   └── news_fetcher.py     # 新闻抓取服务
│   ├── persistence/        # 持久化层
│   │   ├── daos/           # 数据访问对象
│   │   │   ├── base_dao.py     # 基础 DAO（类型转换、批量写入）
│   │   │   ├── stock_dao.py    # 股票基础数据
│   │   │   ├── quote_dao.py    # 日线行情
│   │   │   └── ...
│   │   ├── models.py           # SQLAlchemy ORM 模型
│   │   ├── db_migrator.py      # 数据库迁移管理
│   │   └── quality_gate.py     # 数据质量门控装饰器
│   ├── sync/               # 数据同步策略
│   │   ├── base.py             # 同步策略接口
│   │   ├── historical.py       # 历史行情同步
│   │   └── financial.py        # 财务数据同步
│   ├── mixins/             # 混入类
│   │   ├── health_mixin.py     # 健康检查
│   │   └── calendar_mixin.py   # 日历工具
│   └── data_processor.py   # 数据处理门面类
│
├── strategies/             # 策略层
│   ├── base_strategy.py        # 策略基类 + 自动注册装饰器
│   ├── polars_base.py          # Polars 策略基类
│   ├── ai_mixin.py             # AI 分析混入
│   ├── oversold_strategy.py    # 超跌反弹策略
│   ├── fundamental.py          # 基本面策略（价值、成长）
│   └── market.py               # 市场策略（突破、北向）
│
├── services/               # 服务层
│   ├── ai_service.py           # AI 服务（OpenAI 兼容）
│   ├── local_model_manager.py  # 本地模型管理
│   └── task_manager.py         # 异步任务管理器
│
├── ui/                     # 表现层 (MVVM)
│   ├── app_layout.py           # 主布局
│   ├── components/             # 可复用组件
│   │   ├── virtual_table.py    # 虚拟化表格
│   │   ├── toast_manager.py    # 消息提示
│   │   └── ...
│   ├── viewmodels/             # 视图模型
│   │   ├── home_view_model.py
│   │   └── screener_view_model.py
│   └── views/                  # 视图
│       ├── home_view.py
│       ├── screener_view.py
│       └── settings_view.py
│
├── utils/                  # 工具层
│   ├── config_handler.py       # 配置管理（读写锁）
│   ├── security_utils.py       # 安全工具（AES-GCM）
│   ├── thread_pool.py          # 线程池管理（IO/CPU 分离）
│   ├── rate_limiter.py         # 令牌桶限流器
│   └── technical_analysis.py   # 技术指标计算
│
├── tests/                  # 测试层
│   ├── conftest.py             # 测试配置
│   └── test_*.py               # 单元测试
│
└── alembic/                # 数据库迁移
    └── versions/               # 迁移脚本
```

---

## 📄 快速开始

### 1. 环境要求

* Python 3.10+
* PostgreSQL 14+

### 2. 安装

```bash
git clone https://github.com/shi00/qTrading.git
cd qTrading
pip install -r requirements.txt
```

### 3. 配置数据库

创建 `.env` 文件（或设置系统环境变量）：

```bash
# 必需：数据库连接
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/astock
```

### 4. 运行

```bash
python main.py
```

*首次启动请根据 Onboarding 向导配置您的 Tushare Token。若需启用 AI 分析，请在设置中配置 OpenAI API 或下载 GGUF 模型到 `ai_models/` 目录。*

---

## 🧪 测试

```bash
# 运行所有测试
python -m pytest tests/ -v

# 运行特定测试
python -m pytest tests/test_strategy_oversold.py -v
```

---

## 📊 设计亮点

### 策略自动注册

```python
@register_strategy("oversold")
class OversoldStrategy(BaseStrategy, AIStrategyMixin):
    def _filter_logic(self, lf: pl.LazyFrame, context: dict) -> pl.LazyFrame:
        # 策略逻辑
        pass
```

### 数据质量门控

```python
@require_quality(QualityTier.SILVER)
def _filter_logic(self, lf, context):
    # 只有数据质量达到 SILVER 级别才执行
    pass
```

### 异步性能监控

```python
@log_async_operation(
    operation_name="fetch_market_data",
    threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
)
async def _fetch_market_data(self):
    # 自动记录执行时间，超阈值告警
    pass
```

---

## 📝 License

MIT License

---

*Powered by Local AI & High-Performance Quant Logic | Built with ❤️*
