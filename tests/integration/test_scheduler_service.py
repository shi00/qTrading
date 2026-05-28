"""Tests for SchedulerService singleton management."""

import datetime
import sys
import threading
import typing
import types
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


class TestSchedulerServiceSingleton:
    """Tests for SchedulerService singleton pattern and reset behavior."""

    def test_scheduler_has_reset_singleton(self):
        """Test that SchedulerService has _reset_singleton method."""
        with patch.dict(
            "sys.modules",
            {"utils.scheduler_service": MagicMock()},
        ):
            from utils.scheduler_service import SchedulerService

            SchedulerService._reset_singleton = MagicMock()
            SchedulerService._lock = threading.Lock()

            assert hasattr(SchedulerService, "_reset_singleton")
            assert hasattr(SchedulerService, "_lock")

    def test_scheduler_singleton_reset_clears_instance(self):
        """Test that _reset_singleton clears the singleton instance."""

        class MockSchedulerService:
            _instance = None
            _initialized = False
            _lock = threading.Lock()

            @classmethod
            def _reset_singleton(cls):
                with cls._lock:
                    if cls._instance is not None and hasattr(cls._instance, "scheduler"):
                        try:
                            if cls._instance.scheduler.running:
                                cls._instance.scheduler.shutdown(wait=False)
                        except (RuntimeError, AttributeError):
                            pass
                    cls._instance = None
                    cls._initialized = False

        MockSchedulerService._reset_singleton()
        assert MockSchedulerService._instance is None
        assert MockSchedulerService._initialized is False

    def test_scheduler_singleton_reset_shuts_down_running_scheduler(self):
        """Test that _reset_singleton properly shuts down running scheduler to prevent ghost threads."""

        class MockSchedulerService:
            _instance = None
            _initialized = False
            _lock = threading.Lock()

            @classmethod
            def _reset_singleton(cls):
                with cls._lock:
                    if cls._instance is not None and hasattr(cls._instance, "scheduler"):
                        try:
                            if cls._instance.scheduler.running:
                                cls._instance.scheduler.shutdown(wait=False)
                        except (RuntimeError, AttributeError):
                            pass
                    cls._instance = None
                    cls._initialized = False

        mock_scheduler = MagicMock()
        mock_scheduler.running = True

        instance = MagicMock()
        instance.scheduler = mock_scheduler

        MockSchedulerService._instance = instance
        MockSchedulerService._initialized = True

        MockSchedulerService._reset_singleton()

        mock_scheduler.shutdown.assert_called_once_with(wait=False)
        assert MockSchedulerService._instance is None
        assert MockSchedulerService._initialized is False

    def test_scheduler_singleton_reset_handles_non_running_scheduler(self):
        """Test that _reset_singleton handles non-running scheduler gracefully."""

        class MockSchedulerService:
            _instance = None
            _initialized = False
            _lock = threading.Lock()

            @classmethod
            def _reset_singleton(cls):
                with cls._lock:
                    if cls._instance is not None and hasattr(cls._instance, "scheduler"):
                        try:
                            if cls._instance.scheduler.running:
                                cls._instance.scheduler.shutdown(wait=False)
                        except (RuntimeError, AttributeError):
                            pass
                    cls._instance = None
                    cls._initialized = False

        mock_scheduler = MagicMock()
        mock_scheduler.running = False

        instance = MagicMock()
        instance.scheduler = mock_scheduler

        MockSchedulerService._instance = instance
        MockSchedulerService._initialized = True

        MockSchedulerService._reset_singleton()

        mock_scheduler.shutdown.assert_not_called()
        assert MockSchedulerService._instance is None
        assert MockSchedulerService._initialized is False

    def test_scheduler_singleton_reset_handles_shutdown_exception(self):
        """Test that _reset_singleton handles shutdown exceptions gracefully."""

        class MockSchedulerService:
            _instance = None
            _initialized = False
            _lock = threading.Lock()

            @classmethod
            def _reset_singleton(cls):
                with cls._lock:
                    if cls._instance is not None and hasattr(cls._instance, "scheduler"):
                        try:
                            if cls._instance.scheduler.running:
                                cls._instance.scheduler.shutdown(wait=False)
                        except (RuntimeError, AttributeError):
                            pass
                    cls._instance = None
                    cls._initialized = False

        mock_scheduler = MagicMock()
        mock_scheduler.running = True
        mock_scheduler.shutdown.side_effect = RuntimeError("Shutdown failed")

        instance = MagicMock()
        instance.scheduler = mock_scheduler

        MockSchedulerService._instance = instance
        MockSchedulerService._initialized = True

        MockSchedulerService._reset_singleton()

        assert MockSchedulerService._instance is None
        assert MockSchedulerService._initialized is False


@pytest.mark.asyncio
async def test_daily_update_logic_handles_dataframe_result_without_bool_error(monkeypatch):
    """_daily_update_logic should not evaluate DataFrame in boolean context."""
    import services.task_manager as tm_mod
    import utils.scheduler_service as sched_mod

    sched_mod.SchedulerService._reset_singleton()
    service = sched_mod.SchedulerService()
    service._last_update_date = None

    fake_now = datetime.datetime(2026, 4, 23, 16, 30, 0)
    monkeypatch.setattr(sched_mod, "get_now", lambda: fake_now)
    monkeypatch.setattr(sched_mod.ConfigHandler, "is_auto_update_enabled", staticmethod(lambda: True))
    monkeypatch.setattr(
        sched_mod.I18n,
        "get",
        staticmethod(lambda key, **kwargs: f"{key}:{kwargs.get('added', kwargs.get('date', ''))}"),
    )

    class _FakeTradeCalendar:
        async def is_trading_day(self, _today):
            return True

    class _FakeProcessor:
        def __init__(self):
            self.trade_calendar = _FakeTradeCalendar()

        async def run_daily_update(self, progress_callback=None):
            if progress_callback:
                progress_callback(1, 2, "half")
                progress_callback(2, 2, "done")
            return pd.DataFrame([{"ts_code": "000001.SZ"}, {"ts_code": "000002.SZ"}])

    monkeypatch.setattr(sched_mod, "DataProcessor", _FakeProcessor)

    holder: dict[str, typing.Any] = {"factory": None}

    class _FakeTaskManager:
        def update_progress(self, *_args, **_kwargs):
            return None

        def submit_task(self, **kwargs):
            holder["factory"] = kwargs.get("coroutine_factory")

    monkeypatch.setattr(tm_mod, "TaskManager", _FakeTaskManager)

    await service._run_daily_update()
    assert holder["factory"] is not None

    factory = holder["factory"]
    msg = await factory("task-id")
    assert msg == "sched_daily_done:2"


@pytest.mark.asyncio
async def test_nightly_prediction_passes_trade_date_to_save_results(monkeypatch):
    """夜间预测任务应将 context.trade_date 透传到 save_results，并生成唯一 run_id。"""
    import services.task_manager as tm_mod
    import utils.scheduler_service as sched_mod

    sched_mod.SchedulerService._reset_singleton()
    service = sched_mod.SchedulerService()
    service._last_pred_date = None

    fake_now = datetime.datetime(2026, 4, 23, 20, 30, 0)
    monkeypatch.setattr(sched_mod, "get_now", lambda: fake_now)
    monkeypatch.setattr(sched_mod.ConfigHandler, "is_auto_update_enabled", staticmethod(lambda: True))
    monkeypatch.setattr(
        sched_mod.I18n,
        "get",
        staticmethod(lambda key, **kwargs: f"{key}:{kwargs.get('count', kwargs.get('date', ''))}"),
    )

    holder: dict[str, typing.Any] = {"factory": None, "saved": None, "unique_key": None}

    class _FakeTradeCalendar:
        async def is_trading_day(self, _today):
            return True

    class _FakeProcessor:
        def __init__(self):
            self.trade_calendar = _FakeTradeCalendar()

        async def init_data(self):
            return None

        async def prepare_market_data(self):
            return None

        async def get_strategy_data(self):
            return {
                "screening_data": pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260423"]}),
                "trade_date": "20260423",
            }

    class _FakeReviewManager:
        async def save_results(self, strategy_name, result_df, trade_date=None, run_id=None, params_snapshot=None):
            holder["saved"] = (strategy_name, trade_date, run_id, result_df.copy())

    class _FakeTaskManager:
        def update_progress(self, *_args, **_kwargs):
            return None

        def submit_task(self, **kwargs):
            holder["factory"] = kwargs.get("coroutine_factory")
            holder["unique_key"] = kwargs.get("unique_key")

    class _FakeStrategy:
        async def filter(self, context):
            assert context["trade_date"] == "20260423"
            return pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["平安银行"]})

    monkeypatch.setattr(sched_mod, "DataProcessor", _FakeProcessor)
    monkeypatch.setattr(sched_mod, "ReviewManager", _FakeReviewManager)
    monkeypatch.setattr(tm_mod, "TaskManager", _FakeTaskManager)
    monkeypatch.setitem(sys.modules, "strategies.ai_strategy", types.SimpleNamespace(AISelectionStrategy=_FakeStrategy))

    await service._run_nightly_prediction()
    assert holder["factory"] is not None
    assert holder["unique_key"] == "nightly_prediction", "nightly_prediction should have unique_key for deduplication"

    msg = await holder["factory"]("task-id")
    assert msg == "sched_pred_done_found:1"
    assert holder["saved"] is not None
    assert holder["saved"][0] == "AI_Auto_Nightly"
    assert holder["saved"][1] == "20260423"
    assert holder["saved"][2] is not None, "save_results should receive a non-None run_id"
    assert len(holder["saved"][2]) == 16, f"run_id should be 16 chars, got {len(holder['saved'][2])}"


@pytest.mark.asyncio
async def test_nightly_prediction_raises_when_trade_date_missing(monkeypatch):
    """夜间预测任务在缺少 context.trade_date 时应拒绝保存。"""
    import services.task_manager as tm_mod
    import utils.scheduler_service as sched_mod

    sched_mod.SchedulerService._reset_singleton()
    service = sched_mod.SchedulerService()
    service._last_pred_date = None

    fake_now = datetime.datetime(2026, 4, 23, 20, 30, 0)
    monkeypatch.setattr(sched_mod, "get_now", lambda: fake_now)
    monkeypatch.setattr(sched_mod.ConfigHandler, "is_auto_update_enabled", staticmethod(lambda: True))
    monkeypatch.setattr(
        sched_mod.I18n,
        "get",
        staticmethod(lambda key, **kwargs: f"{key}:{kwargs.get('count', kwargs.get('date', ''))}"),
    )

    holder: dict[str, typing.Any] = {"factory": None, "save_called": False, "unique_key": None}

    class _FakeTradeCalendar:
        async def is_trading_day(self, _today):
            return True

    class _FakeProcessor:
        def __init__(self):
            self.trade_calendar = _FakeTradeCalendar()

        async def init_data(self):
            return None

        async def prepare_market_data(self):
            return None

        async def get_strategy_data(self):
            return {"screening_data": pd.DataFrame({"ts_code": ["000001.SZ"]})}

    class _FakeReviewManager:
        async def save_results(self, strategy_name, result_df, trade_date=None, run_id=None, params_snapshot=None):
            holder["save_called"] = True

    class _FakeTaskManager:
        def update_progress(self, *_args, **_kwargs):
            return None

        def submit_task(self, **kwargs):
            holder["factory"] = kwargs.get("coroutine_factory")
            holder["unique_key"] = kwargs.get("unique_key")

    class _FakeStrategy:
        async def filter(self, context):
            return pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["平安银行"]})

    monkeypatch.setattr(sched_mod, "DataProcessor", _FakeProcessor)
    monkeypatch.setattr(sched_mod, "ReviewManager", _FakeReviewManager)
    monkeypatch.setattr(tm_mod, "TaskManager", _FakeTaskManager)
    monkeypatch.setitem(sys.modules, "strategies.ai_strategy", types.SimpleNamespace(AISelectionStrategy=_FakeStrategy))

    await service._run_nightly_prediction()
    assert holder["factory"] is not None
    assert holder["unique_key"] == "nightly_prediction", "nightly_prediction should have unique_key for deduplication"

    with pytest.raises(RuntimeError, match="missing trade_date"):
        await holder["factory"]("task-id")
    assert holder["save_called"] is False


def test_scheduler_loads_persisted_idempotency_dates(monkeypatch):
    """重建调度器时应从配置恢复最近执行日期。"""
    import utils.scheduler_service as sched_mod

    sched_mod.SchedulerService._reset_singleton()

    values = {
        "scheduler_last_daily_update": "20260428",
        "scheduler_last_nightly_prediction": "20260427",
    }
    monkeypatch.setattr(
        sched_mod.ConfigHandler, "get_setting", staticmethod(lambda key, default=None: values.get(key, default))
    )

    service = sched_mod.SchedulerService()

    assert service._last_update_date == "20260428"
    assert service._last_pred_date == "20260427"


def test_scheduler_marks_run_dates_and_persists(monkeypatch):
    """任务完成后应同时更新内存态和持久化配置。"""
    import utils.scheduler_service as sched_mod

    sched_mod.SchedulerService._reset_singleton()
    saved = []
    monkeypatch.setattr(sched_mod.ConfigHandler, "get_setting", staticmethod(lambda key, default=None: default))
    monkeypatch.setattr(
        sched_mod.ConfigHandler, "save_config", staticmethod(lambda payload: saved.append(payload) or True)
    )

    service = sched_mod.SchedulerService()
    service._mark_daily_update_done("20260428")
    service._mark_nightly_prediction_done("20260428")

    assert service._last_update_date == "20260428"
    assert service._last_pred_date == "20260428"
    assert {"scheduler_last_daily_update": "20260428"} in saved
    assert {"scheduler_last_nightly_prediction": "20260428"} in saved
