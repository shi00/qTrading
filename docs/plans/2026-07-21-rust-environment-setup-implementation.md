# Windows 环境下安装 Rust 编译构建环境实施计划

> **For Antigravity:** REQUIRED WORKFLOW: Use `.agent/workflows/execute-plan.md` to execute this plan in single-flow mode.

**Goal:** 在本地 Windows 机器上安装 Rust 官方编译构建环境（含 MSVC 工具链和 C++ 编译工具），并通过端到端测试进行验证。

**Architecture:** 
1. 在工作区下新建 `.rust_install` 临时目录。
2. 自动化下载 `vs_buildtools.exe` 和 `rustup-init.exe`。
3. 调用 Windows PowerShell 的 `Start-Process -Verb RunAs` 运行 `vs_buildtools.exe` 以静默方式在后台执行安装，触发用户 UAC 授权确认。
4. 调用 `rustup-init.exe -y` 静默配置 Rust。
5. 清理目录，刷新 PATH，并使用一个测试的 `main.rs` 文件测试构建。

**Tech Stack:** PowerShell, rustup, MSVC Build Tools 2022

---

## User Review Required

> [!IMPORTANT]
> - **用户配合操作**：在执行 **Task 2 (安装 C++ Build Tools)** 时，系统会弹出 UAC (用户账户控制) 提权申请。您必须点击**“是”**允许安装器运行，否则安装将会被阻断。
> - **磁盘空间**：安装 C++ Build Tools 大约需要 2~3 GB 磁盘空间。请确保您的系统盘（C盘）有足够的剩余空间。

## Open Questions

无。方案在设计讨论阶段已与用户达成一致。

---

## Proposed Changes

由于本任务属于环境配置任务，不修改任何项目源代码。

### [NEW] [2026-07-21-rust-environment-setup-implementation.md](file:///d:/workspace/Quantitative%20Trading/astock_screener/docs/plans/2026-07-21-rust-environment-setup-implementation.md)
* 项目目录下的实施计划存盘。

---

## 计划分解与执行步骤

### Task 1: 准备下载目录与文件

**Files:**
- Create: `d:\workspace\Quantitative Trading\astock_screener\.rust_install\download.ps1`

**Step 1: 编写下载脚本**
在工作区创建临时目录，并下载必要的安装程序。编写以下 PowerShell 脚本：
```powershell
$tempDir = "d:\workspace\Quantitative Trading\astock_screener\.rust_install"
if (!(Test-Path $tempDir)) {
    New-Item -ItemType Directory -Path $tempDir
}
Write-Host "Downloading VS Build Tools..."
Invoke-WebRequest -Uri "https://aka.ms/vs/17/release/vs_buildtools.exe" -OutFile "$tempDir\vs_buildtools.exe"
Write-Host "Downloading Rustup..."
Invoke-WebRequest -Uri "https://win.rustup.rs/x86_64" -OutFile "$tempDir\rustup-init.exe"
Write-Host "Downloads completed."
```

**Step 2: 运行并验证下载文件**
- 运行：`powershell -File "d:\workspace\Quantitative Trading\astock_screener\.rust_install\download.ps1"`
- 期望结果：在 `.rust_install` 目录下成功下载 `vs_buildtools.exe` (约 1~4MB) 和 `rustup-init.exe` (约 7~10MB)。

---

### Task 2: 安装 VS C++ Build Tools (MSVC)

**Step 1: 执行静默安装**
- 运行：
```powershell
Start-Process -FilePath "d:\workspace\Quantitative Trading\astock_screener\.rust_install\vs_buildtools.exe" -ArgumentList "--quiet --wait --norestart --nocache --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended" -Verb RunAs -Wait
```
- 期望：系统弹出 UAC 提权界面，用户同意后，安装程序在后台执行安装。由于添加了 `-Wait` 参数，该命令将直到安装彻底结束后才返回。

**Step 2: 验证安装是否成功**
- 运行：
```powershell
Test-Path "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
```
且检查在该工具下是否能检测到可用的开发实例。

---

### Task 3: 安装 Rustup 与工具链

**Step 1: 运行 Rustup 静默安装**
- 运行：
```powershell
Start-Process -FilePath "d:\workspace\Quantitative Trading\astock_screener\.rust_install\rustup-init.exe" -ArgumentList "-y --default-host x86_64-pc-windows-msvc" -Wait
```
- 期望：静默安装成功，不产生任何阻塞性的控制台输入提示。

**Step 2: 验证 Rust 安装及清理**
- 运行以下命令刷新临时会话环境变量并检查版本：
```powershell
$env:Path += ";$env:USERPROFILE\.cargo\bin"
rustc --version
cargo --version
```
- 期望结果：打印出正确的 `rustc` 和 `cargo` 版本。
- 运行清理命令：
```powershell
Remove-Item -Recurse -Force "d:\workspace\Quantitative Trading\astock_screener\.rust_install"
```

---

### Task 4: 端到端构建测试

**Step 1: 创建测试项目并运行构建**
- 在临时目录中测试 cargo：
```powershell
$env:Path += ";$env:USERPROFILE\.cargo\bin"
cd "d:\workspace\Quantitative Trading\astock_screener\"
cargo new .rust_install_test --bin
cd .rust_install_test
cargo run
```
- 期望：成功编译并输出 `Hello, world!`。

**Step 2: 清理测试项目**
- 运行：
```powershell
cd "d:\workspace\Quantitative Trading\astock_screener\"
Remove-Item -Recurse -Force .rust_install_test
```

---

## Verification Plan

### Automated Tests
无。

### Manual Verification
1. 观察 `rustc --version` 与 `cargo --version` 是否成功输出。
2. 观察 `cargo run` 在新建测试项目 `.rust_install_test` 下是否能无错输出 `Hello, world!`。
