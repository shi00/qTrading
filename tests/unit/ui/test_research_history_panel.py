"""W-04 历史面板渲染单测：错误态不伪装空列表、清理未接线/预览可见、i18n 双语。"""

from __future__ import annotations

from typing import Any

import flet as ft
import pytest

from ui.i18n import I18n
from ui.viewmodels.research_observer_view_model import (
    CleanupPreviewView,
    CleanupReceiptView,
    HistoryHitView,
    HistoryUsageView,
    ResearchObserverState,
    SessionListItemView,
    SourceStopReceiptView,
)
from ui.views.research_history_panel import build_history_panel

pytestmark = pytest.mark.unit


def _noop() -> None:
    return None


def _noop_text(_value: str) -> None:
    return None


def _panel(state: ResearchObserverState) -> ft.Container:
    return build_history_panel(
        state,
        on_close=_noop,
        on_query_text=_noop_text,
        on_scope_filter=_noop_text,
        on_status_filter=_noop_text,
        on_search=_noop,
        on_load_more=_noop,
        on_refresh_usage=_noop,
        on_open_session=_noop_text,
        on_begin_cleanup=_noop,
        on_confirm_cleanup=_noop,
        on_cancel_cleanup=_noop,
        on_stop_source_id=_noop_text,
        on_stop_source_version=_noop_text,
        on_stop_using=_noop,
    )


def _flatten(control: Any) -> list[Any]:
    """递归收集控件（面板层级较深，逐层展开后按类型/文本断言）。"""

    found: list[Any] = []
    stack = [control]
    while stack:
        current = stack.pop()
        found.append(current)
        content = getattr(current, "content", None)
        if content is not None and not isinstance(content, str):
            stack.append(content)
        for attr in ("controls",):
            children = getattr(current, attr, None) or ()
            stack.extend(children)
    return found


def _texts(control: Any) -> list[str]:
    return [item.value for item in _flatten(control) if isinstance(item, ft.Text) and isinstance(item.value, str)]


def test_history_panel_renders_hits_with_summary_and_location() -> None:
    state = ResearchObserverState(
        available=True,
        history_query="回撤",
        history_hits=(
            HistoryHitView(
                hit_kind="message",
                session_id="s-1",
                created_at="2026-11-24T00:00:00Z",
                summary_text="最大回撤控制",
                location="execution:e1 · round:r1 · message:m1",
                role="user",
            ),
        ),
        usage=HistoryUsageView(
            session_id="s-1",
            sessions_listed=1,
            sessions_truncated=False,
            executions=2,
            messages=3,
            messages_truncated=True,
            artifacts=1,
            artifacts_truncated=False,
            archived_sessions=0,
            cleaned_sessions=0,
        ),
    )

    texts = _texts(_panel(state))

    assert any("最大回撤控制" in text for text in texts)
    assert any("execution:e1" in text for text in texts)
    assert any("字节用量：未知" in text for text in texts)
    assert not any("暂无历史命中" in text for text in texts)


def test_history_panel_shows_error_code_instead_of_empty_hint() -> None:
    state = ResearchObserverState(available=True, history_error_code="PERSISTENCE_UNAVAILABLE")

    texts = _texts(_panel(state))

    assert any("PERSISTENCE_UNAVAILABLE" in text for text in texts)
    assert not any("暂无历史命中" in text for text in texts)


def test_cleanup_unavailable_and_preview_are_explicit() -> None:
    unavailable = ResearchObserverState(available=True, cleanup_available=False, cleanup_unavailable_reason="wiring_x")
    texts = _texts(_panel(unavailable))
    assert any("清理链路未接线" in text for text in texts)
    assert any("wiring_x" in text for text in texts)

    preview = CleanupPreviewView(
        cleanup_job_id="ui-cleanup:s-1",
        phase="prepared",
        exact_count=2,
        session_count=1,
        asset_count=1,
        expected_revision=7,
        verified=False,
    )
    with_preview = ResearchObserverState(
        available=True,
        cleanup_available=True,
        session_id="s-1",
        cleanup_preview=preview,
    )
    panel = _panel(with_preview)
    texts = _texts(panel)
    assert any("执行 2" in text and "修订 7" in text for text in texts)
    confirm_buttons = [
        item
        for item in _flatten(panel)
        if isinstance(item, ft.TextButton) and isinstance(item.content, ft.Text) and item.content.value == "确认清理"
    ]
    assert confirm_buttons and confirm_buttons[0].disabled is False


def test_cleanup_verified_receipt_marks_completed() -> None:
    state = ResearchObserverState(
        available=True,
        cleanup_available=True,
        session_id="s-1",
        cleanup_receipt=CleanupReceiptView(
            cleanup_job_id="ui-cleanup:s-1",
            phase="verified",
            replayed=False,
            verified=True,
            verified_counts=(("agent_ds.datasource_outputs", 0),),
            tombstoned=(("agent_session", 1),),
        ),
    )

    texts = _texts(_panel(state))

    assert any("清理完成" in text for text in texts)
    assert not any("清理未完成" in text for text in texts)


def test_history_panel_i18n_bilingual() -> None:
    previous = I18n.current_locale()
    try:
        I18n.set_locale("en_US")
        english = _texts(_panel(ResearchObserverState(available=True)))
        assert any(text == "Research history" for text in english)
        I18n.set_locale("zh_CN")
        chinese = _texts(_panel(ResearchObserverState(available=True)))
        assert any(text == "研究历史" for text in chinese)
    finally:
        I18n.set_locale(previous)


def test_unavailable_state_shows_reason_and_usage_card_only() -> None:
    state = ResearchObserverState(available=False, unavailable_reason_key="agent_research_not_wired")

    texts = _texts(_panel(state))

    assert any(text == I18n.get("agent_research_not_wired") for text in texts)
    assert any("空间占用" in text for text in texts)
    # 不可用时不渲染检索框与清理/停止使用卡片，避免误导可操作。
    assert not any("关键词" in text for text in texts)


def test_sessions_rows_and_load_more_entry_are_rendered() -> None:
    state = ResearchObserverState(
        available=True,
        history_has_more=True,
        sessions=(
            SessionListItemView(
                session_id="s-1",
                title=None,
                updated_at="2026-11-24T00:00:00Z",
                revision=2,
                pinned=False,
                archived=True,
                cleaned=False,
                status_unknown=False,
            ),
        ),
    )

    panel = _panel(state)
    texts = _texts(panel)

    assert any(text == I18n.get("agent_research_session_untitled") for text in texts)
    assert any(text == "加载更多" for text in texts)


def test_empty_session_directory_and_loading_ring_are_rendered() -> None:
    state = ResearchObserverState(available=True, history_loading=True)

    controls = _flatten(_panel(state))

    assert any(isinstance(control, ft.ProgressRing) for control in controls)
    assert any(
        isinstance(control, ft.Text) and control.value == I18n.get("agent_research_session_list_empty")
        for control in controls
    )


def test_cleanup_error_code_and_pending_ring_are_rendered() -> None:
    state = ResearchObserverState(
        available=True,
        cleanup_available=True,
        session_id="s-1",
        cleanup_error_code="POLICY_DENIED",
        cleanup_pending=True,
        stop_source_error_code="SOURCE_DISABLED",
        stop_source_pending=True,
    )

    panel = _panel(state)
    texts = _texts(panel)
    controls = _flatten(panel)

    assert any("POLICY_DENIED" in text for text in texts)
    assert any("SOURCE_DISABLED" in text for text in texts)
    assert sum(isinstance(control, ft.ProgressRing) for control in controls) == 2


def test_stop_source_receipt_and_replay_hint_are_rendered() -> None:
    state = ResearchObserverState(
        available=True,
        cleanup_available=True,
        stop_source_error_key="agent_research_stop_source_required",
        stop_source_receipt=SourceStopReceiptView(
            source_id="src-1",
            source_version="v3",
            disabled_revision=9,
            already_disabled=True,
            disabled_at="2026-11-24T00:00:00Z",
        ),
    )

    texts = _texts(_panel(state))

    assert any(text == "请填写来源 ID 与版本" for text in texts)
    assert any("src-1@v3" in text for text in texts)
    assert any("此前已停止使用" in text for text in texts)


def test_cleanup_retry_pending_receipt_is_not_rendered_as_completed() -> None:
    state = ResearchObserverState(
        available=True,
        cleanup_available=True,
        cleanup_receipt=CleanupReceiptView(
            cleanup_job_id="ui-cleanup:s-1",
            phase="retry_pending",
            replayed=False,
            verified=False,
            verified_counts=(),
            tombstoned=(),
        ),
    )

    texts = _texts(_panel(state))

    assert any("清理未完成" in text for text in texts)
    assert not any("清理完成" in text for text in texts)
