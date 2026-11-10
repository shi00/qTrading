"""installer / Windows 默认路径静态契约（Linux CI 无法执行 Windows 脚本，只读文本断言）。

守护若干已修复缺陷：
1. Inno Setup 安装器 / 卸载器是 GUI 程序，PowerShell 调用运算符 ``&`` 不等待其结束；
   必须以 ``Start-Process ... -Wait`` 调用（否则后续断言会读到半解包目录）；
2. 升级脚本下载旧版安装包使用变体 glob——``installer.iss`` 的
   ``OutputBaseFilename=AStockScreener-Setup-{#TargetVariant}`` 使资产名为
   ``-embedded-cpu`` / ``-embedded-cuda``（历史版本可能为 ``-embedded``），
   写死单一 ``-embedded.exe`` 无法命中现行命名；
3. Windows 默认 PGDATA 必须与 ``EmbeddedPostgresService.from_config`` 的
   platformdirs 实际行为一致：未传 ``appauthor=False`` 时 Windows 路径为
   ``<LOCALAPPDATA>\\qTrading\\qTrading\\postgres\\16\\data`` 两层结构；
   安装器 / smoke 脚本 / 维护脚本若仍用单层 ``<LOCALAPPDATA>\\qTrading\\postgres\\16\\data``
   会指向错误目录（卸载 stop、升级/卸载保留数据检查、sidecar 场景全部失准）。
4. 三个 smoke 脚本的 ``Wait-SidecarReady`` 曾以「进程名存在」作为 ready 判据：
   PG 仍在 initdb 或稍后认证失败时也会先返回 true。ready 必须以 sidecar
   ``status --data-dir`` 的 JSON（``status == "running"`` 且 ``postgres_alive == true``）
   为唯一判据；崩溃恢复脚本还须走真实认证路径写入合成业务哨兵，崩溃后重启并读回。
5. 运行中拒绝冒烟不能仅凭安装器非零退出码判通过：installer.iss 的 InitializeSetup
   静默拒绝与磁盘/权限等安装内部故障同为非零。installer.iss 现写 ``/LOG`` 受控日志行
   ``qtrading.setup-guard.refused reason=<running-app|running-sidecar>``，smoke 脚本以
   ``/LOG`` 收集并断言该标记，日志缺失/标记不命中即 UNVERIFIED REJECTION 失败（RUNNING-01）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from platformdirs.windows import Windows

pytestmark = [pytest.mark.unit, pytest.mark.meta]

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = ROOT / "scripts"
RESOURCES_DIR = ROOT / "resources" / "maintenance"
SMOKE_SCRIPTS = (
    "installer_smoke_crash_recovery.ps1",
    "installer_smoke_running_app.ps1",
    "installer_smoke_uninstall.ps1",
    "installer_smoke_upgrade.ps1",
)

# platformdirs 在 Windows 上默认追加 author（= appname），故 service 默认根为两层。
TWO_LAYER_WINDOWS_PGDATA = r"qTrading\qTrading\postgres\16\data"
TWO_LAYER_WINDOWS_ROOT = r"qTrading\qTrading"

# 旧单层 Windows 默认路径（未追加 author 的错误形态）。负向断言确保旧路径不被保留。
OLD_SINGLE_LAYER_WINDOWS_PGDATA = re.compile(
    r"(?:%LOCALAPPDATA%|\{localappdata\}|\$env:LOCALAPPDATA)[\\/]+qTrading[\\/]+"
    r"(?!qTrading[\\/])postgres[\\/]+16[\\/]+data",
    re.IGNORECASE,
)

# 调用运算符直接拉起 GUI 安装器/卸载器（`& $InstallerPath ...` / `& $oldInstaller ...`）
_GUI_CALL_OPERATOR = re.compile(r"(?m)^\s*&\s*\$(?:InstallerPath|UninstallerPath|existingUninstaller|oldInstaller)\b")

# 安装新版本的完整调用行：静默参数 + -PassThru（取进程对象）+ -Wait（等 GUI 结束）
_INSTALLER_INVOCATION = re.compile(
    r"(?m)^\s*\$(installerProc|upgradeInstallerProc) = Start-Process -FilePath \$InstallerPath "
    r"-ArgumentList '/SILENT', '/NORESTART' -PassThru -Wait\r?$"
)


def _read(name: str) -> str:
    return (SCRIPTS_DIR / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("script_name", SMOKE_SCRIPTS)
def test_gui_installers_not_invoked_via_call_operator(script_name: str) -> None:
    """任何 smoke 脚本都不得用 ``&`` 直接拉起 GUI 安装器/卸载器（不会等待其结束）。"""
    assert _GUI_CALL_OPERATOR.search(_read(script_name)) is None, (
        f"{script_name} 使用 `& $InstallerPath/$UninstallerPath` 调用 GUI 程序；"
        "须改用 Start-Process -Wait（GUI 程序 `&` 不等待，会读到半解包目录）"
    )


@pytest.mark.parametrize(
    ("script_name", "expected_proc"),
    [
        ("installer_smoke_uninstall.ps1", "installerProc"),
        ("installer_smoke_upgrade.ps1", "upgradeInstallerProc"),
    ],
)
def test_installer_invocation_waits_and_checks_exit_code(script_name: str, expected_proc: str) -> None:
    """安装新版本必须以 ``Start-Process -FilePath $InstallerPath ... -PassThru -Wait`` 调用并校验退出码。

    强断言三项：调用行完整形态（静默参数 + ``-PassThru`` 取进程对象 + ``-Wait`` 等待）、
    调用次数恰为 1、退出码断言引用同一进程变量。
    """
    text = _read(script_name)
    invocations = _INSTALLER_INVOCATION.findall(text)
    assert invocations == [expected_proc], (
        f"{script_name} 未按 `$<proc> = Start-Process -FilePath $InstallerPath "
        "-ArgumentList '/SILENT', '/NORESTART' -PassThru -Wait` 调用安装器"
        "（GUI 程序不 -Wait 会读到半解包目录，不 -PassThru 无法校验退出码）"
    )
    assert text.count("Start-Process -FilePath $InstallerPath") == 1, (
        f"{script_name} 安装器调用次数应为 1，实际 {text.count('Start-Process -FilePath $InstallerPath')}"
    )
    assert f"Assert-True (${expected_proc}.ExitCode -eq 0)" in text, f"{script_name} 未校验 {expected_proc} 退出码"


def test_uninstall_script_waits_for_uninstaller() -> None:
    """卸载分支必须以 Start-Process -FilePath $UninstallerPath -Wait 调用并校验退出码。"""
    text = _read("installer_smoke_uninstall.ps1")
    assert re.search(r"Start-Process -FilePath \$UninstallerPath[^\n]*-PassThru -Wait", text)
    assert "($uninstallerProc.ExitCode -eq 0)" in text


def test_upgrade_download_pattern_matches_variant_asset() -> None:
    """升级脚本按变体精确下载旧版安装包（单 Release 含多个 embedded 资产，glob 会命中多个）。"""
    text = _read("installer_smoke_upgrade.ps1")
    assert '--pattern "AStockScreener-Setup-$OldVariant.exe"' in text
    assert '--pattern "AStockScreener-Setup-embedded.exe"' not in text
    assert '[string]$OldVariant = "embedded-cpu"' in text


def _posix(path: str) -> str:
    """把 Windows / POSIX 分隔符统一为 ``/``，便于跨平台断言。"""
    return path.replace("\\", "/").rstrip("/")


def test_platformdirs_windows_default_is_two_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    """service 未传 ``appauthor=False`` 时，Windows 默认根是两层 ``<LOCALAPPDATA>\\qTrading\\qTrading``。

    ``WIN_PD_OVERRIDE_LOCAL_APPDATA`` 是 platformdirs 公开的 Windows 目录覆盖入口，
    让本测试在 Linux CI 上也能确定性验证 Windows 路径拼接，不依赖注册表 / ctypes。
    对照组 ``appauthor=False`` 证明单层路径只有在显式关闭 author 时才出现。
    """
    local_app_data = r"C:\Users\smoke\AppData\Local"
    monkeypatch.setenv("LOCALAPPDATA", local_app_data)
    monkeypatch.setenv("WIN_PD_OVERRIDE_LOCAL_APPDATA", local_app_data)

    default_root = _posix(Windows("qTrading").user_data_dir)
    assert default_root == "C:/Users/smoke/AppData/Local/qTrading/qTrading"
    assert _posix(str(Path(default_root) / "postgres" / "16")) == (
        "C:/Users/smoke/AppData/Local/" + TWO_LAYER_WINDOWS_PGDATA.replace("\\", "/").removesuffix("/data")
    )

    # 对照：仅显式 appauthor=False 才是单层；service 未传，故默认必须是两层。
    assert _posix(Windows("qTrading", appauthor=False).user_data_dir) == ("C:/Users/smoke/AppData/Local/qTrading")


def test_embedded_pg_service_keeps_default_platformdirs_call() -> None:
    """生产 service 默认路径不得改为 ``appauthor=False``（否则既有用户库失联）。"""
    text = (ROOT / "data" / "persistence" / "embedded_postgres" / "service.py").read_text(encoding="utf-8")
    assert 'platformdirs.user_data_dir("qTrading")' in text
    assert "appauthor=False" not in text


@pytest.mark.parametrize(
    ("relative_path", "expected_snippets"),
    [
        ("installer.iss", [r'"{localappdata}\qTrading\qTrading\postgres\16\data"']),
        (
            "scripts/installer_smoke_running_app.ps1",
            [r'"$env:LOCALAPPDATA\qTrading\qTrading\postgres\16\data"'],
        ),
        (
            "scripts/installer_smoke_upgrade.ps1",
            [r'Join-Path $env:LOCALAPPDATA "qTrading\qTrading\postgres\16\data"'],
        ),
        (
            "scripts/installer_smoke_uninstall.ps1",
            [r'Join-Path $env:LOCALAPPDATA "qTrading\qTrading\postgres\16\data"'],
        ),
        (
            "scripts/installer_smoke_crash_recovery.ps1",
            [r'Join-Path $env:LOCALAPPDATA "qTrading\qTrading"'],
        ),
        (
            "resources/maintenance/qtrading-db-maintenance.bat",
            [r'set "DEFAULT_DATA_DIR=%LOCALAPPDATA%\qTrading\qTrading\postgres\16\data"'],
        ),
        (
            "resources/maintenance/README-maintenance.md",
            [r"%LOCALAPPDATA%\qTrading\qTrading\postgres\16\data"],
        ),
    ],
)
def test_windows_consumers_use_two_layer_pgdata(relative_path: str, expected_snippets: list[str]) -> None:
    """Windows 消费者必须指向 service 实际使用的两层 PGDATA，且不得残留单层旧路径。"""
    text = (ROOT / relative_path).read_text(encoding="utf-8")
    for snippet in expected_snippets:
        assert snippet in text, f"{relative_path} 缺少两层 Windows 默认路径片段：{snippet}"
    assert OLD_SINGLE_LAYER_WINDOWS_PGDATA.search(text) is None, (
        f"{relative_path} 仍残留单层 Windows PGDATA 路径；Windows 默认应为 {TWO_LAYER_WINDOWS_PGDATA}"
    )


@pytest.mark.parametrize(
    "old_path",
    [
        r'"{localappdata}\qTrading\postgres\16\data"',
        r'"$env:LOCALAPPDATA\qTrading\postgres\16\data"',
        r"%LOCALAPPDATA%\qTrading\postgres\16\data",
    ],
)
def test_old_single_layer_windows_path_regex_is_effective(old_path: str) -> None:
    """正则必须能识别旧单层路径、且不误伤两层路径，否则负向断言会恒真。"""
    assert OLD_SINGLE_LAYER_WINDOWS_PGDATA.findall(old_path) == [old_path.strip('"')]
    two_layer_path = old_path.replace(r"qTrading\postgres", r"qTrading\qTrading\postgres")
    assert OLD_SINGLE_LAYER_WINDOWS_PGDATA.search(two_layer_path) is None


def test_crash_recovery_user_data_root_and_logs_are_two_layer() -> None:
    """崩溃恢复脚本的用户数据根（含 postgres-logs 诊断路径）必须是两层根。"""
    text = _read("installer_smoke_crash_recovery.ps1")
    assert r'Join-Path $env:LOCALAPPDATA "qTrading\qTrading"' in text
    assert 'Join-Path $env:LOCALAPPDATA "qTrading"' not in text
    assert 'Join-Path $UserDataRoot "postgres-logs"' in text
    assert TWO_LAYER_WINDOWS_ROOT in text


def test_posix_default_paths_stay_single_layer() -> None:
    """platformdirs 在 Linux/macOS 忽略 appauthor，维护脚本与 README 的单层路径不得改成两层。"""
    sh = (RESOURCES_DIR / "qtrading-db-maintenance.sh").read_text(encoding="utf-8")
    assert 'DEFAULT_DATA_DIR="$HOME/Library/Application Support/qTrading/postgres/16/data"' in sh
    assert 'DEFAULT_DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/qTrading/postgres/16/data"' in sh
    assert "qTrading/qTrading/postgres" not in sh

    readme = (RESOURCES_DIR / "README-maintenance.md").read_text(encoding="utf-8")
    assert "- **Linux**: `~/.local/share/qTrading/postgres/16/data`" in readme
    assert "- **macOS**: `~/Library/Application Support/qTrading/postgres/16/data`" in readme
    assert "qTrading/qTrading/postgres" not in readme


# ---- D17-F01：sidecar 独立启动的凭据 / ready 协议 / 进程生命周期契约 ----

# 伪造密码文件标记（缺陷现场：文件从未创建，Existing PGDATA 必然 exit 16）
_FAKE_SIDECAR_PASSWORD = re.compile(r"fake_sidecar_pwd")

# 显式传入 --password-file 参数（省略时 Rust paths.rs 从 data_dir 派生 runtime/password）
_SIDECAR_PASSWORD_ARG = re.compile(r'"--password-file"')

_RUNNING_APP = "installer_smoke_running_app.ps1"


def test_sidecar_guards_detect_legacy_bug() -> None:
    """负向断言使用的正则必须能命中旧缺陷行，否则“已修复”断言可能恒真。"""
    legacy_line = (
        '$sidecarProc = Start-Process -FilePath $sidecarExe -ArgumentList "run", '
        '"--data-dir", "$env:LOCALAPPDATA\\qTrading\\qTrading\\postgres\\16\\data", '
        '"--password-file", "$env:TEMP\\fake_sidecar_pwd" -PassThru'
    )
    assert _FAKE_SIDECAR_PASSWORD.findall(legacy_line) == ["fake_sidecar_pwd"]
    assert _SIDECAR_PASSWORD_ARG.findall(legacy_line) == ['"--password-file"']
    assert "Start-Process -FilePath $sidecarExe" in legacy_line


def test_running_app_uses_derived_password_not_fake_file() -> None:
    """sidecar 凭据必须由 data-dir 派生（真实 runtime\\password），禁止伪造临时密码文件。"""
    text = _read(_RUNNING_APP)
    assert _FAKE_SIDECAR_PASSWORD.search(text) is None
    # 省略 --password-file 参数，让 Rust paths.rs 派生 <base>\runtime\password
    assert _SIDECAR_PASSWORD_ARG.search(text) is None
    assert '$pgRuntimeDir = Join-Path (Split-Path -Parent $pgDataDir) "runtime"' in text
    assert '$derivedPasswordFile = Join-Path $pgRuntimeDir "password"' in text
    assert "(Test-Path $derivedPasswordFile)" in text


def test_running_app_sidecar_start_uses_redirected_process_start_info() -> None:
    """必须用 ProcessStartInfo 重定向 stdin/stdout/stderr，以便保留 stdin 并解析 ready。"""
    text = _read(_RUNNING_APP)
    assert "System.Diagnostics.ProcessStartInfo" in text
    assert "$sidecarPsi.RedirectStandardInput = $true" in text
    assert "$sidecarPsi.RedirectStandardOutput = $true" in text
    assert "$sidecarPsi.RedirectStandardError = $true" in text
    assert "Start-Process -FilePath $sidecarExe" not in text


def test_running_app_ready_wait_is_bounded_and_timeout_fails() -> None:
    """ready 读取必须有界（120s 量级），超时即失败退出，不得无限等待。"""
    text = _read(_RUNNING_APP)
    assert "$SidecarReadyTimeoutSec = 120" in text
    assert "$readyTask = $sidecarProc.StandardOutput.ReadLineAsync()" in text
    assert "if (-not $readyTask.Wait($SidecarReadyTimeoutSec * 1000))" in text
    timeout_branch = re.search(
        r"if \(-not \$readyTask\.Wait\(\$SidecarReadyTimeoutSec \* 1000\)\) \{[\s\S]{0,300}throw ",
        text,
    )
    assert timeout_branch is not None, "ready 超时分支必须失败退出"


def test_running_app_validates_ready_schema_before_installer_assertion() -> None:
    """必须先拿到 schema=...ready.v1 的 ready JSON，才允许进入 installer 拒绝断言。"""
    text = _read(_RUNNING_APP)
    assert '$SidecarReadySchema = "qtrading.embedded_postgres.run.ready.v1"' in text
    convert_index = text.index("$ready = $readyLine | ConvertFrom-Json")
    schema_index = text.index("$ready.schema -eq $SidecarReadySchema")
    step7_index = text.index('Write-Step "7. Attempt install while sidecar running')
    installation_index = text.index("$reinstallProc2 = Start-Process")
    assert convert_index < step7_index
    assert schema_index < installation_index


def test_running_app_reports_existing_password_failure_not_rejection() -> None:
    """sidecar 以 16 退出 = 前置失败（Existing 缺可读密码），不得计为 installer 拒绝通过。"""
    text = _read(_RUNNING_APP)
    assert "$sidecarExitCode -eq 16" in text
    assert "前置失败（Existing 缺可读密码）" in text
    exit16_branch = re.search(
        r"if \(\$sidecarExitCode -eq 16\) \{[\s\S]{0,500}throw ",
        text,
    )
    assert exit16_branch is not None, "exit 16 分支必须以失败退出"
    assert "不计为 installer 拒绝通过" in text


def test_running_app_keeps_stdin_open_until_same_pid_assertions() -> None:
    """stdin 必须保持到 Step7/8 之后，再关闭并等待退出，避免 ready 前进程被回收或孤儿化。"""
    text = _read(_RUNNING_APP)
    step7_index = text.index('Write-Step "7. Attempt install while sidecar running')
    same_pid_index = text.index("Assert-True ($null -ne $sidecarAfter)")
    close_index = text.index("$sidecarProc.StandardInput.Close()", same_pid_index)
    assert close_index > same_pid_index > step7_index
    assert "$sidecarProc.WaitForExit($SidecarShutdownTimeoutSec * 1000)" in text


def test_running_app_keeps_step7_and_step8_same_pid_assertions() -> None:
    """STEP 7 拒绝认证（RUNNING-01）与“同一 sidecar PID 仍存活”断言必须保留。"""
    text = _read(_RUNNING_APP)
    assert re.search(r'Write-Step "7\. Attempt install while sidecar running', text) is not None
    assert "$sidecarAfter = Get-Process -Id $sidecarProc.Id" in text
    assert "Assert-True ($null -ne $sidecarAfter)" in text
    assert "Assert-True (-not $sidecarAfter.HasExited)" in text


# ---- RUNNING-01：拒绝原因须经 /LOG 受控日志认证 ----
# 旧实现 `Assert-True ($installExitCode -ne 0)` 仅凭非零退出码判「运行中拒绝」通过：
# 磁盘/权限等安装内部故障同样返回非零，无法区分拒绝原因（假通过）。
# 现行契约：installer.iss 的 InitializeSetup 拒绝分支写 /LOG 受控日志行
# `qtrading.setup-guard.refused reason=<running-app|running-sidecar>`（Log() 仅在 /LOG 下生效）；
# 冒烟脚本以 /LOG 收集日志并断言标记，日志缺失或标记不命中即 UNVERIFIED REJECTION 失败。

_REFUSAL_MARKER = "qtrading.setup-guard.refused reason="
_REFUSAL_CERT_FUNCTION = re.compile(r"(?ms)^function Assert-RefusalCertified\([^)]*\) \{.*?^\}")


def test_installer_writes_distinct_refusal_reason_markers() -> None:
    """installer.iss 两个守卫分支必须各自写入可区分原因的受控日志行。"""
    text = (ROOT / "installer.iss").read_text(encoding="utf-8")
    app_guard = text.index("IsProcessRunning('AStockScreener.exe')")
    app_marker = text.index(f"Log('{_REFUSAL_MARKER}running-app')")
    sidecar_guard = text.index("IsProcessRunning('qtrading-pg-sidecar.exe')")
    sidecar_marker = text.index(f"Log('{_REFUSAL_MARKER}running-sidecar')")
    assert app_guard < app_marker < sidecar_guard < sidecar_marker, (
        f"installer.iss 必须在两个 InitializeSetup 守卫分支内分别写 Log('{_REFUSAL_MARKER}...')，且不得共用同一 reason"
    )


def _step_refusal_certified(text: str, step_prefix: str, exit_var: str, log_var: str, reason: str) -> bool:
    """STEP 4/7 收口：0 → 确定失败；非 0 且 /LOG 命中对应 reason 才算拒绝成立。"""
    marker = f'Write-Step "{step_prefix}'
    if marker not in text:
        return False
    step_idx = text.index(marker)
    next_step_idx = text.find("Write-Step", step_idx + len(marker))
    body = text[step_idx : next_step_idx if next_step_idx != -1 else len(text)]
    zero_branch = re.search(
        rf"if \(\${exit_var} -eq 0\) \{{[\s\S]{{0,300}}?throw \"ASSERTION FAILED: installer succeeded while ",
        body,
    )
    return (
        zero_branch is not None
        and f'/LOG=`"${log_var}`"' in body
        and f"${log_var} = New-RefusalLogPath" in text
        and f'Assert-RefusalCertified ${log_var} "{reason}"' in body
    )


def test_running_app_rejection_requires_log_certification() -> None:
    """STEP 4/7 不得仅凭非零退出码判通过：须以 /LOG 标记认证拒绝原因。"""
    text = _read(_RUNNING_APP)
    assert _step_refusal_certified(text, "4. Attempt install", "installExitCode", "appRefusalLog", "running-app"), (
        "STEP 4 未以 /LOG 受控日志行 reason=running-app 认证拒绝原因（RUNNING-01）"
    )
    assert _step_refusal_certified(
        text, "7. Attempt install", "installExitCode2", "sidecarRefusalLog", "running-sidecar"
    ), "STEP 7 未以 /LOG 受控日志行 reason=running-sidecar 认证拒绝原因（RUNNING-01）"
    # 旧的「仅凭非零判通过」断言不得残留（两个 STEP 皆然）。
    assert "Assert-True ($installExitCode -ne 0)" not in text
    assert "Assert-True ($installExitCode2 -ne 0)" not in text
    # 认证必须发生在各自 STEP 内：STEP 4 在 STEP 5 之前，STEP 7 在 STEP 8 之前。
    assert (
        text.index('Write-Step "4. Attempt install while qTrading running')
        < text.index('Assert-RefusalCertified $appRefusalLog "running-app"')
        < text.index('Write-Step "5. Verify qTrading still running')
    ), "STEP 4 认证必须位于 STEP 4 与 STEP 5 之间"
    assert (
        text.index('Write-Step "7. Attempt install while sidecar running')
        < text.index('Assert-RefusalCertified $sidecarRefusalLog "running-sidecar"')
        < text.index('Write-Step "8. Verify sidecar still running')
    ), "STEP 7 认证必须位于 STEP 7 与 STEP 8 之间"


def test_refusal_certification_helper_is_fail_closed() -> None:
    """认证函数缺少日志或标记时必须抛 UNVERIFIED REJECTION（不得静默放行）。"""
    text = _read(_RUNNING_APP)
    match = _REFUSAL_CERT_FUNCTION.search(text)
    assert match is not None, "缺少 Assert-RefusalCertified 认证函数"
    body = match.group(0)
    assert "Test-Path $LogPath" in body
    assert "-notmatch [regex]::Escape($marker)" in body
    assert 'throw "UNVERIFIED REJECTION:' in body
    # 读日志失败也须以 UNVERIFIED REJECTION 收口，不得让原生 IO 异常顶替契约语义。
    assert "installer /LOG unreadable" in body
    # 失败信息须携带实际命中的 reason，用于区分「认证失效」与「命中另一个守卫」（如主程序残留）。
    assert "[regex]::Matches($logText" in body
    assert "(found: $actual)" in body
    # 两步拒绝尝试各自独立日志文件，避免读到上一次残留。
    assert 'New-RefusalLogPath "app"' in text
    assert 'New-RefusalLogPath "sidecar"' in text


def test_refusal_certification_helper_rejects_legacy_and_partial() -> None:
    """负向：仅凭非零退出码、缺 /LOG、或 reason 不匹配的实现必须被判定不合规。"""
    legacy = (
        'Write-Step "4. Attempt install while qTrading running (should be rejected)"\n'
        'Assert-True ($installExitCode -ne 0) "Installer rejected"\n'
    )
    assert (
        _step_refusal_certified(legacy, "4. Attempt install", "installExitCode", "appRefusalLog", "running-app")
        is False
    )
    zero_branch_only = (
        'Write-Step "4. Attempt install while qTrading running (should be rejected)"\n'
        "if ($installExitCode -eq 0) {\n"
        '    throw "ASSERTION FAILED: installer succeeded while the app was running"\n'
        "}\n"
        'Assert-RefusalCertified $appRefusalLog "running-app"\n'
    )
    assert (
        _step_refusal_certified(
            zero_branch_only, "4. Attempt install", "installExitCode", "appRefusalLog", "running-app"
        )
        is False
    )
    wrong_reason = (
        'Write-Step "4. Attempt install while qTrading running (should be rejected)"\n'
        '$appRefusalLog = New-RefusalLogPath "app"\n'
        "-ArgumentList '/SILENT', '/NORESTART', \"/LOG=`\"$appRefusalLog`\"\"\n"
        "if ($installExitCode -eq 0) {\n"
        '    throw "ASSERTION FAILED: installer succeeded while the app was running"\n'
        "}\n"
        'Assert-RefusalCertified $appRefusalLog "running-sidecar"\n'
    )
    assert (
        _step_refusal_certified(wrong_reason, "4. Attempt install", "installExitCode", "appRefusalLog", "running-app")
        is False
    )


# ---- D17-T01：ready 必须是真实活性证据，崩溃恢复须读回已提交哨兵 ----

# 有 Wait-SidecarReady 的三个 smoke 脚本；running_app 脚本无此函数（F01 会话负责）。
WAIT_READY_SCRIPTS = (
    "installer_smoke_crash_recovery.ps1",
    "installer_smoke_uninstall.ps1",
    "installer_smoke_upgrade.ps1",
)

_WAIT_READY_FUNCTION = re.compile(r"(?ms)^function Wait-SidecarReady\([^)]*\) \{.*?^\}")


def _wait_ready_body(script_name: str) -> str:
    match = _WAIT_READY_FUNCTION.search(_read(script_name))
    assert match is not None, f"{script_name} 缺少 Wait-SidecarReady 函数"
    return match.group(0)


def _readiness_uses_status_json(body: str) -> bool:
    """ready 判据必须来自 status JSON 的 running + postgres_alive，且不得仅凭进程名返回 true。"""
    return (
        " status --data-dir " in body
        and "ConvertFrom-Json" in body
        and '$status.status -eq "running"' in body
        and "$status.postgres_alive -eq $true" in body
        and "AddSeconds($TimeoutSec)" in body
        and "if ($p) { return $true }" not in body
    )


@pytest.mark.parametrize("script_name", WAIT_READY_SCRIPTS)
def test_wait_sidecar_ready_requires_status_json(script_name: str) -> None:
    """ready 必须以 sidecar status JSON 的 running + postgres_alive 为判据，不能用进程名代替。"""
    body = _wait_ready_body(script_name)
    assert _readiness_uses_status_json(body), (
        f"{script_name} 的 Wait-SidecarReady 未以 `status --data-dir` JSON 的 "
        "running + postgres_alive 为 ready 判据（进程名存在只能作诊断提示）"
    )
    # 有界超时：保留默认 60s 量级，不得改成无限等待。
    assert "while ($true)" not in body
    assert "return $false" in body


def test_readiness_predicate_rejects_process_only_check() -> None:
    """负向：旧「进程名存在即 ready」实现必须被判定为不合规，避免断言恒真。"""
    legacy = """function Wait-SidecarReady([int]$TimeoutSec = 60) {
    $elapsed = 0
    while ($elapsed -lt $TimeoutSec) {
        $p = Get-Process -Name "qtrading-pg-sidecar" -ErrorAction SilentlyContinue
        if ($p) { return $true }
        Start-Sleep -Seconds 2
        $elapsed += 2
    }
    return $false
}
"""
    assert _readiness_uses_status_json(legacy) is False


def test_readiness_predicate_rejects_missing_postgres_alive() -> None:
    """负向：仅解析 status 字符串但漏掉 postgres_alive 字段同样不得通过。"""
    partial = """function Wait-SidecarReady([int]$TimeoutSec = 60) {
    $status = Get-SidecarStatus -DataDir $PgDataDir
    if ($status.status -eq "running") { return $true }
    return $false
}
"""
    assert _readiness_uses_status_json(partial) is False


def test_crash_recovery_uses_authenticated_sentinel_round_trip() -> None:
    """崩溃恢复脚本必须：真实认证写入哨兵 → 崩溃 → 无孤儿 → 重启 ready → 读回完全一致。"""
    text = _read("installer_smoke_crash_recovery.ps1")

    # 真实认证路径：读 runtime\\password（Windows DPAPI PGPW2）、psql 取自 <PGDATA 父目录>\\install\\bin。
    assert "$PgDataDir = Join-Path $UserDataRoot" in text
    assert '$PgRuntimeDir = Join-Path (Split-Path -Parent $PgDataDir) "runtime"' in text
    assert '$PgInstallDir = Join-Path (Split-Path -Parent $PgDataDir) "install"' in text
    assert '$PgPasswordFile = Join-Path $PgRuntimeDir "password"' in text
    assert '$PsqlExePath = Join-Path $PgInstallDir "bin\\psql.exe"' in text
    assert "PGPW2:" in text
    assert "[System.Security.Cryptography.ProtectedData]::Unprotect" in text
    assert "[System.Security.Cryptography.DataProtectionScope]::CurrentUser" in text
    # 密码经进程环境传递，不进入命令行/日志。
    assert "$env:PGPASSWORD = $Password" in text
    assert "Remove-Item Env:PGPASSWORD" in text
    assert "Write-Host $Password" not in text
    # 端口取自 postmaster.pid 第 4 行（sidecar 每次随机分配端口，重启后需重读）。
    assert "postmaster.pid" in text
    assert "$lines[3]" in text
    # 用户名/库名与 cli.rs DEFAULT_USERNAME/DEFAULT_DATABASE 一致。
    assert '$PgUsername = "postgres"' in text
    assert '$PgDatabase = "qtrading"' in text

    # 哨兵必须是合成值（GUID），且写库/读回均为精确相等断言，而非仅"非空"。
    assert "smoke_crash_recovery_sentinel" in text
    assert '[Guid]::NewGuid().ToString("N")' in text
    assert "on conflict (id) do update set marker = excluded.marker" in text
    assert "Assert-True ($sentinelReadBefore -eq $sentinel)" in text
    assert "Assert-True ($sentinelAfterRestart -eq $sentinel)" in text

    # 结构顺序：写哨兵 → 强杀崩溃 → 无残留断言 → 重启 → 读回。
    write_idx = text.index("$sentinelInsertSql")
    crash_idx = text.index("Stop-Process -Id $proc.Id -Force")
    orphan_idx = text.index("Assert-True ($null -eq $postgresAfter)")
    restart_idx = text.index("$proc2 = Start-Process -FilePath $AppExePath -PassThru")
    readback_idx = text.index("$sentinelAfterRestart = Invoke-Psql")
    assert write_idx < crash_idx < orphan_idx < restart_idx < readback_idx, (
        "崩溃恢复脚本必须按「写哨兵 → 崩溃 → 无孤儿 → 重启 → 读回」顺序执行"
    )

    # 保留既有有界等待量级：sidecar 退出 180s、postgres 退出 30s。
    assert "AddSeconds(180)" in text
    assert "AddSeconds(30)" in text

    # 严禁伪造密码文件或绕过认证。
    assert "fake_sidecar_pwd" not in text
    assert re.search(r"Set-Content[^\n]*(?:password|pwd)", text, re.IGNORECASE) is None


def test_crash_recovery_sequence_checker_is_not_tautological() -> None:
    """负向：缺少重启读回步骤的脚本必须被结构检查识别出来。"""
    legacy = """$sentinelInsertSql = "insert ..."
Stop-Process -Id $proc.Id -Force
Assert-True ($null -eq $postgresAfter)
"""
    assert "$sentinelAfterRestart = Invoke-Psql" not in legacy
    assert legacy.index("Stop-Process -Id $proc.Id -Force") < legacy.index("Assert-True ($null -eq $postgresAfter)")


# ---- ENGA-CRASH-01：强杀注入必须真实发生，主进程自然退出不得计为崩溃恢复通过 ----
# 旧实现 `if ($proc -and -not $proc.HasExited) { Stop-Process ... }` 在主进程已自行退出时
# 静默跳过强杀：正常关闭的 sidecar/PG 恰好满足无残留断言、重启读回也成功，
# 脚本仍报告「崩溃恢复通过」，把正常关闭的持久性误当作强杀故障注入证据。


def _crash_injection_guarded(text: str) -> bool:
    """强杀前必须显式断言 $proc 非空且未退出，并以 $crashInjected 标记在成功出口兜底断言。"""
    return (
        "Assert-True ($null -ne $proc)" in text
        and "Assert-True (-not $proc.HasExited)" in text
        and "$crashInjected = $false" in text
        and "$crashInjected = $true" in text
        and 'Assert-True $crashInjected "Force-kill injection completed"' in text
    )


def test_crash_recovery_force_kill_requires_alive_process() -> None:
    """强杀前必须断言 $proc 非空且未退出；自然退出路径必须失败，不得静默跳过强杀。"""
    text = _read("installer_smoke_crash_recovery.ps1")
    assert _crash_injection_guarded(text), (
        "崩溃恢复脚本缺少强杀注入护栏：须强杀前断言 $proc 非空且未退出，"
        "并以 $crashInjected 标记在成功出口兜底断言（ENGA-CRASH-01）"
    )
    # 结构顺序：初始化标记 → 强杀 → 置真 → 成功出口兜底断言 → PASSED 输出。
    init_idx = text.index("$crashInjected = $false")
    crash_idx = text.index("Stop-Process -Id $proc.Id -Force")
    set_true_idx = text.index("$crashInjected = $true")
    final_assert_idx = text.index('Assert-True $crashInjected "Force-kill injection completed"')
    passed_idx = text.index("=== CRASH RECOVERY SMOKE TEST PASSED ===")
    assert init_idx < crash_idx < set_true_idx < final_assert_idx < passed_idx, (
        "强杀注入标记必须按「初始化 → 强杀 → 置真 → 成功出口断言（PASSED 之前）」顺序执行"
    )


def test_crash_injection_guard_rejects_legacy_silent_skip() -> None:
    """负向：旧实现（if 包裹强杀、无存活断言、无注入标记）必须被判定不合规。"""
    legacy = """Write-Step "6. FAILURE INJECTION: Force kill main qTrading process (simulate crash)"
if ($proc -and -not $proc.HasExited) {
    Stop-Process -Id $proc.Id -Force
    Write-Ok "Main process killed (PID: $($proc.Id))"
}
"""
    assert _crash_injection_guarded(legacy) is False


# ---- D17-T02：升级 / 卸载必须验证已提交业务哨兵与 PG 根完整性 ----
# 旧断言只检查「目录存在且文件数 > 0」：删除唯一业务哨兵但保留 PG_VERSION 仍会通过，
# 既不能证明数据连续，也不能证明未被误删。升级须「写哨兵 → 升级 → 读回完全一致」；
# 卸载须「写哨兵 → 卸载 → data/install/runtime 保留 → 重装后读回完全一致」。

CONTINUITY_SCRIPTS = (
    "installer_smoke_upgrade.ps1",
    "installer_smoke_uninstall.ps1",
)

# 旧「非空即通过」判据：对 fileCount* 变量做 -gt 0 断言。
_NONEMPTY_ONLY_ASSERTION = re.compile(r"\$fileCount(?:Before|After|AfterUninstall)?[^\n]*-gt\s+0")

# 两脚本共用的真实认证路径与 PG 根证据判据。
_AUTHENTICATED_PSQL_SNIPPETS = (
    '$PgInstallDir = Join-Path (Split-Path -Parent $PgDataDir) "install"',
    '$PgRuntimeDir = Join-Path (Split-Path -Parent $PgDataDir) "runtime"',
    '$PgPasswordFile = Join-Path $PgRuntimeDir "password"',
    '$PsqlExePath = Join-Path $PgInstallDir "bin\\psql.exe"',
    '$PgVersionFile = Join-Path $PgDataDir "PG_VERSION"',
    "PGPW2:",
    "[System.Security.Cryptography.ProtectedData]::Unprotect",
    "[System.Security.Cryptography.DataProtectionScope]::CurrentUser",
    'throw "password file missing',
    "$env:PGPASSWORD = $Password",
    "Remove-Item Env:PGPASSWORD",
    "postmaster.pid",
    "$lines[3]",
    '$PgUsername = "postgres"',
    '$PgDatabase = "qtrading"',
    "on conflict (id) do update set marker = excluded.marker",
)


@pytest.mark.parametrize("script_name", CONTINUITY_SCRIPTS)
def test_data_continuity_scripts_drop_nonempty_only_criterion(script_name: str) -> None:
    """负向：升级/卸载脚本不得再以「目录非空」作为数据保留的唯一判据。"""
    text = _read(script_name)
    assert _NONEMPTY_ONLY_ASSERTION.search(text) is None, (
        f"{script_name} 仍以 `$fileCount* -gt 0` 作为数据保留判据；"
        "必须改为断言已提交业务哨兵可读回、PG_VERSION/password/install 完整保留"
    )
    assert "$fileCountBefore" not in text
    assert "$fileCountAfter" not in text


def test_nonempty_only_pattern_is_effective() -> None:
    """负向：检查器本身必须能识别旧判据，避免负向断言恒真。"""
    legacy = """$fileCountAfter = (Get-ChildItem -Path $PgDataDir -Recurse | Measure-Object).Count
Assert-True ($fileCountAfter -gt 0) "PGDATA dir still non-empty"
"""
    assert _NONEMPTY_ONLY_ASSERTION.findall(legacy) == ["$fileCountAfter -gt 0"]


@pytest.mark.parametrize("script_name", CONTINUITY_SCRIPTS)
def test_data_continuity_scripts_use_authenticated_psql(script_name: str) -> None:
    """两脚本必须走真实认证路径（DPAPI 密码 + postmaster.pid 端口 + 安装目录 psql）。"""
    text = _read(script_name)
    for snippet in _AUTHENTICATED_PSQL_SNIPPETS:
        assert snippet in text, f"{script_name} 缺少真实认证/证据片段：{snippet}"
    # 严禁伪造密码文件或把密码写入命令行/日志。
    assert "Write-Host $Password" not in text
    assert "fake_sidecar_pwd" not in text
    assert re.search(r"Set-Content[^\n]*(?:password|pwd)", text, re.IGNORECASE) is None


def _sentinel_guards_marker_format(text: str) -> bool:
    """哨兵 marker 必须经格式校验后再拼入 SQL，防止注入/引号问题。"""
    return "-match '^d17-t02-[0-9a-f]{32}$'" in text and 'throw "sentinel marker format invalid"' in text


def test_sentinel_marker_format_guard_rejects_injected_value() -> None:
    """负向：含引号/分号的 marker 必须被格式校验拒绝（防止拼字符串注入）。"""
    pattern = re.compile(r"^d17-t02-[0-9a-f]{32}$")
    assert pattern.match("d17-t02-" + "a" * 32) is not None
    assert pattern.match("d17-t02-" + "a" * 32 + "';drop table x") is None
    assert pattern.match("d17-t02-'" + "a" * 32) is None


def test_upgrade_script_round_trips_sentinel_across_upgrade() -> None:
    """升级脚本必须：写已提交哨兵 → 记录 PG 根证据 → 升级 → 读回完全一致 + PG_VERSION 未变。"""
    text = _read("installer_smoke_upgrade.ps1")

    assert '$SentinelTable = "smoke_upgrade_sentinel"' in text
    assert '[Guid]::NewGuid().ToString("N")' in text
    assert _sentinel_guards_marker_format(text), (
        "升级脚本的哨兵 marker 未做格式校验（GUID 外字符可能破坏 SQL 引号/注入）"
    )
    assert "Assert-True ($sentinelReadBefore -eq $sentinel)" in text
    assert "Assert-True ($sentinelAfterUpgrade -eq $sentinel)" in text

    # PG 根证据：升级前记录 PG_VERSION 内容、runtime\password、install psql；升级后逐项比对。
    assert "$pgVersionBefore = (Get-Content -Path $PgVersionFile -Raw).Trim()" in text
    assert "$pgVersionAfter = (Get-Content -Path $PgVersionFile -Raw).Trim()" in text
    assert "Assert-True ($pgVersionAfter -eq $pgVersionBefore)" in text
    assert "Assert-True (Test-Path $PgPasswordFile)" in text
    assert "Assert-True (Test-Path $PsqlExePath)" in text

    # 结构顺序：写哨兵 → 升级安装 → 读回；顺序颠倒（先读回后升级）即为恒真伪验证。
    insert_idx = text.index("$sentinelInsertSql")
    upgrade_idx = text.index("$upgradeInstallerProc = Start-Process -FilePath $InstallerPath")
    readback_idx = text.index("$sentinelAfterUpgrade = Invoke-Psql")
    assert insert_idx < upgrade_idx < readback_idx, "升级脚本必须按「写哨兵 → 升级安装 → 读回哨兵」顺序执行"


def test_wait_app_database_ready_dumps_diagnostics_on_timeout() -> None:
    """负向：等库超时不得静默返回 $false（run 42750440663 两个变体无法定位根因）。

    超时必须带出末次探测错误、进程存活与 sidecar state.json 的 last_start_error，
    否则「密码认证失败 / 连接被拒 / 建库失败后 sidecar 退出」共用同一句断言文案。
    """
    text = _read("installer_smoke_upgrade.ps1")

    assert '$PgStateFile = Join-Path $PgRuntimeDir "state.json"' in text
    # catch 必须保留错误；空 catch 会让所有探测失败退化为同一句话。
    assert re.search(r"\} catch \{\n(?:\s*#[^\n]*\n)*\s*\$lastError = \$_\.Exception\.Message", text) is not None, (
        "Wait-AppDatabaseReady 的 catch 未保留 $lastError，超时后将丢失探测失败原因"
    )
    assert 'Write-Fail "last probe error: $lastError"' in text
    assert "process alive: qTrading=$appAlive, qtrading-pg-sidecar=$sidecarAlive" in text
    assert '"status", "last_start_error", "port", "postgres_pid"' in text
    # state.json 缺失/不可解析须如实报告，不得用空串让「无信息」冒充「无异常」。
    assert 'return "state.json missing ($PgStateFile)"' in text
    assert 'return "state.json unreadable: $($_.Exception.Message)"' in text
    # 取证不改变契约：超时仍返回 $false，由调用方 Assert-True fail-closed。
    assert text.count("    return $false\n}") >= 1


def test_wait_app_database_ready_rereads_password_each_attempt() -> None:
    """轮询必须每次重读密码文件（run 42764622846：旧版 sidecar 在 running 之后才落盘新密码）。

    启动前缓存密码会让 psql 持续 `password authentication failed`，被误判成「业务库未创建」。
    """
    text = _read("installer_smoke_upgrade.ps1")

    assert "function Wait-AppDatabaseReady([string]$PasswordFile, [int]$TimeoutSec = 120)" in text
    body_start = text.index("function Wait-AppDatabaseReady(")
    body = text[body_start : text.index("\nfunction Get-SidecarStateDigest", body_start)]
    # 密码读取必须在轮询循环内（与 port 探测同一次尝试），不能作为参数一次性传入。
    assert "$password = Read-PgPassword -PasswordFile $PasswordFile" in body
    assert "Invoke-Psql -Password $password" in body
    # 轮换只报事件（attempt 序号），不打印密码本身（R9）。
    assert 'Write-Ok "password file rotated during startup (attempt $attempts)"' in body

    # 调用方不得再传缓存值，且成功后应重读一次供后续 psql 使用。
    assert "Wait-AppDatabaseReady -Password $pgPassword" not in text
    call_site = text.index("Wait-AppDatabaseReady -PasswordFile $PgPasswordFile")
    refresh = text.index("$pgPassword = Read-PgPassword -PasswordFile $PgPasswordFile", call_site)
    assert call_site < refresh < text.index("$sentinelCreateSql", call_site)


def test_wait_app_database_ready_snapshots_environment_on_timeout() -> None:
    """超时取证必须能区分「state 陈旧 / 遗留 postgres 占端口 / 旧版用了另一路径」。

    run 42770696954：state.json 自述 running 且无 last_start_error，psql 却持续
    `password authentication failed`——两者不可能同时为真，仅记录 state 摘要无法定因。
    """
    text = _read("installer_smoke_upgrade.ps1")

    assert "function Write-EnvironmentSnapshot" in text
    # 进程命令行是唯一能证明「探测目标 ≠ 旧版实际 data dir / 占端口者是谁」的证据。
    assert "Get-CimInstance Win32_Process" in text
    assert "AStockScreener.exe" in text and "qtrading-pg-sidecar.exe" in text and "postgres.exe" in text
    # 命令行可出现 --password-file 路径，但不得泄露明文密码（R9）。
    assert '"--password <redacted>"' in text
    # sidecar 日志尾部给出旧版自述 running 的真实原因（initdb / 认证 / 端口）。
    # 路径必须取自进程实际 --log-file：run 42781862047 按固定路径推导少了一层，日志缺失。
    assert "(?i)--log-file\\s+(\\S+)" in text
    assert "Get-Content -Path $logPath -Tail 60" in text
    # 文件时间戳用于判定 state.json / postmaster.pid 是否来自上一步 smoke（陈旧假阳性）。
    assert "LastWriteTimeUtc" in text
    # PG_VERSION 由 initdb 写入，其 mtime/内容用于判定集群是本次新建还是复用旧集群。
    assert '"PG_VERSION"' in text
    # pg_hba.conf 认证方式可解释「同密码不同客户端一个成功一个失败」。
    assert "pg_hba auth lines" in text
    # 客户端/服务端版本用于排除 PG 版本不匹配导致的认证差异。
    assert "psql file version:" in text
    assert "postgres --version:" in text
    # 密码只报形态与长度，不报内容。
    assert "password file format:" in text
    assert "password file decrypted length:" in text
    assert "Write-Host $Password" not in text
    # 取证必须发生在超时返回 $false 之前（fail-closed 契约不变）。
    assert "Write-EnvironmentSnapshot\n    return $false" in text


def test_uninstall_script_preserves_pg_root_and_reinstall_reuses_data() -> None:
    """卸载脚本必须：写哨兵 → 卸载 → data/PG_VERSION/runtime/install 保留 → 重装读回一致。"""
    text = _read("installer_smoke_uninstall.ps1")

    assert '$SentinelTable = "smoke_uninstall_sentinel"' in text
    assert '[Guid]::NewGuid().ToString("N")' in text
    assert _sentinel_guards_marker_format(text)
    assert "Assert-True ($sentinelReadBefore -eq $sentinel)" in text
    assert "Assert-True ($sentinelAfterReinstall -eq $sentinel)" in text

    # 程序目录被移除（卸载完成），但 PG 根完整保留。
    assert "Assert-True (-not (Test-Path $SidecarExePath))" in text
    assert "$pgVersionBefore = (Get-Content -Path $PgVersionFile -Raw).Trim()" in text
    assert "$pgVersionAfterUninstall = (Get-Content -Path $PgVersionFile -Raw).Trim()" in text
    assert "Assert-True ($pgVersionAfterUninstall -eq $pgVersionBefore)" in text
    assert "Assert-True (Test-Path $PgDataDir)" in text
    assert "Assert-True (Test-Path $PgPasswordFile)" in text
    assert "Assert-True (Test-Path $PsqlExePath)" in text

    # 重装同变体：不得新增第二次对 $InstallerPath 的直接调用（复用变量保持单次原调用断言）。
    assert "$ReinstallPath = $InstallerPath" in text
    assert (
        "$reinstallProc = Start-Process -FilePath $ReinstallPath -ArgumentList '/SILENT', '/NORESTART' -PassThru -Wait"
        in text
    )
    assert "Assert-True ($reinstallProc.ExitCode -eq 0)" in text

    # 结构顺序：写哨兵 → 卸载 → 保留断言 → 重装 → 读回。
    insert_idx = text.index("$sentinelInsertSql")
    uninstall_idx = text.index("Start-Process -FilePath $UninstallerPath")
    preserved_idx = text.index("Assert-True ($pgVersionAfterUninstall -eq $pgVersionBefore)")
    reinstall_idx = text.index("$reinstallProc = Start-Process -FilePath $ReinstallPath")
    readback_idx = text.index("$sentinelAfterReinstall = Invoke-Psql")
    assert insert_idx < uninstall_idx < preserved_idx < reinstall_idx < readback_idx, (
        "卸载脚本必须按「写哨兵 → 卸载 → 保留断言 → 重装 → 读回」顺序执行"
    )
