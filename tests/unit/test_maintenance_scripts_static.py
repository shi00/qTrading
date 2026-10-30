"""Tests for resources/maintenance 离线维护脚本的静态一致性（UIR-F16-001）。

只读取脚本文本做静态断言，不执行 .bat / .sh（避免依赖 Windows cmd 与真实 sidecar）。

守护:
- 分派分支的参数位与 ``shift`` 位置自洽（shift 后取 ``%1``，未 shift 取 ``%2``），
  防止 ``dump`` / ``restore`` 按 ``<command> <file>`` 调用时恒走 Usage 分支；
- 每个分支「缺参守卫」与「sidecar 实际传参」使用同一个位置参数；
- 帮助文本列出的命令集合与实际分派分支集合一致，dump/restore 用法与 README 一致；
- .bat 与 .sh 两条脚本暴露的命令集合一致（跨平台行为对齐）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.meta]

ROOT = Path(__file__).resolve().parent.parent.parent
MAINTENANCE_DIR = ROOT / "resources" / "maintenance"
BAT_PATH = MAINTENANCE_DIR / "qtrading-db-maintenance.bat"
SH_PATH = MAINTENANCE_DIR / "qtrading-db-maintenance.sh"
README_PATH = MAINTENANCE_DIR / "README-maintenance.md"

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
