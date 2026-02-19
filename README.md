# AStockScreener (QTrading) - 智能 A 股 AI 交易员

[![Build Status](https://img.shields.io/badge/build-passing-brightgreen)]() [![Python](https://img.shields.io/badge/python-3.11+-blue)]() [![License](https://img.shields.io/badge/license-MIT-green)]() [![AI Engine](https://img.shields.io/badge/AI-Local%20%2B%20Cloud-blueviolet)]()

**AStockScreener** 是一个本地优先、隐私安全的智能量化分析平台。它结合了 **传统量化因子** 与 **大语言模型 (LLM)** 的能力，为您提供像人类研究员一样的深度投研分析。

> **核心进化**：v2.0 版本现已全面支持 **本地 AI 模型 (Llama.cpp + Vulkan)**，利用您的 GPU (NVIDIA/AMD/Intel) 实现零成本、离线的深度推理。

---

## 🚀 核心特性 (Key Features)

### 1. 🧠 双引擎 AI 决策 (Dual-Engine AI)
*   **本地私有模型 (Local Privaty)**: 内置 `llama-cpp-python`支持，针对 Intel iGPU/NVIDIA GPU 深度优化。无需联网，数据不出内网，由 **Qwen 2.5** 等开源模型提供极速新闻分类与情感分析。
    *   *优化特性*: 针对 1.5B/7B 模型调优，支持 Vulkan 硬件加速，推理速度提升 5-10 倍。
*   **云端深度推理 (Cloud Reasoning)**: 兼容 OpenAI/DeepSeek API，处理复杂的 "长链条推理" 和 "宏观经济映射"。

### 2. ⚡ 极速流式体验 (Streaming & Async)
*   **全异步架构**: 基于 Python `asyncio` + `ThreadPool`，UI 永不卡顿。
*   **实时反馈**: AI 思考过程实时上屏 (Streaming)，拒绝 "黑盒" 等待。可以看到 AI 如何阅读新闻、分析财报并得出结论。

### 3. 🎨 现代可视化界面
*   **多主题支持**: 内置 **Dracula (暗色)**、**Nordic Navy (深蓝)**、**Professional Light (亮色)** 等多套精美主题。
*   **Flet 驱动**: 基于 Flutter 的高性能跨平台 UI，支持高 DPI 缩放与流畅动画。

### 4. 🛡️ 企业级数据安全
*   **加密存储**: Tushare Token 与 API Key 采用 **AES-GCM** 军工级算法本地加密存储。
*   **原子化配置**: 配置文件读写具备原子性保护，防止断电导致配置丢失。

---

## 🛠️ 快速开始 (Quick Start)

### 1. 环境准备
*   **OS**: Windows 10/11 (推荐)
*   **Python**: 3.10 - 3.12
*   **数据源**: [Tushare Pro](https://tushare.pro/) Token (需 2000+ 积分以获取完整数据)
*   **硬件加速 (可选)**: 安装 [Vulkan SDK](https://vulkan.lunarg.com/) 以启用本地 AI 加速。

### 2. 安装
```bash
# 1. 克隆项目
git clone https://github.com/shi00/qTrading.git
cd qTrading

# 2. 安装依赖
# 注意：如需 GPU 加速，建议先手动安装编译好的 llama-cpp-python
pip install -r requirements.txt

# 3. 运行
python main.py
```

### 3. 本地 AI 模型设置 (推荐)
1.  下载 GGUF 模型文件 (推荐 `Qwen/Qwen2.5-1.5B-Instruct-GGUF`)。
2.  将模型放入 `models/` 目录。
3.  在软件 **"设置 -> 本地 AI"** 中选择模型路径。
4.  根据显卡显存大小，调整 **GPU Layers** (推荐 -1 自动加载所有层) 和 **Context Length** (推荐 2048-4096)。

---

## 🏗️ 架构概览

*   **Frontend**: Flet (Flutter) - 响应式 UI，MVVM 模式。
*   **Core**: Python Asyncio - 事件循环驱动。
*   **Data Layer**:
    *   **Source**: Tushare Pro / AkShare
    *   **Storage**: AioSQLite (异步 DB) + CacheManager (LRU 缓存)
*   **AI Layer**:
    *   **Inference**: Llama.cpp (Local) + OpenAI SDK (Cloud)
    *   **Proxy**: 智能代理池，自动处理网络抖动。

---

## 📄 许可证
MIT License.

---
*Built with ❤️ by Quantitative Trading Team*
