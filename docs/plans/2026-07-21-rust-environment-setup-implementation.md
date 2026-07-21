# 免提权 Rust GNU 编译环境安装实施计划 (完全自动化)

> **For Antigravity:** REQUIRED WORKFLOW: Use `.agent/workflows/execute-plan.md` to execute this plan in single-flow mode.

**Goal:** 在无管理员权限、无 UAC 弹窗的限制下，全自动配置 Rust 开发环境（GNU 工具链）并验证构建。

**Architecture:**
1. 在用户家目录下载并解压免安装的 `w64devkit` (包含极简的 GCC、Make 及链接器，仅 ~80MB 下载，体积小且免安装)。
2. 配置当前用户 PATH 环境变量以包含 `w64devkit` 编译器路径。
3. 静默安装 Rust 官方 `x86_64-pc-windows-gnu` 工具链。
4. 验证构建。

**Tech Stack:** w64devkit (MinGW-w64), rustup (GNU toolchain)

---

## User Review Required

> [!NOTE]
> - 本方案**完全不需要管理员权限**，也不需要任何 UAC 弹窗确认，Agent 可完全自动执行完毕。
> - 使用的是 GNU 编译器链而非 MSVC 链，但对于标准 Rust 库的构建与运行完全足够。

---

## Proposed Changes

不修改现有项目代码。

---

## 计划分解与执行步骤

### Task 1: 配置免安装 MinGW (w64devkit) 编译环境

**Step 1: 下载并解压 w64devkit**
- 运行 PowerShell 脚本下载 w64devkit 压缩包：
```powershell
$targetPath = "$env:USERPROFILE\.w64devkit"
if (!(Test-Path $targetPath)) { New-Item -ItemType Directory -Path $targetPath }
Invoke-WebRequest -Uri "https://github.com/skeeto/w64devkit/releases/download/v2.0.0/w64devkit-2.0.0.zip" -OutFile "$targetPath\w64devkit.zip"
Expand-Archive -Path "$targetPath\w64devkit.zip" -DestinationPath $targetPath -Force
```

**Step 2: 验证 GCC 是否就绪**
- 临时配置 PATH 并运行：
```powershell
$env:Path += ";$env:USERPROFILE\.w64devkit\w64devkit\bin"
gcc --version
```
- 期望：输出 `gcc (w64devkit) 14.1.0`。

---

### Task 2: 静默安装 Rust GNU 工具链

**Step 1: 运行 Rustup 且配置为 GNU 默认链**
- 运行：
```powershell
Start-Process -FilePath "d:\workspace\Quantitative Trading\astock_screener\.rust_install\rustup-init.exe" -ArgumentList "-y --default-host x86_64-pc-windows-gnu" -Wait
```
- 期望：成功安装 Rust GNU 环境，不产生阻塞。

**Step 2: 清理与 PATH 环境变量持久化**
- 将 Rust (`.cargo\bin`) 和 MinGW (`.w64devkit\bin`) 永久（当前用户级别）加入系统的 PATH 中，以便后续使用：
```powershell
$oldPath = [Environment]::GetEnvironmentVariable("Path", "User")
$newPath = "$oldPath;$env:USERPROFILE\.cargo\bin;$env:USERPROFILE\.w64devkit\w64devkit\bin"
[Environment]::SetEnvironmentVariable("Path", $newPath, "User")
```

---

### Task 3: 验证 Rust 构建

**Step 1: 创建测试项目测试 GNU 构建**
- 运行：
```powershell
$env:Path += ";$env:USERPROFILE\.cargo\bin;$env:USERPROFILE\.w64devkit\w64devkit\bin"
cd "d:\workspace\Quantitative Trading\astock_screener\"
cargo new .rust_gnu_test --bin
cd .rust_gnu_test
cargo run
```
- 期望：打印出 `Hello, world!`，说明 Rust 能够成功调用 GNU gcc 编译器和 ld 链接器完成构建。

**Step 2: 清理测试项目**
- 运行：
```powershell
cd "d:\workspace\Quantitative Trading\astock_screener\"
Remove-Item -Recurse -Force .rust_gnu_test
```
