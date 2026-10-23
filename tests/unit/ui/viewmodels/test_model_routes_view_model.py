"""ModelRoutesViewModel 单元测试（S5 §11.4 场景配置路由草稿）。

覆盖（R19）：
- 每场景独立草稿
- 切场景保留草稿 + 未保存计数
- 重复添加禁用 / 追加末尾
- 排序首末禁用
- 末次移动 / 移除可撤销（单级）
- 保存只提交当前场景（其它场景未保存草稿不动）
- revision 冲突保留草稿
- F14：路由往返保真（独立情绪候选 / 执行参数逐字段不变；
  route_revision 仅语义变化递增，无变化保存不篡改）

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
    _rebase_drafts,
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
    sentiment_mode: str = "bundled",
    sentiment_candidates: tuple[str, ...] | list[str] = (),
    execution_options: dict | None = None,
) -> dict:
    task_routes: dict = {}
    if scenario_id == "news_annotation":
        task_routes = {
            "sentiment": {
                "mode": sentiment_mode,
                "candidates": list(sentiment_candidates),
                "reuse_bundled_sentiment": True,
            }
        }
    return {
        "scenario_id": scenario_id,
        "enabled": enabled,
        "candidates": list(candidates),
        "execution_options": dict(execution_options) if execution_options is not None else {},
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
    migration_state: str = "active",
    migration_transition: dict | None = None,
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
        migration_state=migration_state,
        connections=tuple(connections),
        scenario_routes=tuple(routes),
        usage_policy={"monthly_cny_limit": None, "detail_retention_days": 180},
        security_revision=1,
        created_at="2026-01-01T00:00:00+00:00",
        migration_transition=migration_transition,
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


# --- F14：路由往返保真 / route_revision 语义 ---


def _news_dedicated_mapping() -> dict:
    """P13 场景基线：新闻主题=聊天主候选、情绪独立候选=编码器、有执行参数。"""
    return _route_mapping(
        "news_annotation",
        ("chat-a",),
        timeout=45,
        sentiment_mode="dedicated",
        sentiment_candidates=("encoder-b",),
        execution_options={"temperature": 0.2},
    )


class TestRouteRoundtripFidelity:
    @pytest.mark.asyncio
    async def test_save_stock_keeps_news_independent_sentiment_and_options(self):
        """P13/F14 回归：编辑股票路由保存后，新闻独立情绪候选与执行参数逐字段不变。"""
        vm, service = _make_vm(
            _snapshot(
                routes=[
                    _news_dedicated_mapping(),
                    _route_mapping("stock_analysis", (), timeout=180),
                    _route_mapping("news_risk", (), timeout=90),
                ]
            )
        )
        await vm.load()
        vm.select_scenario("stock_analysis")
        vm.add_candidate("conn-a")

        await vm.save_current_scenario()

        assert vm.state.save_state == "committed"
        payload = service.save_draft.await_args.args[0]
        rows = {r["scenario_id"]: r for r in payload["scenario_routes"]}
        news_row = rows["news_annotation"]
        assert news_row["candidates"] == ["chat-a"]
        assert news_row["task_routes"]["sentiment"]["mode"] == "dedicated"
        assert news_row["task_routes"]["sentiment"]["candidates"] == ["encoder-b"]
        assert news_row["execution_options"] == {"temperature": 0.2}
        assert news_row["route_revision"] == 1  # 未编辑场景 revision 不被篡改

    @pytest.mark.asyncio
    async def test_draft_state_captures_independent_fields(self):
        """加载后草稿模型携带独立情绪候选与执行参数（全字段捕获）。"""
        vm, _ = _make_vm(
            _snapshot(
                routes=[
                    _news_dedicated_mapping(),
                    _route_mapping("stock_analysis", (), timeout=180),
                    _route_mapping("news_risk", (), timeout=90),
                ]
            )
        )
        await vm.load()
        news_draft = next(r for r in vm.state.routes if r.scenario_id == "news_annotation")
        assert news_draft.sentiment_candidates == ("encoder-b",)
        assert news_draft.execution_options == {"temperature": 0.2}
        assert news_draft.sentiment_mode == "dedicated"
        assert news_draft.candidates == ("chat-a",)

    @pytest.mark.asyncio
    async def test_save_news_keeps_own_independent_fields(self):
        """编辑新闻场景主候选并保存：自身独立情绪候选与执行参数保留，revision 递增。"""
        vm, service = _make_vm(
            _snapshot(
                routes=[
                    _news_dedicated_mapping(),
                    _route_mapping("stock_analysis", (), timeout=180),
                    _route_mapping("news_risk", (), timeout=90),
                ]
            )
        )
        await vm.load()
        vm.add_candidate("conn-b")  # 当前选中即 news_annotation

        await vm.save_current_scenario()

        assert vm.state.save_state == "committed"
        payload = service.save_draft.await_args.args[0]
        rows = {r["scenario_id"]: r for r in payload["scenario_routes"]}
        news_row = rows["news_annotation"]
        assert news_row["candidates"] == ["chat-a", "conn-b"]
        assert news_row["task_routes"]["sentiment"]["candidates"] == ["encoder-b"]
        assert news_row["execution_options"] == {"temperature": 0.2}
        assert news_row["route_revision"] == 2  # 语义变化 → 基线 1 + 1

    @pytest.mark.asyncio
    async def test_route_revision_unchanged_when_no_semantic_change(self):
        """无语义变化保存：route_revision 保持原值不篡改。"""
        vm, service = _make_vm(_snapshot(config_revision=5))
        await vm.load()

        await vm.save_current_scenario()  # 未做任何编辑

        assert vm.state.save_state == "committed"
        payload = service.save_draft.await_args.args[0]
        rows = {r["scenario_id"]: r["route_revision"] for r in payload["scenario_routes"]}
        assert rows == {"news_annotation": 1, "stock_analysis": 1, "news_risk": 1}

    @pytest.mark.asyncio
    async def test_route_revision_increments_once_per_semantic_change(self):
        """同一场景两次语义变化保存：revision 从更新后的基线单调递增（2 → 3）。"""
        vm, service = _make_vm(_snapshot(config_revision=5))
        await vm.load()
        vm.add_candidate("conn-a")
        await vm.save_current_scenario()
        assert vm.state.save_state == "committed"

        vm.add_candidate("conn-b")
        await vm.save_current_scenario()

        assert vm.state.save_state == "committed"
        payload = service.save_draft.await_args.args[0]
        rows = {r["scenario_id"]: r["route_revision"] for r in payload["scenario_routes"]}
        assert rows["news_annotation"] == 3

    @pytest.mark.asyncio
    async def test_all_routes_payload_bumps_only_edited_scenarios(self):
        """向导路径：all_routes_payload 仅对语义变化的场景递增 revision。"""
        vm, _ = _make_vm(_snapshot())
        await vm.load()
        vm.select_scenario("stock_analysis")
        vm.add_candidate("conn-a")

        payload = vm.all_routes_payload()

        rows = {r["scenario_id"]: r["route_revision"] for r in payload}
        assert rows["stock_analysis"] == 2
        assert rows["news_annotation"] == 1
        assert rows["news_risk"] == 1
        stock = next(r for r in payload if r["scenario_id"] == "stock_analysis")
        assert stock["candidates"] == ["conn-a"]
        assert stock["execution_options"] == {}

    @pytest.mark.asyncio
    async def test_committed_routes_absorb_incremented_revision(self):
        """提交成功后 committed_routes 回写递增后的 revision（后续保存单调）。"""
        vm, _ = _make_vm(_snapshot(config_revision=5))
        await vm.load()
        vm.add_candidate("conn-a")
        await vm.save_current_scenario()
        assert vm.state.save_state == "committed"

        committed = {r.scenario_id: r for r in vm.state.committed_routes}
        assert committed["news_annotation"].route_revision == 2
        assert committed["news_annotation"].candidates == ("conn-a",)


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
        assert [c["connection_id"] for c in payload["connections"]] == ["conn-a", "conn-b"]
        assert payload["usage_policy"] == {"monthly_cny_limit": None, "detail_retention_days": 180}
        routes = {r["scenario_id"]: r["candidates"] for r in payload["scenario_routes"]}
        assert routes["stock_analysis"] == ["conn-a"]  # 当前场景用草稿
        assert routes["news_annotation"] == []  # 其余用已提交值

    @pytest.mark.asyncio
    async def test_build_payload_excludes_migration_subdomain(self):
        """F07：迁移子域由 begin/seal/activate 专用命令独占，草稿 payload 不携带。"""
        vm, _ = _make_vm(_snapshot())
        await vm.load()

        payload = vm.build_payload("stock_analysis")

        assert "migration_state" not in payload
        assert "migration_transition" not in payload

    @pytest.mark.asyncio
    async def test_build_payload_without_draft_uses_committed(self):
        vm, _ = _make_vm(_snapshot())
        await vm.load()

        payload = vm.build_payload("stock_analysis")

        routes = {r["scenario_id"]: r["candidates"] for r in payload["scenario_routes"]}
        assert routes["stock_analysis"] == []


# --- F30: 重基（rebase）语义 ---


class TestRebaseDrafts:
    """``_rebase_drafts`` 纯函数：未分叉草稿随新基线、已分叉草稿保留。"""

    def test_undiverged_draft_follows_new_baseline(self):
        old_committed = (ScenarioRouteDraft("s1", candidates=("a",), route_revision=1),)
        new_committed = (ScenarioRouteDraft("s1", candidates=("a",), route_revision=3),)
        rebased = _rebase_drafts(old_committed, old_committed, new_committed)
        # 未分叉 → 采用新基线行（含新 route_revision，消幻影 dirty）
        assert rebased[0] == new_committed[0]

    def test_diverged_draft_is_kept(self):
        old_committed = (ScenarioRouteDraft("s1", candidates=("a",), route_revision=1),)
        new_committed = (ScenarioRouteDraft("s1", candidates=("a",), route_revision=3),)
        diverged = ScenarioRouteDraft("s1", candidates=("a", "b"), route_revision=1)
        rebased = _rebase_drafts((diverged,), old_committed, new_committed)
        assert rebased[0] == diverged  # 已分叉 → 逐字段保留

    def test_divergence_judged_semantically_not_by_revision(self):
        """仅 route_revision 不同的草稿视为未分叉（revision 不参与语义比较，F14 判据）。"""
        old_committed = (ScenarioRouteDraft("s1", candidates=("a",), route_revision=2),)
        draft = ScenarioRouteDraft("s1", candidates=("a",), route_revision=9)
        new_committed = (ScenarioRouteDraft("s1", candidates=("a",), route_revision=4),)
        rebased = _rebase_drafts((draft,), old_committed, new_committed)
        assert rebased[0] == new_committed[0]

    def test_scenario_missing_in_new_committed_keeps_draft(self):
        """新基线缺失对应场景（防御性兜底）：草稿保留。"""
        old_committed = (ScenarioRouteDraft("s1", candidates=("a",)),)
        rebased = _rebase_drafts(old_committed, old_committed, ())
        assert rebased == old_committed


class TestRebaseSnapshot:
    @pytest.mark.asyncio
    async def test_rebase_updates_cas_baseline_and_keeps_diverged_draft(self):
        vm, service = _make_vm(_snapshot(config_revision=5))
        await vm.load()
        vm.select_scenario("stock_analysis")
        vm.add_candidate("conn-a")  # 草稿分叉

        service.get_snapshot = AsyncMock(return_value=_snapshot(config_revision=6))
        ok = await vm.rebase_snapshot()

        assert ok is True
        # CAS 基线前移（经公开 payload 观察，不触私有成员）
        assert vm.build_payload("stock_analysis")["config_revision"] == 6
        # 已分叉草稿保留（连接域提交不丢路由草稿——F30 回归标准）
        assert _route_candidates(vm, "stock_analysis") == ("conn-a",)
        assert vm.state.dirty_counts == (("news_annotation", 0), ("stock_analysis", 1), ("news_risk", 0))

    @pytest.mark.asyncio
    async def test_rebase_undiverged_draft_no_phantom_dirty(self):
        """未分叉草稿随新基线：基线前移后内容等于新基线，不标脏。"""
        vm, service = _make_vm(_snapshot(config_revision=5))
        await vm.load()
        assert vm.state.dirty_counts == (("news_annotation", 0), ("stock_analysis", 0), ("news_risk", 0))

        service.get_snapshot = AsyncMock(return_value=_snapshot(config_revision=6))
        ok = await vm.rebase_snapshot()

        assert ok is True
        assert vm.state.routes == vm.state.committed_routes
        assert all(count == 0 for _, count in vm.state.dirty_counts)

    @pytest.mark.asyncio
    async def test_rebase_resolves_conflict_to_idle(self):
        vm, service = _make_vm(_snapshot(), save_error=ModelConfigurationConflictError(5, 6))
        await vm.load()
        vm.add_candidate("conn-a")
        await vm.save_current_scenario()
        assert vm.state.save_state == "conflict"

        service.get_snapshot = AsyncMock(return_value=_snapshot(config_revision=6))
        ok = await vm.rebase_snapshot()

        assert ok is True
        assert vm.state.save_state == "idle"
        assert vm.state.message is None
        assert _route_candidates(vm, "news_annotation") == ("conn-a",)  # 草稿保留，可直接重试

    @pytest.mark.asyncio
    async def test_rebase_keeps_commit_unknown(self):
        """F31：commit_unknown 回执语义不被重基破坏。"""
        outcome = ConfigurationSaveOutcome(status="commit_unknown", current_revision=5, committed_revision=None)
        vm, service = _make_vm(_snapshot(), save_outcome=outcome)
        await vm.load()
        vm.add_candidate("conn-a")
        await vm.save_current_scenario()
        assert vm.state.save_state == "commit_unknown"

        service.get_snapshot = AsyncMock(return_value=_snapshot(config_revision=6))
        ok = await vm.rebase_snapshot()

        assert ok is True
        assert vm.state.save_state == "commit_unknown"
        assert vm.state.message is not None

    @pytest.mark.asyncio
    async def test_rebase_failure_returns_false_and_keeps_state(self):
        vm, service = _make_vm(_snapshot())
        await vm.load()
        before = vm.state
        service.get_snapshot = AsyncMock(side_effect=RuntimeError("boom"))

        ok = await vm.rebase_snapshot()

        assert ok is False  # 不伪装成功（R21）
        assert vm.state is before  # 状态未被破坏

    @pytest.mark.asyncio
    async def test_rebase_clears_undo_and_refreshes_options(self):
        vm, service = _make_vm(_snapshot())
        await vm.load()
        vm.select_scenario("stock_analysis")
        # 构造 undo：先加候选再移除
        vm.add_candidate("conn-a")
        vm.remove_candidate("conn-a")
        assert vm.state.undo_available is True

        new_snap = _snapshot(
            config_revision=6,
            connections=[_connection_mapping("conn-a", name="主连接", revision=4), _connection_mapping("conn-c")],
        )
        service.get_snapshot = AsyncMock(return_value=new_snap)
        await vm.rebase_snapshot()

        assert vm.state.undo_available is False  # 撤销记录清除（重基后撤销语义失效）
        assert [o.connection_id for o in vm.state.available_connections] == ["conn-a", "conn-c"]  # 候选选择器刷新
        assert vm.state.available_connections[0].connection_revision == 4


class TestCommitSeq:
    """F30：commit_seq 仅 committed 递增（宿主协调触发信号）。"""

    @pytest.mark.asyncio
    async def test_commit_seq_increments_on_committed(self):
        vm, _ = _make_vm(_snapshot(), save_outcome=_committed_outcome())
        await vm.load()
        assert vm.state.commit_seq == 0
        vm.select_scenario("stock_analysis")
        vm.add_candidate("conn-a")

        await vm.save_current_scenario()

        assert vm.state.commit_seq == 1

    @pytest.mark.asyncio
    async def test_commit_seq_unchanged_on_not_committed(self):
        outcome = ConfigurationSaveOutcome(
            status="not_committed", current_revision=7, committed_revision=None, last_config_commit=None
        )
        vm, _ = _make_vm(_snapshot(), save_outcome=outcome)
        await vm.load()
        vm.add_candidate("conn-a")

        await vm.save_current_scenario()

        assert vm.state.save_state == "error"  # not_committed 映射 error
        assert vm.state.commit_seq == 0  # 未提交不触发宿主重基


# --- F13: 场景启用 / 情绪模式 / 向导注入 / 悬空校验 / 一键激活 ---


def _pending_vm(
    *,
    snapshot: ModelManagementSnapshot | None = None,
) -> tuple[ModelRoutesViewModel, MagicMock]:
    """pending 未激活快照的 VM（F13 激活链路预置；service.activate_fresh_setup 可 mock）。"""
    snap = snapshot or _snapshot(migration_state="pending")
    return _make_vm(snap)


def _wizard_option(connection_id: str = "conn-draft", *, name: str = "草稿连接") -> dict:
    return {
        "connection_id": connection_id,
        "name": name,
        "adapter_kind": "litellm_chat",
        "provider": "openai",
        "model": "gpt-x",
        "enabled": True,
        "billing": {"mode": "usage_only", "pricing_unit": "llm_tokens"},
        "connection_revision": 1,
    }


def _option_ids(vm: ModelRoutesViewModel) -> tuple[str, ...]:
    return tuple(o.connection_id for o in vm.state.available_connections)


class TestLoadMigrationMirror:
    @pytest.mark.asyncio
    async def test_load_mirrors_migration_subdomain(self):
        snap = _snapshot(
            migration_state="pending",
            migration_transition={"phase": "draining", "transition_id": "t-1"},
        )
        vm, _ = _make_vm(snap)
        await vm.load()
        assert vm.state.migration_state == "pending"
        assert vm.state.migration_transition_phase == "draining"
        assert vm.state.activation_in_progress is False

    @pytest.mark.asyncio
    async def test_load_without_transition_phase_none(self):
        vm, _ = _make_vm(_snapshot(migration_state="pending"))
        await vm.load()
        assert vm.state.migration_transition_phase is None


class TestSetRouteEnabled:
    @pytest.mark.asyncio
    async def test_toggle_updates_draft_not_committed(self):
        vm, _ = _pending_vm()
        await vm.load()
        vm.set_route_enabled(True)
        assert vm.current_draft().enabled is True
        assert next(r for r in vm.state.committed_routes if r.scenario_id == "news_annotation").enabled is False
        # enabled 不进 dirty 计数（徽标只反映候选差异）
        assert dict(vm.state.dirty_counts)["news_annotation"] == 0

    @pytest.mark.asyncio
    async def test_same_value_noop(self):
        vm, _ = _pending_vm()
        await vm.load()
        vm.set_route_enabled(False)
        assert vm.current_draft().enabled is False

    @pytest.mark.asyncio
    async def test_enabled_included_in_save_revision_bump(self):
        """F13：enabled 语义变化经 _semantic_route_for_save 在保存时递增 route_revision。"""
        vm, _ = _pending_vm()
        await vm.load()
        vm.set_route_enabled(True)
        payload = vm.build_payload("news_annotation")
        route = next(r for r in payload["scenario_routes"] if r["scenario_id"] == "news_annotation")
        assert route["enabled"] is True
        assert route["route_revision"] == 2  # 默认 committed revision=1，语义变化 +1


class TestSetSentimentMode:
    @pytest.mark.asyncio
    async def test_switch_mode_updates_draft(self):
        vm, _ = _pending_vm()
        await vm.load()
        vm.set_sentiment_mode("dedicated")
        assert vm.current_draft().sentiment_mode == "dedicated"

    @pytest.mark.asyncio
    async def test_non_news_scenario_noop(self):
        vm, _ = _pending_vm()
        await vm.load()
        vm.select_scenario("stock_analysis")
        vm.set_sentiment_mode("dedicated")
        route = next(r for r in vm.state.routes if r.scenario_id == "stock_analysis")
        assert route.sentiment_mode == "bundled"  # 默认值不动

    @pytest.mark.asyncio
    async def test_invalid_mode_noop(self):
        vm, _ = _pending_vm()
        await vm.load()
        vm.set_sentiment_mode("turbo")
        assert vm.current_draft().sentiment_mode == "bundled"


class TestAttachInWizardOption:
    @pytest.mark.asyncio
    async def test_append_then_clear(self):
        vm, _ = _pending_vm()
        await vm.load()
        vm.attach_in_wizard_option(_wizard_option())
        assert "conn-draft" in _option_ids(vm)
        vm.attach_in_wizard_option(None)
        assert "conn-draft" not in _option_ids(vm)
        assert vm.state.available_connections == vm.state.available_connections  # 快照选项不受影响

    @pytest.mark.asyncio
    async def test_clear_without_prior_noop(self):
        vm, _ = _pending_vm()
        await vm.load()
        before = vm.state.available_connections
        vm.attach_in_wizard_option(None)
        assert vm.state.available_connections == before

    @pytest.mark.asyncio
    async def test_reinject_switch_replaces_prior(self):
        vm, _ = _pending_vm()
        await vm.load()
        vm.attach_in_wizard_option(_wizard_option("conn-d1"))
        vm.attach_in_wizard_option(_wizard_option("conn-d2"))
        ids = _option_ids(vm)
        assert "conn-d1" not in ids
        assert "conn-d2" in ids

    @pytest.mark.asyncio
    async def test_same_id_refresh_keeps_shape_updated(self):
        vm, _ = _pending_vm()
        await vm.load()
        vm.attach_in_wizard_option(_wizard_option("conn-d1", name="旧名"))
        vm.attach_in_wizard_option(_wizard_option("conn-d1", name="新名"))
        ids = _option_ids(vm)
        assert ids.count("conn-d1") == 1
        option = next(o for o in vm.state.available_connections if o.connection_id == "conn-d1")
        assert option.name == "新名"

    @pytest.mark.asyncio
    async def test_shadow_restore_for_existing_snapshot_id(self):
        """注入与快照同 id（编辑已有连接）→ 清除注入恢复快照形态，不误删选项。"""
        vm, _ = _pending_vm()
        await vm.load()
        original = next(o for o in vm.state.available_connections if o.connection_id == "conn-b")
        vm.attach_in_wizard_option(_wizard_option("conn-b", name="编辑中"))
        injected = next(o for o in vm.state.available_connections if o.connection_id == "conn-b")
        assert injected.name == "编辑中"
        vm.attach_in_wizard_option(None)
        restored = next(o for o in vm.state.available_connections if o.connection_id == "conn-b")
        assert restored == original

    @pytest.mark.asyncio
    async def test_load_resets_injection(self):
        vm, _ = _pending_vm()
        await vm.load()
        vm.attach_in_wizard_option(_wizard_option())
        await vm.load()
        assert "conn-draft" not in _option_ids(vm)


class TestUnresolvedCandidates:
    @pytest.mark.asyncio
    async def test_filters_known_and_dedupes_preserving_order(self):
        vm, _ = _pending_vm(
            snapshot=_snapshot(
                migration_state="pending",
                routes=[
                    _route_mapping("news_annotation", ("ghost-1", "conn-a")),
                    _route_mapping("stock_analysis", ("conn-a", "ghost-1", "ghost-2")),
                    _route_mapping("news_risk", ()),
                ],
            )
        )
        await vm.load()
        unresolved = vm.unresolved_candidates(["conn-a", "conn-b"])
        assert unresolved == ("ghost-1", "ghost-2")

    @pytest.mark.asyncio
    async def test_includes_disabled_scene_candidates(self):
        """禁用场景的悬空候选同样检出（启用时阻断执行，保存前一律拦截）。"""
        vm, _ = _pending_vm(
            snapshot=_snapshot(
                migration_state="pending",
                routes=[
                    _route_mapping("news_annotation", ()),
                    _route_mapping("stock_analysis", ("ghost-x",), enabled=False),
                    _route_mapping("news_risk", ()),
                ],
            )
        )
        await vm.load()
        assert vm.unresolved_candidates([]) == ("ghost-x",)


class TestActivateFreshSetup:
    @pytest.mark.asyncio
    async def test_already_active_returns_true_without_calling_service(self):
        vm, service = _make_vm(_snapshot())  # 默认 active
        await vm.load()
        service.activate_fresh_setup = AsyncMock()
        assert await vm.activate_fresh_setup() is True
        service.activate_fresh_setup.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_enabled_scene_silent_false(self):
        vm, service = _pending_vm()
        await vm.load()
        service.activate_fresh_setup = AsyncMock()
        assert await vm.activate_fresh_setup() is False
        assert vm.state.message is None  # 「暂不用 AI」合法静默，不告警
        service.activate_fresh_setup.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sealed_barrier_passes_through_to_service(self):
        """sealed 屏障态放行 service（编排断点续走 activate 自愈，不在 VM 拦截）。"""
        vm, service = _pending_vm(
            snapshot=_snapshot(
                migration_state="pending",
                migration_transition={"phase": "sealed", "transition_id": "t-1"},
            )
        )
        await vm.load()
        vm.add_candidate("conn-a")
        vm.set_route_enabled(True)
        service.activate_fresh_setup = AsyncMock(
            return_value=ConfigurationSaveOutcome(status="committed", current_revision=6, committed_revision=6)
        )
        assert await vm.activate_fresh_setup() is True
        service.activate_fresh_setup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_committed_reload_and_true(self):
        vm, service = _pending_vm()
        await vm.load()
        vm.add_candidate("conn-a")
        vm.set_route_enabled(True)
        service.activate_fresh_setup = AsyncMock(
            return_value=ConfigurationSaveOutcome(status="committed", current_revision=6, committed_revision=6)
        )
        assert await vm.activate_fresh_setup() is True
        service.activate_fresh_setup.assert_awaited_once()
        assert vm.state.migration_state == "pending"  # load 重读 mock 快照（仍 pending，无碍激活成功语义）
        assert vm.state.activation_in_progress is False

    @pytest.mark.asyncio
    async def test_conflict_false_with_message(self):
        vm, service = _pending_vm()
        await vm.load()
        vm.add_candidate("conn-a")
        vm.set_route_enabled(True)
        service.activate_fresh_setup = AsyncMock(side_effect=ModelConfigurationConflictError(5, 6))
        assert await vm.activate_fresh_setup() is False
        assert vm.state.message is not None and vm.state.message.key == "model_routes_activation_conflict"
        assert vm.state.activation_in_progress is False

    @pytest.mark.asyncio
    async def test_transition_error_false_with_message(self):
        from core.model_management_types import MigrationTransitionError

        vm, service = _pending_vm()
        await vm.load()
        vm.add_candidate("conn-a")
        vm.set_route_enabled(True)
        service.activate_fresh_setup = AsyncMock(side_effect=MigrationTransitionError("blocked"))
        assert await vm.activate_fresh_setup() is False
        assert vm.state.message is not None and vm.state.message.key == "model_routes_activation_blocked_transition"

    @pytest.mark.asyncio
    async def test_generic_error_false_with_message(self):
        vm, service = _pending_vm()
        await vm.load()
        vm.add_candidate("conn-a")
        vm.set_route_enabled(True)
        service.activate_fresh_setup = AsyncMock(side_effect=RuntimeError("boom"))
        assert await vm.activate_fresh_setup() is False
        assert vm.state.message is not None and vm.state.message.key == "model_routes_activation_failed"

    @pytest.mark.asyncio
    async def test_not_committed_false_with_message(self):
        vm, service = _pending_vm()
        await vm.load()
        vm.add_candidate("conn-a")
        vm.set_route_enabled(True)
        service.activate_fresh_setup = AsyncMock(
            return_value=ConfigurationSaveOutcome(status="not_committed", current_revision=5)
        )
        assert await vm.activate_fresh_setup() is False
        assert vm.state.message is not None and vm.state.message.key == "model_routes_save_not_committed"
