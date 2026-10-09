"""按源熔断器单测（N1-4）。

覆盖：closed → open（达阈值）→ 冷却窗口快速失败 → half_open 探活 → 成功闭合 / 失败重开路，
以及参数校验与状态属性。
"""

import datetime

import pytest

from data.external.news_sources.circuit_breaker import (
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_FAILURE_THRESHOLD,
    CircuitBreaker,
)

pytestmark = [pytest.mark.unit, pytest.mark.no_auto_mock]


class _FakeClock:
    def __init__(self, start: datetime.datetime) -> None:
        self._now = start

    def __call__(self) -> datetime.datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += datetime.timedelta(seconds=seconds)


def _breaker(clock: _FakeClock, **kwargs: object) -> CircuitBreaker:
    params = {"threshold": DEFAULT_FAILURE_THRESHOLD, "cooldown_seconds": DEFAULT_COOLDOWN_SECONDS}
    params.update(kwargs)
    return CircuitBreaker("test_source", clock=clock, **params)  # type: ignore[arg-type]


def test_starts_closed_and_allows_request():
    clock = _FakeClock(datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC))
    breaker = _breaker(clock)
    assert breaker.state() == "closed"
    assert breaker.is_open is False
    assert breaker.allow_request() is True


def test_opens_after_threshold_failures():
    clock = _FakeClock(datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC))
    breaker = _breaker(clock, threshold=3)
    assert breaker.record_failure(RuntimeError("x")) is False
    assert breaker.record_failure(RuntimeError("x")) is False
    assert breaker.record_failure(RuntimeError("x")) is True  # 刚好触发开路
    assert breaker.is_open is True
    assert breaker.consecutive_failures == 3
    assert breaker.state() == "open"


def test_open_state_fast_fails_within_cooldown():
    clock = _FakeClock(datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC))
    breaker = _breaker(clock, threshold=2, cooldown_seconds=60)
    breaker.record_failure(RuntimeError("x"))
    breaker.record_failure(RuntimeError("x"))
    clock.advance(30)
    assert breaker.state() == "open"
    assert breaker.allow_request() is False


def test_half_open_after_cooldown():
    clock = _FakeClock(datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC))
    breaker = _breaker(clock, threshold=2, cooldown_seconds=60)
    breaker.record_failure(RuntimeError("x"))
    breaker.record_failure(RuntimeError("x"))
    clock.advance(61)
    assert breaker.state() == "half_open"
    assert breaker.allow_request() is True


def test_success_closes_and_resets_counter():
    clock = _FakeClock(datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC))
    breaker = _breaker(clock, threshold=2, cooldown_seconds=60)
    breaker.record_failure(RuntimeError("x"))
    breaker.record_failure(RuntimeError("x"))
    clock.advance(61)
    assert breaker.record_success() is True  # 此前处于开路 → 发生闭合恢复
    assert breaker.consecutive_failures == 0
    assert breaker.state() == "closed"
    assert breaker.record_success() is False  # 已闭合状态下成功不构成恢复


def test_half_open_failure_reopens_and_resets_cooldown():
    clock = _FakeClock(datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC))
    breaker = _breaker(clock, threshold=2, cooldown_seconds=60)
    breaker.record_failure(RuntimeError("x"))
    breaker.record_failure(RuntimeError("x"))
    clock.advance(61)
    assert breaker.state() == "half_open"
    assert breaker.record_failure(RuntimeError("x")) is False  # 已开路，非再次触发
    clock.advance(30)  # 冷却自探活失败时刻重新计时
    assert breaker.state() == "open"
    clock.advance(31)
    assert breaker.state() == "half_open"


def test_validation_rejects_invalid_params():
    clock = _FakeClock(datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC))
    with pytest.raises(ValueError, match="threshold"):
        _breaker(clock, threshold=0)
    with pytest.raises(ValueError, match="cooldown"):
        _breaker(clock, cooldown_seconds=-1)
