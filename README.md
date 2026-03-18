# AStockScreener (QTrading) - 智能 A 股 AI 量化交易员

[![Build Status](https://img.shields.io/badge/build-passing-brightgreen)]() 
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)]() 
[![UI](https://img.shields.io/badge/UI-Flet-00d2b4)]() 
[![Data Engine](https://img.shields.io/badge/Data-Polars-orange)]()
[![AI Engine](https://img.shields.io/badge/AI-Local%20%2B%20Cloud-blueviolet)]()

**AStockScreener** 是一个极速、隐私优先的本地化量化选股与深度分析平台。它通过将 **高性能 Polars 向量化计算引擎** 与 **大语言模型 (LLM)** 深度结合，提供从“海量指标毫秒级初筛”到“AI 逻辑深度回顾”的全链路工业级投研协作能力。

---

## 🚀 核心特性 (Key Features)

### 1. 🧠 漏斗式智能选股架构 (Funnel-Based Screening)
采用二级联动筛选机制，在处理海量数据的同时提供极高的研报智商：

* **L1 数学策略 (Quantitative)**: 基于 **Polars 惰性求值**，在毫秒级内完成对全市场股票的技术面（超跌、动量）、基本面（PE/ROE/净利增长）及情绪面过滤。支持多维度参数实时交互调节。
* **L2 AI 深度思维 (LLM Analysis)**: 对 L1 选出的候选项进行“人脑化”审读。UI 流式展示思维链 (CoT)；自动聚合个股新闻、龙虎榜、北向资金，生成具有深度见解的分析报告。

### 2. 🔄 自进化 AI 闭环 (Self-Evolving Loop)
内置自动化回顾机制，让 AI 越选越准：

* **结果回顾 (ReviewManager)**: 自动跟踪 T+1/T+5 实际回报，计算相对于基准（CSI300/上证指数）的 **Alpha 收益**。
* **经验学习 (Few-shot Learning)**: 自动标记“成功案例”与“失误陷阱”，并将历史经验动态注入后续筛选的 Prompt 中，实现策略的自进化。

### 3. 🛡️ 工业级数据质量网关 (Data Quality Gate)
三级严苛校验，确保量化决策底座的绝对可靠：

* **Tier 1 (Existence)**: 数据库与存储层的物理映射校验。
* **Tier 2 (Integrity)**: 自动检测交易日历连续性（补齐缺失天数）与同步时效性检查。
* **Tier 3 (Consistency)**: 跨源业务逻辑交叉验证，如量价波动异常检测、财务指标分发一致性校验。

### 4. 🔒 隐私优先设计 (Privacy Priority)

* **本地 AI 推理 (`LocalModelManager`)**: 支持加载 GGUF 格式模型（如 DeepSeek-R1/Llama-3），实现**核心投研逻辑不离本地**。具备智能内存自动卸载、并发锁控制及推理超时强行熔断保护。
* **全本地存储**: 所有交易流水、分析报告、配置信息均通过本地 PostgreSQL 及加密 Service 层管理。

### 5. 🎨 现代桌面交互设计 (Modern UX)

* **响应式 Flet 架构**: 组件化桌面应用，支持主题热切换与自研虚拟化表格，流畅展示 5000+ 数据行。
* **弹性任务中心**: 基于 `ThreadPoolManager` 的高可靠任务调度，任务状态进度持久化，支持异常断点续传。

---

## 🛠️ 技术栈 (Technology Stack)

* **前端**: [Flet](https://flet.dev/) (Flutter 驱动的跨平台框架)
* **计算**: [Polars](https://pola.rs/) (高性能 Rust 计算后端)
* **存储**: [PostgreSQL](https://www.postgresql.org/) + [SQLAlchemy 2.0](https://www.sqlalchemy.org/) + [Alembic](https://alembic.sqlalchemy.org/)
* **AI 推理**: OpenAI Pro (云端) / [llama-cpp-python](https://github.com/abetlen/llama-cpp-python) (本地 GGUF)
* **数据源**: Tushare Pro (核心行情), Akshare (多源补充)

---

## 🏗️ 目录结构 (Architecture Overview)

* **main.py**: 应用入口，负责多重 Service 编排与 UI 路由分发。
* **data/**: 数据中枢。
  * `data_processor.py`: 高可靠增量同步引擎。
  * `data_quality.py`: 三级健康检查服务。
  * `review_manager.py`: 收益总结与 Alpha 回顾。
* **strategies/**: 策略仓库。`all_strategies.py` 统筹 L1-L2 级联逻辑。
* **services/**:
  * `local_model_manager.py`: 本地 Llama 模型生命周期与安全管控。
  * `task_manager.py`: 异步长任务队列管理。
* **ui/**: Flet 组件库，包含虚拟长表、向导界面及任务中心视图。

---

## 📄 快速开始

```bash
# 需 Python 3.10+
git clone https://github.com/shi00/qTrading.git
pip install -r requirements.txt
python main.py
```

*首次启动请根据 Onboarding 向导配置您的 Tushare Token。若需启用 AI 分析，请在设置中配置 OpenAI API 或下载 GGUF 模型到 `models/` 目录。*

---
*Powered by Local AI & High-Performance Quant Logic | Built with ❤️*
