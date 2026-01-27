# AStockScreener (QTrading) - 智能A股选股器

[![Build Android APK](https://github.com/shi00/qTrading/actions/workflows/build_android.yml/badge.svg)](https://github.com/shi00/qTrading/actions/workflows/build_android.yml)

AStockScreener 是一个基于 Python/Flet 开发的跨平台（Windows/Android）A股智能选股工具。它集成了 Tushare Pro 数据接口，提供多种经典的量化选股策略，并支持本地数据缓存与增量更新。

> **注意**：本项目需配合 [Tushare Pro](https://tushare.pro/) Token 使用 (建议 2000 积分以上以解锁全部功能)。

## ✨ 主要功能

### 1. 核心策略 (10+)
*   **💎 价值投资**: 筛选低估值 (低PE/PB)、高盈利 (高ROE) 的白马股。
*   **🚀 高成长**: 捕捉营收与净利润高速增长 (双 >20%) 的潜力股。
*   **💰 高股息**: 寻找高股息率 (>3%) 且分红稳定的防御性标的。
*   **🌏 北向资金**: 跟踪陆股通 (Smart Money) 大幅增持的个股。
*   **🏛️ 龙虎榜机构**: 挖掘机构席位大举净买入的强势股。
*   **💼 大宗交易**: 监控溢价或平价成交的大宗交易数据。
*   **📈 技术突破**: 识别放量突破均线的趋势启动形态。
*   **🔄 超跌反弹**: 捕捉短期连续下跌后的反弹机会。
*   **💵 现金流优质**: 筛选经营性现金流充沛、负债率健康的企业。
*   **🏢 大盘低估**: (新增) 针对大盘股的特定低估值筛选。

### 2. 数据与系统
*   **智能复盘系统**: 自动记录每日选股结果，并跟踪 T+1/T+5 日的收益表现 (实盘验证基础)。
*   **本地数据库**: 使用 SQLite 存储历史行情与财务数据，支持增量更新，极速筛选。
*   **断点续传**: 支持历史数据同步的断点续传功能。同步中断后再次运行时，会自动识别并跳过已完整下载（行情+指标）的日期，避免重复消耗配额。
*   **定时任务**: 后台自动调度数据同步与复盘分析。

### 3. 用户界面 (UI)
*   **跨平台**: 基于 [Flet](https://flet.dev) 构建，通过一套代码支持 Windows 桌面端与 Android 移动端。
*   **可视化**: 交互式数据表格，股票详情弹窗 (行情、估值、财务一目了然)。

## 🛠️ 安装与运行

### 环境要求
*   Python 3.11+
*   Tushare Token

### 本地运行 (源码)

1.  **克隆仓库**
    ```bash
    git clone https://github.com/shi00/qTrading.git
    cd qTrading
    ```

2.  **安装依赖**
    ```bash
    pip install -r requirements.txt
    ```

3.  **运行程序**
    ```bash
    python main.py
    ```

### 使用指南
1.  **初始化配置**: 首次运行会进入向导，输入 Tushare Token 并同步基础数据。
2.  **数据同步**: 建议在“设置”页点击“完整日更新”或“同步3年历史数据”以构建本地库。
3.  **开始选股**: 在“选股器”页面选择策略，点击执行即可看到结果。
4.  **复盘记录**: 勾选“自动保存复盘记录”，系统会自动跟踪后续涨跌幅。

## 📦 打包与发布

### Windows (.exe)
项目使用 `PyInstaller` 进行打包：
```bash
pyinstaller --name "AStockScreener" --onefile --noconsole --icon=NONE --hidden-import=flet --hidden-import=pandas --hidden-import=aiosqlite --hidden-import=tushare --hidden-import=plotly main.py
```

### Android (.apk)
项目使用 GitHub Actions 进行云端构建。
1.  Fork 本仓库。
2.  在 `.github/workflows/build_android.yml` 中配置 (已默认配置)。
3.  Push 代码到 `main` 分支，GitHub Actions 会自动构建并生成 APK Artifact。

## 📝 待办事项 (TODO)
- [ ] **K线图表**: 在详情页添加交互式 K 线图。
- [ ] **AI 优化**: 接入大模型分析复盘记录，自动优化策略阈值。
- [ ] **回测系统**: 提供简单的历史回测功能。

## 📄 开源协议
MIT License
