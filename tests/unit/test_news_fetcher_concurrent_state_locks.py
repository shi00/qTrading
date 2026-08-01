"""测试 _SINA_STATE_LOCK / _CLS_STATE_LOCK 锁对象的可用性 (F5-P1)。

覆盖范围:
- _SINA_STATE_LOCK 锁对象存在且可用，N 线程并发递增 _SINA_CONSECUTIVE_EMPTY["us_api"] 最终值精确等于 N*M
- _CLS_STATE_LOCK 锁对象存在且可用，N 线程并发递增 _CLS_CONSECUTIVE_FAILURES 最终值精确等于 N*M
- 跨键并发（us_api + concept）不互相干扰，各自计数器独立正确

测试价值说明:
- 本测试验证的是 _SINA_STATE_LOCK / _CLS_STATE_LOCK 锁对象本身存在、类型正确（threading.Lock 而非
  asyncio.Lock，避免 R11 违规）、且在并发场景下能正确保护共享状态。
- 业务代码（get_us_major_moves / get_hot_concepts / get_latest_global_news）是否正确使用这些锁，
  由代码审查与现有单元测试（test_news_fetcher.py 中的熔断器/计数器断言）保障，本测试不重复覆盖。
- threading.Lock 的正确性由 stdlib 保证，本测试的核心价值是回归保护"锁对象定义不被移除/改名"。

归类为单元测试：不依赖数据库/网络，仅测试 threading.Lock 保护的共享状态行为。
"""

import threading

import pytest

from data.external import news_fetcher


@pytest.fixture(autouse=True)
def _reset_news_fetcher_global_state():
    """每个测试前后重置 news_fetcher 全局状态，避免测试间污染 (R7 精神)。"""
    news_fetcher._SINA_CONSECUTIVE_EMPTY["us_api"] = 0
    news_fetcher._SINA_CONSECUTIVE_EMPTY["concept"] = 0
    news_fetcher._SINA_CONSECUTIVE_FAILURES["concept"] = 0
    news_fetcher._CLS_CONSECUTIVE_FAILURES = 0
    news_fetcher._CLS_CIRCUIT_OPENED_AT = 0.0
    yield
    news_fetcher._SINA_CONSECUTIVE_EMPTY["us_api"] = 0
    news_fetcher._SINA_CONSECUTIVE_EMPTY["concept"] = 0
    news_fetcher._SINA_CONSECUTIVE_FAILURES["concept"] = 0
    news_fetcher._CLS_CONSECUTIVE_FAILURES = 0
    news_fetcher._CLS_CIRCUIT_OPENED_AT = 0.0


class TestSinaStateLockAvailability:
    """验证 _SINA_STATE_LOCK 锁对象可用性。"""

    def test_sina_state_lock_is_threading_lock(self):
        """_SINA_STATE_LOCK 必须是 threading.Lock 类型（非 asyncio.Lock，符合 R11）。"""
        assert isinstance(news_fetcher._SINA_STATE_LOCK, type(threading.Lock()))

    def test_concurrent_increment_us_api_counter_is_thread_safe(self):
        """10 线程各递增 _SINA_CONSECUTIVE_EMPTY['us_api'] 100 次，最终值应精确等于 1000。

        验证锁对象在并发场景下能正确保护共享 dict 的 read-modify-write 操作。
        """
        n_threads = 10
        n_increments = 100
        barrier = threading.Barrier(n_threads, timeout=10)

        def worker():
            barrier.wait()
            for _ in range(n_increments):
                with news_fetcher._SINA_STATE_LOCK:
                    news_fetcher._SINA_CONSECUTIVE_EMPTY["us_api"] += 1

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert news_fetcher._SINA_CONSECUTIVE_EMPTY["us_api"] == n_threads * n_increments

    def test_concurrent_increment_concept_counter_is_thread_safe(self):
        """10 线程各递增 _SINA_CONSECUTIVE_FAILURES['concept'] 100 次，最终值应精确等于 1000。"""
        n_threads = 10
        n_increments = 100
        barrier = threading.Barrier(n_threads, timeout=10)

        def worker():
            barrier.wait()
            for _ in range(n_increments):
                with news_fetcher._SINA_STATE_LOCK:
                    news_fetcher._SINA_CONSECUTIVE_FAILURES["concept"] += 1

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert news_fetcher._SINA_CONSECUTIVE_FAILURES["concept"] == n_threads * n_increments

    def test_cross_key_concurrent_increments_are_independent(self):
        """us_api 与 concept 并发递增不互相干扰，各自最终值正确。

        验证 _SINA_STATE_LOCK 保护共享 dict 内部结构，两个不同键的并发访问
        不会因 dict 内部结构共享而损坏。
        """
        n_increments = 200
        barrier = threading.Barrier(2, timeout=10)

        def us_api_worker():
            barrier.wait()
            for _ in range(n_increments):
                with news_fetcher._SINA_STATE_LOCK:
                    news_fetcher._SINA_CONSECUTIVE_EMPTY["us_api"] += 1

        def concept_worker():
            barrier.wait()
            for _ in range(n_increments):
                with news_fetcher._SINA_STATE_LOCK:
                    news_fetcher._SINA_CONSECUTIVE_FAILURES["concept"] += 1

        t1 = threading.Thread(target=us_api_worker)
        t2 = threading.Thread(target=concept_worker)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        assert news_fetcher._SINA_CONSECUTIVE_EMPTY["us_api"] == n_increments
        assert news_fetcher._SINA_CONSECUTIVE_FAILURES["concept"] == n_increments


class TestClsStateLockAvailability:
    """验证 _CLS_STATE_LOCK 锁对象可用性。"""

    def test_cls_state_lock_is_threading_lock(self):
        """_CLS_STATE_LOCK 必须是 threading.Lock 类型（非 asyncio.Lock，符合 R11）。"""
        assert isinstance(news_fetcher._CLS_STATE_LOCK, type(threading.Lock()))

    def test_concurrent_increment_cls_failures_is_thread_safe(self):
        """10 线程各递增 _CLS_CONSECUTIVE_FAILURES 100 次，最终值应精确等于 1000。

        _CLS_CONSECUTIVE_FAILURES 是模块级 int，`global` + `+=` 非原子，
        锁保护下最终值应精确。
        """
        n_threads = 10
        n_increments = 100
        barrier = threading.Barrier(n_threads, timeout=10)

        def worker():
            barrier.wait()
            for _ in range(n_increments):
                with news_fetcher._CLS_STATE_LOCK:
                    news_fetcher._CLS_CONSECUTIVE_FAILURES += 1

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert n_threads * n_increments == news_fetcher._CLS_CONSECUTIVE_FAILURES

    def test_concurrent_failure_and_reset_maintain_consistency(self):
        """并发递增与重置操作不会出现中间状态损坏（值始终为非负整数）。"""
        n_ops = 100
        barrier = threading.Barrier(2, timeout=10)
        errors: list[Exception] = []

        def incrementer():
            try:
                barrier.wait()
                for _ in range(n_ops):
                    with news_fetcher._CLS_STATE_LOCK:
                        news_fetcher._CLS_CONSECUTIVE_FAILURES += 1
            except Exception as e:
                errors.append(e)

        def resetter():
            try:
                barrier.wait()
                for _ in range(n_ops):
                    with news_fetcher._CLS_STATE_LOCK:
                        # 模拟成功路径的重置
                        news_fetcher._CLS_CONSECUTIVE_FAILURES = 0
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=incrementer)
        t2 = threading.Thread(target=resetter)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        assert not errors, f"Concurrent errors: {errors}"
        # 最终值应为 0 或正整数（取决于 reset 与 increment 的最后顺序），不能为负
        assert news_fetcher._CLS_CONSECUTIVE_FAILURES >= 0
