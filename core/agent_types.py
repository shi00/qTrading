"""Agent 公共契约（设计正本 §5，CR2 闭合 schema）。

仅使用标准库：本模块定义 frozen/slots DTO、规范 JSON 编码/解码、请求摘要、
封闭 payload/成果/返回引用校验。业务单位、来源归属与 PIT 由 services 层核验，
core 不导入 data/strategies/UI。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
from dataclasses import dataclass, is_dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from typing import Any, Literal, NoReturn, cast

JsonDoc = str

OUTPUT_CONTRACTS: tuple[str, ...] = (
    "answer/1",
    "stock_analysis/1",
    "comparison/1",
    "candidates/1",
    "news_digest/1",
    "review/1",
    "dated_update/1",
    "backtest_explanation/1",
)

SOURCE_PROVIDER_KEYS: tuple[str, ...] = ("stock", "pool", "strategy", "backtest", "news")

ROLE_KEYS: tuple[str, ...] = ("main", "researcher", "summary", "stock", "tutorial")

DECISION_STATUSES = ("pending", "answered", "rejected", "expired", "invalidated")
ANSWER_PHASES = ("unanswered", "recorded", "delivered", "effective", "unknown", "invalidated")
AVAILABILITY = ("available", "partial", "revoked", "deleted", "unknown")

_DECIMAL_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?")
_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class AgentValidationError(ValueError):
    """纯校验失败（§5.5）。服务边界转成 §5.4 Problem。"""

    def __init__(self, code: str, field_path: str) -> None:
        super().__init__(f"{code}: {field_path}")
        self.code = code
        self.field_path = field_path


# ---------------------------------------------------------------------------
# DTO（§5.1）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SubmissionKey:
    domain: str
    source_module: str
    request_key: str


@dataclass(frozen=True, slots=True)
class ResourceRef:
    resource_id: uuid.UUID
    revision: int


@dataclass(frozen=True, slots=True)
class ModelChoice:
    kind: Literal["explicit", "scenario_default", "inherit"]
    connection_id: str | None
    scenario_id: str | None


@dataclass(frozen=True, slots=True)
class RoleBinding:
    role_key: str
    revision: int
    prompt: ResourceRef
    model: ModelChoice
    default_skills: tuple[ResourceRef, ...]
    default_mcp: tuple[ResourceRef, ...]
    enabled_skills: tuple[ResourceRef, ...]


@dataclass(frozen=True, slots=True)
class ContextBinding:
    path_id: uuid.UUID | None
    path_revision: int | None
    anchor_message_id: uuid.UUID | None
    predecessor_intents: tuple[uuid.UUID, ...]
    old_evidence: tuple[ResourceRef, ...]


@dataclass(frozen=True, slots=True)
class AgentRequest:
    key: SubmissionKey
    conversation_id: uuid.UUID | None
    intent_kind: Literal["ordinary", "historical_resend", "plan_start", "plan_revision", "business"]
    text: str
    mode: Literal["discuss", "plan", "execute"]
    as_of: date | None
    context: ContextBinding
    sources: tuple[ResourceRef, ...]
    roles: tuple[RoleBinding, ...]
    manual_skills: tuple[ResourceRef, ...]
    mcp: tuple[ResourceRef, ...]
    output_contract: str
    business_owner: str | None
    business_run: str | None
    business_slot: str | None
    reviewed_plan: ResourceRef | None = None
    plan_review_revision: int | None = None
    plan_review_digest: str | None = None
    as_of_basis: Literal["explicit", "source", "not_applicable"] = "explicit"


@dataclass(frozen=True, slots=True)
class Problem:
    code: str
    message_key: str
    params: JsonDoc
    related_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class SubmissionReceipt:
    status: Literal["accepted", "same_request", "conflict", "rejected", "commit_unknown", "retired"]
    key: SubmissionKey
    intent_id: uuid.UUID | None
    execution_id: uuid.UUID | None
    input_revision: int | None
    accepted_seq: int | None
    problem: Problem | None


@dataclass(frozen=True, slots=True)
class Target:
    execution_id: uuid.UUID
    task_id: uuid.UUID | None
    attempt_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class ControlCommand:
    command_id: uuid.UUID
    target: Target
    action: Literal[
        "interrupt",
        "continue",
        "terminate",
        "retry",
        "revise_queued",
        "withdraw_queued",
        "supplement",
        "answer_decision",
    ]
    expected_input_revision: int
    expected_plan_revision: int
    payload: JsonDoc


@dataclass(frozen=True, slots=True)
class CommandReceipt:
    command_id: uuid.UUID
    status: Literal["committed", "same_command", "conflict", "rejected", "commit_unknown"]
    native_request_id: str | None
    continuation_ref: JsonDoc | None
    problem: Problem | None


@dataclass(frozen=True, slots=True)
class Mutation:
    operation_id: uuid.UUID
    kind: Literal[
        "conversation",
        "draft",
        "branch",
        "role",
        "prompt",
        "prompt_restore",
        "skill",
        "skill_import",
        "mcp",
        "mcp_operation",
        "mcp_tools",
        "delete",
        "export",
        "cancel_delivery",
        "decision_presentation",
    ]
    resource_id: uuid.UUID | None
    expected_revision: int | None
    payload: JsonDoc


@dataclass(frozen=True, slots=True)
class MutationReceipt:
    operation_id: uuid.UUID
    status: Literal["committed", "same_operation", "conflict", "rejected", "commit_unknown"]
    resource_id: uuid.UUID | None
    revision: int | None
    problem: Problem | None


@dataclass(frozen=True, slots=True)
class ControlView:
    command_id: uuid.UUID
    target: Target
    action: Literal["interrupt", "continue", "terminate", "retry", "supplement"]
    phase: Literal["recorded", "delivered", "applying", "effective", "unknown", "rejected"]
    affected_tasks: tuple[uuid.UUID, ...]
    input_revision: int
    plan_revision: int
    effective_from_segment: int | None
    problem: Problem | None


@dataclass(frozen=True, slots=True)
class TaskView:
    task_id: uuid.UUID
    parent_task_id: uuid.UUID | None
    attempt_id: uuid.UUID
    role_key: str
    status: str
    state_revision: int
    depends_on: tuple[uuid.UUID, ...]
    problem: Problem | None
    plan_node_id: uuid.UUID | None = None
    control: ControlView | None = None


@dataclass(frozen=True, slots=True)
class ExecutionView:
    execution_id: uuid.UUID
    conversation_id: uuid.UUID | None
    input_revision: int
    plan_revision: int
    status: str
    fresh: bool
    observed_at: datetime
    tasks: tuple[TaskView, ...]
    native_ref: str | None
    continuation_ref: JsonDoc | None
    usage: JsonDoc
    problem: Problem | None
    source_module: str
    plan_ref: ResourceRef | None = None
    control: ControlView | None = None


@dataclass(frozen=True, slots=True)
class MessageView:
    message_id: uuid.UUID
    conversation_id: uuid.UUID | None
    execution_id: uuid.UUID | None
    attempt_id: uuid.UUID | None
    role: Literal["user", "assistant", "tool", "notice"]
    content: str
    revision: int
    created_at: datetime
    sources: tuple[ResourceRef, ...]


@dataclass(frozen=True, slots=True)
class MessageWindow:
    items: tuple[MessageView, ...]
    conversation_id: uuid.UUID | None
    execution_id: uuid.UUID | None
    attempt_id: uuid.UUID | None
    older_cursor: str | None
    newer_cursor: str | None
    read_revision: int
    anchor_message_id: uuid.UUID | None
    anchor_revision: int | None


@dataclass(frozen=True, slots=True)
class HistoryHit:
    message: MessageView
    excerpt: str
    conversation_title: str | None
    source_module: str
    role_key: str | None
    task_scope: Literal["main", "child", "unassigned"]
    task_id: uuid.UUID | None
    return_ref: JsonDoc


@dataclass(frozen=True, slots=True)
class HistoryQuery:
    text: str
    conversation_id: uuid.UUID | None
    execution_id: uuid.UUID | None
    attempt_id: uuid.UUID | None
    message_role: Literal["user", "assistant", "tool", "notice"] | None
    from_at: datetime | None
    until_at: datetime | None
    include_archived: bool
    statuses: tuple[str, ...]
    fields: Literal["title", "content", "both"]
    role_key: str | None = None
    task_scope: Literal["all", "main", "child"] = "all"
    source_module: str | None = None


@dataclass(frozen=True, slots=True)
class ConversationView:
    conversation_id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime
    revision: int
    pinned: bool
    archived: bool
    path_id: uuid.UUID
    path_revision: int
    active_execution_id: uuid.UUID | None
    pending_decision_count: int | None
    source_ref: JsonDoc | None


@dataclass(frozen=True, slots=True)
class DecisionQuery:
    conversation_id: uuid.UUID | None
    execution_id: uuid.UUID | None
    source_module: str | None
    statuses: tuple[Literal["pending", "answered", "rejected", "expired", "invalidated"], ...]
    cursor: str | None
    limit: int = 50


@dataclass(frozen=True, slots=True)
class DecisionView:
    decision_id: uuid.UUID
    target: Target
    revision: int
    native_request_id: str
    continuation_ref: JsonDoc | None
    question_key: str
    question_params: JsonDoc
    options: JsonDoc
    answer_schema: JsonDoc
    scope_digest: str
    expires_at: datetime | None
    status: Literal["pending", "answered", "rejected", "expired", "invalidated"]
    answer_command_id: uuid.UUID | None
    answer: JsonDoc | None
    answer_phase: Literal["unanswered", "recorded", "delivered", "effective", "unknown", "invalidated"]
    answer_problem: Problem | None


@dataclass(frozen=True, slots=True)
class ArtifactView:
    artifact_id: uuid.UUID
    revision: int
    output_contract: str
    content: JsonDoc
    markdown: str
    content_digest: str
    source_availability: JsonDoc


@dataclass(frozen=True, slots=True)
class DeliveryView:
    schema_version: Literal[1]
    operation_id: uuid.UUID
    revision: int
    artifact_id: uuid.UUID
    artifact_revision: int
    format: Literal["md", "csv", "xlsx"]
    destination: str
    expected_target_digest: str | None
    confirm_overwrite: bool
    content_digest: str
    source_availability: JsonDoc | None
    delivery_digest: str | None
    status: Literal["preparing", "published", "failed", "cancelled", "unknown"]
    phase: Literal["preparing", "writing", "publishing", "settled"]
    cancel_requested: bool
    problem: Problem | None


@dataclass(frozen=True, slots=True)
class PublicEvent:
    seq: int
    event_key: str
    entity_id: uuid.UUID
    entity_revision: int
    kind: str
    payload: JsonDoc


@dataclass(frozen=True, slots=True)
class PlanView:
    ref: ResourceRef
    conversation_id: uuid.UUID | None
    planning_execution_id: uuid.UUID
    source_input_revision: int
    source_manifest_digest: str
    review_revision: int
    review_digest: str
    review_status: Literal["ready", "revision_pending", "superseded", "verifying"]
    plan: JsonDoc
    input_snapshot: JsonDoc
    started_execution_ids: tuple[uuid.UUID, ...]


@dataclass(frozen=True, slots=True)
class DeleteTarget:
    kind: Literal["resource", "conversation"]
    object_id: uuid.UUID
    revision: int


@dataclass(frozen=True, slots=True)
class WaitResult:
    status: Literal["observed", "terminal", "timed_out", "unavailable"]
    execution_id: uuid.UUID
    view: ExecutionView | None
    artifact: ArtifactView | None
    problem: Problem | None
    plan: PlanView | None = None


@dataclass(frozen=True, slots=True)
class Page[T]:
    items: tuple[T, ...]
    next_cursor: str | None
    read_revision: int


@dataclass(frozen=True, slots=True)
class QueueItemView:
    intent_id: uuid.UUID
    execution_id: uuid.UUID | None
    conversation_id: uuid.UUID | None
    input_revision: int
    accepted_seq: int
    admission_seq: int
    acceptance_state: Literal["queued", "dispatched", "withdrawn", "retired"]
    text_excerpt: str
    problem: Problem | None


class AgentOperationError(Exception):
    """无回执读取方法的明确失败（§5.9）。"""

    def __init__(self, problem: Problem) -> None:
        super().__init__(problem.code)
        self.problem = problem


# ---------------------------------------------------------------------------
# 规范编码（§5.5）
# ---------------------------------------------------------------------------


def _fail(code: str, field_path: str) -> NoReturn:
    raise AgentValidationError(code, field_path)


def _decimal_text(value: Decimal) -> str:
    if value.is_nan() or value.is_infinite():
        _fail("invalid_json", "$")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("-0", ""):
        text = "0"
    if len(text) > 128:
        _fail("invalid_json", "$")
    return text


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        _fail("invalid_json", "$")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _to_jsonable(value: object, field_path: str) -> object:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail("invalid_json", field_path)
        return value
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, datetime):
        return _utc_text(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field: _to_jsonable(getattr(value, field), f"{field_path}.{field}")
            for field in value.__dataclass_fields__  # pyright: ignore[reportAttributeAccessIssue]  # 运行期为 dataclass 实例，字段名由标准库提供
        }
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("invalid_json", field_path)
            result[key] = _to_jsonable(item, f"{field_path}.{key}")
        return result
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item, f"{field_path}[{index}]") for index, item in enumerate(value)]
    _fail("invalid_json", field_path)
    return None  # pragma: no cover - _fail 总是抛出


def json_doc(value: object) -> JsonDoc:
    """规范 JSON：UTF-8 语义、键排序、无空白、禁 NaN/Infinity。"""

    encoded = _to_jsonable(value, "$")
    return json.dumps(encoded, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def decode_json_doc(doc: JsonDoc) -> object:
    """严格解析：拒绝重复键与非有限数；未知字段由封闭 schema 拒绝。"""

    if not isinstance(doc, str):
        _fail("invalid_json", "$")
    duplicate = object()

    def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        seen: dict[str, object] = {}
        for key, value in pairs:
            if key in seen:
                raise AgentValidationError("duplicate_key", f"$.{key}")
            seen[key] = value
        return seen

    def _constant(name: str) -> object:
        raise AgentValidationError("non_finite_number", f"$.{name}")

    try:
        return json.loads(
            doc,
            object_pairs_hook=_pairs,
            parse_constant=_constant,
            parse_float=Decimal,
        )
    except AgentValidationError:
        raise
    except (json.JSONDecodeError, ValueError, TypeError):
        _fail("invalid_json", "$")
    return duplicate  # pragma: no cover - _fail 总是抛出


def request_digest(request: AgentRequest) -> str:
    return hashlib.sha256(json_doc(request).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 校验基元
# ---------------------------------------------------------------------------


def _obj(value: object, field_path: str) -> dict[str, object]:
    if not isinstance(value, dict):
        _fail("invalid_type", field_path)
    return value


def _keys(obj: dict[str, object], required: set[str], field_path: str) -> None:
    missing = sorted(required - obj.keys())
    if missing:
        _fail("missing_field", f"{field_path}.{missing[0]}")
    extra = sorted(obj.keys() - required)
    if extra:
        _fail("unknown_field", f"{field_path}.{extra[0]}")


def _text_value(value: object, field_path: str, *, min_len: int = 1, max_len: int | None = None) -> str:
    if not isinstance(value, str) or len(value) < min_len:
        _fail("invalid_type", field_path)
    if max_len is not None and len(value) > max_len:
        _fail("invalid_length", field_path)
    return value


def _text(obj: dict[str, object], key: str, field_path: str, *, min_len: int = 1, max_len: int | None = None) -> str:
    return _text_value(obj[key], f"{field_path}.{key}", min_len=min_len, max_len=max_len)


def _nullable_text(obj: dict[str, object], key: str, field_path: str, *, max_len: int | None = None) -> str | None:
    value = obj[key]
    if value is None:
        return None
    return _text_value(value, f"{field_path}.{key}", max_len=max_len)


def _bool(obj: dict[str, object], key: str, field_path: str) -> bool:
    value = obj[key]
    if not isinstance(value, bool):
        _fail("invalid_type", f"{field_path}.{key}")
    return value


def _int_value(value: object, field_path: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("invalid_type", field_path)
    if minimum is not None and value < minimum:
        _fail("invalid_range", field_path)
    if maximum is not None and value > maximum:
        _fail("invalid_range", field_path)
    return value


def _int(
    obj: dict[str, object], key: str, field_path: str, *, minimum: int | None = None, maximum: int | None = None
) -> int:
    return _int_value(obj[key], f"{field_path}.{key}", minimum=minimum, maximum=maximum)


def _nullable_int(obj: dict[str, object], key: str, field_path: str, *, minimum: int | None = None) -> int | None:
    value = obj[key]
    if value is None:
        return None
    return _int_value(value, f"{field_path}.{key}", minimum=minimum)


def _number_value(value: object, field_path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        _fail("invalid_type", field_path)
    number = float(value)
    if not math.isfinite(number):
        _fail("non_finite_number", field_path)
    return number


def _nullable_number(
    obj: dict[str, object], key: str, field_path: str, *, minimum: float | None = None, maximum: float | None = None
) -> float | None:
    value = obj[key]
    if value is None:
        return None
    number = _number_value(value, f"{field_path}.{key}")
    if minimum is not None and number < minimum:
        _fail("invalid_range", f"{field_path}.{key}")
    if maximum is not None and number > maximum:
        _fail("invalid_range", f"{field_path}.{key}")
    return number


def _decimal_value(value: object, field_path: str) -> Decimal:
    if not isinstance(value, str) or not _DECIMAL_RE.fullmatch(value) or len(value) > 128:
        _fail("invalid_decimal", field_path)
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        _fail("invalid_decimal", field_path)
    if not parsed.is_finite():
        _fail("invalid_decimal", field_path)
    return parsed


def _decimal(obj: dict[str, object], key: str, field_path: str) -> Decimal:
    return _decimal_value(obj[key], f"{field_path}.{key}")


def _nullable_decimal(obj: dict[str, object], key: str, field_path: str) -> Decimal | None:
    value = obj[key]
    if value is None:
        return None
    return _decimal_value(value, f"{field_path}.{key}")


def _uuid_value(value: object, field_path: str) -> uuid.UUID:
    if not isinstance(value, str):
        _fail("invalid_uuid", field_path)
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        _fail("invalid_uuid", field_path)
    if str(parsed) != value:
        _fail("invalid_uuid", field_path)
    return parsed


def _uuid(obj: dict[str, object], key: str, field_path: str) -> uuid.UUID:
    return _uuid_value(obj[key], f"{field_path}.{key}")


def _nullable_uuid(obj: dict[str, object], key: str, field_path: str) -> uuid.UUID | None:
    value = obj[key]
    if value is None:
        return None
    return _uuid_value(value, f"{field_path}.{key}")


def _date_value(value: object, field_path: str) -> date:
    if not isinstance(value, str):
        _fail("invalid_date", field_path)
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        _fail("invalid_date", field_path)
    if parsed.isoformat() != value:
        _fail("invalid_date", field_path)
    return parsed


def _date(obj: dict[str, object], key: str, field_path: str) -> date:
    return _date_value(obj[key], f"{field_path}.{key}")


def _nullable_date(obj: dict[str, object], key: str, field_path: str) -> date | None:
    value = obj[key]
    if value is None:
        return None
    return _date_value(value, f"{field_path}.{key}")


def _utc_value(value: object, field_path: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        _fail("invalid_time", field_path)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail("invalid_time", field_path)
    if parsed.tzinfo is None or parsed.microsecond % 1000 != 0 or parsed.microsecond != int(parsed.microsecond):
        _fail("invalid_time", field_path)
    if _utc_text(parsed) != value:
        _fail("invalid_time", field_path)
    return parsed


def _nullable_utc(obj: dict[str, object], key: str, field_path: str) -> datetime | None:
    value = obj[key]
    if value is None:
        return None
    return _utc_value(value, f"{field_path}.{key}")


def _digest_value(value: object, field_path: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        _fail("invalid_digest", field_path)
    return value


def _digest(obj: dict[str, object], key: str, field_path: str) -> str:
    return _digest_value(obj[key], f"{field_path}.{key}")


def _nullable_digest(obj: dict[str, object], key: str, field_path: str) -> str | None:
    value = obj[key]
    if value is None:
        return None
    return _digest_value(value, f"{field_path}.{key}")


def _enum(value: object, allowed: tuple[str, ...], field_path: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        _fail("invalid_enum", field_path)
    return value


def _array(obj: dict[str, object], key: str, field_path: str) -> list[object]:
    value = obj[key]
    if not isinstance(value, list):
        _fail("invalid_type", f"{field_path}.{key}")
    return value


def _nullable_array(obj: dict[str, object], key: str, field_path: str) -> list[object] | None:
    value = obj[key]
    if value is None:
        return None
    if not isinstance(value, list):
        _fail("invalid_type", f"{field_path}.{key}")
    return value


def _text_array(obj: dict[str, object], key: str, field_path: str, *, allow_empty: bool = True) -> list[str]:
    values = _array(obj, key, field_path)
    if not allow_empty and not values:
        _fail("empty_array", f"{field_path}.{key}")
    result: list[str] = []
    for index, item in enumerate(values):
        result.append(_text_value(item, f"{field_path}.{key}[{index}]"))
    return result


def _nullable_text_array(obj: dict[str, object], key: str, field_path: str) -> list[str] | None:
    value = obj[key]
    if value is None:
        return None
    return _text_array(obj, key, field_path)


def _scalar_value(value: object, field_path: str) -> object:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        _number_value(value, field_path)
        return value
    if isinstance(value, str):
        return value
    _fail("invalid_scalar", field_path)
    return None


def _ref_value(value: object, field_path: str) -> dict[str, object]:
    ref = _obj(value, field_path)
    _keys(ref, {"resource_id", "revision"}, field_path)
    _uuid(ref, "resource_id", field_path)
    _int(ref, "revision", field_path, minimum=1)
    return ref


def _ref(obj: dict[str, object], key: str, field_path: str) -> dict[str, object]:
    return _ref_value(obj[key], f"{field_path}.{key}")


def _ref_array(
    obj: dict[str, object], key: str, field_path: str, *, allow_empty: bool = True
) -> list[dict[str, object]]:
    values = _array(obj, key, field_path)
    if not allow_empty and not values:
        _fail("empty_array", f"{field_path}.{key}")
    return [_ref_value(item, f"{field_path}.{key}[{index}]") for index, item in enumerate(values)]


def _message_ref(value: object, field_path: str) -> dict[str, object]:
    ref = _obj(value, field_path)
    _keys(ref, {"message_id", "revision"}, field_path)
    _uuid(ref, "message_id", field_path)
    _int(ref, "revision", field_path, minimum=1)
    return ref


def _artifact_ref(value: object, field_path: str) -> dict[str, object]:
    ref = _obj(value, field_path)
    _keys(ref, {"artifact_id", "revision"}, field_path)
    _uuid(ref, "artifact_id", field_path)
    _int(ref, "revision", field_path, minimum=1)
    return ref


def _problem_value(value: object, field_path: str, *, read_problem: bool = False) -> dict[str, object]:
    problem = _obj(value, field_path)
    _keys(problem, {"code", "message_key", "params", "related_id"}, field_path)
    _text(problem, "code", field_path)
    _text(problem, "message_key", field_path)
    params = problem["params"]
    if not isinstance(params, str):
        _fail("invalid_type", f"{field_path}.params")
    params_obj = _obj(decode_json_doc(params), f"{field_path}.params")
    params = params_obj
    if read_problem:
        _keys(params, {"field_issues", "expected_revision", "actual_revision"}, f"{field_path}.params")
        issues = _array(params, "field_issues", f"{field_path}.params")
        for index, issue in enumerate(issues):
            path = f"{field_path}.params.field_issues[{index}]"
            item = _obj(issue, path)
            _keys(item, {"field_path", "code", "reason"}, path)
            _text(item, "field_path", path)
            _text(item, "code", path)
            _text(item, "reason", path)
        _nullable_int(params, "expected_revision", f"{field_path}.params", minimum=1)
        _nullable_int(params, "actual_revision", f"{field_path}.params", minimum=1)
    _nullable_uuid(problem, "related_id", field_path)
    return problem


def _nullable_problem(obj: dict[str, object], key: str, field_path: str) -> dict[str, object] | None:
    value = obj[key]
    if value is None:
        return None
    return _problem_value(value, f"{field_path}.{key}")


# ---------------------------------------------------------------------------
# 资源与来源 schema（§9）
# ---------------------------------------------------------------------------


def _unit_basis(value: object, field_path: str) -> dict[str, object]:
    item = _obj(value, field_path)
    _keys(item, {"unit", "basis"}, field_path)
    _text(item, "unit", field_path)
    _text(item, "basis", field_path)
    return item


def _units(obj: dict[str, object], key: str, field_path: str) -> None:
    values = _obj(obj[key], f"{field_path}.{key}")
    for field_name, basis in values.items():
        _text_value(field_name, f"{field_path}.{key}.{field_name}")
        _unit_basis(basis, f"{field_path}.{key}.{field_name}")


def _read_range(value: object, field_path: str) -> dict[str, object]:
    read_range = _obj(value, field_path)
    _keys(read_range, {"pages", "sheets", "row_start", "row_end", "locator"}, field_path)
    for index, page in enumerate(_array(read_range, "pages", field_path)):
        _int_value(page, f"{field_path}.pages[{index}]", minimum=1)
    _text_array(read_range, "sheets", field_path)
    start = _nullable_int(read_range, "row_start", field_path, minimum=1)
    end = _nullable_int(read_range, "row_end", field_path, minimum=1)
    if start is not None and end is not None and start > end:
        _fail("invalid_range", f"{field_path}.row_start")
    _text(read_range, "locator", field_path)
    return read_range


def _content_ref(value: object, field_path: str) -> dict[str, object]:
    ref = _obj(value, field_path)
    kind = ref.get("kind")
    if kind == "managed_blob":
        _keys(ref, {"kind", "relative_path", "digest"}, field_path)
        relative = _text(ref, "relative_path", field_path)
        if os.path.isabs(relative) or ".." in relative.replace("\\", "/").split("/"):
            _fail("invalid_path", f"{field_path}.relative_path")
        _digest(ref, "digest", field_path)
        return ref
    if kind == "resource":
        _keys(ref, {"kind", "ref"}, field_path)
        _ref(ref, "ref", field_path)
        return ref
    _fail("invalid_enum", f"{field_path}.kind")
    return ref


def _nullable_content_ref(obj: dict[str, object], key: str, field_path: str) -> None:
    value = obj[key]
    if value is not None:
        _content_ref(value, f"{field_path}.{key}")


def _time_precision_joint(obj: dict[str, object], field_path: str) -> str:
    precision = _enum(obj["time_precision"], ("datetime", "date", "unknown"), f"{field_path}.time_precision")
    published_at = obj["published_at"]
    published_date = obj["published_date"]
    if precision == "datetime":
        if published_at is None:
            _fail("missing_field", f"{field_path}.published_at")
        _utc_value(published_at, f"{field_path}.published_at")
        if published_date is None:
            _fail("missing_field", f"{field_path}.published_date")
        _date_value(published_date, f"{field_path}.published_date")
    elif precision == "date":
        if published_at is not None:
            _fail("unexpected_field", f"{field_path}.published_at")
        if published_date is None:
            _fail("missing_field", f"{field_path}.published_date")
        _date_value(published_date, f"{field_path}.published_date")
    else:
        if published_at is not None or published_date is not None:
            _fail("unexpected_field", f"{field_path}.published_at")
    return precision


def _source_metadata_obj(value: object, field_path: str) -> dict[str, object]:
    metadata = _obj(value, field_path)
    _keys(
        metadata,
        {
            "schema_version",
            "name",
            "as_of",
            "published_at",
            "published_date",
            "time_precision",
            "period",
            "effective_from",
            "effective_to",
            "units",
            "correction_of",
        },
        field_path,
    )
    _int(metadata, "schema_version", field_path, minimum=1, maximum=1)
    _text(metadata, "name", field_path, max_len=200)
    _date(metadata, "as_of", field_path)
    _time_precision_joint(metadata, field_path)
    _nullable_text(metadata, "period", field_path)
    start = _nullable_date(metadata, "effective_from", field_path)
    end = _nullable_date(metadata, "effective_to", field_path)
    if start is not None and end is not None and start > end:
        _fail("invalid_range", f"{field_path}.effective_from")
    _units(metadata, "units", field_path)
    if metadata["correction_of"] is not None:
        _ref(metadata, "correction_of", field_path)
    return metadata


def _target_obj(value: object, field_path: str) -> dict[str, object]:
    target = _obj(value, field_path)
    _keys(target, {"execution_id", "task_id", "attempt_id"}, field_path)
    _uuid(target, "execution_id", field_path)
    _nullable_uuid(target, "task_id", field_path)
    _nullable_uuid(target, "attempt_id", field_path)
    return target


def _source_registration_obj(doc: object, field_path: str = "$") -> dict[str, object]:
    registration = _obj(doc, field_path)
    if "kind" not in registration:
        _fail("missing_field", f"{field_path}.kind")
    kind = registration["kind"]
    if kind == "business":
        _keys(
            registration,
            {"schema_version", "kind", "provider_key", "source_object_key", "source_revision", "as_of"},
            field_path,
        )
        _int(registration, "schema_version", field_path, minimum=1, maximum=1)
        _enum(registration["provider_key"], SOURCE_PROVIDER_KEYS, f"{field_path}.provider_key")
        _text(registration, "source_object_key", field_path)
        _text(registration, "source_revision", field_path)
        _date(registration, "as_of", field_path)
    elif kind == "tool":
        _keys(registration, {"schema_version", "kind", "target", "effect_key", "as_of"}, field_path)
        _int(registration, "schema_version", field_path, minimum=1, maximum=1)
        target = _target_obj(registration["target"], f"{field_path}.target")
        if target["task_id"] is None or target["attempt_id"] is None:
            _fail("missing_field", f"{field_path}.target.task_id")
        _text(registration, "effect_key", field_path)
        _date(registration, "as_of", field_path)
    elif kind == "user_excerpt":
        _keys(registration, {"schema_version", "kind", "metadata", "text"}, field_path)
        _int(registration, "schema_version", field_path, minimum=1, maximum=1)
        _source_metadata_obj(registration["metadata"], f"{field_path}.metadata")
        _text(registration, "text", field_path)
    else:
        _fail("invalid_enum", f"{field_path}.kind")
    return registration


def _prepared_source_obj(doc: object, field_path: str = "$") -> dict[str, object]:
    prepared = _obj(doc, field_path)
    _keys(
        prepared,
        {"schema_version", "metadata", "media_type", "text", "read_range", "extraction", "limitations"},
        field_path,
    )
    _int(prepared, "schema_version", field_path, minimum=1, maximum=1)
    _source_metadata_obj(prepared["metadata"], f"{field_path}.metadata")
    media_type = _text(prepared, "media_type", field_path)
    text = _text(prepared, "text", field_path)
    if media_type == "application/json":
        decoded = decode_json_doc(text)
        if json_doc(decoded) != text:
            _fail("non_canonical_json", f"{field_path}.text")
    _read_range(prepared["read_range"], f"{field_path}.read_range")
    extraction = _enum(prepared["extraction"], ("complete", "partial"), f"{field_path}.extraction")
    limitations = _text_array(prepared, "limitations", field_path, allow_empty=True)
    if extraction == "partial" and not limitations:
        _fail("missing_field", f"{field_path}.limitations")
    meta = prepared["metadata"]
    if isinstance(meta, dict):
        as_of = _date_value(meta["as_of"], f"{field_path}.metadata.as_of")
        if as_of > datetime.now(UTC).date():
            _fail("pit_ineligible", f"{field_path}.metadata.as_of")
    return prepared


def _resource_payload_obj(kind: str, doc: object, field_path: str = "$") -> dict[str, object]:
    payload = _obj(doc, field_path)
    if kind == "source":
        _keys(
            payload,
            {
                "schema_version",
                "origin",
                "media_type",
                "name",
                "content_ref",
                "published_at",
                "published_date",
                "time_precision",
                "period",
                "effective_from",
                "effective_to",
                "as_of",
                "units",
                "read_range",
                "extraction",
                "limitations",
                "correction_of",
            },
            field_path,
        )
        _int(payload, "schema_version", field_path, minimum=1, maximum=1)
        _enum(payload["origin"], ("business", "file", "tool", "user_excerpt"), f"{field_path}.origin")
        _text(payload, "media_type", field_path)
        _text(payload, "name", field_path, max_len=200)
        _time_precision_joint(payload, field_path)
        extraction = _enum(payload["extraction"], ("complete", "partial", "failed"), f"{field_path}.extraction")
        limitations = _text_array(payload, "limitations", field_path)
        _nullable_content_ref(payload, "content_ref", field_path)
        if extraction == "failed" and payload["content_ref"] is not None:
            _fail("unexpected_field", f"{field_path}.content_ref")
        if extraction != "complete" and not limitations:
            _fail("missing_field", f"{field_path}.limitations")
        _nullable_text(payload, "period", field_path)
        start = _nullable_date(payload, "effective_from", field_path)
        end = _nullable_date(payload, "effective_to", field_path)
        if start is not None and end is not None and start > end:
            _fail("invalid_range", f"{field_path}.effective_from")
        _date(payload, "as_of", field_path)
        _units(payload, "units", field_path)
        _read_range(payload["read_range"], f"{field_path}.read_range")
        if payload["correction_of"] is not None:
            _ref(payload, "correction_of", field_path)
        return payload

    if kind == "role":
        _keys(
            payload,
            {
                "schema_version",
                "role_key",
                "description",
                "prompt_ref",
                "model_choice",
                "enabled_skills",
                "default_skills",
                "default_mcp",
                "enabled",
            },
            field_path,
        )
        _int(payload, "schema_version", field_path, minimum=1, maximum=1)
        _text(payload, "role_key", field_path, max_len=128)
        _text(payload, "description", field_path)
        _ref(payload, "prompt_ref", field_path)
        _model_choice_obj(payload["model_choice"], f"{field_path}.model_choice", child=True)
        enabled = _ref_array(payload, "enabled_skills", field_path)
        defaults = _ref_array(payload, "default_skills", field_path)
        _ref_array(payload, "default_mcp", field_path)
        _bool(payload, "enabled", field_path)
        _subset_refs(defaults, enabled, f"{field_path}.default_skills")
        return payload

    if kind == "prompt":
        _keys(
            payload,
            {"schema_version", "body", "origin", "asset_version", "allowed_variables", "scenario_key"},
            field_path,
        )
        _int(payload, "schema_version", field_path, minimum=1, maximum=1)
        _text(payload, "body", field_path)
        origin = _enum(payload["origin"], ("default", "custom", "legacy_unknown"), f"{field_path}.origin")
        version = _nullable_text(payload, "asset_version", field_path)
        if origin == "default" and version is None:
            _fail("missing_field", f"{field_path}.asset_version")
        _text_array(payload, "allowed_variables", field_path)
        _text(payload, "scenario_key", field_path, max_len=128)
        return payload

    if kind == "skill":
        _keys(
            payload,
            {
                "schema_version",
                "name",
                "description",
                "instructions",
                "resource_refs",
                "compatible_roles",
                "required_capabilities",
                "import_origin",
                "license",
                "dependency_report",
                "enabled",
            },
            field_path,
        )
        _int(payload, "schema_version", field_path, minimum=1, maximum=1)
        _text(payload, "name", field_path, max_len=200)
        _text(payload, "description", field_path)
        _text(payload, "instructions", field_path)
        _ref_array(payload, "resource_refs", field_path)
        _text_array(payload, "compatible_roles", field_path)
        _text_array(payload, "required_capabilities", field_path)
        _import_origin(payload["import_origin"], f"{field_path}.import_origin")
        _declared_license(payload["license"], f"{field_path}.license")
        for index, dependency in enumerate(_array(payload, "dependency_report", field_path)):
            _dependency(dependency, f"{field_path}.dependency_report[{index}]")
        _bool(payload, "enabled", field_path)
        return payload

    if kind == "mcp":
        return _mcp_payload(payload, field_path)

    if kind == "summary":
        _keys(
            payload,
            {
                "schema_version",
                "source_messages",
                "source_manifest_digest",
                "as_of",
                "text",
                "preserved_constraints",
                "model_invocation_ids",
            },
            field_path,
        )
        _int(payload, "schema_version", field_path, minimum=1, maximum=1)
        for index, item in enumerate(_array(payload, "source_messages", field_path)):
            _message_ref(item, f"{field_path}.source_messages[{index}]")
        _digest(payload, "source_manifest_digest", field_path)
        _nullable_date(payload, "as_of", field_path)
        _text(payload, "text", field_path)
        _text_array(payload, "preserved_constraints", field_path)
        _text_array(payload, "model_invocation_ids", field_path)
        return payload

    if kind == "artifact":
        _keys(payload, {"schema_version", "artifact_id", "revision"}, field_path)
        _int(payload, "schema_version", field_path, minimum=1, maximum=1)
        _uuid(payload, "artifact_id", field_path)
        _int(payload, "revision", field_path, minimum=1)
        return payload

    _fail("unsupported_schema", field_path)
    return payload


def _model_choice_obj(value: object, field_path: str, *, child: bool) -> dict[str, object]:
    choice = _obj(value, field_path)
    _keys(choice, {"kind", "connection_id", "scenario_id"}, field_path)
    kind = _enum(choice["kind"], ("explicit", "scenario_default", "inherit"), f"{field_path}.kind")
    connection_id = choice["connection_id"]
    scenario_id = choice["scenario_id"]
    if kind == "explicit":
        if not isinstance(connection_id, str) or not connection_id or scenario_id is not None:
            _fail("invalid_model_choice", field_path)
    elif kind == "scenario_default":
        if not isinstance(scenario_id, str) or not scenario_id or connection_id is not None:
            _fail("invalid_model_choice", field_path)
    else:
        if connection_id is not None or scenario_id is not None or not child:
            _fail("invalid_model_choice", field_path)
    return choice


def _import_origin(value: object, field_path: str) -> None:
    origin = _obj(value, field_path)
    _keys(origin, {"kind", "display_name", "content_digest"}, field_path)
    _enum(origin["kind"], ("directory", "zip"), f"{field_path}.kind")
    _text(origin, "display_name", field_path)
    _digest(origin, "content_digest", field_path)


def _declared_license(value: object, field_path: str) -> None:
    license_record = _obj(value, field_path)
    _keys(license_record, {"status", "identifier", "text_ref"}, field_path)
    status = _enum(license_record["status"], ("declared", "unknown"), f"{field_path}.status")
    _nullable_text(license_record, "identifier", field_path)
    if license_record["text_ref"] is not None:
        _ref(license_record, "text_ref", field_path)
    if status == "declared" and license_record["identifier"] is None:
        _fail("missing_field", f"{field_path}.identifier")


def _dependency(value: object, field_path: str) -> None:
    dependency = _obj(value, field_path)
    _keys(dependency, {"name", "kind", "constraint", "status", "reason"}, field_path)
    _text(dependency, "name", field_path)
    _enum(dependency["kind"], ("package", "capability"), f"{field_path}.kind")
    _nullable_text(dependency, "constraint", field_path)
    status = _enum(dependency["status"], ("satisfied", "missing", "unsupported", "unknown"), f"{field_path}.status")
    _nullable_text(dependency, "reason", field_path)
    if status != "satisfied" and dependency["reason"] is None:
        _fail("missing_field", f"{field_path}.reason")


def _secure_ref(value: object, field_path: str) -> None:
    ref = _obj(value, field_path)
    _keys(ref, {"kind", "name"}, field_path)
    _enum(ref["kind"], ("keyring", "environment"), f"{field_path}.kind")
    _text(ref, "name", field_path)


def _mcp_auth(value: object, field_path: str) -> None:
    auth = _obj(value, field_path)
    _keys(auth, {"kind", "refs"}, field_path)
    kind = _enum(auth["kind"], ("none", "bearer", "headers"), f"{field_path}.kind")
    refs = _obj(auth["refs"], f"{field_path}.refs")
    for header, secure in refs.items():
        _text_value(header, f"{field_path}.refs.{header}")
        _secure_ref(secure, f"{field_path}.refs.{header}")
    if kind == "none" and refs:
        _fail("unexpected_field", f"{field_path}.refs")
    if kind == "bearer" and list(refs.keys()) != ["Authorization"]:
        _fail("invalid_mcp_auth", f"{field_path}.refs")


def _mcp_payload(payload: dict[str, object], field_path: str) -> dict[str, object]:
    if "kind" not in payload:
        _fail("missing_field", f"{field_path}.kind")
    kind = payload["kind"]
    if kind == "local_stdio":
        _keys(
            payload,
            {
                "schema_version",
                "kind",
                "name",
                "timeout_seconds",
                "discovery_revision",
                "selected_tools",
                "executable",
                "arguments",
                "working_directory",
                "env_refs",
            },
            field_path,
        )
        _int(payload, "schema_version", field_path, minimum=1, maximum=1)
        _text(payload, "name", field_path, max_len=200)
        _int(payload, "timeout_seconds", field_path, minimum=1, maximum=120)
        _nullable_digest(payload, "discovery_revision", field_path)
        _tool_selections(payload, field_path)
        _text(payload, "executable", field_path)
        _text_array(payload, "arguments", field_path)
        _text(payload, "working_directory", field_path)
        env_refs = _obj(payload["env_refs"], f"{field_path}.env_refs")
        for name, secure in env_refs.items():
            if not _ENV_NAME_RE.fullmatch(name):
                _fail("invalid_env_name", f"{field_path}.env_refs.{name}")
            _secure_ref(secure, f"{field_path}.env_refs.{name}")
        return payload
    if kind in ("remote_streamable_http", "remote_legacy_sse"):
        _keys(
            payload,
            {
                "schema_version",
                "kind",
                "name",
                "timeout_seconds",
                "discovery_revision",
                "selected_tools",
                "endpoint",
                "auth",
            },
            field_path,
        )
        _int(payload, "schema_version", field_path, minimum=1, maximum=1)
        _text(payload, "name", field_path, max_len=200)
        _int(payload, "timeout_seconds", field_path, minimum=1, maximum=120)
        _nullable_digest(payload, "discovery_revision", field_path)
        _tool_selections(payload, field_path)
        endpoint = _text(payload, "endpoint", field_path)
        if not endpoint.startswith("https://") and not endpoint.startswith("http://"):
            _fail("invalid_endpoint", f"{field_path}.endpoint")
        _mcp_auth(payload["auth"], f"{field_path}.auth")
        return payload
    _fail("invalid_enum", f"{field_path}.kind")
    return payload


def _tool_selections(obj: dict[str, object], field_path: str) -> None:
    selections = _array(obj, "selected_tools", field_path)
    for index, selection in enumerate(selections):
        path = f"{field_path}.selected_tools[{index}]"
        item = _obj(selection, path)
        _keys(item, {"name", "schema_digest"}, path)
        _text(item, "name", path)
        _digest(item, "schema_digest", path)


def _subset_refs(defaults: list[dict[str, object]], enabled: list[dict[str, object]], field_path: str) -> None:
    enabled_pairs = {
        (_uuid_value(item["resource_id"], field_path), _int_value(item["revision"], field_path, minimum=1))
        for item in enabled
    }
    for item in defaults:
        key = (_uuid_value(item["resource_id"], field_path), _int_value(item["revision"], field_path, minimum=1))
        if key not in enabled_pairs:
            _fail("invalid_subset", field_path)


# ---------------------------------------------------------------------------
# 决定/交付/返回引用（§5.10、§5.7）
# ---------------------------------------------------------------------------


def _answer_schema(value: object, field_path: str) -> dict[str, object]:
    schema = _obj(value, field_path)
    if "kind" not in schema:
        _fail("missing_field", f"{field_path}.kind")
    kind = schema["kind"]
    if kind == "text":
        _keys(schema, {"kind", "min_length", "max_length"}, field_path)
        min_length = _int(schema, "min_length", field_path, minimum=1)
        max_length = _int(schema, "max_length", field_path, minimum=1, maximum=20000)
        if min_length > max_length:
            _fail("invalid_range", f"{field_path}.min_length")
    elif kind == "choice":
        _keys(schema, {"kind", "option_ids", "min_items", "max_items"}, field_path)
        option_ids = _text_array(schema, "option_ids", field_path, allow_empty=False)
        if len(set(option_ids)) != len(option_ids):
            _fail("duplicate_id", f"{field_path}.option_ids")
        min_items = _int(schema, "min_items", field_path, minimum=1)
        max_items = _int(schema, "max_items", field_path, minimum=1)
        if min_items > max_items or max_items > len(option_ids):
            _fail("invalid_range", f"{field_path}.max_items")
    elif kind == "permission":
        _keys(schema, {"kind"}, field_path)
    else:
        _fail("invalid_enum", f"{field_path}.kind")
    return schema


def _answer_value(value: object, schema: object, field_path: str) -> None:
    answer = _obj(value, field_path)
    schema_obj = _answer_schema(schema, f"{field_path}.schema")
    kind = schema_obj["kind"]
    if kind == "text":
        _keys(answer, {"kind", "text"}, field_path)
        if answer["kind"] != "text":
            _fail("invalid_enum", f"{field_path}.kind")
        text = _text(answer, "text", field_path)
        min_length = _int_value(schema_obj["min_length"], f"{field_path}.schema.min_length", minimum=1)
        max_length = _int_value(schema_obj["max_length"], f"{field_path}.schema.max_length", minimum=1)
        if not min_length <= len(text) <= max_length:
            _fail("invalid_length", f"{field_path}.text")
        return
    if kind == "choice":
        _keys(answer, {"kind", "option_ids"}, field_path)
        if answer["kind"] != "choice":
            _fail("invalid_enum", f"{field_path}.kind")
        option_ids = _text_array(answer, "option_ids", field_path, allow_empty=False)
        if len(set(option_ids)) != len(option_ids):
            _fail("duplicate_id", f"{field_path}.option_ids")
        allowed = _text_array(schema_obj, "option_ids", f"{field_path}.schema", allow_empty=False)
        if any(item not in allowed for item in option_ids):
            _fail("invalid_option", f"{field_path}.option_ids")
        count = len(option_ids)
        min_items = _int_value(schema_obj["min_items"], f"{field_path}.schema.min_items", minimum=1)
        max_items = _int_value(schema_obj["max_items"], f"{field_path}.schema.max_items", minimum=1)
        if not min_items <= count <= max_items:
            _fail("invalid_range", f"{field_path}.option_ids")
        return
    _keys(answer, {"kind", "allowed"}, field_path)
    if answer["kind"] != "permission":
        _fail("invalid_enum", f"{field_path}.kind")
    _bool(answer, "allowed", field_path)


def _decision_view_obj(doc: object, field_path: str = "$") -> dict[str, object]:
    view = _obj(doc, field_path)
    _keys(
        view,
        {
            "decision_id",
            "target",
            "revision",
            "native_request_id",
            "continuation_ref",
            "question_key",
            "question_params",
            "options",
            "answer_schema",
            "scope_digest",
            "expires_at",
            "status",
            "answer_command_id",
            "answer",
            "answer_phase",
            "answer_problem",
        },
        field_path,
    )
    _uuid(view, "decision_id", field_path)
    _target_obj(view["target"], f"{field_path}.target")
    _int(view, "revision", field_path, minimum=1)
    _text(view, "native_request_id", field_path)
    continuation = view["continuation_ref"]
    if continuation is not None and not isinstance(continuation, str):
        _fail("invalid_type", f"{field_path}.continuation_ref")
    _text(view, "question_key", field_path)
    params = view["question_params"]
    if not isinstance(params, str):
        _fail("invalid_type", f"{field_path}.question_params")
    _obj(decode_json_doc(params), f"{field_path}.question_params")
    options = decode_json_doc(_text(view, "options", field_path))
    options_list = _obj_array(options, f"{field_path}.options")
    option_ids: list[str] = []
    for index, option in enumerate(options_list):
        path = f"{field_path}.options[{index}]"
        item = _obj(option, path)
        _keys(item, {"id", "label"}, path)
        option_ids.append(_text(item, "id", path))
        _text(item, "label", path)
    if len(set(option_ids)) != len(option_ids):
        _fail("duplicate_id", f"{field_path}.options")
    schema = decode_json_doc(_text(view, "answer_schema", field_path))
    _answer_schema(schema, f"{field_path}.answer_schema")
    if isinstance(schema, dict) and schema.get("kind") == "choice":
        allowed = schema.get("option_ids")
        if isinstance(allowed, list) and any(item not in option_ids for item in allowed):
            _fail("invalid_option", f"{field_path}.answer_schema.option_ids")
    _digest(view, "scope_digest", field_path)
    _nullable_utc(view, "expires_at", field_path)
    status = _enum(view["status"], DECISION_STATUSES, f"{field_path}.status")
    answer_command_id = _nullable_uuid(view, "answer_command_id", field_path)
    answer = view["answer"]
    if answer is not None and not isinstance(answer, str):
        _fail("invalid_type", f"{field_path}.answer")
    phase = _enum(view["answer_phase"], ANSWER_PHASES, f"{field_path}.answer_phase")
    problem = _nullable_problem(view, "answer_problem", field_path)
    _decision_answer_joint(status, phase, answer_command_id, answer, problem, field_path)
    if answer is not None:
        _answer_value(decode_json_doc(answer), schema, f"{field_path}.answer")
    return view


def _obj_array(value: object, field_path: str) -> list[object]:
    if not isinstance(value, list):
        _fail("invalid_type", field_path)
    return value


def _decision_answer_joint(
    status: str,
    phase: str,
    command_id: uuid.UUID | None,
    answer: str | None,
    problem: dict[str, object] | None,
    field_path: str,
) -> None:
    recorded = "answer_command_id" if command_id is not None else "answer"
    if phase == "unanswered":
        if command_id is not None or answer is not None:
            _fail("unexpected_field", f"{field_path}.{recorded}")
        if status not in ("pending", "expired", "invalidated"):
            _fail("invalid_enum", f"{field_path}.status")
        if status == "pending" and problem is not None:
            _fail("unexpected_field", f"{field_path}.answer_problem")
        if status in ("expired", "invalidated") and problem is None:
            _fail("missing_field", f"{field_path}.answer_problem")
        return
    if command_id is None or answer is None:
        _fail("missing_field", f"{field_path}.{recorded}")
    if status in ("pending",):
        _fail("invalid_enum", f"{field_path}.status")
    if phase in ("delivered", "effective") and problem is not None:
        _fail("unexpected_field", f"{field_path}.answer_problem")
    if phase == "unknown" and problem is None:
        _fail("missing_field", f"{field_path}.answer_problem")
    if phase == "invalidated":
        if status not in ("expired", "invalidated"):
            _fail("invalid_enum", f"{field_path}.status")
        if problem is None:
            _fail("missing_field", f"{field_path}.answer_problem")
    elif status == "expired":
        _fail("invalid_enum", f"{field_path}.status")
    if status not in ("answered", "rejected", "invalidated", "expired"):
        _fail("invalid_enum", f"{field_path}.status")


def _delivery_view_obj(doc: object, field_path: str = "$") -> dict[str, object]:
    view = _obj(doc, field_path)
    _keys(
        view,
        {
            "schema_version",
            "operation_id",
            "revision",
            "artifact_id",
            "artifact_revision",
            "format",
            "destination",
            "expected_target_digest",
            "confirm_overwrite",
            "content_digest",
            "source_availability",
            "delivery_digest",
            "status",
            "phase",
            "cancel_requested",
            "problem",
        },
        field_path,
    )
    _int(view, "schema_version", field_path, minimum=1, maximum=1)
    _uuid(view, "operation_id", field_path)
    _int(view, "revision", field_path, minimum=1)
    _uuid(view, "artifact_id", field_path)
    _int(view, "artifact_revision", field_path, minimum=1)
    _enum(view["format"], ("md", "csv", "xlsx"), f"{field_path}.format")
    _text(view, "destination", field_path)
    _nullable_digest(view, "expected_target_digest", field_path)
    _bool(view, "confirm_overwrite", field_path)
    _digest(view, "content_digest", field_path)
    if view["source_availability"] is not None:
        _source_availability_obj(view["source_availability"], f"{field_path}.source_availability")
    _nullable_digest(view, "delivery_digest", field_path)
    status = _enum(view["status"], ("preparing", "published", "failed", "cancelled", "unknown"), f"{field_path}.status")
    phase = _enum(view["phase"], ("preparing", "writing", "publishing", "settled"), f"{field_path}.phase")
    _bool(view, "cancel_requested", field_path)
    problem = _nullable_problem(view, "problem", field_path)
    if status == "preparing":
        if phase == "settled":
            _fail("invalid_enum", f"{field_path}.phase")
    elif status in ("published", "failed", "cancelled"):
        if phase != "settled":
            _fail("invalid_enum", f"{field_path}.phase")
    elif status == "unknown":
        if problem is None:
            _fail("missing_field", f"{field_path}.problem")
        if phase == "settled":
            _fail("invalid_enum", f"{field_path}.phase")
    if status == "published":
        if view["source_availability"] is None or view["delivery_digest"] is None or problem is not None:
            _fail("missing_field", f"{field_path}.delivery_digest")
    if status == "failed" and problem is None:
        _fail("missing_field", f"{field_path}.problem")
    if phase in ("writing", "publishing") and view["source_availability"] is None:
        _fail("missing_field", f"{field_path}.source_availability")
    if phase == "publishing" and view["delivery_digest"] is None:
        _fail("missing_field", f"{field_path}.delivery_digest")
    return view


def _source_availability_obj(value: object, field_path: str) -> None:
    availability = _obj(value, field_path)
    _keys(availability, {"observed_at", "fresh", "sources"}, field_path)
    _utc_value(availability["observed_at"], f"{field_path}.observed_at")
    _bool(availability, "fresh", field_path)
    for index, item in enumerate(_array(availability, "sources", field_path)):
        path = f"{field_path}.sources[{index}]"
        source = _obj(item, path)
        _keys(source, {"ref", "status", "problem"}, path)
        _ref(source, "ref", path)
        _enum(source["status"], AVAILABILITY, f"{path}.status")
        _nullable_problem(source, "problem", path)


def _return_ref_obj(doc: object, field_path: str = "$") -> dict[str, object]:
    view = _obj(doc, field_path)
    _keys(
        view,
        {
            "schema_version",
            "kind",
            "conversation_id",
            "path_id",
            "path_revision",
            "message_id",
            "message_revision",
            "execution_id",
            "source_module",
            "source_object_key",
            "view_state",
        },
        field_path,
    )
    _int(view, "schema_version", field_path, minimum=1, maximum=1)
    kind = _enum(view["kind"], ("conversation", "message", "execution", "business"), f"{field_path}.kind")
    conversation_id = _nullable_uuid(view, "conversation_id", field_path)
    path_id = _nullable_uuid(view, "path_id", field_path)
    path_revision = _nullable_int(view, "path_revision", field_path, minimum=1)
    message_id = _nullable_uuid(view, "message_id", field_path)
    message_revision = _nullable_int(view, "message_revision", field_path, minimum=1)
    execution_id = _nullable_uuid(view, "execution_id", field_path)
    _text(view, "source_module", field_path)
    source_object_key = view["source_object_key"]
    if source_object_key is not None:
        _text_value(source_object_key, f"{field_path}.source_object_key")
    if kind == "business":
        if source_object_key is None:
            _fail("missing_field", f"{field_path}.source_object_key")
        _business_view_state(view["view_state"], f"{field_path}.view_state")
        return view
    if source_object_key is not None or view["view_state"] is not None:
        _fail("unexpected_field", f"{field_path}.source_object_key")
    if kind == "conversation":
        if conversation_id is None or path_id is None or path_revision is None:
            _fail("missing_field", f"{field_path}.conversation_id")
        if message_id is not None or execution_id is not None:
            _fail("unexpected_field", f"{field_path}.message_id")
    elif kind == "message":
        if message_id is None or message_revision is None:
            _fail("missing_field", f"{field_path}.message_id")
    elif kind == "execution" and execution_id is None:
        _fail("missing_field", f"{field_path}.execution_id")
    return view


def _business_view_state(value: object, field_path: str) -> None:
    state = _obj(value, field_path)
    _keys(
        state,
        {"schema_version", "page", "selected_object_key", "selected_strategy_key", "search_text", "reading_anchor"},
        field_path,
    )
    _int(state, "schema_version", field_path, minimum=1, maximum=1)
    _enum(state["page"], ("market", "watchlist", "screener", "backtest", "news"), f"{field_path}.page")
    _nullable_text(state, "selected_object_key", field_path)
    _nullable_text(state, "selected_strategy_key", field_path)
    if not isinstance(state["search_text"], str):
        _fail("invalid_type", f"{field_path}.search_text")
    anchor = state["reading_anchor"]
    if anchor is not None:
        anchor_obj = _obj(anchor, f"{field_path}.reading_anchor")
        _keys(anchor_obj, {"section", "row_key"}, f"{field_path}.reading_anchor")
        _text(anchor_obj, "section", f"{field_path}.reading_anchor")
        _nullable_text(anchor_obj, "row_key", f"{field_path}.reading_anchor")


# ---------------------------------------------------------------------------
# 计划与请求（§3/§5.6/§5.5）
# ---------------------------------------------------------------------------


def _plan_obj(doc: object, field_path: str = "$") -> dict[str, object]:
    plan = _obj(doc, field_path)
    _keys(plan, {"schema_version", "goal", "as_of", "nodes", "constraints"}, field_path)
    _int(plan, "schema_version", field_path, minimum=1, maximum=1)
    _text(plan, "goal", field_path)
    as_of = _date(plan, "as_of", field_path)
    if as_of > datetime.now(UTC).date():
        _fail("pit_ineligible", f"{field_path}.as_of")
    nodes = _array(plan, "nodes", field_path)
    if len(nodes) > 20:
        _fail("limit_reached", f"{field_path}.nodes")
    node_ids: list[uuid.UUID] = []
    node_map: dict[uuid.UUID, dict[str, object]] = {}
    for index, node in enumerate(nodes):
        path = f"{field_path}.nodes[{index}]"
        item = _obj(node, path)
        _keys(item, {"node_id", "role_key", "goal", "depends_on", "source_refs", "output_contract", "required"}, path)
        node_id = _uuid(item, "node_id", path)
        if node_id in node_map:
            _fail("duplicate_id", f"{path}.node_id")
        node_ids.append(node_id)
        node_map[node_id] = item
        _text(item, "role_key", path, max_len=128)
        _text(item, "goal", path)
        _ref_array(item, "source_refs", path)
        _enum(item["output_contract"], OUTPUT_CONTRACTS, f"{path}.output_contract")
        _bool(item, "required", path)
    for index, node in enumerate(nodes):
        path = f"{field_path}.nodes[{index}]"
        item = _obj(node, path)
        deps = _array(item, "depends_on", path)
        dep_ids: list[uuid.UUID] = []
        for dep_index, dep in enumerate(deps):
            dep_id = _uuid_value(dep, f"{path}.depends_on[{dep_index}]")
            if dep_id not in node_map:
                _fail("reference_conflict", f"{path}.depends_on[{dep_index}]")
            if dep_id == _uuid_value(item["node_id"], f"{path}.node_id"):
                _fail("invalid_graph", f"{path}.depends_on[{dep_index}]")
            dep_ids.append(dep_id)
        if len(set(dep_ids)) != len(dep_ids):
            _fail("duplicate_id", f"{path}.depends_on")
        item["_resolved_deps"] = dep_ids
    _acyclic(node_map, field_path)
    constraints = _obj(plan["constraints"], f"{field_path}.constraints")
    _keys(constraints, {"external_lookup", "forbidden_operations"}, f"{field_path}.constraints")
    _bool(constraints, "external_lookup", f"{field_path}.constraints")
    _text_array(constraints, "forbidden_operations", f"{field_path}.constraints")
    return plan


def _acyclic(node_map: dict[uuid.UUID, dict[str, object]], field_path: str) -> None:
    state: dict[uuid.UUID, int] = {node_id: 0 for node_id in node_map}

    def _visit(node_id: uuid.UUID) -> None:
        if state[node_id] == 1:
            _fail("invalid_graph", f"{field_path}.nodes")
        if state[node_id] == 2:
            return
        state[node_id] = 1
        resolved = node_map[node_id].get("_resolved_deps")
        if not isinstance(resolved, list):
            _fail("invalid_graph", f"{field_path}.nodes")
        for dep in resolved:
            if not isinstance(dep, uuid.UUID):
                _fail("invalid_graph", f"{field_path}.nodes")
            _visit(dep)
        state[node_id] = 2

    for node_id in node_map:
        _visit(node_id)


def validate_plan(plan: JsonDoc) -> None:
    decoded = decode_json_doc(plan)
    _plan_obj(decoded)


def _request_roles(roles: list[RoleBinding]) -> tuple[RoleBinding, RoleBinding]:
    main = [role for role in roles if role.role_key == "main"]
    if len(main) != 1:
        _fail("invalid_role_set", "roles")
    if len({role.role_key for role in roles}) != len(roles):
        _fail("duplicate_id", "roles")
    return main[0], main[0]


def _dedupe_refs(refs: tuple[ResourceRef, ...], field_path: str) -> tuple[ResourceRef, ...]:
    seen: dict[uuid.UUID, int] = {}
    result: list[ResourceRef] = []
    for ref in refs:
        if not isinstance(ref, ResourceRef):
            _fail("invalid_type", field_path)
        if ref.revision < 1:
            _fail("invalid_range", field_path)
        previous = seen.get(ref.resource_id)
        if previous is not None:
            if previous != ref.revision:
                _fail("identity_conflict", field_path)
            continue
        seen[ref.resource_id] = ref.revision
        result.append(ref)
    return tuple(result)


def _validate_role_binding(role: RoleBinding, index: int) -> None:
    path = f"roles[{index}]"
    if not 1 <= len(role.role_key) <= 128:
        _fail("invalid_length", f"{path}.role_key")
    if role.revision < 1:
        _fail("invalid_range", f"{path}.revision")
    if not isinstance(role.prompt, ResourceRef) or role.prompt.revision < 1:
        _fail("invalid_type", f"{path}.prompt")
    if role.model.kind not in ("explicit", "scenario_default", "inherit"):
        _fail("invalid_enum", f"{path}.model.kind")
    if role.model.kind == "explicit":
        if not role.model.connection_id or role.model.scenario_id is not None:
            _fail("invalid_model_choice", f"{path}.model")
    elif role.model.kind == "scenario_default":
        if not role.model.scenario_id or role.model.connection_id is not None:
            _fail("invalid_model_choice", f"{path}.model")
    elif role.model.connection_id is not None or role.model.scenario_id is not None:
        _fail("invalid_model_choice", f"{path}.model")
    if role.role_key == "main" and role.model.kind == "inherit":
        _fail("invalid_model_choice", f"{path}.model")
    enabled = _dedupe_refs(role.enabled_skills, f"{path}.enabled_skills")
    defaults = _dedupe_refs(role.default_skills, f"{path}.default_skills")
    if not enabled:
        if defaults:
            _fail("invalid_subset", f"{path}.default_skills")
    _subset_dtos(defaults, enabled, f"{path}.default_skills")


def _subset_dtos(refs: tuple[ResourceRef, ...], allowed: tuple[ResourceRef, ...], field_path: str) -> None:
    allowed_pairs = {(ref.resource_id, ref.revision) for ref in allowed}
    for ref in refs:
        if (ref.resource_id, ref.revision) not in allowed_pairs:
            _fail("invalid_subset", field_path)


def validate_request(request: AgentRequest) -> None:
    key = request.key
    for name in ("domain", "source_module", "request_key"):
        value = getattr(key, name)
        if not isinstance(value, str) or not 1 <= len(value) <= 128:
            _fail("invalid_length", f"key.{name}")
    if request.intent_kind not in ("ordinary", "historical_resend", "plan_start", "plan_revision", "business"):
        _fail("invalid_enum", "intent_kind")
    if request.mode not in ("discuss", "plan", "execute"):
        _fail("invalid_enum", "mode")
    if not isinstance(request.text, str) or not request.text:
        _fail("invalid_type", "text")
    if request.output_contract not in OUTPUT_CONTRACTS:
        _fail("unsupported_schema", "output_contract")
    business = (request.business_owner, request.business_run, request.business_slot)
    if any(item is not None for item in business) and not all(item is not None for item in business):
        _fail("invalid_request", "business_owner")
    if request.intent_kind == "business" and not all(item is not None for item in business):
        _fail("missing_field", "business_owner")
    if request.intent_kind != "business" and all(item is not None for item in business):
        _fail("unexpected_field", "business_owner")
    review = (request.reviewed_plan, request.plan_review_revision, request.plan_review_digest)
    if any(item is not None for item in review) and not all(item is not None for item in review):
        _fail("invalid_request", "reviewed_plan")
    if request.intent_kind == "plan_start":
        if request.mode != "execute":
            _fail("invalid_enum", "mode")
        if not all(item is not None for item in review):
            _fail("missing_field", "reviewed_plan")
    elif request.intent_kind == "plan_revision":
        if request.mode != "plan":
            _fail("invalid_enum", "mode")
        if not all(item is not None for item in review):
            _fail("missing_field", "reviewed_plan")
    else:
        if any(item is not None for item in review):
            _fail("unexpected_field", "reviewed_plan")
    if request.plan_review_revision is not None and request.plan_review_revision < 1:
        _fail("invalid_range", "plan_review_revision")
    if request.plan_review_digest is not None and not _DIGEST_RE.fullmatch(request.plan_review_digest):
        _fail("invalid_digest", "plan_review_digest")
    if request.as_of is not None and not isinstance(request.as_of, date):
        _fail("invalid_date", "as_of")
    if request.as_of is not None and request.as_of > datetime.now(UTC).date():
        _fail("pit_ineligible", "as_of")
    basis = request.as_of_basis
    if basis not in ("explicit", "source", "not_applicable"):
        _fail("invalid_enum", "as_of_basis")
    if request.as_of is None:
        if basis != "not_applicable":
            _fail("date_required", "as_of")
        if request.mode != "discuss" or request.output_contract != "answer/1":
            _fail("date_required", "as_of")
    elif basis == "not_applicable":
        _fail("invalid_enum", "as_of_basis")
    if request.conversation_id is None:
        if request.context.path_id is not None or request.context.path_revision is not None:
            _fail("unexpected_field", "context.path_id")
    else:
        path_pair = (request.context.path_id, request.context.path_revision)
        if any(item is not None for item in path_pair) and not all(item is not None for item in path_pair):
            _fail("invalid_request", "context.path_id")
        if request.context.path_revision is not None and request.context.path_revision < 1:
            _fail("invalid_range", "context.path_revision")
    _dedupe_refs(request.sources, "sources")
    _dedupe_refs(request.manual_skills, "manual_skills")
    _dedupe_refs(request.mcp, "mcp")
    if not request.roles:
        _fail("missing_field", "roles")
    for index, role in enumerate(request.roles):
        _validate_role_binding(role, index)
    mains = [role for role in request.roles if role.role_key == "main"]
    if len(mains) != 1:
        _fail("invalid_role_set", "roles")
    if len({role.role_key for role in request.roles}) != len(request.roles):
        _fail("duplicate_id", "roles")
    manual = _dedupe_refs(request.manual_skills, "manual_skills")
    main = mains[0]
    _subset_dtos(manual, _dedupe_refs(main.enabled_skills, "roles[0].enabled_skills"), "manual_skills")
    if request.intent_kind == "historical_resend" and request.conversation_id is None:
        _fail("missing_field", "conversation_id")


def effective_skills(
    defaults: tuple[ResourceRef, ...],
    manual: tuple[ResourceRef, ...],
    *,
    enabled: tuple[ResourceRef, ...],
    child: bool,
) -> tuple[ResourceRef, ...]:
    """§6.3：主=默认∪手选，子仅自身默认；两集合须在 enabled 内。"""

    enabled_refs = _dedupe_refs(enabled, "enabled")
    default_refs = _dedupe_refs(defaults, "default_skills")
    _subset_dtos(default_refs, enabled_refs, "default_skills")
    if child:
        return default_refs
    manual_refs = _dedupe_refs(manual, "manual_skills")
    _subset_dtos(manual_refs, enabled_refs, "manual_skills")
    seen = {ref.resource_id: ref.revision for ref in default_refs}
    result = list(default_refs)
    for ref in manual_refs:
        if ref.resource_id not in seen:
            seen[ref.resource_id] = ref.revision
            result.append(ref)
    return tuple(result)


# ---------------------------------------------------------------------------
# 成果合同（§10.2.1）
# ---------------------------------------------------------------------------


def _claim(value: object, field_path: str) -> dict[str, object]:
    claim = _obj(value, field_path)
    _keys(claim, {"claim_id", "text"}, field_path)
    _text(claim, "claim_id", field_path)
    _text(claim, "text", field_path)
    return claim


def _evidence_key(value: object, field_path: str) -> dict[str, object]:
    evidence = _obj(value, field_path)
    _keys(evidence, {"claim_id", "resource_id", "revision", "locator"}, field_path)
    _text(evidence, "claim_id", field_path)
    _uuid(evidence, "resource_id", field_path)
    _int(evidence, "revision", field_path, minimum=1)
    _text(evidence, "locator", field_path)
    return evidence


def _evidence(value: object, field_path: str) -> dict[str, object]:
    evidence = _obj(value, field_path)
    _keys(
        evidence,
        {"claim_id", "resource_id", "revision", "locator", "published_at", "published_date", "time_precision"},
        field_path,
    )
    _text(evidence, "claim_id", field_path)
    _uuid(evidence, "resource_id", field_path)
    _int(evidence, "revision", field_path, minimum=1)
    _text(evidence, "locator", field_path)
    precision = _enum(evidence["time_precision"], ("datetime", "date", "unknown"), f"{field_path}.time_precision")
    published_at = evidence["published_at"]
    published_date = evidence["published_date"]
    if precision == "datetime":
        if published_at is None:
            _fail("missing_field", f"{field_path}.published_at")
        _utc_value(published_at, f"{field_path}.published_at")
    elif precision == "date":
        if published_at is not None:
            _fail("unexpected_field", f"{field_path}.published_at")
        if published_date is None:
            _fail("missing_field", f"{field_path}.published_date")
        _date_value(published_date, f"{field_path}.published_date")
    elif published_at is not None or published_date is not None:
        _fail("unexpected_field", f"{field_path}.published_at")
    return evidence


def _claim_ids(value: object, field_path: str) -> list[str]:
    items = _obj_array(value, field_path)
    ids: list[str] = []
    for index, item in enumerate(items):
        path = f"{field_path}[{index}]"
        claim = _claim(item, path)
        ids.append(_text(claim, "claim_id", path))
    return ids


def _common(content: dict[str, object], field_path: str, *, allow: frozenset[str] = frozenset()) -> dict[str, object]:
    _keys(
        content,
        {
            "schema_version",
            "as_of",
            "facts",
            "interpretations",
            "uncertainties",
            "limitations",
            "evidence",
        }
        | allow,
        field_path,
    )
    _int(content, "schema_version", field_path, minimum=1, maximum=1)
    _nullable_date(content, "as_of", field_path)
    facts = _array(content, "facts", field_path)
    interpretations = _array(content, "interpretations", field_path)
    fact_ids = tuple(_claim(item, f"{field_path}.facts[{index}]") for index, item in enumerate(facts))
    interp_ids = tuple(
        _claim(item, f"{field_path}.interpretations[{index}]") for index, item in enumerate(interpretations)
    )
    all_ids = [str(item["claim_id"]) for item in fact_ids] + [str(item["claim_id"]) for item in interp_ids]
    if len(set(all_ids)) != len(all_ids):
        _fail("duplicate_id", f"{field_path}.facts")
    _text_array(content, "uncertainties", field_path)
    _text_array(content, "limitations", field_path)
    evidence_items = _array(content, "evidence", field_path)
    evidence_keys: set[tuple[object, object, object, object]] = set()
    for index, item in enumerate(evidence_items):
        evidence = _evidence(item, f"{field_path}.evidence[{index}]")
        claim_id = _text(evidence, "claim_id", f"{field_path}.evidence[{index}]")
        if claim_id not in all_ids:
            _fail("reference_conflict", f"{field_path}.evidence[{index}].claim_id")
        key = (
            claim_id,
            _uuid(evidence, "resource_id", field_path),
            _int(evidence, "revision", field_path, minimum=1),
            _text(evidence, "locator", field_path),
        )
        if key in evidence_keys:
            _fail("duplicate_id", f"{field_path}.evidence[{index}]")
        evidence_keys.add(key)
    content["_evidence_keys"] = evidence_keys
    content["_claim_ids"] = set(all_ids)
    return content


def _evidence_key_set(content: dict[str, object]) -> set[tuple[object, object, object, object]]:
    value = content.get("_evidence_keys")
    if isinstance(value, set):
        return cast("set[tuple[object, object, object, object]]", value)
    return set()


def _check_evidence_refs(
    refs: object, field_path: str, evidence_keys: set[tuple[object, object, object, object]]
) -> None:
    for index, item in enumerate(_obj_array(refs, field_path)):
        key = _evidence_key(item, f"{field_path}[{index}]")
        pair = (
            key["claim_id"],
            _uuid_value(key["resource_id"], f"{field_path}[{index}].resource_id"),
            key["revision"],
            key["locator"],
        )
        if pair not in evidence_keys:
            _fail("reference_conflict", f"{field_path}[{index}]")


def _money(value: object, field_path: str) -> tuple[Decimal | None, str]:
    money = _obj(value, field_path)
    _keys(money, {"value", "unit", "reason"}, field_path)
    amount = _nullable_decimal(money, "value", field_path)
    unit = _text(money, "unit", field_path)
    reason = _nullable_text(money, "reason", field_path)
    if amount is None and reason is None:
        _fail("missing_field", f"{field_path}.reason")
    return amount, unit


def _formula_result(
    numerator: Decimal | None, denominator: Decimal | None, field_path: str
) -> tuple[Decimal | None, str | None]:
    if numerator is None or denominator is None or denominator == 0:
        return None, "operand_missing_or_zero_denominator"
    with localcontext() as context:
        context.prec = 38
        value = (numerator / denominator).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)
    return value, None


def _compare_decimal(actual: Decimal | None, expected: Decimal | None, field_path: str) -> None:
    if actual is None and expected is None:
        return
    if actual is None or expected is None or actual != expected:
        _fail("calculation_mismatch", field_path)


def _comparison_rows(content: dict[str, object], field_path: str) -> None:
    rows = _array(content, "rows", field_path)
    objects: list[str] = []
    for index, row in enumerate(rows):
        path = f"{field_path}.rows[{index}]"
        item = _obj(row, path)
        _keys(
            item,
            {
                "object",
                "period",
                "scope",
                "revenue",
                "net_profit",
                "operating_cash_flow",
                "net_margin",
                "cash_profit_ratio",
                "formulas",
                "evidence_refs",
                "limitations",
            },
            path,
        )
        obj_name = _text(item, "object", path)
        if obj_name in objects:
            _fail("duplicate_id", f"{path}.object")
        objects.append(obj_name)
        _text(item, "period", path)
        _text(item, "scope", path)
        revenue, revenue_unit = _money(item["revenue"], f"{path}.revenue")
        profit, profit_unit = _money(item["net_profit"], f"{path}.net_profit")
        cash, cash_unit = _money(item["operating_cash_flow"], f"{path}.operating_cash_flow")
        if len({revenue_unit, profit_unit, cash_unit}) != 1:
            _fail("unsupported_unit", f"{path}.revenue")
        margin = _nullable_decimal(item, "net_margin", path)
        ratio = _nullable_decimal(item, "cash_profit_ratio", path)
        formulas = _array(item, "formulas", path)
        if len(formulas) != 2:
            _fail("missing_field", f"{path}.formulas")
        seen_fields: set[str] = set()
        for formula_index, formula in enumerate(formulas):
            formula_path = f"{path}.formulas[{formula_index}]"
            formula_obj = _obj(formula, formula_path)
            _keys(formula_obj, {"field", "numerator", "denominator", "result", "reason", "evidence_refs"}, formula_path)
            field_name = _enum(formula_obj["field"], ("net_margin", "cash_profit_ratio"), f"{formula_path}.field")
            if field_name in seen_fields:
                _fail("duplicate_id", f"{formula_path}.field")
            seen_fields.add(field_name)
            expected_num, expected_den = (
                ("net_profit", "revenue") if field_name == "net_margin" else ("operating_cash_flow", "net_profit")
            )
            if formula_obj["numerator"] != expected_num or formula_obj["denominator"] != expected_den:
                _fail("invalid_formula", formula_path)
            operand_num = profit if expected_num == "net_profit" else cash
            operand_den = revenue if expected_den == "revenue" else profit
            computed, _reason = _formula_result(operand_num, operand_den, formula_path)
            stored = _nullable_decimal(formula_obj, "result", formula_path)
            _compare_decimal(stored, computed, f"{formula_path}.result")
            limitation = _nullable_text(formula_obj, "reason", formula_path)
            if computed is None and limitation is None:
                _fail("missing_field", f"{formula_path}.reason")
            if field_name == "net_margin":
                _compare_decimal(margin, computed, f"{path}.net_margin")
            else:
                _compare_decimal(ratio, computed, f"{path}.cash_profit_ratio")
            _check_evidence_refs(
                formula_obj["evidence_refs"], f"{formula_path}.evidence_refs", _evidence_key_set(content)
            )
        if seen_fields != {"net_margin", "cash_profit_ratio"}:
            _fail("missing_field", f"{path}.formulas")
        _check_evidence_refs(item["evidence_refs"], f"{path}.evidence_refs", _evidence_key_set(content))
        _text_array(item, "limitations", path)


def _conditions(value: object, field_path: str) -> list[str]:
    condition_ids: list[str] = []
    for index, condition in enumerate(_obj_array(value, field_path)):
        path = f"{field_path}[{index}]"
        item = _obj(condition, path)
        _keys(item, {"condition_id", "field", "operator", "threshold", "unit"}, path)
        condition_id = _text(item, "condition_id", path)
        if condition_id in condition_ids:
            _fail("duplicate_id", f"{path}.condition_id")
        condition_ids.append(condition_id)
        _text(item, "field", path)
        _enum(item["operator"], ("eq", "ne", "gt", "ge", "lt", "le"), f"{path}.operator")
        _decimal(item, "threshold", path)
        _text(item, "unit", path)
    return condition_ids


def _candidate(value: object, field_path: str, condition_ids: list[str]) -> str:
    candidate = _obj(value, field_path)
    _keys(candidate, {"object", "checks", "evidence_refs", "limitations"}, field_path)
    _text(candidate, "object", field_path)
    checks = _array(candidate, "checks", field_path)
    check_ids: list[str] = []
    results: list[str] = []
    for index, check in enumerate(checks):
        path = f"{field_path}.checks[{index}]"
        item = _obj(check, path)
        _keys(item, {"condition_id", "actual", "unit", "result", "reason", "evidence_refs"}, path)
        condition_id = _text(item, "condition_id", path)
        if condition_id in check_ids:
            _fail("duplicate_id", f"{path}.condition_id")
        check_ids.append(condition_id)
        actual = _nullable_decimal(item, "actual", path)
        _text(item, "unit", path)
        result = _enum(item["result"], ("pass", "fail", "unknown"), f"{path}.result")
        results.append(result)
        reason = _nullable_text(item, "reason", path)
        if result == "unknown" and reason is None:
            _fail("missing_field", f"{path}.reason")
        if actual is None and result != "unknown":
            _fail("invalid_enum", f"{path}.result")
        _check_evidence_refs(item["evidence_refs"], f"{path}.evidence_refs", set())
    if check_ids != condition_ids:
        _fail("missing_field", f"{field_path}.checks")
    _check_evidence_refs(candidate["evidence_refs"], f"{field_path}.evidence_refs", set())
    _text_array(candidate, "limitations", field_path)
    if all(result == "pass" for result in results):
        return "matched"
    if all(result != "unknown" for result in results):
        return "not_matched"
    return "unverified"


def _candidates_body(content: dict[str, object], field_path: str) -> None:
    _ref(content, "pool_ref", field_path)
    condition_ids = _conditions(content["conditions"], f"{field_path}.conditions")
    coverage = _obj(content["coverage"], f"{field_path}.coverage")
    _keys(coverage, {"examined", "total", "verified"}, f"{field_path}.coverage")
    examined = _int(coverage, "examined", f"{field_path}.coverage", minimum=0)
    total = _int(coverage, "total", f"{field_path}.coverage", minimum=0)
    verified = _int(coverage, "verified", f"{field_path}.coverage", minimum=0)
    groups: dict[str, list[str]] = {}
    for group in ("matched", "not_matched", "unverified"):
        objects: list[str] = []
        for index, candidate in enumerate(_array(content, group, field_path)):
            path = f"{field_path}.{group}[{index}]"
            result = _candidate(candidate, path, condition_ids)
            if result != group:
                _fail("calculation_mismatch", f"{path}.checks")
            objects.append(str(_obj(candidate, path)["object"]))
        groups[group] = objects
    union = groups["matched"] + groups["not_matched"] + groups["unverified"]
    if len(union) != len(set(union)):
        _fail("duplicate_id", f"{field_path}.matched")
    if len(union) != examined:
        _fail("calculation_mismatch", f"{field_path}.coverage.examined")
    if verified != len(groups["matched"]) + len(groups["not_matched"]):
        _fail("calculation_mismatch", f"{field_path}.coverage.verified")
    if not 0 <= verified <= examined <= total:
        _fail("invalid_range", f"{field_path}.coverage")


def _occurrence(value: object, field_path: str) -> None:
    occurrence = _obj(value, field_path)
    _keys(occurrence, {"at", "date", "precision"}, field_path)
    precision = _enum(occurrence["precision"], ("datetime", "date", "unknown"), f"{field_path}.precision")
    at = occurrence["at"]
    day = occurrence["date"]
    if precision == "datetime":
        if at is None:
            _fail("missing_field", f"{field_path}.at")
        _utc_value(at, f"{field_path}.at")
        if day is not None:
            _fail("unexpected_field", f"{field_path}.date")
    elif precision == "date":
        if at is not None:
            _fail("unexpected_field", f"{field_path}.at")
        if day is None:
            _fail("missing_field", f"{field_path}.date")
        _date_value(day, f"{field_path}.date")
    elif at is not None or day is not None:
        _fail("unexpected_field", f"{field_path}.at")


def _news_body(content: dict[str, object], field_path: str) -> None:
    event_ids: list[str] = []
    for index, event in enumerate(_array(content, "events", field_path)):
        path = f"{field_path}.events[{index}]"
        item = _obj(event, path)
        _keys(
            item,
            {
                "event_id",
                "occurred_at",
                "published_at",
                "source_kind",
                "fact_or_opinion",
                "affected_objects",
                "description",
                "evidence_refs",
                "limitations",
            },
            path,
        )
        event_id = _text(item, "event_id", path)
        if event_id in event_ids:
            _fail("duplicate_id", f"{path}.event_id")
        event_ids.append(event_id)
        _occurrence(item["occurred_at"], f"{path}.occurred_at")
        _occurrence(item["published_at"], f"{path}.published_at")
        _enum(item["source_kind"], ("announcement", "news", "repost", "opinion", "other"), f"{path}.source_kind")
        _enum(item["fact_or_opinion"], ("fact", "opinion", "unknown"), f"{path}.fact_or_opinion")
        _text_array(item, "affected_objects", path)
        _text(item, "description", path)
        _check_evidence_refs(item["evidence_refs"], f"{path}.evidence_refs", _evidence_key_set(content))
        _text_array(item, "limitations", path)
    for index, excluded in enumerate(_array(content, "excluded_sources", field_path)):
        path = f"{field_path}.excluded_sources[{index}]"
        item = _obj(excluded, path)
        _keys(item, {"ref", "reason", "detail"}, path)
        _ref(item, "ref", path)
        _enum(
            item["reason"], ("after_as_of", "unrelated", "duplicate", "unknown_time", "unavailable"), f"{path}.reason"
        )
        _text(item, "detail", path)


def _review_check(value: object, field_path: str) -> str:
    check = _obj(value, field_path)
    _keys(
        check,
        {
            "claim_id",
            "field_path",
            "original_value",
            "verified_value",
            "unit",
            "result",
            "explanation",
            "evidence_refs",
        },
        field_path,
    )
    _text(check, "claim_id", field_path)
    _text(check, "field_path", field_path)
    _scalar_value(check["original_value"], f"{field_path}.original_value")
    _scalar_value(check["verified_value"], f"{field_path}.verified_value")
    _nullable_text(check, "unit", field_path)
    result = _enum(check["result"], ("confirmed_error", "supported", "unverified"), f"{field_path}.result")
    _text(check, "explanation", field_path)
    _check_evidence_refs(check["evidence_refs"], f"{field_path}.evidence_refs", set())
    return result


def _claim_change(value: object, field_path: str) -> str:
    change = _obj(value, field_path)
    _keys(change, {"claim_id", "field_path", "before", "after", "unit", "explanation", "evidence_refs"}, field_path)
    claim_id = _text(change, "claim_id", field_path)
    _text(change, "field_path", field_path)
    _scalar_value(change["before"], f"{field_path}.before")
    _scalar_value(change["after"], f"{field_path}.after")
    _nullable_text(change, "unit", field_path)
    _text(change, "explanation", field_path)
    _check_evidence_refs(change["evidence_refs"], f"{field_path}.evidence_refs", set())
    return claim_id


def _review_body(content: dict[str, object], field_path: str) -> None:
    _text(content, "challenged_claim", field_path)
    _artifact_ref(content["original_artifact_ref"], f"{field_path}.original_artifact_ref")
    check_results = [
        _review_check(item, f"{field_path}.checks[{index}]")
        for index, item in enumerate(_array(content, "checks", field_path))
    ]
    verdict = _enum(
        content["verdict"], ("confirmed_error", "no_error_found", "insufficient_evidence"), f"{field_path}.verdict"
    )
    changes = [
        _claim_change(item, f"{field_path}.changes[{index}]")
        for index, item in enumerate(_array(content, "changes", field_path))
    ]
    has_error = "confirmed_error" in check_results
    if verdict == "confirmed_error" and not has_error:
        _fail("calculation_mismatch", f"{field_path}.verdict")
    if verdict != "confirmed_error" and changes:
        _fail("unexpected_field", f"{field_path}.changes")
    if verdict == "confirmed_error" and not changes and not has_error:
        _fail("missing_field", f"{field_path}.changes")


def _dated_update_body(content: dict[str, object], field_path: str) -> None:
    old = _date(content, "old_as_of", field_path)
    new = _date(content, "new_as_of", field_path)
    if old > new:
        _fail("invalid_range", f"{field_path}.old_as_of")
    declared_as_of = content.get("as_of")
    if declared_as_of is None or _date_value(declared_as_of, f"{field_path}.as_of") != new:
        _fail("calculation_mismatch", f"{field_path}.as_of")
    _artifact_ref(content["old_artifact_ref"], f"{field_path}.old_artifact_ref")
    for index, item in enumerate(_array(content, "added_evidence", field_path)):
        path = f"{field_path}.added_evidence[{index}]"
        evidence = _evidence(item, path)
        pair = (evidence["claim_id"], evidence["resource_id"], evidence["revision"], evidence["locator"])
        if pair not in _evidence_key_set(content):
            _fail("reference_conflict", path)
    changed = [
        _claim_change(item, f"{field_path}.changed_claims[{index}]")
        for index, item in enumerate(_array(content, "changed_claims", field_path))
    ]
    unchanged = _claim_ids(content["unchanged_claims"], f"{field_path}.unchanged_claims")
    if set(changed) & set(unchanged):
        _fail("duplicate_id", f"{field_path}.changed_claims")


def _config_snapshot(value: object, field_path: str) -> None:
    snapshot = _obj(value, field_path)
    _keys(
        snapshot,
        {
            "report_ref",
            "raw_config_ref",
            "strategy_key",
            "strategy_version",
            "from_date",
            "until_date",
            "parameters",
            "limitations",
        },
        field_path,
    )
    _ref(snapshot, "report_ref", field_path)
    if snapshot["raw_config_ref"] is not None:
        _ref(snapshot, "raw_config_ref", field_path)
    _nullable_text(snapshot, "strategy_key", field_path)
    _nullable_text(snapshot, "strategy_version", field_path)
    _nullable_date(snapshot, "from_date", field_path)
    _nullable_date(snapshot, "until_date", field_path)
    parameters = _nullable_array(snapshot, "parameters", field_path)
    if parameters is not None:
        for index, parameter in enumerate(parameters):
            path = f"{field_path}.parameters[{index}]"
            item = _obj(parameter, path)
            _keys(item, {"name", "value"}, path)
            _text(item, "name", path)
            _scalar_value(item["value"], f"{path}.value")
    _text_array(snapshot, "limitations", field_path)


def _comparability_check(value: object, field_path: str) -> None:
    check = _obj(value, field_path)
    _keys(check, {"aspect", "result", "report_values", "reason", "evidence_refs"}, field_path)
    _enum(
        check["aspect"],
        ("period", "strategy", "parameters", "execution", "cost", "universe", "industry", "data_quality", "benchmark"),
        f"{field_path}.aspect",
    )
    _enum(check["result"], ("comparable", "not_comparable", "unknown"), f"{field_path}.result")
    for index, item in enumerate(_array(check, "report_values", field_path)):
        path = f"{field_path}.report_values[{index}]"
        record = _obj(item, path)
        _keys(record, {"report_ref", "value"}, path)
        _ref(record, "report_ref", path)
        _scalar_value(record["value"], f"{path}.value")
    _text(check, "reason", field_path)
    _check_evidence_refs(check["evidence_refs"], f"{field_path}.evidence_refs", set())


def _trade_assumption(value: object, field_path: str) -> None:
    assumption = _obj(value, field_path)
    _keys(
        assumption,
        {"report_ref", "signal_to_trade", "cost_value", "cost_unit", "universe_basis", "industry_basis", "limitations"},
        field_path,
    )
    _ref(assumption, "report_ref", field_path)
    _nullable_text(assumption, "signal_to_trade", field_path)
    _nullable_decimal(assumption, "cost_value", field_path)
    _nullable_text(assumption, "cost_unit", field_path)
    _enum(
        assumption["universe_basis"], ("point_in_time", "current_snapshot", "unknown"), f"{field_path}.universe_basis"
    )
    _enum(
        assumption["industry_basis"], ("point_in_time", "current_snapshot", "unknown"), f"{field_path}.industry_basis"
    )
    _text_array(assumption, "limitations", field_path)


def _data_warning(value: object, field_path: str) -> None:
    warning = _obj(value, field_path)
    _keys(warning, {"report_ref", "code", "message", "affected_dates", "evidence_refs"}, field_path)
    _ref(warning, "report_ref", field_path)
    _text(warning, "code", field_path)
    _text(warning, "message", field_path)
    affected = _nullable_array(warning, "affected_dates", field_path)
    if affected is not None:
        for index, item in enumerate(affected):
            _date_value(item, f"{field_path}.affected_dates[{index}]")
    _check_evidence_refs(warning["evidence_refs"], f"{field_path}.evidence_refs", set())


def _backtest_body(content: dict[str, object], field_path: str) -> None:
    _ref_array(content, "backtest_refs", field_path, allow_empty=False)
    for index, item in enumerate(_array(content, "config_snapshots", field_path)):
        _config_snapshot(item, f"{field_path}.config_snapshots[{index}]")
    for index, item in enumerate(_array(content, "comparability", field_path)):
        _comparability_check(item, f"{field_path}.comparability[{index}]")
    for index, item in enumerate(_array(content, "trade_assumptions", field_path)):
        _trade_assumption(item, f"{field_path}.trade_assumptions[{index}]")
    warnings = _nullable_array(content, "data_warnings", field_path)
    if warnings is not None:
        for index, item in enumerate(warnings):
            _data_warning(item, f"{field_path}.data_warnings[{index}]")
    _text_array(content, "attribution_limits", field_path)


def _stock_result(value: object, field_path: str) -> None:
    result = _obj(value, field_path)
    _keys(result, {"conclusion_label", "score", "confidence", "uncertainty_factors", "thinking", "summary"}, field_path)
    label = result["conclusion_label"]
    if label is not None:
        _enum(label, ("strong_buy", "watchlist", "uncertain", "reject"), f"{field_path}.conclusion_label")
    _nullable_number(result, "score", field_path, minimum=0, maximum=100)
    _nullable_number(result, "confidence", field_path, minimum=0, maximum=1)
    _nullable_text_array(result, "uncertainty_factors", field_path)
    _nullable_text(result, "thinking", field_path)
    _nullable_text(result, "summary", field_path)


_ARTIFACT_EXTRA_KEYS: dict[str, frozenset[str]] = {
    "answer/1": frozenset({"answer"}),
    "stock_analysis/1": frozenset({"stock_result"}),
    "comparison/1": frozenset({"rows"}),
    "candidates/1": frozenset({"pool_ref", "conditions", "coverage", "matched", "not_matched", "unverified"}),
    "news_digest/1": frozenset({"events", "excluded_sources"}),
    "review/1": frozenset({"challenged_claim", "original_artifact_ref", "checks", "verdict", "changes"}),
    "dated_update/1": frozenset(
        {"old_as_of", "new_as_of", "old_artifact_ref", "added_evidence", "changed_claims", "unchanged_claims"}
    ),
    "backtest_explanation/1": frozenset(
        {
            "backtest_refs",
            "config_snapshots",
            "comparability",
            "trade_assumptions",
            "data_warnings",
            "attribution_limits",
        }
    ),
}


def validate_artifact(output_contract: str, doc: JsonDoc) -> None:
    if output_contract not in OUTPUT_CONTRACTS:
        _fail("unsupported_schema", "output_contract")
    content = decode_json_doc(doc)
    content_obj = _common(_obj(content, "content"), "content", allow=_ARTIFACT_EXTRA_KEYS[output_contract])
    if output_contract != "answer/1" and content_obj["as_of"] is None:
        _fail("date_required", "content.as_of")
    if output_contract == "answer/1":
        if "answer" not in content_obj:
            _fail("missing_field", "content.answer")
        _text(content_obj, "answer", "content")
        return
    if output_contract == "stock_analysis/1":
        if "stock_result" not in content_obj:
            _fail("missing_field", "content.stock_result")
        _stock_result(content_obj["stock_result"], "content.stock_result")
        return
    if output_contract == "comparison/1":
        if "rows" not in content_obj:
            _fail("missing_field", "content.rows")
        _comparison_rows(content_obj, "content")
        return
    if output_contract == "candidates/1":
        missing = {"pool_ref", "conditions", "coverage", "matched", "not_matched", "unverified"} - content_obj.keys()
        if missing:
            _fail("missing_field", f"content.{sorted(missing)[0]}")
        _candidates_body(content_obj, "content")
        return
    if output_contract == "news_digest/1":
        if "events" not in content_obj or "excluded_sources" not in content_obj:
            _fail("missing_field", "content.events")
        _news_body(content_obj, "content")
        return
    if output_contract == "review/1":
        missing = {"challenged_claim", "original_artifact_ref", "checks", "verdict", "changes"} - content_obj.keys()
        if missing:
            _fail("missing_field", f"content.{sorted(missing)[0]}")
        _review_body(content_obj, "content")
        return
    if output_contract == "dated_update/1":
        missing = {
            "old_as_of",
            "new_as_of",
            "old_artifact_ref",
            "added_evidence",
            "changed_claims",
            "unchanged_claims",
        } - content_obj.keys()
        if missing:
            _fail("missing_field", f"content.{sorted(missing)[0]}")
        _dated_update_body(content_obj, "content")
        return
    if output_contract == "backtest_explanation/1":
        missing = {
            "backtest_refs",
            "config_snapshots",
            "comparability",
            "trade_assumptions",
            "data_warnings",
            "attribution_limits",
        } - content_obj.keys()
        if missing:
            _fail("missing_field", f"content.{sorted(missing)[0]}")
        _backtest_body(content_obj, "content")


def validate_return_ref(doc: JsonDoc) -> None:
    _return_ref_obj(decode_json_doc(doc))


# ---------------------------------------------------------------------------
# 管理 payload 分发（§5.3/§5.9/§5.10）
# ---------------------------------------------------------------------------


def _branch_payload(payload: dict[str, object], field_path: str) -> None:
    _keys(payload, {"source_conversation_id", "source_message_id", "source_revision", "title"}, field_path)
    _uuid(payload, "source_conversation_id", field_path)
    _uuid(payload, "source_message_id", field_path)
    _int(payload, "source_revision", field_path, minimum=1)
    _text(payload, "title", field_path)


def _draft_payload(payload: dict[str, object], field_path: str) -> None:
    _keys(
        payload,
        {
            "text",
            "mode",
            "model_choice",
            "manual_skills",
            "sources",
            "path_id",
            "path_revision",
            "as_of",
            "as_of_basis",
            "return_ref",
        },
        field_path,
    )
    _text(payload, "text", field_path)
    _enum(payload["mode"], ("discuss", "plan", "execute"), f"{field_path}.mode")
    _model_choice_obj(payload["model_choice"], f"{field_path}.model_choice", child=True)
    _ref_array(payload, "manual_skills", field_path)
    _ref_array(payload, "sources", field_path)
    _uuid(payload, "path_id", field_path)
    _int(payload, "path_revision", field_path, minimum=1)
    _nullable_date(payload, "as_of", field_path)
    basis = _enum(payload["as_of_basis"], ("explicit", "source", "not_applicable"), f"{field_path}.as_of_basis")
    if payload["as_of"] is None and basis != "not_applicable":
        _fail("date_required", f"{field_path}.as_of")
    if payload["return_ref"] is not None:
        _return_ref_obj(payload["return_ref"], f"{field_path}.return_ref")


def _export_payload(payload: dict[str, object], field_path: str) -> None:
    _keys(
        payload,
        {"artifact_id", "artifact_revision", "format", "destination", "expected_target_digest", "confirm_overwrite"},
        field_path,
    )
    _uuid(payload, "artifact_id", field_path)
    _int(payload, "artifact_revision", field_path, minimum=1)
    _enum(payload["format"], ("md", "csv", "xlsx"), f"{field_path}.format")
    destination = _text(payload, "destination", field_path)
    if not os.path.isabs(destination) or destination.startswith("\\\\"):
        _fail("invalid_path", f"{field_path}.destination")
    _nullable_digest(payload, "expected_target_digest", field_path)
    _bool(payload, "confirm_overwrite", field_path)


def _delete_payload(payload: dict[str, object], field_path: str) -> None:
    _keys(payload, {"target", "use_revision", "confirmation_digest"}, field_path)
    target = _obj(payload["target"], f"{field_path}.target")
    _keys(target, {"kind", "object_id", "revision"}, f"{field_path}.target")
    _enum(target["kind"], ("resource", "conversation"), f"{field_path}.target.kind")
    _uuid(target, "object_id", f"{field_path}.target")
    _int(target, "revision", f"{field_path}.target", minimum=1)
    _int(payload, "use_revision", field_path, minimum=1)
    _digest(payload, "confirmation_digest", field_path)


def _mcp_tools_payload(payload: dict[str, object], field_path: str) -> None:
    _keys(payload, {"service", "discovery_revision", "selected_tools"}, field_path)
    _ref(payload, "service", field_path)
    _digest(payload, "discovery_revision", field_path)
    _tool_selections(payload, field_path)


def _mcp_operation_payload(payload: dict[str, object], field_path: str) -> None:
    _keys(payload, {"ref", "action"}, field_path)
    _ref(payload, "ref", field_path)
    _enum(payload["action"], ("start", "connect", "test", "discover", "stop", "disconnect"), f"{field_path}.action")


def _cancel_delivery_payload(payload: dict[str, object], field_path: str) -> None:
    _keys(payload, {"delivery_operation_id", "expected_delivery_revision"}, field_path)
    _uuid(payload, "delivery_operation_id", field_path)
    _int(payload, "expected_delivery_revision", field_path, minimum=1)


def _decision_presentation_payload(payload: dict[str, object], field_path: str) -> None:
    _keys(payload, {"action", "through_seq", "decision_id"}, field_path)
    _enum(payload["action"], ("later", "reopen"), f"{field_path}.action")
    _int(payload, "through_seq", field_path, minimum=0)
    _nullable_uuid(payload, "decision_id", field_path)


def _conversation_payload(payload: dict[str, object], field_path: str) -> None:
    _keys(payload, {"title", "pinned", "archived"}, field_path)
    _text(payload, "title", field_path, max_len=200)
    _bool(payload, "pinned", field_path)
    _bool(payload, "archived", field_path)


def _retry_payload(payload: dict[str, object], field_path: str) -> None:
    _keys(payload, {"new_key", "reuse_digest"}, field_path)
    new_key = payload["new_key"]
    if new_key is not None:
        key = _obj(new_key, f"{field_path}.new_key")
        _keys(key, {"domain", "source_module", "request_key"}, f"{field_path}.new_key")
        for name in ("domain", "source_module", "request_key"):
            _text(key, name, f"{field_path}.new_key", max_len=128)
    _digest(payload, "reuse_digest", field_path)


def _supplement_payload(payload: dict[str, object], field_path: str) -> None:
    _keys(payload, {"text", "sources", "affects_input"}, field_path)
    _text(payload, "text", field_path)
    _ref_array(payload, "sources", field_path)
    _bool(payload, "affects_input", field_path)


def _answer_decision_payload(payload: dict[str, object], field_path: str) -> None:
    _keys(payload, {"decision_id", "decision_revision", "native_request_id", "scope_digest", "answer"}, field_path)
    _uuid(payload, "decision_id", field_path)
    _int(payload, "decision_revision", field_path, minimum=1)
    _text(payload, "native_request_id", field_path)
    _digest(payload, "scope_digest", field_path)
    _obj(payload["answer"], f"{field_path}.answer")


def _continue_payload(payload: dict[str, object], field_path: str) -> None:
    _keys(payload, {"continuation_ref"}, field_path)
    ref = payload["continuation_ref"]
    if not isinstance(ref, str):
        _fail("invalid_type", f"{field_path}.continuation_ref")
    decoded = decode_json_doc(ref)
    record = _obj(decoded, f"{field_path}.continuation_ref")
    if record.get("schema_version") != 3:
        _fail("unsupported_schema", f"{field_path}.continuation_ref.schema_version")


def _terminate_payload(payload: dict[str, object], field_path: str) -> None:
    _keys(payload, {"reason"}, field_path)
    _text(payload, "reason", field_path)


def _prompt_restore_payload(payload: dict[str, object], field_path: str) -> None:
    _keys(payload, {"prompt", "default_asset_key", "asset_version", "asset_digest"}, field_path)
    _ref(payload, "prompt", field_path)
    _text(payload, "default_asset_key", field_path)
    _text(payload, "asset_version", field_path)
    _digest(payload, "asset_digest", field_path)


def _skill_import_payload(payload: dict[str, object], field_path: str) -> None:
    _keys(payload, {"inspection_id", "inspection_digest", "expected_resource"}, field_path)
    _uuid(payload, "inspection_id", field_path)
    _digest(payload, "inspection_digest", field_path)
    if payload["expected_resource"] is not None:
        _ref(payload, "expected_resource", field_path)


def _agent_request_doc(payload: dict[str, object], field_path: str) -> None:
    required = {
        "key",
        "conversation_id",
        "intent_kind",
        "text",
        "mode",
        "as_of",
        "context",
        "sources",
        "roles",
        "manual_skills",
        "mcp",
        "output_contract",
        "business_owner",
        "business_run",
        "business_slot",
        "reviewed_plan",
        "plan_review_revision",
        "plan_review_digest",
        "as_of_basis",
    }
    _keys(payload, required, field_path)
    key = _obj(payload["key"], f"{field_path}.key")
    _keys(key, {"domain", "source_module", "request_key"}, f"{field_path}.key")
    for name in ("domain", "source_module", "request_key"):
        _text(key, name, f"{field_path}.key", max_len=128)
    _nullable_uuid(payload, "conversation_id", field_path)
    _enum(
        payload["intent_kind"],
        ("ordinary", "historical_resend", "plan_start", "plan_revision", "business"),
        f"{field_path}.intent_kind",
    )
    _text(payload, "text", field_path)
    _enum(payload["mode"], ("discuss", "plan", "execute"), f"{field_path}.mode")
    _nullable_date(payload, "as_of", field_path)
    context = _obj(payload["context"], f"{field_path}.context")
    _keys(
        context,
        {"path_id", "path_revision", "anchor_message_id", "predecessor_intents", "old_evidence"},
        f"{field_path}.context",
    )
    _nullable_uuid(context, "path_id", f"{field_path}.context")
    _nullable_int(context, "path_revision", f"{field_path}.context", minimum=1)
    _nullable_uuid(context, "anchor_message_id", f"{field_path}.context")
    for index, item in enumerate(_array(context, "predecessor_intents", f"{field_path}.context")):
        _uuid_value(item, f"{field_path}.context.predecessor_intents[{index}]")
    _ref_array(context, "old_evidence", f"{field_path}.context")
    _ref_array(payload, "sources", field_path)
    roles = _array(payload, "roles", field_path)
    for index, role in enumerate(roles):
        path = f"{field_path}.roles[{index}]"
        item = _obj(role, path)
        _keys(
            item, {"role_key", "revision", "prompt", "model", "default_skills", "default_mcp", "enabled_skills"}, path
        )
        _text(item, "role_key", path, max_len=128)
        _int(item, "revision", path, minimum=1)
        _ref(item, "prompt", path)
        _model_choice_obj(item["model"], f"{path}.model", child=True)
        enabled = _ref_array(item, "default_skills", path)
        valid = _ref_array(item, "enabled_skills", path)
        _ref_array(item, "default_mcp", path)
        _subset_refs(enabled, valid, f"{path}.default_skills")
    _ref_array(payload, "manual_skills", field_path)
    _ref_array(payload, "mcp", field_path)
    _enum(payload["output_contract"], OUTPUT_CONTRACTS, f"{field_path}.output_contract")
    for name in ("business_owner", "business_run", "business_slot"):
        _nullable_text(payload, name, field_path)
    if payload["reviewed_plan"] is not None:
        _ref(payload, "reviewed_plan", field_path)
    _nullable_int(payload, "plan_review_revision", field_path, minimum=1)
    _nullable_digest(payload, "plan_review_digest", field_path)
    basis = _enum(payload["as_of_basis"], ("explicit", "source", "not_applicable"), f"{field_path}.as_of_basis")
    if payload["as_of"] is None and basis != "not_applicable":
        _fail("date_required", f"{field_path}.as_of")


def validate_payload(kind: str, doc: JsonDoc) -> None:
    if not isinstance(doc, str):
        _fail("invalid_type", "$")
    if kind in ("source_metadata", "source_registration", "prepared_source", "delivery_view", "decision_view"):
        decoded = decode_json_doc(doc)
        if kind == "source_metadata":
            _source_metadata_obj(decoded, "$")
        elif kind == "source_registration":
            _source_registration_obj(decoded)
        elif kind == "prepared_source":
            _prepared_source_obj(decoded)
        elif kind == "delivery_view":
            _delivery_view_obj(decoded)
        else:
            _decision_view_obj(decoded)
        return
    if kind in ("source", "role", "prompt", "skill", "mcp", "summary", "artifact"):
        _resource_payload_obj(kind, decode_json_doc(doc))
        return
    if kind == "plan":
        validate_plan(doc)
        return
    if kind == "return_ref":
        validate_return_ref(doc)
        return
    _MANAGEMENT_PAYLOADS: dict[str, Any] = {
        "interrupt": lambda payload, path: _keys(payload, set(), path),
        "withdraw_queued": lambda payload, path: _keys(payload, set(), path),
        "continue": _continue_payload,
        "terminate": _terminate_payload,
        "retry": _retry_payload,
        "revise_queued": lambda payload, path: (
            _keys(payload, {"request"}, path),
            _agent_request_doc(_obj(payload["request"], f"{path}.request"), f"{path}.request"),
        ),
        "supplement": _supplement_payload,
        "answer_decision": _answer_decision_payload,
        "conversation": _conversation_payload,
        "draft": _draft_payload,
        "branch": _branch_payload,
        "prompt_restore": _prompt_restore_payload,
        "skill_import": _skill_import_payload,
        "mcp_tools": _mcp_tools_payload,
        "mcp_operation": _mcp_operation_payload,
        "export": _export_payload,
        "delete": _delete_payload,
        "cancel_delivery": _cancel_delivery_payload,
        "decision_presentation": _decision_presentation_payload,
    }
    handler = _MANAGEMENT_PAYLOADS.get(kind)
    if handler is None:
        _fail("unsupported_schema", "kind")
    payload = _obj(decode_json_doc(doc), "$")
    handler(payload, "$")
