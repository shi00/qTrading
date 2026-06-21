"""测试 _run_with_python_string_storage 在并发场景下的全局选项隔离。

覆盖范围:
- 单线程下 string_storage 选项的设置与恢复
- 异常路径下全局选项的恢复保证
- 多线程并发调用时全局选项不被互相污染
- 内部锁对临界区交错变更的互斥保护
"""

import threading

import pandas as pd
import pytest

from data.external.news_fetcher import _run_with_python_string_storage


@pytest.mark.slow
class TestConcurrentStringStorage:
    """验证 _run_with_python_string_storage 的选项隔离与并发安全行为。"""

    def test_run_with_python_string_storage_restores_original(self):
        original = pd.options.mode.string_storage
        _run_with_python_string_storage(lambda: None)
        assert pd.options.mode.string_storage == original

    def test_run_with_python_string_storage_sets_python_during_execution(self):
        observed = []

        def inspector():
            observed.append(pd.options.mode.string_storage)

        _run_with_python_string_storage(inspector)
        assert observed == ["python"]

    def test_run_with_python_string_storage_returns_result(self):
        result = _run_with_python_string_storage(lambda: 42)
        assert result == 42

    def test_run_with_python_string_storage_restores_on_exception(self):
        """被包裹函数抛异常时，全局 string_storage 选项仍应被恢复到原始值。"""
        original = pd.options.mode.string_storage

        def failing():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            _run_with_python_string_storage(failing)

        assert pd.options.mode.string_storage == original

    def test_concurrent_fetchers_do_not_corrupt_global_option(self):
        """5 个并发 fetcher 同时执行时，各自临界区内观察到的选项应始终为 'python'，且结束后全局选项恢复原值。"""
        original = pd.options.mode.string_storage
        errors = []
        observed_values = []
        barrier = threading.Barrier(5, timeout=10)
        proceed = threading.Event()

        def concurrent_fetcher(fetch_id):
            try:
                barrier.wait()

                def work():
                    observed_values.append(pd.options.mode.string_storage)
                    proceed.wait(timeout=2.0)
                    observed_values.append(pd.options.mode.string_storage)
                    return fetch_id

                _run_with_python_string_storage(work)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=concurrent_fetcher, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        proceed.set()
        for t in threads:
            t.join(timeout=15)

        assert not errors, f"Concurrent errors: {errors}"
        assert all(v == "python" for v in observed_values), f"Non-python values observed: {observed_values}"
        assert pd.options.mode.string_storage == original

    def test_lock_prevents_interleaved_option_mutation(self):
        """内部锁应保证两个 fetcher 的临界区不交错，A_start~A_end 与 B_start~B_end 区间不得重叠。"""
        original = pd.options.mode.string_storage
        execution_log = []
        a_in_critical = threading.Event()
        proceed = threading.Event()

        def slow_fetcher(label):
            def work():
                execution_log.append(f"{label}_start")
                if label == "A":
                    a_in_critical.set()
                proceed.wait(timeout=5.0)
                execution_log.append(f"{label}_end")
                return label

            return _run_with_python_string_storage(work)

        results: list[str | None] = [None, None]

        def run_0():
            results[0] = slow_fetcher("A")

        def run_1():
            results[1] = slow_fetcher("B")

        t0 = threading.Thread(target=run_0)
        t1 = threading.Thread(target=run_1)
        t0.start()
        a_in_critical.wait(timeout=5.0)
        t1.start()
        proceed.set()
        t0.join(timeout=10)
        t1.join(timeout=10)

        assert results == ["A", "B"]
        assert pd.options.mode.string_storage == original

        a_start = execution_log.index("A_start")
        a_end = execution_log.index("A_end")
        b_start = execution_log.index("B_start")
        b_end = execution_log.index("B_end")

        a_range = (a_start, a_end)
        b_range = (b_start, b_end)
        overlap = max(a_range[0], b_range[0]) <= min(a_range[1], b_range[1])
        assert not overlap, f"Critical sections overlapped: {execution_log}"
