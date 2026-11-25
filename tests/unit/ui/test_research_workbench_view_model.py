"""研究工作台四视角 ViewModel / 投影单测（W-18）。

覆盖：
- 视角切换保留各视角现场（查询草稿 / 证据选中 / 结论备注）；
- 非法视角忽略、token 追加去重；
- 未接线 / 读取失败 / 空态三态显式区分；
- 概览 / 证据 / 结论 / 配置四视角投影字段。
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from ui.viewmodels.research_observer_view_model import (
    BudgetEstimate,
    CostUsageView,
    ExecutionNodeView,
    ModelConnectionOption,
    ResearchObserverState,
    RoleOption,
    SessionArtifactView,
)
from ui.viewmodels.research_workbench_view_model import (
    PERSPECTIVE_CONFIG,
    PERSPECTIVE_CONCLUSION,
    PERSPECTIVE_EVIDENCE,
    PERSPECTIVE_OVERVIEW,
    ResearchWorkbenchState,
    ResearchWorkbenchViewModel,
    build_all_perspective_views,
    build_perspective_view,
)

pytestmark = pytest.mark.unit


def _artifact() -> SessionArtifactView:
    return SessionArtifactView(
        artifact_id="a1",
        artifact_version=2,
        artifact_kind="report",
        execution_id="e1",
        round_id="r1",
        summary="银行息差承压",
        body_text=None,
        body_available=False,
        published_at="2026-11-24T10:00:00",
    )


def _execution() -> ExecutionNodeView:
    return ExecutionNodeView(
        execution_id="e1",
        parent_execution_id=None,
        display_state="done",
        goal_coverage="covered",
        model_attempt_count=2,
        tool_call_count=3,
        active_ms=1500,
        depth=0,
        has_children=False,
    )


def _rich_state() -> ResearchObserverState:
    return ResearchObserverState(
        available=True,
        session_id="s1",
        session_title="银行研究",
        last_updated_at="2026-11-24T10:00:00",
        pending_count=2,
        execution_nodes=(_execution(),),
        artifacts=(_artifact(),),
        last_answer_text="结论正文",
        last_answer_delivery_state="delivered",
        model_options=(
            ModelConnectionOption(
                connection_id="c1",
                display_name="主连接",
                model="m1",
                context_window=128000,
                position=1,
                effective=True,
                reject_key=None,
            ),
        ),
        role_options=(
            RoleOption(role_key="main", name_key="settings_ai_role_name_main", version="v1", reject_key=None),
        ),
        budget=BudgetEstimate(
            state="estimated",
            state_key="agent_research_budget_estimated",
            amount_cny=None,
            unit_key="unit_cny",
            basis_key=None,
            input_tokens=10,
            output_reserve_tokens=5,
            tokens_verified=True,
        ),
        usage_round=CostUsageView(
            state="known",
            state_key="agent_research_cost_known",
            known_cny=None,
            reserved_cny=None,
            unpriced_attempts=None,
            truncated=False,
            read_error=None,
        ),
    )


class TestPerspectiveStatePreserved:
    def test_switching_keeps_other_perspective_drafts(self) -> None:
        vm = ResearchWorkbenchViewModel()
        vm.set_overview_query("银行股")
        vm.select_evidence("e1")
        vm.set_conclusion_note("关注息差")

        vm.set_perspective(PERSPECTIVE_EVIDENCE)
        assert vm.state.perspective == PERSPECTIVE_EVIDENCE
        assert vm.state.overview_query == "银行股"
        assert vm.state.evidence_selected_id == "e1"
        assert vm.state.conclusion_note == "关注息差"

        vm.set_perspective(PERSPECTIVE_OVERVIEW)
        assert vm.state.perspective == PERSPECTIVE_OVERVIEW
        assert vm.state.overview_query == "银行股"
        assert vm.state.evidence_selected_id == "e1"

    def test_invalid_perspective_ignored(self) -> None:
        vm = ResearchWorkbenchViewModel()
        vm.set_perspective("nope")
        assert vm.state.perspective == PERSPECTIVE_OVERVIEW

    def test_insert_token_appends_once(self) -> None:
        vm = ResearchWorkbenchViewModel()
        vm.insert_query_token("/")
        vm.insert_query_token("/")
        assert vm.state.overview_query == "/"
        vm.insert_query_token("@")
        assert vm.state.overview_query == "/ @"
        vm.insert_query_token("")
        assert vm.state.overview_query == "/ @"

    def test_selection_clear(self) -> None:
        vm = ResearchWorkbenchViewModel()
        vm.select_evidence("e1")
        vm.select_evidence(None)
        assert vm.state.evidence_selected_id is None


class TestProjection:
    def test_unavailable_is_explicit_for_all_perspectives(self) -> None:
        state = ResearchObserverState(
            available=False,
            unavailable_reason_key="agent_research_source_unavailable",
            error_key="agent_research_refresh_failed",
        )

        views = build_all_perspective_views(state)

        assert {view.perspective for view in views} == {
            PERSPECTIVE_OVERVIEW,
            PERSPECTIVE_EVIDENCE,
            PERSPECTIVE_CONCLUSION,
            PERSPECTIVE_CONFIG,
        }
        assert all(view.unavailable_key == "agent_research_source_unavailable" for view in views)
        assert all(view.error_key == "agent_research_refresh_failed" for view in views)
        assert all(view.empty_key is None for view in views)

    def test_empty_state_distinguishes_from_unavailable(self) -> None:
        state = ResearchObserverState(available=True)

        overview = build_perspective_view(state, PERSPECTIVE_OVERVIEW)
        evidence = build_perspective_view(state, PERSPECTIVE_EVIDENCE)
        conclusion = build_perspective_view(state, PERSPECTIVE_CONCLUSION)

        assert overview.empty_key == "agent_workbench_empty_overview"
        assert evidence.empty_key == "agent_workbench_empty_evidence"
        assert conclusion.empty_key == "agent_workbench_empty_conclusion"
        assert overview.unavailable_key is None

    def test_overview_facts(self) -> None:
        view = build_perspective_view(_rich_state(), PERSPECTIVE_OVERVIEW)
        facts = {fact.label_key: fact for fact in view.facts}

        assert facts["agent_workbench_fact_session_title"].literal == "银行研究"
        assert facts["agent_workbench_fact_pending"].literal == "2"
        assert facts["agent_workbench_fact_messages"].literal == "0"
        assert facts["agent_workbench_fact_artifacts"].literal == "1"
        assert facts["agent_workbench_fact_executions"].literal == "1"
        assert view.empty_key is None

    def test_evidence_rows_and_selection(self) -> None:
        view = build_perspective_view(_rich_state(), PERSPECTIVE_EVIDENCE, selected_id="e1")

        assert [row.row_id for row in view.rows] == ["e1", "artifact:a1:2"]
        assert view.rows[0].selected is True
        assert view.rows[1].selected is False
        assert view.empty_key is None

    def test_conclusion_facts_and_rows(self) -> None:
        view = build_perspective_view(_rich_state(), PERSPECTIVE_CONCLUSION)
        facts = {fact.label_key: fact for fact in view.facts}

        assert facts["agent_workbench_fact_answer"].literal == "结论正文"
        assert facts["agent_workbench_fact_delivery"].literal == "delivered"
        assert len(view.rows) == 1
        assert view.empty_key is None

    def test_config_facts_use_runtime_values(self) -> None:
        state = replace(_rich_state(), model_selection="c1", role_selection="main")

        view = build_perspective_view(state, PERSPECTIVE_CONFIG)
        facts = {fact.label_key: fact for fact in view.facts}

        assert facts["agent_workbench_fact_model"].literal == "主连接"
        assert facts["agent_workbench_fact_role"].value_key == "settings_ai_role_name_main"
        assert facts["agent_workbench_fact_budget"].value_key == "agent_research_budget_estimated"
        assert facts["agent_workbench_fact_cost_round"].value_key == "agent_research_cost_known"
        assert view.empty_key is None, "有配置读数时不得再呈现空态文案"

    def test_config_empty_only_when_all_values_missing(self) -> None:
        view = build_perspective_view(ResearchObserverState(available=True), PERSPECTIVE_CONFIG)

        assert view.empty_key == "agent_workbench_empty_config"
        assert all(fact.missing for fact in view.facts)

    def test_unknown_perspective_falls_back_to_overview(self) -> None:
        view = build_perspective_view(_rich_state(), "nope")
        assert view.perspective == PERSPECTIVE_OVERVIEW

    def test_default_state_is_overview(self) -> None:
        assert ResearchWorkbenchState().perspective == PERSPECTIVE_OVERVIEW
