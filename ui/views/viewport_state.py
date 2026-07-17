"""viewport_state — 响应式窗口尺寸快照 (Phase 6.2 P2-1).

ViewportState 由 AppLayout 维护 (基于 resize 事件), 通过 props 下发给
active=True 的子视图. 独立模块避免 ``ui.app_layout`` 与 ``ui.views.*`` 之间
的循环依赖 (ui.views.* 反向 import ViewportState 供自身签名标注).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ViewportState:
    """响应式窗口尺寸快照 (Phase 6.2 P2-1).

    由 AppLayout 维护 (基于 resize 事件), 通过 props 下发给 active=True 的子视图.
    breakpoint: "compact" (width < 600) | "medium" (600 <= width < 840) | "expanded" (width >= 840).
    """

    width: float
    height: float
    breakpoint: str
