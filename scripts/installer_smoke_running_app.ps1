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

# ---- sidecar 协议/超时常量 ----
# 与 Rust sidecars/qtrading-pg-sidecar/src/protocol.rs 的 READY_SCHEMA 一致
$SidecarReadySchema = "qtrading.embedded_postgres.run.ready.v1"
# 从启动到 ready 的有界等待（秒）；禁止无限等待
$SidecarReadyTimeoutSec = 120
# 关闭 stdin 后优雅停止的有界等待（秒）
$SidecarShutdownTimeoutSec = 60

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

# 关闭 stdin 触发 Rust 监督循环的 EOF → 分级停止；有界等待后仍未退出才强杀，避免孤儿进程。
function Stop-SidecarGraceful([System.Diagnostics.Process]$proc) {
    if ($null -eq $proc -or $proc.HasExited) { return }
    try { $proc.StandardInput.Close() } catch { }
    if (-not $proc.WaitForExit($SidecarShutdownTimeoutSec * 1000)) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
}

# ready 之前监督循环尚未运行，stdin EOF 不生效；直接强杀，避免无意义等待 60s。
function Stop-SidecarForced([System.Diagnostics.Process]$proc) {
    if ($null -eq $proc -or $proc.HasExited) { return }
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
}

# ---- 主流程 ----
Write-Host "=== Phase 5 Running App Rejection Smoke Test (Failure Injection #32) ===" -ForegroundColor Yellow
Write-Host "InstallerPath: $InstallerPath"
Write-Host "AppInstallDir: $AppInstallDir"

if (-not (Test-Path $InstallerPath)) {
    Write-Fail "InstallerPath not found: $InstallerPath"
    exit 1
}

# B06/B11: 所有 4 个变体（cpu/cuda/embedded-cpu/embedded-cuda）均启用 InitializeSetup 主进程运行拦截；
# Step 6~8 的 sidecar 进程拦截仅在存在 _internal\sidecars\qtrading-pg-sidecar.exe（embedded 变体）时执行。
Write-Host "Variant check: testing InitializeSetup running-app rejection (sidecar check auto-runs if bundled)"

# 供 catch 清理使用；赋值发生在 Step 6（未进入该分支时为 $null）
$sidecarProc = $null

try {
    # STEP 1: 清理环境
    Write-Step "1. Cleanup existing installation"
    Stop-QtradingGraceful
    $uninstaller = Join-Path $AppInstallDir "unins000.exe"
    if (Test-Path $uninstaller) {
        # Inno Setup 卸载器是 GUI 程序，`&` 调用不等待其结束，必须 -Wait。
        Start-Process -FilePath $uninstaller -ArgumentList '/SILENT', '/NORESTART' -Wait | Out-Null
        Start-Sleep -Seconds 5
    }
    if (Test-Path $AppInstallDir) {
        Remove-Item -Recurse -Force $AppInstallDir -ErrorAction SilentlyContinue
    }
    Write-Ok "Environment cleaned"

    # STEP 2: 先安装一次（确保有 EXE 可启动）
    Write-Step "2. Install application (initial install)"
    # Inno Setup 安装器是 GUI 程序，`&` 调用立即返回、不等待解包完成；
    # 必须在 -Wait 下等待安装结束再启动 EXE，否则主程序会在 _internal 未就绪时静默退出。
    $installerProc = Start-Process -FilePath $InstallerPath -ArgumentList '/SILENT', '/NORESTART' -PassThru -Wait
    Assert-True ($installerProc.ExitCode -eq 0) "Installer finished (exit code: $($installerProc.ExitCode))"
    Assert-True (Test-Path $AppExePath) "App installed: $AppExePath"

    # STEP 3: 启动 qTrading（保持运行中）
    Write-Step "3. Start qTrading (keep running)"
    $proc = Start-Process -FilePath $AppExePath -PassThru
    Start-Sleep -Seconds 5
    Assert-True (-not $proc.HasExited) "qTrading running (PID: $($proc.Id))"

    # STEP 4: 在 qTrading 运行中尝试安装（应被拒绝）
    Write-Step "4. Attempt install while qTrading running (should be rejected)"
    $reinstallProc = Start-Process -FilePath $InstallerPath -ArgumentList '/SILENT', '/NORESTART' -PassThru -Wait
    $installExitCode = $reinstallProc.ExitCode

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
        $pgDataDir = "$env:LOCALAPPDATA\qTrading\qTrading\postgres\16\data"
        # Rust paths.rs：省略 --password-file 时由 data_dir 派生 <base>\runtime\password；
        # 禁止伪造临时密码文件（D17-F01：Existing PGDATA 会因此 exit 16）。
        $pgRuntimeDir = Join-Path (Split-Path -Parent $pgDataDir) "runtime"
        $derivedPasswordFile = Join-Path $pgRuntimeDir "password"

        # Existing 前置：数据目录已初始化且派生密码文件存在可读；否则失败退出，不得计为 installer 拒绝。
        if (-not (Test-Path (Join-Path $pgDataDir "PG_VERSION"))) {
            Write-Fail "前置失败（PGDATA 未初始化，缺少 PG_VERSION）：$pgDataDir"
            throw "PRECONDITION FAILED: PGDATA is not an initialized cluster: $pgDataDir"
        }
        if (-not (Test-Path $derivedPasswordFile) -or (Get-Item $derivedPasswordFile).Length -le 0) {
            Write-Fail "前置失败（Existing 缺可读密码）：$derivedPasswordFile"
            throw "PRECONDITION FAILED: missing readable password file: $derivedPasswordFile"
        }

        # ProcessStartInfo 拉起 sidecar：保留 stdin（监督 stdin EOF），重定向 stdout（ready JSON）/stderr（诊断）。
        $sidecarPsi = New-Object System.Diagnostics.ProcessStartInfo
        $sidecarPsi.FileName = $sidecarExe
        $sidecarPsi.Arguments = 'run --data-dir "' + $pgDataDir + '"'
        $sidecarPsi.UseShellExecute = $false
        $sidecarPsi.CreateNoWindow = $true
        $sidecarPsi.RedirectStandardInput = $true
        $sidecarPsi.RedirectStandardOutput = $true
        $sidecarPsi.RedirectStandardError = $true
        $sidecarPsi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
        $sidecarPsi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
        $sidecarProc = [System.Diagnostics.Process]::Start($sidecarPsi)
        $stderrTask = $sidecarProc.StandardError.ReadToEndAsync()

        # 有界等待 stdout 第一行 ready JSON；超时即失败并回收进程，禁止无限等待。
        $readyTask = $sidecarProc.StandardOutput.ReadLineAsync()
        if (-not $readyTask.Wait($SidecarReadyTimeoutSec * 1000)) {
            Stop-SidecarForced $sidecarProc
            Write-Fail "sidecar 未在 $SidecarReadyTimeoutSec s 内输出 ready JSON（timeout）"
            throw "PRECONDITION FAILED: sidecar ready timeout after $SidecarReadyTimeoutSec s"
        }
        $readyLine = $readyTask.Result
        if ($null -eq $readyLine -or $readyLine.Trim() -eq "") {
            if (-not $sidecarProc.WaitForExit(5000)) {
                Stop-SidecarForced $sidecarProc
                Write-Fail "sidecar 在 ready 前退出且无法回收退出码"
                throw "PRECONDITION FAILED: sidecar exited before ready without exit code"
            }
            $sidecarExitCode = $sidecarProc.ExitCode
            if ($sidecarExitCode -eq 16) {
                Write-Fail "前置失败（Existing 缺可读密码）：sidecar exit 16（PASSWORD_FAILED）；不计为 installer 拒绝通过"
                throw "PRECONDITION FAILED: sidecar exit 16 (PASSWORD_FAILED) on Existing PGDATA"
            }
            Write-Fail "sidecar 在 ready 前退出（exit=$sidecarExitCode）；不计为 installer 拒绝通过"
            throw "PRECONDITION FAILED: sidecar exited before ready (exit=$sidecarExitCode)"
        }

        # ready JSON schema 必须与 Rust protocol.rs 一致；未通过前不得进入 installer 拒绝断言。
        $ready = $readyLine | ConvertFrom-Json
        Assert-True ($ready.schema -eq $SidecarReadySchema) "sidecar ready JSON schema: $($ready.schema)"
        Assert-True ($ready.status -eq "running") "sidecar ready status: running"
        # 继续异步消费 stdout 余量（运行期 warning/exit JSON-lines），避免关闭阶段管道阻塞。
        $stdoutRestTask = $sidecarProc.StandardOutput.ReadToEndAsync()

        # STEP 7: sidecar 运行中尝试安装（应被拒绝）
        Write-Step "7. Attempt install while sidecar running (should be rejected, P1-11)"
        $reinstallProc2 = Start-Process -FilePath $InstallerPath -ArgumentList '/SILENT', '/NORESTART' -PassThru -Wait
        $installExitCode2 = $reinstallProc2.ExitCode

        Write-Host "  Installer exit code: $installExitCode2"
        Assert-True ($installExitCode2 -ne 0) "Installer rejected (exit code non-zero: $installExitCode2)"

        # STEP 8: 验证 sidecar 仍在运行
        Write-Step "8. Verify sidecar still running (not killed by installer)"
        Start-Sleep -Seconds 2
        $sidecarAfter = Get-Process -Id $sidecarProc.Id -ErrorAction SilentlyContinue
        Assert-True ($null -ne $sidecarAfter) "sidecar still running (PID: $($sidecarProc.Id))"
        Assert-True (-not $sidecarAfter.HasExited) "sidecar not exited"

        # stdin 保持到安装拒绝断言之后：关闭 stdin 触发优雅停止，有界等待并清理，避免孤儿。
        try { $sidecarProc.StandardInput.Close() } catch { }
        if (-not $sidecarProc.WaitForExit($SidecarShutdownTimeoutSec * 1000)) {
            Stop-Process -Id $sidecarProc.Id -Force -ErrorAction SilentlyContinue
            Write-Fail "sidecar 未在 $SidecarShutdownTimeoutSec s 内优雅退出"
            throw "ASSERTION FAILED: sidecar shutdown timeout"
        }
        $stdoutRestTask.Wait(5000) | Out-Null
        $stderrTask.Wait(5000) | Out-Null
        Assert-True ($sidecarProc.ExitCode -eq 0) "sidecar exited cleanly after stdin EOF (exit code: $($sidecarProc.ExitCode))"
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
    # 失败路径同样回收 sidecar（关闭 stdin → 有界等待 → 强杀），避免孤儿。
    if ($null -ne $sidecarProc) { Stop-SidecarGraceful $sidecarProc }
    Stop-QtradingGraceful
    exit 1
}
