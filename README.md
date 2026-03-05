# AStockScreener (QTrading) - 智能 A 股 AI 量化交易员

[![Build Status](https://img.shields.io/badge/build-passing-brightgreen)]() [![Python](https://img.shields.io/badge/python-3.10%2B-blue)]() [![License](https://img.shields.io/badge/license-MIT-green)]() [![UI](https://img.shields.io/badge/UI-Flet-00d2b4)]() [![AI Engine](https://img.shields.io/badge/AI-Local%20%2B%20Cloud-blueviolet)]()

**AStockScreener** 是一个高性能、本地优先、隐私安全的智能量化分析与选股平台。通过将 **传统量化投研因子** 与 **大语言模型 (LLM)** 深度结合，它旨在为您提供犹如人类资深研究员般的深度技术、基本面与新闻情绪分析。

---

## 🚀 核心特性 (Key Features)

### 1. 🧠 智能选股引擎 (Intelligent Screening Engine)
提供从指标初筛到 AI 深度优选的全链路能力：
*   **量化初筛策略 (Quantitative Filtering)**: 内置基本面策略（动态 PE/PB/高股息）、资金偏好（机构抢筹/量价突破）、技术反转（超卖反弹）等多套投资体系，支持灵活的前端滑块参数调节。
*   **强 AI 深度分析引擎 (LLM-Based Analysis)**: 采用“漏斗式”双层筛架构突破大模型算力瓶颈；UI 实时流式展示大模型思维链 (Chain of Thought)；支持结构化 0-100 评分输出与自定义分析视角。

### 2. 🗄️ 数据生命周期管理 (Data Lifecycle Management)
强健的本地化数据银行，彻底摆脱外部云数据库查询依赖：
*   **智能增量更新 (Smart Sync)**: 自动对齐本地离线交易日历与节假日休市逻辑，精准拉取缺失项以节约 API 网络配额。
*   **极致的运算加速**: 抛弃传统 Pandas，全量采用 **Polars** 惰性求值框架对几十万行规模的数据集进行毫无卡顿的毫秒级大宽表过滤；辅以 SQLite 与内存级高速缓存。

### 3. 📡 信息订阅与宏观监控 (Market Intelligence)
*   **自动化新闻监听与热点提取**: 定时从权威媒体抓取市场快讯，并将杂乱新闻流结构化提炼为当日最瞩目的概念主题。
*   **宏观仪表盘**: 首页高度集成直观呈现大盘温度、全市场涨跌家数比、连板情绪等总体交易风偏指标。

### 4. 🎨 极致的用户体验与监控 (Modern UX & UI)
基于现代响应式框架 Flet 构建流畅的跨端桌面交互：
*   **自研虚拟滚动长表 (Virtual Table)**: 突破前端渲染极限，轻松承载展现 5000+ A股数据，无卡顿支持排序和动态分页。
*   **全异步任务与资源互斥锁**: 底层由线程池接管，任务中心全局展现队列进度条日志防假死；执行“重置数据库”等危操作时自动施加界面互斥锁防崩。
*   **交互细节拉满**: 数据抽屉视图展现场景分析图表；支持中英双语 (i18n) 热切换；旧日历史选股战绩自动存档回溯；支持深色/浅色护眼模式自适应。

### 5. 🛡️ 企业级系统底座 (Enterprise Foundations)
*   **多模型灵活切换 (Local+Cloud)**: 支持零缝隙切换云端顶尖推理模型（DeepSeek等）或接入私有本地化脱网模型运行，兼顾智商与隐私。
*   **高可用网络智能阵列**: 深入网络层级别内置接口流控防封 (Rate Limiter)、错误指数级避退重试 (Exponential Backoff) 及代理 IP 轮换流转池。

---

## 🛠️ 快速开始 (Quick Start)

### 1. 环境准备
*   **OS**: Windows 10/11, macOS, Linux
*   **Python**: `3.10` - `3.12`
*   **数据源依赖**: 推荐注册 [Tushare Pro](https://tushare.pro/) 获取 Token 积分以解除流控限制。
*   *(可选但推荐)* C++ 编译环境：用于开启 `llama-cpp-python` 的 GPU (CUDA/Vulkan) 硬件后端加速支持。

### 2. 安装部署
```bash
# 1. 克隆代码仓库
git clone https://github.com/shi00/qTrading.git
cd qTrading

# 2. 安装 Python 核心依赖
# (注: 如需使用显卡加速的 Local AI，请单独查阅 llama.cpp 的硬件编译文档进行特定 wheel 的安装)
pip install -r requirements.txt

# 3. 启动！
python main.py
```

### 3. 初期向导 (Onboarding)
*   首次打开软件将展示可视化的初始化向导：您只需顺次填入 Tushare Token、AI 模型参数即可开始。所有的私密票据皆会被加密保护。

---

## 🏗️ 架构横截面 (Architecture Overview)

*   **UI 触控层 (ui/)**: `Flet` 组件化体系结构，囊括了视图路由 (`views/`)、高复用控件 (`components/`)，支持多语言扩展 (`I18n`)。
*   **业务编排逻辑 (services/)**: `TaskManager` 处理并发作业的挂起与容错，并包含其它顶层微服务胶水层。
*   **量化核发与大脑 (strategies/ & models/)**: 存放各类传统技术因子、数据穿透策略及与 LLM 对话的主上下文构建器与 Prompt 栈。
*   **底层基础设施 (data/ & utils/)**: 包含对外的 Market Data/News 获取，内部 `aiosqlite` 存储网格，基础工具函数群（网络代理管控、日志拦截、加密器）。

---

## 📄 许可证 (License)
本项目采用 **MIT License** 授权。可以放心进行私人定制与二次衍生开发。

---
*Powered by Local AI & 100% Python | Built with ❤️ by the Quantitative Trading Team*
