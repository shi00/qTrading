"""E-05-4b 交付编排（DD SQ-09 / §10.5 / HANDOFF §10.2）。

覆盖：
- 阶段顺序与 CAS 版本：manifest_saved → staged → validated → publish_claimed →
  published_confirmed，每步 expected_revision 递增，命令预期版全程不变；
- 分页正文跨游标组装完整（不截断）；正文不可得如实标注，不补造空正文；
- 暂存回读不符/逐字段回读不符/目标竞争变化 → 发布前可确证失败，保旧阶段、不发布；
- 取消先胜：落 cancelled_before_publish、不发布、只清本交付暂存；
- 发布认领后不可确证 → unknown（禁止重试），成功回执保存失败同样转 unknown；
- 契约/身份/游标异常在读前拒绝或报告，不产生任何阶段副作用。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    ArtifactDeliveryCommand,
    ArtifactRead,
    ArtifactReadQuery,
    ArtifactSourceState,
    CommandRef,
    DeliveryField,
    DeliveryManifest,
    DeliveryPhaseReceipt,
    DeliveryReceipt,
    DeliveryTarget,
    derive_delivery_id,
    encode_delivery_manifest,
    encode_delivery_receipt,
    sha256_hex,
    validate_delivery_transition,
)
from services.agent_service import delivery_service as delivery_service_module
from services.agent_service.delivery import (
    DeliveryFileError,
    DeliveryUnknownError,
    FileSnapshot,
    PublishResult,
    StagedFile,
)
from services.agent_service.delivery_render import (
    DeliveryRenderError,
    read_delivery_fields,
)
from services.agent_service.delivery_service import (
    DeliveryCommandService,
    DeliveryContext,
    _stage_name_hint,
)

_USER_SCOPE = "user-1"
_CLOCK = "2026-11-15T08:00:00.000000Z"
_TARGET_PATH = r"C:\out\report.md"
_STAGED_PATH = Path(r"C:\out\.report.md.staged")


def _command(**overrides: object) -> ArtifactDeliveryCommand:
    base = ArtifactDeliveryCommand(
        command=CommandRef(
            user_scope=_USER_SCOPE,
            source_module="ui.artifact",
            business_request_id="req-1",
            command_id="cmd-1",
            expected_revision=1,
        ),
        artifact_id="art-1",
        artifact_version=2,
        format="md",
        format_rule_version=1,
        delivery_id="delivery-placeholder",
        target=DeliveryTarget(
            target_path=_TARGET_PATH,
            expected_existing=False,
            expected_existing_digest=None,
            overwrite_confirmed=False,
        ),
        expected_delivery_revision=1,
    )
    values = {
        "command": base.command,
        "artifact_id": base.artifact_id,
        "artifact_version": base.artifact_version,
        "format": base.format,
        "format_rule_version": base.format_rule_version,
        "delivery_id": base.delivery_id,
        "target": base.target,
        "expected_delivery_revision": base.expected_delivery_revision,
    }
    values.update(overrides)
    draft = ArtifactDeliveryCommand(**values)  # pyright: ignore[reportArgumentType]
    if overrides.get("delivery_id") is None:
        draft = ArtifactDeliveryCommand(
            command=draft.command,
            artifact_id=draft.artifact_id,
            artifact_version=draft.artifact_version,
            format=draft.format,
            format_rule_version=draft.format_rule_version,
            delivery_id=derive_delivery_id(draft),
            target=draft.target,
            expected_delivery_revision=draft.expected_delivery_revision,
        )
    return draft


def _read(**overrides: object) -> ArtifactRead:
    values: dict[str, object] = {
        "artifact_id": "art-1",
        "artifact_version": 2,
        "session_id": "s1",
        "execution_id": "exec-1",
        "round_id": "round-1",
        "artifact_kind": "research",
        "input_revision": 3,
        "actual_input_digest": "a" * 64,
        "actual_model_ref": "model/main",
        "result_digest": "b" * 64,
        "source_available_state": "available",
        "summary": "成果摘要",
        "result_payload": '{"n":[1,null,false],"s":"00123"}',
        "published_at": None,
        "cleaned_at": None,
        "updated_at": _CLOCK,
        "revision": 2,
        "body_text": "成果正文",
        "next_cursor": None,
        "body_available": True,
        "unavailable_reason": None,
    }
    values.update(overrides)
    return ArtifactRead(**values)  # pyright: ignore[reportArgumentType]


def _context() -> DeliveryContext:
    return DeliveryContext(
        operation_id="op-1",
        command_record_id="rec-1",
        execution_id="exec-1",
        round_id="round-1",
    )


def _ref_state(
    source_id: str,
    state: str,
    *,
    version: str = "1",
    kind: str = "attachment",
) -> ArtifactSourceState:
    """逐项来源状态（单测夹具）：摘要与源身份一一对应，便于断言不被聚合掩盖。"""

    return ArtifactSourceState(
        source_kind=kind,
        source_id=source_id,
        source_version=version,
        source_digest=sha256_hex(f"{source_id}@{version}".encode()),
        state=state,
    )


class FakeArtifacts:
    """分页成果读取：正文按游标顺序取页，``limit=1`` 走「新鲜核来源」分支。"""

    def __init__(
        self,
        pages: list[ArtifactRead],
        *,
        fresh: ArtifactRead | None = None,
        fresh_states: list[str] | None = None,
        fresh_ref_states: list[list[ArtifactSourceState]] | None = None,
    ) -> None:
        self.pages = pages
        self.fresh = fresh
        self.fresh_states = list(fresh_states) if fresh_states is not None else None
        self.fresh_ref_states = [list(states) for states in fresh_ref_states] if fresh_ref_states is not None else None
        self.queries: list[ArtifactReadQuery] = []
        self._page_index = 0

    async def read_artifact(self, query: ArtifactReadQuery, *, user_scope: str) -> ArtifactRead:
        self.queries.append(query)
        if query.limit == 1:
            if self.fresh_ref_states is not None:
                states = self.fresh_ref_states.pop(0) if len(self.fresh_ref_states) > 1 else self.fresh_ref_states[0]
                return replace(self.pages[0], source_ref_states=tuple(states))
            if self.fresh_states is not None:
                state = self.fresh_states.pop(0) if len(self.fresh_states) > 1 else self.fresh_states[0]
                return replace(self.pages[0], source_available_state=state)
            return self.fresh if self.fresh is not None else self.pages[0]
        if query.body_cursor is None:
            self._page_index = 0
            return self.pages[0]
        self._page_index = min(self._page_index + 1, len(self.pages) - 1)
        return self.pages[self._page_index]

    @property
    def page_queries(self) -> list[ArtifactReadQuery]:
        return [query for query in self.queries if query.limit != 1]

    @property
    def fresh_queries(self) -> list[ArtifactReadQuery]:
        return [query for query in self.queries if query.limit == 1]


class FakePhaseDao:
    """内存阶段 CAS：编码自洽 + 版本递增 + 迁移表校验，与真 DAO 同语义。"""

    def __init__(self) -> None:
        self.revision = 1
        self.saved: list[DeliveryManifest] = []
        self.expected_revisions: list[int] = []
        self.fail_phases: set[str] = set()
        self.fail_error: AgentServiceError | None = None

    async def save_delivery_phase(
        self,
        *,
        user_scope: str,
        operation_id: str,
        expected_revision: int,
        manifest: DeliveryManifest,
    ) -> DeliveryPhaseReceipt:
        encode_delivery_manifest(manifest)
        if manifest.phase in self.fail_phases:
            raise self.fail_error or AgentServiceError(AgentErrorCode.REVISION_CONFLICT, "boom", operation_id)
        if expected_revision != self.revision:
            raise AgentServiceError(AgentErrorCode.REVISION_CONFLICT, "revision changed", operation_id)
        if self.saved:
            validate_delivery_transition(self.saved[-1].phase, manifest.phase)
        self.saved.append(manifest)
        self.expected_revisions.append(expected_revision)
        self.revision += 1
        return DeliveryPhaseReceipt(
            delivery_id=manifest.delivery_id,
            operation_id=operation_id,
            phase=manifest.phase,
            revision=self.revision,
            artifact_id=manifest.artifact_id,
            artifact_version=manifest.artifact_version,
            idempotent=False,
        )

    @property
    def phases(self) -> list[str]:
        return [manifest.phase for manifest in self.saved]


class FakeStore:
    """内存文件交付：记录内容与调用，可注入失败/摘要偏差。"""

    def __init__(self) -> None:
        self.staged_contents: list[bytes] = []
        self.stage_name_hints: list[str | None] = []
        self.published: list[tuple[DeliveryTarget, StagedFile]] = []
        self.publish_attempts = 0
        self.cancelled: list[StagedFile] = []
        self.stage_error: Exception | None = None
        self.publish_error: Exception | None = None
        self.stage_digest_override: str | None = None
        self.publish_digest_override: str | None = None

    def stage(
        self,
        *,
        target: DeliveryTarget,
        content: bytes,
        expected_digest: str,
        name_hint: str | None = None,
    ) -> StagedFile:
        if self.stage_error is not None:
            raise self.stage_error
        self.staged_contents.append(content)
        self.stage_name_hints.append(name_hint)
        return StagedFile(
            path=_STAGED_PATH,
            digest=self.stage_digest_override or expected_digest,
            size=len(content),
            volume_serial=7,
            file_id=11,
            parent_volume_serial=7,
            parent_file_id=8,
        )

    def publish(self, *, target: DeliveryTarget, staged: StagedFile) -> PublishResult:
        self.publish_attempts += 1
        if self.publish_error is not None:
            raise self.publish_error
        self.published.append((target, staged))
        return PublishResult(
            snapshot=FileSnapshot(
                exists=True,
                volume_serial=9,
                file_id=10,
                size=staged.size,
                digest=self.publish_digest_override or staged.digest,
                is_reparse_point=False,
                parent_volume_serial=7,
                parent_file_id=8,
            )
        )

    def cancel(self, staged: StagedFile) -> bool:
        self.cancelled.append(staged)
        return True


async def _run_direct(func: Callable[..., Any], *args: object, **kwargs: object) -> Any:
    return func(*args, **kwargs)


def _service(
    *,
    artifacts: FakeArtifacts,
    dao: FakePhaseDao,
    store: FakeStore,
) -> DeliveryCommandService:
    return DeliveryCommandService(
        artifacts=artifacts,
        dao=dao,
        store=store,
        run_blocking=_run_direct,
        clock=lambda: _CLOCK,
    )


def _assert_receipt_wire(receipt: DeliveryReceipt) -> None:
    wire = encode_delivery_receipt(receipt)
    assert wire["delivery_id"] == receipt.delivery_id
    assert wire["phase"] == receipt.phase


class TestHappyPath:
    async def test_phases_cas_and_receipt_are_consistent(self) -> None:
        artifacts = FakeArtifacts([_read()])
        dao = FakePhaseDao()
        store = FakeStore()

        outcome = await _service(artifacts=artifacts, dao=dao, store=store).deliver(_command(), _context())

        assert dao.phases == ["manifest_saved", "staged", "validated", "publish_claimed", "published_confirmed"]
        assert dao.expected_revisions == [1, 2, 3, 4, 5]
        assert all(manifest.command.expected_revision == 1 for manifest in dao.saved)
        assert [manifest.delivery_id for manifest in dao.saved] == [_command().delivery_id] * 5

        staged, claimed, confirmed = dao.saved[1], dao.saved[3], dao.saved[4]
        assert dao.saved[0].source_state == "available"  # 历史（成果保存时核定）
        assert staged.source_state == "unknown"  # 本次：DAO 不能新鲜核全部来源即显式 unknown
        assert staged.staged_path == str(_STAGED_PATH)
        assert staged.output_digest == confirmed.output_digest
        assert staged.output_bytes == len(store.staged_contents[0])
        assert confirmed.staged_path is None
        assert claimed.source_state == "unknown"
        assert confirmed.artifact_digest == "b" * 64
        assert confirmed.verified_at == _CLOCK

        assert len(store.published) == 1
        assert store.published[0][0] == _command().target
        assert store.stage_name_hints == [_stage_name_hint(_command().delivery_id)]
        assert len(store.stage_name_hints[0]) <= 32

        receipt = outcome.receipt
        assert receipt.phase == "published_confirmed"
        assert receipt.publish_effect == "published"
        assert receipt.cancel_effect == "not_requested"
        assert receipt.output_digest == confirmed.output_digest
        assert receipt.command_record_id == "rec-1"
        assert receipt.next_action is None
        assert outcome.staged_cleaned is False
        _assert_receipt_wire(receipt)

    async def test_rendered_output_roundtrips_to_the_saved_artifact(self) -> None:
        artifacts = FakeArtifacts([_read()])
        store = FakeStore()

        await _service(artifacts=artifacts, dao=FakePhaseDao(), store=store).deliver(_command(), _context())

        fields = read_delivery_fields(store.staged_contents[0], "md")
        index = {field.path: field for field in fields}
        assert index["metadata:artifact_id"].value_text == "art-1"
        assert index["metadata:artifact_version"].value_text == "2"
        assert index["metadata:source_available_state"].value_text == "available"
        assert index["metadata:current_source_state"].value_text == "unknown"
        assert index["body:"].value_text == "成果正文"
        assert index["payload:/s"].value_text == "00123"
        assert index["payload:/n/1"].value_type == "null"


class TestCompleteBody:
    async def test_paged_body_is_joined_across_cursors(self) -> None:
        first = _read(body_text="第一页", next_cursor="b:art-1:2:4")
        second = _read(body_text="第二页", next_cursor=None)
        artifacts = FakeArtifacts([first, second])
        store = FakeStore()

        await _service(artifacts=artifacts, dao=FakePhaseDao(), store=store).deliver(_command(), _context())

        assert [query.body_cursor for query in artifacts.page_queries] == [None, "b:art-1:2:4"]
        fields = read_delivery_fields(store.staged_contents[0], "md")
        body = "".join(field.value_text for field in fields if field.record == "body")
        assert body == "第一页第二页"

    async def test_unavailable_body_is_reported_without_empty_rows(self) -> None:
        artifacts = FakeArtifacts(
            [_read(body_text=None, body_available=False, unavailable_reason="body_cleaned", result_payload=None)]
        )
        store = FakeStore()

        outcome = await _service(artifacts=artifacts, dao=FakePhaseDao(), store=store).deliver(_command(), _context())

        fields = read_delivery_fields(store.staged_contents[0], "md")
        index = {field.path: field for field in fields}
        assert index["metadata:body_available"].value_text == "false"
        assert index["metadata:unavailable_reason"].value_text == "body_cleaned"
        assert "body:" not in index
        assert outcome.receipt.phase == "published_confirmed"

    async def test_cursor_that_does_not_advance_is_reported_as_unknown(self) -> None:
        class StuckArtifacts(FakeArtifacts):
            async def read_artifact(self, query: ArtifactReadQuery, *, user_scope: str) -> ArtifactRead:
                self.queries.append(query)
                return _read(next_cursor="b:art-1:2:4")

        artifacts = StuckArtifacts([_read()])
        dao = FakePhaseDao()
        with pytest.raises(AgentServiceError) as error:
            await _service(artifacts=artifacts, dao=dao, store=FakeStore()).deliver(_command(), _context())
        assert error.value.code == AgentErrorCode.OPERATION_UNKNOWN
        assert dao.phases == []


class TestPrePublishFailures:
    async def test_stage_error_keeps_manifest_saved_and_never_publishes(self) -> None:
        store = FakeStore()
        store.stage_error = DeliveryFileError("delivery_target_changed", "target changed")
        dao = FakePhaseDao()

        outcome = await _service(artifacts=FakeArtifacts([_read()]), dao=dao, store=store).deliver(
            _command(), _context()
        )

        assert dao.phases == ["manifest_saved"]
        assert store.published == []
        assert outcome.receipt.phase == "manifest_saved"
        assert outcome.receipt.error_code == "delivery_target_changed"
        assert outcome.receipt.publish_effect == "not_attempted"
        assert outcome.receipt.output_digest is None
        _assert_receipt_wire(outcome.receipt)

    async def test_staged_digest_mismatch_is_discarded_before_any_staged_fact(self) -> None:
        store = FakeStore()
        store.stage_digest_override = "f" * 64
        dao = FakePhaseDao()

        outcome = await _service(artifacts=FakeArtifacts([_read()]), dao=dao, store=store).deliver(
            _command(), _context()
        )

        assert dao.phases == ["manifest_saved"]
        assert store.cancelled and outcome.staged_cleaned is True
        assert outcome.receipt.error_code == "delivery_stage_readback_failed"
        assert store.published == []

    async def test_readback_mismatch_keeps_staged_and_never_publishes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(fields: object, content: bytes, format_name: str) -> tuple[DeliveryField, ...]:
            raise DeliveryRenderError("delivery_render_readback_mismatch", "tampered")

        monkeypatch.setattr(delivery_service_module, "verify_delivery_readback", _boom)
        store = FakeStore()
        dao = FakePhaseDao()

        outcome = await _service(artifacts=FakeArtifacts([_read()]), dao=dao, store=store).deliver(
            _command(), _context()
        )

        assert dao.phases == ["manifest_saved", "staged"]
        assert store.published == []
        assert outcome.receipt.phase == "staged"
        assert outcome.receipt.error_code == "delivery_render_readback_mismatch"
        assert outcome.receipt.output_digest == dao.saved[-1].output_digest
        assert outcome.staged_cleaned is False
        _assert_receipt_wire(outcome.receipt)

    async def test_render_failure_without_body_is_confirmed_before_staging(self) -> None:
        store = FakeStore()
        dao = FakePhaseDao()

        outcome = await _service(
            artifacts=FakeArtifacts([_read(body_text=None, body_available=True)]),
            dao=dao,
            store=store,
        ).deliver(_command(), _context())

        assert dao.phases == ["manifest_saved"]
        assert store.staged_contents == []
        assert outcome.receipt.error_code == "body_missing"

    async def test_stage_oserror_is_reported_as_receipt(self) -> None:
        store = FakeStore()
        store.stage_error = OSError("disk full")
        dao = FakePhaseDao()

        outcome = await _service(artifacts=FakeArtifacts([_read()]), dao=dao, store=store).deliver(
            _command(), _context()
        )

        assert dao.phases == ["manifest_saved"]
        assert outcome.receipt.error_code == "delivery_local_io_error"
        assert store.published == []
        _assert_receipt_wire(outcome.receipt)

    async def test_publish_oserror_cannot_be_verified(self) -> None:
        store = FakeStore()
        store.publish_error = OSError("io error during replace")
        dao = FakePhaseDao()

        outcome = await _service(artifacts=FakeArtifacts([_read()]), dao=dao, store=store).deliver(
            _command(), _context()
        )

        assert dao.phases[-1] == "unknown"
        assert outcome.receipt.publish_effect == "unknown"

    async def test_manifest_save_failure_propagates_without_side_effects(self) -> None:
        dao = FakePhaseDao()
        dao.fail_phases = {"manifest_saved"}
        store = FakeStore()

        with pytest.raises(AgentServiceError) as error:
            await _service(artifacts=FakeArtifacts([_read()]), dao=dao, store=store).deliver(_command(), _context())
        assert error.value.code == AgentErrorCode.REVISION_CONFLICT
        assert store.staged_contents == []

    async def test_artifact_identity_mismatch_is_rejected_before_persist(self) -> None:
        dao = FakePhaseDao()
        store = FakeStore()
        artifacts = FakeArtifacts([_read(artifact_version=3)])

        with pytest.raises(AgentServiceError) as error:
            await _service(artifacts=artifacts, dao=dao, store=store).deliver(_command(), _context())
        assert error.value.code == AgentErrorCode.INPUT_INVALID
        assert dao.phases == []
        assert store.staged_contents == []

    async def test_invalid_command_contract_is_rejected_before_read(self) -> None:
        artifacts = FakeArtifacts([_read()])
        dao = FakePhaseDao()

        with pytest.raises(AgentServiceError) as error:
            await _service(artifacts=artifacts, dao=dao, store=FakeStore()).deliver(
                _command(delivery_id="delivery-other", expected_delivery_revision=1), _context()
            )
        assert error.value.code == AgentErrorCode.INPUT_INVALID
        assert artifacts.queries == []


class TestCancelAndUnknown:
    async def test_cancel_before_publish_wins_and_cleans_only_staged(self) -> None:
        store = FakeStore()
        dao = FakePhaseDao()

        outcome = await _service(artifacts=FakeArtifacts([_read()]), dao=dao, store=store).deliver(
            _command(), _context(), cancel_requested=lambda: True
        )

        assert dao.phases == ["manifest_saved", "staged", "validated", "cancelled_before_publish"]
        assert store.published == []
        assert len(store.cancelled) == 1
        assert dao.saved[-1].staged_path is None
        assert dao.saved[-1].output_digest == dao.saved[1].output_digest
        assert outcome.staged_cleaned is True
        assert outcome.receipt.phase == "cancelled_before_publish"
        assert outcome.receipt.publish_effect == "not_attempted"
        assert outcome.receipt.cancel_effect == "cancelled_before_publish"
        _assert_receipt_wire(outcome.receipt)

    async def test_publish_claim_cas_failure_means_cancel_won(self) -> None:
        dao = FakePhaseDao()
        dao.fail_phases = {"publish_claimed"}
        store = FakeStore()

        with pytest.raises(AgentServiceError) as error:
            await _service(artifacts=FakeArtifacts([_read()]), dao=dao, store=store).deliver(_command(), _context())
        assert error.value.code == AgentErrorCode.REVISION_CONFLICT
        assert dao.phases == ["manifest_saved", "staged", "validated"]
        assert store.publish_attempts == 0
        assert store.published == []
        assert len(store.cancelled) == 1

    async def test_unverifiable_publish_is_unknown_and_not_retried(self) -> None:
        store = FakeStore()
        store.publish_error = DeliveryUnknownError("delivery_publish_unverified", "cannot verify")
        dao = FakePhaseDao()

        outcome = await _service(artifacts=FakeArtifacts([_read()]), dao=dao, store=store).deliver(
            _command(), _context()
        )

        assert dao.phases[-1] == "unknown"
        assert dao.saved[-1].error_code == "delivery_publish_unverified"
        # 中断后只按原交付核对：unknown 必须保留暂存路径与句柄身份（I79）。
        assert dao.saved[-1].staged_path == str(_STAGED_PATH)
        assert (dao.saved[-1].staged_volume_serial, dao.saved[-1].staged_file_id) == (7, 11)
        assert store.publish_attempts == 1
        assert outcome.receipt.phase == "unknown"
        assert outcome.receipt.publish_effect == "unknown"
        assert outcome.receipt.error_code == "delivery_publish_unverified"
        assert outcome.receipt.next_action is not None
        assert outcome.staged_cleaned is False
        _assert_receipt_wire(outcome.receipt)

    async def test_published_content_mismatch_is_unknown(self) -> None:
        store = FakeStore()
        store.publish_digest_override = "0" * 64
        dao = FakePhaseDao()

        outcome = await _service(artifacts=FakeArtifacts([_read()]), dao=dao, store=store).deliver(
            _command(), _context()
        )

        assert dao.phases[-1] == "unknown"
        assert outcome.receipt.phase == "unknown"

    async def test_publish_effect_unknown_when_success_receipt_cannot_be_saved(self) -> None:
        dao = FakePhaseDao()
        dao.fail_phases = {"published_confirmed"}
        store = FakeStore()

        outcome = await _service(artifacts=FakeArtifacts([_read()]), dao=dao, store=store).deliver(
            _command(), _context()
        )

        assert dao.phases == ["manifest_saved", "staged", "validated", "publish_claimed", "unknown"]
        assert dao.saved[-1].error_code == "delivery_receipt_save_failed"
        assert len(store.published) == 1
        assert outcome.receipt.phase == "unknown"
        assert outcome.receipt.publish_effect == "unknown"

    async def test_confirmed_publish_failure_is_not_unknown(self) -> None:
        store = FakeStore()
        store.publish_error = DeliveryFileError("delivery_target_changed", "target changed")
        dao = FakePhaseDao()

        outcome = await _service(artifacts=FakeArtifacts([_read()]), dao=dao, store=store).deliver(
            _command(), _context()
        )

        assert "unknown" not in dao.phases
        assert outcome.receipt.phase == "publish_claimed"
        assert outcome.receipt.error_code == "delivery_target_changed"
        assert outcome.receipt.publish_effect == "not_attempted"
        assert outcome.receipt.output_digest == dao.saved[-1].output_digest
        _assert_receipt_wire(outcome.receipt)


class TestFreshSourceState:
    async def test_file_splits_historical_and_current_state(self) -> None:
        store = FakeStore()
        artifacts = FakeArtifacts([_read()])

        await _service(artifacts=artifacts, dao=FakePhaseDao(), store=store).deliver(_command(), _context())

        index = {field.path: field for field in read_delivery_fields(store.staged_contents[0], "md")}
        assert index["metadata:source_available_state"].value_text == "available"
        assert index["metadata:current_source_state"].value_text == "unknown"

    async def test_non_standard_source_state_is_recorded_as_unknown(self) -> None:
        dao = FakePhaseDao()
        artifacts = FakeArtifacts([_read(source_available_state="current")])

        await _service(artifacts=artifacts, dao=dao, store=FakeStore()).deliver(_command(), _context())

        assert [manifest.source_state for manifest in dao.saved] == ["unknown"] * 5

    async def test_source_recheck_failure_blocks_publication_before_rendering(self) -> None:
        class BrokenFresh(FakeArtifacts):
            async def read_artifact(self, query: ArtifactReadQuery, *, user_scope: str) -> ArtifactRead:
                if query.limit == 1:
                    self.queries.append(query)
                    raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "artifact cleaned", "op-1")
                return await super().read_artifact(query, user_scope=user_scope)

        artifacts = BrokenFresh([_read()])
        store = FakeStore()
        dao = FakePhaseDao()

        outcome = await _service(artifacts=artifacts, dao=dao, store=store).deliver(_command(), _context())

        assert dao.phases == ["manifest_saved"]
        assert store.staged_contents == []
        assert store.published == []
        assert outcome.receipt.phase == "manifest_saved"
        assert outcome.receipt.error_code is not None
        assert outcome.receipt.error_code.startswith("delivery_source_recheck_failed:")

    @pytest.mark.parametrize("format_name", ["md", "csv", "xlsx"])
    async def test_disabled_state_is_rendered_consistently_in_all_formats(self, format_name: str) -> None:
        store = FakeStore()
        dao = FakePhaseDao()
        artifacts = FakeArtifacts([_read()], fresh_states=["disabled"])

        outcome = await _service(artifacts=artifacts, dao=dao, store=store).deliver(
            _command(format=format_name), _context()
        )

        index = {field.path: field for field in read_delivery_fields(store.staged_contents[0], format_name)}
        assert index["metadata:source_available_state"].value_text == "available"
        assert index["metadata:current_source_state"].value_text == "disabled"
        assert dao.saved[1].source_state == "disabled"
        assert dao.saved[-1].output_digest == sha256_hex(store.staged_contents[0])
        assert outcome.receipt.phase == "published_confirmed"

    async def test_state_change_between_render_and_publish_rebuilds_file(self) -> None:
        store = FakeStore()
        dao = FakePhaseDao()
        artifacts = FakeArtifacts([_read()], fresh_states=["available", "disabled", "disabled", "disabled"])

        outcome = await _service(artifacts=artifacts, dao=dao, store=store).deliver(_command(), _context())

        assert len(store.staged_contents) == 2
        assert len(store.cancelled) == 1
        index = {field.path: field for field in read_delivery_fields(store.staged_contents[-1], "md")}
        assert index["metadata:source_available_state"].value_text == "available"
        assert index["metadata:current_source_state"].value_text == "disabled"
        assert dao.saved[1].output_digest == sha256_hex(store.staged_contents[-1])
        assert dao.saved[-1].output_digest == sha256_hex(store.staged_contents[-1])
        assert outcome.receipt.phase == "published_confirmed"

    async def test_state_change_after_validation_stops_before_publish(self) -> None:
        store = FakeStore()
        dao = FakePhaseDao()
        artifacts = FakeArtifacts([_read()], fresh_states=["available", "available", "disabled"])

        outcome = await _service(artifacts=artifacts, dao=dao, store=store).deliver(_command(), _context())

        assert dao.phases == ["manifest_saved", "staged", "validated"]
        assert store.published == []
        assert outcome.receipt.phase == "validated"
        assert outcome.receipt.error_code == "delivery_source_state_changed"
        assert outcome.receipt.publish_effect == "not_attempted"

    async def test_unstable_state_stops_before_staging_fact(self) -> None:
        store = FakeStore()
        dao = FakePhaseDao()
        artifacts = FakeArtifacts([_read()], fresh_states=["available", "disabled"] * 3)

        outcome = await _service(artifacts=artifacts, dao=dao, store=store).deliver(_command(), _context())

        assert dao.phases == ["manifest_saved"]
        assert store.published == []
        assert len(store.cancelled) == 3
        assert outcome.receipt.error_code == "delivery_source_state_unstable"
        assert outcome.staged_cleaned is True

    async def test_each_source_ref_state_is_written_and_unknown_is_not_masked(self) -> None:
        store = FakeStore()
        dao = FakePhaseDao()
        artifacts = FakeArtifacts(
            [_read()],
            fresh_ref_states=[[_ref_state("src-a", "available"), _ref_state("src-b", "unknown")]],
        )

        outcome = await _service(artifacts=artifacts, dao=dao, store=store).deliver(_command(), _context())

        index = {field.path: field for field in read_delivery_fields(store.staged_contents[0], "md")}
        assert index["metadata:source_available_state"].value_text == "available"
        assert index["source:/0/source_id"].value_text == "src-a"
        assert index["source:/0/source_kind"].value_text == "attachment"
        assert index["source:/0/source_version"].value_text == "1"
        assert index["source:/0/state"].value_text == "available"
        assert index["source:/1/source_id"].value_text == "src-b"
        assert index["source:/1/state"].value_text == "unknown"
        # 逐项权威：一个未知项不得被另一个可用项掩盖成 available（R21）。
        assert index["metadata:current_source_state"].value_text == "unknown"
        assert dao.saved[0].source_state == "available"
        assert [manifest.source_state for manifest in dao.saved[1:]] == ["unknown"] * 4
        assert outcome.receipt.phase == "published_confirmed"

    @pytest.mark.parametrize("format_name", ["md", "csv", "xlsx"])
    async def test_disabled_source_ref_is_written_in_all_formats(self, format_name: str) -> None:
        store = FakeStore()
        dao = FakePhaseDao()
        artifacts = FakeArtifacts(
            [_read()],
            fresh_ref_states=[[_ref_state("src-a", "available"), _ref_state("src-b", "disabled")]],
        )

        outcome = await _service(artifacts=artifacts, dao=dao, store=store).deliver(
            _command(format=format_name), _context()
        )

        index = {field.path: field for field in read_delivery_fields(store.staged_contents[0], format_name)}
        assert index["metadata:current_source_state"].value_text == "disabled"
        assert index["source:/1/state"].value_text == "disabled"
        assert dao.saved[1].source_state == "disabled"
        assert outcome.receipt.phase == "published_confirmed"

    async def test_per_ref_change_between_render_and_recheck_rebuilds_file(self) -> None:
        store = FakeStore()
        dao = FakePhaseDao()
        artifacts = FakeArtifacts(
            [_read()],
            fresh_ref_states=[
                [_ref_state("src-a", "available")],
                [_ref_state("src-a", "disabled")],
                [_ref_state("src-a", "disabled")],
                [_ref_state("src-a", "disabled")],
            ],
        )

        outcome = await _service(artifacts=artifacts, dao=dao, store=store).deliver(_command(), _context())

        assert len(store.staged_contents) == 2
        assert len(store.cancelled) == 1
        index = {field.path: field for field in read_delivery_fields(store.staged_contents[-1], "md")}
        assert index["source:/0/state"].value_text == "disabled"
        assert index["metadata:current_source_state"].value_text == "disabled"
        assert outcome.receipt.phase == "published_confirmed"

    async def test_restored_source_can_be_exported_again(self) -> None:
        blocked_store = FakeStore()
        blocked = await _service(
            artifacts=FakeArtifacts([_read()], fresh_ref_states=[[_ref_state("src-a", "disabled")]]),
            dao=FakePhaseDao(),
            store=blocked_store,
        ).deliver(_command(), _context())

        restored_store = FakeStore()
        restored = await _service(
            artifacts=FakeArtifacts([_read()], fresh_ref_states=[[_ref_state("src-a", "available")]]),
            dao=FakePhaseDao(),
            store=restored_store,
        ).deliver(_command(), _context())

        assert blocked.receipt.phase == "published_confirmed"
        blocked_index = {field.path: field for field in read_delivery_fields(blocked_store.staged_contents[0], "md")}
        assert blocked_index["source:/0/state"].value_text == "disabled"
        restored_index = {field.path: field for field in read_delivery_fields(restored_store.staged_contents[0], "md")}
        assert restored_index["source:/0/state"].value_text == "available"
        assert restored_index["metadata:current_source_state"].value_text == "available"
        assert restored.receipt.phase == "published_confirmed"


class TestCsvAndXlsxFormats:
    @pytest.mark.parametrize("format_name", ["csv", "xlsx"])
    async def test_other_formats_deliver_and_roundtrip(self, format_name: str) -> None:
        store = FakeStore()
        dao = FakePhaseDao()

        outcome = await _service(artifacts=FakeArtifacts([_read()]), dao=dao, store=store).deliver(
            _command(format=format_name), _context()
        )

        assert outcome.receipt.phase == "published_confirmed"
        assert dao.saved[-1].format == format_name
        index = {field.path: field for field in read_delivery_fields(store.staged_contents[0], format_name)}
        assert index["payload:/s"].value_text == "00123"
        assert index["payload:/n/1"].value_type == "null"
        assert index["body:"].value_text == "成果正文"
