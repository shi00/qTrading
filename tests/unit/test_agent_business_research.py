"""R2-I06：业务对象 → 研究草稿准备（不发送）的最小验证。"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from core.agent_types import (
    DRAFT_PAYLOAD_SCHEMA_VERSION,
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    DraftPayload,
    DraftRead,
    DraftTransferCommand,
    DraftTransferSourceKind,
    Mode,
    Purpose,
    ReadState,
    VerificationState,
    draft_transfer_payload_digest,
)
from services.agent_service.business_research import (
    MATERIAL_BYTES_EXCEEDED,
    MATERIAL_COUNT_EXCEEDED,
    BusinessDraftAlreadyPrepared,
    BusinessDraftCommitUnknown,
    BusinessDraftTargetUnavailable,
    BusinessDraftTransferConflict,
    BusinessObjectSnapshot,
    BusinessResearchPreparer,
    BusinessSourceUnavailable,
    MaterialLimitExceeded,
    ResearchMaterial,
    backtest_preparation,
    material_limit_violation,
    material_read_counts,
    snapshot_preparation,
    snapshot_source,
)
from services.agent_service.business_sources import BusinessSnapshotRead, read_backtest_snapshot
from ui.viewmodels.business_research_view_model import (
    NOTICE_COMMIT_UNKNOWN,
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
    def __init__(
        self,
        *,
        existing_revision: int | None = None,
        save_error: Exception | None = None,
        create_error: Exception | None = None,
        draft_read: object | None = None,
        read_draft_error: Exception | None = None,
        transfer_error: Exception | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.saved_payload = None
        self.saved_commands: list = []
        self.session_commands: list = []
        self.registered_items: tuple = ()
        self.transfer_commands: list = []
        self._existing_revision = existing_revision
        self._save_error = save_error
        self._create_error = create_error
        self._draft_read = draft_read
        self._read_draft_error = read_draft_error
        self._transfer_error = transfer_error

    async def read_session(self, query, *, user_scope: str):
        self.calls.append("read_session")
        if self._existing_revision is None:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "session not found", query.session_id)
        return SimpleNamespace(summary=SimpleNamespace(draft_revision=self._existing_revision))

    async def change_session(self, command):
        self.calls.append("change_session")
        self.session_commands.append(command)
        if self._create_error is not None:
            raise self._create_error
        return SimpleNamespace()

    async def register_draft_sources(self, *, user_scope, session_id, draft_revision, items):
        self.calls.append("register_draft_sources")
        self.registered_items = items
        return SimpleNamespace(registered=(), link_ids=(), replayed=False)

    async def save_draft(self, command):
        self.calls.append("save_draft")
        self.saved_commands.append(command)
        if self._save_error is not None:
            raise self._save_error
        self.saved_payload = command.payload
        return SimpleNamespace(
            session_id=command.session_id,
            draft_revision=command.expected_draft_revision + 1,
        )

    async def read_draft(self, query, *, user_scope: str):
        self.calls.append("read_draft")
        if self._read_draft_error is not None:
            raise self._read_draft_error
        return self._draft_read

    async def transfer_draft(self, command):
        self.calls.append("transfer_draft")
        self.transfer_commands.append(command)
        if self._transfer_error is not None:
            raise self._transfer_error
        return SimpleNamespace(
            target_session_id=command.target_session_id,
            target_revision=command.expected_target_draft_revision + 1,
            transfer_id=command.transfer_id,
        )


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
    # T-08：READY 必须带可按原引用读回的正文表示（不再是「READY 却无正文」）。
    assert item.source.read_state is ReadState.READY
    assert item.body_text is not None
    assert "600519.SH" in item.body_text


def test_snapshot_source_downgrades_truncated_body_to_partial():
    item = snapshot_source(
        _snapshot(summary={"news_id": "9", "content_excerpt": "…", "content_truncated": "true"}),
        user_scope="local-desktop",
    )

    assert item.source.read_state is ReadState.PARTIAL
    assert item.source.missing_reason == "content_truncated"
    assert item.body_text is not None and "已截断" in item.body_text


def test_snapshot_preparation_freezes_batch_reference():
    preparation = snapshot_preparation(
        _snapshot(
            object_kind="screener_candidate",
            summary={"ts_code": "600519.SH", "trade_date": "2026-11-18", "strategy": "momentum", "run_id": "r-1"},
        ),
        user_scope="local-desktop",
    )

    batch_ref = preparation.object_context.get("batch_ref")
    assert batch_ref == "trade_date=2026-11-18;strategy=momentum;run_id=r-1"


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


@pytest.mark.asyncio
async def test_prepare_unknown_commit_keeps_original_command_and_replays_same_key():
    """N12：提交结果未知 → 保原命令，下次按原键/原输入查回，不换键补写。"""

    facade = _FakeFacade(save_error=AgentServiceError(AgentErrorCode.OPERATION_UNKNOWN, "commit unknown", "u1"))
    preparer = BusinessResearchPreparer(facade)

    with pytest.raises(BusinessDraftCommitUnknown) as exc:
        await preparer.prepare_object(_snapshot(), user_scope="local-desktop")
    assert exc.value.session_id
    original = facade.saved_commands[0]

    facade._save_error = None
    receipt = await preparer.prepare_object(_snapshot(), user_scope="local-desktop")

    # 原命令（原键/原输入）查回：不换键、不重写第二份
    assert facade.saved_commands[1] is original
    assert receipt.draft_revision == original.expected_draft_revision + 1
    # 查回路径不再探测会话，也不再登记来源/建会话
    assert facade.calls.count("read_session") == 1
    assert facade.calls.count("register_draft_sources") == 1
    assert facade.calls.count("change_session") == 1


@pytest.mark.asyncio
async def test_prepare_not_committed_failure_is_retryable_with_same_key():
    """N12：确定未写 → 一般失败（同键重试安全），不进入「结果未知」状态。"""

    facade = _FakeFacade(save_error=AgentServiceError(AgentErrorCode.PERSISTENCE_UNAVAILABLE, "not committed", "u1"))
    preparer = BusinessResearchPreparer(facade)

    with pytest.raises(AgentServiceError) as exc:
        await preparer.prepare_object(_snapshot(), user_scope="local-desktop")
    assert exc.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE

    facade._save_error = None
    receipt = await preparer.prepare_object(_snapshot(), user_scope="local-desktop")
    assert facade.saved_commands[1].command.command_id == facade.saved_commands[0].command.command_id
    assert receipt.draft_revision == 2


@pytest.mark.asyncio
async def test_prepare_unknown_session_create_is_reported_as_unknown():
    facade = _FakeFacade(create_error=AgentServiceError(AgentErrorCode.OPERATION_UNKNOWN, "commit unknown", "u1"))
    preparer = BusinessResearchPreparer(facade)

    with pytest.raises(BusinessDraftCommitUnknown) as exc:
        await preparer.prepare_object(_snapshot(), user_scope="local-desktop")
    assert exc.value.session_id == facade.session_commands[0].session_id
    assert "save_draft" not in facade.calls


@pytest.mark.asyncio
async def test_replay_pending_definite_failure_keeps_original_command():
    """N12：查回途中确定未写（存储不可用）仍保原命令，后续继续按原键查回。"""

    facade = _FakeFacade(save_error=AgentServiceError(AgentErrorCode.OPERATION_UNKNOWN, "commit unknown", "u1"))
    preparer = BusinessResearchPreparer(facade)
    with pytest.raises(BusinessDraftCommitUnknown) as exc:
        await preparer.prepare_object(_snapshot(), user_scope="local-desktop")
    assert exc.value.session_id == facade.saved_commands[0].session_id
    original = facade.saved_commands[0]

    facade._save_error = AgentServiceError(AgentErrorCode.PERSISTENCE_UNAVAILABLE, "not committed", "u1")
    with pytest.raises(AgentServiceError) as exc:
        await preparer.prepare_object(_snapshot(), user_scope="local-desktop")
    assert exc.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE

    facade._save_error = None
    receipt = await preparer.prepare_object(_snapshot(), user_scope="local-desktop")
    assert facade.saved_commands[-1] is original
    assert receipt.draft_revision == original.expected_draft_revision + 1


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
    assert '"availability_state":"known"' in facade.saved_payload.object_context


@pytest.mark.asyncio
async def test_restricted_preparation_keeps_unknown_availability_without_faking_utc():
    """P1-04：存量 naive executed_at → 受限稿（时点未知），不按 UTC 贴成已知。"""
    facade = _FakeFacade()
    preparer = BusinessResearchPreparer(facade)
    legacy_row = {
        "run_id": "bt-legacy",
        "strategy_name": "momentum",
        "start_date": "2026-01-01",
        "end_date": "2026-03-01",
        # timestamp without time zone：绝对时刻不可证。
        "executed_at": datetime(2026, 3, 2, 8, 0, 0),
    }

    read = await read_backtest_snapshot(_FakeReader(legacy_row), user_scope="local-desktop", result_id="bt-legacy")
    preparation = backtest_preparation(read, result_id="bt-legacy")

    source = preparation.item.source
    assert source.available_at is None
    assert source.read_state is ReadState.UNKNOWN
    assert preparation.object_context["availability_state"] == "unknown"
    assert preparation.object_context["available_at"] is None
    # 受限稿正文用户可见地标注未知与发送期后果，不伪装成可证时点。
    receipt = await preparer.prepare(preparation, user_scope="local-desktop")
    assert receipt.sent is False
    assert facade.saved_payload.cutoff is None
    assert facade.saved_payload.purpose is Purpose.POST_HOC_EXPLANATION
    assert "未知" in facade.saved_payload.text


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


@pytest.mark.asyncio
async def test_view_model_reports_commit_unknown_without_claiming_success_or_failure():
    """N12：结果未知单独提示（保原键查回），不冒充已准备，也不当一般失败。"""

    facade = _FakeFacade(save_error=AgentServiceError(AgentErrorCode.OPERATION_UNKNOWN, "commit unknown", "u1"))
    vm = BusinessResearchViewModel(facade)
    try:
        await vm.prepare(_request())
        assert vm.state.notice_key == NOTICE_COMMIT_UNKNOWN
        assert vm.state.last_error_code == "OPERATION_UNKNOWN"
        assert vm.state.last_session_id is not None
        # 不臆造已提交版本（未知不等于成功）
        assert vm.state.last_draft_revision is None
        assert vm.state.preparing is False
    finally:
        vm.dispose()


@pytest.mark.asyncio
async def test_view_model_reports_not_committed_failure_as_generic_failure():
    """N12：确定未写仍按一般失败提示（可同键重试），与「结果未知」区分。"""

    facade = _FakeFacade(save_error=AgentServiceError(AgentErrorCode.PERSISTENCE_UNAVAILABLE, "not committed", "u1"))
    vm = BusinessResearchViewModel(facade)
    try:
        await vm.prepare(_request())
        assert vm.state.notice_key == NOTICE_PREPARE_FAILED
        assert vm.state.last_error_code == "PERSISTENCE_UNAVAILABLE"
        assert vm.state.preparing is False
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


# --- 研究资料：限额 / 已读计数 / 并入既有草稿 / 草稿转移 ---

_MATERIAL_CAPTURED = "2026-11-20T08:00:00.000000Z"
_MATERIAL_CONTENT = "2026-11-18T00:00:00.000000Z"


def _material(
    index: int,
    *,
    read_state: ReadState = ReadState.READY,
    byte_size: int | None = None,
) -> ResearchMaterial:
    return ResearchMaterial(
        kind="file",
        source_id=f"file:doc-{index}",
        source_version=hashlib.sha256(f"doc-{index}".encode()).hexdigest(),
        display_name=f"doc-{index}.md",
        read_state=read_state,
        missing_reason="material_extraction_not_wired" if read_state is ReadState.UNKNOWN else None,
        captured_at=_MATERIAL_CAPTURED,
        content_at=_MATERIAL_CONTENT,
        byte_size=byte_size,
    )


def _draft_payload() -> DraftPayload:
    return DraftPayload(
        schema_version=DRAFT_PAYLOAD_SCHEMA_VERSION,
        text="已有草稿",
        mode=Mode.DISCUSS,
        model_selection=None,
        source_refs=(),
        skill_versions=(),
        mcp_selection=(),
        purpose=Purpose.END_OF_DAY_RESEARCH,
        research_date="2026-11-19",
        cutoff=None,
        send_target=None,
        object_context=None,
        role_prompt_selection=None,
        customized_fields=(),
        defaults_baseline_digest=None,
    )


def _draft_read(*, payload: DraftPayload | None = None, revision: int = 3, cleaned: bool = False) -> DraftRead:
    return DraftRead(
        session_id="session-target",
        draft_revision=revision,
        payload=payload,
        is_empty=False,
        cleaned=cleaned,
        archived=False,
        transferred=False,
        read_at="2026-11-25T00:00:00.000000Z",
    )


def test_material_limit_violation_accepts_exact_limits_and_rejects_overflow():
    exact = [_material(index) for index in range(5)]

    assert material_limit_violation(exact) is None

    overflow = [_material(index) for index in range(6)]
    violation = material_limit_violation(overflow)
    assert violation is not None
    assert violation.code == MATERIAL_COUNT_EXCEEDED
    assert violation.index == 5
    assert violation.limit == 5
    assert violation.actual == 6


def test_material_limit_violation_checks_single_item_bytes():
    materials = [_material(0), _material(1, byte_size=20_000_001), _material(2)]

    violation = material_limit_violation(materials)

    assert violation is not None
    assert violation.code == MATERIAL_BYTES_EXCEEDED
    assert violation.index == 1
    assert violation.limit == 20_000_000
    assert violation.actual == 20_000_001


def test_material_read_counts_treat_partial_and_unknown_as_unread():
    materials = [_material(0), _material(1, read_state=ReadState.PARTIAL), _material(2, read_state=ReadState.UNKNOWN)]

    assert material_read_counts(materials) == (1, 2)


@pytest.mark.asyncio
async def test_add_materials_to_session_appends_refs_and_keeps_existing_payload_text():
    payload = _draft_payload()
    facade = _FakeFacade(draft_read=_draft_read(payload=payload))
    preparer = BusinessResearchPreparer(facade)

    receipt = await preparer.add_materials_to_session(
        user_scope="local-desktop",
        target_session_id="session-target",
        materials=(_material(0), _material(1, read_state=ReadState.UNKNOWN)),
        command_id="cmd-add-1",
    )

    assert receipt.target_session_id == "session-target"
    assert receipt.draft_revision == 4
    assert receipt.added == 2
    assert receipt.read == 1
    assert receipt.unread == 1
    assert receipt.sent is False
    sent_payload = facade.saved_commands[0].payload
    assert sent_payload.text == "已有草稿"
    assert [ref.source_id for ref in sent_payload.source_refs] == ["file:doc-0", "file:doc-1"]
    assert [item.source.source_id for item in facade.registered_items] == ["file:doc-0", "file:doc-1"]
    assert [item.source.read_state for item in facade.registered_items] == [ReadState.READY, ReadState.UNKNOWN]
    assert facade.saved_commands[0].expected_draft_revision == 3


@pytest.mark.asyncio
async def test_add_materials_to_session_is_idempotent_when_all_refs_already_present():
    existing = _material(0)
    from core.agent_types import SourceKind, SourceVersionRef

    payload = _draft_payload()
    payload = DraftPayload(
        schema_version=payload.schema_version,
        text=payload.text,
        mode=payload.mode,
        model_selection=None,
        source_refs=(
            SourceVersionRef(
                source_kind=SourceKind.ATTACHMENT,
                source_id=existing.source_id,
                source_version=existing.source_version,
                source_digest=existing.source_version,
            ),
        ),
        skill_versions=(),
        mcp_selection=(),
        purpose=payload.purpose,
        research_date=payload.research_date,
        cutoff=None,
        send_target=None,
        object_context=None,
        role_prompt_selection=None,
        customized_fields=(),
        defaults_baseline_digest=None,
    )
    facade = _FakeFacade(draft_read=_draft_read(payload=payload))
    preparer = BusinessResearchPreparer(facade)

    receipt = await preparer.add_materials_to_session(
        user_scope="local-desktop",
        target_session_id="session-target",
        materials=(existing,),
        command_id="cmd-add-2",
    )

    assert receipt.added == 0
    assert receipt.draft_revision == 3
    assert "save_draft" not in facade.calls
    assert facade.registered_items == ()


@pytest.mark.asyncio
async def test_add_materials_to_session_rejects_over_limit_before_touching_target():
    facade = _FakeFacade(draft_read=_draft_read(payload=_draft_payload()))
    preparer = BusinessResearchPreparer(facade)

    with pytest.raises(MaterialLimitExceeded) as exc:
        await preparer.add_materials_to_session(
            user_scope="local-desktop",
            target_session_id="session-target",
            materials=tuple(_material(index) for index in range(6)),
            command_id="cmd-add-3",
        )

    assert exc.value.code == MATERIAL_COUNT_EXCEEDED
    assert facade.calls == []


@pytest.mark.asyncio
async def test_add_materials_to_session_reports_cleaned_target():
    facade = _FakeFacade(draft_read=_draft_read(payload=None, cleaned=True))
    preparer = BusinessResearchPreparer(facade)

    with pytest.raises(BusinessDraftTargetUnavailable) as exc:
        await preparer.add_materials_to_session(
            user_scope="local-desktop",
            target_session_id="session-target",
            materials=(_material(0),),
            command_id="cmd-add-4",
        )

    assert exc.value.code == "target_session_unavailable"
    assert "save_draft" not in facade.calls


@pytest.mark.asyncio
async def test_add_materials_to_session_maps_revision_conflict():
    conflict = AgentServiceError(AgentErrorCode.REVISION_CONFLICT, "stale", "session-target")
    facade = _FakeFacade(draft_read=_draft_read(payload=_draft_payload()), save_error=conflict)
    preparer = BusinessResearchPreparer(facade)

    with pytest.raises(BusinessDraftTransferConflict) as exc:
        await preparer.add_materials_to_session(
            user_scope="local-desktop",
            target_session_id="session-target",
            materials=(_material(0),),
            command_id="cmd-add-5",
        )

    assert exc.value.code == "target_draft_conflict"


@pytest.mark.asyncio
async def test_transfer_draft_sends_idempotent_command_without_sending_research():
    facade = _FakeFacade(draft_read=_draft_read(payload=_draft_payload()))
    preparer = BusinessResearchPreparer(facade)

    receipt = await preparer.transfer_draft(
        user_scope="local-desktop",
        target_session_id="session-target",
        materials=(_material(0), _material(1, read_state=ReadState.PARTIAL)),
        command_id="cmd-transfer-1",
        target_draft_revision=3,
    )

    assert receipt.sent is False
    assert receipt.target_session_id == "session-target"
    assert receipt.target_revision == 4
    assert receipt.material_count == 2
    command = facade.transfer_commands[0]
    assert isinstance(command, DraftTransferCommand)
    assert command.command.command_id == "cmd-transfer-1"
    assert command.source_kind is DraftTransferSourceKind.BUSINESS_PREPARATION
    assert command.source_session_id is None
    assert command.expected_source_draft_revision == 1
    assert command.expected_target_draft_revision == 3
    assert command.confirmed is True
    assert command.payload_digest == draft_transfer_payload_digest(command.payload)
    assert command.payload.research_date is None
    assert [ref.source_id for ref in command.payload.source_refs] == ["file:doc-0", "file:doc-1"]
    assert "1 件资料未读或仅部分读取" in command.payload.text


@pytest.mark.asyncio
async def test_transfer_draft_rejects_invalid_revision_and_empty_materials():
    preparer = BusinessResearchPreparer(_FakeFacade(draft_read=_draft_read()))

    with pytest.raises(AgentValidationError) as exc:
        await preparer.transfer_draft(
            user_scope="local-desktop",
            target_session_id="session-target",
            materials=(_material(0),),
            command_id="cmd-transfer-2",
            target_draft_revision=0,
        )

    assert exc.value.code == "invalid_revision"
    with pytest.raises(MaterialLimitExceeded) as exc2:
        await preparer.transfer_draft(
            user_scope="local-desktop",
            target_session_id="session-target",
            materials=(),
            command_id="cmd-transfer-3",
            target_draft_revision=3,
        )
    assert exc2.value.code == "material_empty"


@pytest.mark.asyncio
async def test_transfer_draft_maps_unknown_outcome_without_faking_success():
    unknown = AgentServiceError(AgentErrorCode.OPERATION_UNKNOWN, "timeout", "session-target")
    facade = _FakeFacade(draft_read=_draft_read(), transfer_error=unknown)
    preparer = BusinessResearchPreparer(facade)

    with pytest.raises(BusinessDraftCommitUnknown) as exc:
        await preparer.transfer_draft(
            user_scope="local-desktop",
            target_session_id="session-target",
            materials=(_material(0),),
            command_id="cmd-transfer-4",
            target_draft_revision=3,
        )

    assert exc.value.session_id == "session-target"


@pytest.mark.asyncio
async def test_transfer_draft_maps_input_invalid_to_not_transferable():
    invalid = AgentServiceError(AgentErrorCode.INPUT_INVALID, "target draft not empty", "session-target")
    facade = _FakeFacade(draft_read=_draft_read(), transfer_error=invalid)
    preparer = BusinessResearchPreparer(facade)

    with pytest.raises(BusinessDraftTransferConflict) as exc:
        await preparer.transfer_draft(
            user_scope="local-desktop",
            target_session_id="session-target",
            materials=(_material(0),),
            command_id="cmd-transfer-5",
            target_draft_revision=3,
        )

    assert exc.value.code == "target_not_transferable"


@pytest.mark.asyncio
async def test_add_materials_to_session_rejects_blank_target_and_invalid_kind():
    preparer = BusinessResearchPreparer(_FakeFacade(draft_read=_draft_read()))

    with pytest.raises(AgentServiceError) as blank:
        await preparer.add_materials_to_session(
            user_scope="local-desktop",
            target_session_id="   ",
            materials=(_material(0),),
            command_id="cmd-add-6",
        )
    assert blank.value.code is AgentErrorCode.INPUT_INVALID

    with pytest.raises(MaterialLimitExceeded) as kind:
        await preparer.add_materials_to_session(
            user_scope="local-desktop",
            target_session_id="session-target",
            materials=(replace(_material(0), kind="video"),),
            command_id="cmd-add-7",
        )
    assert kind.value.code == "material_kind_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (AgentServiceError(AgentErrorCode.INPUT_CONFLICT, "payload mismatch", "s"), "transfer_conflict"),
        (AgentServiceError(AgentErrorCode.POLICY_DENIED, "cleaned", "s"), "target_session_unavailable"),
    ],
)
async def test_transfer_draft_maps_each_conflict_code(error, expected):
    facade = _FakeFacade(draft_read=_draft_read(), transfer_error=error)
    preparer = BusinessResearchPreparer(facade)

    with pytest.raises(BusinessDraftTransferConflict) as exc:
        await preparer.transfer_draft(
            user_scope="local-desktop",
            target_session_id="session-target",
            materials=(_material(0),),
            command_id="cmd-transfer-6",
            target_draft_revision=3,
        )

    assert exc.value.code == expected


@pytest.mark.asyncio
async def test_transfer_draft_preserves_unknown_facade_code():
    unknown = AgentServiceError(AgentErrorCode.PERSISTENCE_UNAVAILABLE, "db down", "session-target")
    facade = _FakeFacade(draft_read=_draft_read(), transfer_error=unknown)
    preparer = BusinessResearchPreparer(facade)

    with pytest.raises(BusinessDraftTransferConflict) as exc:
        await preparer.transfer_draft(
            user_scope="local-desktop",
            target_session_id="session-target",
            materials=(_material(0),),
            command_id="cmd-transfer-7",
            target_draft_revision=3,
        )

    assert exc.value.code == "PERSISTENCE_UNAVAILABLE"
