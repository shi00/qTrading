"""应用环境判定工具（review03-C16）。

统一收口 `E2E_TESTING` 环境变量的读取，消除分散在各层的直接 `os.environ.get`
判断（质量门控、交易日历、UI 锚点、bootstrap 等），便于集中审计与防护。
"""

from __future__ import annotations

import os


def is_e2e_mode() -> bool:
    """E2E 测试模式判定（单一事实来源，review03-C16）。

    所有层的 E2E 分支应统一调用本函数，而非直接读取 ``os.environ["E2E_TESTING"]``。
    """
    return os.environ.get("E2E_TESTING") == "true"
