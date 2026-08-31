"""``--run-windows-skip`` CLI 选项：临时移除 skipif markers 用于 Windows 复验.

Windows E2E skipif 复验专用：在 ``windows-skip-revalidation`` CI job 中通过
``--run-windows-skip`` CLI 标志临时取消 8 个 Windows skipif 用例的 skipif 装饰器，
使其实际运行以判断 Flet 0.86.2 下问题是否仍存在.

设计要点：
- 仅在 ``--run-windows-skip`` 显式传入时生效（默认不影响任何测试）
- 移除所有 skipif markers（安全：该标志仅在复验 CI job 中使用，job 仅运行这 8 个文件）
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


def strip_windows_skipif(config: pytest.Config, items: list[pytest.Item]) -> int:
    """当 ``--run-windows-skip`` 设置时，移除 items 上的 skipif markers.

    Args:
        config: pytest Config 对象，用于查询 ``--run-windows-skip`` 选项值.
        items: 已收集的测试用例列表.

    Returns:
        被 un-skip 的用例数（即原本有 skipif marker 且被移除的用例数）.

    Note:
        移除所有 skipif markers 而非仅 ``sys.platform == "win32"`` 条件的.
        这是安全决策：``--run-windows-skip`` 仅在 ``windows-skip-revalidation`` CI job
        中使用，该 job 仅运行 8 个 Windows E2E skipif 用例文件，无其他 skipif markers.
        未来扩展 test_targets 时需确保所有被收集的用例都应被 un-skip，避免误 un-skip
        其他 skipif 用例（如 Python 版本 skipif / 依赖缺失 skipif）.
    """
    if not config.getoption("--run-windows-skip", default=False):
        return 0
    unskipped = 0
    for item in items:
        skipif_markers = [m for m in item.own_markers if m.name == "skipif"]
        if not skipif_markers:
            continue
        for marker in skipif_markers:
            item.own_markers.remove(marker)
        unskipped += 1
    return unskipped
