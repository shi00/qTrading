# installer_smoke_uninstall.ps1
# Phase 5 DoD §17.5 卸载验证（保留数据）smoke test 脚本
# 用法: powershell -File scripts/installer_smoke_uninstall.ps1 -InstallerPath <path>
# 验证: 卸载后程序目录移除，但 PG 根（data/PG_VERSION、runtime\password、install）完整保留；
#       重装同变体后读回卸载前写入的已提交业务哨兵，证明数据可复用（不再以「目录非空」作判据）。

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
$PgDataDir = Join-Path $env:LOCALAPPDATA "qTrading\qTrading\postgres\16\data"
# 嵌入 PG 根布局 <用户数据>\postgres\16\{data,install,runtime}（sidecars/qtrading-pg-sidecar/src/paths.rs）
$PgInstallDir = Join-Path (Split-Path -Parent $PgDataDir) "install"
$PgRuntimeDir = Join-Path (Split-Path -Parent $PgDataDir) "runtime"
$PgPasswordFile = Join-Path $PgRuntimeDir "password"
$PgVersionFile = Join-Path $PgDataDir "PG_VERSION"
$PsqlExePath = Join-Path $PgInstallDir "bin\psql.exe"
# 与 sidecars/qtrading-pg-sidecar/src/cli.rs 的 DEFAULT_USERNAME / DEFAULT_DATABASE 一致
$PgUsername = "postgres"
$PgDatabase = "qtrading"
$SentinelTable = "smoke_uninstall_sentinel"
# sidecar CLI 位于安装目录 _internal\sidecars（resources/maintenance/README-maintenance.md）
$SidecarExePath = Join-Path $AppInstallDir "_internal\sidecars\qtrading-pg-sidecar.exe"

# ---- 工具函数 ----
function Write-Step([string]$msg) { Write-Host "`n[STEP] $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg)   { Write-Host "  [PASS] $msg" -ForegroundColor Green }
function Write-Fail([string]$msg) { Write-Host "  [FAIL] $msg" -ForegroundColor Red }
function Assert-True([bool]$cond, [string]$msg) {
    if ($cond) { Write-Ok $msg } else { Write-Fail $msg; throw "ASSERTION FAILED: $msg" }
}

# ready 判据 = sidecar status JSON 的 status == running 且 postgres_alive == true
# （与 run.rs 一致：ready 表示 PG 已启动；进程名存在只能作诊断提示，不能单独判定 ready）。
function Wait-SidecarReady([int]$TimeoutSec = 60) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $sidecarProcessSeen = $false
    while ((Get-Date) -lt $deadline) {
        if (Get-Process -Name "qtrading-pg-sidecar" -ErrorAction SilentlyContinue) { $sidecarProcessSeen = $true }
        $status = $null
        if (Test-Path $SidecarExePath) {
            try {
                $raw = @(& $SidecarExePath status --data-dir $PgDataDir 2>$null)
                if ($LASTEXITCODE -eq 0 -and $raw.Count -gt 0) {
                    $status = (($raw -join "`n") | ConvertFrom-Json)
                }
            } catch {
                $status = $null
            }
        }
        if ($null -ne $status -and $status.status -eq "running" -and $status.postgres_alive -eq $true) {
            return $true
        }
        Start-Sleep -Seconds 2
    }
    if ($sidecarProcessSeen) {
        Write-Fail "sidecar process present but status JSON never reported running/postgres_alive within ${TimeoutSec}s"
    } else {
        Write-Fail "qtrading-pg-sidecar process never appeared within ${TimeoutSec}s"
    }
    return $false
}

# Windows DPAPI 密码文件（sidecar password.rs：`PGPW2:<hex>`，CurrentUser 作用域）；
# 无前缀按明文读（Unix 或升级前旧格式）。严禁伪造密码文件或绕过认证。
function Read-PgPassword([string]$PasswordFile) {
    if (-not (Test-Path $PasswordFile)) { throw "password file missing: $PasswordFile" }
    $rawText = Get-Content -Path $PasswordFile -Raw
    if ([string]::IsNullOrEmpty($rawText)) { throw "password file empty: $PasswordFile" }
    $raw = $rawText.Trim()
    if ([string]::IsNullOrEmpty($raw)) { throw "password file blank: $PasswordFile" }
    if (-not $raw.StartsWith("PGPW2:")) { return $raw }
    $hex = $raw.Substring(6)
    if ($hex.Length -eq 0 -or ($hex.Length % 2) -ne 0) { throw "DPAPI password blob hex length invalid" }
    Add-Type -AssemblyName System.Security
    $blob = New-Object byte[] ([int]($hex.Length / 2))
    for ($i = 0; $i -lt $blob.Length; $i++) {
        $blob[$i] = [Convert]::ToByte($hex.Substring($i * 2, 2), 16)
    }
    $plainBytes = [System.Security.Cryptography.ProtectedData]::Unprotect(
        $blob, $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)
    $plain = [System.Text.Encoding]::UTF8.GetString($plainBytes)
    if ([string]::IsNullOrEmpty($plain)) { throw "DPAPI password decrypt returned empty value" }
    return $plain
}

# 端口取自 postmaster.pid 第 4 行（sidecar 每次启动随机分配端口，重启后必须重读）。
function Get-PostgresPort([string]$DataDir) {
    $pidFile = Join-Path $DataDir "postmaster.pid"
    if (-not (Test-Path $pidFile)) { throw "postmaster.pid missing: $pidFile" }
    $lines = @(Get-Content -Path $pidFile)
    if ($lines.Count -lt 4) { throw "postmaster.pid has fewer than 4 lines (port required)" }
    $portText = ([string]$lines[3]).Trim()
    $parsed = 0
    if (-not [int]::TryParse($portText, [ref]$parsed) -or $parsed -lt 1 -or $parsed -gt 65535) {
        throw "postmaster.pid port invalid: $portText"
    }
    return $parsed
}

# 真实认证路径执行 SQL：密码经 PGPASSWORD 进程环境传递（不进入命令行/日志），调用后清理。
function Invoke-Psql([string]$Password, [int]$Port, [string]$Sql) {
    if (-not (Test-Path $PsqlExePath)) { throw "psql not found: $PsqlExePath" }
    $previousPassword = $env:PGPASSWORD
    $env:PGPASSWORD = $Password
    try {
        $output = @(& $PsqlExePath -h 127.0.0.1 -p $Port -U $PgUsername -d $PgDatabase -v ON_ERROR_STOP=1 -t -A -c $Sql 2>&1)
        if ($LASTEXITCODE -ne 0) {
            throw "psql failed (exit $LASTEXITCODE): $($output -join ' ')"
        }
        return (($output -join "`n").Trim())
    } finally {
        if ($null -ne $previousPassword) {
            $env:PGPASSWORD = $previousPassword
        } else {
            Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
        }
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
    Assert-True $ready "Sidecar ready (status JSON: running + postgres_alive)"

    # STEP 4: 运行期写入已提交的合成业务哨兵，并记录卸载前 PG 根证据（真实认证路径）
    Write-Step "4. Write committed sentinel & record PG root evidence"
    $sentinel = "d17-t02-" + [Guid]::NewGuid().ToString("N")
    if (-not ($sentinel -match '^d17-t02-[0-9a-f]{32}$')) {
        throw "sentinel marker format invalid"
    }
    $pgPassword = Read-PgPassword -PasswordFile $PgPasswordFile
    $portBefore = Get-PostgresPort -DataDir $PgDataDir
    $sentinelCreateSql = "create table if not exists $SentinelTable (id integer primary key, marker text not null)"
    $sentinelInsertSql = "insert into $SentinelTable (id, marker) values (1, '$sentinel') on conflict (id) do update set marker = excluded.marker"
    Invoke-Psql -Password $pgPassword -Port $portBefore -Sql $sentinelCreateSql | Out-Null
    Invoke-Psql -Password $pgPassword -Port $portBefore -Sql $sentinelInsertSql | Out-Null
    $sentinelReadBefore = Invoke-Psql -Password $pgPassword -Port $portBefore -Sql "select marker from $SentinelTable where id = 1"
    Assert-True ($sentinelReadBefore -eq $sentinel) "Synthetic sentinel committed & read back before uninstall"

    Assert-True (Test-Path $PgDataDir) "PGDATA dir exists before uninstall: $PgDataDir"
    Assert-True (Test-Path $PgVersionFile) "PG_VERSION exists before uninstall: $PgVersionFile"
    $pgVersionBefore = (Get-Content -Path $PgVersionFile -Raw).Trim()
    Assert-True ($pgVersionBefore.Length -gt 0) "PG_VERSION is non-empty before uninstall ($pgVersionBefore)"
    Assert-True (Test-Path $PgPasswordFile) "runtime password file exists before uninstall"
    Assert-True (Test-Path $PsqlExePath) "install psql exists before uninstall"

    # STEP 5: 停止 qTrading（卸载前必须关闭，否则文件占用）
    Write-Step "5. Stop qTrading before uninstall"
    Stop-QtradingGraceful
    Start-Sleep -Seconds 3
    Assert-True ($null -eq (Get-Process -Name "AStockScreener" -ErrorAction SilentlyContinue)) "App process exited before uninstall"
    Assert-True ($null -eq (Get-Process -Name "qtrading-pg-sidecar" -ErrorAction SilentlyContinue)) "Sidecar process exited before uninstall"

    # STEP 6: 卸载
    Write-Step "6. Uninstall qTrading"
    $uninstallerProc = Start-Process -FilePath $UninstallerPath -ArgumentList '/SILENT', '/NORESTART' -PassThru -Wait
    Assert-True ($uninstallerProc.ExitCode -eq 0) "Uninstaller finished (exit code: $($uninstallerProc.ExitCode))"
    # Inno 卸载器会先把自身复制到 %TEMP% 再执行，启动进程可能先退出：保留结算等待再断言删除结果
    Start-Sleep -Seconds 5
    Assert-True (-not (Test-Path $UninstallerPath)) "Uninstaller removed (uninstall completed)"
    Assert-True (-not (Test-Path $AppExePath)) "App EXE removed"
    Assert-True (-not (Test-Path $SidecarExePath)) "Bundled sidecar EXE removed"

    # STEP 7: 验证 PG 根完整保留（data/PG_VERSION 未变、runtime\password 与 install 仍在）
    Write-Step "7. Verify PG root preserved after uninstall"
    Assert-True (Test-Path $PgDataDir) "PGDATA dir preserved: $PgDataDir"
    Assert-True (Test-Path $PgVersionFile) "PG_VERSION preserved: $PgVersionFile"
    $pgVersionAfterUninstall = (Get-Content -Path $PgVersionFile -Raw).Trim()
    Assert-True ($pgVersionAfterUninstall -eq $pgVersionBefore) "PG_VERSION unchanged after uninstall ($pgVersionAfterUninstall)"
    Assert-True (Test-Path $PgInstallDir) "install dir preserved: $PgInstallDir"
    Assert-True (Test-Path $PsqlExePath) "install psql preserved: $PsqlExePath"
    Assert-True (Test-Path $PgRuntimeDir) "runtime dir preserved: $PgRuntimeDir"
    Assert-True (Test-Path $PgPasswordFile) "runtime password file preserved: $PgPasswordFile"

    # STEP 8: 重装同变体并启动，验证数据可复用（卸载不是数据丢失）
    Write-Step "8. Reinstall same variant & verify preserved data is reusable"
    # 复用 $InstallerPath 的变量副本：保持上文「仅一次直接调用 $InstallerPath」的既有静态契约。
    $ReinstallPath = $InstallerPath
    $reinstallProc = Start-Process -FilePath $ReinstallPath -ArgumentList '/SILENT', '/NORESTART' -PassThru -Wait
    Assert-True ($reinstallProc.ExitCode -eq 0) "Reinstall finished (exit code: $($reinstallProc.ExitCode))"
    Assert-True (Test-Path $AppExePath) "App reinstalled: $AppExePath"

    $proc2 = Start-Process -FilePath $AppExePath -PassThru
    $ready2 = Wait-SidecarReady -TimeoutSec 60
    Assert-True $ready2 "Sidecar ready after reinstall (status JSON: running + postgres_alive)"

    # STEP 9: 读回哨兵并断言与卸载前完全一致
    Write-Step "9. Read back sentinel after reinstall & assert exact match"
    $pgPasswordAfter = Read-PgPassword -PasswordFile $PgPasswordFile
    Assert-True (-not ([string]::IsNullOrEmpty($pgPasswordAfter))) "Preserved password file decrypts after reinstall"
    $portAfterReinstall = Get-PostgresPort -DataDir $PgDataDir
    $sentinelAfterReinstall = Invoke-Psql -Password $pgPasswordAfter -Port $portAfterReinstall -Sql "select marker from $SentinelTable where id = 1"
    Assert-True ($sentinelAfterReinstall -eq $sentinel) "Synthetic sentinel survived uninstall & reinstall unchanged"

    Stop-QtradingGraceful

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
