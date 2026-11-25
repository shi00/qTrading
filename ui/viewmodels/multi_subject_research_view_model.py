"""MultiSubjectResearchViewModel — 多标的多选与结构化对照（J05、J04、J06）。

边界（CLAUDE.md §3.2 MVVM / docs/patterns/mvvm.md）：

- VM 只持有选择与对照状态，不 import flet、不感知 locale（只产出 i18n key）；
- 选择按 ``object_kind:object_id`` 稳定身份去重，超上限不静默丢弃而是记入
  ``excluded_keys`` 并在提交后以「缺失」显式列出（R21）；
- 提交只把所选标的交给宿主准备研究（宿主沿用既有单对象草稿路径），本 VM 不发送、
  不派发 workflow、不产生费用；
- 对照骨架由服务层 ``build_pending_subject_comparison`` 生成：逐标的 ``pending``，
  任一家未纳入/准备失败即批次 ``partial`` 并列出缺失对象，绝不把「只回来一家」
  呈现为单对象完整结论。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ui.viewmodels.observable_mixin import ObservableViewModelMixin

if TYPE_CHECKING:
    from services.agent_service.workflows import SubjectComparison

#: 选择上限（与服务层 ``MULTI_SUBJECT_LIMIT`` 同源；VM 不 import 服务层运行期模块）。
MULTI_SUBJECT_LIMIT = 10

#: 提示 key。
NOTICE_LIMIT_REACHED = "agent_multi_subject_limit_reached"
NOTICE_SELECT_ALL_TRUNCATED = "agent_multi_subject_limit_truncated"
NOTICE_NO_SELECTION = "agent_multi_subject_no_selection"
NOTICE_SUBMITTED = "agent_multi_subject_submitted"
NOTICE_PARTIAL = "agent_multi_subject_partial"

#: 不可选/缺失原因（与服务层同值域）。
MISSING_REASON_IDENTITY = "missing_identity"
MISSING_REASON_OVER_LIMIT = "over_limit"


@dataclass(frozen=True)
class SubjectCandidate:
    """一个可参与多选研究的标的。

    ``object_id`` 为空表示缺稳定身份（无 ``ts_code`` / ``news_id``）：仍作为候选
    呈现但不可选，提交时显式计入缺失（不静默消失，R21）。
    """

    object_kind: str
    object_id: str
    display_name: str
    missing_reason: str | None = None

    @property
    def key(self) -> str:
        if self.object_id:
            return f"{self.object_kind}:{self.object_id}"
        return f"missing:{self.object_kind}:{self.display_name}"

    @property
    def selectable(self) -> bool:
        return bool(self.object_id) and self.missing_reason is None


@dataclass(frozen=True)
class MultiSubjectResearchState:
    """不可变快照：``notice_key`` 为 i18n key，``comparison`` 为服务层对照结果。"""

    open: bool = False
    candidates: tuple[SubjectCandidate, ...] = ()
    selected_keys: tuple[str, ...] = ()
    excluded_keys: tuple[str, ...] = ()
    limit: int = MULTI_SUBJECT_LIMIT
    notice_key: str | None = None
    submitted: bool = False
    comparison: SubjectComparison | None = None


def _dedupe_candidates(candidates: Iterable[SubjectCandidate]) -> tuple[SubjectCandidate, ...]:
    """按稳定 key 去重（保留首次出现顺序）；身份缺失的候选仍保留。"""

    seen: set[str] = set()
    ordered: list[SubjectCandidate] = []
    for candidate in candidates:
        key = candidate.key
        if key in seen:
            continue
        seen.add(key)
        ordered.append(candidate)
    return tuple(ordered)


class MultiSubjectResearchViewModel(ObservableViewModelMixin[MultiSubjectResearchState]):
    """多标的多选 + 对照视图的唯一 UI 命令入口。"""

    def __init__(self, *, limit: int = MULTI_SUBJECT_LIMIT) -> None:
        if limit < 1:
            raise ValueError("limit must be positive")
        self._state = MultiSubjectResearchState(limit=limit)
        self._init_mixin_fields()

    def open_candidates(self, candidates: Iterable[SubjectCandidate]) -> None:
        """打开选择面板：候选去重后呈现，清空上一批选择与对照。"""

        self._set_state(
            open=True,
            candidates=_dedupe_candidates(candidates),
            selected_keys=(),
            excluded_keys=(),
            notice_key=None,
            submitted=False,
            comparison=None,
        )

    def close(self) -> None:
        """收起面板并清空本批选择（对照结果不跨批保留）。"""

        self._set_state(
            open=False,
            candidates=(),
            selected_keys=(),
            excluded_keys=(),
            notice_key=None,
            submitted=False,
            comparison=None,
        )

    def toggle(self, key: str) -> None:
        """切换单个标的；超过上限时拒绝并提示（不静默挤掉已选项）。"""

        state = self._state
        candidate = next((item for item in state.candidates if item.key == key), None)
        if candidate is None or not candidate.selectable:
            return
        selected = list(state.selected_keys)
        if key in selected:
            selected.remove(key)
            self._set_state(selected_keys=tuple(selected), notice_key=None, submitted=False, comparison=None)
            return
        if len(selected) >= state.limit:
            self._set_state(notice_key=NOTICE_LIMIT_REACHED)
            return
        selected.append(key)
        self._set_state(selected_keys=tuple(selected), notice_key=None, submitted=False, comparison=None)

    def select_all(self) -> None:
        """全选可选标的；超过上限的部分记入 ``excluded_keys`` 并提示数量。"""

        state = self._state
        selectable = [item for item in state.candidates if item.selectable]
        selected = selectable[: state.limit]
        excluded = selectable[state.limit :]
        notice = NOTICE_SELECT_ALL_TRUNCATED if excluded else None
        self._set_state(
            selected_keys=tuple(item.key for item in selected),
            excluded_keys=tuple(item.key for item in excluded),
            notice_key=notice,
            submitted=False,
            comparison=None,
        )

    def clear(self) -> None:
        """清空选择与已生成的对照（保持面板打开）。"""

        self._set_state(
            selected_keys=(),
            excluded_keys=(),
            notice_key=None,
            submitted=False,
            comparison=None,
        )

    def submit(self, prepare: Callable[[str], bool]) -> None:
        """按所选标的发起研究准备，并生成逐标的对照骨架（不发送、零费用）。

        ``prepare`` 由宿主实现：把某个标的交给既有单对象草稿路径；返回 ``False``
        表示该标的无法准备（缺身份/来源失效），该行如实记为缺失并使批次 ``partial``。
        """

        state = self._state
        selected = [item for item in state.candidates if item.key in state.selected_keys]
        if not selected:
            self._set_state(notice_key=NOTICE_NO_SELECTION)
            return

        from services.agent_service.workflows import (
            COMPARISON_STATUS_COMPLETE,
            MISSING_REASON_IDENTITY as SERVICE_MISSING_IDENTITY,
            MISSING_REASON_OVER_LIMIT as SERVICE_MISSING_OVER_LIMIT,
            SUBJECT_STATE_MISSING,
            SUBJECT_STATE_PENDING,
            ComparisonSubject,
            MissingSubject,
            SubjectResearchResult,
            build_subject_comparison,
            normalize_subjects,
        )

        missing: list[MissingSubject] = [
            MissingSubject(item.display_name, SERVICE_MISSING_IDENTITY)
            for item in state.candidates
            if not item.selectable
        ]
        excluded = [item for item in state.candidates if item.key in state.excluded_keys]
        missing.extend(MissingSubject(item.display_name, SERVICE_MISSING_OVER_LIMIT) for item in excluded)
        selection = normalize_subjects(
            [ComparisonSubject(item.object_kind, item.object_id, item.display_name) for item in selected],
            limit=state.limit,
            missing=tuple(missing),
        )

        results: dict[str, SubjectResearchResult | None] = {}
        for subject in selection.subjects:
            prepared = bool(prepare(subject.key))
            results[subject.key] = SubjectResearchResult(
                state=SUBJECT_STATE_PENDING if prepared else SUBJECT_STATE_MISSING,
                gaps=() if prepared else ("prepare_failed",),
            )
        comparison = build_subject_comparison(selection, results)
        # 刚发起时逐标的均为 pending，不能据此宣称「部分完成」；只有确有缺失才提示部分完成。
        notice = NOTICE_PARTIAL if comparison.missing_objects else NOTICE_SUBMITTED
        if comparison.status == COMPARISON_STATUS_COMPLETE:
            notice = NOTICE_SUBMITTED
        self._set_state(
            submitted=True,
            comparison=comparison,
            notice_key=notice,
            selected_keys=tuple(subject.key for subject in selection.subjects),
        )


__all__ = [
    "MISSING_REASON_IDENTITY",
    "MISSING_REASON_OVER_LIMIT",
    "MULTI_SUBJECT_LIMIT",
    "NOTICE_LIMIT_REACHED",
    "NOTICE_NO_SELECTION",
    "NOTICE_PARTIAL",
    "NOTICE_SELECT_ALL_TRUNCATED",
    "NOTICE_SUBMITTED",
    "MultiSubjectResearchState",
    "MultiSubjectResearchViewModel",
    "SubjectCandidate",
]
