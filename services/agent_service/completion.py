"""研究完成核定（F04）：叶事实汇总 → 对象级缺口 → 组级结论。

``PlanRunSummary`` 只报叶事实，``leaves_all_succeeded`` 不代表目标已覆盖；本模块
把叶终态事实转成逐对象缺口，并与成果保存/目标覆盖/执行结束三项事实合并为
:class:`ResearchCompletionAssessment`（保存侧经 ``ResearchResult.object_gaps`` 落库）。

边界（R21）：失败/终止/结束/未确认的叶都保留为对象级缺口，未终止叶不伪造结论；
零匹配只有调用方以 ``goal_coverage="full"`` 且无缺口表达时才可判完整。
"""

from __future__ import annotations

from core.agent_types import (
    ResearchCompletionAssessment,
    ResearchObjectGap,
    assess_research_completion,
)
from services.agent_service.workflows import PlanRunSummary

#: 叶状态 → 对象级缺口类型（无成果/结果未确认/部分数据）。
_LEAF_GAP_KINDS: dict[str, str] = {
    "partial": "missing_data",
    "failed": "no_result",
    "withdrawn": "no_result",
    "ended": "no_result",
    "closed_unknown": "result_unconfirmed",
}


def object_gaps_from_leaf_summary(summary: PlanRunSummary) -> tuple[ResearchObjectGap, ...]:
    """叶事实汇总 → 逐对象缺口（按计划/叶登记顺序，稳定可重放）。

    - ``complete`` 叶不产生缺口（其成果保存事实由成果合同另行核定）；
    - ``partial`` → ``missing_data``；``failed``/``withdrawn``/``ended`` → ``no_result``；
    - ``closed_unknown`` 与其他非终态叶事实 → ``result_unconfirmed``（未知不等于失败）；
    - 因上游失败/输入变更被阻断的叶 → ``missing_object``（对象未被执行，原因保留）。
    """

    reasons = dict(summary.block_reasons)
    gaps: list[ResearchObjectGap] = []
    seen: set[str] = set()
    for outcome in summary.outcomes:
        if outcome.node_id in seen or outcome.state == "complete":
            continue
        gap_kind = _LEAF_GAP_KINDS.get(outcome.state, "result_unconfirmed")
        seen.add(outcome.node_id)
        detail = f"leaf_state={outcome.state}"
        if outcome.error_code is not None:
            detail = f"{detail};error_code={outcome.error_code}"
        gaps.append(ResearchObjectGap(object_ref=outcome.node_id, gap_kind=gap_kind, detail=detail))
    for node_id in summary.blocked:
        if node_id in seen:
            continue
        seen.add(node_id)
        gaps.append(
            ResearchObjectGap(
                object_ref=node_id,
                gap_kind="missing_object",
                detail=f"blocked:{reasons.get(node_id, 'blocked_without_reason')}",
            )
        )
    for node_id in summary.pending:
        if node_id in seen:
            continue
        seen.add(node_id)
        gaps.append(
            ResearchObjectGap(
                object_ref=node_id,
                gap_kind="result_unconfirmed",
                detail="no_terminal_fact",
            )
        )
    return tuple(gaps)


def assess_plan_completion(
    summary: PlanRunSummary,
    *,
    execution_state: str,
    final_output_saved: bool,
    goal_coverage: str,
    convergence_confirmed: bool,
    missing_fields: tuple[str, ...] = (),
    data_warnings: tuple[str, ...] = (),
) -> ResearchCompletionAssessment:
    """叶事实 + 三项成果事实 → 组级完成结论（完整/部分/失败/未知）。

    不以成功叶数量代替目标覆盖：任何未完成/未确认的叶都成为对象级缺口，使声明
    ``full`` 的有效覆盖降级 ``partial``；任一事实未核定则结论为 ``unknown``。
    """

    if summary.pending:
        # 仍有未终止叶：本批推进结束不等于研究结束（不推断成功或失败）。
        effective_state = "unknown"
    else:
        effective_state = execution_state
    return assess_research_completion(
        execution_state=effective_state,
        final_output_saved=final_output_saved,
        goal_coverage=goal_coverage,
        convergence_confirmed=convergence_confirmed,
        object_gaps=object_gaps_from_leaf_summary(summary),
        missing_fields=missing_fields,
        data_warnings=data_warnings,
    )
