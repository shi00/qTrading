"""Tests for resources/maintenance 离线维护脚本的静态一致性（UIR-F16-001）。

只读取脚本文本做静态断言；SH 分支另有 POSIX-only 执行型用例（stub sidecar 记录收到的
参数），Windows CI 上自动跳过（不做 ``.bat`` 执行，避免依赖 cmd）。

守护:
- 分派分支的参数位与 ``shift`` 位置自洽（shift 后取 ``%1``，未 shift 取 ``%2``），
  防止 ``dump`` / ``restore`` 按 ``<command> <file>`` 调用时恒走 Usage 分支；
- 每个分支「缺参守卫」与「sidecar 实际传参」使用同一个位置参数；
- 帮助文本列出的命令集合与实际分派分支集合一致，dump/restore 用法与 README 一致；
- ``.bat`` 与 ``.sh`` 两条脚本暴露的命令集合一致（跨平台行为对齐）；
- 超级用户名（UIR-M21）：``dump`` / ``restore`` / ``maintenance-shell`` 仅在非默认
  ``postgres`` 时透传 ``--username``，默认路径调用形态与旧脚本一致。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.meta]

ROOT = Path(__file__).resolve().parent.parent.parent
MAINTENANCE_DIR = ROOT / "resources" / "maintenance"
BAT_PATH = MAINTENANCE_DIR / "qtrading-db-maintenance.bat"
SH_PATH = MAINTENANCE_DIR / "qtrading-db-maintenance.sh"
README_PATH = MAINTENANCE_DIR / "README-maintenance.md"

_PG_USERNAME_ENV = "QTRADING_PG_USERNAME"
_USERNAME_COMMANDS = ("dump", "restore", "maintenance-shell")
_SH_AVAILABLE = os.name == "posix" and shutil.which("sh") is not None

_ARG_INDEX_PATTERN = re.compile(r"%~(\d)")
_DISPATCH_PATTERN = re.compile(r'(?m)^if /i "%CMD%"=="([^"]+)"')
_HELP_CMD_PATTERN = re.compile(r"(?m)^echo {3}([a-z][a-z-]*)(?: \^<[a-z-]+\^>)? {2,}")
_BAT_BLOCK_TEMPLATE = r'(?ms)^if /i "%CMD%"=="{cmd}" \(\n(.*?)^\)$'
_SHIFT_PATTERN = re.compile(r"(?m)^\s*shift\b")

_SIDECAR_FLAG = {"dump": "--output", "restore": "--input"}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _expected_arg_index(text: str) -> str:
    """经 ``shift`` 后位置参数整体左移一位，故文件参数位随是否 shift 变化。"""
    return "1" if _SHIFT_PATTERN.search(text) else "2"


def _bat_block(text: str, cmd: str) -> str:
    match = re.search(_BAT_BLOCK_TEMPLATE.format(cmd=cmd), text)
    assert match is not None, f"未找到 {cmd} 分派分支"
    return match.group(1)


def _bat_dispatch_commands(text: str) -> set[str]:
    """分派分支命令集合（剔除 help 别名，与帮助文本口径对齐）。"""
    aliases = {"/?", "-h", "--help"}
    return {c.lower() for c in _DISPATCH_PATTERN.findall(text) if c.lower() not in aliases}


def _bat_help_commands(text: str) -> set[str]:
    help_section = text[text.index(":help") :]
    return {c.lower() for c in _HELP_CMD_PATTERN.findall(help_section)}


def _sh_dispatch_commands(text: str) -> set[str]:
    """``case "$CMD" in`` 中的分支标签集合（剔除 ``--help`` / ``-h`` 别名）。"""
    body = text[text.index('case "$CMD" in') :]
    names: set[str] = set()
    for label in re.findall(r"(?m)^ {4}([a-z][a-z|-]*)\)", body):
        names.update(alias for alias in label.split("|") if not alias.startswith("-"))
    return names


@pytest.mark.parametrize("cmd", ["dump", "restore"])
def test_bat_file_argument_position_tracks_shift(cmd: str) -> None:
    """分派分支的文件参数位必须与脚本是否 shift 一致（shift 后为 %1，否则为 %2）。"""
    text = _read(BAT_PATH)
    expected_index = _expected_arg_index(text)
    indices = set(_ARG_INDEX_PATTERN.findall(_bat_block(text, cmd)))

    assert indices == {expected_index}, (
        f"{cmd} 分支引用 %~{sorted(indices)}，但脚本"
        f"{'已' if expected_index == '1' else '未'} shift，应为 %~{expected_index}（UIR-F16-001）"
    )


@pytest.mark.parametrize("cmd", ["dump", "restore"])
def test_bat_missing_arg_guard_and_sidecar_use_same_position(cmd: str) -> None:
    """缺参守卫与 sidecar 传参必须引用同一个位置参数，避免「守卫恒空 / 传参错位」。"""
    text = _read(BAT_PATH)
    expected_index = _expected_arg_index(text)
    block = _bat_block(text, cmd)

    assert f'if "%~{expected_index}"==""' in block, f"{cmd} 分支缺参守卫缺失或参数位错位"
    assert f'{_SIDECAR_FLAG[cmd]} "%~{expected_index}"' in block, f"{cmd} 传给 sidecar 的参数位与守卫不一致"


def test_bat_help_lists_dispatched_commands() -> None:
    """帮助文本与分派分支命令集合一致（不留「文档有、脚本无」或反之）。"""
    text = _read(BAT_PATH)
    assert _bat_help_commands(text) == _bat_dispatch_commands(text)


def test_bat_and_sh_command_sets_align() -> None:
    """Windows 与 Unix 维护脚本暴露的命令集合一致。"""
    assert _bat_help_commands(_read(BAT_PATH)) == _sh_dispatch_commands(_read(SH_PATH))


def test_dump_and_restore_usage_mentions_file_argument() -> None:
    """帮助文本中 dump/restore 的用法与 README 命令表一致（均要求文件参数）。"""
    help_section = _read(BAT_PATH)[_read(BAT_PATH).index(":help") :]
    readme = _read(README_PATH)

    assert re.search(r"(?m)^echo {3}dump \^<file\^>", help_section)
    assert re.search(r"(?m)^echo {3}restore \^<file\^>", help_section)
    assert "| `dump <file>`" in readme
    assert "| `restore <file>`" in readme


# --------------------------------------------------------------------------- #
# 超级用户名透传（UIR-M21：非 postgres 时必须传 --username）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("cmd", _USERNAME_COMMANDS)
def test_bat_forwards_username_arg_for_auth_commands(cmd: str) -> None:
    """dump / restore / maintenance-shell 分支必须透传 ``%USERNAME_ARG%``。"""
    block = _bat_block(_read(BAT_PATH), cmd)
    assert "%USERNAME_ARG%" in block, f"{cmd} 分支未透传 %USERNAME_ARG%（非 postgres 用户名无法认证）"


@pytest.mark.parametrize("cmd", _USERNAME_COMMANDS)
def test_sh_forwards_username_arg_for_auth_commands(cmd: str) -> None:
    """dump / restore / maintenance-shell 分支必须透传 ``$USERNAME_ARG``。"""
    lines = [
        line.strip() for line in _read(SH_PATH).splitlines() if line.strip().startswith(f'exec "$SIDECAR_BIN" {cmd} ')
    ]
    assert len(lines) == 1, f"{cmd} 分支应恰有一次 sidecar 调用，实际 {len(lines)}"
    assert "$USERNAME_ARG" in lines[0], f"{cmd} 分支未透传 $USERNAME_ARG（非 postgres 用户名无法认证）"


def test_bat_username_defaults_to_postgres_without_flag() -> None:
    """未设置环境变量时按 postgres 处理，且默认路径不追加 ``--username``（保持旧行为）。"""
    text = _read(BAT_PATH)
    assert 'set "PG_USERNAME=%QTRADING_PG_USERNAME%"' in text
    assert 'if not defined PG_USERNAME set "PG_USERNAME=postgres"' in text
    assert 'set "USERNAME_ARG="' in text
    assert 'if /i not "%PG_USERNAME%"=="postgres" set "USERNAME_ARG=--username %PG_USERNAME%"' in text


def test_sh_username_env_and_prefix_flag_priority() -> None:
    """用户名来源：前置 ``--username`` > ``QTRADING_PG_USERNAME`` > ``postgres``。"""
    text = _read(SH_PATH)
    assert f'PG_USERNAME="${{{_PG_USERNAME_ENV}:-postgres}}"' in text
    assert 'if [ -z "$PG_USERNAME" ]; then' in text
    assert 'if [ "$PG_USERNAME" != "postgres" ]; then' in text
    assert 'USERNAME_ARG="--username $PG_USERNAME"' in text
    assert "        --username)" in text
    assert "        --username=*)" in text


def test_readme_documents_username_env_for_both_platforms() -> None:
    """README 记录用户名覆盖方式（Windows 环境变量 + Unix 参数/环境变量）。"""
    readme = _read(README_PATH)
    assert _PG_USERNAME_ENV in readme
    assert "--username" in readme


def _stage_script_tree(tmp_path: Path) -> Path:
    """搭最小安装布局：临时脚本副本 + 打印参数的 stub sidecar（exec 后输出 argv）。"""
    maint_dir = tmp_path / "app" / "resources" / "maintenance"
    maint_dir.mkdir(parents=True)
    script = maint_dir / SH_PATH.name
    shutil.copy2(SH_PATH, script)
    script.chmod(0o755)

    sidecar = tmp_path / "app" / "_internal" / "sidecars" / "qtrading-pg-sidecar"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n", encoding="utf-8")
    sidecar.chmod(0o755)
    return script


def _run_sh(script: Path, args: list[str], tmp_path: Path, username: str | None) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key != _PG_USERNAME_ENV}
    if username is not None:
        env[_PG_USERNAME_ENV] = username
    return subprocess.run(
        ["sh", str(script), *args], capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=60
    )


@pytest.mark.skipif(not _SH_AVAILABLE, reason="POSIX sh 不可用（Windows CI 仅做静态断言）")
@pytest.mark.parametrize("username, expect_flag", [(None, False), ("postgres", False), ("admin", True)])
@pytest.mark.parametrize("cmd", _USERNAME_COMMANDS)
def test_sh_executed_username_forwarding(tmp_path: Path, cmd: str, username: str | None, expect_flag: bool) -> None:
    """执行型断言：默认/显式 postgres 不传 ``--username``，自定义用户名透传且取值正确。"""
    script = _stage_script_tree(tmp_path)
    backup_file = tmp_path / "backup.dump"
    extra = [str(backup_file)] if cmd in ("dump", "restore") else []

    proc = _run_sh(script, [cmd, *extra], tmp_path, username)
    assert proc.returncode == 0, proc.stderr

    received = proc.stdout.splitlines()
    assert received[0] == cmd
    assert received[1] == "--data-dir" and received[2]
    if cmd in ("dump", "restore"):
        flag = "--output" if cmd == "dump" else "--input"
        assert received[3] == flag and received[4] == str(backup_file)
    assert ("--username" in received) is expect_flag
    if expect_flag:
        assert received[received.index("--username") + 1] == username


@pytest.mark.skipif(not _SH_AVAILABLE, reason="POSIX sh 不可用（Windows CI 仅做静态断言）")
def test_sh_prefix_flag_overrides_env(tmp_path: Path) -> None:
    """前置参数 ``--username`` 优先于 ``QTRADING_PG_USERNAME``。"""
    script = _stage_script_tree(tmp_path)
    proc = _run_sh(script, ["--username", "dba", "dump", str(tmp_path / "backup.dump")], tmp_path, "admin")
    assert proc.returncode == 0, proc.stderr
    received = proc.stdout.splitlines()
    assert received[0] == "dump"
    assert received[received.index("--username") + 1] == "dba"
