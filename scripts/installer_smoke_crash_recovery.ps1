# installer_smoke_crash_recovery.ps1
# Phase 5 DoD §17.6 失败注入 #8 验证脚本
# 验证 Windows Job Object 机制：父进程崩溃时 sidecar + postgres 自动退出
# 用法: powershell -File scripts/installer_smoke_crash_recovery.ps1 -InstallerPath <path>

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

function Wait-SidecarReady([int]$TimeoutSec = 60) {
    $elapsed = 0
    while ($elapsed -lt $TimeoutSec) {
        $p = Get-Process -Name "qtrading-pg-sidecar" -ErrorAction SilentlyContinue
        if ($p) { return $true }
        Start-Sleep -Seconds 2
        $elapsed += 2
    }
    return $false
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
Write-Host "=== Phase 5 Crash Recovery Smoke Test (Failure Injection #8) ===" -ForegroundColor Yellow
Write-Host "InstallerPath: $InstallerPath"
Write-Host "AppInstallDir: $AppInstallDir"

if (-not (Test-Path $InstallerPath)) {
    Write-Fail "InstallerPath not found: $InstallerPath"
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

    # STEP 2: 安装 embedded variant
    Write-Step "2. Install embedded variant"
    & $InstallerPath /SILENT /NORESTART
    Start-Sleep -Seconds 5
    Assert-True (Test-Path $AppExePath) "App installed: $AppExePath"

    # STEP 3: 启动 qTrading，等待 sidecar ready
    Write-Step "3. Start qTrading & wait for sidecar ready"
    $proc = Start-Process -FilePath $AppExePath -PassThru
    $ready = Wait-SidecarReady -TimeoutSec 60
    Assert-True $ready "Sidecar process started"

    # STEP 4: 记录 sidecar 与 postgres 进程
    Write-Step "4. Record sidecar & postgres processes"
    $sidecarBefore = Get-Process -Name "qtrading-pg-sidecar" -ErrorAction SilentlyContinue
    Assert-True ($null -ne $sidecarBefore) "Sidecar process exists before crash"
    Write-Host "  Sidecar PID(s) before crash: $($sidecarBefore.Id -join ',')"

    $postgresBefore = Get-Process -Name "postgres" -ErrorAction SilentlyContinue
    if ($postgresBefore) {
        Write-Host "  Postgres PID(s) before crash: $($postgresBefore.Id -join ',')"
    } else {
        Write-Host "  Postgres: (no postgres.exe process detected, may be embedded in sidecar)"
    }

    # STEP 5: 失败注入 - 强制 kill 主进程（不触发优雅停机）
    Write-Step "5. FAILURE INJECTION: Force kill main qTrading process (simulate crash)"
    if ($proc -and -not $proc.HasExited) {
        # 使用 Stop-Process -Force 模拟进程崩溃，不经过 qTrading 的 shutdown handler
        Stop-Process -Id $proc.Id -Force
        Write-Ok "Main process killed (PID: $($proc.Id))"
    }

    # STEP 6: 等待 Job Object 触发 sidecar+postgres 退出
    Write-Step "6. Wait for Job Object to terminate sidecar (5s grace period)"
    Start-Sleep -Seconds 5

    # STEP 7: 验证 sidecar 无残留（核心断言 - Job Object 机制）
    Write-Step "7. Verify no residual sidecar process (Job Object killed it)"
    $sidecarAfter = Get-Process -Name "qtrading-pg-sidecar" -ErrorAction SilentlyContinue
    Assert-True ($null -eq $sidecarAfter) "No residual sidecar process after main crash"

    # STEP 8: 验证 postgres 无残留
    Write-Step "8. Verify no residual postgres process"
    $postgresAfter = Get-Process -Name "postgres" -ErrorAction SilentlyContinue
    Assert-True ($null -eq $postgresAfter) "No residual postgres process after main crash"

    Write-Host "`n=== CRASH RECOVERY SMOKE TEST PASSED ===" -ForegroundColor Green
    Write-Host "Job Object mechanism verified: parent crash → sidecar+postgres auto-exit" -ForegroundColor Green
    exit 0
}
catch {
    Write-Host "`n=== CRASH RECOVERY SMOKE TEST FAILED ===" -ForegroundColor Red
    Write-Host "Error: $_" -ForegroundColor Red
    Write-Host "Stack: $($_.ScriptStackTrace)" -ForegroundColor Red
    # 清理残留进程
    Stop-QtradingGraceful
    exit 1
}
