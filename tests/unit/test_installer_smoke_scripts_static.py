"""installer / Windows 默认路径静态契约（Linux CI 无法执行 Windows 脚本，只读文本断言）。

守护三类已修复缺陷：
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
    assert OLD_SINGLE_LAYER_WINDOWS_PGDATA.search(old_path) is not None
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
