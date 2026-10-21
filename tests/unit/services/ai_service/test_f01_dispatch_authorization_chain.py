"""F01 分派授权身份全链回归（真实分派 + 真实配置服务 + 离线适配器）。

回归要求（F01 检视报告）：真实分派链路（``resolve_executor``）+ 真实
``ModelConfigurationService``（磁盘配置，无 keyring 依赖）+ 离线适配器替身，
验证——
- 合法 grant（同连接 / 同规范化目标 / 同用途 / 当前 scope 版本）→ 远程适配器
  恰好调用一次（适配器是本链唯一出站点，零调用即零出站）；
- 换目标 / 换用途 / 范围轮转（旧世代 grant 失配）/ grant 撤销 → 远程适配器零调用；
- 无关 grant 增删不破坏既有许可（后续尝试仍恰一次授权放行）；
- 本地 gguf 连接无任何 grant 可执行（免 egress grant），凭据撤销仍零调用。

探针 P09（空参数拒绝）/ P11（错范围拒绝）的服务层验证位于
``test_model_configuration_service.py::TestAuthorizeEgressRequirement``；
本文件覆盖「分派 → 许可 → 适配器执行」全链行为。
"""

import json
from pathlib import Path
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

import pytest

import utils.config_handler as cfg_mod
from core.model_management_types import (
    AdapterResponse,
    InferenceRequest,
    PersistenceOutcome,
    ValidatedBusinessOutput,
)
from services.ai_service import scenario_dispatch
from services.ai_service.model_compatibility import ModelCompatibilityService
from services.ai_service.scenario_executor import ScenarioExecutor
from services.model_configuration_service import ModelConfigurationService

pytestmark = pytest.mark.unit

_REMOTE_CONN = "conn-remote"
_LOCAL_CONN = "conn-local"
_SCENARIO_ID = "news_annotation"
_REMOTE_TARGET = "https://api.example.com:443"  # 磁盘 api_base 混合大小写 → 规范化目标


@pytest.fixture()
def isolated_config(tmp_path):
    """独立配置文件与干净缓存（与 test_model_configuration_service 同语义）。"""
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


@pytest.fixture(autouse=True)
def _reset_provider():
    yield
    scenario_dispatch.set_scheduling_services_provider(None)


class _OfflineAdapter:
    """离线适配器替身：仅计数调用，绝无网络 IO（零出站断言的事实源）。"""

    def __init__(self) -> None:
        self.invoke_count = 0
        self.last_attempt_id: str | None = None

    async def invoke(self, request, resolved_connection, deadline, attempt_context):
        self.invoke_count += 1
        self.last_attempt_id = attempt_context.attempt_id
        return AdapterResponse(content="ok", usage=None, actual_model=resolved_connection.model, metadata={})


def _recorder_mock() -> MagicMock:
    recorder = MagicMock()
    recorder.observe_usage = MagicMock()
    recorder.mark_dispatched = AsyncMock(return_value=PersistenceOutcome(persisted=True, error_code=None))
    recorder.begin_attempt = AsyncMock(return_value=PersistenceOutcome(persisted=True, error_code=None))
    recorder.submit_terminal = AsyncMock(return_value=PersistenceOutcome(persisted=True, error_code=None))
    return recorder


def _validator_ok(_response) -> ValidatedBusinessOutput:
    return ValidatedBusinessOutput(output={"label": "正面"}, metadata={})


def _request() -> InferenceRequest:
    # deadline 为单调时钟截止值：配合注入的假时钟（1000.0 起步）恒有余量
    return InferenceRequest(
        scenario_id=_SCENARIO_ID,
        task_id=_SCENARIO_ID,
        invocation_id="inv-f01",
        payload={"text": "测试新闻"},
        deadline=10_000.0,
    )


async def _advance_to_active(service: ModelConfigurationService) -> None:
    """F07：经真实迁移命令链把迁移状态推进到合法激活态（active + sealed 屏障）。

    新链场景路由要求 ``migration_state == "active"``；schema 拒绝无屏障的裸
    active，且草稿通道不携带迁移键，故须走 begin → seal → activate 生产路径。
    """

    def _rev() -> int:
        return cfg_mod.ConfigHandler.read_model_management_fresh()["model_management"]["config_revision"]

    outcome = await service.begin_migration(_rev(), "op-f01-begin", "owner-f01")
    assert outcome.status == "committed"
    outcome = await service.seal_legacy(_rev(), "op-f01-seal", {"2026-01": "seal-f01"}, "owner-f01")
    assert outcome.status == "committed"
    outcome = await service.activate(_rev(), "op-f01-activate")
    assert outcome.status == "committed"


async def _setup_config(service: ModelConfigurationService, candidates: list[str]) -> None:
    """写两连接（远程 litellm_chat 混合大小写 api_base + 本地 gguf_chat）并启用场景路由。

    F07：迁移子域由迁移专用命令独占——草稿 payload 不携带迁移键（普通保存
    无条件剥离）；新链场景路由要求 ``migration_state == "active"``，故草稿提交后
    经真实迁移命令链 begin → seal → activate 推进到合法激活态（active + sealed
    屏障，schema 拒绝无屏障的裸 active）。
    """
    fresh = cfg_mod.ConfigHandler.read_model_management_fresh()
    mm = dict(fresh["model_management"])
    expected = mm["config_revision"]
    mm.pop("migration_state", None)
    mm.pop("migration_transition", None)
    mm["connections"] = [
        {
            "connection_id": _REMOTE_CONN,
            "name": "官方 API",
            "adapter_kind": "litellm_chat",
            "provider": "deepseek",
            "model": "deepseek-chat",
            "api_base": "https://API.Example.COM",  # 大小写不规范，分派时规范化
            "billing": {"mode": "usage_only", "pricing_unit": "llm_tokens"},
        },
        {
            "connection_id": _LOCAL_CONN,
            "name": "本地 GGUF",
            "adapter_kind": "gguf_chat",
            "provider": "local",
            "model": "qwen2-0.5b-instruct",
            "api_base": None,
            "adapter_options": {"n_ctx": 2048},
            "billing": {"mode": "usage_only", "pricing_unit": "llm_tokens"},
        },
    ]
    route = next(r for r in mm["scenario_routes"] if r["scenario_id"] == _SCENARIO_ID)
    route["enabled"] = True
    route["candidates"] = list(candidates)
    outcome = await service.save_draft(mm, expected, "op-f01-setup")
    assert outcome.status == "committed"
    await _advance_to_active(service)


async def _grant(service: ModelConfigurationService, connection_id: str, target: str, purpose: str) -> None:
    outcome = await service.grant_egress(
        connection_id=connection_id,
        target_fingerprint=target,
        purpose=purpose,
        scope_version=1,
        expected_revision=cfg_mod.ConfigHandler.read_model_management_fresh()["model_management"]["config_revision"],
        operation_id=f"op-grant-{connection_id}-{purpose}",
    )
    assert outcome.status == "committed"


def _build_chain(
    service: ModelConfigurationService,
    *,
    remote_adapter: _OfflineAdapter | None = None,
    local_adapter: _OfflineAdapter | None = None,
) -> tuple[ScenarioExecutor, dict[str, _OfflineAdapter], dict[str, float]]:
    """组装真实执行链：真服务 + 真兼容解析 + 离线适配器 + mock 落账；返回（executor, 适配器表, 可控时钟）。"""
    adapters = {
        "litellm_chat": remote_adapter if remote_adapter is not None else _OfflineAdapter(),
        "gguf_chat": local_adapter if local_adapter is not None else _OfflineAdapter(),
    }
    now = {"t": 1000.0}
    executor = ScenarioExecutor(
        config_service=service,
        recorder=_recorder_mock(),
        compatibility=ModelCompatibilityService(),
        adapters=adapters,
        clock=lambda: now["t"],
    )
    scenario_dispatch.set_scheduling_services_provider(lambda: {"executor": executor})
    return executor, adapters, now


def _grants_on_disk() -> list[dict]:
    return cfg_mod.ConfigHandler.read_model_management_fresh()["model_management"]["security_state"]["grants"]


def _bump_scope_version_on_disk(config_file: Path, value: int) -> None:
    """范围轮转命令语义（F01 未来交付的轮转命令将封装此变更）：递增安全状态 scope_version。"""
    raw = json.loads(config_file.read_text(encoding="utf-8"))
    raw["model_management"]["security_state"]["scope_version"] = value
    config_file.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    cfg_mod.ConfigHandler._clear_cache()


class TestRemoteChainAuthorization:
    @pytest.mark.asyncio
    async def test_valid_grant_adapter_invoked_exactly_once(self, service):
        """合法 grant（规范化目标 + 场景用途 + 当前 scope）→ 远程适配器恰一次调用。"""
        await _setup_config(service, [_REMOTE_CONN])
        await _grant(service, _REMOTE_CONN, _REMOTE_TARGET, _SCENARIO_ID)
        remote, local = _OfflineAdapter(), _OfflineAdapter()
        _build_chain(service, remote_adapter=remote, local_adapter=local)

        dispatch = await scenario_dispatch.resolve_executor(_SCENARIO_ID)
        assert dispatch is not None
        # 分派身份：候选携带规范化目标（host 小写 + 显式端口）与场景用途
        cand = dispatch.snapshot.routes[0]["candidates"][0]
        assert cand["target_fingerprint"] == _REMOTE_TARGET
        assert cand["purpose"] == _SCENARIO_ID
        assert dispatch.snapshot.security_scope_version == 1

        result = await dispatch.executor.execute(_request(), dispatch.snapshot, _validator_ok)

        assert result.status == "succeeded"
        assert remote.invoke_count == 1
        assert local.invoke_count == 0
        assert result.candidate_decisions[0].decision == "succeeded"
        assert remote.last_attempt_id is not None

    @pytest.mark.asyncio
    async def test_unrelated_grant_changes_do_not_break_permit(self, service):
        """无关 grant（其他连接 / 其他用途）追加与撤销均不破坏既有许可。"""
        await _setup_config(service, [_REMOTE_CONN])
        await _grant(service, _REMOTE_CONN, _REMOTE_TARGET, _SCENARIO_ID)
        remote, _local = _OfflineAdapter(), _OfflineAdapter()
        _build_chain(service, remote_adapter=remote)

        dispatch = await scenario_dispatch.resolve_executor(_SCENARIO_ID)
        result = await dispatch.executor.execute(_request(), dispatch.snapshot, _validator_ok)
        assert result.status == "succeeded"
        assert remote.invoke_count == 1

        # 追加无关 grant（本地连接 + 其他用途；grant 引用的连接必须真实存在，
        # 见 ModelManagementConfig._validate_references）→ 旧许可不失效
        await _grant(service, _LOCAL_CONN, "https://other.example.com:443", "stock_analysis")
        assert len(_grants_on_disk()) == 2
        dispatch = await scenario_dispatch.resolve_executor(_SCENARIO_ID)
        result = await dispatch.executor.execute(_request(), dispatch.snapshot, _validator_ok)
        assert result.status == "succeeded"
        assert remote.invoke_count == 2

        # 撤销无关 grant → 旧许可仍不失效
        unrelated_id = _grants_on_disk()[1]["grant_id"]
        revoke = await service.revoke_egress(
            grant_id=unrelated_id,
            expected_revision=cfg_mod.ConfigHandler.read_model_management_fresh()["model_management"][
                "config_revision"
            ],
            operation_id="op-revoke-unrelated",
        )
        assert revoke.status == "committed"
        dispatch = await scenario_dispatch.resolve_executor(_SCENARIO_ID)
        result = await dispatch.executor.execute(_request(), dispatch.snapshot, _validator_ok)
        assert result.status == "succeeded"
        assert remote.invoke_count == 3

    @pytest.mark.asyncio
    async def test_wrong_target_zero_invocations(self, service):
        """换目标（grant 指向未确认地址）→ 远程适配器零调用。"""
        await _setup_config(service, [_REMOTE_CONN])
        await _grant(service, _REMOTE_CONN, "https://evil.example.com:443", _SCENARIO_ID)
        remote, _local = _OfflineAdapter(), _OfflineAdapter()
        _build_chain(service, remote_adapter=remote)

        dispatch = await scenario_dispatch.resolve_executor(_SCENARIO_ID)
        result = await dispatch.executor.execute(_request(), dispatch.snapshot, _validator_ok)

        assert result.status == "failed"
        assert remote.invoke_count == 0
        assert result.candidate_decisions[0].decision == "rejected"
        assert "authorize failed: PermissionError" in result.candidate_decisions[0].reason

    @pytest.mark.asyncio
    async def test_wrong_purpose_zero_invocations(self, service):
        """换用途（grant 授予其他场景）→ 远程适配器零调用。"""
        await _setup_config(service, [_REMOTE_CONN])
        await _grant(service, _REMOTE_CONN, _REMOTE_TARGET, "stock_analysis")
        remote, _local = _OfflineAdapter(), _OfflineAdapter()
        _build_chain(service, remote_adapter=remote)

        dispatch = await scenario_dispatch.resolve_executor(_SCENARIO_ID)
        result = await dispatch.executor.execute(_request(), dispatch.snapshot, _validator_ok)

        assert result.status == "failed"
        assert remote.invoke_count == 0
        assert result.candidate_decisions[0].decision == "rejected"

    @pytest.mark.asyncio
    async def test_scope_rotation_invalidates_old_grants(self, service, isolated_config):
        """P11 全链：范围轮转（scope_version 1→2）使旧世代 grant 全部失配 → 零调用。"""
        await _setup_config(service, [_REMOTE_CONN])
        await _grant(service, _REMOTE_CONN, _REMOTE_TARGET, _SCENARIO_ID)
        remote, _local = _OfflineAdapter(), _OfflineAdapter()
        _build_chain(service, remote_adapter=remote)

        _bump_scope_version_on_disk(isolated_config, 2)

        dispatch = await scenario_dispatch.resolve_executor(_SCENARIO_ID)
        assert dispatch.snapshot.security_scope_version == 2  # 分派冻结轮转后范围版本
        result = await dispatch.executor.execute(_request(), dispatch.snapshot, _validator_ok)

        assert result.status == "failed"
        assert remote.invoke_count == 0
        assert result.candidate_decisions[0].decision == "rejected"

    @pytest.mark.asyncio
    async def test_revoked_grant_zero_invocations(self, service):
        """grant 撤销（revoked_at 置位）→ 远程适配器零调用。"""
        await _setup_config(service, [_REMOTE_CONN])
        await _grant(service, _REMOTE_CONN, _REMOTE_TARGET, _SCENARIO_ID)
        grant_id = _grants_on_disk()[0]["grant_id"]
        remote, _local = _OfflineAdapter(), _OfflineAdapter()
        _build_chain(service, remote_adapter=remote)

        revoke = await service.revoke_egress(
            grant_id=grant_id,
            expected_revision=cfg_mod.ConfigHandler.read_model_management_fresh()["model_management"][
                "config_revision"
            ],
            operation_id="op-revoke-matching",
        )
        assert revoke.status == "committed"

        dispatch = await scenario_dispatch.resolve_executor(_SCENARIO_ID)
        result = await dispatch.executor.execute(_request(), dispatch.snapshot, _validator_ok)

        assert result.status == "failed"
        assert remote.invoke_count == 0


class TestLocalChainAuthorization:
    @pytest.mark.asyncio
    async def test_local_connection_executes_without_any_grant(self, service):
        """本地 gguf 无任何 grant 可执行（免 egress grant），不产生外发授权要求。"""
        await _setup_config(service, [_LOCAL_CONN])
        _remote, local = _OfflineAdapter(), _OfflineAdapter()
        _build_chain(service, local_adapter=local)

        dispatch = await scenario_dispatch.resolve_executor(_SCENARIO_ID)
        result = await dispatch.executor.execute(_request(), dispatch.snapshot, _validator_ok)

        assert result.status == "succeeded"
        assert local.invoke_count == 1
        assert _grants_on_disk() == []  # 全程零 grant

    @pytest.mark.asyncio
    async def test_local_connection_revoked_credential_zero_invocations(self, service):
        """本地路径安全检查保留：凭据版本撤销 → 零调用（免 grant 不免撤销校验）。"""
        fresh = cfg_mod.ConfigHandler.read_model_management_fresh()
        mm = dict(fresh["model_management"])
        expected = mm["config_revision"]
        mm.pop("migration_state", None)
        mm.pop("migration_transition", None)
        mm["connections"] = [
            {
                "connection_id": _LOCAL_CONN,
                "name": "本地 GGUF",
                "adapter_kind": "gguf_chat",
                "provider": "local",
                "model": "qwen2-0.5b-instruct",
                "api_base": None,
                "adapter_options": {"n_ctx": 2048},
                "credential_version": "cred-v1",
                "billing": {"mode": "usage_only", "pricing_unit": "llm_tokens"},
            },
        ]
        route = next(r for r in mm["scenario_routes"] if r["scenario_id"] == _SCENARIO_ID)
        route["enabled"] = True
        route["candidates"] = [_LOCAL_CONN]
        outcome = await service.save_draft(mm, expected, "op-f01-local-cred")
        assert outcome.status == "committed"
        await _advance_to_active(service)

        revoke = await service.revoke_credential(
            credential_version="cred-v1",
            expected_revision=cfg_mod.ConfigHandler.read_model_management_fresh()["model_management"][
                "config_revision"
            ],
            operation_id="op-revoke-cred",
        )
        assert revoke.status == "committed"

        _remote, local = _OfflineAdapter(), _OfflineAdapter()
        _build_chain(service, local_adapter=local)

        dispatch = await scenario_dispatch.resolve_executor(_SCENARIO_ID)
        result = await dispatch.executor.execute(_request(), dispatch.snapshot, _validator_ok)

        assert result.status == "failed"
        assert local.invoke_count == 0
        assert result.candidate_decisions[0].decision == "rejected"

    @pytest.mark.asyncio
    async def test_remote_rejected_falls_back_to_local(self, service):
        """远程候选未授权被拒后切换本地候选：远程零出站、本地恰一次（候选遍历语义）。"""
        await _setup_config(service, [_REMOTE_CONN, _LOCAL_CONN])
        await _grant(service, _REMOTE_CONN, "https://evil.example.com:443", _SCENARIO_ID)
        remote, local = _OfflineAdapter(), _OfflineAdapter()
        _build_chain(service, remote_adapter=remote, local_adapter=local)

        dispatch = await scenario_dispatch.resolve_executor(_SCENARIO_ID)
        result = await dispatch.executor.execute(_request(), dispatch.snapshot, _validator_ok)

        assert result.status == "succeeded"
        assert remote.invoke_count == 0  # 未授权远程零出站
        assert local.invoke_count == 1
        decisions = [d.decision for d in result.candidate_decisions]
        assert decisions == ["rejected", "succeeded"]
