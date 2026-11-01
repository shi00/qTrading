# installer_smoke_uninstall.ps1
# Phase 5 DoD §17.5 卸载验证（保留数据）smoke test 脚本
# 用法: powershell -File scripts/installer_smoke_uninstall.ps1 -InstallerPath <path>
# 验证: 卸载后 PGDATA 目录保留（不删除用户数据）

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
$UninstallerPath = Join-Path $AppInstallDir "unins000.exe"
$PgDataDir = Join-Path $env:LOCALAPPDATA "qTrading\postgres\16\data"

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
Write-Host "=== Phase 5 Uninstall Smoke Test ===" -ForegroundColor Yellow
Write-Host "InstallerPath: $InstallerPath"
Write-Host "AppInstallDir: $AppInstallDir"
Write-Host "PgDataDir:     $PgDataDir"

if (-not (Test-Path $InstallerPath)) {
    Write-Fail "InstallerPath not found: $InstallerPath"
    exit 1
}

try {
    # STEP 1: 清理环境
    Write-Step "1. Cleanup existing installation"
    Stop-QtradingGraceful
    if (Test-Path $AppInstallDir) {
        $existingUninstaller = Join-Path $AppInstallDir "unins000.exe"
        if (Test-Path $existingUninstaller) {
            # Inno Setup 卸载器是 GUI 程序，`&` 调用不等待其结束；必须 -Wait。
            Start-Process -FilePath $existingUninstaller -ArgumentList '/SILENT', '/NORESTART' -Wait | Out-Null
            Start-Sleep -Seconds 5
        }
        Remove-Item -Recurse -Force $AppInstallDir -ErrorAction SilentlyContinue
    }
    if (Test-Path $PgDataDir) {
        Remove-Item -Recurse -Force $PgDataDir -ErrorAction SilentlyContinue
    }
    Write-Ok "Environment cleaned"

    # STEP 2: 安装 embedded variant
    Write-Step "2. Install embedded variant"
    # Inno Setup 安装器是 GUI 程序，`&` 调用立即返回、不等待解包完成；必须 -Wait 并校验退出码，
    # 否则后续断言会读到半解包的安装目录（同 crash_recovery / running_app 的既有做法）。
    $installerProc = Start-Process -FilePath $InstallerPath -ArgumentList '/SILENT', '/NORESTART' -PassThru -Wait
    Assert-True ($installerProc.ExitCode -eq 0) "Installer finished (exit code: $($installerProc.ExitCode))"
    Assert-True (Test-Path $AppExePath) "App installed: $AppExePath"
    Assert-True (Test-Path $UninstallerPath) "Uninstaller exists: $UninstallerPath"

    # STEP 3: 启动 qTrading，等待 sidecar ready
    Write-Step "3. Start qTrading & wait for sidecar ready"
    $proc = Start-Process -FilePath $AppExePath -PassThru
    $ready = Wait-SidecarReady -TimeoutSec 60
    Assert-True $ready "Sidecar process started"

    # STEP 4: 停止 qTrading（卸载前必须关闭，否则文件占用）
    Write-Step "4. Stop qTrading before uninstall"
    Stop-QtradingGraceful
    Start-Sleep -Seconds 3
    Write-Ok "qTrading stopped"

    # STEP 5: 验证 PGDATA 目录已建立
    Write-Step "5. Verify PGDATA directory exists before uninstall"
    Assert-True (Test-Path $PgDataDir) "PGDATA dir exists: $PgDataDir"
    $fileCountBefore = (Get-ChildItem -Path $PgDataDir -Recurse -ErrorAction SilentlyContinue | Measure-Object).Count
    Assert-True ($fileCountBefore -gt 0) "PGDATA dir non-empty (file count: $fileCountBefore)"

    # STEP 6: 卸载
    Write-Step "6. Uninstall qTrading"
    $uninstallerProc = Start-Process -FilePath $UninstallerPath -ArgumentList '/SILENT', '/NORESTART' -PassThru -Wait
    Assert-True ($uninstallerProc.ExitCode -eq 0) "Uninstaller finished (exit code: $($uninstallerProc.ExitCode))"
    # Inno 卸载器会先把自身复制到 %TEMP% 再执行，启动进程可能先退出：保留结算等待再断言删除结果
    Start-Sleep -Seconds 5
    Assert-True (-not (Test-Path $UninstallerPath)) "Uninstaller removed (uninstall completed)"
    Assert-True (-not (Test-Path $AppExePath)) "App EXE removed"

    # STEP 7: 验证 PGDATA 目录保留（核心断言）
    Write-Step "7. Verify PGDATA preserved after uninstall"
    Assert-True (Test-Path $PgDataDir) "PGDATA dir preserved: $PgDataDir"
    $fileCountAfter = (Get-ChildItem -Path $PgDataDir -Recurse -ErrorAction SilentlyContinue | Measure-Object).Count
    Assert-True ($fileCountAfter -gt 0) "PGDATA dir still non-empty (file count: $fileCountAfter)"
    Write-Host "  File count before uninstall: $fileCountBefore"
    Write-Host "  File count after uninstall:  $fileCountAfter"

    Write-Host "`n=== UNINSTALL SMOKE TEST PASSED ===" -ForegroundColor Green
    exit 0
}
catch {
    Write-Host "`n=== UNINSTALL SMOKE TEST FAILED ===" -ForegroundColor Red
    Write-Host "Error: $_" -ForegroundColor Red
    Write-Host "Stack: $($_.ScriptStackTrace)" -ForegroundColor Red
    Stop-QtradingGraceful
    exit 1
}
