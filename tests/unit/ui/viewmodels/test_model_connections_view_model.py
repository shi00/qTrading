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
- test() 单次测试（专用测试上下文：一次性消费 consent + 直连适配器，F15）
- dispose() 清敏感草稿
- R2 CancelledError 显式传播
"""

import asyncio
from dataclasses import FrozenInstanceError
from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.model_management_types import (
    AdapterResponse,
    AttemptContext,
    ConfigurationSaveOutcome,
    InferenceRequest,
    ModelConfigurationConflictError,
    ModelManagementSnapshot,
    ResolvedConnection,
    SyntheticTestConsent,
    SyntheticTestContext,
)
from services.ai_service.model_compatibility import ModelCompatibilityService
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
    security_scope_version: int = 1,
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
        security_scope_version=security_scope_version,
        created_at="2026-10-19T00:00:00+00:00",
    )


def _conn(
    connection_id: str = "c1",
    name: str = "Conn 1",
    model: str = "gpt-4o",
    adapter_kind: str = "litellm_chat",
    enabled: bool = True,
    provider: str = "openai",
    api_base: str | None = "https://api.openai.com/v1",
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
    svc.consume_synthetic_test_consent = MagicMock(side_effect=lambda consent, **kwargs: consent)
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


class TestSaveCancellationIdentity:
    """F31：保存取消丢失恢复身份 / 旧回执覆盖新草稿 的回归。"""

    async def test_save_cancelled_before_write_restores_idle_and_allows_resave(self, vm, filled_litellm_draft):
        """写前取消：可证明未提交 → 恢复 idle 可再次保存，不留 SAVING 悬挂态。"""
        import asyncio

        # 录入新凭据使保存首个 await 落在凭据准备阶段（写前窗口）
        vm.update_credential("sk-test-123")
        vm._service.prepare_credential_version.side_effect = asyncio.CancelledError  # noqa: SLF001
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await vm.save()
        assert exc_info.type is asyncio.CancelledError

        st = vm.state
        assert st.save_state == "idle"
        assert st.commit_unknown_pending is False
        assert st.editor_open is True
        assert st.draft.name == "New Conn"
        assert st.dirty is True
        vm._service.save_draft.assert_not_awaited()  # noqa: SLF001
        assert vm._pending_operation_id is None  # noqa: SLF001

        # 恢复后可再次保存（不再被 SAVING guard 拒绝）
        vm._service.prepare_credential_version.side_effect = None  # noqa: SLF001
        assert await vm.save() is True
        assert vm.state.save_state == "committed"

    async def test_save_cancelled_after_write_registers_recoverable_receipt(self, vm, filled_litellm_draft):
        """写后取消：落盘线程不随等待取消而停止 → commit_unknown 可查回执并恢复。"""
        import asyncio

        vm._service.save_draft.side_effect = asyncio.CancelledError  # noqa: SLF001
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await vm.save()
        assert exc_info.type is asyncio.CancelledError

        st = vm.state
        assert st.save_state == "commit_unknown"
        assert st.commit_unknown_pending is True
        operation_id = vm._service.save_draft.await_args.args[2]  # noqa: SLF001
        assert vm._pending_operation_id == operation_id  # noqa: SLF001

        # 按 operation_id 查回执恢复（F11 语义），不臆造「未提交」
        vm._service.resolve_save_outcome.return_value = ConfigurationSaveOutcome(  # noqa: SLF001
            status="committed", current_revision=3
        )
        assert await vm.resolve_commit_unknown() is True
        st = vm.state
        assert st.save_state == "committed"
        assert st.commit_unknown_pending is False
        assert st.dirty is False

    async def test_stale_committed_receipt_does_not_close_new_draft(self, vm, filled_litellm_draft):
        """保存期间新增草稿：旧 committed 回执不清 dirty / 不关闭新编辑器。"""
        snapshot_calls = {"count": 0}

        async def reset_then_commit(payload, expected_revision, operation_id):
            vm.start_add()  # 延迟提交期间新建草稿（换代）
            snapshot_calls["count"] = vm._service.get_snapshot.await_count  # noqa: SLF001
            return ConfigurationSaveOutcome(status="committed", current_revision=2, committed_revision=2)

        vm._service.save_draft = AsyncMock(side_effect=reset_then_commit)  # noqa: SLF001
        ok = await vm.save()

        assert ok is False
        st = vm.state
        # 旧回执未作用于重置后的新草稿
        assert st.editor_open is True
        assert st.save_state == "idle"
        assert st.selected_connection_id is None
        assert st.draft.name == ""
        assert st.dirty is False
        assert st.commit_unknown_pending is False
        # 磁盘已提交：仍刷新快照维持行 / CAS 基线新鲜
        assert vm._service.get_snapshot.await_count > snapshot_calls["count"]  # noqa: SLF001

    async def test_cancelled_committing_save_during_draft_reset_keeps_receipt_resolvable(
        self, vm, filled_litellm_draft
    ):
        """写后取消 + 保存期间新建草稿：身份保留可恢复，恢复不作用于新草稿。"""
        import asyncio

        async def reset_then_cancel(payload, expected_revision, operation_id):
            vm.start_add()
            raise asyncio.CancelledError

        vm._service.save_draft = AsyncMock(side_effect=reset_then_cancel)  # noqa: SLF001
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await vm.save()
        assert exc_info.type is asyncio.CancelledError

        st = vm.state
        # save_state 归重置命令所有；未知结果以旗标 + 回执身份保留
        assert st.save_state == "idle"
        assert st.commit_unknown_pending is True
        save_call = vm._service.save_draft.await_args  # noqa: SLF001
        assert save_call is not None
        operation_id = save_call.args[2]
        assert vm._pending_operation_id == operation_id  # noqa: SLF001

        vm._service.resolve_save_outcome.return_value = ConfigurationSaveOutcome(  # noqa: SLF001
            status="committed", current_revision=3
        )
        assert await vm.resolve_commit_unknown() is True
        st = vm.state
        assert st.commit_unknown_pending is False
        # 门控：确认结果不清新草稿 dirty / 不改新草稿 save_state
        assert st.editor_open is True
        assert st.save_state == "idle"
        assert st.dirty is False

    async def test_stale_error_receipt_does_not_clobber_new_draft(self, vm, filled_litellm_draft):
        """保存期间新增草稿：旧保存的异常不把新草稿状态打成 SAVE_ERROR。"""
        import asyncio

        async def reset_then_fail(payload, expected_revision, operation_id):
            vm.start_add()
            raise RuntimeError("boom")

        vm._service.save_draft = AsyncMock(side_effect=reset_then_fail)  # noqa: SLF001
        ok = await vm.save()

        assert ok is False
        st = vm.state
        assert st.save_state == "idle"
        assert st.editor_open is True
        assert st.commit_unknown_pending is False

    async def test_stale_commit_unknown_receipt_keeps_receipt_resolvable(self, vm, filled_litellm_draft):
        """保存期间新增草稿 + 回执 commit_unknown：身份保留可查，状态机不覆盖新草稿。"""

        async def reset_then_unknown(payload, expected_revision, operation_id):
            vm.start_add()
            return ConfigurationSaveOutcome(status="commit_unknown")

        vm._service.save_draft = AsyncMock(side_effect=reset_then_unknown)  # noqa: SLF001
        ok = await vm.save()

        assert ok is False
        st = vm.state
        # 结果未知：回执身份保留（save_state 归重置命令所有，仅挂旗标）
        assert st.save_state == "idle"
        assert st.commit_unknown_pending is True
        assert st.editor_open is True
        save_call = vm._service.save_draft.await_args  # noqa: SLF001
        assert save_call is not None
        operation_id = save_call.args[2]
        assert vm._pending_operation_id == operation_id  # noqa: SLF001

        # 门控恢复：确认 committed 不清新草稿 dirty / 不改新草稿 save_state
        vm._service.resolve_save_outcome.return_value = ConfigurationSaveOutcome(  # noqa: SLF001
            status="committed", current_revision=3
        )
        assert await vm.resolve_commit_unknown() is True
        st = vm.state
        assert st.commit_unknown_pending is False
        assert st.editor_open is True
        assert st.save_state == "idle"

    async def test_stale_not_committed_receipt_does_not_touch_new_draft(self, vm, filled_litellm_draft):
        """保存期间新增草稿 + 回执 not_committed：可证明未提交，状态归重置命令。"""

        async def reset_then_not_committed(payload, expected_revision, operation_id):
            vm.start_add()
            return ConfigurationSaveOutcome(status="not_committed", current_revision=1)

        vm._service.save_draft = AsyncMock(side_effect=reset_then_not_committed)  # noqa: SLF001
        ok = await vm.save()

        assert ok is False
        st = vm.state
        assert st.save_state == "idle"
        assert st.editor_open is True
        assert st.commit_unknown_pending is False
        assert vm._pending_operation_id is None  # noqa: SLF001


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


class _StubAdapter:
    """单次测试适配器替身（InferenceAdapter 协议）：记录入参并返回协议响应。"""

    def __init__(self, response: AdapterResponse | None = None, error: Exception | None = None) -> None:
        self.response = (
            response if response is not None else AdapterResponse(content="pong", usage=None, actual_model=None)
        )
        self.error = error
        self.requests: list[InferenceRequest] = []
        self.connections: list[ResolvedConnection] = []
        self.attempt_contexts: list[AttemptContext] = []

    async def invoke(
        self,
        request: InferenceRequest,
        resolved_connection: ResolvedConnection,
        deadline: float,
        attempt_context: AttemptContext,
    ) -> AdapterResponse:
        self.requests.append(request)
        self.connections.append(resolved_connection)
        self.attempt_contexts.append(attempt_context)
        if self.error is not None:
            raise self.error
        return self.response


class TestSyntheticTest:
    async def test_unsaved_draft_runs_dedicated_test_path(self):
        """未保存草稿可测：一次性消费 consent 后直连适配器；不落业务授权 / 不保存。"""
        svc = _make_service()
        adapter = _StubAdapter()
        vm = _make_vm(svc, synthetic_adapters={"litellm_chat": adapter})
        vm.start_add()
        vm.update_name("Probe")
        vm.update_model("gpt-4o")
        vm.update_provider("openai")
        vm.update_api_base("https://api.example.com/v1")

        ok = await vm.test()

        assert ok is True
        assert vm.state.test_state == "success"
        svc.begin_synthetic_test.assert_awaited_once()
        consent_call = svc.create_synthetic_test_consent.call_args
        assert consent_call.args[0] is svc.begin_synthetic_test.return_value
        assert set(consent_call.kwargs) == {"target_fingerprint", "deadline"}
        # 一次性消费：绑定事实（consent 对象 + 草稿 payload + 目标指纹）交给许可消费者
        consume_call = svc.consume_synthetic_test_consent.call_args
        assert consume_call.args[0] is svc.create_synthetic_test_consent.return_value
        assert consume_call.kwargs["target_fingerprint"] == consent_call.kwargs["target_fingerprint"]
        assert consume_call.kwargs["draft_connection"]["model"] == "gpt-4o"
        # 适配器直连：固定合成探针正文（常量 ping，无用户正文）
        assert len(adapter.requests) == 1
        assert adapter.requests[0].payload == {"prompt": "ping"}
        assert adapter.requests[0].scenario_id == "synthetic_test"
        assert adapter.requests[0].task_id == "model_connection_test"
        # 无隐式持久化授权：不保存连接、不写业务 grant
        svc.save_draft.assert_not_called()
        svc.grant_egress.assert_not_called()

    async def test_gguf_sentiment_probe_payload_satisfies_adapter_protocol(self):
        """gguf_sentiment 探针含 texts（适配器协议强制），空 payload 必然失败的反向回归。"""
        svc = _make_service()
        adapter = _StubAdapter()
        vm = _make_vm(svc, synthetic_adapters={"gguf_sentiment": adapter})
        vm.start_add()
        vm.update_name("Probe")
        vm.update_model("finbert-encoder")
        vm.update_adapter_kind("gguf_sentiment")
        # gguf_sentiment 静态校验强制三路径（encoder/head/tokenizer）
        vm.update_encoder_model_path("models/encoder.gguf")
        vm.update_head_path("models/head.onnx")
        vm.update_tokenizer_path("models/tokenizer.json")

        ok = await vm.test()

        assert ok is True
        assert len(adapter.requests) == 1
        assert adapter.requests[0].payload == {"texts": ["ping"]}

    def test_resolved_test_connection_rejects_unknown_kind(self):
        """kind 域外拒绝（防御：payload 由校验后草稿构建，正常流程不可达）。"""
        with pytest.raises(ValueError, match="unsupported adapter kind"):
            ModelConnectionsViewModel._resolved_test_connection({"adapter_kind": "bogus"})

    async def test_new_credential_participates_in_test(self):
        """新填 credential 参与测试（临时版本化凭据），不使用落库旧值。"""
        svc = _make_service()
        adapter = _StubAdapter()
        vm = _make_vm(svc, synthetic_adapters={"litellm_chat": adapter})
        vm.start_add()
        vm.update_name("Probe")
        vm.update_model("gpt-4o")
        vm.update_provider("openai")
        vm.update_credential("sk-live-key")

        ok = await vm.test()

        assert ok is True
        svc.prepare_credential_version.assert_awaited_once()
        assert svc.prepare_credential_version.call_args.args[1] == "sk-live-key"
        # 版本化凭据进入测试上下文与实际执行连接
        assert svc.begin_synthetic_test.call_args.kwargs["credential_version"] == "v-new"
        resolved = adapter.connections[0]
        assert resolved.credential_ref == "keyring:new"
        assert resolved.credential_version == "v-new"

    async def test_blank_credential_uses_stored_version(self):
        """留空凭据：编辑既有连接时以已存版本参与测试，不重复创建 keyring 项。"""
        svc = _make_service(_snapshot(connections=(_conn(),)))
        adapter = _StubAdapter()
        vm = _make_vm(svc, synthetic_adapters={"litellm_chat": adapter})
        await vm.load()
        vm.edit("c1")

        ok = await vm.test()

        assert ok is True
        svc.prepare_credential_version.assert_not_awaited()
        resolved = adapter.connections[0]
        assert resolved.credential_ref == "keyring:old"
        assert resolved.credential_version == "v-old"

    async def test_consume_rejection_fails_test(self):
        """许可消费被拒（重放 / 绑定不符）→ 测试失败，不执行适配器。"""
        svc = _make_service()
        svc.consume_synthetic_test_consent = MagicMock(side_effect=PermissionError("单次测试许可无效或已消费"))
        adapter = _StubAdapter()
        vm = _make_vm(svc, synthetic_adapters={"litellm_chat": adapter})
        vm.start_add()
        vm.update_name("Probe")
        vm.update_model("gpt-4o")

        ok = await vm.test()

        assert ok is False
        assert vm.state.test_state == "failed"
        assert adapter.requests == []

    async def test_adapter_error_fails_test(self):
        """适配器执行异常 → 测试失败（不伪装成功，R21）。"""
        svc = _make_service()
        adapter = _StubAdapter(error=RuntimeError("connection refused"))
        vm = _make_vm(svc, synthetic_adapters={"litellm_chat": adapter})
        vm.start_add()
        vm.update_name("Probe")
        vm.update_model("gpt-4o")

        ok = await vm.test()

        assert ok is False
        assert vm.state.test_state == "failed"

    async def test_missing_adapter_kind_fails_test(self):
        """适配器表缺该 kind → 诚实失败（不伪装成功，R21）。"""
        svc = _make_service()
        adapter = _StubAdapter()
        vm = _make_vm(svc, synthetic_adapters={"gguf_chat": adapter})
        vm.start_add()
        vm.update_name("Probe")
        vm.update_model("gpt-4o")

        ok = await vm.test()

        assert ok is False
        assert vm.state.test_state == "failed"
        assert adapter.requests == []

    async def test_without_adapters_degrades(self, vm):
        vm.start_add()
        vm.update_name("Probe")
        vm.update_model("gpt-4o")
        ok = await vm.test()
        assert ok is False
        assert vm.state.test_state == "failed"

    async def test_cancelled_error_propagates(self):
        """R2：取消显式传播，不吞没。"""
        svc = _make_service()
        svc.begin_synthetic_test = AsyncMock(side_effect=asyncio.CancelledError())
        vm = _make_vm(svc, synthetic_adapters={"litellm_chat": _StubAdapter()})
        vm.start_add()
        vm.update_name("Probe")
        vm.update_model("gpt-4o")

        with pytest.raises(asyncio.CancelledError) as exc_info:
            await vm.test()
        # 精确状态断言：取消发生在 begin（终态写入前），test_state 保持 running 不被改写
        assert isinstance(exc_info.value, asyncio.CancelledError)
        assert vm.state.test_state == "running"


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
    scope_version: int = 1,
    confirmed_at: str = "2026-10-19T00:00:00+00:00",
    revoked_at: str | None = None,
) -> dict:
    return {
        "grant_id": grant_id,
        "connection_id": connection_id,
        "target_fingerprint": target_fingerprint,
        "purpose": purpose,
        "scope_version": scope_version,
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

    async def test_grants_for_filters_mismatched_target(self):
        """R07/T2：授权摘要按当前规范化目标筛选——grant 不属于当前目标不展示。

        运行链路同构：grant.target_fingerprint 与 candidate.target_fingerprint
        均为 normalize_egress_target 规范化目标（authorize_attempt 严格匹配）。
        """
        svc = _make_service(
            _snapshot(
                connections=(
                    _conn(connection_id="c1", api_base="https://api.openai.com/v1"),
                    _conn(connection_id="c2", api_base="https://relay.example.com/v1"),
                ),
                security_grants=(
                    # c1 当前目标的有效 grant（展示）
                    _grant(grant_id="g1", connection_id="c1", purpose="stock_analysis"),
                    # c1 的旧目标 grant（api_base 改址前签发，不展示）
                    _grant(
                        grant_id="g2",
                        connection_id="c1",
                        purpose="stock_analysis",
                        target_fingerprint="https://old.example.com:443/v1",
                    ),
                    # 同连接但冒充 c2 目标的 grant（同 id / 同用途、目标失配，不展示）
                    _grant(
                        grant_id="g3",
                        connection_id="c1",
                        purpose="stock_analysis",
                        target_fingerprint="https://relay.example.com:443/v1",
                    ),
                ),
            )
        )
        vm = _make_vm(svc)
        await vm.load()

        assert tuple(g["grant_id"] for g in vm.grants_for("c1", "stock_analysis")) == ("g1",)

    async def test_grants_for_filters_stale_scope_version(self):
        """R07/T2：授权摘要按当前授权范围世代筛选——旧世代 grant 不展示。"""
        svc = _make_service(
            _snapshot(
                connections=(_conn(connection_id="c1"),),
                security_grants=(
                    # 当前世代（1）grant（展示）
                    _grant(grant_id="g1", connection_id="c1", purpose="stock_analysis", scope_version=1),
                    # 旧世代 grant（不展示）
                    _grant(grant_id="g2", connection_id="c1", purpose="stock_analysis", scope_version=0),
                    # 未来世代 grant（不展示）
                    _grant(grant_id="g3", connection_id="c1", purpose="stock_analysis", scope_version=2),
                    # 缺 scope_version 字段的旧数据（不展示，fail-closed）
                    {
                        "grant_id": "g4",
                        "connection_id": "c1",
                        "purpose": "stock_analysis",
                        "target_fingerprint": "https://api.openai.com:443/v1",
                        "confirmed_at": "2026-10-19T00:00:00+00:00",
                        "revoked_at": None,
                    },
                ),
            )
        )
        vm = _make_vm(svc)
        await vm.load()

        assert tuple(g["grant_id"] for g in vm.grants_for("c1", "stock_analysis")) == ("g1",)

    async def test_grants_for_follows_current_scope_generation(self):
        """快照 security_scope_version 轮转后，摘要随当前世代切换（联动）。"""
        svc = _make_service(
            _snapshot(
                connections=(_conn(connection_id="c1"),),
                security_grants=(_grant(grant_id="g1", connection_id="c1", purpose="stock_analysis", scope_version=2),),
                security_scope_version=2,
            )
        )
        vm = _make_vm(svc)
        await vm.load()
        assert tuple(g["grant_id"] for g in vm.grants_for("c1", "stock_analysis")) == ("g1",)

    async def test_grants_for_local_connection_returns_empty(self):
        """本地连接（无有效目标）返回空 tuple，诚实表示无有效授权（R21）。"""
        svc = _make_service(
            _snapshot(
                connections=(_conn(connection_id="c1", adapter_kind="gguf_chat", api_base=None),),
                security_grants=(_grant(grant_id="g1", connection_id="c1", purpose="stock_analysis"),),
            )
        )
        vm = _make_vm(svc)
        await vm.load()
        assert vm.grants_for("c1", "stock_analysis") == ()

    async def test_connection_requires_egress_by_adapter_kind(self):
        """R07/T2：本地 / egress 判定一律经 core 正本 adapter_kind_requires_egress。"""
        svc = _make_service(
            _snapshot(
                connections=(
                    _conn(connection_id="c1", adapter_kind="litellm_chat"),
                    _conn(connection_id="c2", adapter_kind="gguf_chat", api_base=None),
                    _conn(connection_id="c3", adapter_kind="gguf_sentiment", api_base=None),
                    _conn(connection_id="c4", adapter_kind="unknown_future_kind"),
                ),
            )
        )
        vm = _make_vm(svc)
        await vm.load()

        assert vm.connection_requires_egress("c1") is True
        assert vm.connection_requires_egress("c2") is False
        assert vm.connection_requires_egress("c3") is False
        # fail-closed：未知 kind（含新增未登记类型）要求 egress
        assert vm.connection_requires_egress("c4") is True
        # fail-closed：连接不存在（未知身份）不放宽
        assert vm.connection_requires_egress("missing") is True


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


class TestWizardDraftOption:
    """F13：阶段内草稿注入形态（纯内存，供场景选择器 attach）。"""

    async def test_empty_name_returns_none(self, vm):
        await vm.load()
        vm.start_add()
        assert vm.wizard_draft_option() is None

    async def test_named_draft_returns_option_shape(self, vm):
        await vm.load()
        vm.start_add()
        vm.update_name("New Conn")
        vm.update_model("gpt-4o")
        vm.update_provider("openai")
        vm.update_api_base("https://api.example.com/v1")

        option = vm.wizard_draft_option()

        assert option is not None
        # 能力留空：草稿未经验证，选择器按 unknown 展示，不臆造支持态（R21）
        assert set(option.keys()) == {
            "connection_id",
            "name",
            "adapter_kind",
            "provider",
            "model",
            "enabled",
            "billing",
            "connection_revision",
        }
        assert option["name"] == "New Conn"
        assert option["adapter_kind"] == "litellm_chat"
        assert option["provider"] == "openai"
        assert option["model"] == "gpt-4o"
        assert option["enabled"] is True
        assert option["connection_revision"] == 1
        assert option["connection_id"]  # 稳定非空标识

    async def test_option_id_matches_draft_entry_id(self):
        """注入候选 id 与提交时的连接 id 同源，提交后候选不悬空。"""
        svc = _make_service()
        vm = _make_vm(svc)
        await vm.load()
        vm.start_add()
        vm.update_name("New Conn")
        vm.update_model("gpt-4o")
        vm.update_provider("openai")
        vm.update_credential("sk-new")

        option = vm.wizard_draft_option()
        entry = await vm.draft_connection_entry("op-1")

        assert option is not None and entry is not None
        assert option["connection_id"] == entry["connection_id"]


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

        # test() 前置要求 synthetic_adapters 非 None（否则提前返回 TEST_FAILED），
        # 故直接构造带适配器表的 VM（默认 vm fixture 不注入适配器表）。
        vm = _make_vm(_make_service(), synthetic_adapters={"litellm_chat": _StubAdapter()})
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


# --- F32：表单映射闭合（timeout / n_ctx / 按计量单位验价） ---


class TestFormPayloadClosure:
    async def test_minimal_gguf_chat_payload_passes_real_compatibility(self):
        """回归（P08 消除）：UI 必填最小 GGUF chat 即可执行——payload 带显式 n_ctx。

        仅填表单可见必填项（名称 + 对话模型路径），落盘 payload 必须被真实兼容
        函数（``ModelCompatibilityService.resolve_execution_params``）接受。
        """
        svc = _make_service()
        vm = _make_vm(svc)
        await vm.load()
        vm.start_add()
        vm.update_name("Local Chat")
        vm.update_adapter_kind("gguf_chat")
        vm.update_dialog_model_path("dialog.gguf")

        assert await vm.save() is True

        conn = svc.save_draft.await_args.args[0]["connections"][0]
        assert conn["adapter_options"]["n_ctx"] == 4096
        decision = ModelCompatibilityService().resolve_execution_params(conn)
        assert decision.compatible is True
        assert decision.reason is None

    async def test_minimal_gguf_sentiment_payload_passes_real_compatibility(self):
        """回归（P08 消除）：UI 必填最小 GGUF sentiment（三路径）同样通过真实兼容函数。"""
        svc = _make_service()
        vm = _make_vm(svc)
        await vm.load()
        vm.start_add()
        vm.update_name("Local Sentiment")
        vm.update_adapter_kind("gguf_sentiment")
        vm.update_encoder_model_path("enc.gguf")
        vm.update_head_path("head.npz")
        vm.update_tokenizer_path("tok.json")

        assert await vm.save() is True

        conn = svc.save_draft.await_args.args[0]["connections"][0]
        assert conn["adapter_options"]["n_ctx"] == 4096
        decision = ModelCompatibilityService().resolve_execution_params(conn)
        assert decision.compatible is True
        assert decision.reason is None

    async def test_existing_gguf_n_ctx_preserved_on_edit(self):
        """读取重开保真：磁盘已有合法 n_ctx（如手配 2048）编辑保存不被默认值覆盖。"""
        raw = _conn(connection_id="c1", adapter_kind="gguf_chat", api_base=None)
        raw["adapter_options"] = {"dialog_model_path": "dialog.gguf", "n_ctx": 2048}
        svc = _make_service(_snapshot(connections=(raw,)))
        vm = _make_vm(svc)
        await vm.load()
        vm.edit("c1")

        assert await vm.save() is True

        conn = svc.save_draft.await_args.args[0]["connections"][0]
        assert conn["adapter_options"]["n_ctx"] == 2048

    async def test_timeout_roundtrip_600_via_reload(self):
        """回归：timeout=600 保存 → 重载草稿仍为 600 → 再保存仍进入 adapter_options["timeout"]。"""
        svc = _make_service()
        vm = _make_vm(svc)
        await vm.load()
        vm.start_add()
        vm.update_name("Cloud")
        vm.update_model("gpt-4o")
        vm.update_timeout_seconds("600")

        assert await vm.save() is True

        saved = dict(svc.save_draft.await_args.args[0]["connections"][0])
        assert saved["adapter_options"]["timeout"] == 600.0

        # 模拟磁盘回读重开（编辑既有连接）
        svc2 = _make_service(_snapshot(connections=(saved,)))
        vm2 = _make_vm(svc2)
        await vm2.load()
        vm2.edit(str(saved["connection_id"]))
        assert vm2.state.draft.timeout_seconds == "600"

        assert await vm2.save() is True
        reloaded = svc2.save_draft.await_args.args[0]["connections"][0]
        assert reloaded["adapter_options"]["timeout"] == 600.0

    async def test_timeout_blank_not_written(self):
        """R21：timeout 未填不伪装——adapter_options 不写入 timeout 键。"""
        svc = _make_service()
        vm = _make_vm(svc)
        await vm.load()
        vm.start_add()
        vm.update_name("Cloud")
        vm.update_model("gpt-4o")

        assert await vm.save() is True

        conn = svc.save_draft.await_args.args[0]["connections"][0]
        assert "timeout" not in conn["adapter_options"]

    async def test_timeout_clamped_to_min(self):
        """timeout 低于下限 clamp 至 1.0s（负值/零不可执行，落盘即有效值）。"""
        svc = _make_service()
        vm = _make_vm(svc)
        await vm.load()
        vm.start_add()
        vm.update_name("Cloud")
        vm.update_model("gpt-4o")
        vm.update_timeout_seconds("0.2")

        assert await vm.save() is True

        conn = svc.save_draft.await_args.args[0]["connections"][0]
        assert conn["adapter_options"]["timeout"] == 1.0

    @pytest.mark.parametrize("raw", ["inf", "nan", "abc", "-"])
    async def test_timeout_non_finite_or_garbage_not_written(self, raw):
        """非有限/非法输入不落盘（不伪装，R21；inf/nan 会破坏严格 JSON 往返）。"""
        svc = _make_service()
        vm = _make_vm(svc)
        await vm.load()
        vm.start_add()
        vm.update_name("Cloud")
        vm.update_model("gpt-4o")
        vm.update_timeout_seconds(raw)

        assert await vm.save() is True

        conn = svc.save_draft.await_args.args[0]["connections"][0]
        assert "timeout" not in conn["adapter_options"]

    async def test_encoder_price_only_required_for_encoder_unit(self):
        """回归：分类费用型仅 encoder 单价可保存——不再要求 input 单价。"""
        svc = _make_service()
        vm = _make_vm(svc)
        await vm.load()
        vm.start_add()
        vm.update_name("Local Sentiment")
        vm.update_adapter_kind("gguf_sentiment")
        vm.update_encoder_model_path("enc.gguf")
        vm.update_head_path("head.npz")
        vm.update_tokenizer_path("tok.json")
        vm.update_billing_mode("estimate_cost")
        vm.update_encoder_price("0.8")
        # 不填 input_price_per_m

        assert vm.validate_draft() == ()

        assert await vm.save() is True
        conn = svc.save_draft.await_args.args[0]["connections"][0]
        assert conn["billing"]["pricing_unit"] == "encoder_input_tokens"
        assert conn["billing"]["encoder_price_per_m"] == "0.8"
        assert conn["billing"]["input_price_per_m"] is None

    async def test_encoder_price_missing_still_required(self):
        """分类费用型 estimate_cost 缺 encoder 单价仍拦截（不放松校验）。"""
        vm = _make_vm(_make_service())
        await vm.load()
        vm.start_add()
        vm.update_name("Local Sentiment")
        vm.update_adapter_kind("gguf_sentiment")
        vm.update_encoder_model_path("enc.gguf")
        vm.update_head_path("head.npz")
        vm.update_tokenizer_path("tok.json")
        vm.update_billing_mode("estimate_cost")

        errors = vm.validate_draft()
        assert "model_connection_error_price_required" in errors

    async def test_litellm_estimate_cost_requires_input_price(self):
        """llm_tokens 计量路径不回归：estimate_cost 缺 input 单价仍拦截。"""
        vm = _make_vm(_make_service())
        await vm.load()
        vm.start_add()
        vm.update_name("Cloud")
        vm.update_model("gpt-4o")
        vm.update_billing_mode("estimate_cost")
        vm.update_output_price("2.0")  # 只填输出价

        errors = vm.validate_draft()
        assert "model_connection_error_price_required" in errors


# --- F30: 重基（rebase）---


class TestRebaseSnapshot:
    @pytest.mark.asyncio
    async def test_rebase_updates_baseline_and_keeps_draft(self):
        """场景域提交后重基：CAS revision 前移，编辑中的草稿逐字段保留（F30）。"""
        svc = _make_service(_snapshot(revision=1, connections=(_conn(),), scenario_routes=(_route(),)))
        vm = _make_vm(svc)
        await vm.load()
        vm.edit("c1")
        vm.update_name("Renamed")  # 草稿分叉
        assert vm.state.dirty is True

        svc.get_snapshot = AsyncMock(
            return_value=_snapshot(
                revision=6,
                connections=(_conn(), _conn("c2", name="New Conn")),
                scenario_routes=(_route(),),
            )
        )
        ok = await vm.rebase_snapshot()

        assert ok is True
        # CAS 基线前移 + 行刷新（含新连接 c2）
        assert vm.state.config_revision == 6
        assert [r.connection_id for r in vm.state.connections] == ["c1", "c2"]
        # 草稿保留：编辑器开、dirty、改名不丢（F30 回归标准——无丢草稿）
        assert vm.state.editor_open is True
        assert vm.state.dirty is True
        assert vm.state.draft.name == "Renamed"

    @pytest.mark.asyncio
    async def test_rebase_resolves_conflict_to_idle(self):
        svc = _make_service(_snapshot(revision=1, connections=(_conn(),)))
        vm = _make_vm(svc)
        await vm.load()
        vm.edit("c1")
        vm.update_name("Renamed")
        svc.save_draft = AsyncMock(side_effect=ModelConfigurationConflictError(5, 6))
        await vm.save()
        assert vm.state.save_state == "conflict"

        svc.get_snapshot = AsyncMock(return_value=_snapshot(revision=6, connections=(_conn(),)))
        ok = await vm.rebase_snapshot()

        assert ok is True
        assert vm.state.save_state == "idle"  # 冲突就地恢复（新基线可重试）
        assert vm.state.message is None
        assert vm.state.draft.name == "Renamed"  # 草稿保留

    @pytest.mark.asyncio
    async def test_rebase_keeps_validation_error_state(self):
        """SAVE_ERROR 含验证失败语义，重基不清（R21：错误提示仍真实）。"""
        svc = _make_service(_snapshot(revision=1, connections=(_conn(),)))
        vm = _make_vm(svc)
        await vm.load()
        vm.start_add()
        vm.update_billing_mode("estimate_cost")  # 缺单价 → 验证失败
        await vm.save()
        assert vm.state.save_state == "error"
        assert vm.state.validation_errors != ()

        svc.get_snapshot = AsyncMock(return_value=_snapshot(revision=6, connections=(_conn(),)))
        ok = await vm.rebase_snapshot()

        assert ok is True
        assert vm.state.save_state == "error"  # 不清验证失败态
        assert vm.state.validation_errors != ()

    @pytest.mark.asyncio
    async def test_rebase_failure_returns_false_and_keeps_state(self):
        svc = _make_service(_snapshot(revision=1, connections=(_conn(),)))
        vm = _make_vm(svc)
        await vm.load()
        vm.edit("c1")
        before = vm.state
        svc.get_snapshot = AsyncMock(side_effect=RuntimeError("boom"))

        ok = await vm.rebase_snapshot()

        assert ok is False  # 不伪装成功（R21）
        assert vm.state is before

    @pytest.mark.asyncio
    async def test_rebase_r2_cancelled_error_propagates(self):
        svc = _make_service(_snapshot(revision=1, connections=(_conn(),)))
        vm = _make_vm(svc)
        await vm.load()
        before = vm.state
        svc.get_snapshot = AsyncMock(side_effect=asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError) as exc_info:
            await vm.rebase_snapshot()
        assert isinstance(exc_info.value, asyncio.CancelledError)  # 显式断言取消类型
        assert svc.get_snapshot.await_count == 1  # 取消发生在读快照中（非入口短路）
        assert vm.state is before  # 取消未产生部分状态更新
