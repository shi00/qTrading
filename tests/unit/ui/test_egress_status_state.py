"""ui/egress_status_state.py 单测 (SEC-03 状态栏指示器信号源).

覆盖：
1. get_egress_status_state 单例语义（同对象）
2. notify_egress_count 更新 count（含 int 强转）
3. count 变化可被 Observable 订阅（value change 触发通知）
"""

from __future__ import annotations

import pytest

from ui.egress_status_state import get_egress_status_state, notify_egress_count

pytestmark = pytest.mark.unit


class TestEgressStatusState:
    def test_singleton_returns_same_instance(self) -> None:
        assert get_egress_status_state() is get_egress_status_state()

    def test_initial_count_zero(self) -> None:
        state = get_egress_status_state()
        state.count = 0
        assert state.count == 0

    def test_notify_updates_count(self) -> None:
        state = get_egress_status_state()
        notify_egress_count(3)
        assert state.count == 3
        notify_egress_count(5)
        assert state.count == 5

    def test_notify_coerces_int(self) -> None:
        state = get_egress_status_state()
        notify_egress_count("4")
        assert state.count == 4

    def test_count_change_notifies_subscriber(self) -> None:
        """值变化触发 Observable 通知（状态栏重渲染依赖）。

        ft.Observable.subscribe 回调签名 (sender, field)；收到任一通知即证明变更被发布。
        """
        state = get_egress_status_state()
        seen: list[object] = []
        dispose = state.subscribe(lambda _sender, _field: seen.append(_field))
        notify_egress_count(2)
        assert seen
        dispose()
