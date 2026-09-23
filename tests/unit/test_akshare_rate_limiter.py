"""review08-B4：akshare 出站调用共享限速器（模块级 TokenBucket）单元测试。

覆盖：
- 共享实例单例性 / 配置（1 QPS / capacity 2）/ reset 隔离（R7）
- 与 AkshareConceptClient / NewsFetcher 共用同一实例（报告核心意图）
- 线程并发 consume 下余额不转负
"""

import threading
from unittest.mock import patch

import pytest

from data.external.akshare_rate_limiter import (
    AKSHARE_RATE_LIMIT_CAPACITY,
    AKSHARE_RATE_LIMIT_PER_SEC,
    get_akshare_rate_limiter,
    reset_akshare_rate_limiter,
)
from tests.virtual_clock import VirtualClock
from utils.rate_limiter import TokenBucket

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_shared_limiter():
    """R7: 共享限速器为模块级可变状态（非注册单例，_reset_all_singletons 不覆盖），
    测试前后重建实例，防止跨测试污染。"""
    reset_akshare_rate_limiter()
    yield
    reset_akshare_rate_limiter()


class TestSharedLimiterIdentity:
    def test_getter_returns_same_instance(self):
        assert get_akshare_rate_limiter() is get_akshare_rate_limiter()

    def test_is_token_bucket_1_qps_capacity_2(self):
        limiter = get_akshare_rate_limiter()
        assert isinstance(limiter, TokenBucket)
        assert limiter.original_rate == AKSHARE_RATE_LIMIT_PER_SEC == 1.0
        assert limiter.capacity == AKSHARE_RATE_LIMIT_CAPACITY == 2.0

    def test_reset_builds_new_instance(self):
        old = get_akshare_rate_limiter()
        reset_akshare_rate_limiter()
        assert get_akshare_rate_limiter() is not old


class TestSharedLimiterSameInstanceAcrossClients:
    """report 核心意图：NewsFetcher 与概念客户端共用同一令牌桶。"""

    def test_concept_client_uses_shared_limiter(self):
        from data.external.akshare_concept_client import AkshareConceptClient

        client = AkshareConceptClient()
        assert client._rate_limiter is get_akshare_rate_limiter()

    def test_news_fetcher_imports_same_getter(self):
        """news_fetcher 与概念客户端从同一模块 import 同一 getter（同实例）。"""
        import data.external.akshare_rate_limiter as arl_mod
        import data.external.news_fetcher as nf_mod

        assert nf_mod.get_akshare_rate_limiter is arl_mod.get_akshare_rate_limiter
        assert nf_mod.get_akshare_rate_limiter() is get_akshare_rate_limiter()


class TestSharedLimiterConcurrentConsume:
    def test_concurrent_consume_never_negative(self):
        """2 线程并发 consume(1)：capacity 2 恰可突发，余额不转负（D1-5 收敛窗口）。"""
        clock = VirtualClock()
        limiter = get_akshare_rate_limiter()
        results: list[str] = []
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                limiter.consume(1)
                results.append("ok")
            except BaseException as exc:  # 收集线程内任何异常供断言
                errors.append(exc)

        # VirtualClock 冻结时间基数：patched 后 elapsed 计算为 0，不补充令牌，
        # 使并发语义确定（容量内恰可突发）。
        with (
            patch("utils.rate_limiter.time.monotonic", clock.now),
            patch("utils.rate_limiter.time.sleep", clock.sleep),
        ):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert errors == []
        assert results == ["ok", "ok"]
        assert limiter.tokens >= 0
