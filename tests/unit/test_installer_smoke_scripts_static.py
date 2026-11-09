"""installer / Windows 默认路径静态契约（Linux CI 无法执行 Windows 脚本，只读文本断言）。

守护五类已修复缺陷：
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
5. 升级脚本下载旧版安装包曾以全局 TEMP 固定文件名落盘，且 ``2>&1 | Out-Null`` 吞掉
   gh 返回码（Windows PowerShell 5.1 下原生命令非零退出不因 ``ErrorActionPreference``
   变为终止异常），仅以 ``Test-Path`` 判成功——下载失败（网络/鉴权/资产匹配）时命中
   历史残留文件仍继续安装，网络失败被误报为「指定旧版升级通过」。必须：本次唯一
   目录（Guid）落盘 + 显式校验 ``$LASTEXITCODE`` + 落盘非空校验，失败不进入安装步骤，
   并在整个主流程末尾（STEP 10 回滚腿之后）由 ``finally`` 清理下载目录。
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
    """STEP 7 fail-closed 拒绝收口（ENGA-RUNNING-01）与“同一 sidecar PID 仍存活”断言必须保留。"""
    text = _read(_RUNNING_APP)
    assert re.search(r'Write-Step "7\. Attempt install while sidecar running', text) is not None
    assert "$sidecarAfter = Get-Process -Id $sidecarProc.Id" in text
    assert "Assert-True ($null -ne $sidecarAfter)" in text
    assert "Assert-True (-not $sidecarAfter.HasExited)" in text


# ---- ENGA-RUNNING-01：拒绝假通过收口——非零退出码不能认证运行中拒绝原因 ----
# 旧实现 `Assert-True ($installExitCode -ne 0)` 仅凭非零退出码判「运行中拒绝」通过：
# 磁盘/权限等安装内部故障同样返回非零，而 installer.iss 的 InitializeSetup 静默分支
# 只有 Result=False/Exit（无受控日志、无唯一退出码），拒绝原因无法认证。
# fail-closed 两支收口：退出码 0 = 安装成功覆盖运行中的应用（确定失败）；
# 非 0 = 拒绝原因不可认证，同样失败（有意不冒充「正常拒绝已验证」，ENGA-RUNNING-01）。

_REJECTION_UNVERIFIED_MSG = (
    "UNVERIFIED REJECTION: installer failed (exit code ${var}), but InitializeSetup refusal cause cannot be certified"
)


def _rejection_fail_closed(text: str, exit_var: str, scenario: str) -> bool:
    """STEP 4/7 拒绝断言必须两支收口：退出码 0 → 确定失败；非 0 → 不可认证也失败。"""
    zero_branch = re.search(
        rf"if \(\${exit_var} -eq 0\) \{{[\s\S]{{0,300}}?throw \"ASSERTION FAILED: installer succeeded "
        rf"while the {scenario} was running\"",
        text,
    )
    unverified = _REJECTION_UNVERIFIED_MSG.format(var=exit_var) in text
    return zero_branch is not None and unverified


def test_running_app_rejection_assertions_are_fail_closed() -> None:
    """STEP 4/7 不得以「非零退出码」判通过：0 → ASSERTION FAILED；非 0 → UNVERIFIED REJECTION。"""
    text = _read(_RUNNING_APP)
    assert _rejection_fail_closed(text, "installExitCode", "app"), (
        "STEP 4 缺少 fail-closed 收口：须先断言退出码非 0（0 = 安装成功覆盖运行中的应用，确定失败），"
        "非 0 时以 UNVERIFIED REJECTION 失败（拒绝原因不可认证，ENGA-RUNNING-01）"
    )
    assert _rejection_fail_closed(text, "installExitCode2", "sidecar"), (
        "STEP 7 缺少与 STEP 4 同语义的 fail-closed 收口（ENGA-RUNNING-01）"
    )
    # 旧的「仅凭非零判通过」断言不得残留（两个 STEP 皆然）。
    assert "Assert-True ($installExitCode -ne 0)" not in text
    assert "Assert-True ($installExitCode2 -ne 0)" not in text
    # 收口必须发生在各自 STEP 内：STEP 4 收口在 STEP 5 之前，STEP 7 收口在 STEP 8 之前。
    step4_idx = text.index('Write-Step "4. Attempt install while qTrading running')
    step5_idx = text.index('Write-Step "5. Verify qTrading still running')
    step7_idx = text.index('Write-Step "7. Attempt install while sidecar running')
    step8_idx = text.index('Write-Step "8. Verify sidecar still running')
    zero4_idx = text.index("if ($installExitCode -eq 0)")
    zero7_idx = text.index("if ($installExitCode2 -eq 0)")
    assert step4_idx < zero4_idx < step5_idx, "STEP 4 收口必须位于 STEP 4 与 STEP 5 之间"
    assert step7_idx < zero7_idx < step8_idx, "STEP 7 收口必须位于 STEP 7 与 STEP 8 之间"


def test_rejection_fail_closed_helper_rejects_legacy_and_partial() -> None:
    """负向：旧「仅凭非零判通过」与只改一半（缺 UNVERIFIED throw）的实现必须被判定不合规。"""
    legacy = (
        'Write-Host "  Installer exit code: $installExitCode"\n'
        'Assert-True ($installExitCode -ne 0) "Installer rejected (exit code non-zero: $installExitCode)"\n'
    )
    assert _rejection_fail_closed(legacy, "installExitCode", "app") is False
    partial_zero_branch_only = (
        "if ($installExitCode -eq 0) {\n"
        '    throw "ASSERTION FAILED: installer succeeded while the app was running"\n'
        "}\n"
    )
    assert _rejection_fail_closed(partial_zero_branch_only, "installExitCode", "app") is False


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


# ---- ENGA-UPGRADE-01：旧版安装器下载必须 fail-closed（返回码/非空/唯一落盘） ----

# 旧缺陷实现特征：全局 TEMP 固定文件名（下载前不清除，历史残留被当作本次下载结果）。
_LEGACY_TEMP_DOWNLOAD = re.compile(r"\$env:TEMP\\AStockScreener-Setup-\$OldVariant-\$OldVersion\.exe")

# fail-closed 下载链的顺序标记：唯一目录 → gh 调用 → 返回码校验 → 落盘存在 → 落盘非空 → 才允许安装。
_DOWNLOAD_FAIL_CLOSED_MARKERS = (
    "$oldDownloadDir = Join-Path (Get-Location)",
    "gh release download",
    "Assert-True ($LASTEXITCODE -eq 0)",
    "Assert-True ($null -ne $downloadedInstallerItem)",
    "Assert-True ($downloadedInstallerItem.Length -gt 0)",
    "Start-Process -FilePath $oldInstaller",
)


def _download_fails_closed(text: str) -> bool:
    """下载链必须按唯一目录 → gh → 返回码 → 存在 → 非空 → 安装的顺序 fail-closed。"""
    try:
        positions = [text.index(marker) for marker in _DOWNLOAD_FAIL_CLOSED_MARKERS]
    except ValueError:
        return False
    return positions == sorted(positions)


def test_upgrade_download_fails_closed_on_gh_exit_code() -> None:
    """gh 返回码/落盘存在/落盘非空必须在校验通过后才允许启动旧版安装器。

    Windows PowerShell 5.1（CI 以 ``powershell -File`` 运行本脚本）下原生命令非零退出
    不因 ``ErrorActionPreference = "Stop"`` 变为终止异常，必须显式校验 ``$LASTEXITCODE``。
    """
    text = _read("installer_smoke_upgrade.ps1")
    assert _download_fails_closed(text), (
        "升级脚本下载链未按「唯一目录 → gh → $LASTEXITCODE 校验 → 落盘存在 → 落盘非空 → 安装」"
        "顺序 fail-closed；下载失败不得继续执行既有/残留安装器文件"
    )


def test_download_fails_closed_guard_rejects_legacy_temp_name() -> None:
    """负向：旧实现（TEMP 固定名 + 返回码被管道吞掉 + 仅 Test-Path）必须被检查器识别，避免断言恒真。"""
    legacy = (
        '    $oldInstaller = "$env:TEMP\\AStockScreener-Setup-$OldVariant-$OldVersion.exe"\n'
        '    gh release download "v$OldVersion" --repo $env:GITHUB_REPOSITORY `\n'
        '        --pattern "AStockScreener-Setup-$OldVariant.exe" `\n'
        "        --output $oldInstaller 2>&1 | Out-Null\n"
        "    if (-not (Test-Path $oldInstaller)) {\n"
        '        throw "Failed to download old version installer v$OldVersion"\n'
        "    }\n"
        "    $oldInstallerProc = Start-Process -FilePath $oldInstaller "
        "-ArgumentList '/SILENT', '/NORESTART' -PassThru -Wait\n"
    )
    assert _LEGACY_TEMP_DOWNLOAD.findall(legacy) == ["$env:TEMP\\AStockScreener-Setup-$OldVariant-$OldVersion.exe"]
    assert not _download_fails_closed(legacy), "旧 TEMP 固定名实现不应通过 fail-closed 顺序检查"


def test_upgrade_download_uses_unique_per_run_dir_and_cleans_up_after_rollback() -> None:
    """下载落盘必须是本次运行唯一目录（Guid），且清理只在 finally、位于回滚腿之后。"""
    text = _read("installer_smoke_upgrade.ps1")
    assert "$oldDownloadDir = Join-Path (Get-Location)" in text
    assert '("installer-upgrade-" + [Guid]::NewGuid().ToString("N"))' in text
    assert _LEGACY_TEMP_DOWNLOAD.search(text) is None, (
        "升级脚本不得再用全局 TEMP 固定文件名下载（历史残留会被当作本次下载结果，缓存替代下载使网络失败误报为升级通过）"
    )
    # StrictMode Latest 下 finally 引用未定义变量会报错：目录变量必须在 try 前预置 $null。
    assert "$oldDownloadDir = $null" in text
    # 清理不得发生在回滚腿之前（回滚腿复用同一 $oldInstaller），且只在 finally 出现一次。
    assert text.count("Remove-Item -LiteralPath $oldDownloadDir") == 1
    rollback_idx = text.index("$rollbackProc = Start-Process -FilePath $oldInstaller")
    cleanup_idx = text.index("Remove-Item -LiteralPath $oldDownloadDir -Recurse -Force")
    assert cleanup_idx > rollback_idx, "下载目录清理必须位于 STEP 10 回滚腿之后（finally 在 try/catch 末尾）"


def test_upgrade_download_does_not_swallow_stderr() -> None:
    """gh 调用不得用 ``2>&1 | Out-Null`` 吞掉 stderr（失败原因直出控制台，返回码兜底）。"""
    text = _read("installer_smoke_upgrade.ps1")
    gh_idx = text.index("gh release download")
    exit_code_idx = text.index("Assert-True ($LASTEXITCODE -eq 0)", gh_idx)
    window = text[gh_idx:exit_code_idx]
    assert "2>&1" not in window and "Out-Null" not in window, (
        "gh release download 调用不得以 `2>&1 | Out-Null` 吞掉 stderr；"
        "失败原因应直出控制台，返回码由 $LASTEXITCODE 显式校验"
    )
    # 负向有效性：旧「--output $oldInstaller 2>&1 | Out-Null」写法必须被窗口检查识别。
    legacy = (
        'gh release download "v$OldVersion" --repo $env:GITHUB_REPOSITORY `\n'
        '    --pattern "AStockScreener-Setup-$OldVariant.exe" `\n'
        "    --output $oldInstaller 2>&1 | Out-Null\n"
        '    if (-not (Test-Path $oldInstaller)) { throw "x" }\n'
        '    Assert-True ($LASTEXITCODE -eq 0) "y"\n'
    )
    legacy_window = legacy[legacy.index("gh release download") : legacy.index("Assert-True ($LASTEXITCODE -eq 0)")]
    assert "2>&1" in legacy_window and "Out-Null" in legacy_window


def test_upgrade_download_notes_identity_verification_boundary() -> None:
    """下载段必须以 NOTE(lazy) 标记「未校验受信摘要/版本身份」边界（三要素齐全）。

    本片段仅落实下载返回码、落盘非空与唯一文件；不得在身份验证契约确认前编造校验值。
    """
    text = _read("installer_smoke_upgrade.ps1")
    assert "NOTE(lazy): 旧版安装器下载仅校验返回码/落盘非空/唯一目录，未校验受信摘要与版本身份." in text
    assert "ceiling: 资产名仅按变体精确匹配" in text
    assert "upgrade: Release 提供旧资产校验值或版本身份" in text
