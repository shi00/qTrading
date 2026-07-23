# installer_smoke_running_app.ps1
# Phase 5 DoD §17.6 失败注入 #32 + P1-11 验证脚本
# 验证 installer.iss InitializeSetup 前置检测：
#   1. qTrading 主进程运行中拒绝安装 (#32)
#   2. qtrading-pg-sidecar 运行中拒绝安装 (P1-11)
# 用法: powershell -File scripts/installer_smoke_running_app.ps1 -InstallerPath <path>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# ---- 路径常量 ----
$AppInstallDir = Join-Path $env:LOCALAPPDATA "Programs\AStockScreener"
$AppExePath = Join-Path $AppInstallDir "AStockScreener.exe"

# ---- 工具函数 ----
function Write-Step([string]$msg) { Write-Host "`n[STEP] $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg)   { Write-Host "  [PASS] $msg" -ForegroundColor Green }
function Write-Fail([string]$msg) { Write-Host "  [FAIL] $msg" -ForegroundColor Red }
function Assert-True([bool]$cond, [string]$msg) {
    if ($cond) { Write-Ok $msg } else { Write-Fail $msg; throw "ASSERTION FAILED: $msg" }
}

function Stop-QtradingGraceful {
    $p = Get-Process -Name "AStockScreener" -ErrorAction SilentlyContinue
    if ($p) {
        Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 3
    }
    $sidecar = Get-Process -Name "qtrading-pg-sidecar" -ErrorAction SilentlyContinue
    if ($sidecar) {
        Stop-Process -Name "qtrading-pg-sidecar" -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
}

# ---- 主流程 ----
Write-Host "=== Phase 5 Running App Rejection Smoke Test (Failure Injection #32) ===" -ForegroundColor Yellow
Write-Host "InstallerPath: $InstallerPath"
Write-Host "AppInstallDir: $AppInstallDir"

if (-not (Test-Path $InstallerPath)) {
    Write-Fail "InstallerPath not found: $InstallerPath"
    exit 1
}

# 仅 embedded variant 有 InitializeSetup 前置检测
# 通过 installer 文件名判断 variant
if ($InstallerPath -notmatch "embedded") {
    Write-Fail "This smoke test only applies to embedded variant installer"
    Write-Host "  Expected: path contains 'embedded'"
    Write-Host "  Got: $InstallerPath"
    exit 1
}

try {
    # STEP 1: 清理环境
    Write-Step "1. Cleanup existing installation"
    Stop-QtradingGraceful
    $uninstaller = Join-Path $AppInstallDir "unins000.exe"
    if (Test-Path $uninstaller) {
        & $uninstaller /SILENT /NORESTART 2>$null | Out-Null
        Start-Sleep -Seconds 5
    }
    if (Test-Path $AppInstallDir) {
        Remove-Item -Recurse -Force $AppInstallDir -ErrorAction SilentlyContinue
    }
    Write-Ok "Environment cleaned"

    # STEP 2: 先安装一次（确保有 EXE 可启动）
    Write-Step "2. Install embedded variant (initial install)"
    & $InstallerPath /SILENT /NORESTART
    Start-Sleep -Seconds 5
    Assert-True (Test-Path $AppExePath) "App installed: $AppExePath"

    # STEP 3: 启动 qTrading（保持运行中）
    Write-Step "3. Start qTrading (keep running)"
    $proc = Start-Process -FilePath $AppExePath -PassThru
    Start-Sleep -Seconds 5
    Assert-True (-not $proc.HasExited) "qTrading running (PID: $($proc.Id))"

    # STEP 4: 在 qTrading 运行中尝试安装（应被拒绝）
    Write-Step "4. Attempt install while qTrading running (should be rejected)"
    $installResult = & $InstallerPath /SILENT /NORESTART 2>&1
    $installExitCode = $LASTEXITCODE

    Write-Host "  Installer exit code: $installExitCode"

    # /SILENT 模式下 InitializeSetup 返回 False 时 installer 退出码为 1 或 2（Inno Setup 行为）
    # 退出码 0 = 安装成功（不允许），非 0 = 被拒绝（期望）
    Assert-True ($installExitCode -ne 0) "Installer rejected (exit code non-zero: $installExitCode)"

    # STEP 5: 验证 qTrading 仍在运行（未被 installer 杀死）
    Write-Step "5. Verify qTrading still running (not killed by installer)"
    Start-Sleep -Seconds 2
    $procAfter = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
    Assert-True ($null -ne $procAfter) "qTrading still running (PID: $($proc.Id))"
    Assert-True (-not $procAfter.HasExited) "qTrading not exited"

    # ---- P1-11: sidecar 运行中拒绝安装 ----
    # 清理主进程，保留干净环境测试 sidecar 独立运行场景
    Stop-QtradingGraceful
    Start-Sleep -Seconds 2

    Write-Step "6. Start qtrading-pg-sidecar (keep running, P1-11)"
    $sidecarExe = Join-Path $AppInstallDir "_internal\sidecars\qtrading-pg-sidecar.exe"
    if (-not (Test-Path $sidecarExe)) {
        Write-Host "  sidecar binary not found at $sidecarExe" -ForegroundColor Yellow
        Write-Host "  Skipping P1-11 sidecar rejection scenario (dev build without sidecar)" -ForegroundColor Yellow
    } else {
        # sidecar run 会驻留（等 stdin EOF 退出），用 Start-Process 启动后不等待
        $sidecarProc = Start-Process -FilePath $sidecarExe -ArgumentList "run", "--data-dir", "$env:LOCALAPPDATA\qTrading\postgres\17\data", "--password-file", "$env:TEMP\fake_sidecar_pwd" -PassThru
        Start-Sleep -Seconds 3
        Assert-True (-not $sidecarProc.HasExited) "sidecar running (PID: $($sidecarProc.Id))"

        # STEP 7: sidecar 运行中尝试安装（应被拒绝）
        Write-Step "7. Attempt install while sidecar running (should be rejected, P1-11)"
        $installResult2 = & $InstallerPath /SILENT /NORESTART 2>&1
        $installExitCode2 = $LASTEXITCODE

        Write-Host "  Installer exit code: $installExitCode2"
        Assert-True ($installExitCode2 -ne 0) "Installer rejected (exit code non-zero: $installExitCode2)"

        # STEP 8: 验证 sidecar 仍在运行
        Write-Step "8. Verify sidecar still running (not killed by installer)"
        Start-Sleep -Seconds 2
        $sidecarAfter = Get-Process -Id $sidecarProc.Id -ErrorAction SilentlyContinue
        Assert-True ($null -ne $sidecarAfter) "sidecar still running (PID: $($sidecarProc.Id))"
        Assert-True (-not $sidecarAfter.HasExited) "sidecar not exited"
    }

    Write-Host "`n=== RUNNING APP REJECTION SMOKE TEST PASSED ===" -ForegroundColor Green
    Write-Host "InitializeSetup correctly rejected install while qTrading or sidecar running" -ForegroundColor Green

    # 清理
    Stop-QtradingGraceful
    exit 0
}
catch {
    Write-Host "`n=== RUNNING APP REJECTION SMOKE TEST FAILED ===" -ForegroundColor Red
    Write-Host "Error: $_" -ForegroundColor Red
    Write-Host "Stack: $($_.ScriptStackTrace)" -ForegroundColor Red
    Stop-QtradingGraceful
    exit 1
}
