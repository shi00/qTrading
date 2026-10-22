"""ModelConnectionsViewModel 单元测试（S5 双入口「模型连接」页 ViewModel）。

Mock ``ModelConfigurationService``（DI 注入），不连真实 DB / keyring / 网络。
覆盖（R19）：
- state 冻结 + 容器 tuple + 默认值
- load() 映射快照为行 / 摘要（可执行场景数 / 需处理配置数 / 费用三独立字段）
- 搜索 / 状态过滤 / 三步骤创建（前后切换保留输入）/ 编辑展开相同字段分组
- save() 三态（committed / not_committed / commit_unknown）
- resolve_commit_unknown() 读盘恢复
- revision 冲突保留草稿
- 凭据留空 ≠ 显式清除（不回显已存凭据）
- adapter_kind 条件字段（litellm vs gguf）
- test() 单次测试（begin_synthetic_test + 注入 ScenarioExecutor）
- dispose() 清敏感草稿
- R2 CancelledError 显式传播
"""

from dataclasses import FrozenInstanceError
from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.model_management_types import (
    ConfigurationSaveOutcome,
    ModelConfigurationConflictError,
    ModelManagementSnapshot,
    ScenarioExecutionResult,
    SyntheticTestConsent,
    SyntheticTestContext,
)
from ui.viewmodels.model_connections_view_model import (
    ModelConnectionsState,
    ModelConnectionsViewModel,
    UsageCostSummary,
)

pytestmark = pytest.mark.unit


# --- Builders ---


def _snapshot(
    revision: int = 1,
    connections: tuple[dict, ...] = (),
    scenario_routes: tuple[dict, ...] = (),
    security_grants: tuple[dict, ...] = (),
) -> ModelManagementSnapshot:
    return ModelManagementSnapshot(
        snapshot_id="snap-1",
        config_revision=revision,
        migration_state="active",
        connections=tuple(MappingProxyType(c) for c in connections),
        scenario_routes=tuple(MappingProxyType(r) for r in scenario_routes),
        usage_policy=MappingProxyType({}),
        security_revision=0,
        security_grants=tuple(MappingProxyType(g) for g in security_grants),
        created_at="2026-10-19T00:00:00+00:00",
    )


def _conn(
    connection_id: str = "c1",
    name: str = "Conn 1",
    model: str = "gpt-4o",
    adapter_kind: str = "litellm_chat",
    enabled: bool = True,
    provider: str = "openai",
    api_base: str = "https://api.openai.com/v1",
    credential_ref: str | None = "keyring:old",
    credential_version: str | None = "v-old",
    capabilities: dict | None = None,
) -> dict:
    return {
        "connection_id": connection_id,
        "name": name,
        "enabled": enabled,
        "adapter_kind": adapter_kind,
        "provider": provider,
        "model": model,
        "api_base": api_base,
        "credential_ref": credential_ref,
        "credential_version": credential_version,
        "adapter_options": {},
        "capabilities": capabilities or {},
        "context_window": 8192,
        "concurrency_limit": 2,
        "billing": {"mode": "usage_only", "pricing_unit": "llm_tokens"},
        "connection_revision": 1,
    }


def _route(scenario_id: str = "stock_analysis", enabled: bool = True, candidates: tuple[str, ...] = ("c1",)) -> dict:
    return {"scenario_id": scenario_id, "enabled": enabled, "candidates": list(candidates)}


def _make_service(snapshot: ModelManagementSnapshot | None = None) -> MagicMock:
    svc = MagicMock()
    svc.get_snapshot = AsyncMock(return_value=snapshot or _snapshot())
    svc.save_draft = AsyncMock(
        return_value=ConfigurationSaveOutcome(status="committed", current_revision=2, committed_revision=2)
    )
    svc.resolve_save_outcome = AsyncMock(return_value=ConfigurationSaveOutcome(status="committed", current_revision=2))
    svc.prepare_credential_version = AsyncMock(return_value=("keyring:new", "v-new"))
    svc.begin_synthetic_test = AsyncMock(
        return_value=SyntheticTestContext(
            test_operation_id="t1",
            invocation_id="i1",
            host_identity="host",
            draft_connection_id="c1",
            draft_connection_revision=1,
            draft_fingerprint="fp",
            task_id="model_connection_test",
            credential_version=None,
            config_revision=1,
        )
    )
    svc.create_synthetic_test_consent = MagicMock(
        return_value=SyntheticTestConsent(
            test_operation_id="t1",
            host_identity="host",
            target_fingerprint="tf",
            draft_fingerprint="fp",
            scope_version=1,
            deadline=999.0,
        )
    )
    svc.grant_egress = AsyncMock(return_value=ConfigurationSaveOutcome(status="committed", current_revision=2))
    svc.revoke_egress = AsyncMock(return_value=ConfigurationSaveOutcome(status="committed", current_revision=2))
    return svc


def _make_vm(svc: MagicMock, **kwargs) -> ModelConnectionsViewModel:
    return ModelConnectionsViewModel(svc, **kwargs)


@pytest.fixture
def filled_litellm_draft(vm: ModelConnectionsViewModel) -> ModelConnectionsViewModel:
    vm.start_add()
    vm.update_name("New Conn")
    vm.update_model("gpt-4o")
    vm.update_provider("openai")
    vm.update_api_base("https://api.example.com/v1")
    return vm


@pytest.fixture
def vm() -> ModelConnectionsViewModel:
    return _make_vm(_make_service())


# --- State ---


class TestState:
    def test_initial_state_defaults(self, vm):
        st = vm.state
        assert st.connections == ()
        assert st.search_query == ""
        assert st.status_filter == "all"
        assert st.selected_connection_id is None
        assert st.editor_open is False
        assert st.editor_step == 1
        assert st.validation_errors == ()
        assert st.save_state == "idle"
        assert st.test_state == "idle"
        assert st.commit_unknown_pending is False
        assert st.dirty is False
        assert st.message is None

    def test_state_is_frozen_and_containers_are_tuple(self, vm):
        assert isinstance(vm.state, ModelConnectionsState)
        with pytest.raises(FrozenInstanceError) as exc_info:
            vm.state.search_query = "x"  # type: ignore[misc]
        assert "search_query" in str(exc_info.value)

    def test_summary_cost_fields_default_to_none(self, vm):
        assert vm.state.summary.known_cost_cny is None
        assert vm.state.summary.unknown_cost_count is None
        assert vm.state.summary.unpriced_count is None


# --- Load ---


class TestLoad:
    async def test_load_maps_connections_and_summary(self):
        svc = _make_service(
            _snapshot(
                revision=7,
                connections=(_conn(connection_id="c1"), _conn(connection_id="c2", name="Conn 2")),
                scenario_routes=(_route(), _route(scenario_id="news_risk", enabled=False, candidates=())),
            )
        )
        vm = _make_vm(svc)
        await vm.load()

        st = vm.state
        assert st.config_revision == 7
        assert len(st.connections) == 2
        assert st.connections[0].connection_id == "c1"
        assert st.connections[0].name == "Conn 1"
        assert st.connections[0].adapter_kind == "litellm_chat"
        assert st.connections[0].credential_ref == "keyring:old"
        assert st.connections[0].scenario_ref_count == 1
        assert st.summary.executable_scenarios == 1

    async def test_load_uses_injected_usage_loader(self):
        loader = AsyncMock(
            return_value=UsageCostSummary(known_cost_cny="12.50", unknown_cost_count=3, unpriced_count=2)
        )
        vm = _make_vm(
            _make_service(_snapshot(connections=(_conn(),), scenario_routes=(_route(),))), usage_summary_loader=loader
        )
        await vm.load()

        st = vm.state
        assert st.summary.known_cost_cny == "12.50"
        assert st.summary.unknown_cost_count == 3
        assert st.summary.unpriced_count == 2

    async def test_visible_connections_respects_search_and_filter(self):
        svc = _make_service(
            _snapshot(
                connections=(
                    _conn(connection_id="c1", name="Alpha"),
                    _conn(connection_id="c2", name="Beta", enabled=False),
                )
            )
        )
        vm = _make_vm(svc)
        await vm.load()

        assert len(vm.visible_connections()) == 2
        vm.set_search("alp")
        assert tuple(r.connection_id for r in vm.visible_connections()) == ("c1",)
        vm.set_search("")
        vm.set_status_filter("disabled")
        assert tuple(r.connection_id for r in vm.visible_connections()) == ("c2",)


# --- Editor / steps ---


class TestEditor:
    def test_create_three_steps_preserve_input(self, vm):
        vm.start_add()
        assert vm.state.editor_open is True
        assert vm.state.editor_step == 1

        vm.update_name("Draft")
        vm.set_editor_step(2)
        vm.update_billing_mode("estimate_cost")
        vm.set_editor_step(3)
        vm.set_editor_step(1)

        assert vm.state.editor_step == 1
        assert vm.state.draft.name == "Draft"
        assert vm.state.draft.billing_mode == "estimate_cost"
        assert vm.state.dirty is True

    def test_set_editor_step_clamps(self, vm):
        vm.start_add()
        vm.set_editor_step(9)
        assert vm.state.editor_step == 3
        vm.set_editor_step(0)
        assert vm.state.editor_step == 1

    async def test_edit_expands_same_field_group(self):
        svc = _make_service(_snapshot(connections=(_conn(connection_id="c1", name="Existing"),)))
        vm = _make_vm(svc)
        await vm.load()
        vm.edit("c1")

        st = vm.state
        assert st.editor_open is True
        assert st.editor_step == 1
        assert st.selected_connection_id == "c1"
        assert st.draft.name == "Existing"
        assert st.draft.model == "gpt-4o"
        assert st.draft.credential_version == "v-old"
        # 凭据不回显
        assert st.draft.credential_api_key == ""


# --- Save three states ---


class TestSave:
    async def test_save_committed_enters_detail(self, vm, filled_litellm_draft):
        await vm.load()
        ok = await vm.save()

        assert ok is True
        st = vm.state
        assert st.save_state == "committed"
        assert st.editor_open is False
        assert st.dirty is False
        assert st.commit_unknown_pending is False

        payload, expected_revision, _operation_id = vm._service.save_draft.await_args.args  # noqa: SLF001
        assert expected_revision == 1
        # save_draft 期望完整 model_management 域，连接草稿合并在 connections 列表内
        assert payload["schema_version"] == 1
        new_conn = next(c for c in payload["connections"] if c["name"] == "New Conn")
        assert new_conn["connection_id"] == st.selected_connection_id
        assert st.message is not None

    async def test_save_payload_excludes_migration_subdomain(self, vm, filled_litellm_draft):
        """F07：迁移子域由 begin/seal/activate 专用命令独占，连接草稿 payload 不携带。"""
        await vm.load()
        await vm.save()

        payload, _revision, _op = vm._service.save_draft.await_args.args  # noqa: SLF001
        assert "migration_state" not in payload
        assert "migration_transition" not in payload

    async def test_save_appends_new_connection_and_preserves_other_domains(self):
        svc = _make_service(
            _snapshot(
                revision=5,
                connections=(_conn(connection_id="c1", name="Existing"),),
                scenario_routes=(_route(candidates=("c1",)),),
            )
        )
        vm = _make_vm(svc)
        await vm.load()
        vm.start_add()
        vm.update_name("Added")
        vm.update_model("gpt-4o")
        await vm.save()

        payload, _revision, _op = svc.save_draft.await_args.args  # noqa: SLF001
        ids = [c["connection_id"] for c in payload["connections"]]
        # 既有连接保留 + 新连接追加，不重复
        assert ids[0] == "c1"
        assert len(ids) == 2
        assert len(set(ids)) == 2
        # 其它已提交域原样带入
        assert payload["scenario_routes"][0]["candidates"] == ["c1"]
        assert payload["config_revision"] == 5

    async def test_save_edit_replaces_existing_connection_in_place(self):
        svc = _make_service(
            _snapshot(connections=(_conn(connection_id="c1", name="Existing"), _conn(connection_id="c2", name="Other")))
        )
        vm = _make_vm(svc)
        await vm.load()
        vm.edit("c1")
        vm.update_name("Renamed")
        await vm.save()

        payload, _revision, _op = svc.save_draft.await_args.args  # noqa: SLF001
        ids = [c["connection_id"] for c in payload["connections"]]
        assert ids == ["c1", "c2"]
        renamed = next(c for c in payload["connections"] if c["connection_id"] == "c1")
        assert renamed["name"] == "Renamed"
        # 编辑保留磁盘能力证据等未在草稿内的字段（合并而非替换整条）
        assert "capabilities" in renamed

    async def test_save_not_committed_keeps_draft(self, vm, filled_litellm_draft):
        vm._service.save_draft.return_value = ConfigurationSaveOutcome(status="not_committed", current_revision=1)  # noqa: SLF001
        ok = await vm.save()

        assert ok is False
        st = vm.state
        assert st.save_state == "not_committed"
        assert st.editor_open is True
        assert st.draft.name == "New Conn"

    async def test_save_commit_unknown_flags_pending(self, vm, filled_litellm_draft):
        vm._service.save_draft.return_value = ConfigurationSaveOutcome(status="commit_unknown")  # noqa: SLF001
        ok = await vm.save()

        assert ok is False
        st = vm.state
        assert st.save_state == "commit_unknown"
        assert st.commit_unknown_pending is True
        assert st.draft.name == "New Conn"

    async def test_revision_conflict_keeps_draft_without_overwrite(self, vm, filled_litellm_draft):
        vm._service.save_draft.side_effect = ModelConfigurationConflictError(1, 2)  # noqa: SLF001
        ok = await vm.save()

        assert ok is False
        st = vm.state
        assert st.save_state == "conflict"
        assert st.draft.name == "New Conn"
        assert st.editor_open is True

    async def test_save_cancelled_error_propagates(self, vm, filled_litellm_draft):
        import asyncio

        vm._service.save_draft.side_effect = asyncio.CancelledError  # noqa: SLF001
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await vm.save()
        assert exc_info.type is asyncio.CancelledError
        vm._service.save_draft.assert_awaited_once()  # noqa: SLF001


class TestResolveCommitUnknown:
    async def test_resolve_recovers_to_committed(self, vm, filled_litellm_draft):
        vm._service.save_draft.return_value = ConfigurationSaveOutcome(status="commit_unknown")  # noqa: SLF001
        await vm.save()
        assert vm.state.commit_unknown_pending is True

        vm._service.resolve_save_outcome.return_value = ConfigurationSaveOutcome(  # noqa: SLF001
            status="committed", current_revision=3
        )
        ok = await vm.resolve_commit_unknown()

        assert ok is True
        st = vm.state
        assert st.commit_unknown_pending is False
        assert st.save_state == "committed"
        assert st.dirty is False
        vm._service.resolve_save_outcome.assert_awaited_once()  # noqa: SLF001

    async def test_resolve_still_unknown_keeps_pending(self, vm, filled_litellm_draft):
        vm._service.save_draft.return_value = ConfigurationSaveOutcome(status="commit_unknown")  # noqa: SLF001
        await vm.save()

        vm._service.resolve_save_outcome.return_value = ConfigurationSaveOutcome(status="commit_unknown")  # noqa: SLF001
        ok = await vm.resolve_commit_unknown()

        assert ok is False
        assert vm.state.commit_unknown_pending is True

    async def test_resolve_without_pending_is_noop(self, vm):
        ok = await vm.resolve_commit_unknown()
        assert ok is False
        vm._service.resolve_save_outcome.assert_not_awaited()  # noqa: SLF001


# --- Credential semantics ---


class TestCredential:
    async def test_blank_credential_keeps_existing(self):
        svc = _make_service(_snapshot(connections=(_conn(connection_id="c1"),)))
        vm = _make_vm(svc)
        await vm.load()
        vm.edit("c1")
        assert vm.state.draft.credential_api_key == ""

        await vm.save()

        conn = svc.save_draft.await_args.args[0]["connections"][0]
        assert conn["credential_ref"] == "keyring:old"
        assert conn["credential_version"] == "v-old"
        svc.prepare_credential_version.assert_not_awaited()

    async def test_new_credential_prepares_version(self):
        svc = _make_service(_snapshot(connections=(_conn(connection_id="c1"),)))
        vm = _make_vm(svc)
        await vm.load()
        vm.edit("c1")
        vm.update_credential("sk-new")

        await vm.save()

        svc.prepare_credential_version.assert_awaited_once()
        conn = svc.save_draft.await_args.args[0]["connections"][0]
        assert conn["credential_ref"] == "keyring:new"
        assert conn["credential_version"] == "v-new"

    async def test_explicit_clear_is_distinct_from_blank(self):
        svc = _make_service(_snapshot(connections=(_conn(connection_id="c1"),)))
        vm = _make_vm(svc)
        await vm.load()
        vm.edit("c1")
        vm.update_credential_cleared(True)

        await vm.save()

        conn = svc.save_draft.await_args.args[0]["connections"][0]
        assert conn["credential_ref"] is None
        assert conn["credential_version"] is None
        svc.prepare_credential_version.assert_not_awaited()


# --- Adapter kind conditional fields ---


class TestAdapterKindFields:
    async def test_litellm_payload_has_api_fields_and_not_gguf(self):
        svc = _make_service()
        vm = _make_vm(svc)
        await vm.load()
        vm.start_add()
        vm.update_name("Lite")
        vm.update_adapter_kind("litellm_chat")
        vm.update_model("gpt-4o")
        vm.update_provider("openai")
        vm.update_api_base("https://api.example.com/v1")

        await vm.save()

        conn = svc.save_draft.await_args.args[0]["connections"][0]
        assert conn["adapter_kind"] == "litellm_chat"
        assert conn["provider"] == "openai"
        assert conn["api_base"] == "https://api.example.com/v1"
        assert "encoder_model_path" not in conn["adapter_options"]
        assert "dialog_model_path" not in conn["adapter_options"]

    async def test_gguf_sentiment_payload_has_three_paths(self):
        svc = _make_service()
        vm = _make_vm(svc)
        await vm.load()
        vm.start_add()
        vm.update_name("Sentiment")
        vm.update_adapter_kind("gguf_sentiment")
        vm.update_encoder_model_path("enc.gguf")
        vm.update_head_path("head.npz")
        vm.update_tokenizer_path("tok.json")

        await vm.save()

        conn = svc.save_draft.await_args.args[0]["connections"][0]
        assert conn["adapter_kind"] == "gguf_sentiment"
        assert conn["provider"] == ""
        assert conn["api_base"] is None
        assert conn["credential_ref"] is None
        assert conn["adapter_options"]["encoder_model_path"] == "enc.gguf"
        assert conn["adapter_options"]["head_path"] == "head.npz"
        assert conn["adapter_options"]["tokenizer_path"] == "tok.json"

    def test_validation_rejects_missing_required_field(self, vm):
        vm.start_add()
        vm.update_adapter_kind("gguf_sentiment")
        # 未填 encoder / head / tokenizer
        errors = vm.validate_draft()
        assert errors  # 非空

    async def test_validation_failure_blocks_save(self, vm):
        vm.start_add()
        ok = await vm.save()
        assert ok is False
        assert vm.state.validation_errors
        vm._service.save_draft.assert_not_awaited()  # noqa: SLF001


# --- Test (single synthetic test) ---


class TestSyntheticTest:
    async def test_runs_begin_and_executor(self):
        svc = _make_service()
        executor = MagicMock()
        executor.execute = AsyncMock(
            return_value=ScenarioExecutionResult(
                scenario_id="synthetic_test",
                invocation_id="i1",
                status="succeeded",
                outputs=(),
                candidate_decisions=(),
                actual_usage=(),
            )
        )
        vm = _make_vm(svc, scenario_executor=executor)
        vm.start_add()
        vm.update_name("Probe")
        vm.update_model("gpt-4o")
        vm.update_provider("openai")

        ok = await vm.test()

        assert ok is True
        assert vm.state.test_state == "success"
        svc.begin_synthetic_test.assert_awaited_once()
        consent_call = svc.create_synthetic_test_consent.call_args
        assert consent_call.args[0] is svc.begin_synthetic_test.return_value
        assert set(consent_call.kwargs) == {"target_fingerprint", "deadline"}
        executor.execute.assert_awaited_once()

    async def test_without_executor_degrades(self, vm):
        vm.start_add()
        vm.update_name("Probe")
        vm.update_model("gpt-4o")
        ok = await vm.test()
        assert ok is False
        assert vm.state.test_state == "failed"


# --- Egress commands ---


class TestEgress:
    async def test_grant_egress_forwards_and_refreshes(self):
        svc = _make_service()
        vm = _make_vm(svc)
        await vm.load()
        ok = await vm.grant_egress("c1", "target-fp", "stock_analysis")
        assert ok is True
        svc.grant_egress.assert_awaited_once()

    async def test_revoke_egress_forwards(self):
        svc = _make_service()
        vm = _make_vm(svc)
        await vm.load()
        ok = await vm.revoke_egress("g1")
        assert ok is True
        svc.revoke_egress.assert_awaited_once()


# --- Egress queries（§4.3 授权摘要查询） ---


def _grant(
    grant_id: str = "g1",
    connection_id: str = "c1",
    purpose: str = "stock_analysis",
    target_fingerprint: str = "https://api.openai.com:443/v1",
    confirmed_at: str = "2026-10-19T00:00:00+00:00",
    revoked_at: str | None = None,
) -> dict:
    return {
        "grant_id": grant_id,
        "connection_id": connection_id,
        "target_fingerprint": target_fingerprint,
        "purpose": purpose,
        "confirmed_at": confirmed_at,
        "revoked_at": revoked_at,
    }


class TestEgressQueries:
    async def test_connection_target_normalizes_remote(self):
        svc = _make_service(_snapshot(connections=(_conn(connection_id="c1"),)))
        vm = _make_vm(svc)
        await vm.load()
        assert vm.connection_target("c1") == "https://api.openai.com:443/v1"

    async def test_connection_target_ipv6_literal_is_bracketed(self):
        """T1：VM 统一 import core 正本后，IPv6 字面量带方括号且跳过 IDNA。"""
        svc = _make_service(_snapshot(connections=(_conn(connection_id="c1", api_base="http://[::1]:8080/v1"),)))
        vm = _make_vm(svc)
        await vm.load()
        assert vm.connection_target("c1") == "http://[::1]:8080/v1"

    async def test_connection_target_local_adapter_is_none(self):
        svc = _make_service(
            _snapshot(connections=(_conn(connection_id="c1", adapter_kind="gguf_sentiment", api_base=None),))
        )
        vm = _make_vm(svc)
        await vm.load()
        assert vm.connection_target("c1") is None

    async def test_connection_target_unknown_id_is_none(self, vm):
        await vm.load()
        assert vm.connection_target("missing") is None

    async def test_grants_for_filters_purpose_and_revoked(self):
        svc = _make_service(
            _snapshot(
                connections=(_conn(connection_id="c1"), _conn(connection_id="c2")),
                security_grants=(
                    _grant(grant_id="g1", connection_id="c1", purpose="stock_analysis"),
                    _grant(grant_id="g2", connection_id="c1", purpose="news_risk"),
                    _grant(grant_id="g3", connection_id="c2", purpose="stock_analysis"),
                    _grant(
                        grant_id="g4",
                        connection_id="c1",
                        purpose="stock_analysis",
                        revoked_at="2026-10-19T01:00:00+00:00",
                    ),
                ),
            )
        )
        vm = _make_vm(svc)
        await vm.load()

        active = vm.grants_for("c1", "stock_analysis")
        assert tuple(g["grant_id"] for g in active) == ("g1",)
        assert vm.grants_for("c2", "news_risk") == ()
        assert tuple(g["grant_id"] for g in vm.grants_for("c1", "news_risk")) == ("g2",)


# --- Draft entry / merge（§11.6 向导原子提交公开入口） ---


class TestDraftConnectionEntry:
    async def test_filled_draft_yields_entry_with_monotonic_revision(self):
        svc = _make_service()
        vm = _make_vm(svc)
        await vm.load()
        vm.start_add()
        vm.update_name("New Conn")
        vm.update_model("gpt-4o")
        vm.update_provider("openai")
        vm.update_api_base("https://api.example.com/v1")
        vm.update_credential("sk-new")

        entry = await vm.draft_connection_entry("op-1")

        assert entry is not None
        assert entry["name"] == "New Conn"
        assert entry["model"] == "gpt-4o"
        assert entry["connection_revision"] == 1
        assert entry["credential_ref"] == "keyring:new"
        assert entry["credential_version"] == "v-new"
        svc.prepare_credential_version.assert_awaited_once_with("op-1", "sk-new")

    async def test_existing_connection_revision_increments(self):
        svc = _make_service(_snapshot(connections=(_conn(connection_id="c1"),)))
        vm = _make_vm(svc)
        await vm.load()
        vm.edit("c1")
        vm.update_model("gpt-4o-mini")  # 执行指纹变化 → revision +1

        entry = await vm.draft_connection_entry("op-2")

        assert entry is not None
        assert entry["connection_id"] == "c1"
        assert entry["connection_revision"] == 2

    async def test_empty_name_yields_none(self, vm):
        await vm.load()
        vm.start_add()
        assert await vm.draft_connection_entry("op-1") is None

    async def test_draft_connection_id_stable_across_repeated_commits(self):
        """草稿连接标识在草稿生命周期内稳定：向导返回本步再次保存不追加重复连接。

        回归：向导「保存并继续」成功后仅刷新已提交基线、不清草稿；若草稿标识每次
        提交都新生成，再次保存会把同一连接追加成第二份。故草稿标识必须稳定，
        同一草稿经 ``merged_connections`` 覆盖而非追加。
        """
        svc = _make_service()
        vm = _make_vm(svc)
        await vm.load()
        vm.start_add()
        vm.update_name("New Conn")
        vm.update_model("gpt-4o")

        first = await vm.draft_connection_entry("op-1")
        second = await vm.draft_connection_entry("op-1")

        assert first is not None and second is not None
        assert first["connection_id"]  # 非空稳定标识
        assert first["connection_id"] == second["connection_id"]
        # 模拟首次提交后已提交基线含该连接：再次保存应覆盖原行，不产生重复
        vm._raw_connections = (dict(first),)
        merged = vm.merged_connections(second)
        assert [c["connection_id"] for c in merged] == [first["connection_id"]]


class TestMergedConnections:
    async def test_entry_none_returns_copies_of_committed(self):
        svc = _make_service(_snapshot(connections=(_conn(connection_id="c1", name="Existing"),)))
        vm = _make_vm(svc)
        await vm.load()

        merged = vm.merged_connections(None)
        assert [c["connection_id"] for c in merged] == ["c1"]
        merged[0]["name"] = "Mutated"
        assert vm.merged_connections(None)[0]["name"] == "Existing"

    async def test_same_id_entry_overwrites_in_place(self):
        svc = _make_service(
            _snapshot(connections=(_conn(connection_id="c1", name="Old"), _conn(connection_id="c2", name="Other")))
        )
        vm = _make_vm(svc)
        await vm.load()

        merged = vm.merged_connections({"connection_id": "c1", "name": "Renamed"})
        assert [c["connection_id"] for c in merged] == ["c1", "c2"]
        assert merged[0] == {"connection_id": "c1", "name": "Renamed"}

    async def test_new_id_entry_appended(self):
        svc = _make_service(_snapshot(connections=(_conn(connection_id="c1", name="Old"),)))
        vm = _make_vm(svc)
        await vm.load()

        merged = vm.merged_connections({"connection_id": "c-new", "name": "Fresh"})
        assert [c["connection_id"] for c in merged] == ["c1", "c-new"]
        assert merged[-1] == {"connection_id": "c-new", "name": "Fresh"}


# --- Dispose ---


class TestDispose:
    def test_dispose_clears_sensitive_draft(self, vm):
        vm.start_add()
        vm.update_name("Secret Conn")
        vm.update_credential("sk-secret")

        vm.dispose()

        st = vm.state
        assert st.draft.credential_api_key == ""
        assert st.editor_open is False
        assert st.selected_connection_id is None
        assert vm.disposed is True

    def test_dispose_resets_test_state(self, vm):
        """F40：单次测试挂起中被 dispose 后，test_state 不残留 TEST_RUNNING。"""
        vm.start_add()
        vm._set_state(test_state="running")

        vm.dispose()

        assert vm.state.test_state == "idle"


# --- F40：dispose 管理在途任务（cancellable 取消 / committing 放行） ---


class TestDisposeInflightTasks:
    """F40 回归：页面卸载（dispose）取消在途测试/加载任务；提交中的保存放行。"""

    @pytest.mark.asyncio
    async def test_dispose_cancels_inflight_test(self):
        """单次测试挂起在 begin_synthetic_test 时页面卸载 → 任务被取消（不再继续烧凭据）。"""
        import asyncio

        # test() 前置要求 scenario_executor 非 None（否则提前返回 TEST_FAILED），
        # 故直接构造带 executor 的 VM（默认 vm fixture 不注入 executor）。
        vm = _make_vm(_make_service(), scenario_executor=MagicMock())
        vm.start_add()
        vm.update_name("New Conn")
        vm.update_model("gpt-4o")
        vm.update_provider("openai")
        vm.update_api_base("https://api.example.com/v1")
        svc = vm._service
        gate = asyncio.Event()
        release = asyncio.Event()

        async def hang_on_gate(*args, **kwargs):
            gate.set()
            await release.wait()
            raise AssertionError("cancelled test must not reach executor result")

        svc.begin_synthetic_test = AsyncMock(side_effect=hang_on_gate)

        task = asyncio.create_task(vm.test())
        await gate.wait()  # 测试已挂起（登记完成）
        vm.dispose()

        with pytest.raises(asyncio.CancelledError) as exc_info:
            await task
        assert isinstance(exc_info.value, asyncio.CancelledError)
        assert task.cancelled()
        svc.begin_synthetic_test.assert_awaited_once()  # 确认挂起发生在测试入口而非提前返回

    @pytest.mark.asyncio
    async def test_dispose_leaves_committing_save_running(self, filled_litellm_draft):
        """保存已进入提交（save_draft 挂起中）页面卸载 → 不取消，写流程自然完成落库。"""
        import asyncio

        vm = filled_litellm_draft
        svc = vm._service
        gate = asyncio.Event()
        release = asyncio.Event()

        async def hang_on_save(*args, **kwargs):
            gate.set()
            await release.wait()
            return ConfigurationSaveOutcome(status="committed", current_revision=2, committed_revision=2)

        svc.save_draft = AsyncMock(side_effect=hang_on_save)

        task = asyncio.create_task(vm.save())
        await gate.wait()  # save_draft 已挂起（committing 阶段）
        vm.dispose()

        release.set()
        assert await task is True  # 未被取消，提交完成
        assert not task.cancelled()
        svc.save_draft.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispose_cancels_inflight_save_before_commit(self, filled_litellm_draft):
        """保存仍在凭据准备阶段（未进入提交）页面卸载 → 任务被取消（安全窗口）。"""
        import asyncio

        vm = filled_litellm_draft
        # prepare_credential_version 仅在凭据非空时被调用（_resolve_credential 分支），
        # 必须显式填入凭据才能挂起在 cancellable 阶段。
        vm.update_credential("sk-new-key")
        svc = vm._service
        gate = asyncio.Event()
        release = asyncio.Event()

        async def hang_on_credential(*args, **kwargs):
            gate.set()
            await release.wait()
            return ("keyring:new", "v-new")

        svc.prepare_credential_version = AsyncMock(side_effect=hang_on_credential)

        task = asyncio.create_task(vm.save())
        await gate.wait()  # 凭据准备挂起（cancellable 阶段）
        vm.dispose()

        with pytest.raises(asyncio.CancelledError) as exc_info:
            await task
        assert isinstance(exc_info.value, asyncio.CancelledError)
        assert task.cancelled()
        svc.save_draft.assert_not_awaited()  # 从未进入提交阶段
