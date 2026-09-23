"""utils/rate_limiter.py 的 TokenBucket 不变量属性测试（hypothesis）。

对 `_consume_reserve`（D1-5 原子预留）在随机输入域下验证核心不变量：
1. 任意时刻余额恒在 ``[0, capacity]``（充值按 capacity 封顶、绝不转负）——D1-5 的核心修复保证；
2. 请求成功（wait==0）⇔ 充水后余额 >= 请求，且余额精确扣减为 ``充水后余额 - 请求``；
3. 请求积压（wait>0）⇔ 充水后余额 < 请求，余额不被扣减，``wait == (请求 - 充水后余额)/rate``；
4. 请求超过 capacity 时抛 ``ValueError``（guard，不进入扣减）。

与 ``tests/unit/test_rate_limiter.py`` 的确定性用例互补：本文件用随机域
（capacity/rate/start/elapsed/request 全覆盖）探索边界，验证不变量在任意组合下恒真。
虚拟时间经 ``VirtualClock`` 推进，无需真实 sleep，纯同步快速运行。
"""

from unittest.mock import patch

import pytest
from hypothesis import given, settings, strategies as st

from tests.virtual_clock import VirtualClock
from utils.rate_limiter import TokenBucket

pytestmark = pytest.mark.unit


# 输入域假设（均为合法正缩放参数，排除 NaN/Inf）：
# - capacity: >=1（构造器会把 <=0 归一化为 1.0，此处避免歧义）
# - rate: >=1（rate>0 才保证 wait 计算无除零；构造器会把 <=0 归一化为 1.0）
# - start: 任意非负初始余额（构造器按 min(start, capacity) 统一封顶，覆盖越界输入）
# - elapsed: 相邻两次调用间的流逝时间
# - request: 每次请求令牌数，必须 <= capacity（否则触发 ValueError guard，属另一独立用例）
_N_TOKENS = 100


@given(st.data())
@settings(max_examples=_N_TOKENS, deadline=None)
def test_consume_reserve_balance_invariants(data: st.DataObject) -> None:
    """随机容量/速率/初始余额与消费序列下，余额恒在 [0, capacity] 且扣减/积压精确。"""
    vclock = VirtualClock()
    capacity = data.draw(st.floats(min_value=1.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False))
    rate = data.draw(st.floats(min_value=1.0, max_value=100_000.0, allow_nan=False, allow_infinity=False))
    # 起始余额可超容量（0..capacity*2 覆盖越界输入）：构造器按 min(start, capacity)
    # 封顶归一化（TokenBucket 构造器修复后），不变量仍须成立。
    start = data.draw(st.floats(min_value=0.0, max_value=capacity * 2, allow_nan=False, allow_infinity=False))

    with (
        patch("utils.rate_limiter.time.monotonic", vclock.now),
        patch("utils.rate_limiter.time.sleep", vclock.sleep),
    ):
        bucket = TokenBucket(start_tokens=start, capacity=capacity, rate=rate)
        # 初始不变量：余额非负且不超上限
        assert 0.0 <= bucket.tokens <= bucket.capacity

        prev = bucket.tokens
        n_ops = data.draw(st.integers(min_value=1, max_value=30))
        for _ in range(n_ops):
            elapsed = data.draw(st.floats(min_value=0.0, max_value=60.0, allow_nan=False, allow_infinity=False))
            req = data.draw(st.floats(min_value=0.0, max_value=bucket.capacity, allow_nan=False, allow_infinity=False))
            vclock.advance(elapsed)

            # _consume_reserve 内部：new_tokens = prev + elapsed*rate，再 min(capacity) 封顶
            charged = min(bucket.capacity, prev + elapsed * bucket.rate)
            wait = bucket._consume_reserve(req)

            if wait == 0.0:
                # 成功：充水后余额足额，精确扣减
                assert charged >= req
                assert bucket.tokens == pytest.approx(charged - req, rel=1e-9, abs=1e-9)
            else:
                # 积压：充水后余额不足，余额不被扣减，返回待等时长
                assert wait > 0.0
                assert charged < req
                assert bucket.tokens == pytest.approx(charged, rel=1e-9, abs=1e-9)
                assert wait == pytest.approx((req - charged) / bucket.rate, rel=1e-9, abs=1e-9)

            # 全局不变量：余额恒在 [0, capacity]
            assert 0.0 <= bucket.tokens <= bucket.capacity
            prev = bucket.tokens


@given(st.data())
@settings(max_examples=_N_TOKENS, deadline=None)
def test_consume_rejects_request_above_capacity(data: st.DataObject) -> None:
    """请求超过当前容量时必抛 ValueError（guard 前置），不进入扣减。"""
    vclock = VirtualClock()
    capacity = data.draw(st.floats(min_value=1.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False))
    rate = data.draw(st.floats(min_value=1.0, max_value=100_000.0, allow_nan=False, allow_infinity=False))
    # 起始余额可超容量（0..capacity*2 覆盖越界输入）：构造器按 min(start, capacity)
    # 封顶归一化（TokenBucket 构造器修复后），不变量仍须成立。
    start = data.draw(st.floats(min_value=0.0, max_value=capacity * 2, allow_nan=False, allow_infinity=False))

    with (
        patch("utils.rate_limiter.time.monotonic", vclock.now),
        patch("utils.rate_limiter.time.sleep", vclock.sleep),
    ):
        bucket = TokenBucket(start_tokens=start, capacity=capacity, rate=rate)
        req = data.draw(
            st.floats(min_value=capacity * 1.1, max_value=capacity * 100.0, allow_nan=False, allow_infinity=False)
        )
        with pytest.raises(ValueError, match="exceed bucket capacity"):
            bucket._consume_reserve(req)
        # guard 抛错前不改变余额
        assert 0.0 <= bucket.tokens <= bucket.capacity
