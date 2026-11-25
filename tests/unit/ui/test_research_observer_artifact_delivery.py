"""成果预览/复制/导出接线单测（W-03）。

覆盖：预览格式与错误、覆盖确认流程、未知回执不按成功呈现且锁定目标、复制范围、
错误码映射、i18n 双语键、组合根适配（预览口径/目标探测/导出命令构造）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import flet as ft
import pytest

from app import agent_wiring
from app.agent_wiring import (
    _ARTIFACT_PREVIEW_CHAR_LIMIT,
    AgentArtifactDelivery,
    _build_artifact_delivery,
    _xlsx_field_preview,
)
from core.agent_types import AgentServiceError, AgentValidationError, DeliveryField
from ui.i18n import I18n
from ui.viewmodels.research_observer_view_model import (
    ARTIFACT_COPY_CLIPBOARD_FAILED_KEY,
    ARTIFACT_COPY_DONE_KEY,
    ARTIFACT_COPY_INCOMPLETE_KEY,
    ARTIFACT_COPY_NOT_LOADED_KEY,
    ARTIFACT_COPY_TRUNCATED_KEY,
    ARTIFACT_DELIVERY_UNAVAILABLE_KEY,
    ARTIFACT_EXPORT_UNKNOWN_BLOCKED_KEY,
    ARTIFACT_EXPORT_UNKNOWN_NOTICE_KEY,
    ARTIFACT_NOT_FOUND_KEY,
    ARTIFACT_SELECT_ONE_KEY,
    ARTIFACT_SELECTION_EMPTY_KEY,
    ARTIFACT_TARGET_PATH_EMPTY_KEY,
    ARTIFACT_TARGET_REPARSE_KEY,
    ARTIFACT_TARGET_UNREADABLE_KEY,
    ArtifactDeliveryReceiptView,
    ArtifactExportTargetView,
    ArtifactPreviewView,
    LoadedArtifactBody,
    ResearchObserverState,
    ResearchObserverViewModel,
    SessionArtifactView,
    resolve_artifact_delivery,
)
from ui.views import research_observer_artifacts as artifacts_view
from ui.views.research_observer_artifacts import (
    _get_page,
    artifact_preview_meta,
    artifact_overwrite_summary,
    artifact_receipt_lines,
    artifact_selection_reason,
    artifact_unavailable_text,
    build_artifact_card,
    build_artifact_delivery_actions,
    build_artifact_export_panels,
    build_artifact_preview_panel,
    build_artifacts_section,
)

pytestmark = pytest.mark.unit

_LOCALES = Path(__file__).resolve().parents[3] / "locales"

_W03_KEYS = (
    "agent_research_artifact_selection_empty",
    "agent_research_artifact_select_one",
    "agent_research_artifact_delivery_unavailable",
    "agent_research_artifact_copy_done",
    "agent_research_artifact_copy_clipboard_failed",
    "agent_research_artifact_preview_truncated",
    "agent_research_artifact_preview_field_view",
    "agent_research_artifact_preview_field_view_truncated",
    "agent_research_artifact_export_success",
    "agent_research_artifact_export_cancelled",
    "agent_research_artifact_export_failed_fmt",
    "agent_research_artifact_export_unknown",
    "agent_research_artifact_export_unknown_blocked",
    "agent_research_artifact_export_overwrite_fmt",
    "agent_research_artifact_export_overwrite_confirm",
    "agent_research_artifact_export_overwrite_cancel",
    "agent_research_artifact_export_staged_dirty",
)


def _artifact(
    artifact_id: str = "a-1",
    *,
    version: int = 1,
    execution_id: str = "e-1",
    round_id: str = "r-1",
) -> SessionArtifactView:
    return SessionArtifactView(
        artifact_id=artifact_id,
        artifact_version=version,
        artifact_kind="report",
        execution_id=execution_id,
        round_id=round_id,
        summary="候选 A 评分",
        body_text=None,
        body_available=False,
        published_at="2026-11-17T01:40:00.000000Z",
    )


def _body(key: str, text: str, *, truncated: bool = False, available: bool = True) -> LoadedArtifactBody:
    return LoadedArtifactBody(
        key=key,
        body_text=text,
        body_available=available,
        unavailable_reason=None if available else "body_cleaned",
        truncated=truncated,
    )


def _receipt(*, phase: str, error_code: str | None = None, staged_cleaned: bool = True) -> Any:
    return SimpleNamespace(
        delivery_id="d-1",
        phase=phase,
        publish_effect="published" if phase == "published_confirmed" else "not_published",
        cancel_effect="none",
        error_code=error_code,
        next_action="check_original_delivery" if phase == "unknown" else None,
        output_digest="f" * 64 if phase == "published_confirmed" else None,
        verified_at="2026-11-17T06:00:00.000000Z",
        staged_cleaned=staged_cleaned,
    )


def _probe(
    *,
    exists: bool = False,
    digest: str | None = None,
    reparse: bool = False,
    size: int | None = None,
) -> Any:
    return SimpleNamespace(
        target_path="C:/exports/report.md",
        exists=exists,
        size=size,
        digest=digest,
        volume_serial=7 if exists else None,
        file_id=11 if exists else None,
        is_reparse_point=reparse,
    )


class _FakeDelivery:
    def __init__(
        self,
        *,
        probe: Any | None = None,
        receipt: Any | None = None,
        preview: Any | None = None,
        export_error: Exception | None = None,
        preview_error: Exception | None = None,
        inspect_error: Exception | None = None,
    ) -> None:
        self._probe = probe or _probe()
        self._receipt = receipt or _receipt(phase="published_confirmed")
        self._preview = preview or SimpleNamespace(
            format="md",
            text="# 报告",
            digest="a" * 64,
            byte_count=12,
            field_count=3,
            note_key=None,
        )
        self._export_error = export_error
        self._preview_error = preview_error
        self._inspect_error = inspect_error
        self.preview_calls: list[dict[str, Any]] = []
        self.inspect_calls: list[str] = []
        self.export_calls: list[dict[str, Any]] = []

    async def preview(self, **kwargs: Any) -> Any:
        self.preview_calls.append(kwargs)
        if self._preview_error is not None:
            raise self._preview_error
        return self._preview

    async def inspect_target(self, *, target_path: str) -> Any:
        self.inspect_calls.append(target_path)
        if self._inspect_error is not None:
            raise self._inspect_error
        return self._probe

    async def export(self, **kwargs: Any) -> Any:
        self.export_calls.append(kwargs)
        if self._export_error is not None:
            raise self._export_error
        return SimpleNamespace(
            receipt=self._receipt,
            staged_cleaned=getattr(self._receipt, "staged_cleaned", True),
            target_path=kwargs["target_path"],
        )


def _vm(
    *,
    artifacts: tuple[SessionArtifactView, ...] = (_artifact(),),
    bodies: tuple[LoadedArtifactBody, ...] = (),
    delivery: Any | None = None,
    selected: tuple[str, ...] = ("a-1:1",),
    format_name: str = "md",
) -> ResearchObserverViewModel:
    vm = ResearchObserverViewModel(None, delivery=delivery)
    vm._set_state(
        available=True,
        artifacts=artifacts,
        artifact_bodies=bodies,
        artifact_selected_keys=selected,
        artifact_export_format=format_name,
    )
    return vm


def _run_handler(handler: Any) -> None:
    """调用控件事件处理器（同步/异步皆可；测试里不构造真实 Event）。"""

    assert callable(handler)
    result = handler(None)
    if result is not None:
        asyncio.run(result)  # type: ignore[arg-type]  # 事件处理器返回 coroutine，但类型擦除为 Any


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


# ----------------------------------------------------------------------
# 预览
# ----------------------------------------------------------------------


@pytest.mark.parametrize("format_name", ["md", "csv", "xlsx"])
def test_preview_renders_selected_format(format_name: str) -> None:
    delivery = _FakeDelivery()
    vm = _vm(delivery=delivery, format_name=format_name)

    asyncio.run(vm.preview_selected_artifact())

    assert delivery.preview_calls == [
        {
            "artifact_id": "a-1",
            "artifact_version": 1,
            "execution_id": "e-1",
            "round_id": "r-1",
            "format_name": format_name,
        }
    ]
    assert vm.state.artifact_preview is not None
    assert vm.state.artifact_preview.text == "# 报告"
    assert vm.state.artifact_preview.format == "md"  # 端口返回值原样承载
    assert vm.state.artifact_preview_pending is False
    assert vm.state.artifact_preview_error_key is None


def test_preview_requires_exactly_one_selection() -> None:
    delivery = _FakeDelivery()
    empty = _vm(delivery=delivery, selected=())
    asyncio.run(empty.preview_selected_artifact())
    assert empty.state.artifact_preview_error_key == ARTIFACT_SELECTION_EMPTY_KEY
    assert delivery.preview_calls == []

    multi = _vm(delivery=delivery, artifacts=(_artifact("a-1"), _artifact("a-2")), selected=("a-1:1", "a-2:1"))
    asyncio.run(multi.preview_selected_artifact())
    assert multi.state.artifact_preview_error_key == ARTIFACT_SELECT_ONE_KEY
    assert delivery.preview_calls == []


def test_preview_reports_unwired_delivery() -> None:
    vm = _vm(delivery=None)

    asyncio.run(vm.preview_selected_artifact())

    assert vm.state.artifact_preview_error_key == ARTIFACT_DELIVERY_UNAVAILABLE_KEY


# ----------------------------------------------------------------------
# 覆盖确认 / 回执三态
# ----------------------------------------------------------------------


def test_export_existing_target_requires_confirmation_then_binds_facts() -> None:
    receipt = _receipt(phase="published_confirmed")
    delivery = _FakeDelivery(
        probe=_probe(exists=True, digest="b" * 64, size=99),
        receipt=receipt,
    )
    vm = _vm(delivery=delivery)

    asyncio.run(vm.request_selected_export("C:/exports/report.md"))

    assert delivery.export_calls == [], "目标已存在时不得直接写入"
    confirmation = vm.state.artifact_export_confirmation
    assert confirmation is not None
    assert confirmation.digest == "b" * 64
    assert confirmation.size == 99

    asyncio.run(vm.confirm_selected_export())

    assert len(delivery.export_calls) == 1
    call = delivery.export_calls[0]
    assert call["expected_existing"] is True
    assert call["expected_digest"] == "b" * 64
    assert call["expected_volume_serial"] == 7
    assert call["expected_file_id"] == 11
    assert vm.state.artifact_export_confirmation is None
    assert vm.state.artifact_export_receipt is not None
    assert vm.state.artifact_export_receipt.phase == "published_confirmed"
    assert vm.state.artifact_export_notice_key is None


def test_export_missing_target_runs_without_confirmation() -> None:
    delivery = _FakeDelivery(probe=_probe(exists=False))
    vm = _vm(delivery=delivery)

    asyncio.run(vm.request_selected_export("C:/exports/new.csv"))

    assert vm.state.artifact_export_confirmation is None
    assert len(delivery.export_calls) == 1
    assert delivery.export_calls[0]["expected_existing"] is False
    assert delivery.export_calls[0]["expected_digest"] is None


def test_export_reparse_target_is_refused_without_writing() -> None:
    delivery = _FakeDelivery(probe=_probe(exists=True, digest="b" * 64, size=1, reparse=True))
    vm = _vm(delivery=delivery)

    asyncio.run(vm.request_selected_export("C:/exports/link.md"))

    assert vm.state.artifact_export_error_key == ARTIFACT_TARGET_REPARSE_KEY
    assert delivery.export_calls == []


def test_export_unknown_receipt_is_not_success_and_blocks_same_target() -> None:
    delivery = _FakeDelivery(
        probe=_probe(exists=False),
        receipt=_receipt(phase="unknown", error_code="delivery_publish_unknown", staged_cleaned=False),
    )
    vm = _vm(delivery=delivery)

    asyncio.run(vm.request_selected_export("C:/exports/report.md"))

    receipt = vm.state.artifact_export_receipt
    assert receipt is not None
    assert receipt.phase == "unknown"
    assert vm.state.artifact_export_notice_key == ARTIFACT_EXPORT_UNKNOWN_NOTICE_KEY
    assert receipt.staged_cleaned is False
    lines = artifact_receipt_lines(receipt)
    assert lines[0] == I18n.get("agent_research_artifact_export_unknown")
    assert lines[0] != I18n.get("agent_research_artifact_export_success")

    asyncio.run(vm.request_selected_export("C:/exports/report.md"))

    assert vm.state.artifact_export_error_key == ARTIFACT_EXPORT_UNKNOWN_BLOCKED_KEY
    assert len(delivery.inspect_calls) == 1, "未知回执后不得再次探测/导出同一目标"


def test_export_failure_maps_string_error_code() -> None:
    class _TargetChanged(Exception):
        code = "delivery_target_changed"

    delivery = _FakeDelivery(export_error=_TargetChanged())
    vm = _vm(delivery=delivery)

    asyncio.run(vm.request_selected_export("C:/exports/report.md"))

    assert vm.state.artifact_export_error_code == "delivery_target_changed"
    assert vm.state.artifact_export_receipt is None


def test_export_requires_target_path() -> None:
    delivery = _FakeDelivery()
    vm = _vm(delivery=delivery)

    asyncio.run(vm.request_selected_export("  "))

    assert vm.state.artifact_export_error_key == ARTIFACT_TARGET_PATH_EMPTY_KEY
    assert delivery.inspect_calls == []


def test_cancel_export_drops_confirmation_without_writing() -> None:
    delivery = _FakeDelivery(probe=_probe(exists=True, digest="b" * 64, size=3))
    vm = _vm(delivery=delivery)
    asyncio.run(vm.request_selected_export("C:/exports/report.md"))

    vm.cancel_selected_export()

    assert vm.state.artifact_export_confirmation is None
    assert delivery.export_calls == []


# ----------------------------------------------------------------------
# 复制范围
# ----------------------------------------------------------------------


def test_copy_empty_selection_is_refused_with_reason() -> None:
    vm = _vm(selected=())

    assert vm.copy_selected_artifacts() is None
    assert vm.state.artifact_copy_error_key == ARTIFACT_SELECTION_EMPTY_KEY


def test_copy_requires_loaded_complete_body() -> None:
    not_loaded = _vm(bodies=())
    assert not_loaded.copy_selected_artifacts() is None
    assert not_loaded.state.artifact_copy_error_key == ARTIFACT_COPY_NOT_LOADED_KEY

    truncated = _vm(bodies=(_body("a-1:1", "前一半", truncated=True),))
    assert truncated.copy_selected_artifacts() is None
    assert truncated.state.artifact_copy_error_key == ARTIFACT_COPY_TRUNCATED_KEY


def test_copy_returns_exact_selected_bodies() -> None:
    vm = _vm(
        artifacts=(_artifact("a-1"), _artifact("a-2")),
        bodies=(_body("a-1:1", "第一段"), _body("a-2:1", "第二段")),
        selected=("a-1:1", "a-2:1"),
    )

    assert vm.copy_selected_artifacts() == "第一段\n\n第二段"
    assert vm.state.artifact_copy_error_key is None
    assert vm.state.artifact_copy_notice_key is None

    vm.confirm_artifact_copy()
    assert vm.state.artifact_copy_notice_key == ARTIFACT_COPY_DONE_KEY

    vm.note_artifact_copy_failed()
    assert vm.state.artifact_copy_error_key == ARTIFACT_COPY_CLIPBOARD_FAILED_KEY
    assert vm.state.artifact_copy_notice_key is None


# ----------------------------------------------------------------------
# 视图契约
# ----------------------------------------------------------------------


def test_selection_reason_covers_empty_multi_and_unwired() -> None:
    assert artifact_selection_reason(ResearchObserverState(available=True, artifacts=(_artifact(),))) == I18n.get(
        "agent_research_artifact_selection_empty"
    )
    assert artifact_selection_reason(
        ResearchObserverState(available=True, artifacts=(_artifact(),), artifact_selected_keys=("a-1:1",))
    ) == I18n.get("agent_research_artifact_delivery_unavailable")
    assert (
        artifact_selection_reason(
            ResearchObserverState(
                available=True,
                artifacts=(_artifact(),),
                artifact_selected_keys=("a-1:1",),
                artifact_delivery_available=True,
            )
        )
        is None
    )


def test_overwrite_summary_contains_name_size_and_digest_prefix() -> None:
    target = SimpleNamespace(
        target_path=r"C:\exports\report.md",
        size=2048,
        digest="cdef0123456789" + "0" * 50,
    )

    text = artifact_overwrite_summary(target)  # type: ignore[arg-type]  # 测试用结构等价对象

    assert "report.md" in text
    assert "2048" in text
    assert "cdef01234567" in text


def test_artifacts_section_disables_actions_with_reason() -> None:
    state = ResearchObserverState(available=True, artifacts=(_artifact(),), artifact_delivery_available=True)

    controls = build_artifacts_section(
        state,
        on_load_artifact=lambda *_args: None,
        on_toggle_artifact_selection=lambda _key: None,
        on_select_all_artifacts=lambda: None,
        on_clear_artifact_selection=lambda: None,
        on_artifact_format_change=lambda _value: None,
        on_artifact_target_path_change=lambda _value: None,
        on_preview_artifact=_async_noop,
        on_copy_artifacts=lambda: None,
        on_artifact_copy_done=lambda: None,
        on_artifact_copy_failed=lambda: None,
        on_request_export=_async_noop,
        on_confirm_export=_async_noop,
        on_cancel_export=lambda: None,
    )
    buttons = [item for item in _walk(controls) if isinstance(item, (ft.FilledButton, ft.OutlinedButton))]
    preview = next(item for item in buttons if item.tooltip == I18n.get("agent_research_artifact_selection_empty"))
    assert preview.disabled is True


async def _async_noop() -> None:
    return None


def test_locale_keys_are_bilingual() -> None:
    for locale in ("zh_CN", "en_US"):
        payload = json.loads((_LOCALES / locale / "strings.json").read_text(encoding="utf-8"))
        missing = [key for key in _W03_KEYS if key not in payload]
        assert not missing, f"locales/{locale}/strings.json missing {missing}"
    zh = json.loads((_LOCALES / "zh_CN" / "strings.json").read_text(encoding="utf-8"))
    en = json.loads((_LOCALES / "en_US" / "strings.json").read_text(encoding="utf-8"))
    assert zh["agent_research_artifact_export_unknown"] != en["agent_research_artifact_export_unknown"]


# ----------------------------------------------------------------------
# 组合根适配（预览口径 / 目标探测 / 导出命令）
# ----------------------------------------------------------------------


class _FakeDao:
    def __init__(self, reads: list[Any], *, states: tuple[Any, ...] = ()) -> None:
        self._reads = reads
        self._states = states
        self.queries: list[Any] = []
        self.operations: list[dict[str, Any]] = []

    async def read_artifact(self, query: Any, *, user_scope: str) -> Any:
        self.queries.append(query)
        if len(self.queries) == 1:
            return self._reads[0]
        return SimpleNamespace(source_ref_states=self._states, source_available_state="available")

    async def ensure_model_operation(self, **kwargs: Any) -> bool:
        self.operations.append(kwargs)
        return True


def _read(*, body_text: str = "正文", next_cursor: str | None = None) -> Any:
    return SimpleNamespace(
        artifact_id="a-1",
        artifact_version=1,
        execution_id="e-1",
        round_id="r-1",
        body_text=body_text,
        body_available=True,
        next_cursor=next_cursor,
        source_ref_states=(),
        result_digest="a" * 64,
    )


def test_adapter_preview_uses_full_body_and_reports_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    dao = _FakeDao([_read(), _read()])
    adapter = AgentArtifactDelivery(dao=dao, user_scope="u", store=object())

    monkeypatch.setattr(
        agent_wiring,
        "build_delivery_fields",
        lambda read, **_: (DeliveryField(record="metadata", pointer="k", value_type="string", value_text="v"),),
    )
    monkeypatch.setattr(
        agent_wiring,
        "render_delivery",
        lambda fields, format_name: SimpleNamespace(
            content=b"# title\n",
            digest="e" * 64,
        ),
    )

    result = asyncio.run(
        adapter.preview(
            artifact_id="a-1",
            artifact_version=1,
            execution_id="e-1",
            round_id="r-1",
            format_name="md",
        )
    )

    assert result.text == "# title\n"
    assert result.digest == "e" * 64
    assert result.byte_count == 8
    assert result.field_count == 1
    assert result.note_key is None


def test_adapter_preview_xlsx_field_view_and_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    dao = _FakeDao([_read(), _read()])
    adapter = AgentArtifactDelivery(dao=dao, user_scope="u", store=object())
    monkeypatch.setattr(
        agent_wiring,
        "build_delivery_fields",
        lambda read, **_: (DeliveryField(record="metadata", pointer="k", value_type="string", value_text="v" * 50),),
    )
    monkeypatch.setattr(
        agent_wiring, "render_delivery", lambda fields, format_name: SimpleNamespace(content=b"x", digest="e" * 64)
    )
    monkeypatch.setattr(agent_wiring, "_ARTIFACT_PREVIEW_CHAR_LIMIT", 10)

    result = asyncio.run(
        adapter.preview(
            artifact_id="a-1",
            artifact_version=1,
            execution_id="e-1",
            round_id="r-1",
            format_name="xlsx",
        )
    )

    assert result.note_key == "agent_research_artifact_preview_field_view_truncated"
    assert len(result.text) == 10


def test_xlsx_field_preview_header_and_rows() -> None:
    text = _xlsx_field_preview((DeliveryField(record="metadata", pointer="k", value_type="string", value_text="v"),))

    assert text.splitlines()[0] == "path\tvalue_type\tchunk_index\tchunk_count\tvalue"
    assert text.splitlines()[1] == "metadata:k\tstring\t1\t1\tv"


def test_adapter_inspect_target_existing_and_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # 交付目标是 Windows 盘符绝对路径（跨平台用例固定语法，不依赖宿主机路径形态）；
    # 真实 stat/摘要由文件适配器负责，这里替换快照函数以覆盖「存在/缺失」两分支。
    adapter = AgentArtifactDelivery(dao=_FakeDao([]), user_scope="u", store=object())
    snapshots = {
        r"C:\exports\missing.md": SimpleNamespace(
            exists=False, size=None, digest=None, volume_serial=None, file_id=None, is_reparse_point=False
        ),
        r"C:\exports\report.md": SimpleNamespace(
            exists=True, size=5, digest="b" * 64, volume_serial=7, file_id=11, is_reparse_point=False
        ),
    }
    monkeypatch.setattr(agent_wiring, "_snapshot", lambda path: snapshots[str(path)])

    missing = asyncio.run(adapter.inspect_target(target_path=r"C:\exports\missing.md"))
    assert missing.exists is False
    assert missing.digest is None

    existing = asyncio.run(adapter.inspect_target(target_path=r"C:\exports\report.md"))
    assert existing.exists is True
    assert existing.size == 5
    assert isinstance(existing.digest, str) and len(existing.digest) == 64
    assert isinstance(existing.volume_serial, int) and existing.volume_serial > 0


def test_adapter_export_builds_command_with_confirmation_facts() -> None:
    dao = _FakeDao([])
    adapter = AgentArtifactDelivery(dao=dao, user_scope="u", store=object())
    captured: list[Any] = []
    target_path = r"C:\exports\report.md"

    class _Service:
        async def deliver(self, command: Any, context: Any) -> Any:
            captured.append((command, context))
            return SimpleNamespace(receipt=_receipt(phase="published_confirmed"), staged_cleaned=True)

    adapter._service = _Service()  # type: ignore[assignment]  # 测试替身

    result = asyncio.run(
        adapter.export(
            artifact_id="a-1",
            artifact_version=1,
            execution_id="e-1",
            round_id="r-1",
            format_name="md",
            target_path=target_path,
            expected_existing=True,
            expected_digest="b" * 64,
            expected_volume_serial=7,
            expected_file_id=11,
        )
    )

    assert result.receipt.phase == "published_confirmed"
    assert dao.operations and dao.operations[0]["execution_id"] == "e-1"
    command, context = captured[0]
    assert command.delivery_id
    assert command.target.overwrite_confirmed is True
    assert command.target.expected_volume_serial == 7
    assert context.execution_id == "e-1"
    assert command.command.source_module == "ui.research_observer"


def test_build_artifact_delivery_is_failure_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    built = _build_artifact_delivery(dao=_FakeDao([]), user_scope="u")
    assert isinstance(built, AgentArtifactDelivery)

    class _Broken:
        def __init__(self, **_: Any) -> None:
            raise RuntimeError("boom")

    monkeypatch.setattr(agent_wiring, "AgentArtifactDelivery", _Broken)
    assert _build_artifact_delivery(dao=_FakeDao([]), user_scope="u") is None


# ----------------------------------------------------------------------
# 命令分支（选择/格式/目标/预览/导出/复制边界）
# ----------------------------------------------------------------------


def test_selection_and_format_commands_keep_state_consistent() -> None:
    vm = _vm(delivery=_FakeDelivery())

    vm.set_artifact_export_format("csv")
    assert vm.state.artifact_export_format == "csv"
    vm.set_artifact_export_format("bogus")
    assert vm.state.artifact_export_format == "csv"

    vm.set_artifact_export_target_path("  C:/exports/a.md  ")
    assert vm.state.artifact_export_target_path == "C:/exports/a.md"

    vm.clear_artifact_selection()
    assert vm.state.artifact_selected_keys == ()
    vm.select_all_artifacts()
    assert vm.state.artifact_selected_keys == ("a-1:1",)

    vm.toggle_artifact_selection("missing:1")
    assert vm.state.artifact_selected_keys == ("a-1:1",)
    vm.toggle_artifact_selection("a-1:1")
    assert vm.state.artifact_selected_keys == ()
    vm.toggle_artifact_selection("a-1:1")
    assert vm.state.artifact_selected_keys == ("a-1:1",)


def test_copy_refuses_missing_cleaned_and_unavailable_bodies() -> None:
    missing = _vm(selected=("ghost:1",))
    assert missing.copy_selected_artifacts() is None
    assert missing.state.artifact_copy_error_key == ARTIFACT_NOT_FOUND_KEY

    cleaned = _vm(bodies=(_body("a-1:1", "旧正文"),))
    cleaned._raw_artifacts = (
        SimpleNamespace(artifact_id="a-1", artifact_version=1, cleaned_at="2026-11-17T00:00:00.000000Z"),
    )
    assert cleaned.copy_selected_artifacts() is None
    assert cleaned.state.artifact_copy_error_key == ARTIFACT_COPY_INCOMPLETE_KEY

    unavailable = _vm(bodies=(_body("a-1:1", "不可用", available=False),))
    assert unavailable.copy_selected_artifacts() is None
    assert unavailable.state.artifact_copy_error_key == ARTIFACT_COPY_INCOMPLETE_KEY


def test_preview_and_export_guards_and_error_paths() -> None:
    pending_preview = _vm(delivery=_FakeDelivery())
    pending_preview._set_state(artifact_preview_pending=True)
    asyncio.run(pending_preview.preview_selected_artifact())
    assert pending_preview.state.artifact_preview_pending is True

    failing_preview = _vm(delivery=_FakeDelivery(preview_error=RuntimeError("boom")))
    asyncio.run(failing_preview.preview_selected_artifact())
    assert failing_preview.state.artifact_preview_error_code == "RuntimeError"
    assert failing_preview.state.artifact_preview_pending is False

    pending_export = _vm(delivery=_FakeDelivery())
    pending_export._set_state(artifact_export_pending=True)
    asyncio.run(pending_export.request_selected_export("C:/exports/a.md"))
    assert pending_export.state.artifact_export_pending is True

    unreadable = _vm(delivery=_FakeDelivery(probe=_probe(exists=True, digest=None, size=3)))
    asyncio.run(unreadable.request_selected_export("C:/exports/a.md"))
    assert unreadable.state.artifact_export_error_key == ARTIFACT_TARGET_UNREADABLE_KEY

    failing_probe = _vm(delivery=_FakeDelivery(inspect_error=RuntimeError("stat boom")))
    asyncio.run(failing_probe.request_selected_export("C:/exports/a.md"))
    assert failing_probe.state.artifact_export_error_code == "RuntimeError"
    assert failing_probe.state.artifact_export_pending is False

    unwired = _vm(delivery=None)
    asyncio.run(unwired.request_selected_export("C:/exports/a.md"))
    assert unwired.state.artifact_export_error_key == ARTIFACT_DELIVERY_UNAVAILABLE_KEY

    stale = _vm(delivery=_FakeDelivery())
    stale._set_state(
        artifact_export_confirmation=ArtifactExportTargetView(
            artifact_key="ghost:1",
            format="md",
            target_path="C:/exports/a.md",
            size=1,
            digest="b" * 64,
            volume_serial=7,
            file_id=11,
        )
    )
    asyncio.run(stale.confirm_selected_export())
    assert stale.state.artifact_export_error_key == ARTIFACT_NOT_FOUND_KEY

    cancel_pending = _vm(
        delivery=_FakeDelivery(),
        selected=("a-1:1",),
    )
    cancel_pending._set_state(
        artifact_export_pending=True,
        artifact_export_confirmation=ArtifactExportTargetView(
            artifact_key="a-1:1",
            format="md",
            target_path="C:/exports/a.md",
            size=1,
            digest="b" * 64,
            volume_serial=7,
            file_id=11,
        ),
    )
    cancel_pending.cancel_selected_export()
    assert cancel_pending.state.artifact_export_confirmation is not None


def test_resolve_artifact_delivery_requires_ready_wiring_with_methods() -> None:
    class _Port:
        async def preview(self, **_: Any) -> Any: ...
        async def inspect_target(self, **_: Any) -> Any: ...
        async def export(self, **_: Any) -> Any: ...

    assert resolve_artifact_delivery(None) is None
    assert resolve_artifact_delivery(SimpleNamespace(ready=False, delivery=_Port())) is None
    assert resolve_artifact_delivery(SimpleNamespace(ready=True, delivery=None)) is None
    assert resolve_artifact_delivery(SimpleNamespace(ready=True, delivery=object())) is None
    port = _Port()
    assert resolve_artifact_delivery(SimpleNamespace(ready=True, delivery=port)) is port


# ----------------------------------------------------------------------
# 视图渲染分支（回执/预览/确认/复制/危险摘要）
# ----------------------------------------------------------------------


def test_render_helpers_cover_unselected_unknown_and_meta() -> None:
    assert _get_page() is None
    assert artifact_unavailable_text(None) == I18n.get("agent_research_artifact_body_unloaded")
    assert "custom_reason" in artifact_unavailable_text("custom_reason")
    assert artifact_selection_reason(ResearchObserverState()) == I18n.get("agent_research_no_artifacts")
    assert artifact_selection_reason(
        ResearchObserverState(
            available=True,
            artifacts=(_artifact("a-1"), _artifact("a-2")),
            artifact_selected_keys=("a-1:1", "a-2:1"),
            artifact_delivery_available=True,
        )
    ) == I18n.get("agent_research_artifact_select_one")
    unknown_size = artifact_overwrite_summary(
        SimpleNamespace(  # type: ignore[arg-type]  # 测试用结构等价对象
            target_path=r"C:\exports\a.md", size=None, digest="d" * 64
        )
    )
    assert I18n.get("agent_research_artifact_export_size_unknown") in unknown_size

    preview = ArtifactPreviewView("a-1", 1, "xlsx", "字段表", "e" * 64, 12, 3, "note")
    assert "XLSX" in artifact_preview_meta(preview)
    assert artifact_preview_meta(ArtifactPreviewView("a-1", 1, "md", "x", "e" * 64, 1, 1, None)).startswith("Markdown")


@pytest.mark.parametrize("phase", ["published_confirmed", "unknown", "cancelled_before_publish", "staged"])
def test_receipt_lines_cover_three_states_and_failure(phase: str) -> None:
    receipt = ArtifactDeliveryReceiptView(
        delivery_id="d-1",
        phase=phase,
        publish_effect="not_published",
        cancel_effect="cancelled_before_publish",
        error_code="delivery_target_changed" if phase == "staged" else None,
        next_action="check_original_delivery" if phase == "unknown" else None,
        output_digest=None,
        verified_at="2026-11-17T06:00:00.000000Z",
        target_path="C:/exports/a.md",
        staged_cleaned=False,
    )

    lines = artifact_receipt_lines(receipt)

    expected_first = {
        "published_confirmed": I18n.get("agent_research_artifact_export_success"),
        "unknown": I18n.get("agent_research_artifact_export_unknown"),
        "cancelled_before_publish": I18n.get("agent_research_artifact_export_cancelled"),
    }.get(phase)
    if expected_first is not None:
        assert lines[0] == expected_first
    else:
        assert "delivery_target_changed" in lines[0]
    assert any(I18n.get("agent_research_artifact_export_staged_dirty") == line for line in lines)


def test_artifact_card_renders_loaded_unavailable_and_loading_states() -> None:
    toggled: list[str] = []
    loaded = build_artifact_card(
        _artifact(),
        loaded=_body("a-1:1", "完整正文", truncated=True),
        loading=False,
        on_load_artifact=lambda *_args: None,
        selected=True,
        on_toggle_selection=toggled.append,
    )
    checkbox = next(item for item in _walk(loaded) if isinstance(item, ft.Checkbox))
    _run_handler(checkbox.on_change)
    assert toggled == ["a-1:1"]
    assert any(getattr(item, "value", None) == I18n.get("agent_research_artifact_truncated") for item in _walk(loaded))

    unavailable = build_artifact_card(
        _artifact(),
        loaded=_body("a-1:1", "x", available=False),
        loading=False,
        on_load_artifact=lambda *_args: None,
    )
    assert any(
        getattr(item, "value", None) == I18n.get("agent_research_artifact_body_cleaned") for item in _walk(unavailable)
    )

    loading_card = build_artifact_card(
        _artifact(),
        loaded=None,
        loading=True,
        on_load_artifact=lambda *_args: None,
    )
    assert any(isinstance(item, ft.ProgressRing) for item in _walk(loading_card))

    load_requested: list[tuple[str, int, str, str]] = []
    unloaded = build_artifact_card(
        _artifact(),
        loaded=None,
        loading=False,
        on_load_artifact=lambda *args: load_requested.append(args),
    )
    load_button = next(item for item in _walk(unloaded) if isinstance(item, ft.IconButton))
    _run_handler(load_button.on_click)
    assert load_requested == [("a-1", 1, "e-1", "r-1")]


def test_delivery_actions_render_copy_reason_and_unwired_hint() -> None:
    state = ResearchObserverState(
        available=True,
        artifacts=(_artifact(),),
        artifact_delivery_available=False,
        artifact_copy_error_key=ARTIFACT_COPY_CLIPBOARD_FAILED_KEY,
        artifact_copy_notice_key=ARTIFACT_COPY_DONE_KEY,
    )

    control = build_artifact_delivery_actions(
        state,
        on_format_change=lambda _value: None,
        on_target_path_change=lambda _value: None,
        on_preview=_async_noop,
        on_copy=_async_noop,
        on_export=_async_noop,
    )
    texts = [getattr(item, "value", None) for item in _walk(control)]
    assert I18n.get(ARTIFACT_COPY_CLIPBOARD_FAILED_KEY) in texts
    assert I18n.get(ARTIFACT_COPY_DONE_KEY) in texts
    assert I18n.get(ARTIFACT_DELIVERY_UNAVAILABLE_KEY) in texts


def test_preview_panel_renders_pending_error_and_content() -> None:
    pending = build_artifact_preview_panel(ResearchObserverState(artifact_preview_pending=True))
    assert pending is not None
    assert any(isinstance(item, ft.ProgressRing) for item in _walk(pending))

    error = build_artifact_preview_panel(
        ResearchObserverState(
            artifact_preview_error_key=ARTIFACT_COPY_INCOMPLETE_KEY,
            artifact_preview_error_code="body_cleaned",
        )
    )
    assert error is not None
    error_texts = [getattr(item, "value", None) for item in _walk(error)]
    assert any(isinstance(value, str) and "body_cleaned" in value for value in error_texts)

    content = build_artifact_preview_panel(
        ResearchObserverState(
            artifact_preview=ArtifactPreviewView(
                "a-1", 1, "xlsx", "字段表预览", "e" * 64, 12, 3, "agent_research_artifact_preview_field_view"
            )
        )
    )
    assert content is not None
    field = next(item for item in _walk(content) if isinstance(item, ft.TextField))
    assert field.value == "字段表预览"
    assert field.tooltip == I18n.get("agent_research_artifact_preview")

    assert build_artifact_preview_panel(ResearchObserverState()) is None


def test_export_panels_render_confirmation_notice_receipt_and_codes() -> None:
    confirmation = ArtifactExportTargetView(
        artifact_key="a-1:1",
        format="md",
        target_path="C:/exports/a.md",
        size=7,
        digest="b" * 64,
        volume_serial=7,
        file_id=11,
    )
    row = ArtifactDeliveryReceiptView(
        delivery_id="d-1",
        phase="unknown",
        publish_effect="not_published",
        cancel_effect="cancelled_before_publish",
        error_code="delivery_publish_unknown",
        next_action="check_original_delivery",
        output_digest=None,
        verified_at="2026-11-17T06:00:00.000000Z",
        target_path="C:/exports/a.md",
        staged_cleaned=True,
    )
    panels = build_artifact_export_panels(
        ResearchObserverState(
            artifact_export_confirmation=confirmation,
            artifact_export_notice_key=ARTIFACT_EXPORT_UNKNOWN_NOTICE_KEY,
            artifact_export_receipt=row,
            artifact_export_error_key=ARTIFACT_EXPORT_UNKNOWN_BLOCKED_KEY,
            artifact_export_error_code="delivery_publish_unknown",
        )
    )
    assert len(panels) >= 4
    texts = [getattr(item, "value", None) for _panel in panels for item in _walk(_panel)]
    assert I18n.get("agent_research_artifact_export_unknown") in texts
    assert I18n.get(ARTIFACT_EXPORT_UNKNOWN_BLOCKED_KEY) in texts
    assert any(isinstance(value, str) and "delivery_publish_unknown" in value for value in texts)


def test_artifacts_section_confirm_buttons_and_empty_state() -> None:
    state = ResearchObserverState(
        available=True,
        artifacts=(_artifact(),),
        artifact_delivery_available=True,
        artifact_selected_keys=("a-1:1",),
        artifact_export_confirmation=ArtifactExportTargetView(
            artifact_key="a-1:1",
            format="md",
            target_path="C:/exports/a.md",
            size=7,
            digest="b" * 64,
            volume_serial=7,
            file_id=11,
        ),
    )
    confirmed: list[bool] = []
    cancelled: list[bool] = []
    select_all: list[bool] = []
    cleared: list[bool] = []

    async def _confirm() -> None:
        confirmed.append(True)

    controls = build_artifacts_section(
        state,
        on_load_artifact=lambda *_args: None,
        on_toggle_artifact_selection=lambda _key: None,
        on_select_all_artifacts=lambda: select_all.append(True),
        on_clear_artifact_selection=lambda: cleared.append(True),
        on_artifact_format_change=lambda _value: None,
        on_artifact_target_path_change=lambda _value: None,
        on_preview_artifact=_async_noop,
        on_copy_artifacts=lambda: None,
        on_artifact_copy_done=lambda: None,
        on_artifact_copy_failed=lambda: None,
        on_request_export=_async_noop,
        on_confirm_export=_confirm,
        on_cancel_export=lambda: cancelled.append(True),
    )
    buttons = [item for item in _walk(controls) if isinstance(item, (ft.FilledButton, ft.OutlinedButton))]
    confirm_button = next(
        item
        for item in buttons
        if getattr(item.content, "value", None) == I18n.get("agent_research_artifact_export_overwrite_confirm")
    )
    _run_handler(confirm_button.on_click)
    assert confirmed == [True]

    cancel_button = next(
        item
        for item in buttons
        if getattr(item.content, "value", None) == I18n.get("agent_research_artifact_export_overwrite_cancel")
    )
    _run_handler(cancel_button.on_click)
    assert cancelled == [True]

    select_all_button = next(item for item in buttons if getattr(item, "icon", None) == ft.Icons.SELECT_ALL)
    _run_handler(select_all_button.on_click)
    clear_button = next(item for item in buttons if getattr(item, "icon", None) == ft.Icons.DESELECT)
    _run_handler(clear_button.on_click)
    assert select_all == [True]
    assert cleared == [True]

    empty_rows = build_artifacts_section(ResearchObserverState(), on_load_artifact=lambda *_args: None)
    assert any(getattr(item, "value", None) == I18n.get("agent_research_no_artifacts") for item in _walk(empty_rows))

    more_rows = build_artifacts_section(
        ResearchObserverState(available=True, artifacts=(_artifact(),), artifacts_has_more=True),
        on_load_artifact=lambda *_args: None,
    )
    assert any(getattr(item, "value", None) == I18n.get("agent_research_more_items") for item in _walk(more_rows))


def test_copy_handler_marks_done_only_after_clipboard_write(monkeypatch: pytest.MonkeyPatch) -> None:
    done: list[bool] = []
    failed: list[bool] = []

    class _Clipboard:
        def __init__(self) -> None:
            self.written: list[str] = []

        async def set(self, value: str) -> None:
            self.written.append(value)

    class _FailingClipboard(_Clipboard):
        async def set(self, value: str) -> None:
            raise RuntimeError("clipboard unavailable")

    page = SimpleNamespace(services=[])

    def _build(clipboard: Any) -> list[Any]:
        monkeypatch.setattr(artifacts_view, "_get_page", lambda: page)
        monkeypatch.setattr(artifacts_view.ft, "Clipboard", lambda: clipboard)
        return build_artifacts_section(
            ResearchObserverState(
                available=True,
                artifacts=(_artifact(),),
                artifact_delivery_available=True,
                artifact_selected_keys=("a-1:1",),
            ),
            on_load_artifact=lambda *_args: None,
            on_toggle_artifact_selection=lambda _key: None,
            on_select_all_artifacts=lambda: None,
            on_clear_artifact_selection=lambda: None,
            on_artifact_format_change=lambda _value: None,
            on_artifact_target_path_change=lambda _value: None,
            on_preview_artifact=_async_noop,
            on_copy_artifacts=lambda: "正文",
            on_artifact_copy_done=lambda: done.append(True),
            on_artifact_copy_failed=lambda: failed.append(True),
            on_request_export=_async_noop,
            on_confirm_export=_async_noop,
            on_cancel_export=lambda: None,
        )

    clipboard = _Clipboard()
    controls = _build(clipboard)
    copy_button = next(
        item
        for item in _walk(controls)
        if isinstance(item, ft.OutlinedButton)
        and getattr(item.content, "value", None) == I18n.get("agent_research_artifact_copy")
    )
    _run_handler(copy_button.on_click)
    assert clipboard.written == ["正文"]
    assert done == [True]

    failing = _FailingClipboard()
    controls = _build(failing)
    copy_button = next(
        item
        for item in _walk(controls)
        if isinstance(item, ft.OutlinedButton)
        and getattr(item.content, "value", None) == I18n.get("agent_research_artifact_copy")
    )
    _run_handler(copy_button.on_click)
    assert failed == [True]

    controls = _build(clipboard)
    monkeypatch.setattr(artifacts_view, "_get_page", lambda: None)
    copy_button = next(
        item
        for item in _walk(controls)
        if isinstance(item, ft.OutlinedButton)
        and getattr(item.content, "value", None) == I18n.get("agent_research_artifact_copy")
    )
    before = len(done)
    _run_handler(copy_button.on_click)
    assert len(done) == before


# ----------------------------------------------------------------------
# 适配器分页/校验分支
# ----------------------------------------------------------------------


class _SequenceDao:
    def __init__(self, reads: list[Any]) -> None:
        self._reads = list(reads)
        self.queries: list[Any] = []
        self.operations: list[dict[str, Any]] = []

    async def read_artifact(self, query: Any, *, user_scope: str) -> Any:
        self.queries.append(query)
        return self._reads.pop(0)

    async def ensure_model_operation(self, **kwargs: Any) -> bool:
        self.operations.append(kwargs)
        return True


def _page_read(*, artifact_id: str = "a-1", body: str | None, cursor: str | None, states: tuple[Any, ...] = ()) -> Any:
    return SimpleNamespace(
        artifact_id=artifact_id,
        artifact_version=1,
        execution_id="e-1",
        round_id="r-1",
        body_text=body,
        body_available=True,
        next_cursor=cursor,
        source_ref_states=states,
        source_available_state="available",
    )


def test_adapter_preview_concatenates_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    dao = _SequenceDao(
        [_page_read(body="前半", cursor="c1"), _page_read(body="后半", cursor=None), _page_read(body=None, cursor=None)]
    )
    adapter = AgentArtifactDelivery(dao=dao, user_scope="u", store=object())
    captured: dict[str, Any] = {}

    def _fields(read: Any, **kwargs: Any) -> tuple[Any, ...]:
        captured.update(kwargs)
        return (DeliveryField(record="metadata", pointer="k", value_type="string", value_text="v"),)

    monkeypatch.setattr(agent_wiring, "build_delivery_fields", _fields)
    monkeypatch.setattr(
        agent_wiring, "render_delivery", lambda fields, format_name: SimpleNamespace(content=b"x", digest="e" * 64)
    )

    asyncio.run(
        adapter.preview(artifact_id="a-1", artifact_version=1, execution_id="e-1", round_id="r-1", format_name="md")
    )

    assert captured["body_text"] == "前半后半"
    assert captured["current_source_state"] == "unknown"
    assert captured["current_source_states"] == ()


def test_adapter_preview_aggregates_source_states(monkeypatch: pytest.MonkeyPatch) -> None:
    dao = _SequenceDao([_page_read(body="正文", cursor=None), _page_read(body=None, cursor=None, states=("s1",))])
    adapter = AgentArtifactDelivery(dao=dao, user_scope="u", store=object())
    captured: dict[str, Any] = {}

    monkeypatch.setattr(agent_wiring, "aggregate_delivery_source_state", lambda states: "aggregate-ok")
    monkeypatch.setattr(
        agent_wiring,
        "build_delivery_fields",
        lambda read, **kwargs: captured.update(kwargs) or (),
    )
    monkeypatch.setattr(
        agent_wiring, "render_delivery", lambda fields, format_name: SimpleNamespace(content=b"", digest="e" * 64)
    )

    asyncio.run(
        adapter.preview(artifact_id="a-1", artifact_version=1, execution_id="e-1", round_id="r-1", format_name="md")
    )

    assert captured["current_source_state"] == "aggregate-ok"
    assert captured["current_source_states"] == ("s1",)


@pytest.mark.parametrize(
    ("first", "second", "message"),
    [
        (
            _page_read(artifact_id="other", body="x", cursor=None),
            _page_read(body=None, cursor=None),
            "identity mismatch",
        ),
        (_page_read(body="x", cursor=None), _page_read(body=None, cursor=None), "execution/round mismatch"),
    ],
)
def test_adapter_preview_rejects_identity_mismatch(
    monkeypatch: pytest.MonkeyPatch, first: Any, second: Any, message: str
) -> None:
    if message == "execution/round mismatch":
        first.execution_id = "e-other"
    dao = _SequenceDao([first, second])
    adapter = AgentArtifactDelivery(dao=dao, user_scope="u", store=object())
    with pytest.raises(AgentServiceError) as excinfo:
        asyncio.run(
            adapter.preview(artifact_id="a-1", artifact_version=1, execution_id="e-1", round_id="r-1", format_name="md")
        )
    assert message in str(excinfo.value)


def test_adapter_preview_rejects_identity_change_and_stuck_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    changed = _SequenceDao([_page_read(body="x", cursor="c1"), _page_read(artifact_id="other", body="y", cursor=None)])
    adapter = AgentArtifactDelivery(dao=changed, user_scope="u", store=object())
    with pytest.raises(AgentServiceError) as changed_exc:
        asyncio.run(
            adapter.preview(artifact_id="a-1", artifact_version=1, execution_id="e-1", round_id="r-1", format_name="md")
        )
    assert "identity changed" in str(changed_exc.value)

    stuck = _SequenceDao([_page_read(body="x", cursor="c1"), _page_read(body="y", cursor="c1")])
    adapter = AgentArtifactDelivery(dao=stuck, user_scope="u", store=object())
    with pytest.raises(AgentServiceError) as stuck_exc:
        asyncio.run(
            adapter.preview(artifact_id="a-1", artifact_version=1, execution_id="e-1", round_id="r-1", format_name="md")
        )
    assert "cursor did not advance" in str(stuck_exc.value)


def test_adapter_inspect_target_maps_oserror_to_service_error(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = AgentArtifactDelivery(dao=_FakeDao([]), user_scope="u", store=object())

    def _unreadable(path: Path) -> Any:
        raise OSError("unreadable target")

    monkeypatch.setattr(agent_wiring, "_snapshot", _unreadable)

    with pytest.raises(AgentServiceError) as excinfo:
        asyncio.run(adapter.inspect_target(target_path=r"C:\exports\blocked.md"))

    assert getattr(excinfo.value.code, "value", excinfo.value.code) == "INPUT_INVALID"


def test_adapter_export_rejects_relative_target() -> None:
    adapter = AgentArtifactDelivery(dao=_FakeDao([]), user_scope="u", store=object())

    with pytest.raises(AgentValidationError) as excinfo:
        asyncio.run(
            adapter.export(
                artifact_id="a-1",
                artifact_version=1,
                execution_id="e-1",
                round_id="r-1",
                format_name="md",
                target_path="relative.md",
                expected_existing=False,
                expected_digest=None,
                expected_volume_serial=None,
                expected_file_id=None,
            )
        )
    assert getattr(excinfo.value.code, "value", excinfo.value.code) == "invalid_target_path"


def test_build_artifact_delivery_propagates_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []

    class _Cancelled:
        def __init__(self, **_: Any) -> None:
            attempts.append(1)
            raise asyncio.CancelledError

    monkeypatch.setattr(agent_wiring, "AgentArtifactDelivery", _Cancelled)
    try:
        _build_artifact_delivery(dao=_FakeDao([]), user_scope="u")
    except asyncio.CancelledError:
        assert attempts == [1]
    else:
        pytest.fail("CancelledError 未传播")
