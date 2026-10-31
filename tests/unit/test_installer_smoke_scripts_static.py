"""installer smoke PowerShell 脚本静态契约（Linux CI 无法执行，只读文本断言）。

守护两类已修复缺陷：
1. Inno Setup 安装器 / 卸载器是 GUI 程序，PowerShell 调用运算符 ``&`` 不等待其结束；
   必须以 ``Start-Process ... -Wait`` 调用（否则后续断言会读到半解包目录）；
2. 升级脚本下载旧版安装包使用变体 glob——``installer.iss`` 的
   ``OutputBaseFilename=AStockScreener-Setup-{#TargetVariant}`` 使资产名为
   ``-embedded-cpu`` / ``-embedded-cuda``（历史版本可能为 ``-embedded``），
   写死单一 ``-embedded.exe`` 无法命中现行命名。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.meta]

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = ROOT / "scripts"
SMOKE_SCRIPTS = (
    "installer_smoke_crash_recovery.ps1",
    "installer_smoke_running_app.ps1",
    "installer_smoke_uninstall.ps1",
    "installer_smoke_upgrade.ps1",
)

# 调用运算符直接拉起 GUI 安装器/卸载器（`& $InstallerPath ...` / `& $oldInstaller ...`）
_GUI_CALL_OPERATOR = re.compile(r"(?m)^\s*&\s*\$(?:InstallerPath|UninstallerPath|existingUninstaller|oldInstaller)\b")


def _read(name: str) -> str:
    return (SCRIPTS_DIR / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("script_name", SMOKE_SCRIPTS)
def test_gui_installers_not_invoked_via_call_operator(script_name: str) -> None:
    """任何 smoke 脚本都不得用 ``&`` 直接拉起 GUI 安装器/卸载器（不会等待其结束）。"""
    assert _GUI_CALL_OPERATOR.search(_read(script_name)) is None, (
        f"{script_name} 使用 `& $InstallerPath/$UninstallerPath` 调用 GUI 程序；"
        "须改用 Start-Process -Wait（GUI 程序 `&` 不等待，会读到半解包目录）"
    )


@pytest.mark.parametrize("script_name", ["installer_smoke_uninstall.ps1", "installer_smoke_upgrade.ps1"])
def test_installer_invocation_waits_and_checks_exit_code(script_name: str) -> None:
    """安装新版本必须以 ``Start-Process -FilePath $InstallerPath -PassThru -Wait`` 调用并校验退出码。"""
    text = _read(script_name)
    invocation = re.search(r"Start-Process -FilePath \$InstallerPath[^\n]*-PassThru -Wait", text)
    assert invocation is not None, f"{script_name} 未以 Start-Process -Wait 调用安装器"
    assert "($installerProc.ExitCode -eq 0)" in text or "($upgradeInstallerProc.ExitCode -eq 0)" in text, (
        f"{script_name} 未校验安装器退出码"
    )


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
