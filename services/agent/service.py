"""Agent 服务公开读取/回执入口（设计正本 §5.2/§5.4）。

本模块只做服务边界：参数先验证、调用 DAO、失败转译成不可变 Problem。
提交/管理事务与执行编排由后续卡在同一服务上补齐，不另建第二入口。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

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
    decode_json_doc,
    json_doc,
    request_digest,
    validate_payload,
    validate_request,
)
from core.model_management_types import ConfigurationSaveOutcome
from data.persistence.daos.base_dao import EngineDisposedError
from services.agent.permissions import ExecutionPermission
from utils.thread_pool import TaskType, ThreadPoolManager

if TYPE_CHECKING:
    from services.agent.capabilities import SourceProvider
    from services.model_configuration_service import ModelConfigurationService

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

    async def lookup_source_operation(self, domain: str, operation_id: UUID) -> tuple[str, str] | None: ...

    async def save_source(
        self, domain: str, operation_id: UUID, input_digest: str, source: JsonDoc
    ) -> MutationReceipt: ...

    async def read_tool_call(self, effect_key: str) -> JsonDoc | None: ...


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


_MEDIA_BY_SUFFIX: dict[str, str] = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
_MEDIA_SUFFIX: dict[str, str] = {
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/csv": ".csv",
    "application/json": ".json",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}
_TEXT_MEDIA_TYPES = frozenset({"text/plain", "text/markdown", "text/csv", "application/json"})
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_MAX_FILE_BYTES = 20 * 1024 * 1024


def _empty_read_range(locator: str) -> dict[str, object]:
    return {"pages": [], "sheets": [], "row_start": None, "row_end": None, "locator": locator}


def _read_file_bytes(path: str) -> bytes:
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    if os.path.getsize(path) > _MAX_FILE_BYTES:
        raise ValueError("file_too_large")
    with open(path, "rb") as handle:
        return handle.read()


def _prepare_file_body(media_type: str, data: bytes) -> bytes:
    """文本解码/JSON 规范化；二进制保持原字节。"""
    if media_type not in _TEXT_MEDIA_TYPES:
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("invalid_text") from error
    if media_type == "application/json":
        decoded = decode_json_doc(text)
        canonical = json_doc(decoded)
        return canonical.encode("utf-8")
    return text.encode("utf-8")


def _write_blob(root: Path, relative: str, data: bytes) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, target)


def _source_input_digest(kind: str, value: object) -> str:
    return hashlib.sha256(json_doc({"kind": kind, "input": value}).encode("utf-8")).hexdigest()


def _file_media_type(path: str) -> str:
    media_type = _MEDIA_BY_SUFFIX.get(Path(path).suffix.lower())
    if media_type is None:
        raise AgentOperationError(_problem("source_unavailable"))
    return media_type


def _parse_metadata(metadata: JsonDoc) -> dict[str, object]:
    try:
        validate_payload("source_metadata", metadata)
        decoded = decode_json_doc(metadata)
    except AgentValidationError as error:
        raise _invalid_request(error) from error
    if not isinstance(decoded, dict):
        raise _invalid_request(AgentValidationError("invalid_type", "metadata"))
    return decoded


def _assert_pit(metadata: dict[str, object]) -> None:
    as_of = date.fromisoformat(cast(str, metadata["as_of"]))
    if as_of > datetime.now(UTC).date():
        raise AgentOperationError(_problem("pit_ineligible", field_issues=(("as_of", "pit_ineligible"),)))
    precision = metadata["time_precision"]
    if precision == "date" and metadata["published_date"] is not None:
        published = date.fromisoformat(cast(str, metadata["published_date"]))
    elif precision == "datetime" and metadata["published_at"] is not None:
        parsed = datetime.fromisoformat(cast(str, metadata["published_at"]).replace("Z", "+00:00"))
        published = parsed.astimezone(_SHANGHAI).date()
    else:
        return
    if published > as_of:
        raise AgentOperationError(_problem("pit_ineligible", field_issues=(("published", "pit_ineligible"),)))


def _build_source_payload(
    *,
    metadata: dict[str, object],
    origin: str,
    media_type: str,
    relative_path: str,
    content_digest: str,
    read_range: dict[str, object],
    extraction: str,
    limitations: list[str],
) -> JsonDoc:
    source = json_doc(
        {
            "schema_version": 1,
            "origin": origin,
            "media_type": media_type,
            "name": metadata["name"],
            "content_ref": {
                "kind": "managed_blob",
                "relative_path": relative_path,
                "digest": content_digest,
            },
            "published_at": metadata["published_at"],
            "published_date": metadata["published_date"],
            "time_precision": metadata["time_precision"],
            "period": metadata["period"],
            "effective_from": metadata["effective_from"],
            "effective_to": metadata["effective_to"],
            "as_of": metadata["as_of"],
            "units": metadata["units"],
            "read_range": read_range,
            "extraction": extraction,
            "limitations": limitations,
            "correction_of": metadata["correction_of"],
        }
    )
    validate_payload("source", source)
    return source


def _unknown_published_metadata(name: str, as_of: str) -> dict[str, object]:
    """工具响应未给发布时间：保留 unknown，不按完成时间推定。"""
    return {
        "schema_version": 1,
        "name": name,
        "as_of": as_of,
        "published_at": None,
        "published_date": None,
        "time_precision": "unknown",
        "period": None,
        "effective_from": None,
        "effective_to": None,
        "units": {},
        "correction_of": None,
    }


class AgentService:
    """绑定当前可信 domain 的服务实例（§5.2）。"""

    def __init__(
        self,
        *,
        domain: str,
        dao: AgentDaoProtocol,
        permission: ExecutionPermission | None = None,
        model_config_service: ModelConfigurationService | None = None,
        source_providers: Mapping[str, SourceProvider] | None = None,
        blob_root: Path | str | None = None,
    ) -> None:
        self._domain = domain
        self._dao = dao
        self._permission = permission or ExecutionPermission()
        # §9.7：授权只复用既有 ModelConfigurationService，不新建 grant 账本。
        self._model_config_service = model_config_service
        self._source_providers = dict(source_providers or {})
        self._blob_root = Path(blob_root) if blob_root is not None else None

    async def import_source(self, operation_id: UUID, path: str, metadata: JsonDoc) -> MutationReceipt:
        if not isinstance(operation_id, UUID):
            raise AgentOperationError(_problem("invalid_request", field_issues=(("operation_id", "invalid_uuid"),)))
        if not isinstance(path, str) or not path:
            raise AgentOperationError(_problem("invalid_request", field_issues=(("path", "invalid_type"),)))
        parsed = _parse_metadata(metadata)
        _assert_pit(parsed)
        input_digest = _source_input_digest("import_source", {"path": path, "metadata": parsed})
        replay = await self._call(lambda: self._dao.lookup_source_operation(self._domain, operation_id))
        if replay is not None:
            return await self._replay_source(replay, input_digest, operation_id)
        media_type = _file_media_type(path)
        data = await self._call(lambda: ThreadPoolManager().run_async(TaskType.IO, _read_file_bytes, path))
        body = await self._call(
            lambda: ThreadPoolManager().run_async(TaskType.IO, _prepare_file_body, media_type, data)
        )
        return await self._commit_source(
            operation_id,
            input_digest,
            metadata=parsed,
            origin="file",
            media_type=media_type,
            body=body,
            read_range=_empty_read_range(Path(path).name),
            extraction="complete",
            limitations=[],
        )

    async def register_source(self, operation_id: UUID, registration: JsonDoc) -> MutationReceipt:
        if not isinstance(operation_id, UUID):
            raise AgentOperationError(_problem("invalid_request", field_issues=(("operation_id", "invalid_uuid"),)))
        try:
            validate_payload("source_registration", registration)
            decoded = decode_json_doc(registration)
        except AgentValidationError as error:
            raise _invalid_request(error) from error
        if not isinstance(decoded, dict):
            raise AgentOperationError(_problem("invalid_request", field_issues=(("registration", "invalid_type"),)))
        input_digest = _source_input_digest("register_source", decoded)
        replay = await self._call(lambda: self._dao.lookup_source_operation(self._domain, operation_id))
        if replay is not None:
            return await self._replay_source(replay, input_digest, operation_id)
        kind = decoded["kind"]
        if kind == "business":
            return await self._register_business(operation_id, input_digest, decoded)
        if kind == "tool":
            return await self._register_tool(operation_id, input_digest, decoded)
        return await self._register_excerpt(operation_id, input_digest, decoded)

    async def _register_business(
        self, operation_id: UUID, input_digest: str, decoded: dict[str, object]
    ) -> MutationReceipt:
        as_of = date.fromisoformat(cast(str, decoded["as_of"]))
        if as_of > datetime.now(UTC).date():
            raise AgentOperationError(_problem("pit_ineligible", field_issues=(("as_of", "pit_ineligible"),)))
        provider = self._source_providers.get(cast(str, decoded["provider_key"]))
        if provider is None:
            raise AgentOperationError(_problem("source_unavailable"))
        try:
            prepared_doc = await provider.read_source(
                cast(str, decoded["source_object_key"]),
                cast(str, decoded["source_revision"]),
                as_of,
            )
        except asyncio.CancelledError:
            raise
        except AgentOperationError:
            raise
        except Exception as error:
            raise AgentOperationError(_problem("source_unavailable")) from error
        try:
            validate_payload("prepared_source", prepared_doc)
            prepared = decode_json_doc(prepared_doc)
        except AgentValidationError as error:
            raise _invalid_request(error) from error
        if not isinstance(prepared, dict):
            raise AgentOperationError(_problem("source_unavailable"))
        metadata = prepared["metadata"]
        if not isinstance(metadata, dict) or metadata.get("as_of") != decoded["as_of"]:
            raise AgentOperationError(_problem("source_unavailable"))
        _assert_pit(metadata)
        text = prepared["text"]
        media_type = prepared["media_type"]
        if not isinstance(text, str) or not text.strip() or media_type not in _TEXT_MEDIA_TYPES:
            raise AgentOperationError(_problem("source_unavailable"))
        body = text.encode("utf-8")
        if media_type == "application/json":
            body = json_doc(decode_json_doc(text)).encode("utf-8")
        read_range = prepared["read_range"]
        if not isinstance(read_range, dict):
            raise AgentOperationError(_problem("source_unavailable"))
        return await self._commit_source(
            operation_id,
            input_digest,
            metadata=metadata,
            origin="business",
            media_type=cast(str, media_type),
            body=body,
            read_range=cast(dict[str, object], read_range),
            extraction=cast(str, prepared["extraction"]),
            limitations=list(cast(list[str], prepared["limitations"])),
        )

    async def _register_tool(
        self, operation_id: UUID, input_digest: str, decoded: dict[str, object]
    ) -> MutationReceipt:
        target = decoded["target"]
        assert isinstance(target, dict)
        try:
            execution_id = UUID(cast(str, target["execution_id"]))
            task_id = UUID(cast(str, target["task_id"]))
            attempt_id = UUID(cast(str, target["attempt_id"]))
        except (TypeError, ValueError) as error:
            raise _invalid_request(AgentValidationError("invalid_uuid", "target")) from error
        execution = await self._call(lambda: self._dao.read_execution(self._domain, execution_id))
        matched = execution is not None and any(
            task.task_id == task_id and task.attempt_id == attempt_id for task in execution.tasks
        )
        if not matched:
            raise AgentOperationError(_problem("reference_conflict", related_id=execution_id))
        effect_key = cast(str, decoded["effect_key"])
        raw = await self._call(lambda: self._dao.read_tool_call(effect_key))
        if raw is None:
            raise AgentOperationError(_problem("source_unavailable"))
        tool = decode_json_doc(raw)
        if not isinstance(tool, dict):
            raise AgentOperationError(_problem("source_unavailable"))
        if (
            tool.get("state") != "committed"
            or tool.get("retired_at") is not None
            or str(tool.get("execution_id")) != str(execution_id)
            or str(tool.get("attempt_id")) != str(attempt_id)
            or tool.get("response") is None
        ):
            raise AgentOperationError(_problem("source_unavailable"))
        as_of = date.fromisoformat(cast(str, decoded["as_of"]))
        if as_of > datetime.now(UTC).date():
            raise AgentOperationError(_problem("pit_ineligible", field_issues=(("as_of", "pit_ineligible"),)))
        tool_ref = tool.get("tool_ref")
        name = "tool"
        if isinstance(tool_ref, dict) and isinstance(tool_ref.get("name"), str):
            name = cast(str, tool_ref["name"])
        metadata = _unknown_published_metadata(name, cast(str, decoded["as_of"]))
        body = json_doc(tool["response"]).encode("utf-8")
        return await self._commit_source(
            operation_id,
            input_digest,
            metadata=metadata,
            origin="tool",
            media_type="application/json",
            body=body,
            read_range=_empty_read_range(effect_key),
            extraction="complete",
            limitations=[],
        )

    async def _register_excerpt(
        self,
        operation_id: UUID,
        input_digest: str,
        decoded: dict[str, object],
    ) -> MutationReceipt:
        metadata = decoded["metadata"]
        assert isinstance(metadata, dict)
        _assert_pit(metadata)
        text = decoded["text"]
        if not isinstance(text, str) or not text.strip():
            raise AgentOperationError(_problem("source_unavailable"))
        return await self._commit_source(
            operation_id,
            input_digest,
            metadata=metadata,
            origin="user_excerpt",
            media_type="text/plain",
            body=text.encode("utf-8"),
            read_range=_empty_read_range("user_excerpt"),
            extraction="complete",
            limitations=[],
        )

    async def _commit_source(
        self,
        operation_id: UUID,
        input_digest: str,
        *,
        metadata: dict[str, object],
        origin: str,
        media_type: str,
        body: bytes,
        read_range: dict[str, object],
        extraction: str,
        limitations: list[str],
    ) -> MutationReceipt:
        root = self._blob_root
        if root is None:
            raise AgentOperationError(_problem("source_unavailable"))
        resource_id = uuid5(operation_id, "source")
        content_digest = hashlib.sha256(body).hexdigest()
        relative = f"agent/sources/{resource_id}/{content_digest}{_MEDIA_SUFFIX.get(media_type, '')}"
        await self._call(lambda: ThreadPoolManager().run_async(TaskType.IO, _write_blob, root, relative, body))
        source = _build_source_payload(
            metadata=metadata,
            origin=origin,
            media_type=media_type,
            relative_path=relative,
            content_digest=content_digest,
            read_range=read_range,
            extraction=extraction,
            limitations=limitations,
        )
        return await self._call(lambda: self._dao.save_source(self._domain, operation_id, input_digest, source))

    async def _replay_source(self, existing: tuple[str, str], input_digest: str, operation_id: UUID) -> MutationReceipt:
        if existing[0] not in ("source_import", "source_register") or existing[1] != input_digest:
            return MutationReceipt(
                operation_id=operation_id,
                status="conflict",
                resource_id=None,
                revision=None,
                problem=_problem("identity_conflict", related_id=operation_id),
            )
        return await self._call(lambda: self._dao.lookup_mutation(self._domain, operation_id))

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
        if not self._permission.allows(request.mode):
            raise AgentOperationError(
                _problem("runtime_not_admitted", field_issues=(("mode", "runtime_not_admitted"),))
            )
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

    # ------------------------------------------------------------------
    # 授权读 / 撤（§9.7）：复用既有 ModelConfigurationService，不建第二账本
    # ------------------------------------------------------------------

    def _model_config(self) -> ModelConfigurationService:
        if self._model_config_service is None:
            raise RuntimeError("AgentService 未注入 ModelConfigurationService，不能读取或撤销授权")
        return self._model_config_service

    async def read_authorizations(self, execution_id: UUID | None = None) -> JsonDoc:
        """新鲜读取安全 grant；读取失败原样传播，绝不降级为空授权列表。"""
        if execution_id is not None and not isinstance(execution_id, UUID):
            raise AgentOperationError(_problem("invalid_request", field_issues=(("execution_id", "invalid_uuid"),)))
        snapshot = await self._model_config().get_snapshot()
        return json_doc(
            {
                "config_revision": snapshot.config_revision,
                "security_revision": snapshot.security_revision,
                "scope_version": snapshot.security_scope_version,
                "grants": [
                    {
                        "grant_id": grant.get("grant_id"),
                        "connection_id": grant.get("connection_id"),
                        "target_fingerprint": grant.get("target_fingerprint"),
                        "purpose": grant.get("purpose"),
                        "scope_version": grant.get("scope_version"),
                        "revoked_at": grant.get("revoked_at"),
                        "data_scope_digest": grant.get("data_scope_digest"),
                    }
                    for grant in snapshot.security_grants
                ],
                # 决定-grant 关联由 N03 落地；此处不伪造已关联事实。
                "agent_scopes": [],
                "problem": None,
            }
        )

    async def revoke_authorization(
        self, operation_id: UUID, grant_id: str, expected_config_revision: int
    ) -> ConfigurationSaveOutcome:
        if not isinstance(operation_id, UUID):
            raise AgentOperationError(_problem("invalid_request", field_issues=(("operation_id", "invalid_uuid"),)))
        return await self._model_config().revoke_egress(grant_id, expected_config_revision, str(operation_id))

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
