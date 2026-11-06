"""D02：AgentService.list_queue 参数校验与空页语义（§5.4/§6）。

真实 DAO 的游标/排序/域隔离由 tests/integration/agent/test_store.py 覆盖；
本文件只验证服务边界不放宽 limit、不吞读取失败、不把空页伪装成错误。
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from core.agent_types import (
    AgentOperationError,
    Page,
    QueueItemView,
    SubmissionKey,
    SubmissionReceipt,
    decode_json_doc,
)
from services.agent.service import AgentDaoProtocol, AgentService

_DOMAIN = "test-domain"


def _item(admission_seq: int) -> QueueItemView:
    return QueueItemView(
        intent_id=uuid4(),
        execution_id=uuid4(),
        conversation_id=uuid4(),
        input_revision=1,
        accepted_seq=admission_seq,
        admission_seq=admission_seq,
        acceptance_state="queued",
        text_excerpt=f"item-{admission_seq}",
        problem=None,
    )


class FakeDao:
    def __init__(self, *, page: Page[QueueItemView] | None = None, error: Exception | None = None) -> None:
        self.page = page
        self.error = error
        self.calls: list[tuple[object, ...]] = []

    async def list_queue(self, domain: str, *, cursor: str | None, limit: int) -> Page[QueueItemView]:
        self.calls.append((domain, cursor, limit))
        if self.error is not None:
            raise self.error
        assert self.page is not None
        return self.page

    async def read_execution(self, domain: str, execution_id: UUID):  # pragma: no cover - 协议满足
        raise NotImplementedError

    async def read_conversation(self, domain: str, conversation_id: UUID):  # pragma: no cover
        raise NotImplementedError

    async def list_conversations(self, domain: str, *, archived, cursor, limit):  # pragma: no cover
        raise NotImplementedError

    async def read_messages(self, domain: str, **kwargs):  # pragma: no cover
        raise NotImplementedError

    async def read_artifact(self, domain: str, artifact_id: UUID, revision: int):  # pragma: no cover
        raise NotImplementedError

    async def read_decision(self, domain: str, decision_id: UUID):  # pragma: no cover
        raise NotImplementedError

    async def lookup_delivery(self, domain: str, operation_id: UUID):  # pragma: no cover
        raise NotImplementedError

    async def read_plan(self, domain: str, ref):  # pragma: no cover
        raise NotImplementedError

    async def read_resource(self, domain: str, ref):  # pragma: no cover
        raise NotImplementedError

    async def lookup_submission(self, domain: str, key: SubmissionKey) -> SubmissionReceipt:  # pragma: no cover
        raise NotImplementedError

    async def lookup_command(self, domain: str, command_id: UUID):  # pragma: no cover
        raise NotImplementedError

    async def lookup_mutation(self, domain: str, operation_id: UUID):  # pragma: no cover
        raise NotImplementedError

    async def accept_request(self, domain: str, request, digest: str):  # pragma: no cover
        raise NotImplementedError

    async def save_command(self, domain: str, command):  # pragma: no cover
        raise NotImplementedError

    async def apply_mutation(self, domain: str, mutation):  # pragma: no cover
        raise NotImplementedError


def _service(dao: FakeDao) -> AgentService:
    return AgentService(domain=_DOMAIN, dao=cast(AgentDaoProtocol, dao))


@pytest.mark.parametrize("limit", [0, -1, 201, 10_000])
async def test_limit_out_of_range_is_rejected_before_dao(limit: int) -> None:
    dao = FakeDao(page=Page(items=(), next_cursor=None, read_revision=0))
    service = _service(dao)
    with pytest.raises(AgentOperationError) as error:
        await service.list_queue(cursor=None, limit=limit)
    assert error.value.problem.code == "invalid_request"
    params = cast(dict[str, Any], decode_json_doc(error.value.problem.params))
    assert params["field_issues"][0]["field_path"] == "limit"
    assert dao.calls == []


async def test_empty_queue_is_success_not_error() -> None:
    dao = FakeDao(page=Page(items=(), next_cursor=None, read_revision=3))
    page = await _service(dao).list_queue(cursor=None, limit=50)
    assert page.items == ()
    assert page.next_cursor is None
    assert page.read_revision == 3
    assert dao.calls == [(_DOMAIN, None, 50)]


async def test_cursor_and_limit_are_passed_through_unchanged() -> None:
    cursor = "djE6ZG9tYWluOnRlc3Q"
    page = Page(items=(_item(1), _item(2)), next_cursor="next", read_revision=2)
    dao = FakeDao(page=page)
    result = await _service(dao).list_queue(cursor=cursor, limit=2)
    assert result is page
    assert dao.calls == [(_DOMAIN, cursor, 2)]


async def test_dao_failure_is_sanitized_and_never_becomes_empty_queue() -> None:
    dao = FakeDao(error=RuntimeError("token=super-secret"))
    with pytest.raises(AgentOperationError) as error:
        await _service(dao).list_queue(cursor=None, limit=50)
    assert error.value.problem.code == "source_unavailable"
    assert "super-secret" not in error.value.problem.message_key
    assert "super-secret" not in error.value.problem.params
