# AStockScreener (QTrading) - 智能 A 股 AI 量化交易员

[![Build Status](https://img.shields.io/badge/build-passing-brightgreen)]() [![Python](https://img.shields.io/badge/python-3.10%2B-blue)]() [![License](https://img.shields.io/badge/license-MIT-green)]() [![UI](https://img.shields.io/badge/UI-Flet-00d2b4)]() [![AI Engine](https://img.shields.io/badge/AI-Local%20%2B%20Cloud-blueviolet)]()

**AStockScreener** 是一个高性能、本地优先、隐私安全的智能量化分析与选股平台。通过将 **传统量化投研因子** 与 **大语言模型 (LLM)** 深度结合，它旨在为您提供犹如人类资深研究员般的深度技术、基本面与新闻情绪分析。

---

## 🚀 核心特性 (Key Features)

### 1. 🧠 双引擎 AI 决策 (Dual-Engine AI)
*   **本地私有模型 (Local Privacy)**: 内置 `llama-cpp-python` 支持，针对主流硬件（CPU/GPU）深度优化。无需联网，数据不出内网，依靠开源模型（如 Qwen 系列）提供极速的本地资讯过滤、情感分类。
*   **云端深度推理 (Cloud Reasoning)**: 兼容 OpenAI 协议 (支持 OpenAI / DeepSeek 等)，处理高度复杂的“长链条逻辑推理”、“行业纵深对比”和“宏观经济映射”。
*   **流式输出反馈**: AI 的思考过程实时流式展示 (Streaming)，所见即所得，彻底告别“黑盒等待”。

### 2. ⚡ 极致性能与全异步架构 (High-Performance Async)
*   **全异步数据流**: 基于 Python `asyncio` + 后台任务队列 (Task Manager) 与线程池调度，彻底解决界面卡顿问题。
*   **计算与存储优化**: 引入高性能的 `Polars` 引擎对抗庞大数据集，结合 `AioSQLite` 异步本地数据库与 `CacheManager` (LRU 内存缓存) 构建极速的数据获取层保障。
*   **智能后台调度**: 基于 `APScheduler` 构建的后台定时任务，静默完成海量行情与新闻的订阅、清洗与落盘。

### 3. 📊 多维融合数据源 (Multi-Source Data Fusion)
*   **无缝融合**: 原生完美对接 **Tushare Pro** 主力数据，辅以 **AkShare** 作为开源替代/补全方案。
*   **实时与冷热分离**: 支持毫秒级别的实时推送流与百GB级历史数据（冷数据/K线/主力资金/财报）的结构化持久存储。
*   **智能代理自治**: `ProxyManager` 在底层自动处理代理切换纠错与网络抖动抗性检查。

### 4. 🎨 现代可视化沉浸形态 (Modern Cross-Platform UI)
*   **Flet 跨平台前端**: 基于 Flet (底层由 Flutter 驱动) 的流畅动画 UI，兼容 Windows/macOS/Linux 以及极高的 DPI 缩放适配。
*   **精美主题切换**: 内置 **Dracula (暗色)**、**Nordic Navy (深蓝)**、**Professional Light (亮色)** 等护眼专业风格。
*   **人性化交互**: 开箱即用的向导进程 (`OnboardingWizard`) 以及全局的提示气泡 (`ToastManager`)。

### 5. 🛡️ 军工级企业数据安全 (Enterprise-Grade Security)
*   **本地硬核加密**: API Key (如 Tushare Token, OpenAI Key) 使用 `cryptography` AES-GCM 算法与 `keyring` 操作系统级安全底座层层防护加密。
*   **原子化防丢**: 用户配置及运行时中间状态由原子化写入守护，彻底切断由于断电或异常崩溃导致的配置文件损坏。

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
