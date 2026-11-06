"""C03：AgentService 读取/回执失败出口（§5.2/§5.4）。"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest

from core.agent_types import (
    AgentOperationError,
    CommandReceipt,
    ExecutionView,
    Page,
    SubmissionKey,
    SubmissionReceipt,
    decode_json_doc,
    json_doc,
)
from data.persistence.daos.base_dao import EngineDisposedError
from services.agent.service import AgentDaoProtocol, AgentService
from tests.builders.agent import make_request

_EXECUTION_ID = UUID("40000000-0000-0000-0000-000000000001")


def _execution(status: str = "running") -> ExecutionView:
    return ExecutionView(
        execution_id=_EXECUTION_ID,
        conversation_id=None,
        input_revision=1,
        plan_revision=1,
        status=status,
        fresh=True,
        observed_at=datetime(2026, 4, 30, tzinfo=UTC),
        tasks=(),
        native_ref=None,
        continuation_ref=None,
        usage=json_doc({}),
        problem=None,
        source_module="agent",
    )


def _submission(status: str = "accepted") -> SubmissionReceipt:
    return SubmissionReceipt(
        status=status,  # type: ignore[arg-type]  # 参数化测试状态
        key=SubmissionKey("test-domain", "tests", "request-1"),
        intent_id=uuid4(),
        execution_id=None,
        input_revision=None,
        accepted_seq=None,
        problem=None,
    )


class FakeDao:
    def __init__(self, *, responses=None, errors=None) -> None:
        self.responses = responses or {}
        self.errors = errors or {}
        self.calls: list[tuple[object, ...]] = []

    async def _respond(self, name: str, *args):
        self.calls.append((name, *args))
        if name in self.errors:
            raise self.errors[name]
        value = self.responses.get(name)
        if isinstance(value, BaseException):
            raise value
        return value

    async def read_execution(self, domain, execution_id):
        return await self._respond("read_execution")

    async def read_conversation(self, domain, conversation_id):
        return await self._respond("read_conversation")

    async def list_conversations(self, domain, *, archived, cursor, limit):
        return await self._respond("list_conversations")

    async def read_messages(self, domain, *, conversation_id, execution_id, attempt_id, cursor, limit):
        return await self._respond("read_messages")

    async def read_artifact(self, domain, artifact_id, revision):
        return await self._respond("read_artifact")

    async def read_decision(self, domain, decision_id):
        return await self._respond("read_decision")

    async def lookup_delivery(self, domain, operation_id):
        return await self._respond("lookup_delivery")

    async def read_plan(self, domain, ref):
        return await self._respond("read_plan")

    async def read_resource(self, domain, ref):
        return await self._respond("read_resource")

    async def list_queue(self, domain, *, cursor, limit):
        return await self._respond("list_queue")

    async def lookup_submission(self, domain, key):
        return await self._respond("lookup_submission")

    async def lookup_command(self, domain, command_id):
        return await self._respond("lookup_command")

    async def lookup_mutation(self, domain, operation_id):
        return await self._respond("lookup_mutation")

    async def accept_request(self, domain, request, digest):
        return await self._respond("accept_request", domain, digest)

    async def save_command(self, domain, command):
        return await self._respond("save_command")

    async def apply_mutation(self, domain, mutation):
        return await self._respond("apply_mutation")


def _service(fake: FakeDao) -> AgentService:
    return AgentService(domain="test-domain", dao=cast(AgentDaoProtocol, fake))


async def test_missing_execution_raises_not_found_instead_of_fake_success():
    fake = FakeDao()
    service = _service(fake)

    with pytest.raises(AgentOperationError) as error:
        await service.get_execution(_EXECUTION_ID)
    assert error.value.problem.code == "not_found"
    assert error.value.problem.related_id == _EXECUTION_ID
    assert fake.calls == [("read_execution",)]


async def test_retired_execution_raises_retired():
    fake = FakeDao(responses={"read_execution": _execution("retired")})
    service = _service(fake)

    with pytest.raises(AgentOperationError) as error:
        await service.get_execution(_EXECUTION_ID)
    assert error.value.problem.code == "retired"


async def test_empty_page_is_a_real_empty_list_success():
    fake = FakeDao(responses={"list_conversations": Page(items=(), next_cursor=None, read_revision=7)})
    service = _service(fake)

    page = await service.list_conversations(archived=None, cursor=None)
    assert page.items == ()
    assert page.read_revision == 7


async def test_read_failure_is_sanitized_and_never_becomes_empty_success():
    fake = FakeDao(errors={"read_decision": RuntimeError("token=super-secret")})
    service = _service(fake)

    with pytest.raises(AgentOperationError) as error:
        await service.read_decision(uuid4())
    problem = error.value.problem
    assert problem.code == "source_unavailable"
    assert "super-secret" not in problem.message_key
    assert "super-secret" not in problem.params
    params = decode_json_doc(problem.params)
    assert isinstance(params, dict)
    assert params["field_issues"] == []


async def test_cancellation_and_engine_disposal_propagate_untouched():
    service = _service(FakeDao(errors={"read_artifact": asyncio.CancelledError()}))
    with pytest.raises(asyncio.CancelledError):
        await service.read_artifact(uuid4(), 1)

    service = _service(FakeDao(errors={"read_plan": EngineDisposedError("disposed")}))
    with pytest.raises(EngineDisposedError):
        await service.read_plan(make_request().roles[0].prompt)


async def test_receipt_methods_return_dao_status_unchanged():
    command = CommandReceipt(
        command_id=uuid4(),
        status="conflict",
        native_request_id=None,
        continuation_ref=None,
        problem=None,
    )
    receipt = _submission("conflict")
    fake = FakeDao(responses={"lookup_command": command, "accept_request": receipt, "save_command": command})
    service = _service(fake)

    assert await service.lookup_command(command.command_id) is command
    result = await service.submit(make_request())
    assert result is receipt
    assert result.status == "conflict"
    assert fake.calls[-1][0] == "accept_request"
    assert fake.calls[-1][1] == "test-domain"


async def test_parameter_validation_happens_before_dao():
    fake = FakeDao()
    service = _service(fake)

    with pytest.raises(AgentOperationError) as error:
        await service.read_messages(conversation_id=None, execution_id=None, attempt_id=None)
    assert error.value.problem.code == "invalid_request"

    with pytest.raises(AgentOperationError) as error:
        await service.read_messages(conversation_id=uuid4(), execution_id=None, attempt_id=None, limit=201)
    assert error.value.problem.code == "invalid_request"

    with pytest.raises(AgentOperationError) as error:
        await service.read_artifact(uuid4(), 0)
    assert error.value.problem.code == "invalid_request"

    assert fake.calls == []


async def test_submit_validates_domain_and_request_before_dao():
    fake = FakeDao()
    service = _service(fake)

    foreign = replace(make_request(), key=SubmissionKey("other-domain", "tests", "request-1"))
    with pytest.raises(AgentOperationError) as error:
        await service.submit(foreign)
    assert error.value.problem.code == "reference_conflict"

    with pytest.raises(AgentOperationError) as error:
        await service.submit(replace(make_request(), text=""))
    assert error.value.problem.code == "invalid_request"
    params = decode_json_doc(error.value.problem.params)
    assert isinstance(params, dict)
    assert params["field_issues"][0]["field_path"] == "text"

    assert fake.calls == []


async def test_missing_message_scope_does_not_fabricate_window():
    fake = FakeDao(responses={"read_messages": None})
    service = _service(fake)

    with pytest.raises(AgentOperationError) as error:
        await service.read_messages(conversation_id=uuid4(), execution_id=None, attempt_id=None)
    assert error.value.problem.code == "not_found"
