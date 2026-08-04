"""E2E 测试锚点包装器（单一入口）。

E2E_TESTING=true 时用 `ft.Semantics(container=True)` 包裹控件；
生产 build 直接返回原控件，零性能/语义副作用。

CanvasKit 对 Semantics label 的双轨映射（PoC EVIDENCE.md）：
  - INTERACTIVE/INPUT 类控件 → 生成 `flt-semantics[aria-label="EID"]` 独立节点
  - LABEL/COMPLEX 类控件 → EID 落入该子树 `textContent`
定位策略由 `AnchorPage` 依 `EIDS` 携带的 `AnchorKind` 分派，本模块只负责生成。
"""

import os
from functools import cache

import flet as ft

from ui.testing.e2e_ids import AnchorKind, Eid


@cache
def _e2e_enabled() -> bool:
    """E2E 模式判定。模块级缓存，避免每次 render 都 os.environ.get。

    与 tests/e2e/helpers/app_launcher.py:155 已注入的 env var 对接。

    NOTE(lazy): @cache 一旦缓存不会重读 env var. ceiling: pytest session 内
    env var 不变. upgrade: 测试中需模拟不同 E2E_TESTING 值时调用
    `_e2e_enabled.cache_clear()` 主动清理（tests/unit/ui/test_anchor.py
    TestE2EEnabledCache 已覆盖此契约）.
    """
    return os.environ.get("E2E_TESTING") == "true"


def anchored(eid: Eid, control: ft.Control) -> ft.Control:
    """给控件添加稳定测试锚点，无论 control 类型均返回可安全用作 Column/Row 子节点的 Control。

    生产 build (`_e2e_enabled=False`): 直接返回原控件，零副作用。
    E2E build (`_e2e_enabled=True`): 用 `Semantics(container=True, label=eid_str)` 包裹。

    INTERACTIVE kind 额外设 `button=True`：CanvasKit 双轨映射仅在 `content` 为
    标准交互控件（Button 等）时生成 `flt-semantics[aria-label=EID]` 独立节点。
    GestureDetector 不是标准交互控件，`button=True` 强制 CanvasKit 将 Semantics
    节点识别为按钮，确保 `aria-label` 生成（PR-2 列头/行 anchor 修复）。

    事件穿透：不设 `Semantics.on_tap` → Button.on_click / GestureDetector.on_tap
    正常触发。PoC A3 confirmed（reviews/poc/EVIDENCE.md）。
    """
    if not _e2e_enabled():
        return control
    eid_str, kind = eid
    return ft.Semantics(
        container=True,
        label=eid_str,
        content=control,
        button=(kind == AnchorKind.INTERACTIVE),
    )


__all__ = ["AnchorKind", "Eid", "anchored"]
