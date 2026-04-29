import threading
import time

import pandas as pd
import pytest

from data.external.news_fetcher import _run_with_python_string_storage


@pytest.mark.unit
class TestConcurrentStringStorage:
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
        original = pd.options.mode.string_storage

        def failing():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            _run_with_python_string_storage(failing)

        assert pd.options.mode.string_storage == original

    def test_concurrent_fetchers_do_not_corrupt_global_option(self):
        original = pd.options.mode.string_storage
        errors = []
        observed_values = []
        barrier = threading.Barrier(5, timeout=10)

        def concurrent_fetcher(fetch_id):
            try:
                barrier.wait()

                def work():
                    observed_values.append(pd.options.mode.string_storage)
                    time.sleep(0.02)
                    observed_values.append(pd.options.mode.string_storage)
                    return fetch_id

                _run_with_python_string_storage(work)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=concurrent_fetcher, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not errors, f"Concurrent errors: {errors}"
        assert all(v == "python" for v in observed_values), f"Non-python values observed: {observed_values}"
        assert pd.options.mode.string_storage == original

    def test_lock_prevents_interleaved_option_mutation(self):
        original = pd.options.mode.string_storage
        execution_log = []

        def slow_fetcher(label):
            def work():
                execution_log.append(f"{label}_start")
                time.sleep(0.05)
                execution_log.append(f"{label}_end")
                return label

            return _run_with_python_string_storage(work)

        results = [None, None]

        def run_0():
            results[0] = slow_fetcher("A")

        def run_1():
            results[1] = slow_fetcher("B")

        t0 = threading.Thread(target=run_0)
        t1 = threading.Thread(target=run_1)
        t0.start()
        time.sleep(0.01)
        t1.start()
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
