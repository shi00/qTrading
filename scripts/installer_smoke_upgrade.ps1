# installer_smoke_upgrade.ps1
# Phase 5 DoD §17.5 升级验证（保留数据）smoke test 脚本
# 用法: powershell -File scripts/installer_smoke_upgrade.ps1 -InstallerPath <path> [-OldVersion 0.9.0]
# 前置: 已发布 OldVersion 的 embedded variant GitHub Release，且本机已登录 gh CLI

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath,

    [Parameter(Mandatory = $false)]
    [string]$OldVersion = "0.9.0"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# ---- 路径常量 ----
$AppInstallDir = Join-Path $env:LOCALAPPDATA "Programs\AStockScreener"
$AppExePath = Join-Path $AppInstallDir "AStockScreener.exe"
# PGDATA 路径以 resources/maintenance/README-maintenance.md 为准（platformdirs 默认）
$PgDataDir = Join-Path $env:LOCALAPPDATA "qTrading\postgres\17\data"

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
Write-Host "=== Phase 5 Upgrade Smoke Test ===" -ForegroundColor Yellow
Write-Host "InstallerPath: $InstallerPath"
Write-Host "OldVersion:    $OldVersion"
Write-Host "AppInstallDir: $AppInstallDir"
Write-Host "PgDataDir:     $PgDataDir"

if (-not (Test-Path $InstallerPath)) {
    Write-Fail "InstallerPath not found: $InstallerPath"
    exit 1
}

try {
    # STEP 1: 清理环境（确保旧版本不存在）
    Write-Step "1. Cleanup existing installation"
    Stop-QtradingGraceful
    $uninstaller = Join-Path $AppInstallDir "unins000.exe"
    if (Test-Path $uninstaller) {
        & $uninstaller /SILENT /NORESTART 2>&1 | Out-Null
        Start-Sleep -Seconds 5
    }
    if (Test-Path $AppInstallDir) {
        Remove-Item -Recurse -Force $AppInstallDir -ErrorAction SilentlyContinue
    }
    # 注意：不删除 PgDataDir，保留以验证升级保留数据；但首次运行需清空以建立已知基线
    if (Test-Path $PgDataDir) {
        Remove-Item -Recurse -Force $PgDataDir -ErrorAction SilentlyContinue
    }
    Write-Ok "Environment cleaned"

    # STEP 2: 下载并安装旧版本
    Write-Step "2. Download & install old version v$OldVersion"
    $oldInstaller = "$env:TEMP\AStockScreener-Setup-embedded-$OldVersion.exe"
    Write-Host "  Downloading from GitHub Release v$OldVersion..."
    gh release download "v$OldVersion" --repo $env:GITHUB_REPOSITORY `
        --pattern "AStockScreener-Setup-embedded.exe" `
        --output $oldInstaller 2>&1 | Out-Null
    if (-not (Test-Path $oldInstaller)) {
        throw "Failed to download old version installer v$OldVersion"
    }
    & $oldInstaller /SILENT /NORESTART
    Start-Sleep -Seconds 5
    Assert-True (Test-Path $AppExePath) "Old version installed: $AppExePath"

    # STEP 3: 启动旧版 qTrading，等待 sidecar ready
    Write-Step "3. Start old version & wait for sidecar ready"
    $proc = Start-Process -FilePath $AppExePath -PassThru
    $ready = Wait-SidecarReady -TimeoutSec 60
    Assert-True $ready "Sidecar process started (old version)"

    # STEP 4: 模拟用户关闭
    Write-Step "4. Stop qTrading (simulate user close)"
    Stop-QtradingGraceful
    Start-Sleep -Seconds 3
    Write-Ok "qTrading stopped"

    # STEP 5: 验证 PGDATA 目录已建立且非空
    Write-Step "5. Verify PGDATA directory exists after old version run"
    Assert-True (Test-Path $PgDataDir) "PGDATA dir exists: $PgDataDir"
    $fileCount = (Get-ChildItem -Path $PgDataDir -Recurse -ErrorAction SilentlyContinue | Measure-Object).Count
    Assert-True ($fileCount -gt 0) "PGDATA dir non-empty (file count: $fileCount)"

    # STEP 6: 安装新版本（覆盖升级）
    Write-Step "6. Install new version (in-place upgrade)"
    & $InstallerPath /SILENT /NORESTART
    Start-Sleep -Seconds 5
    Assert-True (Test-Path $AppExePath) "New version installed (overwritten)"

    # STEP 7: 验证 PGDATA 目录保留且非空（核心断言）
    Write-Step "7. Verify PGDATA preserved after upgrade"
    Assert-True (Test-Path $PgDataDir) "PGDATA dir still exists: $PgDataDir"
    $fileCountAfter = (Get-ChildItem -Path $PgDataDir -Recurse -ErrorAction SilentlyContinue | Measure-Object).Count
    Assert-True ($fileCountAfter -gt 0) "PGDATA dir still non-empty (file count: $fileCountAfter)"
    Write-Host "  File count before upgrade: $fileCount"
    Write-Host "  File count after upgrade:  $fileCountAfter"

    # STEP 8: 启动新版本验证 sidecar 可正常启动（数据可加载）
    Write-Step "8. Start new version & verify sidecar starts (data loads)"
    $proc2 = Start-Process -FilePath $AppExePath -PassThru
    $ready2 = Wait-SidecarReady -TimeoutSec 60
    Assert-True $ready2 "Sidecar process started (new version, existing data)"
    Stop-QtradingGraceful

    Write-Host "`n=== UPGRADE SMOKE TEST PASSED ===" -ForegroundColor Green
    exit 0
}
catch {
    Write-Host "`n=== UPGRADE SMOKE TEST FAILED ===" -ForegroundColor Red
    Write-Host "Error: $_" -ForegroundColor Red
    Write-Host "Stack: $($_.ScriptStackTrace)" -ForegroundColor Red
    # 清理残留进程
    Stop-QtradingGraceful
    exit 1
}
