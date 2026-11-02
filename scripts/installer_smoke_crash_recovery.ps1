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
# 用户数据根目录（platformdirs 在 Windows 追加 author=appname，故为 qTrading\qTrading 两层）：
# 嵌入 PG 数据（qTrading\qTrading\postgres\16\data）与 sidecar 日志（qTrading\qTrading\postgres-logs）
$UserDataRoot = Join-Path $env:LOCALAPPDATA "qTrading\qTrading"
# 主程序日志目录（utils/logger.py 的 LOG_DIR）
$AppLogDir = Join-Path $env:LOCALAPPDATA "AStockScreener\logs"
# 嵌入 PG 数据目录（<PGDATA 父目录> 下为 install 与 runtime，见 sidecar paths.rs Layout）
$PgDataDir = Join-Path $UserDataRoot "postgres\16\data"
$PgInstallDir = Join-Path (Split-Path -Parent $PgDataDir) "install"
$PgRuntimeDir = Join-Path (Split-Path -Parent $PgDataDir) "runtime"
$PgPasswordFile = Join-Path $PgRuntimeDir "password"
$PsqlExePath = Join-Path $PgInstallDir "bin\psql.exe"
# 与 sidecars/qtrading-pg-sidecar/src/cli.rs 的 DEFAULT_USERNAME / DEFAULT_DATABASE 一致
$PgUsername = "postgres"
$PgDatabase = "qtrading"
$SentinelTable = "smoke_crash_recovery_sentinel"
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
    Assert-True $ready "Sidecar ready (status JSON: running + postgres_alive)"

    # STEP 4: 崩溃前写入「已提交的合成业务哨兵」（真实认证路径，非敏感数据；禁止伪造密码/绕过认证）
    Write-Step "4. Write committed synthetic business sentinel (authenticated psql)"
    $sentinel = "d17-t01-" + [Guid]::NewGuid().ToString("N")
    $pgPassword = Read-PgPassword -PasswordFile $PgPasswordFile
    $portBefore = Get-PostgresPort -DataDir $PgDataDir
    $sentinelCreateSql = "create table if not exists $SentinelTable (id integer primary key, marker text not null)"
    $sentinelInsertSql = "insert into $SentinelTable (id, marker) values (1, '$sentinel') on conflict (id) do update set marker = excluded.marker"
    Invoke-Psql -Password $pgPassword -Port $portBefore -Sql $sentinelCreateSql | Out-Null
    Invoke-Psql -Password $pgPassword -Port $portBefore -Sql $sentinelInsertSql | Out-Null
    $sentinelReadBefore = Invoke-Psql -Password $pgPassword -Port $portBefore -Sql "select marker from $SentinelTable where id = 1"
    Assert-True ($sentinelReadBefore -eq $sentinel) "Synthetic sentinel committed & read back before crash"

    # STEP 5: 记录 sidecar 与 postgres 进程
    Write-Step "5. Record sidecar & postgres processes"
    $sidecarBefore = Get-Process -Name "qtrading-pg-sidecar" -ErrorAction SilentlyContinue
    Assert-True ($null -ne $sidecarBefore) "Sidecar process exists before crash"
    Write-Host "  Sidecar PID(s) before crash: $($sidecarBefore.Id -join ',')"

    $postgresBefore = Get-Process -Name "postgres" -ErrorAction SilentlyContinue
    if ($postgresBefore) {
        Write-Host "  Postgres PID(s) before crash: $($postgresBefore.Id -join ',')"
    } else {
        Write-Host "  Postgres: (no postgres.exe process detected, may be embedded in sidecar)"
    }

    # STEP 6: 失败注入 - 强制 kill 主进程（不触发优雅停机）
    Write-Step "6. FAILURE INJECTION: Force kill main qTrading process (simulate crash)"
    if ($proc -and -not $proc.HasExited) {
        # 使用 Stop-Process -Force 模拟进程崩溃，不经过 qTrading 的 shutdown handler
        Stop-Process -Id $proc.Id -Force
        Write-Ok "Main process killed (PID: $($proc.Id))"
    }

    # STEP 7: 等待 sidecar 自行退出（父进程兜底机制：`--parent-pid` 每 500ms 轮询 + stdin EOF）
    # 注意：sidecar 的父进程检测循环在 postgres ready（打印 ready JSON）之后才启动；
    # 首次启动（initdb）期间父进程被杀时，sidecar 会先完成 initdb 再检测到父进程消失，
    # 实测可耗时数十秒，因此上限取 180s（原先固定 5s 会必然失败，且代码中并无 Job Object）。
    Write-Step "7. Wait for sidecar to exit after parent crash (up to 180s)"
    $waitStarted = Get-Date
    $deadline = $waitStarted.AddSeconds(180)
    $sidecarAfter = Get-Process -Name "qtrading-pg-sidecar" -ErrorAction SilentlyContinue
    while ($sidecarAfter -and (Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        $sidecarAfter = Get-Process -Name "qtrading-pg-sidecar" -ErrorAction SilentlyContinue
    }
    $waitedSecs = [int]((Get-Date) - $waitStarted).TotalSeconds
    if ($sidecarAfter) {
        Write-Host "  [DIAG] sidecar still alive after ${waitedSecs}s: PID(s) $($sidecarAfter.Id -join ',')"
        Write-Diagnostics "sidecar still alive 180s after parent crash"
    } else {
        Write-Host "  sidecar exited after ~${waitedSecs}s"
    }

    # STEP 8: 验证 sidecar 无残留（核心断言：父进程崩溃后 sidecar 最终自行退出）
    Write-Step "8. Verify no residual sidecar process (parent-loss watchdog exited it)"
    Assert-True ($null -eq $sidecarAfter) "No residual sidecar process after main crash"

    # STEP 9: 验证 postgres 无残留（sidecar 退出后由 graceful stop 关闭 postgres，留出停机余量）
    Write-Step "9. Verify no residual postgres process"
    $postgresAfter = Get-Process -Name "postgres" -ErrorAction SilentlyContinue
    $pgDeadline = (Get-Date).AddSeconds(30)
    while ($postgresAfter -and (Get-Date) -lt $pgDeadline) {
        Start-Sleep -Seconds 3
        $postgresAfter = Get-Process -Name "postgres" -ErrorAction SilentlyContinue
    }
    if ($postgresAfter) {
        Write-Host "  [DIAG] postgres still alive: PID(s) $($postgresAfter.Id -join ',')"
        Write-Diagnostics "postgres still alive 30s after sidecar exit"
    }
    Assert-True ($null -eq $postgresAfter) "No residual postgres process after main crash"

    # STEP 10: 崩溃/退出后重启应用，等待真实 ready（status JSON: running + postgres_alive）
    Write-Step "10. Restart qTrading & wait for real sidecar ready"
    $proc2 = Start-Process -FilePath $AppExePath -PassThru
    $ready2 = Wait-SidecarReady -TimeoutSec 60
    if (-not $ready2) {
        Write-Diagnostics "sidecar not ready within 60s after restart"
    }
    Assert-True $ready2 "Sidecar ready after restart (status JSON: running + postgres_alive)"

    # STEP 11: 读回哨兵并断言与崩溃前完全一致（已提交事务在崩溃恢复后仍可见）
    Write-Step "11. Read back sentinel after restart & assert exact match"
    $portAfterRestart = Get-PostgresPort -DataDir $PgDataDir
    $sentinelAfterRestart = Invoke-Psql -Password $pgPassword -Port $portAfterRestart -Sql "select marker from $SentinelTable where id = 1"
    Assert-True ($sentinelAfterRestart -eq $sentinel) "Synthetic sentinel survived crash & restart unchanged"

    Stop-QtradingGraceful
    Write-Host "`n=== CRASH RECOVERY SMOKE TEST PASSED ===" -ForegroundColor Green
    Write-Host "Verified: committed sentinel survives crash+restart; parent crash -> sidecar exits (parent-loss watchdog), postgres shut down" -ForegroundColor Green
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
