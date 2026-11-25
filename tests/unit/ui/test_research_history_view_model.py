"""W-04 历史浏览/检索/改名归档/清理确认/停止使用来源的 ViewModel 单元测试。

全部使用替代端口（不连 DB/网络），覆盖：筛选与游标续翻、读取失败不伪装空列表、
改名 CAS 冲突报码、清理预览/确认/未准备阻断、停止使用来源入口与回执。
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    CleanupJobRecord,
    CleanupPhase,
)
from services.agent_service.cleanup import CleanupConfirmCommand
from services.agent_service.cleanup_execution import CleanupExecutionReceipt
from services.agent_history import HistoryCleanupGateway
from ui.viewmodels.research_observer_view_model import (
    HISTORY_PAGE_LIMIT,
    CleanupPreviewView,
    ResearchObserverViewModel,
    SessionListItemView,
)

pytestmark = pytest.mark.asyncio

_USER_SCOPE = "local-desktop"


def _hit(key: str, *, session_id: str = "s-1", kind: str = "message") -> SimpleNamespace:
    return SimpleNamespace(
        hit_kind=kind,
        session_id=session_id,
        created_at="2026-11-24T00:00:00Z",
        summary_text=f"摘要-{key}",
        execution_id=f"e-{key}" if kind == "message" else None,
        node_id=None,
        round_id=f"r-{key}",
        message_id=key if kind == "message" else None,
        artifact_id=key if kind == "artifact" else None,
        artifact_version=1 if kind == "artifact" else None,
        role="user",
        artifact_kind="report" if kind == "artifact" else None,
    )


def _page(hits: tuple[SimpleNamespace, ...], cursor: str | None) -> SimpleNamespace:
    return SimpleNamespace(hits=hits, next_cursor=cursor, read_moment="2026-11-24T00:00:00Z")


def _read(
    session_id: str,
    *,
    title: str = "会话一",
    revision: int = 4,
    archived: bool = False,
    cleaned: bool = False,
    messages: tuple[Any, ...] = (),
) -> SimpleNamespace:
    summary = SimpleNamespace(
        title=title,
        updated_at="2026-11-24T00:00:00Z",
        revision=revision,
        pinned=False,
        archived=archived,
        cleaned_at="2026-11-24T00:00:00Z" if cleaned else None,
        draft_revision=0,
    )
    return SimpleNamespace(
        summary=summary,
        executions=(),
        messages=SimpleNamespace(items=messages, has_more=False),
        artifacts=SimpleNamespace(items=(), has_more=False),
        decisions=(),
        read_moment="2026-11-24T00:00:00Z",
    )


class _Service:
    def __init__(
        self,
        *,
        pages: list[SimpleNamespace] | None = None,
        read: SimpleNamespace | None = None,
        search_error: Exception | None = None,
        change_error: Exception | None = None,
        stop_receipt: SimpleNamespace | None = None,
        stop_error: Exception | None = None,
    ) -> None:
        self.pages = list(pages or [])
        self.read = read or _read("s-1")
        self.search_error = search_error
        self.change_error = change_error
        self.stop_receipt = stop_receipt
        self.stop_error = stop_error
        self.queries: list[Any] = []
        self.changes: list[Any] = []
        self.stops: list[Any] = []

    async def search(self, query: Any, *, user_scope: str) -> Any:
        self.queries.append(query)
        if self.search_error is not None:
            raise self.search_error
        if not self.pages:
            return _page((), None)
        return self.pages.pop(0)

    async def read_session(self, query: Any, *, user_scope: str) -> Any:
        return self.read

    async def change_session(self, command: Any) -> Any:
        self.changes.append(command)
        if self.change_error is not None:
            raise self.change_error
        return SimpleNamespace(session_id=command.session_id, revision=command.expected_session_revision + 1)

    async def stop_using(self, request: Any) -> Any:
        self.stops.append(request)
        if self.stop_error is not None:
            raise self.stop_error
        return self.stop_receipt


def _record() -> CleanupJobRecord:
    return CleanupJobRecord(
        user_scope=_USER_SCOPE,
        cleanup_job_id="ui-cleanup:s-1",
        owner_ref="owner",
        preview_digest="b" * 64,
        expected_revision=7,
        exact_ids=("wf-1", "wf-2"),
        phase=CleanupPhase.PREPARED,
        fence_committed=False,
        session_ids=("s-1",),
        asset_keys=(("asset-1", "1"),),
    )


class _Store:
    def __init__(self, record: CleanupJobRecord | None) -> None:
        self.record = record

    async def load(self, user_scope: str, cleanup_job_id: str) -> CleanupJobRecord | None:
        return self.record


class _Execution:
    def __init__(self, *, phase: CleanupPhase = CleanupPhase.VERIFIED) -> None:
        self.phase = phase
        self.commands: list[CleanupConfirmCommand] = []

    async def confirm_and_execute(self, command: CleanupConfirmCommand) -> CleanupExecutionReceipt:
        self.commands.append(command)
        return CleanupExecutionReceipt(
            cleanup_job_id=command.cleanup_job_id,
            phase=self.phase,
            replayed=False,
            verified_counts={"agent_ds.datasource_outputs": 0},
            tombstoned={"agent_session": 1},
        )


def _view_model(service: Any, *, cleanup: HistoryCleanupGateway | None = None) -> ResearchObserverViewModel:
    return ResearchObserverViewModel(
        service,
        user_scope=_USER_SCOPE,
        cleanup=cleanup,
        cleanup_unavailable_reason="cleanup_wiring_failed:test",
    )


async def test_history_search_applies_filters_and_paginates_with_cursor() -> None:
    service = _Service(pages=[_page((_hit("m1"),), "cursor-2"), _page((_hit("m1"), _hit("a1", kind="artifact")), None)])
    vm = _view_model(service)

    vm.set_history_query("  回撤  ")
    vm.set_history_scope_filter("child")
    await vm.run_history_search()

    first = service.queries[0]
    assert first.query_text == "回撤"
    assert first.type_filter == "child"
    assert first.cursor is None
    assert first.limit == HISTORY_PAGE_LIMIT
    assert vm.state.history_hits[0].location == "execution:e-m1 · round:r-m1 · message:m1"
    assert vm.state.history_has_more is True

    await vm.load_more_history()

    assert service.queries[1].cursor == "cursor-2"
    # 重复命中按 (kind, 会话, 地址) 去重，仅追加新命中。
    assert [hit.hit_kind for hit in vm.state.history_hits] == ["message", "artifact"]
    assert vm.state.history_has_more is False
    assert vm.state.history_cursor is None


async def test_history_search_failure_keeps_existing_hits_and_reports_code() -> None:
    service = _Service(pages=[_page((_hit("m1"),), None)])
    vm = _view_model(service)
    await vm.run_history_search()
    assert len(vm.state.history_hits) == 1

    service.search_error = AgentServiceError(
        AgentErrorCode.PERSISTENCE_UNAVAILABLE, "index unavailable", "history/search"
    )
    await vm.run_history_search()

    assert len(vm.state.history_hits) == 1
    assert vm.state.history_error_code == "PERSISTENCE_UNAVAILABLE"
    assert vm.state.history_loading is False


async def test_rename_revision_conflict_is_reported_explicitly() -> None:
    service = _Service(
        change_error=AgentServiceError(AgentErrorCode.REVISION_CONFLICT, "stale revision", "session/s-1")
    )
    vm = _view_model(service)
    vm._set_state(
        sessions=(
            SessionListItemView(
                session_id="s-1",
                title="旧名",
                updated_at="2026-11-24T00:00:00Z",
                revision=3,
                pinned=False,
                archived=False,
                cleaned=False,
                status_unknown=False,
            ),
        )
    )

    vm.begin_rename("s-1")
    vm.set_rename_text("新名")
    await vm.commit_rename()

    assert service.changes[0].expected_session_revision == 3
    assert vm.state.session_action_error_key is None
    assert vm.state.session_action_error_code == "REVISION_CONFLICT"


async def test_cleanup_preview_then_explicit_confirm_returns_receipt() -> None:
    execution = _Execution()
    gateway = HistoryCleanupGateway(store=_Store(_record()), execution=execution)
    vm = _view_model(_Service(), cleanup=gateway)
    await vm.open_session("s-1")
    vm.refresh_history_usage()

    await vm.begin_cleanup()

    preview = vm.state.cleanup_preview
    assert isinstance(preview, CleanupPreviewView)
    assert preview.exact_count == 2
    assert vm.state.usage is not None
    assert vm.state.usage.exact_bytes is None
    assert execution.commands == []

    await vm.confirm_cleanup(False)
    assert vm.state.cleanup_error_key == "agent_research_cleanup_confirm_required"

    await vm.confirm_cleanup(True)

    assert len(execution.commands) == 1
    assert execution.commands[0].preview_digest == "b" * 64
    assert vm.state.cleanup_receipt is not None
    assert vm.state.cleanup_receipt.verified is True
    assert vm.state.cleanup_preview is None


async def test_cleanup_without_prepared_job_is_blocked_not_forced() -> None:
    gateway = HistoryCleanupGateway(store=_Store(None), execution=_Execution())
    vm = _view_model(_Service(), cleanup=gateway)
    await vm.open_session("s-1")

    await vm.begin_cleanup()

    assert vm.state.cleanup_preview is None
    assert vm.state.cleanup_error_key == "agent_research_cleanup_job_not_prepared"
    assert vm.state.cleanup_error_code == "CONFIG_UNAVAILABLE"


async def test_cleanup_without_wiring_is_blocked_with_reason() -> None:
    vm = _view_model(_Service())

    await vm.open_session("s-1")
    await vm.begin_cleanup()

    assert vm.state.cleanup_available is False
    assert vm.state.cleanup_error_key == "agent_research_cleanup_unavailable"
    assert vm.state.cleanup_error_code == "cleanup_wiring_failed:test"


async def test_stop_source_requires_inputs_then_records_receipt() -> None:
    receipt = SimpleNamespace(
        source_id="src-1",
        source_version="v3",
        disabled_revision=9,
        already_disabled=False,
        disabled_at="2026-11-24T00:00:00Z",
    )
    service = _Service(stop_receipt=receipt)
    vm = _view_model(service)

    await vm.stop_source_use()
    assert vm.state.stop_source_error_key == "agent_research_stop_source_required"
    assert service.stops == []

    vm.set_stop_source_id("src-1")
    vm.set_stop_source_version("v3")
    await vm.stop_source_use()

    assert service.stops[0].command_id == "ui-stop:src-1@v3"
    assert vm.state.stop_source_receipt is not None
    assert vm.state.stop_source_receipt.disabled_revision == 9
    assert vm.state.stop_source_id == ""


async def test_stop_source_failure_reports_code_without_receipt() -> None:
    service = _Service(stop_error=AgentServiceError(AgentErrorCode.SOURCE_DISABLED, "source disabled", "src-1"))
    vm = _view_model(service)
    vm.set_stop_source_id("src-1")
    vm.set_stop_source_version("v3")

    await vm.stop_source_use()

    assert vm.state.stop_source_receipt is None
    assert vm.state.stop_source_error_code == "SOURCE_DISABLED"


async def test_usage_marks_truncation_and_unknown_bytes() -> None:
    vm = _view_model(_Service(read=_read("s-1", messages=(SimpleNamespace(), SimpleNamespace()))))
    await vm.open_session("s-1")
    vm._raw_messages = (SimpleNamespace(), SimpleNamespace())
    vm._set_state(messages_has_more=True)

    vm.refresh_history_usage()

    usage = vm.state.usage
    assert usage is not None
    assert usage.messages == 2
    assert usage.messages_truncated is True
    assert usage.exact_bytes is None


async def test_history_page_limit_is_within_service_bound() -> None:
    # 服务层 HistoryQuery.limit 硬上界 50；UI 分页大小不得越界。
    assert 1 <= HISTORY_PAGE_LIMIT <= 50
    assert inspect.iscoroutinefunction(ResearchObserverViewModel.run_history_search)


async def test_unknown_filters_are_ignored() -> None:
    vm = _view_model(_Service())

    vm.set_history_scope_filter("grandchild")
    vm.set_session_status_filter("deleted")

    assert vm.state.history_scope_filter == "all"
    assert vm.state.session_status_filter == "all"


async def test_cancel_cleanup_clears_preview_without_side_effects() -> None:
    gateway = HistoryCleanupGateway(store=_Store(_record()), execution=_Execution())
    vm = _view_model(_Service(), cleanup=gateway)
    await vm.open_session("s-1")
    await vm.begin_cleanup()
    assert vm.state.cleanup_preview is not None

    vm.cancel_cleanup()

    assert vm.state.cleanup_preview is None
    assert vm.state.cleanup_receipt is None


async def test_stop_source_requires_wired_service_method() -> None:
    # 服务对象未提供 stop_using（与其能力子集端口一致）：如实报未接线。
    vm = _view_model(SimpleNamespace())
    vm.set_stop_source_id("src-1")
    vm.set_stop_source_version("v1")

    await vm.stop_source_use()

    assert vm.state.stop_source_error_key == "agent_research_not_wired"
    assert vm.state.stop_source_receipt is None
