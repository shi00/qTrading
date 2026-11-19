"""R2-I06：业务对象 → 研究草稿准备（不发送）的最小验证。"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from core.agent_types import AgentErrorCode, AgentServiceError, Purpose, ReadState, VerificationState
from services.agent_service.business_research import (
    BusinessDraftAlreadyPrepared,
    BusinessObjectSnapshot,
    BusinessResearchPreparer,
    BusinessSourceUnavailable,
    backtest_preparation,
    snapshot_source,
)
from services.agent_service.business_sources import BusinessSnapshotRead
from ui.viewmodels.business_research_view_model import (
    NOTICE_DRAFT_EXISTS,
    NOTICE_DRAFT_PREPARED,
    NOTICE_PREPARE_FAILED,
    NOTICE_SOURCE_UNAVAILABLE,
    OBJECT_KIND_BACKTEST,
    UNAVAILABLE_NOT_READY,
    UNAVAILABLE_NOT_WIRED,
    BusinessResearchRequest,
    BusinessResearchViewModel,
    reset_shared_business_research,
    resolve_business_research_service,
    shared_business_research,
)


class _FakeFacade:
    def __init__(self, *, existing_revision: int | None = None) -> None:
        self.calls: list[str] = []
        self.saved_payload = None
        self.registered_items: tuple = ()
        self._existing_revision = existing_revision

    async def read_session(self, query, *, user_scope: str):
        self.calls.append("read_session")
        if self._existing_revision is None:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "session not found", query.session_id)
        return SimpleNamespace(summary=SimpleNamespace(draft_revision=self._existing_revision))

    async def change_session(self, command):
        self.calls.append("change_session")
        return SimpleNamespace()

    async def register_draft_sources(self, *, user_scope, session_id, draft_revision, items):
        self.calls.append("register_draft_sources")
        self.registered_items = items
        return SimpleNamespace(registered=(), link_ids=(), replayed=False)

    async def save_draft(self, command):
        self.calls.append("save_draft")
        self.saved_payload = command.payload
        return SimpleNamespace(draft_revision=command.expected_draft_revision + 1)


def _snapshot(**overrides):
    values = {
        "source_module": "screener",
        "object_kind": "screener_candidate",
        "object_id": "600519.SH",
        "display_name": "贵州茅台 (600519.SH)",
        "summary": {"ts_code": "600519.SH", "score": "88.5"},
        "available_at": "2026-11-18T01:00:00.000000Z",
        "period": None,
    }
    values.update(overrides)
    return BusinessObjectSnapshot(**values)


def test_snapshot_source_freezes_version_and_marks_unverified():
    item = snapshot_source(_snapshot(), user_scope="local-desktop")

    assert item.source.source_id == "screener_candidate:600519.SH"
    assert item.source.version == item.source.digest
    assert item.source.available_at == "2026-11-18T01:00:00.000000Z"
    # UI 捕获快照未经程序核验：不得自称 program_verified（R21）
    assert item.verification is VerificationState.UNVERIFIED


def test_snapshot_source_rejects_unknown_availability():
    with pytest.raises(BusinessSourceUnavailable) as exc:
        snapshot_source(_snapshot(available_at=None), user_scope="local-desktop")
    assert exc.value.reason == "available_at_unknown"


def test_snapshot_version_changes_with_content():
    first = snapshot_source(_snapshot(), user_scope="local-desktop")
    second = snapshot_source(_snapshot(summary={"ts_code": "600519.SH", "score": "77.0"}), user_scope="local-desktop")
    assert first.source.version != second.source.version


@pytest.mark.asyncio
async def test_prepare_creates_draft_without_sending():
    facade = _FakeFacade()
    preparer = BusinessResearchPreparer(facade)

    receipt = await preparer.prepare_object(_snapshot(), user_scope="local-desktop")

    assert receipt.sent is False
    assert receipt.draft_revision == 2
    assert facade.calls == ["read_session", "change_session", "register_draft_sources", "save_draft"]
    assert "submit" not in facade.calls
    assert facade.registered_items[0].source.source_id == "screener_candidate:600519.SH"
    payload = facade.saved_payload
    assert payload.source_refs[0].source_id == "screener_candidate:600519.SH"
    # 截止界不伪造：草稿期留空，由用户发送前确认（R21/R24）
    assert payload.cutoff is None
    assert payload.purpose is Purpose.END_OF_DAY_RESEARCH
    assert '"object_kind":"screener_candidate"' in payload.object_context


@pytest.mark.asyncio
async def test_prepare_keeps_existing_non_empty_draft():
    facade = _FakeFacade(existing_revision=3)
    preparer = BusinessResearchPreparer(facade)

    with pytest.raises(BusinessDraftAlreadyPrepared) as exc:
        await preparer.prepare_object(_snapshot(), user_scope="local-desktop")
    assert exc.value.draft_revision == 3
    # 不覆盖既有草稿：只读探测后即停止
    assert "save_draft" not in facade.calls
    assert "register_draft_sources" not in facade.calls


class _FakeReader:
    def __init__(self, row: dict | None) -> None:
        self._row = row

    async def get_result(self, run_id: str, *, suppress_errors: bool = True) -> dict | None:
        return self._row


@pytest.mark.asyncio
async def test_prepare_backtest_uses_strict_snapshot_read():
    facade = _FakeFacade()
    preparer = BusinessResearchPreparer(facade)
    row = {
        "run_id": "bt-1",
        "strategy_name": "momentum",
        "start_date": "2026-01-01",
        "end_date": "2026-03-01",
        "executed_at": datetime(2026, 3, 2, 8, 0, 0, tzinfo=UTC),
        "metrics": {"total_return": 0.1},
        "initial_capital": 100000,
        "final_capital": 110000,
        "trade_count": 12,
    }

    receipt = await preparer.prepare_backtest(_FakeReader(row), user_scope="local-desktop", result_id="bt-1")

    assert receipt.sent is False
    assert facade.saved_payload.purpose is Purpose.POST_HOC_EXPLANATION
    assert facade.registered_items[0].source.source_id == "backtest:bt-1"
    assert '"object_kind":"backtest_result"' in facade.saved_payload.object_context


def test_backtest_preparation_fails_closed_when_unavailable():
    read = BusinessSnapshotRead(source=None, read_state=ReadState.UNAVAILABLE, missing_reason="result_missing")
    with pytest.raises(BusinessSourceUnavailable) as exc:
        backtest_preparation(read, result_id="bt-404")
    assert exc.value.reason == "result_missing"


@pytest.mark.asyncio
async def test_view_model_requires_wiring():
    vm = BusinessResearchViewModel(None)
    try:
        await vm.prepare(
            BusinessResearchRequest(
                source_module="news",
                object_kind="news_item",
                object_id="1",
                display_name="n",
                summary=(("news_id", "1"),),
                available_at="2026-11-18T01:00:00.000000Z",
            )
        )
        assert vm.state.available is False
        assert vm.state.notice_key == UNAVAILABLE_NOT_WIRED
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_view_model_backtest_requires_reader():
    facade = _FakeFacade()
    vm = BusinessResearchViewModel(facade)
    try:
        await vm.prepare(
            BusinessResearchRequest(
                source_module="backtest",
                object_kind=OBJECT_KIND_BACKTEST,
                object_id="bt-1",
                display_name="bt",
                summary=(("run_id", "bt-1"),),
                available_at=None,
            )
        )
        assert vm.state.notice_key == NOTICE_SOURCE_UNAVAILABLE
        assert vm.state.last_error_code == "backtest_reader_unavailable"
        assert "change_session" not in facade.calls
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_view_model_reports_existing_draft():
    facade = _FakeFacade(existing_revision=2)
    vm = BusinessResearchViewModel(facade)
    try:
        await vm.prepare(
            BusinessResearchRequest(
                source_module="screener",
                object_kind="stock",
                object_id="600519.SH",
                display_name="贵州茅台",
                summary=(("ts_code", "600519.SH"),),
                available_at="2026-11-18T01:00:00.000000Z",
            )
        )
        assert vm.state.notice_key == NOTICE_DRAFT_EXISTS
        assert vm.state.last_draft_revision == 2
        assert "save_draft" not in facade.calls
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_view_model_reports_prepared_draft():
    facade = _FakeFacade()
    vm = BusinessResearchViewModel(facade)
    try:
        await vm.prepare(
            BusinessResearchRequest(
                source_module="screener",
                object_kind="stock",
                object_id="600519.SH",
                display_name="贵州茅台",
                summary=(("ts_code", "600519.SH"),),
                available_at="2026-11-18T01:00:00.000000Z",
            )
        )
        assert vm.state.notice_key == NOTICE_DRAFT_PREPARED
        assert vm.state.last_draft_revision == 2
        assert vm.state.last_error_code is None
    finally:
        vm.dispose()


def _request(
    *,
    source_module: str = "screener",
    object_kind: str = "stock",
    object_id: str = "600519.SH",
    display_name: str = "贵州茅台",
    summary: tuple[tuple[str, str], ...] = (("ts_code", "600519.SH"),),
    available_at: str | None = "2026-11-18T01:00:00.000000Z",
) -> BusinessResearchRequest:
    """构造业务入口冻结的研究请求（默认股票详情入口）。"""

    return BusinessResearchRequest(
        source_module=source_module,
        object_kind=object_kind,
        object_id=object_id,
        display_name=display_name,
        summary=summary,
        available_at=available_at,
    )


@pytest.fixture(autouse=True)
def _reset_shared_business_vm() -> Iterator[None]:
    """隔离共享 VM 单例（R7：跨用例不残留 facade 绑定）。"""

    reset_shared_business_research()
    yield
    reset_shared_business_research()


class _BlockingFacade(_FakeFacade):
    """首次会话读阻塞至被取消（验证在途去重与取消传播）。"""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def read_session(self, query, *, user_scope: str):
        self.calls.append("read_session")
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class _FailingFacade(_FakeFacade):
    """会话读直接抛错（验证失败路径不冒充已准备）。"""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    async def read_session(self, query, *, user_scope: str):
        self.calls.append("read_session")
        raise self._error


@pytest.mark.asyncio
async def test_view_model_skips_duplicate_prepare_while_inflight_and_propagates_cancel():
    facade = _BlockingFacade()
    vm = BusinessResearchViewModel(facade)
    request = _request()

    task = asyncio.create_task(vm.prepare(request))
    await asyncio.wait_for(facade.started.wait(), timeout=2)
    assert vm.state.preparing is True

    # 在途未结束时重复发起：直接返回，不起第二份准备（不重复登记来源/写草稿）
    await vm.prepare(request)
    assert vm.state.preparing is True
    assert facade.calls == ["read_session"], "重复发起不得触发第二次准备链路"
    assert vm.state.notice_key is None

    task.cancel()
    with pytest.raises(asyncio.CancelledError) as excinfo:
        await task
    assert excinfo.type is asyncio.CancelledError
    assert task.cancelled() is True, "取消须传播到任务本身而非被吞没"
    # R2：取消必须传播；同时释放 preparing，避免后续准备被永久拦截
    assert vm.state.preparing is False
    vm.dispose()


@pytest.mark.asyncio
async def test_view_model_prepares_backtest_draft_from_strict_reader():
    facade = _FakeFacade()
    vm = BusinessResearchViewModel(facade)
    row = {
        "run_id": "bt-1",
        "strategy_name": "momentum",
        "start_date": "2026-01-01",
        "end_date": "2026-03-01",
        "executed_at": datetime(2026, 3, 2, 8, 0, 0, tzinfo=UTC),
        "metrics": {"total_return": 0.1},
        "initial_capital": 100000,
        "final_capital": 110000,
        "trade_count": 12,
    }
    try:
        await vm.prepare(
            _request(
                source_module="backtest",
                object_kind=OBJECT_KIND_BACKTEST,
                object_id="bt-1",
                display_name="动量回测",
                summary=(("run_id", "bt-1"),),
                available_at=None,
            ),
            backtest_reader=_FakeReader(row),
        )
        assert vm.state.notice_key == NOTICE_DRAFT_PREPARED
        assert vm.state.last_draft_revision == 2
        # 回测入口按事后解释准备草稿；来源为回测结果对象
        assert facade.saved_payload.purpose is Purpose.POST_HOC_EXPLANATION
        assert facade.registered_items[0].source.source_id == "backtest:bt-1"
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_view_model_reports_unknown_failure_by_exception_type():
    vm = BusinessResearchViewModel(_FailingFacade(RuntimeError("boom")))
    try:
        await vm.prepare(_request())
        assert vm.state.notice_key == NOTICE_PREPARE_FAILED
        # 无类型化 code：如实给类名，不伪装成功、不留 preparing
        assert vm.state.last_error_code == "RuntimeError"
        assert vm.state.preparing is False
        assert vm.state.last_draft_revision is None
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_view_model_reports_typed_facade_error_code():
    error = AgentServiceError(AgentErrorCode.PERSISTENCE_UNAVAILABLE, "store down", "business_research")
    vm = BusinessResearchViewModel(_FailingFacade(error))
    try:
        await vm.prepare(_request())
        assert vm.state.notice_key == NOTICE_PREPARE_FAILED
        assert vm.state.last_error_code == "PERSISTENCE_UNAVAILABLE"
        assert vm.state.preparing is False
    finally:
        vm.dispose()


def test_shared_business_research_reuses_instance_per_facade_and_reset_disposes():
    service = _FakeFacade()
    first = shared_business_research(service)
    second = shared_business_research(service)

    assert first is second, "同一 facade 必须复用同一准备 VM"
    assert second.state.available is True

    reset_shared_business_research()
    assert first.disposed is True, "重置必须销毁共享实例"
    reset_shared_business_research()  # 幂等：无实例时不得抛错


def test_resolve_business_research_service_reports_wiring_states():
    ready_service = object()

    # 未接线 / 接线但无 facade：都如实报告「未接线」（不猜可用）
    assert resolve_business_research_service(None) == (None, UNAVAILABLE_NOT_WIRED)
    assert resolve_business_research_service(SimpleNamespace(service=None, ready=True)) == (
        None,
        UNAVAILABLE_NOT_WIRED,
    )
    # 有 facade 但运行时就绪门闩未通过：报告「未就绪」
    assert resolve_business_research_service(SimpleNamespace(service=ready_service, ready=False)) == (
        None,
        UNAVAILABLE_NOT_READY,
    )
    # 就绪：返回 facade 本身且无原因 key
    assert resolve_business_research_service(SimpleNamespace(service=ready_service, ready=True)) == (ready_service, "")
