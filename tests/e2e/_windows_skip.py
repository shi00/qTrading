"""``--run-windows-skip`` CLI 选项：临时移除 skipif markers 用于 Windows 复验.

Windows E2E skipif 复验专用：在 ``windows-skip-revalidation`` CI job 中通过
``--run-windows-skip`` CLI 标志临时取消 8 个 Windows skipif 用例的 skipif 装饰器，
使其实际运行以判断 Flet 0.86.2 下问题是否仍存在.

设计要点：
- 仅在 ``--run-windows-skip`` 显式传入时生效（默认不影响任何测试）
- 只移除 reason 显式标注 Windows 的 skipif markers，保留模式/依赖 skip
  （如 ``QTRADING_DATABASE_MODE=embedded`` 条件）——复验不应改变测试装配语义
- 无副作用模块，可被单元测试直接 import（不依赖 keyring/DB/Playwright）
"""

from __future__ import annotations

import pytest


def add_windows_skip_option(parser: pytest.Parser) -> None:
    """注册 ``--run-windows-skip`` CLI 选项.

    必须在 ``pytest_addoption`` hook 中调用.
    """
    parser.addoption(
        "--run-windows-skip",
        action="store_true",
        default=False,
        help=(
            "Temporarily remove @pytest.mark.skipif markers for Windows E2E revalidation "
            "(Windows E2E skipif). Only use in windows-skip-revalidation CI job."
        ),
    )


def _is_windows_skipif(marker: pytest.Mark) -> bool:
    """判定 skipif 是否显式针对 Windows（reason 含 ``Windows``）.

    pytest 在装饰期已把 skipif 条件求值为 bool，无法反查原始表达式；
    故以约定 ``reason`` 标注为准。模式/依赖 skip 不应带 Windows reason，
    从而在复验时被保留。
    """
    reason = str(marker.kwargs.get("reason", ""))
    return "windows" in reason.lower()


def strip_windows_skipif(config: pytest.Config, items: list[pytest.Item]) -> int:
    """当 ``--run-windows-skip`` 设置时，移除 items 上 Windows 专属 skipif markers.

    Args:
        config: pytest Config 对象，用于查询 ``--run-windows-skip`` 选项值.
        items: 已收集的测试用例列表.

    Returns:
        被 un-skip 的用例数（即至少移除一个 Windows skipif marker 的 item 数）.

    Note:
        只移除 ``reason`` 显式包含 ``Windows`` 的 skipif markers；embedded
        模式、依赖缺失等条件 skip 保持生效，避免复验改变测试装配语义。
    """
    if not config.getoption("--run-windows-skip", default=False):
        return 0
    unskipped = 0
    for item in items:
        windows_markers = [m for m in item.own_markers if m.name == "skipif" and _is_windows_skipif(m)]
        if not windows_markers:
            continue
        for marker in windows_markers:
            item.own_markers.remove(marker)
        unskipped += 1
    return unskipped
