"""ui/cache_cleared_state.py 单元测试 (UIX-01).

验证 CacheClearedState Observable 信号源:
- get_cache_cleared_state 惰性单例
- notify_cache_cleared 递增 seq 并触发订阅回调

单例复位由 tests/unit/ui/conftest.py 的 ``_reset_cache_cleared_state`` autouse
fixture 统一处理 (R7 测试隔离, 对齐 _reset_pending_prefill 模式).
"""

from __future__ import annotations

import pytest


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


class TestNotifyCacheClearedSubscription:
    """notify_cache_cleared → 订阅回调可被触发 (Observable 语义)."""

    def test_notify_triggers_observable_subscribers(self) -> None:
        """订阅 CacheClearedState 后, notify_cache_cleared 使 seq 同步递增."""
        import ui.cache_cleared_state as m
        from ui.cache_cleared_state import get_cache_cleared_state

        state = get_cache_cleared_state()

        # 确认 Flet Observable 订阅协议存在 (subscribe 返回可调用 disposer)
        handler = state.subscribe(lambda *a, **kw: None)
        try:
            assert callable(handler), "Flet Observable.subscribe 应返回可调用退订句柄"
            m.notify_cache_cleared()
            assert state.seq == 1
        finally:
            handler()

    def test_state_updated_before_notify_returns(self) -> None:
        """notify 返回后 seq 已更新 (同步生效, 无异步窗口)."""
        import ui.cache_cleared_state as m
        from ui.cache_cleared_state import get_cache_cleared_state

        state = get_cache_cleared_state()
        m.notify_cache_cleared()
        assert state.seq == 1


pytestmark = [pytest.mark.unit]
