"""研究工作台四视角 ViewModel 与只读投影（W-18）。

设计边界：

- **纯视图状态归 VM**：当前视角、各视角的输入草稿（概览查询、证据选中、结论备注）
  与视角选择一起保存在不可变 state 中——视角切换只换渲染分支，草稿/选中不丢
  （「视角切换保留现场」）。
- **数据只读投影**：四视角事实来自既有 ``ResearchObserverState`` 快照，本模块只做
  纯函数投影，不派发研究、不写配置、不触发 IO。
- **空态与失败态显式**：数据未接线（``available=False``）、读取失败（``error_key``）
  与「有数据源但当前视角为空」分别用不同 key 表达，不用空表冒充「没问题」（R21）。

MVVM 契约：不 import flet、不感知 locale（只产出 i18n key）。
"""

from __future__ import annotations

from dataclasses import dataclass

from ui.viewmodels.observable_mixin import ObservableViewModelMixin
from ui.viewmodels.research_observer_view_model import ResearchObserverState

# --- 四视角 ---
PERSPECTIVE_OVERVIEW = "overview"
PERSPECTIVE_EVIDENCE = "evidence"
PERSPECTIVE_CONCLUSION = "conclusion"
PERSPECTIVE_CONFIG = "config"
PERSPECTIVE_ORDER: tuple[str, ...] = (
    PERSPECTIVE_OVERVIEW,
    PERSPECTIVE_EVIDENCE,
    PERSPECTIVE_CONCLUSION,
    PERSPECTIVE_CONFIG,
)
PERSPECTIVE_TITLE_KEYS: dict[str, str] = {
    PERSPECTIVE_OVERVIEW: "agent_workbench_tab_overview",
    PERSPECTIVE_EVIDENCE: "agent_workbench_tab_evidence",
    PERSPECTIVE_CONCLUSION: "agent_workbench_tab_conclusion",
    PERSPECTIVE_CONFIG: "agent_workbench_tab_config",
}
PERSPECTIVE_EMPTY_KEYS: dict[str, str] = {
    PERSPECTIVE_OVERVIEW: "agent_workbench_empty_overview",
    PERSPECTIVE_EVIDENCE: "agent_workbench_empty_evidence",
    PERSPECTIVE_CONCLUSION: "agent_workbench_empty_conclusion",
    PERSPECTIVE_CONFIG: "agent_workbench_empty_config",
}

VALUE_MISSING_KEY = "agent_workbench_value_missing"
#: 研究配置统一入口的位置提示（工作台配置视角只指路，不复制第二份配置值）。
CONFIG_ENTRY_HINT_KEY = "agent_workbench_config_entry_hint"


def _literal(value: str | None) -> str | None:
    """非空文本才作为字面值；空串/None 视为未提供（R21，不填占位业务值）。"""
    return value if isinstance(value, str) and value.strip() else None


@dataclass(frozen=True)
class WorkbenchFact:
    """单条事实：``value_key``（i18n 值）或 ``literal``（原样值）二选一。"""

    label_key: str
    value_key: str | None = None
    literal: str | None = None

    @property
    def missing(self) -> bool:
        return self.value_key is None and self.literal is None


@dataclass(frozen=True)
class WorkbenchRow:
    """可点选的证据行（子任务节点 / 成果）。"""

    row_id: str
    title: str
    subtitle: str | None = None
    selected: bool = False


@dataclass(frozen=True)
class WorkbenchPerspectiveView:
    """单个视角的只读投影。

    ``unavailable_key`` / ``error_key`` / ``empty_key`` 三态互斥且显式：
    未接线、读取失败、确实为空，界面据此分别呈现，不混成一个空表。
    """

    perspective: str
    facts: tuple[WorkbenchFact, ...] = ()
    rows: tuple[WorkbenchRow, ...] = ()
    empty_key: str | None = None
    unavailable_key: str | None = None
    error_key: str | None = None


@dataclass(frozen=True)
class ResearchWorkbenchState:
    """工作台不可变 state snapshot（视角 + 各视角草稿）。"""

    perspective: str = PERSPECTIVE_OVERVIEW
    overview_query: str = ""
    evidence_selected_id: str | None = None
    conclusion_note: str = ""


class ResearchWorkbenchViewModel(ObservableViewModelMixin[ResearchWorkbenchState]):
    """四视角工作台的纯视图状态 VM（无 IO、无服务依赖）。"""

    def __init__(self) -> None:
        self._state = ResearchWorkbenchState()
        self._init_mixin_fields()

    # --- 视角切换：只改当前视角，其它视角草稿原样保留 ---
    def set_perspective(self, perspective: str) -> None:
        if perspective not in PERSPECTIVE_ORDER or perspective == self._state.perspective:
            return
        self._set_state(perspective=perspective)

    # --- 概览：查询草稿（`＋指令与引用` 菜单以 token 追加，不重复实现选择器） ---
    def set_overview_query(self, value: str) -> None:
        self._set_state(overview_query=value)

    def insert_query_token(self, token: str) -> None:
        """把菜单 token 追加到查询草稿（已存在时不重复追加）。"""
        if not token:
            return
        current = self._state.overview_query
        if current.endswith(token):
            return
        separator = "" if not current or current.endswith(" ") else " "
        self._set_state(overview_query=f"{current}{separator}{token}")

    # --- 证据：选中的取证对象（切走后仍保留） ---
    def select_evidence(self, row_id: str | None) -> None:
        selected = row_id if row_id else None
        if selected == self._state.evidence_selected_id:
            return
        self._set_state(evidence_selected_id=selected)

    # --- 结论：备注草稿（切走后仍保留） ---
    def set_conclusion_note(self, value: str) -> None:
        self._set_state(conclusion_note=value)


# ---------------------------------------------------------------------------
# 只读投影
# ---------------------------------------------------------------------------


def _unavailable_view(perspective: str, source: ResearchObserverState) -> WorkbenchPerspectiveView:
    return WorkbenchPerspectiveView(
        perspective=perspective,
        unavailable_key=source.unavailable_reason_key or "agent_workbench_unavailable",
        error_key=source.error_key,
    )


def _overview_view(source: ResearchObserverState) -> WorkbenchPerspectiveView:
    facts = (
        WorkbenchFact("agent_workbench_fact_session_title", literal=_literal(source.session_title)),
        WorkbenchFact("agent_workbench_fact_session_id", literal=_literal(source.session_id)),
        WorkbenchFact("agent_workbench_fact_updated", literal=_literal(source.last_updated_at)),
        WorkbenchFact("agent_workbench_fact_pending", literal=str(source.pending_count)),
        WorkbenchFact("agent_workbench_fact_messages", literal=str(len(source.messages))),
        WorkbenchFact("agent_workbench_fact_artifacts", literal=str(len(source.artifacts))),
        WorkbenchFact("agent_workbench_fact_executions", literal=str(len(source.execution_nodes))),
    )
    has_session_fact = any(fact.literal is not None for fact in facts[:3])
    has_content = has_session_fact or bool(source.messages) or bool(source.artifacts) or bool(source.execution_nodes)
    return WorkbenchPerspectiveView(
        perspective=PERSPECTIVE_OVERVIEW,
        facts=facts,
        empty_key=None if has_content else PERSPECTIVE_EMPTY_KEYS[PERSPECTIVE_OVERVIEW],
        error_key=source.error_key,
    )


def _evidence_view(source: ResearchObserverState, selected_id: str | None) -> WorkbenchPerspectiveView:
    rows: list[WorkbenchRow] = []
    for node in source.execution_nodes:
        rows.append(
            WorkbenchRow(
                row_id=node.execution_id,
                title=node.execution_id,
                subtitle=(
                    f"state={node.display_state} goal={node.goal_coverage} "
                    f"model={node.model_attempt_count} tool={node.tool_call_count} active_ms={node.active_ms}"
                ),
                selected=node.execution_id == selected_id,
            )
        )
    for artifact in source.artifacts:
        row_id = f"artifact:{artifact.artifact_id}:{artifact.artifact_version}"
        summary = _literal(artifact.summary)
        rows.append(
            WorkbenchRow(
                row_id=row_id,
                title=f"{artifact.artifact_kind} v{artifact.artifact_version}",
                subtitle=summary or artifact.execution_id,
                selected=row_id == selected_id,
            )
        )
    return WorkbenchPerspectiveView(
        perspective=PERSPECTIVE_EVIDENCE,
        rows=tuple(rows),
        empty_key=None if rows else PERSPECTIVE_EMPTY_KEYS[PERSPECTIVE_EVIDENCE],
        error_key=source.error_key,
    )


def _conclusion_view(source: ResearchObserverState) -> WorkbenchPerspectiveView:
    facts = (
        WorkbenchFact("agent_workbench_fact_answer", literal=_literal(source.last_answer_text)),
        WorkbenchFact("agent_workbench_fact_delivery", literal=_literal(source.last_answer_delivery_state)),
    )
    rows = tuple(
        WorkbenchRow(
            row_id=f"conclusion:{artifact.artifact_id}:{artifact.artifact_version}",
            title=artifact.summary or artifact.artifact_kind,
            subtitle=artifact.published_at,
        )
        for artifact in source.artifacts
        if _literal(artifact.summary) is not None
    )
    has_content = facts[0].literal is not None or bool(rows)
    return WorkbenchPerspectiveView(
        perspective=PERSPECTIVE_CONCLUSION,
        facts=facts,
        rows=rows,
        empty_key=None if has_content else PERSPECTIVE_EMPTY_KEYS[PERSPECTIVE_CONCLUSION],
        error_key=source.error_key,
    )


def _config_view(source: ResearchObserverState) -> WorkbenchPerspectiveView:
    model_choice = source.model_selection_frozen or source.model_selection
    model_option = source.model_option(model_choice) if model_choice else None
    role_choice = source.role_selection_frozen or source.role_selection
    role_option = source.role_option(role_choice) if role_choice else None

    budget_fact = (
        WorkbenchFact("agent_workbench_fact_budget", value_key=source.budget.state_key)
        if source.budget is not None
        else WorkbenchFact("agent_workbench_fact_budget")
    )
    round_fact = (
        WorkbenchFact("agent_workbench_fact_cost_round", value_key=source.usage_round.state_key)
        if source.usage_round is not None
        else WorkbenchFact("agent_workbench_fact_cost_round")
    )
    cumulative_fact = (
        WorkbenchFact("agent_workbench_fact_cost_cumulative", value_key=source.usage_cumulative.state_key)
        if source.usage_cumulative is not None
        else WorkbenchFact("agent_workbench_fact_cost_cumulative")
    )
    facts = (
        WorkbenchFact(
            "agent_workbench_fact_model",
            literal=model_option.display_name if model_option is not None else _literal(model_choice),
        ),
        WorkbenchFact(
            "agent_workbench_fact_role",
            value_key=role_option.name_key if role_option is not None else None,
            literal=None if role_option is not None else _literal(role_choice),
        ),
        budget_fact,
        round_fact,
        cumulative_fact,
    )
    has_content = any(fact.missing is False for fact in facts)
    return WorkbenchPerspectiveView(
        perspective=PERSPECTIVE_CONFIG,
        facts=facts,
        empty_key=None if has_content else PERSPECTIVE_EMPTY_KEYS[PERSPECTIVE_CONFIG],
        error_key=source.error_key,
    )


def build_perspective_view(
    source: ResearchObserverState,
    perspective: str,
    *,
    selected_id: str | None = None,
) -> WorkbenchPerspectiveView:
    """按视角生成只读投影；未接线时四视角统一显式呈现「未接线原因 + 错误」。"""
    if perspective not in PERSPECTIVE_ORDER:
        perspective = PERSPECTIVE_OVERVIEW
    if not source.available:
        return _unavailable_view(perspective, source)
    if perspective == PERSPECTIVE_EVIDENCE:
        return _evidence_view(source, selected_id)
    if perspective == PERSPECTIVE_CONCLUSION:
        return _conclusion_view(source)
    if perspective == PERSPECTIVE_CONFIG:
        return _config_view(source)
    return _overview_view(source)


def build_all_perspective_views(
    source: ResearchObserverState,
    *,
    selected_id: str | None = None,
) -> tuple[WorkbenchPerspectiveView, ...]:
    """四视角一次性投影（供 View 以固定顺序渲染，不重复计算）。"""
    return tuple(
        build_perspective_view(source, perspective, selected_id=selected_id) for perspective in PERSPECTIVE_ORDER
    )
