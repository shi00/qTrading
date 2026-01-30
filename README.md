# AStockScreener (QTrading) - 智能A股 AI 交易员

[![Build Status](https://img.shields.io/badge/build-passing-brightgreen)]() [![Python](https://img.shields.io/badge/python-3.11+-blue)]() [![License](https://img.shields.io/badge/license-MIT-green)]()

**AStockScreener** 不仅仅是一个选股器，它已经进化为一个具备 **"透明思考、实时反馈、自进化"** 能力的 AI 智能交易员。

利用 **DeepSeek/OpenAI** 大模型能力，它能像人类研究员一样，实时阅读新闻、财报和盘面数据，并 **实时(Streaming)** 展示其完整的思考推理过程。系统内置 **PVC (预测-验证-修正)** 闭环，能从历史盈亏中自动学习进化。

> **注意**：本项目需配合 [Tushare Pro](https://tushare.pro/) Token 使用 (建议 2000 积分以上)。

---

## 🚀 核心特性 (Key Features)

### 1. 🧠 透明化 AI 决策 (Visible Thinking)
*   **白盒推理**: 告别 AI "黑盒"。每一只股票的评分，你都能看到 AI 的完整 **思维链 (Chain of Thought)**。
*   **多维分析**: 综合 **政策面(Policy)**、**全球映射(Global)**、**资金流(Capital)**、**技术面(Tech)**、**基本面(Fundamental)** 五维打分。

### 2. ⚡ 流式急速体验 (Streaming Experience)
*   **实时反馈**: 采用 `Asyncio` 流式并发架构，AI 分析完一只股票 **立即显示**，无需漫长等待。
*   **可视化进度**: 实时进度条与状态日志，让复杂的量化分析过程清晰可见。

### 3. 🔄 自进化闭环 (PVC Loop)
系统拥有自我学习能力：
*   **Prediction**: 记录 AI 预测快照。
*   **Verification**: T+1 日 17:00 自动复盘，计算超额收益 (Alpha)。
*   **Correction**: 自动提取 "成功经验" 和 "失败教训"，动态注入到下一次 Prompt 中，实现 **越用越聪明**。

### 4. 🛡️ 企业级安全与鲁棒性
*   **Token 加密**: 本地 Tushare Token 采用 **AES-GCM** 军工级加密存储。
*   **断点续传**: 数据同步支持断点续传，并在断网/报错时自动重试。
*   **国际化 (I18n)**: 支持中/英双语界面。

---

## 🛠️ 快速开始 (Quick Start)

### 1. 准备工作
*   Python 3.11+
*   Tushare Token (注册 [Tushare](https://tushare.pro/))
*   DeepSeek API Key (或 OpenAI 兼容 Key)

### 2. 安装与运行
```bash
# 克隆仓库
git clone https://github.com/shi00/qTrading.git
cd qTrading

# 安装依赖
pip install -r requirements.txt

# 运行 (自动进入向导模式)
python main.py
```

### 3. 首次配置
1.  在向导中输入 Tushare Token 和 API Key。
2.  点击 **"开始同步"** (Sync Data)，系统将拉取历史行情与财务数据。
3.  进入 **"设置" (Settings)** 页，开启 "自动每日更新"。

### 4. 使用 AI 选股
1.  进入 **"选股器" (Screener)**。
2.  选择策略 **"AI 深度精选 (Beta)"**。
3.  点击 **"执行筛选"**。
4.  观察 **"AI 思考过程日志"** 区域，看着 AI 逐个分析股票。
5.  点击任意股票 **"详情"**，展开 **"查看 AI 思考过程"** 阅读完整研报。

---

## 🏗️ 架构概览

详见 [系统架构设计文档](architecture_design.md)。

*   **UI**: Flet (Flutter based)
*   **Data**: Tushare Pro + Asyncio Crawler
*   **AI**: OpenAI Protocol (DeepSeek V3/R1 Recommended)
*   **Storage**: AioSQLite + AES Encryption

---

## 📄 开源协议
MIT License
