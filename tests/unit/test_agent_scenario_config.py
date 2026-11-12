"""A-01 场景注册与配置往返兼容测试（详细设计 §10.2「配置和授权归属」）。

正本约束（AGENT_SYSTEM_DETAILED_DESIGN_v1.4_2026-11-11 §10.2 / REQ RR01/D05/D08）：

- 原 ``SCENARIO_IDS``、``Literal``、``EGRESS_PURPOSES``、默认 route 新增
  ``agent_research``（§10.2 第一条）；
- 旧配置补「禁用空候选」行（enabled=False、candidates=[]），原三场景行与
  旧 grant **往返不变**（A-01 DoD），不自动给旧 grant 新增范围；
- 显式连接不暗备用（REQ RR01 / D08 / §10.2「禁备用」）：agent_research 默认行
  ``fallback_enabled=False``；场景层禁暗备，备用 attempt 仅显式登记（§3.4）；
- agent_research 默认行总时限取 REQ D05 总执行活跃时限（60 分钟 = 3600 秒）；
  禁用空候选下行内数值不参与派发，仅为 schema 必填占位。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

import utils.config_handler as cfg_mod
from services.model_configuration_service import ModelConfigurationService, _PURPOSES
from utils.config_models import (
    EGRESS_PURPOSES,
    SCENARIO_IDS,
    EgressGrantConfig,
    ModelManagementConfig,
    ScenarioRouteConfig,
    _default_scenario_routes,
)

CONN_A = "11111111-1111-4111-8111-111111111111"

AGENT_RESEARCH = "agent_research"
LEGACY_SCENARIOS = ("news_annotation", "stock_analysis", "news_risk")


# ---------------------------------------------------------------------------
# fixture：独立配置文件（与 test_model_configuration_service 同语义）
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_config(tmp_path):
    config_file = tmp_path / "user_settings.json"
    with (
        mock.patch.object(cfg_mod, "CONFIG_FILE", str(config_file)),
        mock.patch.object(cfg_mod.ConfigHandler, "_config_cache", None),
    ):
        yield config_file


@pytest.fixture()
def service(isolated_config):
    cfg_mod.ConfigHandler.ensure_defaults()
    return ModelConfigurationService()


def _read_raw(config_file: Path) -> dict:
    return json.loads(config_file.read_text(encoding="utf-8"))


def _write_raw(config_file: Path, data: dict) -> None:
    config_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _base_connection(conn_id: str, api_base: str) -> dict:
    return {
        "connection_id": conn_id,
        "name": "官方 API",
        "adapter_kind": "litellm_chat",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "api_base": api_base,
        "billing": {"mode": "usage_only", "pricing_unit": "llm_tokens"},
    }


def _legacy_payload() -> dict:
    """自足构造 v1.4 时代的旧磁盘 model_management 域：三场景行 + 一条旧 grant。

    场景行显式写全字段（与 model_dump 输出同形），使「往返不变」逐字段断言
    精确可比；grant 引用的连接一并定义（AppConfig 引用完整性校验要求）。
    """
    return {
        "schema_version": 1,
        "config_revision": 1,
        "migration_state": "pending",
        "connections": [
            {
                **_base_connection(CONN_A, "https://api.deepseek.com"),
                "connection_revision": 1,
            }
        ],
        "scenario_routes": [
            {
                "scenario_id": "news_annotation",
                "enabled": False,
                "candidates": [],
                "execution_options": {},
                "total_timeout_seconds": 45,
                "transient_retries": 1,
                "fallback_enabled": True,
                "route_revision": 1,
                "task_routes": {"sentiment": {"mode": "bundled", "candidates": [], "reuse_bundled_sentiment": True}},
            },
            {
                "scenario_id": "stock_analysis",
                "enabled": False,
                "candidates": [],
                "execution_options": {},
                "total_timeout_seconds": 180,
                "transient_retries": 1,
                "fallback_enabled": True,
                "route_revision": 1,
                "task_routes": {},
            },
            {
                "scenario_id": "news_risk",
                "enabled": False,
                "candidates": [],
                "execution_options": {},
                "total_timeout_seconds": 90,
                "transient_retries": 1,
                "fallback_enabled": True,
                "route_revision": 1,
                "task_routes": {},
            },
        ],
        "usage_policy": {"detail_retention_days": 180},
        "security_state": {
            "security_revision": 0,
            "scope_version": 1,
            "grants": [
                {
                    "grant_id": "legacy-grant-1",
                    "connection_id": CONN_A,
                    "target_fingerprint": "https://api.deepseek.com:443",
                    "purpose": "stock_analysis",
                    "scope_version": 1,
                    "confirmed_at": "2026-01-01T00:00:00+00:00",
                    "revoked_at": None,
                }
            ],
            "revoked_credential_versions": [],
        },
    }


class TestScenarioRegistry:
    """§10.2：场景注册面新增 agent_research。"""

    def test_scenario_ids_include_agent_research(self) -> None:
        assert AGENT_RESEARCH in SCENARIO_IDS
        assert AGENT_RESEARCH in EGRESS_PURPOSES
        # 旧三场景与 synthetic_test 仍在注册面（旧链归属不变，§10.2「保原场景归属」）
        for legacy in (*LEGACY_SCENARIOS, "synthetic_test"):
            assert legacy in EGRESS_PURPOSES

    def test_scenario_route_literal_accepts_agent_research(self) -> None:
        route = ScenarioRouteConfig(scenario_id=AGENT_RESEARCH, total_timeout_seconds=3600)
        assert route.scenario_id == AGENT_RESEARCH

    def test_egress_grant_purpose_accepts_agent_research(self) -> None:
        grant = EgressGrantConfig(
            grant_id="g-1",
            connection_id=CONN_A,
            target_fingerprint="https://api.example.com:443",
            purpose=AGENT_RESEARCH,
            scope_version=1,
            confirmed_at="2026-01-01T00:00:00+00:00",
        )
        assert grant.purpose == AGENT_RESEARCH


class TestDefaultScenarioRoutes:
    """默认 route 新增 agent_research（§10.2 第一条）。"""

    def test_defaults_contain_four_rows(self) -> None:
        routes = _default_scenario_routes()
        assert [r.scenario_id for r in routes] == [*LEGACY_SCENARIOS, AGENT_RESEARCH]

    def test_agent_research_row_disabled_empty_candidates(self) -> None:
        route = next(r for r in _default_scenario_routes() if r.scenario_id == AGENT_RESEARCH)
        # 「旧配置补禁用空候选」同口径：默认行禁用、空候选
        assert route.enabled is False
        assert route.candidates == []

    def test_agent_research_row_no_dark_fallback(self) -> None:
        # REQ RR01/D08、§10.2：显式 A 不暗备用——agent_research 默认行显式禁备用
        route = next(r for r in _default_scenario_routes() if r.scenario_id == AGENT_RESEARCH)
        assert route.fallback_enabled is False

    def test_agent_research_row_timeout_matches_d05(self) -> None:
        # REQ D05：总执行活跃时限 60 分钟（等待/主动中断不计）
        route = next(r for r in _default_scenario_routes() if r.scenario_id == AGENT_RESEARCH)
        assert route.total_timeout_seconds == 3600

    def test_legacy_rows_keep_legacy_fallback_default(self) -> None:
        # 旧三场景默认行保持既有语义（fallback_enabled=True），不被新场景牵连改变
        for sid in LEGACY_SCENARIOS:
            route = next(r for r in _default_scenario_routes() if r.scenario_id == sid)
            assert route.fallback_enabled is True


class TestLegacyConfigRoundTrip:
    """旧配置加载补「禁用空候选」行；旧三场景行与旧 grant 往返不变（A-01 DoD）。"""

    def test_legacy_config_gains_agent_research_row(self, service, isolated_config) -> None:
        raw = _read_raw(isolated_config)
        raw["model_management"] = _legacy_payload()
        _write_raw(isolated_config, raw)
        cfg_mod.ConfigHandler._clear_cache()
        mm = ModelManagementConfig.model_validate(
            cfg_mod.ConfigHandler.read_model_management_fresh()["model_management"]
        )
        ids = [r.scenario_id for r in mm.scenario_routes]
        assert AGENT_RESEARCH in ids

    def test_legacy_three_rows_unchanged_after_validation(self) -> None:
        legacy = _legacy_payload()
        validated = ModelManagementConfig.model_validate(legacy).model_dump()
        routes = {r["scenario_id"]: r for r in validated["scenario_routes"]}
        for legacy_route in legacy["scenario_routes"]:
            sid = legacy_route["scenario_id"]
            # 逐字段相等：补行不得改动旧场景行的任何字段
            assert routes[sid] == legacy_route, f"旧场景行被改动: {sid}"

    def test_legacy_grants_unchanged_after_validation(self) -> None:
        legacy = _legacy_payload()
        validated = ModelManagementConfig.model_validate(legacy).model_dump()
        # 旧 grant 逐字段往返不变；旧 grant 不自动获得 agent_research 用途（无新行）
        assert validated["security_state"]["grants"] == legacy["security_state"]["grants"]
        assert all(g["purpose"] != AGENT_RESEARCH for g in validated["security_state"]["grants"])

    def test_appended_row_is_disabled_empty(self) -> None:
        legacy = _legacy_payload()
        validated = ModelManagementConfig.model_validate(legacy)
        row = next(r for r in validated.scenario_routes if r.scenario_id == AGENT_RESEARCH)
        assert row.enabled is False
        assert row.candidates == []

    def test_existing_agent_research_row_not_overwritten(self) -> None:
        legacy = _legacy_payload()
        legacy["scenario_routes"] = [
            *legacy["scenario_routes"],
            {
                "scenario_id": AGENT_RESEARCH,
                "enabled": True,
                "candidates": [CONN_A],
                "total_timeout_seconds": 7200,
            },
        ]
        validated = ModelManagementConfig.model_validate(legacy)
        rows = [r for r in validated.scenario_routes if r.scenario_id == AGENT_RESEARCH]
        # 不重复补行，不覆盖用户已有行
        assert len(rows) == 1
        assert rows[0].enabled is True
        assert rows[0].candidates == [CONN_A]
        assert rows[0].total_timeout_seconds == 7200

    def test_duplicate_scenario_rows_rejected(self) -> None:
        legacy = _legacy_payload()
        legacy["scenario_routes"] = [
            *legacy["scenario_routes"],
            dict(legacy["scenario_routes"][0]),
        ]
        with pytest.raises(ValueError, match="重复"):
            ModelManagementConfig.model_validate(legacy)


class TestServiceLayerPurpose:
    """授权 owner（ModelConfigurationService）放行 agent_research 用途（§10.2）。"""

    def test_purposes_registry_includes_agent_research(self) -> None:
        assert AGENT_RESEARCH in _PURPOSES

    @pytest.mark.asyncio
    async def test_grant_egress_accepts_agent_research_purpose(self, service, isolated_config) -> None:
        mm = dict(cfg_mod.ConfigHandler.read_model_management_fresh()["model_management"])
        expected = mm["config_revision"]
        mm["connections"] = [_base_connection(CONN_A, "https://api.deepseek.com")]
        await service.save_draft(mm, expected, "op-ar0")

        outcome = await service.grant_egress(
            connection_id=CONN_A,
            target_fingerprint="https://api.deepseek.com:443",
            purpose=AGENT_RESEARCH,
            scope_version=1,
            expected_revision=cfg_mod.ConfigHandler.read_model_management_fresh()["model_management"][
                "config_revision"
            ],
            operation_id="op-ar1",
        )
        assert outcome.status == "committed"

        fresh = cfg_mod.ConfigHandler.read_model_management_fresh()["model_management"]
        grants = [g for g in fresh["security_state"]["grants"] if g["purpose"] == AGENT_RESEARCH]
        assert len(grants) == 1

        permit = await service.authorize_attempt(
            connection_id=CONN_A,
            target_fingerprint="https://api.deepseek.com:443",
            purpose=AGENT_RESEARCH,
            scope_version=1,
            credential_version=None,
            attempt_id="att-ar1",
        )
        assert permit.connection_id == CONN_A

    @pytest.mark.asyncio
    async def test_legacy_grants_do_not_cover_agent_research(self, service, isolated_config) -> None:
        """旧 grant 不扩权（§10.2「不自动给旧 grant 新增范围」）：stock_analysis
        grant 不匹配 agent_research 用途的 authorize_attempt。"""
        mm = dict(cfg_mod.ConfigHandler.read_model_management_fresh()["model_management"])
        mm["connections"] = [_base_connection(CONN_A, "https://api.deepseek.com")]
        await service.save_draft(mm, mm["config_revision"], "op-ar2")
        await service.grant_egress(
            connection_id=CONN_A,
            target_fingerprint="https://api.deepseek.com:443",
            purpose="stock_analysis",
            scope_version=1,
            expected_revision=cfg_mod.ConfigHandler.read_model_management_fresh()["model_management"][
                "config_revision"
            ],
            operation_id="op-ar3",
        )

        with pytest.raises(PermissionError, match="无匹配外发授权"):
            await service.authorize_attempt(
                connection_id=CONN_A,
                target_fingerprint="https://api.deepseek.com:443",
                purpose=AGENT_RESEARCH,
                scope_version=1,
                credential_version=None,
                attempt_id="att-ar2",
            )
