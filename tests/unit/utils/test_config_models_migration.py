"""S6 D3 迁移屏障配置 schema 回归（utils/config_models.py）。

覆盖 reviews/s6_plan.md §2「config_models」行：
- MigrationTransitionConfig / LegacySealConfig 校验（非法 phase / epoch<1 / month 格式 /
  extra forbid / 同月唯一）；
- 旧数据 migration_transition=None 兼容与 dict 自动校验收紧。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from utils.config_models import AppConfig, MigrationTransitionConfig, ModelManagementConfig


def _valid_transition(**overrides):
    data = {
        "transition_id": "t-1",
        "owner_session_id": "owner-1",
        "transition_epoch": 1,
        "phase": "draining",
        "lease_expires_at": "2026-10-19T12:00:00+00:00",
        "source_fingerprint": "fp-1",
        "legacy_seals": [],
    }
    data.update(overrides)
    return data


class TestMigrationTransitionConfig:
    def test_valid_draining_minimal(self):
        transition = MigrationTransitionConfig.model_validate(_valid_transition())
        assert transition.phase == "draining"
        assert transition.transition_epoch == 1
        assert transition.legacy_seals == []

    def test_invalid_phase_rejected(self):
        with pytest.raises(ValidationError) as excinfo:
            MigrationTransitionConfig.model_validate(_valid_transition(phase="active"))
        assert "phase" in str(excinfo.value)

    def test_epoch_below_one_rejected(self):
        with pytest.raises(ValidationError) as excinfo:
            MigrationTransitionConfig.model_validate(_valid_transition(transition_epoch=0))
        assert "transition_epoch" in str(excinfo.value)

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError) as excinfo:
            MigrationTransitionConfig.model_validate(_valid_transition(unknown_key=1))
        assert "unknown_key" in str(excinfo.value)

    def test_seal_month_format_rejected(self):
        with pytest.raises(ValidationError) as excinfo:
            MigrationTransitionConfig.model_validate(
                _valid_transition(legacy_seals=[{"month": "202610", "legacy_seal_id": "s-1", "sealed_at": "x"}])
            )
        assert "month" in str(excinfo.value)

    def test_duplicate_seal_months_rejected(self):
        seal = {"month": "2026-10", "legacy_seal_id": "s-1", "sealed_at": "x"}
        with pytest.raises(ValidationError) as excinfo:
            MigrationTransitionConfig.model_validate(_valid_transition(legacy_seals=[seal, dict(seal)]))
        assert "month 重复" in str(excinfo.value)

    def test_distinct_seal_months_accepted(self):
        seals = [
            {"month": "2026-10", "legacy_seal_id": "s-1", "sealed_at": "x"},
            {"month": "2026-11", "legacy_seal_id": "s-2", "sealed_at": "y"},
        ]
        transition = MigrationTransitionConfig.model_validate(_valid_transition(legacy_seals=seals))
        assert [s.month for s in transition.legacy_seals] == ["2026-10", "2026-11"]


class TestModelManagementMigrationField:
    def test_default_migration_transition_is_none(self):
        assert ModelManagementConfig().migration_transition is None

    def test_dict_payload_coerced_to_model(self):
        """D3 收紧：dict 载入由 pydantic 自动校验为 MigrationTransitionConfig。"""
        mm = ModelManagementConfig.model_validate({"migration_transition": _valid_transition()})
        assert isinstance(mm.migration_transition, MigrationTransitionConfig)
        assert mm.migration_transition is not None
        assert mm.migration_transition.phase == "draining"
        assert mm.migration_transition.transition_epoch == 1

    def test_legacy_data_none_roundtrip(self):
        """旧数据 migration_transition=None 兼容：校验通过且 dump 保持 None。"""
        mm = ModelManagementConfig.model_validate({"migration_transition": None})
        assert mm.migration_transition is None
        assert mm.model_dump()["migration_transition"] is None

    def test_appconfig_rejects_invalid_nested_transition(self):
        with pytest.raises(ValidationError) as excinfo:
            AppConfig.model_validate({"model_management": {"migration_transition": _valid_transition(phase="bogus")}})
        assert "phase" in str(excinfo.value)
