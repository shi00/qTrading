"""E2E 测试锚点包装器（单一入口）。

E2E_TESTING=true 时用 `ft.Semantics(container=True, label=EID, identifier=EID)` 包裹控件；
生产 build 直接返回原控件，零性能/语义副作用。

`identifier` 是唯一定位通道：CanvasKit web 上落为 DOM 属性 `flt-semantics-identifier`
（PoC EVIDENCE.md P0-1 实测），供 `AnchorPage` 走精确选择器
`flt-semantics[flt-semantics-identifier="<EID>"]` 定位，其节点存在性与 `AnchorKind`
无关（P0-2 矩阵：16 枚基线 identifier 中 14 枚 count=1，其余 2 枚为 offstage 预期排除）。

`label=EID` 仍然注入并保留，作为无障碍 label 通道（供 E2E 的 `container=True` 独立性
守护断言使用），但已不再是定位通道。定位（bbox 解析）由 `AnchorPage` 依 kind 特定语义
决定（INPUT 下潜后代 input，LABEL 拒绝点击），本模块只负责生成锚点。
"""

from functools import cache

import flet as ft

from utils.app_env import is_e2e_mode

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
    return is_e2e_mode()


def anchored(eid: Eid, control: ft.Control) -> ft.Control:
    """给控件添加稳定测试锚点，无论 control 类型均返回可安全用作 Column/Row 子节点的 Control。

    生产 build (`_e2e_enabled=False`): 直接返回原控件，零副作用。
    E2E build (`_e2e_enabled=True`): 用 `Semantics(container=True, label=eid_str,
    identifier=eid_str)` 包裹。`identifier` 是唯一定位通道（CanvasKit 落为 DOM 属性
    `flt-semantics-identifier`，`AnchorPage` 以精确选择器定位）；`label=eid_str` 仍保留，
    作为无障碍 label 通道（供 E2E 的 `container=True` 独立性守护断言使用），非定位必需。

    INTERACTIVE kind 额外设 `button=True`：保留独立无障碍语义节点（标注为按钮）。
    定位已由 identifier 承担，`button=True` 不再影响定位行为。

    事件穿透：不设 `Semantics.on_tap` → Button.on_click / GestureDetector.on_tap
    正常触发。PoC A3 confirmed（reviews/poc/EVIDENCE.md）。
    """
    if not _e2e_enabled():
        return control
    eid_str, kind = eid
    return ft.Semantics(
        container=True,
        label=eid_str,
        identifier=eid_str,
        content=control,
        button=(kind == AnchorKind.INTERACTIVE),
    )


__all__ = ["AnchorKind", "Eid", "anchored"]
