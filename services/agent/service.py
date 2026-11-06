"""Agent 服务公开读取/回执入口（设计正本 §5.2/§5.4）。

本模块只做服务边界：参数先验证、调用 DAO、失败转译成不可变 Problem。
提交/管理事务与执行编排由后续卡在同一服务上补齐，不另建第二入口。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID

from core.agent_types import (
    AgentOperationError,
    AgentRequest,
    AgentValidationError,
    ArtifactView,
    CommandReceipt,
    ControlCommand,
    ConversationView,
    DecisionView,
    DeliveryView,
    ExecutionView,
    JsonDoc,
    MessageWindow,
    Mutation,
    MutationReceipt,
    Page,
    PlanView,
    Problem,
    QueueItemView,
    ResourceRef,
    SubmissionKey,
    SubmissionReceipt,
    json_doc,
    request_digest,
    validate_request,
)
from data.persistence.daos.base_dao import EngineDisposedError

_READ_FAILED = "source_unavailable"


class AgentDaoProtocol(Protocol):
    """D01/D02 提供同名实现；C03 用测试替身固定失败出口。"""

    async def read_execution(self, domain: str, execution_id: UUID) -> ExecutionView | None: ...

    async def read_conversation(self, domain: str, conversation_id: UUID) -> ConversationView | None: ...

    async def list_conversations(
        self, domain: str, *, archived: bool | None, cursor: str | None, limit: int
    ) -> Page[ConversationView]: ...

    async def read_messages(
        self,
        domain: str,
        *,
        conversation_id: UUID | None,
        execution_id: UUID | None,
        attempt_id: UUID | None,
        cursor: str | None,
        limit: int,
    ) -> MessageWindow | None: ...

    async def read_artifact(self, domain: str, artifact_id: UUID, revision: int) -> ArtifactView | None: ...

    async def read_decision(self, domain: str, decision_id: UUID) -> DecisionView | None: ...

    async def lookup_delivery(self, domain: str, operation_id: UUID) -> DeliveryView | None: ...

    async def read_plan(self, domain: str, ref: ResourceRef) -> PlanView | None: ...

    async def read_resource(self, domain: str, ref: ResourceRef) -> JsonDoc | None: ...

    async def list_queue(self, domain: str, *, cursor: str | None, limit: int) -> Page[QueueItemView]: ...

    async def lookup_submission(self, domain: str, key: SubmissionKey) -> SubmissionReceipt: ...

    async def lookup_command(self, domain: str, command_id: UUID) -> CommandReceipt: ...

    async def lookup_mutation(self, domain: str, operation_id: UUID) -> MutationReceipt: ...

    async def accept_request(self, domain: str, request: AgentRequest, digest: str) -> SubmissionReceipt: ...

    async def save_command(self, domain: str, command: ControlCommand) -> CommandReceipt: ...

    async def apply_mutation(self, domain: str, mutation: Mutation) -> MutationReceipt: ...


def _problem(
    code: str,
    *,
    field_issues: tuple[tuple[str, str], ...] = (),
    expected_revision: int | None = None,
    actual_revision: int | None = None,
    related_id: UUID | None = None,
) -> Problem:
    issues = [{"field_path": path, "code": issue, "reason": "schema validation failed"} for path, issue in field_issues]
    params = json_doc(
        {
            "field_issues": issues,
            "expected_revision": expected_revision,
            "actual_revision": actual_revision,
        }
    )
    return Problem(code=code, message_key=f"agent.{code}", params=params, related_id=related_id)


def _invalid_request(error: AgentValidationError) -> AgentOperationError:
    return AgentOperationError(_problem("invalid_request", field_issues=((error.field_path, error.code),)))


class AgentService:
    """绑定当前可信 domain 的服务实例（§5.2）。"""

    def __init__(self, *, domain: str, dao: AgentDaoProtocol) -> None:
        self._domain = domain
        self._dao = dao

    # ------------------------------------------------------------------
    # 写入回执：参数先验证，再进 DAO；status 原样返回
    # ------------------------------------------------------------------

    async def submit(self, request: AgentRequest) -> SubmissionReceipt:
        if request.key.domain != self._domain:
            raise AgentOperationError(
                _problem("reference_conflict", field_issues=(("key.domain", "reference_conflict"),))
            )
        try:
            validate_request(request)
        except AgentValidationError as error:
            raise _invalid_request(error) from error
        return await self._call(lambda: self._dao.accept_request(self._domain, request, request_digest(request)))

    async def control(self, command: ControlCommand) -> CommandReceipt:
        return await self._call(lambda: self._dao.save_command(self._domain, command))

    async def mutate(self, mutation: Mutation) -> MutationReceipt:
        return await self._call(lambda: self._dao.apply_mutation(self._domain, mutation))

    async def lookup_submission(self, key: SubmissionKey) -> SubmissionReceipt:
        if key.domain != self._domain:
            raise AgentOperationError(
                _problem("reference_conflict", field_issues=(("key.domain", "reference_conflict"),))
            )
        return await self._call(lambda: self._dao.lookup_submission(self._domain, key))

    async def lookup_command(self, command_id: UUID) -> CommandReceipt:
        return await self._call(lambda: self._dao.lookup_command(self._domain, command_id))

    async def lookup_mutation(self, operation_id: UUID) -> MutationReceipt:
        return await self._call(lambda: self._dao.lookup_mutation(self._domain, operation_id))

    # ------------------------------------------------------------------
    # 无回执读取：成功原样返回；失败抛 AgentOperationError
    # ------------------------------------------------------------------

    async def get_execution(self, execution_id: UUID) -> ExecutionView:
        view = await self._read(lambda: self._dao.read_execution(self._domain, execution_id))
        if view is None:
            raise AgentOperationError(_problem("not_found", related_id=execution_id))
        if view.status == "retired":
            raise AgentOperationError(_problem("retired", related_id=execution_id))
        return view

    async def read_conversation(self, conversation_id: UUID) -> ConversationView:
        view = await self._read(lambda: self._dao.read_conversation(self._domain, conversation_id))
        if view is None:
            raise AgentOperationError(_problem("not_found", related_id=conversation_id))
        return view

    async def list_conversations(
        self, *, archived: bool | None, cursor: str | None, limit: int = 50
    ) -> Page[ConversationView]:
        if not 1 <= limit <= 200:
            raise AgentOperationError(_problem("invalid_request", field_issues=(("limit", "invalid_value"),)))
        return await self._read(
            lambda: self._dao.list_conversations(self._domain, archived=archived, cursor=cursor, limit=limit)
        )

    async def read_messages(
        self,
        *,
        conversation_id: UUID | None,
        execution_id: UUID | None,
        attempt_id: UUID | None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> MessageWindow:
        if conversation_id is None and execution_id is None:
            raise AgentOperationError(
                _problem("invalid_request", field_issues=(("conversation_id/execution_id", "missing_scope"),))
            )
        if not 1 <= limit <= 200:
            raise AgentOperationError(_problem("invalid_request", field_issues=(("limit", "invalid_value"),)))
        window = await self._read(
            lambda: self._dao.read_messages(
                self._domain,
                conversation_id=conversation_id,
                execution_id=execution_id,
                attempt_id=attempt_id,
                cursor=cursor,
                limit=limit,
            )
        )
        if window is None:
            raise AgentOperationError(_problem("not_found", related_id=conversation_id or execution_id))
        return window

    async def read_artifact(self, artifact_id: UUID, revision: int) -> ArtifactView:
        if revision < 1:
            raise AgentOperationError(_problem("invalid_request", field_issues=(("revision", "invalid_value"),)))
        view = await self._read(lambda: self._dao.read_artifact(self._domain, artifact_id, revision))
        if view is None:
            raise AgentOperationError(_problem("not_found", related_id=artifact_id))
        return view

    async def read_decision(self, decision_id: UUID) -> DecisionView:
        view = await self._read(lambda: self._dao.read_decision(self._domain, decision_id))
        if view is None:
            raise AgentOperationError(_problem("not_found", related_id=decision_id))
        return view

    async def lookup_delivery(self, operation_id: UUID) -> DeliveryView:
        view = await self._read(lambda: self._dao.lookup_delivery(self._domain, operation_id))
        if view is None:
            raise AgentOperationError(_problem("not_found", related_id=operation_id))
        return view

    async def read_plan(self, ref: ResourceRef) -> PlanView:
        view = await self._read(lambda: self._dao.read_plan(self._domain, ref))
        if view is None:
            raise AgentOperationError(_problem("not_found", related_id=ref.resource_id))
        return view

    async def read_resource(self, ref: ResourceRef) -> JsonDoc:
        doc = await self._read(lambda: self._dao.read_resource(self._domain, ref))
        if doc is None:
            raise AgentOperationError(_problem("not_found", related_id=ref.resource_id))
        return doc

    async def list_queue(self, *, cursor: str | None, limit: int = 50) -> Page[QueueItemView]:
        if not 1 <= limit <= 200:
            raise AgentOperationError(_problem("invalid_request", field_issues=(("limit", "invalid_value"),)))
        return await self._read(lambda: self._dao.list_queue(self._domain, cursor=cursor, limit=limit))

    async def close(self) -> None:
        return None

    # ------------------------------------------------------------------
    # 失败转译：取消/引擎销毁始终传播；其他 IO 失败脱敏为 Problem
    # ------------------------------------------------------------------

    async def _call[T](self, call: Callable[[], Awaitable[T]]) -> T:
        try:
            return await call()
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except AgentOperationError:
            raise
        except AgentValidationError as error:
            raise _invalid_request(error) from error
        except Exception as error:
            raise AgentOperationError(_problem(_READ_FAILED)) from error

    async def _read[T](self, call: Callable[[], Awaitable[T]]) -> T:
        return await self._call(call)
