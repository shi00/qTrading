# AStockScreener (QTrading) - 智能 A 股 AI 量化交易员

[![Build Status](https://img.shields.io/badge/build-passing-brightgreen)]() 
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)]() 
[![UI](https://img.shields.io/badge/UI-Flet-00d2b4)]() 
[![Data Engine](https://img.shields.io/badge/Data-Polars-orange)]()
[![AI Engine](https://img.shields.io/badge/AI-Local%20%2B%20Cloud-blueviolet)]()

**AStockScreener** 是一个极速、隐私优先的本地化量化选股与分析平台。它通过将 **高性能 Polars 向量化计算** 与 **大语言模型 (LLM)** 深度结合，提供从“海量数据指标初筛”到“AI 逻辑深度优选”的全链路量化投研能力。

---

## 🚀 核心特性 (Key Features)

### 1. 🧠 漏斗式智能选股架构 (Funnel-Based Screening)
采用二级联动筛选机制，平衡算力与智商：
*   **L1 数学策略 (Quantitative)**: 基于 **Polars 惰性求值**，在毫秒级内完成对全市场（5000+）股票的技术面（KDJ/MACD）、基本面（PE/PB/ROE）及资金面过滤。支持滑块参数实时调节。
*   **L2 AI 深度思维 (LLM Analysis)**: 对 L1 选出的候选项进行“人脑化”审读。UI 流式展示思维链 (COT)；自动抓取个股新闻、龙虎榜数据、北向资金流向，产出具有深度见解的分析报告与 0-100 结构化评分。

### 2. 🗄️ 极致的数据银行 (Data Engineering)
*   **极速引擎**: 彻底抛弃 Pandas 的低效循环，全量采用 Polars 向量化处理，处理几十万行规模的数据集毫无卡顿。
*   **智能增量同步**: 自动对齐交易日历，精准补齐缺失数据；内置 **Rate Limiter**、**指数级避退 (Exponential Backoff)** 策略，保护 API 配额并防止接口封禁。
*   **多层缓存**: 结合 PostgreSQL 持久化、SQLite 本地缓存与内存级高速缓冲，确保数据调取零延迟。

### 3. 📡 全方位市场感知 (Market Awareness)
*   **自动化新闻情报**: 实时监听主流财经媒体，利用 AI 自动打标、分类（L1/L2 级别）并提取当日热点概念。
*   **情绪监控仪表盘**: 集成大盘温度、全市场涨跌分布、连板情绪，直观呈现当日交易风偏。

### 4. 🎨 现代桌面交互设计 (Modern UX)
*   **Flet 响应式架构**: 组件化 UI，支持主题热切换（深色/浅色/极客）与多语言 (i18n)。
*   **自研虚拟长表**: 轻松流畅展现 5000+ 数据行，无卡顿支持动态排序、过滤与数据导出。
*   **高可靠任务中心**: 后台线程池 (ThreadPoolManager) 与 任务管理器 (TaskManager) 联动。任务状态进度持久化，不惧程序异常退出，支持断点恢复。

---

## 🛠️ 技术栈 (Technology Stack)

*   **前端**: [Flet](https://flet.dev/) (Powered by Flutter)
*   **计算**: [Polars](https://pola.rs/) (Lightning-fast DataFrames)
*   **数据库**: [PostgreSQL](https://www.postgresql.org/) + [SQLAlchemy 2.0](https://www.sqlalchemy.org/) + [Alembic](https://alembic.sqlalchemy.org/)
*   **AI 推理**: [OpenAI SDK](https://github.com/openai/openai-python) (云) + [llama-cpp-python](https://github.com/abetlen/llama-cpp-python) (本地)
*   **数据源**: Tushare Pro, Akshare

---

## 🏗️ 目录结构 (Architecture Overview)

*   `main.py`: 应用入口，负责服务编排与生命周期管理。
*   `data/`: 数据核心，包含 `CacheManager` (DB 调度), `DataProcessor` (同步逻辑), `DAOs` (存储网格)。
*   `strategies/`: 策略定义。`polars_base.py` 提供极速过滤底座，`ai_mixin.py` 注入 LLM 分析流。
*   `services/`: `TaskManager` 负责长任务并发控制；`AIService` 负责大模型通信。
*   `ui/`: Flet 组件库。`views/` 定义路由，`components/` 包含通用控件与 Toast 管理。

---

## 📄 快速开始

```bash
# 需 Python 3.10+
git clone https://github.com/shi00/qTrading.git
pip install -r requirements.txt
python main.py
```
*首次启动请根据 Onboarding 向导配置您的 Tushare Token。*

---
*Powered by Local AI & High-Performance Quant Logic | Built with ❤️*
