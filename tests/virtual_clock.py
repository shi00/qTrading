"""Virtual clock utility for tests.

提供可注入到被测代码的虚拟时钟，替代 ``time.monotonic`` / ``time.sleep`` /
``asyncio.sleep``，让测试无需真实等待即可推进时间。

Example:
    >>> from unittest.mock import patch
    >>> from tests.virtual_clock import VirtualClock

    >>> clock = VirtualClock()
    >>> clock.now()
    0.0
    >>> clock.advance(5.0)
    >>> clock.now()
    5.0
    >>> clock.sleep(2.0)  # 不实际阻塞
    >>> clock.now()
    7.0

    # 注入到被测代码（以 utils.rate_limiter 为例）：
    >>> def test_rate_limiter():
    ...     clock = VirtualClock()
    ...     with patch("utils.rate_limiter.time.monotonic", clock.now), \\
    ...          patch("utils.rate_limiter.time.sleep", clock.sleep):
    ...         # 被测代码使用虚拟时间，测试可断言 clock.now() 验证等待
    ...         pass
"""

from __future__ import annotations


class VirtualClock:
    """确定性、可控的虚拟时钟，用于测试。

    虚拟时间仅在显式调用 ``advance`` / ``sleep`` / ``async_sleep`` 时推进，
    可作为 ``time.monotonic`` / ``time.sleep`` / ``asyncio.sleep`` 的替代品
    注入到被测代码中。
    """

    def __init__(self, start: float = 0.0) -> None:
        self._time: float = float(start)

    def now(self) -> float:
        """返回当前虚拟时间（drop-in for ``time.monotonic``）。"""
        return self._time

    def advance(self, seconds: float) -> None:
        """推进虚拟时间 ``seconds`` 秒。"""
        self._time += float(seconds)

    def sleep(self, seconds: float) -> None:
        """虚拟 sleep：推进时间但不阻塞（drop-in for ``time.sleep``）。"""
        self.advance(seconds)

    async def async_sleep(self, seconds: float) -> None:
        """虚拟异步 sleep：推进时间但不 await（drop-in for ``asyncio.sleep``）。"""
        self.advance(seconds)
