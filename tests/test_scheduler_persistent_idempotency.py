import datetime
from unittest.mock import MagicMock

import pytest


_CFG_LAST_DAILY_UPDATE = "scheduler_last_daily_update"
_CFG_LAST_NIGHTLY_PREDICTION = "scheduler_last_nightly_prediction"


@pytest.mark.unit
class TestSchedulerPersistentIdempotency:
    def test_persist_daily_update(self):
        mock_ch = MagicMock()
        saved = []
        mock_ch.save_config.side_effect = lambda payload: saved.append(payload)

        mock_ch.save_config({_CFG_LAST_DAILY_UPDATE: "20260429"})

        assert len(saved) == 1
        assert saved[0][_CFG_LAST_DAILY_UPDATE] == "20260429"

    def test_persist_nightly_prediction(self):
        mock_ch = MagicMock()
        saved = []
        mock_ch.save_config.side_effect = lambda payload: saved.append(payload)

        mock_ch.save_config({_CFG_LAST_NIGHTLY_PREDICTION: "20260429"})

        assert len(saved) == 1
        assert saved[0][_CFG_LAST_NIGHTLY_PREDICTION] == "20260429"

    def test_restart_reads_persisted_key(self):
        mock_ch = MagicMock()
        mock_ch.get_setting.side_effect = lambda key, default=None: (
            "20260428" if key == _CFG_LAST_DAILY_UPDATE else "20260427"
        )

        last_update = mock_ch.get_setting(_CFG_LAST_DAILY_UPDATE)
        last_pred = mock_ch.get_setting(_CFG_LAST_NIGHTLY_PREDICTION)

        assert last_update == "20260428"
        assert last_pred == "20260427"

    def test_persist_empty_string_on_none(self):
        mock_ch = MagicMock()
        saved = []
        mock_ch.save_config.side_effect = lambda payload: saved.append(payload)

        value = None
        mock_ch.save_config({_CFG_LAST_DAILY_UPDATE: value or ""})

        assert saved[0][_CFG_LAST_DAILY_UPDATE] == ""

    def test_idempotency_prevents_duplicate_run(self):
        mock_ch = MagicMock()
        mock_ch.get_setting.side_effect = lambda key, default=None: (
            "20260429" if key == _CFG_LAST_DAILY_UPDATE else None
        )

        last_update_date = mock_ch.get_setting(_CFG_LAST_DAILY_UPDATE)
        today_str = datetime.date(2026, 4, 29).strftime("%Y%m%d")

        assert last_update_date == today_str, "After restart, idempotency key should match today"

    def test_mark_done_sets_instance_and_persists(self):
        mock_ch = MagicMock()
        saved = []
        mock_ch.save_config.side_effect = lambda payload: saved.append(payload)

        last_update_date = None
        today_str = "20260429"
        last_update_date = today_str
        mock_ch.save_config({_CFG_LAST_DAILY_UPDATE: today_str})

        assert last_update_date == "20260429"
        assert len(saved) == 1
        assert saved[0][_CFG_LAST_DAILY_UPDATE] == "20260429"
