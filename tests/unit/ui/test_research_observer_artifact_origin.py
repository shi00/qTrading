"""成果卡回业务动作单测（复制摘要/回原报告/查看当前个股/关注与方案定位）。

覆盖：导航映射与股票代码提取、摘要文本事实完整性（缺失即「未知」，R21）、
按钮可见性规则、剪贴板写入与 toast、定位消息广播（仅定位，不重跑/不预填）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import flet as ft
import pytest

from ui.i18n import I18n
from ui.pubsub_topics import TOPIC_NAVIGATE
from ui.viewmodels.research_observer_view_model import SessionArtifactView, SessionOriginView
from ui.views import research_observer_artifacts as artifacts_view
from ui.views.research_observer_artifacts import (
    artifact_brief_lines,
    build_artifact_card,
    origin_report_navigate,
    origin_stock_code,
)

pytestmark = pytest.mark.unit


def _origin(
    *,
    source_module: str | None = "screener",
    object_kind: str | None = "screener_candidate",
    object_id: str | None = "000001.SZ",
    object_version: str | None = "abcdef0123456789ffff",
    display_name: str | None = "平安银行",
    available_at: str | None = "2026-11-20",
    period: str | None = "20261120",
    availability_state: str | None = "available",
    batch_ref: str | None = "batch-1",
    summary_items: tuple[tuple[str, str], ...] = (),
) -> SessionOriginView:
    return SessionOriginView(
        source_module=source_module,
        object_kind=object_kind,
        object_id=object_id,
        object_version=object_version,
        display_name=display_name,
        available_at=available_at,
        period=period,
        availability_state=availability_state,
        batch_ref=batch_ref,
        summary_items=summary_items,
    )


def _artifact(
    *,
    summary: str | None = "候选 A 评分",
    body_available: bool = True,
    published_at: str | None = "2026-11-17T01:40:00.000000Z",
) -> SessionArtifactView:
    return SessionArtifactView(
        artifact_id="a-1",
        artifact_version=1,
        artifact_kind="report",
        execution_id="e-1",
        round_id="r-1",
        summary=summary,
        body_text="正文" if body_available else None,
        body_available=body_available,
        published_at=published_at,
    )


def _walk(control: Any) -> list[Any]:
    if isinstance(control, list):
        found: list[Any] = []
        for item in control:
            found.extend(_walk(item))
        return found
    found = [control]
    for attribute in ("controls", "content", "actions"):
        value = getattr(control, attribute, None)
        if isinstance(value, list):
            for item in value:
                found.extend(_walk(item))
        elif value is not None and not isinstance(value, str):
            found.extend(_walk(value))
    return found


def _button_labels(card: ft.Control) -> list[str]:
    return [getattr(item.content, "value", "") for item in _walk(card) if isinstance(item, ft.TextButton)]


def _button(card: ft.Control, label_key: str) -> ft.TextButton:
    label = I18n.get(label_key)
    return next(
        item
        for item in _walk(card)
        if isinstance(item, ft.TextButton) and getattr(item.content, "value", None) == label
    )


def _run_handler(handler: Any) -> None:
    assert callable(handler)
    result = handler(None)
    if result is not None:
        asyncio.run(result)


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("backtest_result", "backtest"),
        ("screener_candidate", "screener"),
        ("news_item", "market"),
        ("stock", None),
        ("unknown_kind", None),
        (None, None),
    ],
)
def test_origin_report_navigate_maps_kinds(kind: str | None, expected: str | None) -> None:
    assert origin_report_navigate(_origin(object_kind=kind)) == expected


def test_origin_report_navigate_without_origin_returns_none() -> None:
    assert origin_report_navigate(None) is None


@pytest.mark.parametrize(
    ("kind", "object_id", "expected"),
    [
        ("stock", "000001.sz", "000001.SZ"),
        ("screener_candidate", "600519.sh", "600519.SH"),
        ("news_item", "000001.SZ", None),
        ("backtest_result", "bt-1", None),
        ("stock", None, None),
        ("stock", "   ", None),
    ],
)
def test_origin_stock_code_requires_stock_kind(kind: str, object_id: str | None, expected: str | None) -> None:
    assert origin_stock_code(_origin(object_kind=kind, object_id=object_id)) == expected


def test_origin_stock_code_without_origin_returns_none() -> None:
    assert origin_stock_code(None) is None


def test_brief_lines_keep_all_frozen_facts() -> None:
    origin = _origin()
    lines = artifact_brief_lines(origin, _artifact())

    assert lines == [
        I18n.get("agent_research_artifact_brief_object").format(object="平安银行"),
        I18n.get("agent_research_artifact_brief_kind").format(kind="screener_candidate:000001.SZ"),
        I18n.get("agent_research_artifact_brief_source").format(source="screener"),
        I18n.get("agent_research_artifact_brief_version").format(version="abcdef012345"),
        I18n.get("agent_research_artifact_brief_available_at").format(time="2026-11-20"),
        I18n.get("agent_research_artifact_brief_period").format(period="20261120"),
        I18n.get("agent_research_artifact_brief_published_at").format(time="2026-11-17T01:40:00.000000Z"),
        I18n.get("agent_research_artifact_brief_gap").format(gap=I18n.get("agent_research_artifact_brief_unknown")),
    ]


def test_brief_lines_render_unknown_instead_of_empty_values() -> None:
    unknown = I18n.get("agent_research_artifact_brief_unknown")
    lines = artifact_brief_lines(
        None,
        _artifact(summary=None, body_available=False, published_at=None),
    )

    assert lines[0] == I18n.get("agent_research_artifact_brief_object").format(object=unknown)
    assert lines[1] == I18n.get("agent_research_artifact_brief_kind").format(kind=unknown)
    assert lines[2] == I18n.get("agent_research_artifact_brief_source").format(source=unknown)
    assert lines[3] == I18n.get("agent_research_artifact_brief_version").format(version=unknown)
    assert lines[6] == I18n.get("agent_research_artifact_brief_published_at").format(time=unknown)
    gap = " / ".join(
        [
            I18n.get("agent_research_artifact_brief_gap_missing_summary"),
            I18n.get("agent_research_artifact_brief_gap_body_unavailable"),
        ]
    )
    assert lines[7] == I18n.get("agent_research_artifact_brief_gap").format(gap=gap)
    assert all(line.strip() for line in lines), "缺失事实不得渲染为空行"


def test_brief_lines_partial_origin_uses_unknown_for_missing_fields() -> None:
    origin = _origin(display_name=None, source_module=None, available_at=None, period=None, object_version=None)
    lines = artifact_brief_lines(origin, _artifact())

    unknown = I18n.get("agent_research_artifact_brief_unknown")
    assert lines[0] == I18n.get("agent_research_artifact_brief_object").format(object=unknown)
    assert lines[2] == I18n.get("agent_research_artifact_brief_source").format(source=unknown)
    assert lines[3] == I18n.get("agent_research_artifact_brief_version").format(version=unknown)
    assert lines[4] == I18n.get("agent_research_artifact_brief_available_at").format(time=unknown)
    assert lines[5] == I18n.get("agent_research_artifact_brief_period").format(period=unknown)


def test_card_shows_report_stock_and_entry_actions_for_stock_origin() -> None:
    card = build_artifact_card(
        _artifact(),
        loaded=None,
        loading=False,
        on_load_artifact=lambda *_args: None,
        origin=_origin(object_kind="screener_candidate"),
    )

    assert _button_labels(card) == [
        I18n.get("agent_research_artifact_brief_copy"),
        I18n.get("agent_research_artifact_open_report"),
        I18n.get("agent_research_artifact_view_current_stock"),
        I18n.get("agent_research_artifact_open_watchlist"),
        I18n.get("agent_research_artifact_open_plan"),
    ]
    for button in _walk(card):
        if isinstance(button, ft.TextButton):
            assert button.tooltip, "每个动作按钮必须说明仅定位/仅复制语义"


def test_card_news_origin_hides_stock_action_but_keeps_report() -> None:
    card = build_artifact_card(
        _artifact(),
        loaded=None,
        loading=False,
        on_load_artifact=lambda *_args: None,
        origin=_origin(object_kind="news_item", object_id="news-1", display_name="新闻"),
    )

    labels = _button_labels(card)
    assert I18n.get("agent_research_artifact_open_report") in labels
    assert I18n.get("agent_research_artifact_view_current_stock") not in labels


def test_card_without_origin_only_copy_and_business_entry() -> None:
    card = build_artifact_card(
        _artifact(),
        loaded=None,
        loading=False,
        on_load_artifact=lambda *_args: None,
    )

    assert _button_labels(card) == [
        I18n.get("agent_research_artifact_brief_copy"),
        I18n.get("agent_research_artifact_open_watchlist"),
        I18n.get("agent_research_artifact_open_plan"),
    ]


def test_copy_button_writes_brief_text_and_toasts_success(monkeypatch: pytest.MonkeyPatch) -> None:
    written: list[str] = []
    toasts: list[tuple[str, str]] = []

    class _Clipboard:
        async def set(self, value: str) -> None:
            written.append(value)

    page = SimpleNamespace(
        services=[],
        toast=SimpleNamespace(show=lambda msg, kind="info": toasts.append((msg, kind))),
        run_task=lambda fn, *args, **kwargs: asyncio.run(fn(*args, **kwargs)),
    )
    monkeypatch.setattr(artifacts_view, "_get_page", lambda: page)
    monkeypatch.setattr(artifacts_view.ft, "Clipboard", lambda: _Clipboard())

    origin = _origin()
    artifact = _artifact()
    card = build_artifact_card(
        artifact,
        loaded=None,
        loading=False,
        on_load_artifact=lambda *_args: None,
        origin=origin,
    )
    _run_handler(_button(card, "agent_research_artifact_brief_copy").on_click)

    assert written == ["\n".join(artifact_brief_lines(origin, artifact))]
    assert toasts == [(I18n.get("agent_research_artifact_brief_copied"), "success")]
    assert page.services == [], "剪贴板 service 用完必须移除"


def test_copy_button_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    toasts: list[tuple[str, str]] = []

    class _FailingClipboard:
        async def set(self, value: str) -> None:
            raise RuntimeError("clipboard unavailable")

    page = SimpleNamespace(
        services=[],
        toast=SimpleNamespace(show=lambda msg, kind="info": toasts.append((msg, kind))),
        run_task=lambda fn, *args, **kwargs: asyncio.run(fn(*args, **kwargs)),
    )
    monkeypatch.setattr(artifacts_view, "_get_page", lambda: page)
    monkeypatch.setattr(artifacts_view.ft, "Clipboard", lambda: _FailingClipboard())

    card = build_artifact_card(
        _artifact(),
        loaded=None,
        loading=False,
        on_load_artifact=lambda *_args: None,
        origin=_origin(),
    )
    _run_handler(_button(card, "agent_research_artifact_brief_copy").on_click)

    assert toasts == [(I18n.get("agent_research_artifact_brief_copy_failed"), "error")]
    assert page.services == []


def test_navigation_buttons_publish_locate_only_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, str]] = []
    page = SimpleNamespace(
        pubsub=SimpleNamespace(send_all_on_topic=lambda topic, message: sent.append((topic, message)))
    )
    monkeypatch.setattr(artifacts_view, "_get_page", lambda: page)

    card = build_artifact_card(
        _artifact(),
        loaded=None,
        loading=False,
        on_load_artifact=lambda *_args: None,
        origin=_origin(object_kind="screener_candidate", object_id="600519.sh"),
    )
    for label_key in (
        "agent_research_artifact_open_report",
        "agent_research_artifact_view_current_stock",
        "agent_research_artifact_open_watchlist",
        "agent_research_artifact_open_plan",
    ):
        _run_handler(_button(card, label_key).on_click)

    assert sent == [
        (TOPIC_NAVIGATE, "screener"),
        (TOPIC_NAVIGATE, "watchlist:600519.SH"),
        (TOPIC_NAVIGATE, "watchlist"),
        (TOPIC_NAVIGATE, "screener"),
    ]
