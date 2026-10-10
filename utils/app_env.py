"""应用环境判定工具（review03-C16）。

统一收口 `E2E_TESTING` 环境变量的读取，消除分散在各层的直接 `os.environ.get`
判断（质量门控、交易日历、UI 锚点、bootstrap 等），便于集中审计与防护。
"""

from __future__ import annotations

import os
import sys


def is_e2e_mode() -> bool:
    """E2E 测试模式判定（单一事实来源，review03-C16）。

    所有层的 E2E 分支应统一调用本函数，而非直接读取 ``os.environ["E2E_TESTING"]``。

    DS-06: 冻结产物（PyInstaller 分发版）恒返回 False——E2E_TESTING 是测试开关，
    不应能被终端用户在打包分发版上通过环境变量静默打开（关闭质量门控等安全机制）。
    E2E 测试以源码运行（CI ``pytest tests/e2e/``），``sys.frozen`` 不存在，不受影响。
    """
    if getattr(sys, "frozen", False):
        return False
    return os.environ.get("E2E_TESTING") == "true"


def is_e2e_full_mount_mode() -> bool:
    """E2E 全量挂载模式判定（F07 生产挂载场景池）。

    ``E2E_FULL_MOUNT=true`` 时，``ui/app_layout.py`` 的页面栈按生产模型常驻构造
    全部视图（非激活视图不再替换为空占位），供 ``tests/e2e/test_production_mount.py``
    覆盖「切页不销毁组件 / use_state 跨页保持 / use_dialog overlay 跨页存活」的
    生产挂载行为；快速 smoke 模式（仅 ``E2E_TESTING=true``）维持按页挂载优化。

    防护语义与 :func:`is_e2e_mode` 同源：

    - 冻结产物（PyInstaller 分发版）恒返回 False（DS-06，测试开关不得随产物分发）；
    - 单独设置 ``E2E_FULL_MOUNT`` 而非 ``E2E_TESTING`` 时，调用处以 ``is_e2e_mode()``
      前置短路，生产（非 E2E）行为不受影响——本函数不单独开启任何 E2E 分支。
    """
    if getattr(sys, "frozen", False):
        return False
    return os.environ.get("E2E_FULL_MOUNT") == "true"
