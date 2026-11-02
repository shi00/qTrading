"""ui/cache_cleared_state.py 单元测试 (UIX-01).

验证 CacheClearedState Observable 信号源:
- get_cache_cleared_state 惰性单例
- notify_cache_cleared 递增 seq 并触发订阅回调
- 订阅回调确实收到通知 (而非仅 seq 递增), 退订只影响自身订阅者
"""

from __future__ import annotations

import pytest

from ui.cache_cleared_state import CacheClearedState


class _NotificationRecorder:
    """记录 Observable 通知 payload 的订阅者替身.

    ``ft.Observable`` 内部以 ``WeakSet`` 保存监听器, 内联 lambda 无强引用会被
    回收, 导致"回调未送达"与"未真正订阅"不可区分; 本类由测试局部变量持有
    强引用, 并以 ``(sender.seq, field)`` 记录每一次送达的 payload.
    """

    def __init__(self) -> None:
        self.notifications: list[tuple[int, str | None]] = []

    def __call__(self, sender: CacheClearedState, field: str | None) -> None:
        self.notifications.append((sender.seq, field))


@pytest.fixture(autouse=True)
def _reset_cache_cleared_state():
    """每个测试开始前重置模块级单例, 避免跨测试 seq 累积污染。"""
    import ui.cache_cleared_state as m

    saved = m._cache_cleared_state
    m._cache_cleared_state = None
    yield
    m._cache_cleared_state = saved


class TestCacheClearedState:
    """CacheClearedState Observable 状态源行为."""

    def test_notify_increments_seq(self) -> None:
        """notify_cache_cleared 递增 seq (每次通知 +1)."""
        import ui.cache_cleared_state as m
        from ui.cache_cleared_state import get_cache_cleared_state

        state = get_cache_cleared_state()
        assert state.seq == 0

        m.notify_cache_cleared()
        assert state.seq == 1
        m.notify_cache_cleared()
        assert state.seq == 2

    def test_get_cache_cleared_state_is_lazy_singleton(self) -> None:
        """get_cache_cleared_state 返回同一实例 (惰性单例)."""
        from ui.cache_cleared_state import CacheClearedState, get_cache_cleared_state

        s1 = get_cache_cleared_state()
        s2 = get_cache_cleared_state()
        assert s1 is s2
        assert isinstance(s1, CacheClearedState)

    def test_seq_zero_initial(self) -> None:
        """初始 seq 为 0 (首次订阅天然无信号)."""
        from ui.cache_cleared_state import CacheClearedState

        s = CacheClearedState()
        assert s.seq == 0

    def test_reset_state_for_test_discards_singleton(self) -> None:
        """_reset_state_for_test 丢弃单例 (连同订阅者), 后续取回新实例 (R7)."""
        from ui.cache_cleared_state import _reset_state_for_test, get_cache_cleared_state

        s1 = get_cache_cleared_state()
        _reset_state_for_test()
        s2 = get_cache_cleared_state()
        assert s2 is not s1
        assert s2.seq == 0


class TestNotifyCacheClearedSubscription:
    """notify_cache_cleared → 订阅回调送达与退订语义 (ft.Observable 同步通知)."""

    def test_notify_triggers_observable_subscribers(self) -> None:
        """订阅后 notify 返回前即收到回调, 首次 payload seq=1, 第二次 seq=2."""
        import ui.cache_cleared_state as m
        from ui.cache_cleared_state import get_cache_cleared_state

        state = get_cache_cleared_state()
        recorder = _NotificationRecorder()

        # 确认 Flet Observable 订阅协议存在 (subscribe 返回可调用 disposer)
        dispose = state.subscribe(recorder)
        try:
            assert callable(dispose), "Flet Observable.subscribe 应返回可调用退订句柄"
            m.notify_cache_cleared()
            # 同步断言: notify 返回时回调已送达, 无需 sleep/受控等待
            assert recorder.notifications == [(1, "seq")]
            m.notify_cache_cleared()
            assert recorder.notifications == [(1, "seq"), (2, "seq")]
            assert state.seq == 2
        finally:
            dispose()

    def test_disposer_only_stops_its_own_subscriber(self) -> None:
        """notify → 退订一个 → 再 notify: 已退订者不增长, 另一者仍收到新信号."""
        import ui.cache_cleared_state as m
        from ui.cache_cleared_state import get_cache_cleared_state

        state = get_cache_cleared_state()
        recorder_a = _NotificationRecorder()
        recorder_b = _NotificationRecorder()
        dispose_a = state.subscribe(recorder_a)
        dispose_b = state.subscribe(recorder_b)
        try:
            m.notify_cache_cleared()
            assert recorder_a.notifications == [(1, "seq")]
            assert recorder_b.notifications == [(1, "seq")]

            dispose_a()
            m.notify_cache_cleared()
            assert recorder_a.notifications == [(1, "seq")]
            assert recorder_b.notifications == [(1, "seq"), (2, "seq")]
        finally:
            dispose_b()

    def test_notify_after_reset_reaches_new_instance_only(self) -> None:
        """reset 后新实例从 0 起, notify 只送达新 collector, 旧 collector 不变."""
        import ui.cache_cleared_state as m
        from ui.cache_cleared_state import _reset_state_for_test, get_cache_cleared_state

        old_state = get_cache_cleared_state()
        old_recorder = _NotificationRecorder()
        old_state.subscribe(old_recorder)

        _reset_state_for_test()
        new_state = get_cache_cleared_state()
        assert new_state is not old_state
        assert new_state.seq == 0

        new_recorder = _NotificationRecorder()
        new_state.subscribe(new_recorder)
        m.notify_cache_cleared()
        assert new_recorder.notifications == [(1, "seq")]
        assert new_state.seq == 1
        assert old_recorder.notifications == []
        assert old_state.seq == 0

    def test_seq_increment_without_callback_fails_delivery_assertion(self) -> None:
        """反证: seq 递增但抑制通知的受控替身, 必须使送达断言失败."""
        import ui.cache_cleared_state as m

        class _SeqOnlyCacheClearedState(CacheClearedState):
            """seq 仍递增, 但抑制 Observable 回调的受控替身."""

            def _notify(self, field: str | None) -> None:
                pass

        stand_in = _SeqOnlyCacheClearedState()
        m._cache_cleared_state = stand_in
        recorder = _NotificationRecorder()
        stand_in.subscribe(recorder)

        m.notify_cache_cleared()
        assert stand_in.seq == 1
        # 与正向用例相同的送达断言在此必然不成立, 证其检验的是回调送达而非 seq 递增
        callback_delivery_assertion_holds = recorder.notifications == [(1, "seq")]
        assert callback_delivery_assertion_holds is False

    def test_state_updated_before_notify_returns(self) -> None:
        """notify 返回后 seq 已更新 (同步生效, 无异步窗口)."""
        import ui.cache_cleared_state as m
        from ui.cache_cleared_state import get_cache_cleared_state

        state = get_cache_cleared_state()
        m.notify_cache_cleared()
        assert state.seq == 1


pytestmark = [pytest.mark.unit]
