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
# 用户数据根目录：嵌入 PG 数据（postgres\16\data）与 sidecar 日志（postgres-logs）
$UserDataRoot = Join-Path $env:LOCALAPPDATA "qTrading"
# 主程序日志目录（utils/logger.py 的 LOG_DIR）
$AppLogDir = Join-Path $env:LOCALAPPDATA "AStockScreener\logs"

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

function Write-Diagnostics([string]$reason) {
    Write-Host "`n[DIAG] $reason" -ForegroundColor Yellow
    $sidecarDir = Join-Path $AppInstallDir "_internal\sidecars"
    if (Test-Path $sidecarDir) {
        Write-Host "[DIAG] sidecar dir: $sidecarDir"
        Get-ChildItem $sidecarDir -Recurse -Force -ErrorAction SilentlyContinue |
            Select-Object FullName, Length | Format-Table -AutoSize | Out-String | Write-Host
    } else {
        Write-Host "[DIAG] sidecar dir missing: $sidecarDir"
    }
    $logs = @()
    if (Test-Path $AppLogDir) {
        $logs += Get-ChildItem $AppLogDir -Filter *.log -ErrorAction SilentlyContinue
    }
    $pgLogDir = Join-Path $UserDataRoot "postgres-logs"
    if (Test-Path $pgLogDir) {
        $logs += Get-ChildItem $pgLogDir -Filter *.log -ErrorAction SilentlyContinue
    }
    if ($logs.Count -gt 0) {
        foreach ($log in $logs) {
            Write-Host "----- $($log.FullName) (tail 60) -----"
            Get-Content $log.FullName -Tail 60 -ErrorAction SilentlyContinue | Write-Host
        }
    } else {
        Write-Host "[DIAG] no log files under $AppLogDir or $pgLogDir"
    }
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
        # Inno Setup 的卸载器是 GUI 程序，PowerShell `&` 调用不会等待其结束；
        # 必须 -Wait，否则后续安装/启动会与卸载并行，读到半删除的安装目录。
        Start-Process -FilePath $uninstaller -ArgumentList '/SILENT', '/NORESTART' -Wait | Out-Null
        Start-Sleep -Seconds 5
    }
    if (Test-Path $AppInstallDir) {
        Remove-Item -Recurse -Force $AppInstallDir -ErrorAction SilentlyContinue
    }
    # 同一 job 的前置步骤 `Smoke Test - Cold Start` 会用同一用户数据目录启动 dist 版 exe 并被强杀，
    # 留下未完成 initdb 的 postgres 数据目录；若不清理，安装包启动时 sidecar 无法就绪。
    # 本脚本验证的是「全新安装 → 崩溃恢复」，必须从干净用户数据目录开始。
    if (Test-Path $UserDataRoot) {
        Remove-Item -Recurse -Force $UserDataRoot -ErrorAction SilentlyContinue
    }
    Write-Ok "Environment cleaned"

    # STEP 2: 安装 embedded variant
    Write-Step "2. Install embedded variant"
    # Inno Setup 安装器是 GUI 程序，PowerShell `&` 调用立即返回、不等待解包完成；
    # 5s 后仅凭 EXE 存在就启动，会在 _internal 尚未解包完时启动主程序
    # （PyInstaller onedir 缺 python313.dll 等 → 进程无日志静默退出，
    #  v0.11.1/v0.11.2 tag run 的 crash_recovery 失败即此原因）。必须 -Wait 并校验退出码。
    $installerProc = Start-Process -FilePath $InstallerPath -ArgumentList '/SILENT', '/NORESTART' -PassThru -Wait
    Assert-True ($installerProc.ExitCode -eq 0) "Installer finished (exit code: $($installerProc.ExitCode))"
    Assert-True (Test-Path $AppExePath) "App installed: $AppExePath"

    # STEP 3: 启动 qTrading，等待 sidecar ready
    Write-Step "3. Start qTrading & wait for sidecar ready"
    $proc = Start-Process -FilePath $AppExePath -PassThru
    Start-Sleep -Seconds 3
    if ($proc.HasExited) {
        # 主进程启动即退出：通常是安装不完整或启动期异常，先 dump 诊断再断言，避免只看到超时
        Write-Host "[DIAG] app process exited early with code $($proc.ExitCode)"
        Write-Diagnostics "app process exited immediately after start (code $($proc.ExitCode))"
    }
    $ready = Wait-SidecarReady -TimeoutSec 60
    if (-not $ready) {
        # 主进程存活但 sidecar 未出现：dump 安装的 sidecar 文件与主程序/sidecar 日志便于定位
        # （主程序若在启动阶段抛 EmbeddedPostgresStartError 会停在 PreInitErrorView，不写 stdout）
        Write-Diagnostics "sidecar not ready within 60s after app start"
    }
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
