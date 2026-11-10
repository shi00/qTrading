# installer_smoke_upgrade.ps1
# Phase 5 DoD §17.5 升级验证（保留数据）smoke test 脚本
# 用法: powershell -File scripts/installer_smoke_upgrade.ps1 -InstallerPath <path> [-OldVersion 0.9.0] [-OldVariant embedded-cpu] [-VerifyRollback]
# 前置: 已发布 OldVersion 的 embedded variant GitHub Release，且本机已登录 gh CLI
# OldVariant 对应 installer.iss 的 TargetVariant（现役 embedded-cpu / embedded-cuda；
# 历史版本若为 embedded 则显式传 -OldVariant embedded）。
# 判据: 旧版运行期经真实认证路径写入已提交业务哨兵，升级后读回完全一致，且 PG 根
#       （PG_VERSION / runtime\password / install）完整保留（不再以「目录非空」代表数据保留）。

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath,

    [Parameter(Mandatory = $false)]
    [string]$OldVersion = "0.9.0",

    [Parameter(Mandatory = $false)]
    [string]$OldVariant = "embedded-cpu",

    # G4 §205：回滚腿（候选包 → 旧恢复包）。默认关闭以保持既有本地用法不变；
    # CI 显式开启以同时覆盖正向升级与恢复路径。
    [Parameter(Mandatory = $false)]
    [switch]$VerifyRollback
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# ---- 路径常量 ----
$AppInstallDir = Join-Path $env:LOCALAPPDATA "Programs\AStockScreener"
$AppExePath = Join-Path $AppInstallDir "AStockScreener.exe"
# PGDATA 路径以 resources/maintenance/README-maintenance.md 为准（platformdirs 默认；Windows 追加 author=appname，故为两层）
$PgDataDir = Join-Path $env:LOCALAPPDATA "qTrading\qTrading\postgres\16\data"
# 嵌入 PG 根布局 <用户数据>\postgres\16\{data,install,runtime}（sidecars/qtrading-pg-sidecar/src/paths.rs）
$PgInstallDir = Join-Path (Split-Path -Parent $PgDataDir) "install"
$PgRuntimeDir = Join-Path (Split-Path -Parent $PgDataDir) "runtime"
$PgPasswordFile = Join-Path $PgRuntimeDir "password"
$PgVersionFile = Join-Path $PgDataDir "PG_VERSION"
$PsqlExePath = Join-Path $PgInstallDir "bin\psql.exe"
# sidecar 自述状态（paths.rs：state_file = runtime\state.json；run.rs 启动失败时写
# status=failed 与 last_start_error），用于诊断「等库超时」的真实原因。
$PgStateFile = Join-Path $PgRuntimeDir "state.json"
# 与 sidecars/qtrading-pg-sidecar/src/cli.rs 的 DEFAULT_USERNAME / DEFAULT_DATABASE 一致
$PgUsername = "postgres"
$PgDatabase = "qtrading"
$SentinelTable = "smoke_upgrade_sentinel"
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
# 超时 180s：run 42357815491 实证 STEP 1 清空 PGDATA 后旧版冷启动（完整 initdb）在慢 runner
# 上超过 60s（上次成功已耗时 50s 逼近上限），60s 属边缘性超时，非产品缺陷。
function Wait-SidecarReady([int]$TimeoutSec = 180) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $sidecarProcessSeen = $false
    $lastRaw = $null
    while ((Get-Date) -lt $deadline) {
        if (Get-Process -Name "qtrading-pg-sidecar" -ErrorAction SilentlyContinue) { $sidecarProcessSeen = $true }
        $status = $null
        if (Test-Path $SidecarExePath) {
            try {
                $raw = @(& $SidecarExePath status --data-dir $PgDataDir 2>$null)
                if ($LASTEXITCODE -eq 0 -and $raw.Count -gt 0) {
                    $lastRaw = ($raw -join "`n")
                    $status = ($lastRaw | ConvertFrom-Json)
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
    if ($null -ne $lastRaw) {
        Write-Fail "last sidecar status output: $lastRaw"
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
function Invoke-Psql([string]$Password, [int]$Port, [string]$Sql, [string]$Database = $PgDatabase) {
    if (-not (Test-Path $PsqlExePath)) { throw "psql not found: $PsqlExePath" }
    $previousPassword = $env:PGPASSWORD
    $env:PGPASSWORD = $Password
    try {
        $output = @(& $PsqlExePath -h 127.0.0.1 -p $Port -U $PgUsername -d $Database -v ON_ERROR_STOP=1 -t -A -c $Sql 2>&1)
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

# sidecar 的 `status` 在 postmaster 进程存活时即推导为 running（commands.rs derive_status），
# 早于 run.rs 步骤 10 的 ensure_database。冒烟若只等 sidecar ready 就写哨兵，会与建库竞态
# （run 42324064125 因此报 database "qtrading" does not exist）。故额外轮询业务库是否已存在。
# 超时不再静默返回：run 42750440663 中两个 embedded 变体只得到同一句断言文案，无法区分
# 「认证失败 / 连接被拒 / sidecar 建库失败后退出」，故必须带出末次错误与 sidecar 自述状态。
# 密码必须在轮询内每次重读：run 42764622846 取证显示 sidecar 已 running 且无 last_start_error，
# 但 psql 持续 `password authentication failed`——旧版 sidecar 在自述 running 之后才落盘自己
# （重新生成）的密码，启动前缓存的密码已过期，被误判成「业务库未创建」。
function Wait-AppDatabaseReady([string]$PasswordFile, [int]$TimeoutSec = 120) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $attempts = 0
    $lastError = $null
    $port = $null
    $password = $null
    $previousPassword = $null
    while ((Get-Date) -lt $deadline) {
        $attempts++
        try {
            $password = Read-PgPassword -PasswordFile $PasswordFile
            if ($null -ne $previousPassword -and $password -ne $previousPassword) {
                # 只报「发生了轮换」，不打印密码本身（R9）。
                Write-Ok "password file rotated during startup (attempt $attempts)"
            }
            $previousPassword = $password
            $port = Get-PostgresPort -DataDir $PgDataDir
            $exists = Invoke-Psql -Password $password -Port $port -Database "postgres" `
                -Sql "select 1 from pg_database where datname = '$PgDatabase'"
            if ($exists -eq "1") { return $true }
            $lastError = "postgres reachable (port $port) but pg_database has no '$PgDatabase' row"
        } catch {
            # 启动期连接可能被拒或 postmaster.pid 竞争：保留末次错误用于超时取证，继续轮询
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Seconds 2
    }
    Write-Fail "application database '$PgDatabase' not ready within ${TimeoutSec}s ($attempts attempts, last port: $port)"
    Write-Fail "last probe error: $lastError"
    $appAlive = @(Get-Process -Name "AStockScreener" -ErrorAction SilentlyContinue).Count -gt 0
    $sidecarAlive = @(Get-Process -Name "qtrading-pg-sidecar" -ErrorAction SilentlyContinue).Count -gt 0
    Write-Fail "process alive: qTrading=$appAlive, qtrading-pg-sidecar=$sidecarAlive"
    Write-Fail "sidecar state: $(Get-SidecarStateDigest)"
    Write-EnvironmentSnapshot
    return $false
}

# 读 sidecar 的 state.json（status / last_start_error / port / postgres_pid）做超时取证；
# 文件缺失或解析失败同样如实报告，不得用空字符串让「无信息」冒充「无异常」。
function Get-SidecarStateDigest {
    if (-not (Test-Path $PgStateFile)) { return "state.json missing ($PgStateFile)" }
    try {
        $state = Get-Content -Path $PgStateFile -Raw | ConvertFrom-Json
    } catch {
        return "state.json unreadable: $($_.Exception.Message)"
    }
    $names = @($state.PSObject.Properties.Name)
    $fields = foreach ($name in @("status", "last_start_error", "port", "postgres_pid")) {
        if ($names -contains $name) { "$name=$($state.$name)" } else { "$name=<absent>" }
    }
    return ($fields -join ", ")
}

# 失败时刻的环境快照：进程命令行 / sidecar 日志尾部 / PG 根文件时间戳与凭据形态。
# run 42770696954 中 state.json 自述 running 且无 last_start_error，psql 却持续
# `password authentication failed`——两者不可能同时为真，仅记录 state 摘要无法定因，须区分：
#   (a) state.json / postmaster.pid 来自上一步 smoke（陈旧，指向已停止的旧集群）；
#   (b) 旧版实际使用了另一个 data dir / 密码文件（状态与探测目标错配）；
#   (c) 遗留 postgres 仍占端口（Stop-QtradingGraceful 只杀 sidecar，不连带其子进程）。
# 只输出路径、时间戳、命令行与密码文件形态（前缀/长度）；密码内容与解密结果不落日志（R9）。
function Write-EnvironmentSnapshot {
    $userRoot = Split-Path -Parent (Split-Path -Parent $PgDataDir)
    Write-Fail "user root: $userRoot (exists=$(Test-Path $userRoot))"
    $legacyRoot = Join-Path (Split-Path -Parent $userRoot) "postgres"
    Write-Fail "legacy single-layer root: $legacyRoot (exists=$(Test-Path $legacyRoot))"

    $procs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -in @("AStockScreener.exe", "qtrading-pg-sidecar.exe", "postgres.exe") })
    Write-Fail "process snapshot (count=$($procs.Count)):"
    foreach ($p in $procs) {
        $cmd = [string]$p.CommandLine
        if ([string]::IsNullOrEmpty($cmd)) { $cmd = "<unavailable>" }
        # 命令行可出现 --password-file 路径，但绝不允许出现明文密码（R9）。
        $cmd = [regex]::Replace($cmd, "(?i)--password\s+\S+", "--password <redacted>")
        Write-Fail "  $($p.Name) pid=$($p.ProcessId) started=$($p.CreationDate.ToUniversalTime().ToString('o')) cmd=$cmd"
    }

    $logPath = Join-Path $userRoot "postgres-logs\sidecar.log"
    if (Test-Path $logPath) {
        Write-Fail "sidecar.log tail 40 ($logPath):"
        Get-Content -Path $logPath -Tail 40 | ForEach-Object { Write-Fail "  | $_" }
    } else {
        Write-Fail "sidecar.log missing ($logPath)"
    }

    $pgBaseDir = Split-Path -Parent $PgDataDir
    foreach ($file in @($PgStateFile, (Join-Path $PgDataDir "postmaster.pid"), $PgPasswordFile,
            (Join-Path $pgBaseDir "install\.setup-complete"))) {
        if (Test-Path $file) {
            $item = Get-Item -Path $file
            Write-Fail "file: $file mtime=$($item.LastWriteTimeUtc.ToString('o')) size=$($item.Length)"
        } else {
            Write-Fail "file: $file missing"
        }
    }

    if (Test-Path $PgPasswordFile) {
        $rawPassword = (Get-Content -Path $PgPasswordFile -Raw).Trim()
        $format = if ($rawPassword.StartsWith("PGPW2:")) { "PGPW2(DPAPI)" } else { "plaintext" }
        Write-Fail "password file format: $format"
        try {
            Write-Fail "password file decrypted length: $((Read-PgPassword -PasswordFile $PgPasswordFile).Length)"
        } catch {
            Write-Fail "password file decrypt failed: $($_.Exception.Message)"
        }
    } else {
        Write-Fail "password file missing ($PgPasswordFile)"
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

# 读取已安装包声明的版本（Inno Setup 写入 HKCU 卸载项，PrivilegesRequired=lowest）。
# Inno Setup 的卸载项键名固定为 <AppId>_is1（installer.iss 的 AppId，行内 {{ 是 Inno 转义）。
# DisplayName 受 AppVerName 缺省规则影响、不保证等于 AppName（run 42340112867 因精确匹配
# "AStockScreener" 取不到条目），故按 AppId 定位；取不到时返回 $null，不伪造版本。
$UninstallKeyName = "{7E6D0B44-BD44-4E46-8B5C-3A5F2D237C1A}_is1"
$UninstallRoots = @(
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall",
    "HKCU:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
)

function ConvertTo-AppInfo($props, [string]$keyPath) {
    $names = $props.PSObject.Properties.Name
    $displayName = if ($names -contains "DisplayName") { [string]$props.DisplayName } else { "" }
    $version = if ($names -contains "DisplayVersion") { [string]$props.DisplayVersion } else { "" }
    if ([string]::IsNullOrEmpty($version) -and $displayName -match "(\d+\.\d+\.\d+)") {
        $version = $Matches[1]
    }
    return [pscustomobject]@{
        KeyPath     = $keyPath
        DisplayName = $displayName
        Version     = $version
    }
}

function Get-InstalledAppInfo {
    # 首选：AppId 键（Inno 固定命名，与 DisplayName 文案无关）
    foreach ($root in $UninstallRoots) {
        $keyPath = Join-Path $root $UninstallKeyName
        if (-not (Test-Path $keyPath)) { continue }
        $props = Get-ItemProperty -Path $keyPath -ErrorAction SilentlyContinue
        if ($null -ne $props) { return ConvertTo-AppInfo $props $keyPath }
    }
    # 兜底：键名规则变化时按 DisplayName 前缀取证，避免再次盲跑
    foreach ($root in $UninstallRoots) {
        if (-not (Test-Path $root)) { continue }
        foreach ($entry in @(Get-ChildItem -Path $root -ErrorAction SilentlyContinue)) {
            $props = Get-ItemProperty -Path $entry.PSPath -ErrorAction SilentlyContinue
            if ($null -eq $props) { continue }
            if (-not ($props.PSObject.Properties.Name -contains "DisplayName")) { continue }
            if (([string]$props.DisplayName) -like "AStockScreener*") {
                return ConvertTo-AppInfo $props $entry.PSPath
            }
        }
    }
    return $null
}

# ---- 主流程 ----
Write-Host "=== Phase 5 Upgrade Smoke Test ===" -ForegroundColor Yellow
Write-Host "InstallerPath: $InstallerPath"
Write-Host "OldVersion:    $OldVersion"
Write-Host "OldVariant:    $OldVariant"
Write-Host "VerifyRollback:$VerifyRollback"
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
        # Inno Setup 卸载器是 GUI 程序，`&` 调用不等待其结束；必须 -Wait。
        Start-Process -FilePath $uninstaller -ArgumentList '/SILENT', '/NORESTART' -Wait | Out-Null
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
    $oldInstaller = "$env:TEMP\AStockScreener-Setup-$OldVariant-$OldVersion.exe"
    Write-Host "  Downloading from GitHub Release v$OldVersion..."
    # 资产名由 installer.iss 的 OutputBaseFilename=AStockScreener-Setup-{#TargetVariant} 决定；
    # 单个 Release 同时含 embedded-cpu / embedded-cuda 资产，故须按变体精确匹配（glob 会命中多个，
    # gh 的 --output 只接受唯一资产），历史命名（embedded）通过 -OldVariant 显式指定。
    gh release download "v$OldVersion" --repo $env:GITHUB_REPOSITORY `
        --pattern "AStockScreener-Setup-$OldVariant.exe" `
        --output $oldInstaller 2>&1 | Out-Null
    if (-not (Test-Path $oldInstaller)) {
        throw "Failed to download old version installer v$OldVersion"
    }
    # Inno Setup 安装器是 GUI 程序，`&` 调用立即返回、不等待解包完成；必须 -Wait 并校验退出码。
    $oldInstallerProc = Start-Process -FilePath $oldInstaller -ArgumentList '/SILENT', '/NORESTART' -PassThru -Wait
    Assert-True ($oldInstallerProc.ExitCode -eq 0) "Old version installer finished (exit code: $($oldInstallerProc.ExitCode))"
    Assert-True (Test-Path $AppExePath) "Old version installed: $AppExePath"

    # STEP 3: 启动旧版 qTrading，等待 sidecar ready
    Write-Step "3. Start old version & wait for sidecar ready"
    $proc = Start-Process -FilePath $AppExePath -PassThru
    $ready = Wait-SidecarReady
    Assert-True $ready "Sidecar ready (old version, status JSON: running + postgres_alive)"

    # STEP 4: 旧版运行期写入已提交的合成业务哨兵，并记录升级前 PG 根证据
    # （真实认证路径：runtime\password（DPAPI）+ postmaster.pid 端口 + install\bin\psql.exe）
    Write-Step "4. Write committed sentinel & record PG root evidence (old version)"
    $sentinel = "d17-t02-" + [Guid]::NewGuid().ToString("N")
    if (-not ($sentinel -match '^d17-t02-[0-9a-f]{32}$')) {
        throw "sentinel marker format invalid"
    }
    $portBefore = Get-PostgresPort -DataDir $PgDataDir
    Assert-True (Wait-AppDatabaseReady -PasswordFile $PgPasswordFile) "Application database '$PgDatabase' created (sidecar ready precedes ensure_database)"
    # 轮询成功即证明读到的密码被服务端接受；重读一次供后续 psql 使用，避免沿用启动前旧值。
    $pgPassword = Read-PgPassword -PasswordFile $PgPasswordFile
    $sentinelCreateSql = "create table if not exists $SentinelTable (id integer primary key, marker text not null)"
    $sentinelInsertSql = "insert into $SentinelTable (id, marker) values (1, '$sentinel') on conflict (id) do update set marker = excluded.marker"
    Invoke-Psql -Password $pgPassword -Port $portBefore -Sql $sentinelCreateSql | Out-Null
    Invoke-Psql -Password $pgPassword -Port $portBefore -Sql $sentinelInsertSql | Out-Null
    $sentinelReadBefore = Invoke-Psql -Password $pgPassword -Port $portBefore -Sql "select marker from $SentinelTable where id = 1"
    Assert-True ($sentinelReadBefore -eq $sentinel) "Synthetic sentinel committed & read back before upgrade"

    Assert-True (Test-Path $PgDataDir) "PGDATA dir exists before upgrade: $PgDataDir"
    Assert-True (Test-Path $PgVersionFile) "PG_VERSION exists before upgrade: $PgVersionFile"
    $pgVersionBefore = (Get-Content -Path $PgVersionFile -Raw).Trim()
    Assert-True ($pgVersionBefore.Length -gt 0) "PG_VERSION is non-empty before upgrade ($pgVersionBefore)"
    Assert-True (Test-Path $PgPasswordFile) "runtime password file exists before upgrade"
    Assert-True (Test-Path $PsqlExePath) "install psql exists before upgrade"

    # STEP 5: 模拟用户关闭（升级前必须完全退出，否则安装器会拒绝 / 产生文件锁竞争）
    Write-Step "5. Stop qTrading before upgrade"
    Stop-QtradingGraceful
    Start-Sleep -Seconds 3
    Assert-True ($null -eq (Get-Process -Name "AStockScreener" -ErrorAction SilentlyContinue)) "App process exited before upgrade"
    Assert-True ($null -eq (Get-Process -Name "qtrading-pg-sidecar" -ErrorAction SilentlyContinue)) "Sidecar process exited before upgrade"

    # STEP 6: 安装新版本（覆盖升级）
    Write-Step "6. Install new version (in-place upgrade)"
    # 同 STEP 2：GUI 安装器必须 -Wait + 校验退出码，否则会在解包完成前断言安装结果。
    $upgradeInstallerProc = Start-Process -FilePath $InstallerPath -ArgumentList '/SILENT', '/NORESTART' -PassThru -Wait
    Assert-True ($upgradeInstallerProc.ExitCode -eq 0) "New version installer finished (exit code: $($upgradeInstallerProc.ExitCode))"
    Assert-True (Test-Path $AppExePath) "New version installed (overwritten)"

    # STEP 7: 验证 PG 根完整保留且仍指向原库（PG_VERSION 内容未变，password/install 仍在）
    Write-Step "7. Verify PG root preserved after upgrade"
    Assert-True (Test-Path $PgDataDir) "PGDATA dir preserved: $PgDataDir"
    Assert-True (Test-Path $PgVersionFile) "PG_VERSION preserved: $PgVersionFile"
    $pgVersionAfter = (Get-Content -Path $PgVersionFile -Raw).Trim()
    Assert-True ($pgVersionAfter -eq $pgVersionBefore) "PG_VERSION unchanged after upgrade ($pgVersionAfter)"
    Assert-True (Test-Path $PgInstallDir) "install dir preserved: $PgInstallDir"
    Assert-True (Test-Path $PsqlExePath) "install psql preserved: $PsqlExePath"
    Assert-True (Test-Path $PgRuntimeDir) "runtime dir preserved: $PgRuntimeDir"
    Assert-True (Test-Path $PgPasswordFile) "runtime password file preserved: $PgPasswordFile"

    # STEP 8: 启动新版本，等待真实 ready（status JSON: running + postgres_alive）
    Write-Step "8. Start new version & wait for real sidecar ready"
    $proc2 = Start-Process -FilePath $AppExePath -PassThru
    $ready2 = Wait-SidecarReady
    Assert-True $ready2 "Sidecar ready (new version, existing data, status JSON: running + postgres_alive)"

    # STEP 9: 读回哨兵并断言与升级前完全一致（已提交事务在原库中仍可见）
    Write-Step "9. Read back sentinel after upgrade & assert exact match"
    $pgPasswordAfter = Read-PgPassword -PasswordFile $PgPasswordFile
    Assert-True (-not ([string]::IsNullOrEmpty($pgPasswordAfter))) "Preserved password file decrypts after upgrade"
    $portAfterUpgrade = Get-PostgresPort -DataDir $PgDataDir
    $sentinelAfterUpgrade = Invoke-Psql -Password $pgPasswordAfter -Port $portAfterUpgrade -Sql "select marker from $SentinelTable where id = 1"
    Assert-True ($sentinelAfterUpgrade -eq $sentinel) "Synthetic sentinel survived in-place upgrade unchanged"

    Stop-QtradingGraceful

    # STEP 10: 回滚腿（G4 §205：候选包 → 旧恢复包）
    # 仅证明「同一数据目录可从候选包回退到旧恢复包且业务数据不变」；
    # 候选版本标识仅记录不判等（dispatch 构建可能将 AppVersion 写成分支名）。
    if ($VerifyRollback) {
        Write-Step "10. Rollback: restore baseline package over candidate"
        $candidateInfo = Get-InstalledAppInfo
        if ($null -eq $candidateInfo) {
            Write-Host "  Installed (candidate): uninstall entry not found ($UninstallKeyName)"
        } else {
            Write-Host "  Installed (candidate): $($candidateInfo.DisplayName) / version='$($candidateInfo.Version)'"
        }
        Stop-QtradingGraceful
        Assert-True ($null -eq (Get-Process -Name "AStockScreener" -ErrorAction SilentlyContinue)) "App process exited before rollback"
        Assert-True ($null -eq (Get-Process -Name "qtrading-pg-sidecar" -ErrorAction SilentlyContinue)) "Sidecar process exited before rollback"

        $rollbackProc = Start-Process -FilePath $oldInstaller -ArgumentList '/SILENT', '/NORESTART' -PassThru -Wait
        Assert-True ($rollbackProc.ExitCode -eq 0) "Restore installer finished (exit code: $($rollbackProc.ExitCode))"
        Assert-True (Test-Path $AppExePath) "Restored package installed: $AppExePath"

        $restoredInfo = Get-InstalledAppInfo
        Assert-True ($null -ne $restoredInfo) "Restored package registered in Add/Remove Programs ($UninstallKeyName)"
        $restoredVersion = $restoredInfo.Version
        Assert-True (-not [string]::IsNullOrEmpty($restoredVersion)) "Restored package declares a version"
        # 发布构建以 github.ref_name（形如 v0.11.3）作为 ISCC /DMyAppVersion，故 DisplayVersion
        # 带 v 前缀而 $OldVersion 已去前缀；比较前归一化，不修改断言目标版本。
        $restoredVersionNormalized = $restoredVersion.TrimStart('v', 'V')
        Assert-True ($restoredVersionNormalized -eq $OldVersion) "Restored version matches baseline ($restoredVersionNormalized vs $OldVersion)"
        Assert-True (Test-Path $PgVersionFile) "PG_VERSION preserved after rollback: $PgVersionFile"

        $proc3 = Start-Process -FilePath $AppExePath -PassThru
        $ready3 = Wait-SidecarReady
        Assert-True $ready3 "Sidecar ready (restored version, status JSON: running + postgres_alive)"

        $pgPasswordRollback = Read-PgPassword -PasswordFile $PgPasswordFile
        $portAfterRollback = Get-PostgresPort -DataDir $PgDataDir
        $sentinelAfterRollback = Invoke-Psql -Password $pgPasswordRollback -Port $portAfterRollback -Sql "select marker from $SentinelTable where id = 1"
        Assert-True ($sentinelAfterRollback -eq $sentinel) "Synthetic sentinel survived rollback unchanged"

        Stop-QtradingGraceful
    }

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
