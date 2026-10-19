"""ModelRoutesViewModel 单元测试（S5 §11.4 场景配置路由草稿）。

覆盖（R19）：
- 每场景独立草稿
- 切场景保留草稿 + 未保存计数
- 重复添加禁用 / 追加末尾
- 排序首末禁用
- 末次移动 / 移除可撤销（单级）
- 保存只提交当前场景（其它场景未保存草稿不动）
- revision 冲突保留草稿

mock ``ModelConfigurationService``（非单例 DI），不触碰 ConfigHandler / flet。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.i18n import Message
from core.model_management_types import (
    ConfigurationSaveOutcome,
    ModelConfigurationConflictError,
    ModelManagementSnapshot,
)
from ui.viewmodels.model_routes_view_model import (
    ConnectionOption,
    ModelRoutesState,
    ModelRoutesViewModel,
    ScenarioRouteDraft,
)

pytestmark = pytest.mark.unit


# --- fixtures / helpers ---


def _route_mapping(
    scenario_id: str,
    candidates: tuple[str, ...] | list[str] = (),
    *,
    enabled: bool = False,
    timeout: int = 45,
    revision: int = 1,
) -> dict:
    task_routes: dict = {}
    if scenario_id == "news_annotation":
        task_routes = {"sentiment": {"mode": "bundled", "candidates": [], "reuse_bundled_sentiment": True}}
    return {
        "scenario_id": scenario_id,
        "enabled": enabled,
        "candidates": list(candidates),
        "execution_options": {},
        "total_timeout_seconds": timeout,
        "transient_retries": 1,
        "fallback_enabled": True,
        "route_revision": revision,
        "task_routes": task_routes,
    }


def _connection_mapping(
    connection_id: str,
    *,
    name: str = "连接",
    enabled: bool = True,
    adapter_kind: str = "litellm_chat",
    model: str = "gpt-x",
    revision: int = 1,
    billing_mode: str = "usage_only",
    capability: str = "supported",
) -> dict:
    return {
        "connection_id": connection_id,
        "name": name,
        "enabled": enabled,
        "adapter_kind": adapter_kind,
        "provider": "openai",
        "model": model,
        "connection_revision": revision,
        "billing": {"mode": billing_mode, "pricing_unit": "llm_tokens"},
        "capabilities": {"news_annotation": {"support": capability}},
    }


def _snapshot(
    *,
    config_revision: int = 5,
    routes: list[dict] | None = None,
    connections: list[dict] | None = None,
) -> ModelManagementSnapshot:
    if routes is None:
        routes = [
            _route_mapping("news_annotation", (), timeout=45),
            _route_mapping("stock_analysis", (), timeout=180),
            _route_mapping("news_risk", (), timeout=90),
        ]
    if connections is None:
        connections = [
            _connection_mapping("conn-a", name="主连接", revision=3),
            _connection_mapping("conn-b", name="备用连接", revision=1),
        ]
    return ModelManagementSnapshot(
        snapshot_id="snap-1",
        config_revision=config_revision,
        migration_state="active",
        connections=tuple(connections),
        scenario_routes=tuple(routes),
        usage_policy={"monthly_cny_limit": None, "detail_retention_days": 180},
        security_revision=1,
        created_at="2026-01-01T00:00:00+00:00",
    )


def _committed_outcome(revision: int = 6) -> ConfigurationSaveOutcome:
    return ConfigurationSaveOutcome(
        status="committed",
        current_revision=revision,
        committed_revision=revision,
        last_config_commit={"operation_id": "op-1"},
    )


def _make_vm(
    snapshot: ModelManagementSnapshot,
    *,
    save_outcome: ConfigurationSaveOutcome | None = None,
    save_error: Exception | None = None,
) -> tuple[ModelRoutesViewModel, MagicMock]:
    service = MagicMock()
    service.get_snapshot = AsyncMock(return_value=snapshot)
    if save_error is not None:
        service.save_draft = AsyncMock(side_effect=save_error)
    else:
        service.save_draft = AsyncMock(return_value=save_outcome or _committed_outcome())
    service.resolve_save_outcome = AsyncMock(
        return_value=ConfigurationSaveOutcome(status="commit_unknown", current_revision=5)
    )
    return ModelRoutesViewModel(service), service


def _route_candidates(vm: ModelRoutesViewModel, scenario_id: str) -> tuple[str, ...]:
    route = next(r for r in vm.state.routes if r.scenario_id == scenario_id)
    return route.candidates


def _committed_candidates(vm: ModelRoutesViewModel, scenario_id: str) -> tuple[str, ...]:
    route = next(r for r in vm.state.committed_routes if r.scenario_id == scenario_id)
    return route.candidates


async def _vm_with_three_in_stock_analysis() -> ModelRoutesViewModel:
    snap = _snapshot(
        routes=[
            _route_mapping("news_annotation", ()),
            _route_mapping("stock_analysis", ("conn-a", "conn-b", "conn-c"), timeout=180),
            _route_mapping("news_risk", ()),
        ]
    )
    vm, _ = _make_vm(snap)
    await vm.load()
    vm.select_scenario("stock_analysis")
    return vm


# --- load ---


class TestLoad:
    @pytest.mark.asyncio
    async def test_load_builds_state(self):
        vm, _ = _make_vm(_snapshot())
        await vm.load()
        assert {r.scenario_id for r in vm.state.routes} == {"news_annotation", "stock_analysis", "news_risk"}
        assert len(vm.state.available_connections) == 2
        assert vm.state.selected_scenario == "news_annotation"
        assert vm.state.routes == vm.state.committed_routes
        assert vm.state.undo_available is False
        assert vm.state.save_state == "idle"
        assert vm.state.message is None

    @pytest.mark.asyncio
    async def test_load_failure_sets_message(self):
        service = MagicMock()
        service.get_snapshot = AsyncMock(side_effect=RuntimeError("boom"))
        service.save_draft = AsyncMock()
        vm = ModelRoutesViewModel(service)
        await vm.load()
        assert vm.state.save_state == "error"
        assert isinstance(vm.state.message, Message)


# --- per-scenario drafts ---


class TestPerScenarioDraft:
    @pytest.mark.asyncio
    async def test_per_scenario_independent_drafts(self):
        vm, _ = _make_vm(_snapshot())
        await vm.load()
        vm.add_candidate("conn-a")  # 默认选中 news_annotation
        vm.select_scenario("stock_analysis")
        vm.add_candidate("conn-b")
        drafts = {r.scenario_id: r.candidates for r in vm.state.routes}
        assert drafts["news_annotation"] == ("conn-a",)
        assert drafts["stock_analysis"] == ("conn-b",)
        assert drafts["news_risk"] == ()

    @pytest.mark.asyncio
    async def test_switch_scenario_preserves_draft_and_dirty_count(self):
        vm, _ = _make_vm(_snapshot())
        await vm.load()
        vm.add_candidate("conn-a")
        assert dict(vm.state.dirty_counts)["news_annotation"] == 1
        vm.select_scenario("stock_analysis")
        assert vm.state.selected_scenario == "stock_analysis"
        assert _route_candidates(vm, "news_annotation") == ("conn-a",)
        assert dict(vm.state.dirty_counts)["news_annotation"] == 1
        assert dict(vm.state.dirty_counts)["stock_analysis"] == 0


# --- candidate add / remove ---


class TestCandidateCommands:
    @pytest.mark.asyncio
    async def test_add_appends_to_end(self):
        vm, _ = _make_vm(_snapshot())
        await vm.load()
        vm.add_candidate("conn-a")
        vm.add_candidate("conn-b")
        assert _route_candidates(vm, "news_annotation") == ("conn-a", "conn-b")

    @pytest.mark.asyncio
    async def test_add_disables_duplicate(self):
        vm, _ = _make_vm(_snapshot())
        await vm.load()
        vm.add_candidate("conn-a")
        vm.add_candidate("conn-a")
        assert _route_candidates(vm, "news_annotation") == ("conn-a",)

    @pytest.mark.asyncio
    async def test_remove_reference_keeps_connection(self):
        vm, _ = _make_vm(_snapshot())
        await vm.load()
        vm.add_candidate("conn-a")
        vm.remove_candidate("conn-a")
        assert _route_candidates(vm, "news_annotation") == ()
        assert any(o.connection_id == "conn-a" for o in vm.state.available_connections)


# --- ordering / undo ---


class TestOrderingUndo:
    @pytest.mark.asyncio
    async def test_move_down_then_undo_restores_order(self):
        vm = await _vm_with_three_in_stock_analysis()
        vm.move_down("conn-a")
        assert _route_candidates(vm, "stock_analysis") == ("conn-b", "conn-a", "conn-c")
        assert vm.state.undo_available is True
        vm.undo_last_move()
        assert _route_candidates(vm, "stock_analysis") == ("conn-a", "conn-b", "conn-c")
        assert vm.state.undo_available is False

    @pytest.mark.asyncio
    async def test_move_up_on_first_is_noop(self):
        vm = await _vm_with_three_in_stock_analysis()
        assert vm.can_move_up("conn-a") is False
        vm.move_up("conn-a")
        assert _route_candidates(vm, "stock_analysis") == ("conn-a", "conn-b", "conn-c")
        assert vm.state.undo_available is False

    @pytest.mark.asyncio
    async def test_move_down_on_last_is_noop(self):
        vm = await _vm_with_three_in_stock_analysis()
        assert vm.can_move_down("conn-c") is False
        vm.move_down("conn-c")
        assert _route_candidates(vm, "stock_analysis") == ("conn-a", "conn-b", "conn-c")
        assert vm.state.undo_available is False

    @pytest.mark.asyncio
    async def test_undo_only_last_move(self):
        vm = await _vm_with_three_in_stock_analysis()
        vm.move_down("conn-a")  # -> b,a,c
        vm.move_down("conn-a")  # -> b,c,a
        assert _route_candidates(vm, "stock_analysis") == ("conn-b", "conn-c", "conn-a")
        vm.undo_last_move()  # 仅撤销最后一次 -> b,a,c
        assert _route_candidates(vm, "stock_analysis") == ("conn-b", "conn-a", "conn-c")

    @pytest.mark.asyncio
    async def test_undo_restores_removed_candidate(self):
        vm = await _vm_with_three_in_stock_analysis()
        vm.remove_candidate("conn-b")
        assert _route_candidates(vm, "stock_analysis") == ("conn-a", "conn-c")
        assert vm.state.undo_available is True
        vm.undo_last_move()
        assert _route_candidates(vm, "stock_analysis") == ("conn-a", "conn-b", "conn-c")


# --- save ---


class TestSave:
    @pytest.mark.asyncio
    async def test_save_commits_only_current_scenario(self):
        vm, service = _make_vm(_snapshot(config_revision=5))
        await vm.load()
        vm.add_candidate("conn-a")  # news_annotation（选中）
        vm.select_scenario("stock_analysis")
        vm.add_candidate("conn-b")
        vm.select_scenario("news_annotation")

        await vm.save_current_scenario()

        service.save_draft.assert_awaited_once()
        call_args = service.save_draft.await_args
        assert call_args is not None
        payload = call_args.args[0]
        assert call_args.args[1] == 5  # expected_revision
        routes = {r["scenario_id"]: r["candidates"] for r in payload["scenario_routes"]}
        assert routes["news_annotation"] == ["conn-a"]
        assert routes["stock_analysis"] == []  # 其它未保存草稿未一并提交

        assert _route_candidates(vm, "stock_analysis") == ("conn-b",)  # 草稿保留
        assert _committed_candidates(vm, "news_annotation") == ("conn-a",)
        assert _committed_candidates(vm, "stock_analysis") == ()
        assert dict(vm.state.dirty_counts)["news_annotation"] == 0
        assert dict(vm.state.dirty_counts)["stock_analysis"] == 1
        assert vm.state.save_state == "committed"

    @pytest.mark.asyncio
    async def test_revision_conflict_keeps_draft(self):
        conflict = ModelConfigurationConflictError(expected_revision=5, current_revision=6)
        vm, _ = _make_vm(_snapshot(config_revision=5), save_error=conflict)
        await vm.load()
        vm.add_candidate("conn-a")

        await vm.save_current_scenario()

        assert vm.state.save_state == "conflict"
        assert _route_candidates(vm, "news_annotation") == ("conn-a",)
        assert _committed_candidates(vm, "news_annotation") == ()

    @pytest.mark.asyncio
    async def test_commit_unknown_keeps_draft(self):
        outcome = ConfigurationSaveOutcome(status="commit_unknown", current_revision=5)
        vm, _ = _make_vm(_snapshot(config_revision=5), save_outcome=outcome)
        await vm.load()
        vm.add_candidate("conn-a")

        await vm.save_current_scenario()

        assert vm.state.save_state == "commit_unknown"
        assert _route_candidates(vm, "news_annotation") == ("conn-a",)
        assert _committed_candidates(vm, "news_annotation") == ()


# --- risk binding / UI toggles ---


class TestRiskBindingAndToggles:
    @pytest.mark.asyncio
    async def test_risk_binding_uses_connection_revision(self):
        vm, _ = _make_vm(_snapshot())
        await vm.load()
        binding = vm.risk_confirmation_binding("conn-a", "news_annotation", "risk_x")
        assert binding == (3, "news_annotation", "risk_x")

    @pytest.mark.asyncio
    async def test_risk_binding_unknown_connection_returns_none(self):
        vm, _ = _make_vm(_snapshot())
        await vm.load()
        assert vm.risk_confirmation_binding("missing", "news_annotation", "risk_x") is None

    @pytest.mark.asyncio
    async def test_picker_open_close(self):
        vm, _ = _make_vm(_snapshot())
        await vm.load()
        vm.open_candidate_picker()
        assert vm.state.candidate_picker_open is True
        vm.close_candidate_picker()
        assert vm.state.candidate_picker_open is False

    @pytest.mark.asyncio
    async def test_toggle_advanced(self):
        vm, _ = _make_vm(_snapshot())
        await vm.load()
        assert vm.state.advanced_expanded is False
        vm.toggle_advanced()
        assert vm.state.advanced_expanded is True

    @pytest.mark.asyncio
    async def test_dispose_marks_disposed(self):
        vm, _ = _make_vm(_snapshot())
        await vm.load()
        vm.dispose()
        assert vm.disposed is True


# --- state shape ---


class TestStateShape:
    @pytest.mark.asyncio
    async def test_state_is_frozen_and_tuple_containers(self):
        import dataclasses

        vm, _ = _make_vm(_snapshot())
        await vm.load()
        assert dataclasses.is_dataclass(ModelRoutesState)
        with pytest.raises(dataclasses.FrozenInstanceError) as exc_info:
            vm.state.selected_scenario = "news_risk"  # type: ignore[misc]  # 验证 frozen
        assert "selected_scenario" in str(exc_info.value)
        assert isinstance(vm.state.routes, tuple)
        assert isinstance(vm.state.dirty_counts, tuple)
        assert isinstance(vm.state.available_connections, tuple)
        assert isinstance(vm.state.routes[0], ScenarioRouteDraft)
        assert isinstance(vm.state.available_connections[0], ConnectionOption)


# --- payload builders（§11.6 向导原子提交本阶段差异） ---


class TestPayloadBuilders:
    @pytest.mark.asyncio
    async def test_all_routes_payload_serializes_drafts(self):
        vm, _ = _make_vm(_snapshot())
        await vm.load()
        vm.select_scenario("stock_analysis")
        vm.add_candidate("conn-a")

        payload = vm.all_routes_payload()

        assert [r["scenario_id"] for r in payload] == ["news_annotation", "stock_analysis", "news_risk"]
        stock = next(r for r in payload if r["scenario_id"] == "stock_analysis")
        assert stock["candidates"] == ["conn-a"]

    @pytest.mark.asyncio
    async def test_build_payload_uses_draft_for_current_and_committed_for_others(self):
        vm, _ = _make_vm(_snapshot())
        await vm.load()
        vm.select_scenario("stock_analysis")
        vm.add_candidate("conn-a")

        payload = vm.build_payload("stock_analysis")

        assert payload["schema_version"] == 1
        assert payload["config_revision"] == 5
        assert payload["migration_state"] == "active"
        assert [c["connection_id"] for c in payload["connections"]] == ["conn-a", "conn-b"]
        assert payload["usage_policy"] == {"monthly_cny_limit": None, "detail_retention_days": 180}
        routes = {r["scenario_id"]: r["candidates"] for r in payload["scenario_routes"]}
        assert routes["stock_analysis"] == ["conn-a"]  # 当前场景用草稿
        assert routes["news_annotation"] == []  # 其余用已提交值

    @pytest.mark.asyncio
    async def test_build_payload_without_draft_uses_committed(self):
        vm, _ = _make_vm(_snapshot())
        await vm.load()

        payload = vm.build_payload("stock_analysis")

        routes = {r["scenario_id"]: r["candidates"] for r in payload["scenario_routes"]}
        assert routes["stock_analysis"] == []
