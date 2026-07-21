# 本地安装 Rust 编译构建环境设计方案

- **日期**：2026-07-21
- **目标**：在 Windows 本地环境下安装 Rust 开发环境（rustc、cargo、rustup）以及必选的 C++ Build Tools (MSVC 链接器)。
- **背景**：Rust 在 Windows 平台上的默认工具链是 `x86_64-pc-windows-msvc`，该工具链依赖于 Microsoft Visual Studio 的 C++ 构建工具和 Windows SDK。目前本地未安装任何 VS 实例，且当前命令行运行在非管理员账户下。

---

## 方案设计

我们选择 **MSVC 工具链半自动化安装方案**：由 Agent 自动在工作区下载相关安装器，在后台静默运行安装命令，同时在需要提权时由用户在弹出的 Windows UAC 窗口中确认授权。

### 详细步骤

#### 1. 准备临时下载目录
- 在工作区下创建专用临时目录：`d:\workspace\Quantitative Trading\astock_screener\.rust_install`。

#### 2. 下载安装包
- 使用 PowerShell 从官方渠道下载：
  - VS Build Tools 2022 安装器：`https://aka.ms/vs/17/release/vs_buildtools.exe`
  - Rustup 安装器 (x64)：`https://win.rustup.rs/x86_64`

#### 3. 安装 Visual Studio C++ Build Tools 2022
- 静默调用 `vs_buildtools.exe`，安装 `Microsoft.VisualStudio.Workload.VCTools`（C++ 生成工具工作负载）以及推荐的 Windows SDK 组件：
  ```powershell
  Start-Process -FilePath "vs_buildtools.exe" -ArgumentList "--quiet --wait --norestart --nocache --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended" -Verb RunAs -Wait
  ```
- **注意**：由于指定了 `-Verb RunAs`（以管理员身份运行），Windows 系统会弹出 UAC 授权弹窗，用户需要点击“是”批准。

#### 4. 安装 Rust 环境 (Rustup)
- 静默调用 `rustup-init.exe` 并传递默认参数以避免交互式命令行中断：
  ```powershell
  Start-Process -FilePath "rustup-init.exe" -ArgumentList "-y --default-host x86_64-pc-windows-msvc" -Wait
  ```
- 默认会将 Rust 相关的二进制文件（`cargo.exe`，`rustc.exe`，`rustup.exe`）安装到用户的 `%USERPROFILE%\.cargo\bin` 目录。

#### 5. 环境清理与验证
- 清理临时目录 `.rust_install`。
- 在当前 Shell 会话中刷新环境变量 `PATH`。
- 执行版本检查命令：
  - `rustc --version`
  - `cargo --version`
- 编写并编译一个极简的 Hello World 测试程序，确保 MSVC 链接器能够正常链接并生成 Windows 可执行文件。
