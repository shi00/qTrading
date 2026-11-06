"""S01 来源登记：受管 blob、PIT、业务/工具/摘录登记与零重取。"""

from __future__ import annotations

import json
import os
import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from core.agent_types import (
    AgentOperationError,
    JsonDoc,
    MutationReceipt,
    Problem,
    decode_json_doc,
    json_doc,
)
from services.agent.service import AgentService

pytestmark = pytest.mark.asyncio

_DOMAIN = "test-domain"
_AS_OF = "2026-04-30"


def _payload(source: JsonDoc) -> dict[str, Any]:
    decoded = decode_json_doc(source)
    assert isinstance(decoded, dict)
    return decoded


def _metadata(**overrides: object) -> dict[str, object]:
    doc: dict[str, object] = {
        "schema_version": 1,
        "name": "公告原文",
        "as_of": _AS_OF,
        "published_at": None,
        "published_date": "2026-03-20",
        "time_precision": "date",
        "period": None,
        "effective_from": None,
        "effective_to": None,
        "units": {},
        "correction_of": None,
    }
    doc.update(overrides)
    return doc


def _registration_business(**overrides: object) -> JsonDoc:
    doc: dict[str, object] = {
        "schema_version": 1,
        "kind": "business",
        "provider_key": "stock",
        "source_object_key": "600519",
        "source_revision": "2025-12-31",
        "as_of": _AS_OF,
    }
    doc.update(overrides)
    return json_doc(doc)


def _prepared(**overrides: object) -> JsonDoc:
    doc: dict[str, object] = {
        "schema_version": 1,
        "metadata": _metadata(),
        "media_type": "text/plain",
        "text": "年报摘要正文",
        "read_range": {
            "pages": [1, 2],
            "sheets": [],
            "row_start": None,
            "row_end": None,
            "locator": "p1-2",
        },
        "extraction": "partial",
        "limitations": ["仅前两页"],
    }
    doc.update(overrides)
    return json_doc(doc)


class _FakeDao:
    def __init__(self) -> None:
        self.operations: dict[uuid.UUID, tuple[str, str]] = {}
        self.receipts: dict[uuid.UUID, MutationReceipt] = {}
        self.saved: list[tuple[uuid.UUID, str, JsonDoc]] = []
        self.tool_calls: dict[str, JsonDoc] = {}
        self.executions: dict[uuid.UUID, Any] = {}
        self.accept_request_calls = 0

    async def lookup_source_operation(self, domain: str, operation_id: uuid.UUID) -> tuple[str, str] | None:
        assert domain == _DOMAIN
        return self.operations.get(operation_id)

    async def lookup_mutation(self, domain: str, operation_id: uuid.UUID) -> MutationReceipt:
        receipt = self.receipts.get(operation_id)
        if receipt is None:
            raise AgentOperationError(Problem("not_found", "x", "{}", None))
        return receipt

    async def save_source(
        self, domain: str, operation_id: uuid.UUID, input_digest: str, source: JsonDoc
    ) -> MutationReceipt:
        assert domain == _DOMAIN
        existing = self.operations.get(operation_id)
        if existing is not None and existing[1] != input_digest:
            return MutationReceipt(
                operation_id=operation_id,
                status="conflict",
                resource_id=None,
                revision=None,
                problem=Problem("identity_conflict", "x", "{}", None),
            )
        receipt = MutationReceipt(
            operation_id=operation_id,
            status="committed",
            resource_id=uuid.uuid5(operation_id, "source"),
            revision=1,
            problem=None,
        )
        kind = "source_import" if _payload(source)["origin"] == "file" else "source_register"
        self.operations[operation_id] = (kind, input_digest)
        self.receipts[operation_id] = receipt
        self.saved.append((operation_id, input_digest, source))
        return receipt

    async def read_tool_call(self, effect_key: str) -> JsonDoc | None:
        return self.tool_calls.get(effect_key)

    async def read_execution(self, domain: str, execution_id: uuid.UUID) -> Any:
        assert domain == _DOMAIN
        return self.executions.get(execution_id)

    async def accept_request(self, domain: str, request: object, digest: str) -> object:
        self.accept_request_calls += 1


class _FakeProvider:
    def __init__(self, prepared: JsonDoc | None = None) -> None:
        self.prepared = prepared or _prepared()
        self.calls: list[tuple[str, str, date]] = []

    async def read_source(self, source_object_key: str, expected_revision: str, as_of: date) -> JsonDoc:
        self.calls.append((source_object_key, expected_revision, as_of))
        return self.prepared


def _service(tmp_path: Path, dao: _FakeDao, providers: dict[str, Any] | None = None) -> AgentService:
    return AgentService(
        domain=_DOMAIN,
        dao=dao,  # type: ignore[arg-type]  # 单元测试替身固定同名接口
        source_providers=providers or {},
        blob_root=tmp_path,
    )


def _blob(tmp_path: Path, source: JsonDoc) -> tuple[Path, str]:
    payload = _payload(source)
    ref = payload["content_ref"]
    assert isinstance(ref, dict) and ref["kind"] == "managed_blob"
    path = tmp_path / ref["relative_path"]
    return path, ref["digest"]


async def test_import_source_date_only_writes_managed_blob_and_source(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "notice.md"
    file_path.write_text("仅日期文件正文", encoding="utf-8")
    operation_id = uuid.uuid4()
    dao = _FakeDao()

    receipt = await _service(tmp_path, dao).import_source(operation_id, str(file_path), json_doc(_metadata()))

    assert receipt.status == "committed"
    assert receipt.resource_id == uuid.uuid5(operation_id, "source")
    assert receipt.revision == 1
    source = dao.saved[0][2]
    payload = _payload(source)
    assert payload["origin"] == "file"
    assert payload["media_type"] == "text/markdown"
    assert payload["time_precision"] == "date"
    assert payload["as_of"] == _AS_OF
    assert payload["extraction"] == "complete"
    assert payload["limitations"] == []
    blob_path, _digest = _blob(tmp_path, source)
    assert blob_path.read_text(encoding="utf-8") == "仅日期文件正文"
    assert dao.accept_request_calls == 0


async def test_import_source_rejects_future_as_of(tmp_path: Path) -> None:
    file_path = tmp_path / "notice.md"
    file_path.write_text("正文", encoding="utf-8")
    dao = _FakeDao()
    with pytest.raises(AgentOperationError) as caught:
        await _service(tmp_path, dao).import_source(
            uuid.uuid4(), str(file_path), json_doc(_metadata(as_of="2099-01-01"))
        )
    assert caught.value.problem.code == "pit_ineligible"
    assert dao.saved == []


async def test_import_source_rejects_datetime_after_as_of(tmp_path: Path) -> None:
    file_path = tmp_path / "notice.md"
    file_path.write_text("正文", encoding="utf-8")
    dao = _FakeDao()
    metadata = _metadata(
        time_precision="datetime",
        published_at="2026-05-01T01:00:00.000000Z",
        published_date="2026-05-01",
    )
    with pytest.raises(AgentOperationError) as caught:
        await _service(tmp_path, dao).import_source(uuid.uuid4(), str(file_path), json_doc(metadata))
    assert caught.value.problem.code == "pit_ineligible"


async def test_import_source_preserves_binary_media(tmp_path: Path) -> None:
    file_path = tmp_path / "chart.png"
    file_path.write_bytes(b"\x89PNG\r\n\x1a\nbytes")
    operation_id = uuid.uuid4()
    dao = _FakeDao()

    await _service(tmp_path, dao).import_source(operation_id, str(file_path), json_doc(_metadata(name="图表")))

    payload = _payload(dao.saved[0][2])
    assert payload["media_type"] == "image/png"
    blob_path, _digest = _blob(tmp_path, dao.saved[0][2])
    assert blob_path.read_bytes() == b"\x89PNG\r\n\x1a\nbytes"


async def test_import_source_same_operation_does_not_reread_path(tmp_path: Path) -> None:
    file_path = tmp_path / "notice.md"
    file_path.write_text("正文", encoding="utf-8")
    operation_id = uuid.uuid4()
    dao = _FakeDao()
    service = _service(tmp_path, dao)
    first = await service.import_source(operation_id, str(file_path), json_doc(_metadata()))
    os.unlink(file_path)

    second = await service.import_source(operation_id, str(file_path), json_doc(_metadata()))

    assert first == second
    assert len(dao.saved) == 1


async def test_import_source_same_operation_different_payload_conflicts(tmp_path: Path) -> None:
    file_path = tmp_path / "notice.md"
    file_path.write_text("正文", encoding="utf-8")
    operation_id = uuid.uuid4()
    dao = _FakeDao()
    service = _service(tmp_path, dao)
    await service.import_source(operation_id, str(file_path), json_doc(_metadata()))
    os.unlink(file_path)

    result = await service.import_source(operation_id, str(file_path), json_doc(_metadata(name="另一份")))

    assert result.status == "conflict"
    assert result.resource_id is None
    assert len(dao.saved) == 1


async def test_register_business_uses_provider_and_keeps_partial_range(tmp_path: Path) -> None:
    provider = _FakeProvider()
    dao = _FakeDao()
    operation_id = uuid.uuid4()

    receipt = await _service(tmp_path, dao, {"stock": provider}).register_source(operation_id, _registration_business())

    assert receipt.status == "committed"
    assert provider.calls == [("600519", "2025-12-31", date(2026, 4, 30))]
    payload = _payload(dao.saved[0][2])
    assert payload["origin"] == "business"
    assert payload["extraction"] == "partial"
    assert payload["limitations"] == ["仅前两页"]
    assert payload["read_range"] == {
        "pages": [1, 2],
        "sheets": [],
        "row_start": None,
        "row_end": None,
        "locator": "p1-2",
    }
    blob_path, _digest = _blob(tmp_path, dao.saved[0][2])
    assert blob_path.read_text(encoding="utf-8") == "年报摘要正文"


async def test_register_business_unknown_provider_is_unavailable(tmp_path: Path) -> None:
    dao = _FakeDao()
    with pytest.raises(AgentOperationError) as caught:
        await _service(tmp_path, dao).register_source(uuid.uuid4(), _registration_business())
    assert caught.value.problem.code == "source_unavailable"
    assert dao.saved == []


async def test_register_business_rejects_provider_as_of_mismatch(tmp_path: Path) -> None:
    provider = _FakeProvider(_prepared(metadata=_metadata(as_of="2026-04-29")))
    dao = _FakeDao()
    with pytest.raises(AgentOperationError) as caught:
        await _service(tmp_path, dao, {"stock": provider}).register_source(uuid.uuid4(), _registration_business())
    assert caught.value.problem.code == "source_unavailable"
    assert dao.saved == []


async def test_register_business_rejects_empty_text(tmp_path: Path) -> None:
    provider = _FakeProvider(_prepared(text="   "))
    dao = _FakeDao()
    with pytest.raises(AgentOperationError) as caught:
        await _service(tmp_path, dao, {"stock": provider}).register_source(uuid.uuid4(), _registration_business())
    assert caught.value.problem.code == "source_unavailable"


async def test_register_user_excerpt_is_not_authoritative(tmp_path: Path) -> None:
    manifest = {
        "schema_version": 1,
        "kind": "user_excerpt",
        "metadata": _metadata(time_precision="unknown", published_date=None),
        "text": "用户自己选的一段",
    }
    dao = _FakeDao()
    operation_id = uuid.uuid4()

    receipt = await _service(tmp_path, dao).register_source(operation_id, json_doc(manifest))

    assert receipt.status == "committed"
    payload = _payload(dao.saved[0][2])
    assert payload["origin"] == "user_excerpt"
    assert payload["media_type"] == "text/plain"
    assert payload["name"] == "公告原文"
    blob_path, _digest = _blob(tmp_path, dao.saved[0][2])
    assert blob_path.read_text(encoding="utf-8") == "用户自己选的一段"


def _tool_doc(
    execution_id: uuid.UUID,
    attempt_id: uuid.UUID,
    *,
    state: str = "committed",
    retired_at: str | None = None,
) -> JsonDoc:
    return json_doc(
        {
            "effect_key": "effect-1",
            "execution_id": execution_id,
            "attempt_id": attempt_id,
            "native_owner_id": "owner-1",
            "native_task_id": "native-task-1",
            "native_run_id": "native-run-1",
            "native_event_ref": "event-1",
            "tool_call_id": "call-1",
            "tool_ref": {"name": "search_news", "schema_revision": "1"},
            "input_digest": "c" * 64,
            "state": state,
            "response": {"items": [{"title": "样本新闻"}], "status": "ok"},
            "result_digest": "d" * 64,
            "retired_at": retired_at,
        }
    )


def _registration_tool(execution_id: uuid.UUID, task_id: uuid.UUID, attempt_id: uuid.UUID) -> JsonDoc:
    return json_doc(
        {
            "schema_version": 1,
            "kind": "tool",
            "target": {
                "execution_id": execution_id,
                "task_id": task_id,
                "attempt_id": attempt_id,
            },
            "effect_key": "effect-1",
            "as_of": _AS_OF,
        }
    )


def _seed_tool(
    dao: _FakeDao, execution_id: uuid.UUID, task_id: uuid.UUID, attempt_id: uuid.UUID, **overrides: Any
) -> None:
    dao.tool_calls["effect-1"] = _tool_doc(execution_id, attempt_id, **overrides)
    dao.executions[execution_id] = SimpleNamespace(tasks=(SimpleNamespace(task_id=task_id, attempt_id=attempt_id),))


async def test_register_tool_uses_committed_response_with_unknown_published_time(tmp_path: Path) -> None:
    execution_id, task_id, attempt_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    dao = _FakeDao()
    _seed_tool(dao, execution_id, task_id, attempt_id)
    operation_id = uuid.uuid4()

    receipt = await _service(tmp_path, dao).register_source(
        operation_id, _registration_tool(execution_id, task_id, attempt_id)
    )

    assert receipt.status == "committed"
    payload = _payload(dao.saved[0][2])
    assert payload["origin"] == "tool"
    assert payload["media_type"] == "application/json"
    assert payload["time_precision"] == "unknown"
    assert payload["published_at"] is None and payload["published_date"] is None
    assert payload["units"] == {}
    blob_path, _digest = _blob(tmp_path, dao.saved[0][2])
    assert json.loads(blob_path.read_text(encoding="utf-8")) == {"items": [{"title": "样本新闻"}], "status": "ok"}


async def test_register_tool_wrong_target_is_rejected_without_read(tmp_path: Path) -> None:
    execution_id, task_id, attempt_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    dao = _FakeDao()
    _seed_tool(dao, execution_id, task_id, attempt_id)
    with pytest.raises(AgentOperationError) as caught:
        await _service(tmp_path, dao).register_source(
            uuid.uuid4(), _registration_tool(execution_id, uuid.uuid4(), attempt_id)
        )
    assert caught.value.problem.code == "reference_conflict"
    assert dao.saved == []


async def test_register_tool_unknown_or_retired_is_rejected(tmp_path: Path) -> None:
    execution_id, task_id, attempt_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    dao = _FakeDao()
    _seed_tool(dao, execution_id, task_id, attempt_id, state="unknown")
    with pytest.raises(AgentOperationError) as caught:
        await _service(tmp_path, dao).register_source(
            uuid.uuid4(), _registration_tool(execution_id, task_id, attempt_id)
        )
    assert caught.value.problem.code == "source_unavailable"

    _seed_tool(dao, execution_id, task_id, attempt_id, retired_at="2026-04-30T00:00:00.000Z")
    with pytest.raises(AgentOperationError) as caught:
        await _service(tmp_path, dao).register_source(
            uuid.uuid4(), _registration_tool(execution_id, task_id, attempt_id)
        )
    assert caught.value.problem.code == "source_unavailable"
    assert dao.saved == []


async def test_register_same_operation_does_not_call_provider_twice(tmp_path: Path) -> None:
    provider = _FakeProvider()
    dao = _FakeDao()
    operation_id = uuid.uuid4()
    service = _service(tmp_path, dao, {"stock": provider})

    first = await service.register_source(operation_id, _registration_business())
    second = await service.register_source(operation_id, _registration_business())

    assert first == second
    assert len(provider.calls) == 1
    assert len(dao.saved) == 1
