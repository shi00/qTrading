"""Unit tests for ASYNC-001: PolarsBaseStrategy.filter() offloads collect+to_pandas to CPU thread pool."""

from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

from data.persistence.quality_gate import QualityTier
from strategies.polars_base import PolarsBaseStrategy
from utils.thread_pool import TaskType, ThreadPoolManager
import pytest


pytestmark = pytest.mark.unit


def _make_dp():
    dp = MagicMock()
    dp._quality_tier = QualityTier.GOLD
    dp.cache = MagicMock()
    return dp


def _make_strategy(**kwargs):
    """Create a concrete PolarsBaseStrategy subclass for testing."""

    class StubStrategy(PolarsBaseStrategy):
        key = "test_polars_async"

        def __init__(self):
            super().__init__("test_name", "test_desc")
            for k, v in kwargs.items():
                setattr(self, k, v)

        def _filter_logic(self, lf, context):
            return lf

    return StubStrategy()


# --- TestPolarsFilterOffloadsToCPUPool ---


async def test_filter_calls_run_async_with_cpu_task_type():
    """filter() should call ThreadPoolManager().run_async(TaskType.CPU, ...) for collect+to_pandas."""
    s = _make_strategy(enable_ai_analysis=False)
    data = pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.0]})
    dp = _make_dp()
    context = {"screening_data": data, "data_processor": dp, "params": {}}

    with patch.object(
        s,
        "check_dependencies",
        return_value={"status": "ok", "missing_keys": [], "missing_tables": []},
    ):
        with patch.object(ThreadPoolManager, "run_async", new_callable=AsyncMock) as mock_run_async:
            mock_run_async.return_value = data
            await s.filter(context)

            mock_run_async.assert_called_once()
            call_args = mock_run_async.call_args
            assert call_args[0][0] == TaskType.CPU


async def test_filter_run_async_receives_callable():
    """The second argument to run_async should be a callable (lambda) that performs collect+to_pandas."""
    s = _make_strategy(enable_ai_analysis=False)
    data = pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.0]})
    dp = _make_dp()
    context = {"screening_data": data, "data_processor": dp, "params": {}}

    with patch.object(
        s,
        "check_dependencies",
        return_value={"status": "ok", "missing_keys": [], "missing_tables": []},
    ):
        with patch.object(ThreadPoolManager, "run_async", new_callable=AsyncMock) as mock_run_async:
            mock_run_async.return_value = data
            await s.filter(context)

            call_args = mock_run_async.call_args
            callable_arg = call_args[0][1]
            assert callable(callable_arg), "Second argument to run_async must be callable"


async def test_filter_run_async_callable_produces_expected_dataframe():
    """The lambda passed to run_async, when invoked, should produce the correct pandas DataFrame."""
    s = _make_strategy(enable_ai_analysis=False)
    input_data = pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ"], "close": [10.0, 20.0]})
    dp = _make_dp()
    context = {"screening_data": input_data, "data_processor": dp, "params": {}}

    with patch.object(
        s,
        "check_dependencies",
        return_value={"status": "ok", "missing_keys": [], "missing_tables": []},
    ):
        with patch.object(ThreadPoolManager, "run_async", new_callable=AsyncMock) as mock_run_async:
            mock_run_async.return_value = input_data
            await s.filter(context)

            callable_arg = mock_run_async.call_args[0][1]
            result = callable_arg()
            assert isinstance(result, pd.DataFrame)
            assert len(result) == 2
            assert "ts_code" in result.columns


# --- TestPolarsFilterWithMockedThreadPool ---


async def test_filter_returns_dataframe_from_run_async():
    """filter() should return the DataFrame produced by run_async (AI disabled)."""
    s = _make_strategy(enable_ai_analysis=False)
    input_data = pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.0]})
    expected = pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.0]})
    dp = _make_dp()
    context = {"screening_data": input_data, "data_processor": dp, "params": {}}

    with patch.object(
        s,
        "check_dependencies",
        return_value={"status": "ok", "missing_keys": [], "missing_tables": []},
    ):
        with patch.object(
            ThreadPoolManager,
            "run_async",
            new_callable=AsyncMock,
            return_value=expected,
        ):
            result = await s.filter(context)
            assert not result.empty
            assert len(result) == 1
            assert "ts_code" in result.columns


async def test_filter_returns_empty_when_run_async_returns_empty():
    """filter() should return empty DataFrame when run_async returns an empty one."""
    s = _make_strategy(enable_ai_analysis=False)
    input_data = pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.0]})
    dp = _make_dp()
    context = {"screening_data": input_data, "data_processor": dp, "params": {}}

    with patch.object(
        s,
        "check_dependencies",
        return_value={"status": "ok", "missing_keys": [], "missing_tables": []},
    ):
        with patch.object(
            ThreadPoolManager,
            "run_async",
            new_callable=AsyncMock,
            return_value=pd.DataFrame(),
        ):
            result = await s.filter(context)
            assert result.empty


# --- TestPolarsFilterEmptyInputSkipsRunAsync ---


async def test_empty_screening_data_does_not_call_run_async():
    """When screening_data is an empty DataFrame, filter() should not invoke run_async."""
    s = _make_strategy(enable_ai_analysis=False)
    dp = _make_dp()
    context = {"screening_data": pd.DataFrame(), "data_processor": dp, "params": {}}

    with patch.object(
        s,
        "check_dependencies",
        return_value={"status": "ok", "missing_keys": [], "missing_tables": []},
    ):
        with patch.object(ThreadPoolManager, "run_async", new_callable=AsyncMock) as mock_run_async:
            result = await s.filter(context)
            assert result.empty
            mock_run_async.assert_not_called()


async def test_none_screening_data_does_not_call_run_async():
    """When screening_data is None, filter() should not invoke run_async."""
    s = _make_strategy(enable_ai_analysis=False)
    dp = _make_dp()
    context = {"data_processor": dp, "params": {}}

    with patch.object(
        s,
        "check_dependencies",
        return_value={"status": "ok", "missing_keys": [], "missing_tables": []},
    ):
        with patch.object(ThreadPoolManager, "run_async", new_callable=AsyncMock) as mock_run_async:
            result = await s.filter(context)
            assert result.empty
            mock_run_async.assert_not_called()


async def test_unready_dependencies_does_not_call_run_async():
    """When dependencies are unready, filter() should return empty without calling run_async."""
    s = _make_strategy(enable_ai_analysis=False)
    dp = _make_dp()
    context = {"data_processor": dp, "params": {}}

    with patch.object(
        s,
        "check_dependencies",
        return_value={
            "status": "unready",
            "missing_keys": ["screening_data"],
            "missing_tables": [],
        },
    ):
        with patch.object(ThreadPoolManager, "run_async", new_callable=AsyncMock) as mock_run_async:
            result = await s.filter(context)
            assert result.empty
            mock_run_async.assert_not_called()
