"""同一任务的多模型独立分析与结果对比（F37/SQ-08）。

职责边界（DD-F37、REQ F37、UX P07）：

- **共同输入一次冻结**：组内各路共享同一份规范 manifest（任务正文、对象/资料
  身份与版本、用途、截止时点、可见历史前缀、角色 Prompt、Skills/MCP 范围、
  输出要求），manifest 摘要即组身份；冻结后不因排队或开始时间不同重取资料
  （R24），各路只携带该 manifest，不各自重算。
- **逐路身份/固定连接**：每路按所选连接稳定身份派生 ``lane_ref``，manifest 内
  冻结 ``connection_id`` / ``connection_revision`` 及连接授权引用（沿用 M15
  精确授权语义）；运行期只允许使用该连接，禁止替换（:func:`require_lane_connection`）。
- **独立完整任务排队**：每路是一次独立总执行（``comparison_lane``），按 D05
  全局总位与同会话单总执行限逐路排队；某路重试只新建本路轮次（
  :func:`plan_lane_retry`），不重跑其他路。
- **组收敛**：只按各路既有事实收敛（:func:`converge_comparison_group`）；未启动/
  排队/等待/未知保持未结束，``closed_unknown`` 显示「执行已结束、部分结果未确认」，
  不伪造成功/失败（R21）。
- **生产编排**：:func:`drive_comparison_group` 逐路启动/观察（单路启动失败隔离，
  取消传播），:class:`ComparisonRunReceipt` 只含逐路执行事实，组本身没有执行
  身份，不能计为一次完整成功。
- **逐路费用**：每路只读 :class:`CostProjection`；组只给「已计价合计 + 缺口」，
  未计价/预留/未知不拼成完整总费用（R21）。
- **只读比较/导出**：:func:`build_comparison_view` / :func:`comparison_export_rows`
  只消费已保存事实，零模型/工具调用、零新派发。

本模块不新增表列、不建第二调度：持久事实仍由 ``AgentDao`` 事务承载，组/路引用
复用 ``agent_execution`` 既有列，共同 manifest 复用命令 ``request_payload``。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Final, NoReturn

from core.agent_types import (
    DISPLAY_STATES,
    FROZEN_SHARED_INPUT_KEYS,
    NON_RECOVERABLE_DISPLAY_STATES,
    AcceptRequest,
    AgentValidationError,
    CanonicalJson,
    CommitState,
    CostProjection,
    Mode,
    Purpose,
    SourceKind,
    SourceVersionRef,
    is_digest,
    json_doc,
    require_identity,
    sha256_hex,
    stable_id,
)

#: 共同 manifest 结构版本（进入规范摘要，版本演进不改写旧组身份）。
COMPARISON_MANIFEST_VERSION: Final[int] = 1

#: 至少两个具体连接才构成对比（单连接是普通研究，不自动开启比较）。
COMPARISON_MIN_LANES: Final[int] = 2

#: D05：全局推进中的总执行上限（比较路每路按一次独立总执行计量）。
COMPARISON_TOTAL_SLOTS: Final[int] = 2

#: D05/RR02：同一主会话同时只允许一个未结束总执行。
COMPARISON_SESSION_SLOTS: Final[int] = 1

#: 组停止原因：共同资料被用户停止使用（F30）。
STOP_REASON_SHARED_SOURCE: Final[str] = "shared_source_stopped"
#: 组停止原因：用户明确结束整组。
STOP_REASON_USER_ENDED_GROUP: Final[str] = "user_ended_group"

#: 组费用状态：逐路账本事实单独保留，组只提供「已计价合计」与缺口标记。
COMPARISON_COST_KNOWN: Final[str] = "known"
COMPARISON_COST_PARTIAL: Final[str] = "partial"
COMPARISON_COST_RESERVED: Final[str] = "reserved"
COMPARISON_COST_UNKNOWN: Final[str] = "unknown"
COMPARISON_COST_FAILED: Final[str] = "failed"

#: 路启动失败（未派发/受理失败）的观察备注前缀；仅承载错误码，不含正文。
DISPATCH_FAILED_PREFIX: Final[str] = "dispatch_failed:"
ACCEPT_FAILED_PREFIX: Final[str] = "accept_failed:"

#: 校验码：同组键携不同完整冻结 manifest（模型集合/连接版本等任一维度不同）。
GROUP_MANIFEST_CONFLICT: Final[str] = "group_manifest_conflict"
#: 校验码：读到的 manifest 未覆盖该组已持久化的全部路（旧版/部分写入，非权威）。
GROUP_MANIFEST_STALE: Final[str] = "group_manifest_stale"
#: 校验码：本路冻结参数缺失/非法（不得回退当前设置重解析）。
LANE_PARAMETERS_MISSING: Final[str] = "lane_parameters_missing"

_MANIFEST_KEYS: Final = frozenset(
    {"schema_version", "group_ref", "user_scope", "session_id", "source_module", "input_digest", "shared", "lanes"}
)
#: 共同输入冻结段（正本由 core 承载，受理侧同一合同校验，避免两处漂移）。
_SHARED_KEYS: Final = FROZEN_SHARED_INPUT_KEYS
_LANE_KEYS: Final = frozenset(
    {
        "lane_ref",
        "business_request_id",
        "client_command_id",
        "connection_id",
        "connection_revision",
        "connection_target_fingerprint",
        "connection_purpose",
        "connection_scope_version",
        "verified_connection_grant_id",
        "display_name",
    }
)

_TERMINAL_STATES: Final[frozenset[str]] = NON_RECOVERABLE_DISPLAY_STATES
#: 已结束但结果未确认（显示「执行已结束，部分结果未确认」）。
_UNCONFIRMED_STATES: Final[frozenset[str]] = frozenset({"closed_unknown", "ended"})


def _fail(code: str, field_path: str) -> NoReturn:
    raise AgentValidationError(code, field_path)


def _require_text(value: object, path: str) -> str:
    if not isinstance(value, str) or value == "":
        _fail("invalid_text", path)
    return value


def _opt_text(value: object, path: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, path)


def _opt_digest(value: object, path: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not is_digest(value):
        _fail("invalid_digest", path)
    return value


def _require_digest(value: object, path: str) -> str:
    if not isinstance(value, str) or not is_digest(value):
        _fail("invalid_digest", path)
    return value


def _require_revision(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _fail("invalid_revision", path)
    return value


def _require_position(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail("invalid_revision", path)
    return value


def _str_tuple(value: object, path: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        _fail("invalid_type", path)
    seen: set[str] = set()
    for index, item in enumerate(value):
        text = _require_text(item, f"{path}[{index}]")
        if text in seen:
            _fail("duplicate_ref", f"{path}[{index}]")
        seen.add(text)
    return value


def _ref_pairs(value: object, path: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, tuple):
        _fail("invalid_type", path)
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(value):
        if not isinstance(item, tuple) or len(item) != 2:
            _fail("invalid_type", f"{path}[{index}]")
        ref = (_require_text(item[0], f"{path}[{index}][0]"), _require_text(item[1], f"{path}[{index}][1]"))
        if ref in seen:
            _fail("duplicate_ref", f"{path}[{index}]")
        seen.add(ref)
    return value


@dataclass(frozen=True, slots=True)
class ComparisonLaneSelection:
    """用户所选具体连接（F37：至少两个；同名不同 ID 按稳定身份区分）。

    ``connection_revision`` 与连接授权引用字段为**冻结**值：运行期只核不换
    （M15 精确授权语义），不得按当前设置重解析成另一连接。
    """

    connection_id: str
    connection_revision: int
    target_fingerprint: str
    connection_purpose: str
    scope_version: int
    display_name: str
    verified_connection_grant_id: str | None = None


@dataclass(frozen=True, slots=True)
class ComparisonSharedInput:
    """组内共同的完整任务输入（提交时一次冻结，含摘要）。

    ``source_refs`` 为共同原始资料身份/版本（各路可引用同一冻结快照）；不可变
    容器保证冻结后调用方再改原集合也不影响已提交输入（排队中资料变化不混入）。
    """

    text: str
    mode: Mode
    purpose: Purpose
    cutoff: str | None = None
    source_refs: tuple[SourceVersionRef, ...] = ()
    visible_history_message_ids: tuple[str, ...] = ()
    visible_history_artifact_refs: tuple[tuple[str, str], ...] = ()
    role_prompt_refs: tuple[tuple[str, str], ...] = ()
    skill_refs: tuple[tuple[str, str], ...] = ()
    mcp_scope_digest: str | None = None
    output_requirements: str | None = None


@dataclass(frozen=True, slots=True)
class ComparisonLaneFrozen:
    """一路独立完整任务的身份（固定连接 + 本路意图键/命令键）。"""

    lane_ref: str
    business_request_id: str
    client_command_id: str
    selection: ComparisonLaneSelection


@dataclass(frozen=True, slots=True)
class FrozenComparisonGroup:
    """共同输入一次冻结后的比较组（所有路共享同一 manifest 字节）。"""

    user_scope: str
    session_id: str
    source_module: str
    group_ref: str
    input_digest: str
    manifest_payload: CanonicalJson
    manifest_digest: str
    shared: ComparisonSharedInput
    lanes: tuple[ComparisonLaneFrozen, ...]


@dataclass(frozen=True, slots=True)
class ComparisonLaneRetry:
    """某一路单独重试的轮次身份（只含目标路，不重跑其他路）。"""

    group_ref: str
    round_id: str
    lane_ref: str
    business_request_id: str
    client_command_id: str
    manifest_payload: CanonicalJson


def _require_exact_keys(value: Mapping[str, object], required: frozenset[str], path: str) -> None:
    missing = sorted(required - value.keys())
    if missing:
        _fail("missing_field", f"{path}.{missing[0]}")
    extra = sorted(value.keys() - required)
    if extra:
        _fail("unknown_field", f"{path}.{extra[0]}")


def _source_wire(ref: SourceVersionRef) -> dict[str, str]:
    return {
        "source_kind": ref.source_kind.value,
        "source_id": require_identity(ref.source_id, "$.shared.source_refs.source_id"),
        "source_version": require_identity(ref.source_version, "$.shared.source_refs.source_version"),
        "source_digest": _require_digest(ref.source_digest, "$.shared.source_refs.source_digest"),
    }


def _shared_wire(shared: ComparisonSharedInput) -> dict[str, object]:
    """共同输入规范 wire（摘要唯一事实源；不改写正文/Unicode）。"""

    if not isinstance(shared.mode, Mode):
        _fail("invalid_tag", "$.shared.mode")
    if not isinstance(shared.purpose, Purpose):
        _fail("invalid_tag", "$.shared.purpose")
    return {
        "text": _require_text(shared.text, "$.shared.text"),
        "mode": shared.mode.value,
        "purpose": shared.purpose.value,
        "cutoff": _opt_text(shared.cutoff, "$.shared.cutoff"),
        "source_refs": [_source_wire(ref) for ref in shared.source_refs],
        "visible_history_message_ids": list(
            _str_tuple(shared.visible_history_message_ids, "$.shared.visible_history_message_ids")
        ),
        "visible_history_artifact_refs": [
            list(ref)
            for ref in _ref_pairs(shared.visible_history_artifact_refs, "$.shared.visible_history_artifact_refs")
        ],
        "role_prompt_refs": [list(ref) for ref in _ref_pairs(shared.role_prompt_refs, "$.shared.role_prompt_refs")],
        "skill_refs": [list(ref) for ref in _ref_pairs(shared.skill_refs, "$.shared.skill_refs")],
        "mcp_scope_digest": _opt_digest(shared.mcp_scope_digest, "$.shared.mcp_scope_digest"),
        "output_requirements": _opt_text(shared.output_requirements, "$.shared.output_requirements"),
    }


def _selection_wire(selection: ComparisonLaneSelection) -> dict[str, object]:
    return {
        "connection_id": require_identity(selection.connection_id, "$.lanes.connection_id"),
        "connection_revision": _require_revision(selection.connection_revision, "$.lanes.connection_revision"),
        "connection_target_fingerprint": require_identity(
            selection.target_fingerprint, "$.lanes.connection_target_fingerprint"
        ),
        "connection_purpose": require_identity(selection.connection_purpose, "$.lanes.connection_purpose"),
        "connection_scope_version": _require_position(selection.scope_version, "$.lanes.connection_scope_version"),
        "verified_connection_grant_id": _opt_text(
            selection.verified_connection_grant_id, "$.lanes.verified_connection_grant_id"
        ),
        "display_name": _require_text(selection.display_name, "$.lanes.display_name"),
    }


def _dedupe_lanes(lanes: tuple[ComparisonLaneSelection, ...]) -> tuple[ComparisonLaneSelection, ...]:
    """同一连接重复选择只保留一项；同名不同 ID 是不同路，不按名字合并。"""

    if not isinstance(lanes, tuple):
        _fail("invalid_type", "$.lanes")
    seen: set[str] = set()
    kept: list[ComparisonLaneSelection] = []
    for index, lane in enumerate(lanes):
        if not isinstance(lane, ComparisonLaneSelection):
            _fail("invalid_type", f"$.lanes[{index}]")
        connection_id = require_identity(lane.connection_id, f"$.lanes[{index}].connection_id")
        if connection_id in seen:
            continue
        seen.add(connection_id)
        _selection_wire(lane)
        kept.append(lane)
    return tuple(kept)


def freeze_comparison_group(
    *,
    user_scope: str,
    session_id: str,
    source_module: str,
    group_command_id: str,
    shared: ComparisonSharedInput,
    lanes: tuple[ComparisonLaneSelection, ...],
) -> FrozenComparisonGroup:
    """一次冻结共同输入并派生各路身份（重复连接去重；不足两路拒绝）。

    所有路共享同一 ``manifest_payload``（规范 JSON）与 ``manifest_digest``；各路
    只以 ``business_request_id``/``client_command_id``/``lane_ref`` 区分，故排队
    或开始时间不同不会重取资料。冻结对象不可变，调用方后续改动原输入不影响本组。
    """

    scope = require_identity(user_scope, "$.user_scope")
    session = require_identity(session_id, "$.session_id")
    module = require_identity(source_module, "$.source_module")
    command_id = require_identity(group_command_id, "$.group_command_id")
    shared_wire = _shared_wire(shared)
    input_digest = sha256_hex(json_doc(shared_wire))
    unique_lanes = _dedupe_lanes(lanes)
    if len(unique_lanes) < COMPARISON_MIN_LANES:
        _fail("insufficient_lanes", "$.lanes")
    group_ref = stable_id("comparison_group", [scope, session, command_id])
    frozen_lanes: list[ComparisonLaneFrozen] = []
    lane_wires: list[dict[str, object]] = []
    for selection in unique_lanes:
        lane_ref = stable_id("comparison_lane", [group_ref, selection.connection_id])
        lane = ComparisonLaneFrozen(
            lane_ref=lane_ref,
            business_request_id=stable_id("comparison_intent", [group_ref, lane_ref]),
            client_command_id=stable_id("comparison_command", [group_ref, lane_ref]),
            selection=selection,
        )
        frozen_lanes.append(lane)
        lane_wires.append(
            {
                **_selection_wire(selection),
                "lane_ref": lane_ref,
                "business_request_id": lane.business_request_id,
                "client_command_id": lane.client_command_id,
            }
        )
    manifest_payload = json_doc(
        {
            "schema_version": COMPARISON_MANIFEST_VERSION,
            "group_ref": group_ref,
            "user_scope": scope,
            "session_id": session,
            "source_module": module,
            "input_digest": input_digest,
            "shared": shared_wire,
            "lanes": lane_wires,
        }
    )
    return FrozenComparisonGroup(
        user_scope=scope,
        session_id=session,
        source_module=module,
        group_ref=group_ref,
        input_digest=input_digest,
        manifest_payload=manifest_payload,
        manifest_digest=sha256_hex(manifest_payload),
        shared=shared,
        lanes=tuple(frozen_lanes),
    )


def _decode_source_wire(value: object, path: str) -> SourceVersionRef:
    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_exact_keys(value, frozenset({"source_kind", "source_id", "source_version", "source_digest"}), path)
    kind_raw = value["source_kind"]
    if not isinstance(kind_raw, str):
        _fail("invalid_type", f"{path}.source_kind")
    try:
        kind = SourceKind(kind_raw)
    except ValueError:
        _fail("invalid_tag", f"{path}.source_kind")
    return SourceVersionRef(
        source_kind=kind,
        source_id=require_identity(value["source_id"], f"{path}.source_id"),
        source_version=require_identity(value["source_version"], f"{path}.source_version"),
        source_digest=_require_digest(value["source_digest"], f"{path}.source_digest"),
    )


def _decode_shared(value: object) -> ComparisonSharedInput:
    path = "$.comparison_manifest.shared"
    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_exact_keys(value, _SHARED_KEYS, path)
    mode_raw = value["mode"]
    purpose_raw = value["purpose"]
    if not isinstance(mode_raw, str):
        _fail("invalid_type", f"{path}.mode")
    if not isinstance(purpose_raw, str):
        _fail("invalid_type", f"{path}.purpose")
    try:
        mode = Mode(mode_raw)
        purpose = Purpose(purpose_raw)
    except ValueError:
        _fail("invalid_tag", path)
    raw_sources = value["source_refs"]
    if not isinstance(raw_sources, list):
        _fail("invalid_type", f"{path}.source_refs")
    raw_messages = value["visible_history_message_ids"]
    if not isinstance(raw_messages, list):
        _fail("invalid_type", f"{path}.visible_history_message_ids")
    shared = ComparisonSharedInput(
        text=_require_text(value["text"], f"{path}.text"),
        mode=mode,
        purpose=purpose,
        cutoff=_opt_text(value["cutoff"], f"{path}.cutoff"),
        source_refs=tuple(
            _decode_source_wire(item, f"{path}.source_refs[{index}]") for index, item in enumerate(raw_sources)
        ),
        visible_history_message_ids=_str_tuple(
            tuple(item for item in raw_messages), f"{path}.visible_history_message_ids"
        ),
        visible_history_artifact_refs=_wire_ref_pairs(
            value["visible_history_artifact_refs"], f"{path}.visible_history_artifact_refs"
        ),
        role_prompt_refs=_wire_ref_pairs(value["role_prompt_refs"], f"{path}.role_prompt_refs"),
        skill_refs=_wire_ref_pairs(value["skill_refs"], f"{path}.skill_refs"),
        mcp_scope_digest=_opt_digest(value["mcp_scope_digest"], f"{path}.mcp_scope_digest"),
        output_requirements=_opt_text(value["output_requirements"], f"{path}.output_requirements"),
    )
    # 规范字节必须与重算一致（不信任存储摘要之外的形态变化）。
    _shared_wire(shared)
    return shared


def _wire_ref_pairs(value: object, path: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        _fail("invalid_type", path)
    pairs: list[tuple[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, list) or len(item) != 2:
            _fail("invalid_type", f"{path}[{index}]")
        pairs.append((_require_text(item[0], f"{path}[{index}][0]"), _require_text(item[1], f"{path}[{index}][1]")))
    return _ref_pairs(tuple(pairs), path)


def _decode_lane(value: object, path: str) -> ComparisonLaneFrozen:
    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_exact_keys(value, _LANE_KEYS, path)
    selection = ComparisonLaneSelection(
        connection_id=require_identity(value["connection_id"], f"{path}.connection_id"),
        connection_revision=_require_revision(value["connection_revision"], f"{path}.connection_revision"),
        target_fingerprint=require_identity(
            value["connection_target_fingerprint"], f"{path}.connection_target_fingerprint"
        ),
        connection_purpose=require_identity(value["connection_purpose"], f"{path}.connection_purpose"),
        scope_version=_require_position(value["connection_scope_version"], f"{path}.connection_scope_version"),
        display_name=_require_text(value["display_name"], f"{path}.display_name"),
        verified_connection_grant_id=_opt_text(
            value["verified_connection_grant_id"], f"{path}.verified_connection_grant_id"
        ),
    )
    lane_ref = require_identity(value["lane_ref"], f"{path}.lane_ref")
    business_request_id = require_identity(value["business_request_id"], f"{path}.business_request_id")
    client_command_id = require_identity(value["client_command_id"], f"{path}.client_command_id")
    return ComparisonLaneFrozen(
        lane_ref=lane_ref,
        business_request_id=business_request_id,
        client_command_id=client_command_id,
        selection=selection,
    )


def decode_comparison_manifest(payload: object) -> FrozenComparisonGroup:
    """严格解码已保存的共同 manifest（历史按组找回；不按当前设置重推）。"""

    path = "$.comparison_manifest"
    if not isinstance(payload, dict):
        _fail("invalid_type", path)
    _require_exact_keys(payload, _MANIFEST_KEYS, path)
    version = payload["schema_version"]
    # 版本严格整数：True/1.0/"1" 都不是合法版本（I69）。
    if isinstance(version, bool) or not isinstance(version, int) or version != COMPARISON_MANIFEST_VERSION:
        _fail("unsupported_version", f"{path}.schema_version")
    group_ref = require_identity(payload["group_ref"], f"{path}.group_ref")
    raw_lanes = payload["lanes"]
    if not isinstance(raw_lanes, list) or not raw_lanes:
        _fail("invalid_type", f"{path}.lanes")
    lanes = tuple(_decode_lane(item, f"{path}.lanes[{index}]") for index, item in enumerate(raw_lanes))
    shared = _decode_shared(payload["shared"])
    input_digest = _require_digest(payload["input_digest"], f"{path}.input_digest")
    if input_digest != sha256_hex(json_doc(_shared_wire(shared))):
        _fail("digest_mismatch", f"{path}.input_digest")
    canonical = json_doc(payload)
    return FrozenComparisonGroup(
        user_scope=require_identity(payload["user_scope"], f"{path}.user_scope"),
        session_id=require_identity(payload["session_id"], f"{path}.session_id"),
        source_module=require_identity(payload["source_module"], f"{path}.source_module"),
        group_ref=group_ref,
        input_digest=input_digest,
        manifest_payload=canonical,
        manifest_digest=sha256_hex(canonical),
        shared=shared,
        lanes=lanes,
    )


def build_lane_accept_request(
    group: FrozenComparisonGroup,
    lane: ComparisonLaneFrozen,
    *,
    expected_revision: int,
    node_id: str,
    workflow_id: str,
    source_display_name: str,
    business_request_id: str | None = None,
    client_command_id: str | None = None,
) -> AcceptRequest:
    """构造本路独立完整任务受理输入（共享 manifest，固定连接身份）。

    每路命令键与意图键不同（独立排队/重试），但 ``manifest_payload`` 与本组
    逐字节相同：同输入只冻结一次，后续设置/资料变化不进入已提交输入。

    ``workflow_id`` 是组命名空间（可读前缀），**不是**原生执行身份：原生身份按
    「组 + 路 + 本次意图」派生的稳定 ID 生成，保证两路不会共用同一 workflow，
    重试本路也自然得到新身份（A 路完成时 B 路重试不会命中 A 的执行）。
    """

    if lane not in group.lanes:
        _fail("lane_not_in_group", "$.lane")
    intent_id = require_identity(
        lane.business_request_id if business_request_id is None else business_request_id,
        "$.business_request_id",
    )
    lane_workflow = stable_id("comparison_lane_workflow", [group.group_ref, lane.lane_ref, intent_id])
    return AcceptRequest(
        user_scope=group.user_scope,
        session_id=group.session_id,
        source_module=group.source_module,
        business_request_id=intent_id,
        client_command_id=require_identity(
            lane.client_command_id if client_command_id is None else client_command_id,
            "$.client_command_id",
        ),
        expected_revision=expected_revision,
        node_id=node_id,
        workflow_id=f"{require_identity(workflow_id, '$.workflow_id')}:{lane_workflow}",
        mode=group.shared.mode,
        purpose=group.shared.purpose,
        text=group.shared.text,
        manifest_payload=group.manifest_payload,
        source_display_name=source_display_name,
        comparison_group_ref=group.group_ref,
        comparison_lane_ref=lane.lane_ref,
    )


def require_lane_connection(
    group: FrozenComparisonGroup,
    lane_ref: str,
    *,
    connection_id: str,
    connection_revision: int,
) -> ComparisonLaneFrozen:
    """运行期核本路连接：只接受冻结的准确连接 ID/版本，禁止替换（M15）。"""

    for lane in group.lanes:
        if lane.lane_ref == lane_ref:
            if (
                lane.selection.connection_id != connection_id
                or lane.selection.connection_revision != connection_revision
            ):
                _fail("connection_mismatch", f"$.lanes[{lane_ref}]")
            return lane
    _fail("unknown_lane", f"$.lanes[{lane_ref}]")


def plan_lane_retry(group: FrozenComparisonGroup, lane_ref: str, *, round_id: str) -> ComparisonLaneRetry:
    """B 路单独重试：只返回本路新轮身份，不触发其他路执行。"""

    lane = next((item for item in group.lanes if item.lane_ref == lane_ref), None)
    if lane is None:
        _fail("unknown_lane", f"$.lanes[{lane_ref}]")
    round_text = require_identity(round_id, "$.round_id")
    return ComparisonLaneRetry(
        group_ref=group.group_ref,
        round_id=round_text,
        lane_ref=lane.lane_ref,
        business_request_id=stable_id("comparison_lane_retry", [group.group_ref, lane.lane_ref, round_text]),
        client_command_id=stable_id("comparison_lane_retry_command", [group.group_ref, lane.lane_ref, round_text]),
        manifest_payload=group.manifest_payload,
    )


@dataclass(frozen=True, slots=True)
class LaneQueueDecision:
    """单路排队决定：``dispatch`` 立即派发，``queued`` 说明准确阻塞限额。"""

    lane_ref: str
    decision: str
    reason: str | None


def plan_lane_queue(
    group: FrozenComparisonGroup,
    *,
    active_total_executions: int,
    active_session_executions: int = 0,
    total_slots: int = COMPARISON_TOTAL_SLOTS,
    session_slots: int = COMPARISON_SESSION_SLOTS,
) -> tuple[LaneQueueDecision, ...]:
    """按 D05 全局总位 + 同会话单总执行逐路排队（用户所选顺序，不并行抢跑）。

    只计算决定，不写事实、不派发；调用方按 ``dispatch`` 顺序启动且每次状态变化后
    重算。等待/暂停中的路仍占位（不早释在途占用）。``queued`` 的 ``reason`` 是该路
    被哪个限额挡住：仅全局满为总位码、仅会话满为会话码、两者同时满则两码并列
    （单报其一会让调用方误以为只放宽一种限额即可派发）。
    """

    if isinstance(active_total_executions, bool) or not isinstance(active_total_executions, int):
        _fail("invalid_type", "$.active_total_executions")
    if active_total_executions < 0:
        _fail("invalid_range", "$.active_total_executions")
    if isinstance(active_session_executions, bool) or not isinstance(active_session_executions, int):
        _fail("invalid_type", "$.active_session_executions")
    if active_session_executions < 0:
        _fail("invalid_range", "$.active_session_executions")
    if isinstance(total_slots, bool) or not isinstance(total_slots, int) or total_slots < 1:
        _fail("invalid_range", "$.total_slots")
    if isinstance(session_slots, bool) or not isinstance(session_slots, int) or session_slots < 1:
        _fail("invalid_range", "$.session_slots")
    global_free = max(0, total_slots - active_total_executions)
    session_free = max(0, session_slots - active_session_executions)
    free = min(global_free, session_free)
    if global_free <= 0 and session_free <= 0:
        reason = "d05_total_execution_slots+d05_session_single_total_execution"
    elif session_free <= global_free:
        reason = "d05_session_single_total_execution"
    else:
        reason = "d05_total_execution_slots"
    return tuple(
        LaneQueueDecision(
            lane_ref=lane.lane_ref,
            decision="dispatch" if index < free else "queued",
            reason=None if index < free else reason,
        )
        for index, lane in enumerate(group.lanes)
    )


@dataclass(frozen=True, slots=True)
class ComparisonLaneObservation:
    """一路已保存事实（只读投影输入；未知/未启动不填业务合法值，R21）。

    ``display_state=None`` 表示尚未观察到本路执行（未启动/排队无投影）；
    ``reliable_result`` 三态：``True`` 有可核成果、``False`` 可靠确证无成果、
    ``None`` 未知（不得据此判成功或失败）。``execution_id`` 为该路本次尝试的
    准确执行身份（只读费用引用的入口）；``cost`` 为该路原账本只读投影，
    ``None`` 表示尚未读取（未知，不等同免费）。
    """

    lane_ref: str
    display_state: str | None = None
    reliable_result: bool | None = None
    artifact_refs: tuple[tuple[str, str], ...] = ()
    note: str | None = None
    execution_id: str | None = None
    cost: CostProjection | None = None


def _validate_observation(observation: ComparisonLaneObservation, path: str) -> None:
    if not isinstance(observation, ComparisonLaneObservation):
        _fail("invalid_type", path)
    require_identity(observation.lane_ref, f"{path}.lane_ref")
    if observation.display_state is not None and observation.display_state not in DISPLAY_STATES:
        _fail("invalid_display_state", f"{path}.display_state")
    if observation.reliable_result is not None and not isinstance(observation.reliable_result, bool):
        _fail("invalid_type", f"{path}.reliable_result")
    _ref_pairs(observation.artifact_refs, f"{path}.artifact_refs")
    if observation.note is not None:
        _require_text(observation.note, f"{path}.note")
    if observation.execution_id is not None:
        _require_text(observation.execution_id, f"{path}.execution_id")
    if observation.cost is not None and not isinstance(observation.cost, CostProjection):
        _fail("invalid_type", f"{path}.cost")


@dataclass(frozen=True, slots=True)
class ComparisonLaneView:
    """一路只读比较视图（连接身份取自冻结 manifest，不按当前设置重推）。

    ``gap`` 为该路尚未覆盖的对象级缺口（``None`` 仅当该路确实完整）；``cost``
    为该路原账本只读投影（``None`` 表示本次未读/未知，禁止按 0 展示）。
    """

    lane_ref: str
    connection_id: str
    connection_revision: int
    display_name: str
    display_state: str | None
    reliable_result: bool | None
    artifact_refs: tuple[tuple[str, str], ...]
    reason: str
    execution_id: str | None = None
    gap: str | None = None
    cost: CostProjection | None = None


@dataclass(frozen=True, slots=True)
class ComparisonGroupView:
    """组收敛/覆盖/费用只读投影；未确认结果逐路保留，不以计数伪造结论。

    ``cost_state`` 只描述**已读到的事实**：``known`` 才是全部路已计价且无缺口；
    ``partial`` 为「已知合计 + 有未计价项」，``reserved`` 为有活跃预留未结算，
    ``unknown`` 为未读到/无可计价引用，``failed`` 为账本读取失败。``known_cny``
    始终只合计已读到的计价事实；为 ``None`` 才表示没有任何可用金额（禁止 0）。
    """

    group_ref: str
    manifest_digest: str
    group_state: str
    goal_coverage: str
    ended: bool
    selected_count: int
    complete_count: int
    partial_count: int
    failed_count: int
    pending_count: int
    not_started_count: int
    unconfirmed_count: int
    lanes: tuple[ComparisonLaneView, ...]
    cost_state: str = COMPARISON_COST_UNKNOWN
    known_cny: Decimal | None = None
    reserved_cny: Decimal | None = None
    unpriced_attempts: int = 0


def _observation_map(
    group: FrozenComparisonGroup, observations: tuple[ComparisonLaneObservation, ...]
) -> dict[str, ComparisonLaneObservation]:
    lane_ids = {lane.lane_ref for lane in group.lanes}
    mapped: dict[str, ComparisonLaneObservation] = {}
    for index, observation in enumerate(observations):
        _validate_observation(observation, f"$.observations[{index}]")
        if observation.lane_ref not in lane_ids:
            _fail("unknown_lane", f"$.observations[{index}].lane_ref")
        if observation.lane_ref in mapped:
            _fail("duplicate_lane_observation", f"$.observations[{index}].lane_ref")
        mapped[observation.lane_ref] = observation
    return mapped


#: 尚未启动的显示态（受理初值 ``queued`` / 计划态 ``planned``）。
_PRE_START_STATES: Final[frozenset[str]] = frozenset({"queued", "planned"})


def _lane_reason(state: str | None, reliable: bool | None, note: str | None = None) -> str:
    # 明确零派发原因优先：状态缺失或仍是受理初值（未推进）时，拒绝原因就是本路结论。
    if note is not None and (state is None or state in _PRE_START_STATES):
        if note.startswith(DISPATCH_FAILED_PREFIX):
            return "dispatch_failed"
        if note.startswith(ACCEPT_FAILED_PREFIX):
            return "accept_failed"
    if state is None:
        return "started_no_fact" if note == "started_no_fact" else "not_started"
    if state in _PRE_START_STATES:
        # 未启动（排队/计划）不是「保留成果」，与缺失观察同一口径，便于三态区分。
        return "not_started"
    if state in _UNCONFIRMED_STATES or (state in _TERMINAL_STATES and reliable is None):
        return "result_unconfirmed"
    if state == "complete":
        return "complete"
    if state == "partial":
        return "partial_result"
    if state == "failed":
        return "failed_no_result"
    if state == "withdrawn":
        return "withdrawn"
    return "preserve_result"


def _lane_gap(state: str | None, reliable: bool | None) -> str | None:
    """一路的对象级缺口（与 ``completion.py`` 的叶缺口口径一致）。

    ``None`` 只表示该路确实完整（``complete`` 且有可靠成果）；非终态返回
    ``no_terminal_fact``，未知结案返回 ``result_unconfirmed``，不得读成无缺口（R21）。
    """

    if state is None:
        return "no_terminal_fact"
    if state == "complete":
        return None if reliable is True else "result_unconfirmed"
    if state == "partial":
        return "missing_data"
    if state in {"failed", "withdrawn", "ended"}:
        return "no_result"
    if state == "closed_unknown":
        return "result_unconfirmed"
    if state in _TERMINAL_STATES:
        return "result_unconfirmed"
    return "no_terminal_fact"


def _group_cost(views: list[ComparisonLaneView]) -> tuple[str, Decimal | None, Decimal | None, int]:
    """逐路费用事实 → 组费用状态（不把未知拼成完整总费用，R21）。"""

    costs = [view.cost for view in views]
    if any(cost is None for cost in costs):
        observed = [cost for cost in costs if cost is not None]
        known = _sum_known(observed)
        return COMPARISON_COST_UNKNOWN, known, _sum_reserved(observed), _sum_unpriced(observed)
    concrete = [cost for cost in costs if cost is not None]
    if any(cost.read_state != "ok" for cost in concrete):
        # 读取失败不丢弃其他路的已计价事实：已知合计照常给出，缺口单列。
        return COMPARISON_COST_FAILED, _sum_known(concrete), _sum_reserved(concrete), _sum_unpriced(concrete)
    if _sum_unpriced(concrete) > 0:
        return COMPARISON_COST_PARTIAL, _sum_known(concrete), _sum_reserved(concrete), _sum_unpriced(concrete)
    if any(cost.known_cny is None for cost in concrete):
        return COMPARISON_COST_UNKNOWN, None, _sum_reserved(concrete), 0
    reserved = _sum_reserved(concrete)
    if reserved is not None and reserved > 0:
        return COMPARISON_COST_RESERVED, _sum_known(concrete), reserved, 0
    return COMPARISON_COST_KNOWN, _sum_known(concrete), reserved, 0


def _sum_known(costs: list[CostProjection]) -> Decimal | None:
    priced = [cost.known_cny for cost in costs if cost.read_state == "ok" and cost.known_cny is not None]
    return sum(priced, Decimal("0")) if priced else None


def _sum_reserved(costs: list[CostProjection]) -> Decimal | None:
    amounts = [cost.reserved_cny for cost in costs if cost.read_state == "ok" and cost.reserved_cny is not None]
    return sum(amounts, Decimal("0")) if amounts else None


def _sum_unpriced(costs: list[CostProjection]) -> int:
    return sum(cost.unpriced_attempts for cost in costs if cost.read_state == "ok")


def build_comparison_view(
    group: FrozenComparisonGroup, observations: tuple[ComparisonLaneObservation, ...]
) -> ComparisonGroupView:
    """只读组收敛：零模型调用、零新派发（仅消费已保存事实）。

    规则（DD-F37/REQ F37）：

    - 尚有未启动/排队/运行/等待/未知的路 → 组未结束（``queued``/``running``），
      覆盖 ``unknown``；
    - 全部路已有终态但存在 ``closed_unknown``/``ended`` 或结果未知 → 组
      ``closed_unknown``（执行已结束、部分结果未确认），**不**强判成功/失败；
    - 全部可核：全 ``complete`` → ``complete``/``full``；有可靠成果但未全完整 →
      ``partial``/``partial``；可靠确证无成果且全部 ``failed`` → ``failed``/``failed``；
      其余可靠无成果（撤回/结束）→ ``ended``/``none``。
    """

    mapped = _observation_map(group, observations)
    views: list[ComparisonLaneView] = []
    for lane in group.lanes:
        observation = mapped.get(lane.lane_ref) or ComparisonLaneObservation(lane_ref=lane.lane_ref)
        views.append(
            ComparisonLaneView(
                lane_ref=lane.lane_ref,
                connection_id=lane.selection.connection_id,
                connection_revision=lane.selection.connection_revision,
                display_name=lane.selection.display_name,
                display_state=observation.display_state,
                reliable_result=observation.reliable_result,
                artifact_refs=observation.artifact_refs,
                reason=_lane_reason(observation.display_state, observation.reliable_result, observation.note),
                execution_id=observation.execution_id,
                gap=_lane_gap(observation.display_state, observation.reliable_result),
                cost=observation.cost,
            )
        )
    cost_state, known_cny, reserved_cny, unpriced = _group_cost(views)
    observed_states = [view.display_state for view in views]
    pending = [view for view in views if view.display_state is None or view.display_state not in _TERMINAL_STATES]
    complete_count = sum(1 for view in views if view.display_state == "complete")
    partial_count = sum(1 for view in views if view.display_state == "partial")
    failed_count = sum(1 for view in views if view.display_state == "failed")
    not_started_count = sum(1 for view in views if view.reason == "not_started")
    if pending:
        running = any(
            state is not None and state not in _TERMINAL_STATES and state not in {"queued", "planned"}
            for state in observed_states
        ) or any(view.reason == "started_no_fact" for view in views)
        return ComparisonGroupView(
            group_ref=group.group_ref,
            manifest_digest=group.manifest_digest,
            group_state="running" if running else "queued",
            goal_coverage="unknown",
            ended=False,
            selected_count=len(views),
            complete_count=complete_count,
            partial_count=partial_count,
            failed_count=failed_count,
            pending_count=len(pending),
            not_started_count=not_started_count,
            unconfirmed_count=0,
            lanes=tuple(views),
            cost_state=cost_state,
            known_cny=known_cny,
            reserved_cny=reserved_cny,
            unpriced_attempts=unpriced,
        )
    unconfirmed = [view for view in views if view.display_state in _UNCONFIRMED_STATES or view.reliable_result is None]
    if unconfirmed:
        return ComparisonGroupView(
            group_ref=group.group_ref,
            manifest_digest=group.manifest_digest,
            group_state="closed_unknown",
            goal_coverage="unknown",
            ended=True,
            selected_count=len(views),
            complete_count=complete_count,
            partial_count=partial_count,
            failed_count=failed_count,
            pending_count=0,
            not_started_count=0,
            unconfirmed_count=len(unconfirmed),
            lanes=tuple(views),
            cost_state=cost_state,
            known_cny=known_cny,
            reserved_cny=reserved_cny,
            unpriced_attempts=unpriced,
        )
    reliable = [view for view in views if view.reliable_result is True]
    if len(reliable) == len(views) and all(view.display_state == "complete" for view in views):
        state, coverage = "complete", "full"
    elif reliable:
        state, coverage = "partial", "partial"
    elif all(view.display_state == "failed" for view in views):
        state, coverage = "failed", "failed"
    else:
        state, coverage = "ended", "none"
    return ComparisonGroupView(
        group_ref=group.group_ref,
        manifest_digest=group.manifest_digest,
        group_state=state,
        goal_coverage=coverage,
        ended=True,
        selected_count=len(views),
        complete_count=complete_count,
        partial_count=partial_count,
        failed_count=failed_count,
        pending_count=0,
        not_started_count=0,
        unconfirmed_count=0,
        lanes=tuple(views),
        cost_state=cost_state,
        known_cny=known_cny,
        reserved_cny=reserved_cny,
        unpriced_attempts=unpriced,
    )


def converge_comparison_group(
    group: FrozenComparisonGroup, observations: tuple[ComparisonLaneObservation, ...]
) -> ComparisonGroupView:
    """组收敛生产入口（读事实 → 结论）；不写库、不派发、不调模型。"""

    return build_comparison_view(group, observations)


def observations_from_executions(
    group: FrozenComparisonGroup, executions: tuple[object, ...]
) -> tuple[ComparisonLaneObservation, ...]:
    """把会话投影的 ``ExecutionSummary`` 转成逐路观察（只读，缺失如实未知）。

    ``complete``/``partial`` 为可核成果；``failed``/``withdrawn`` 为可靠确证无
    成果；``ended``/``closed_unknown``/``unknown`` 及非终态保持未知（不推断）。
    非本组/非本路行忽略；同一路有多条（本路重试）时**取最后一行**（会话按
    ``created_at`` 升序返回，故最后一行即最新一次尝试），不把旧失败盖在新成果上。
    """

    lane_ids = {lane.lane_ref for lane in group.lanes}
    by_lane: dict[str, ComparisonLaneObservation] = {}
    for row in executions:
        group_ref = getattr(row, "comparison_group_ref", None)
        lane_ref = getattr(row, "comparison_lane_ref", None)
        if group_ref != group.group_ref or not isinstance(lane_ref, str) or lane_ref not in lane_ids:
            continue
        state = getattr(row, "display_state", None)
        reliable: bool | None
        if state in {"complete", "partial"}:
            reliable = True
        elif state in {"failed", "withdrawn"}:
            reliable = False
        else:
            reliable = None
        execution_id = getattr(row, "execution_id", None)
        by_lane[lane_ref] = ComparisonLaneObservation(
            lane_ref=lane_ref,
            display_state=state if isinstance(state, str) else None,
            reliable_result=reliable,
            note=None,
            execution_id=execution_id if isinstance(execution_id, str) and execution_id else None,
        )
    return tuple(by_lane.values())


def apply_persisted_lane_notes(
    group: FrozenComparisonGroup,
    observations: tuple[ComparisonLaneObservation, ...],
    executions: tuple[object, ...],
    notes: Mapping[str, str],
) -> tuple[ComparisonLaneObservation, ...]:
    """把已持久化的**明确拒绝原因**折回逐路观察（只读，零派发，R21）。

    重建摘要只从执行行得到状态，拒绝原因不在行内，故按持久化记录补回；只有
    「从未推进」的路才接受记录，避免旧拒绝覆盖新的真实事实：

    - ``accept_failed`` 仅用于**尚无执行行**的路（受理失败本就未建执行）；
    - ``dispatch_failed`` 仅用于已有执行行但从未启动的路（``display_state`` 仍是
      受理初值 ``queued`` 且无本轮轮次）。

    路一旦推进（有轮次或已离开初值状态），记录视为过时并忽略；正常排队的路没有
    记录，保持未启动，两者不混淆。
    """

    lane_ids = {lane.lane_ref for lane in group.lanes}
    latest: dict[str, object] = {}
    for row in executions:
        if getattr(row, "comparison_group_ref", None) != group.group_ref:
            continue
        lane_ref = getattr(row, "comparison_lane_ref", None)
        if isinstance(lane_ref, str) and lane_ref in lane_ids:
            # 会话按 created_at 升序返回：同一路多次尝试时保留最后一行。
            latest[lane_ref] = row
    by_lane = {observation.lane_ref: observation for observation in observations}
    folded: list[ComparisonLaneObservation] = []
    for lane in group.lanes:
        observation = by_lane.get(lane.lane_ref, ComparisonLaneObservation(lane_ref=lane.lane_ref))
        note = notes.get(lane.lane_ref)
        if note is not None:
            row = latest.get(lane.lane_ref)
            if row is None:
                applies = note.startswith(ACCEPT_FAILED_PREFIX)
            else:
                applies = note.startswith(DISPATCH_FAILED_PREFIX) and _lane_never_advanced(row)
            if applies:
                observation = replace(observation, note=note)
        folded.append(observation)
    return tuple(folded)


def _lane_never_advanced(row: object) -> bool:
    """该路执行行是否仍停在受理初值（未启动、无任何轮次）。"""

    return getattr(row, "display_state", None) == "queued" and getattr(row, "current_round_id", None) is None


def comparison_export_rows(view: ComparisonGroupView) -> tuple[dict[str, str], ...]:
    """只读导出（沿 F26 原成果规则）：逐路原状态，零模型/零新派发、无合并报告。

    每路单列其执行身份、结果状态、缺口与费用状态；组级 ``known_cny`` 只作
    「已计价合计」，未计价/预留/未知分别标出，不拼成完整总费用（R21）。
    """

    rows: list[dict[str, str]] = []
    for lane in view.lanes:
        rows.append(
            {
                "group_ref": view.group_ref,
                "lane_ref": lane.lane_ref,
                "connection_id": lane.connection_id,
                "connection_revision": str(lane.connection_revision),
                "display_name": lane.display_name,
                "display_state": lane.display_state if lane.display_state is not None else "unknown",
                "result_state": "complete"
                if lane.reliable_result is True and lane.display_state == "complete"
                else "partial"
                if lane.reliable_result is True
                else "no_result"
                if lane.reliable_result is False
                else "unknown",
                "reason": lane.reason,
                "gap": lane.gap if lane.gap is not None else "",
                "execution_id": lane.execution_id if lane.execution_id is not None else "",
                "artifact_refs": ";".join(f"{artifact_id}@{version}" for artifact_id, version in lane.artifact_refs),
                "cost_read_state": lane.cost.read_state if lane.cost is not None else "unread",
                "known_cny": str(lane.cost.known_cny)
                if lane.cost is not None and lane.cost.known_cny is not None
                else "",
                "unpriced_attempts": str(lane.cost.unpriced_attempts) if lane.cost is not None else "",
            }
        )
    return tuple(rows)


@dataclass(frozen=True, slots=True)
class LaneStopDecision:
    """组停止时单路动作：撤回/停新派发/保留成果/继续核实未知。"""

    lane_ref: str
    action: str
    reason: str


@dataclass(frozen=True, slots=True)
class ComparisonGroupStopPlan:
    """组一致停止计划：全路均有明确动作，已完成结果不被回滚。"""

    group_ref: str
    reason: str
    dispatch_closed: bool
    decisions: tuple[LaneStopDecision, ...]


def plan_group_stop(
    group: FrozenComparisonGroup,
    observations: tuple[ComparisonLaneObservation, ...],
    *,
    reason: str,
) -> ComparisonGroupStopPlan:
    """组停止（共同源停止/用户结束整组）：组内一致，未派发撤回、在途只停新派发。

    共同资料被停止使用时所有路都受影响（共享同一冻结输入），故每路都必须得到
    动作，不允许某路继续按旧输入派发；在途未知仍核实（不伪回滚），已完成成果
    与费用保留。
    """

    stop_reason = require_identity(reason, "$.reason")
    mapped = _observation_map(group, observations)
    decisions: list[LaneStopDecision] = []
    for lane in group.lanes:
        observation = mapped.get(lane.lane_ref)
        state = observation.display_state if observation is not None else None
        if state is not None and state in _TERMINAL_STATES:
            action = (
                "verify_unknown"
                if observation is not None and observation.reliable_result is None
                else "preserve_result"
            )
            lane_reason = _lane_reason(state, observation.reliable_result if observation is not None else None)
        elif state is None or state in {"queued", "planned"}:
            action = "withdraw"
            lane_reason = "not_dispatched"
        else:
            action = "stop_new_dispatch"
            lane_reason = "in_flight"
        decisions.append(LaneStopDecision(lane_ref=lane.lane_ref, action=action, reason=f"{stop_reason}:{lane_reason}"))
    return ComparisonGroupStopPlan(
        group_ref=group.group_ref,
        reason=stop_reason,
        dispatch_closed=True,
        decisions=tuple(decisions),
    )


def verify_group_manifest_authority(authoritative: FrozenComparisonGroup, candidate: FrozenComparisonGroup) -> None:
    """受理前核验：同组键只能有一个完整冻结 manifest（先核后建路）。

    逐项比对组键（``user_scope`` / ``session_id`` / ``source_module`` /
    ``group_ref``）、共同输入摘要、完整 manifest 摘要与逐路连接矩阵
    （``lane_ref`` + 连接 ID/版本/目标指纹/用途/范围版本/授权引用）。任一不同
    → ``group_manifest_conflict``：整组拒绝，不建任何新路——同组键携不同模型
    集合（新连接带来新路键）不得并入既有组，也不静默另立一组。
    """

    path = "$.comparison_group.manifest"
    for attr, field in (
        ("user_scope", "user_scope"),
        ("session_id", "session_id"),
        ("source_module", "source_module"),
        ("group_ref", "group_ref"),
        ("input_digest", "input_digest"),
        ("manifest_digest", "manifest_digest"),
    ):
        if getattr(authoritative, attr) != getattr(candidate, attr):
            _fail(GROUP_MANIFEST_CONFLICT, f"{path}.{field}")
    authoritative_matrix = {lane.lane_ref: _selection_wire(lane.selection) for lane in authoritative.lanes}
    candidate_matrix = {lane.lane_ref: _selection_wire(lane.selection) for lane in candidate.lanes}
    if authoritative_matrix != candidate_matrix:
        _fail(GROUP_MANIFEST_CONFLICT, f"{path}.lanes")


def require_group_lane_consistency(group: FrozenComparisonGroup, persisted_lane_refs: frozenset[str]) -> None:
    """核验读到的 manifest 覆盖该组已持久化的全部路（读到的可能不是权威版本）。

    会话投影里存在 manifest 未登记的路，说明本次读到的 manifest 是旧版/部分
    写入，不能据此反推组内模型集合；显式 ``group_manifest_stale`` 失败，不把
    旧 manifest 当作该组权威版本使用。
    """

    known = {lane.lane_ref for lane in group.lanes}
    unknown = sorted(persisted_lane_refs - known)
    if unknown:
        _fail(GROUP_MANIFEST_STALE, f"$.comparison_group.manifest.lanes[{unknown[0]}]")


@dataclass(frozen=True, slots=True)
class ComparisonLaneParameters:
    """一路运行期必须消费的冻结参数（缺失即 fail-closed，不按当前设置重解析）。

    这些值来自组共同 manifest 的该路条目：受理时冻结的连接 ID/版本/目标指纹/
    用途/范围版本（M15 精确授权语义）。运行期只核不换——不得以当前
    ``agent_research`` 场景路由或当前连接设置替换本路所选连接。
    """

    group_ref: str
    lane_ref: str
    connection_id: str
    connection_revision: int
    target_fingerprint: str
    purpose: str
    scope_version: int
    verified_connection_grant_id: str | None = None


def require_lane_parameters(group: FrozenComparisonGroup, lane_ref: str) -> ComparisonLaneParameters:
    """取本路冻结参数；任一必填项缺失/非法 → ``lane_parameters_missing``。

    这是运行期唯一允许的取值入口：调用方拿不到完整参数时必须零外发，不得
    回退按当前设置重解析另一连接或候选。
    """

    lane = next((item for item in group.lanes if item.lane_ref == lane_ref), None)
    if lane is None:
        _fail("unknown_lane", f"$.lanes[{lane_ref}]")
    selection = lane.selection
    path = f"$.lanes[{lane_ref}]"
    if not isinstance(selection, ComparisonLaneSelection):
        _fail(LANE_PARAMETERS_MISSING, path)
    for field, value in (
        ("connection_id", selection.connection_id),
        ("connection_target_fingerprint", selection.target_fingerprint),
        ("connection_purpose", selection.connection_purpose),
    ):
        if not isinstance(value, str) or value == "":
            _fail(LANE_PARAMETERS_MISSING, f"{path}.{field}")
    if (
        isinstance(selection.connection_revision, bool)
        or not isinstance(selection.connection_revision, int)
        or selection.connection_revision < 1
    ):
        _fail(LANE_PARAMETERS_MISSING, f"{path}.connection_revision")
    if (
        isinstance(selection.scope_version, bool)
        or not isinstance(selection.scope_version, int)
        or selection.scope_version < 0
    ):
        _fail(LANE_PARAMETERS_MISSING, f"{path}.connection_scope_version")
    grant = selection.verified_connection_grant_id
    if grant is not None and (not isinstance(grant, str) or grant == ""):
        _fail(LANE_PARAMETERS_MISSING, f"{path}.verified_connection_grant_id")
    return ComparisonLaneParameters(
        group_ref=group.group_ref,
        lane_ref=lane.lane_ref,
        connection_id=selection.connection_id,
        connection_revision=selection.connection_revision,
        target_fingerprint=selection.target_fingerprint,
        purpose=selection.connection_purpose,
        scope_version=selection.scope_version,
        verified_connection_grant_id=selection.verified_connection_grant_id,
    )


@dataclass(frozen=True, slots=True)
class LaneWithdrawal:
    """一路撤回的可核事实：``withdrawn`` 只在撤回已落定且可核时为真。"""

    lane_ref: str
    withdrawn: bool
    reason: str | None = None


#: 撤回端口：真正撤回一路未派发执行（写库/关闭派发由实现承担）并回可核事实。
#: 参数顺序：本路冻结条目、该路执行 ID（未知为 ``None``）、停止原因码。
LaneWithdrawer = Callable[[ComparisonLaneFrozen, str | None, str], Awaitable[LaneWithdrawal]]


@dataclass(frozen=True, slots=True)
class LaneStopOutcome:
    """一路停止结果：计划动作 + 是否已落实并可核（未核不伪装成功，R21）。"""

    lane_ref: str
    action: str
    verified: bool
    reason: str


@dataclass(frozen=True, slots=True)
class ComparisonGroupStopOutcome:
    """组停止结果：派发关闭事实 + 逐路落实结果（不再只是计划）。

    ``verified`` 只在动作已落定且可核时为真：撤回需撤回端口确认；在途路只停
    新派发（原生退出未核定前不算已停止）；已完成路不再变更、成果保留。
    """

    group_ref: str
    reason: str
    dispatch_closed: bool
    lanes: tuple[LaneStopOutcome, ...]

    @property
    def all_verified(self) -> bool:
        return all(lane.verified for lane in self.lanes)


async def execute_group_stop(
    group: FrozenComparisonGroup,
    observations: tuple[ComparisonLaneObservation, ...],
    *,
    reason: str,
    withdraw_lane: LaneWithdrawer | None,
) -> ComparisonGroupStopOutcome:
    """执行组停止：撤单未派发路 + 关闭派发，并逐路核对落实结果。

    调用方必须**先**关闭本组派发门（``dispatch_closed`` 在下发前落定，使任何
    在途编排的下一路零派发），再调用本函数。``withdraw`` 动作的路必须经
    ``withdraw_lane`` 真正撤回；端口未接线、抛错或回执未确认 → ``verified=False``
    （如实标未确认，不把「计划」当「已撤回」）。``CancelledError`` 原样传播（R2）。
    """

    plan = plan_group_stop(group, observations, reason=reason)
    by_lane = {lane.lane_ref: lane for lane in group.lanes}
    execution_by_lane = {item.lane_ref: item.execution_id for item in observations}
    outcomes: list[LaneStopOutcome] = []
    for decision in plan.decisions:
        lane = by_lane.get(decision.lane_ref)
        if decision.action != "withdraw":
            # 已完成/未知核实不在本函数写库；在途只停新派发，原生退出未核定不算已停止。
            outcomes.append(
                LaneStopOutcome(
                    lane_ref=decision.lane_ref,
                    action=decision.action,
                    verified=decision.action == "preserve_result",
                    reason=decision.reason,
                )
            )
            continue
        if lane is None or withdraw_lane is None:
            outcomes.append(
                LaneStopOutcome(
                    lane_ref=decision.lane_ref,
                    action=decision.action,
                    verified=False,
                    reason=f"{decision.reason}:withdrawer_unwired",
                )
            )
            continue
        try:
            fact = await withdraw_lane(lane, execution_by_lane.get(decision.lane_ref), plan.reason)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            code = getattr(exc, "code", None)
            code_value = getattr(code, "value", code)
            reason_code = code_value if isinstance(code_value, str) and code_value else type(exc).__name__
            outcomes.append(
                LaneStopOutcome(
                    lane_ref=decision.lane_ref,
                    action=decision.action,
                    verified=False,
                    reason=f"{decision.reason}:withdraw_failed:{reason_code}",
                )
            )
            continue
        if not fact.withdrawn:
            outcomes.append(
                LaneStopOutcome(
                    lane_ref=decision.lane_ref,
                    action=decision.action,
                    verified=False,
                    reason=f"{decision.reason}:{fact.reason or 'withdraw_unconfirmed'}",
                )
            )
            continue
        outcomes.append(
            LaneStopOutcome(
                lane_ref=decision.lane_ref,
                action=decision.action,
                verified=True,
                reason=f"{decision.reason}:withdrawn",
            )
        )
    return ComparisonGroupStopOutcome(
        group_ref=group.group_ref,
        reason=plan.reason,
        dispatch_closed=plan.dispatch_closed,
        lanes=tuple(outcomes),
    )


LaneStart = Callable[[ComparisonLaneFrozen], Awaitable[str]]
LaneObserve = Callable[[tuple[tuple[str, str], ...]], Awaitable[Mapping[str, ComparisonLaneObservation]]]
StopCheckpoint = Callable[[], Awaitable[bool]]
#: 明确拒绝原因持久化回调：``(lane_ref, note)``，由调用方决定是否落库与失败策略。
LaneNoteRecorder = Callable[[str, str], Awaitable[None]]


async def drive_comparison_group(
    group: FrozenComparisonGroup,
    start_lane: LaneStart,
    observe_lanes: LaneObserve,
    *,
    active_total_executions: int = 0,
    active_session_executions: int = 0,
    total_slots: int = COMPARISON_TOTAL_SLOTS,
    session_slots: int = COMPARISON_SESSION_SLOTS,
    idle_observation_limit: int | None = None,
    wait_checkpoint: StopCheckpoint | None = None,
    record_lane_note: LaneNoteRecorder | None = None,
) -> ComparisonGroupView:
    """逐路排队推进一组独立完整任务，返回组收敛投影（生产编排入口）。

    启动/观察由本协程顺序发出（不并发抢占），每路完成立即启动下一路（
    不按批次 gather）；同会话单总执行与全局总位同时生效，等待中的路继续占位。

    **单路启动失败隔离**：``start_lane`` 抛出的普通异常只把该路记为
    ``dispatch_failed:<错误码>`` 并继续启动其余路（不阻断、不重试其他路）；
    ``asyncio.CancelledError`` 原样传播（R2）。该路不会被本批次重复尝试，
    重试由调用方以本路新轮另行发起。

    观察为空且达到 ``idle_observation_limit`` 时：有持久等待检查点则继续，否则
    按本批次结束（保持未收敛，不伪造终态）。生产观察只回传终态事实时，本函数
    自然在一次空观察后返回（发起/推进批次），由再次调用接续完成。

    ``record_lane_note`` 收到本批的**明确拒绝原因**（``dispatch_failed:`` /
    ``accept_failed:``，不含「已启动未观察」），供调用方持久化，使重开与行导出
    与即时响应同因；本函数自身不写库、不派发。
    """

    capacities = plan_lane_queue(
        group,
        active_total_executions=active_total_executions,
        active_session_executions=active_session_executions,
        total_slots=total_slots,
        session_slots=session_slots,
    )
    capacity = sum(1 for decision in capacities if decision.decision == "dispatch")
    running: dict[str, str] = {}
    started: set[str] = set()
    unavailable: set[str] = set()
    observed: dict[str, ComparisonLaneObservation] = {}
    idle = 0
    while True:
        for lane in group.lanes:
            if len(running) >= capacity:
                break
            if lane.lane_ref in started or lane.lane_ref in unavailable:
                continue
            try:
                workflow_id = await start_lane(lane)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                unavailable.add(lane.lane_ref)
                code = getattr(exc, "code", None)
                code_value = getattr(code, "value", code)
                reason = code_value if isinstance(code_value, str) and code_value else type(exc).__name__
                prefix = getattr(exc, "note_prefix", DISPATCH_FAILED_PREFIX)
                note = f"{prefix}{reason}"
                observed[lane.lane_ref] = ComparisonLaneObservation(
                    lane_ref=lane.lane_ref,
                    display_state=None,
                    reliable_result=None,
                    note=note,
                )
                if record_lane_note is not None:
                    await record_lane_note(lane.lane_ref, note)
                continue
            started.add(lane.lane_ref)
            running[lane.lane_ref] = workflow_id
            # 已启动是可靠事实；尚无终态投影时如实标为「已启动、未观察到事实」，
            # 不冒充未启动，也不伪造运行/成功状态（R21）。
            observed[lane.lane_ref] = ComparisonLaneObservation(
                lane_ref=lane.lane_ref, display_state=None, reliable_result=None, note="started_no_fact"
            )
        if not running:
            break
        facts = await observe_lanes(tuple(running.items()))
        if not facts:
            idle += 1
            if idle_observation_limit is not None and idle >= idle_observation_limit:
                if wait_checkpoint is None:
                    break
                idle = 0
                if not await wait_checkpoint():
                    break
            continue
        idle = 0
        for lane_ref, observation in facts.items():
            if lane_ref not in running:
                _fail("observation_unknown_lane", f"$.observations[{lane_ref}]")
            _validate_observation(observation, f"$.observations[{lane_ref}]")
            if observation.lane_ref != lane_ref:
                _fail("observation_identity_mismatch", f"$.observations[{lane_ref}].lane_ref")
            observed[lane_ref] = observation
            if observation.display_state is not None and observation.display_state in _TERMINAL_STATES:
                running.pop(lane_ref, None)
        if len(observed) == len(group.lanes) and not running:
            break
    return build_comparison_view(group, tuple(observed.values()))


@dataclass(frozen=True, slots=True)
class ComparisonSubmissionRequest:
    """组提交输入（共同输入 + 所选具体连接，至少两路）。"""

    user_scope: str
    session_id: str
    source_module: str
    group_command_id: str
    shared: ComparisonSharedInput
    lanes: tuple[ComparisonLaneSelection, ...]
    expected_revision: int = 0
    node_id: str = "root"
    workflow_id: str = "comparison"
    source_display_name: str = "用户消息"


@dataclass(frozen=True, slots=True)
class ComparisonLaneReceipt:
    """单路受理结果（受理失败逐路如实分列，不伪造已受理）。

    ``dispatch_submitted`` 只在真正尝试派发或确定因限额零派发后有值：``False``
    表示本次零派发（原因在 ``dispatch_reason``，含派发器拒绝与排队限额），
    ``None`` 表示未尝试且无已知限额原因（只受理不派发）。
    """

    lane_ref: str
    execution_id: str | None
    command_record_id: str | None
    commit_state: CommitState | None
    error_code: str | None
    dispatch_submitted: bool | None = None
    dispatch_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ComparisonSubmissionReceipt:
    """组受理回执：共同 manifest 身份 + 逐路受理事实。"""

    group_ref: str
    manifest_digest: str
    lane_count: int
    lanes: tuple[ComparisonLaneReceipt, ...]

    @property
    def all_accepted(self) -> bool:
        return all(lane.execution_id is not None for lane in self.lanes)


@dataclass(frozen=True, slots=True)
class ComparisonRunReceipt:
    """组发起/推进回执：逐路独立执行事实 + 当前组投影（无组级执行身份）。

    比较组本身**不是**一次研究执行：没有 group execution_id，也没有组级完成
    结论可被计为「一次完整成功」；成功/缺口只逐路按各自执行事实呈现。
    """

    group_ref: str
    manifest_digest: str
    lanes: tuple[ComparisonLaneReceipt, ...]
    view: ComparisonGroupView
    dispatcher_wired: bool

    @property
    def all_accepted(self) -> bool:
        """逐路是否都已保存受理事实（不代表已派发或已消费）。"""

        return bool(self.lanes) and all(lane.execution_id is not None for lane in self.lanes)


@dataclass(frozen=True, slots=True)
class ComparisonReadQuery:
    """只读比较/导出查询（按准确组身份与主会话）。

    ``include_cost`` 为真时逐路读取原账本费用投影（只读、零模型调用）；
    缺省只读执行事实，费用保持未知而不是伪造 0。
    """

    user_scope: str
    session_id: str
    group_ref: str
    limit: int = 50
    include_cost: bool = False


__all__ = [
    "ACCEPT_FAILED_PREFIX",
    "COMPARISON_COST_FAILED",
    "COMPARISON_COST_KNOWN",
    "COMPARISON_COST_PARTIAL",
    "COMPARISON_COST_RESERVED",
    "COMPARISON_COST_UNKNOWN",
    "COMPARISON_MANIFEST_VERSION",
    "COMPARISON_MIN_LANES",
    "COMPARISON_SESSION_SLOTS",
    "COMPARISON_TOTAL_SLOTS",
    "DISPATCH_FAILED_PREFIX",
    "GROUP_MANIFEST_CONFLICT",
    "GROUP_MANIFEST_STALE",
    "LANE_PARAMETERS_MISSING",
    "STOP_REASON_SHARED_SOURCE",
    "STOP_REASON_USER_ENDED_GROUP",
    "ComparisonGroupStopOutcome",
    "ComparisonGroupStopPlan",
    "ComparisonGroupView",
    "ComparisonLaneFrozen",
    "ComparisonLaneObservation",
    "ComparisonLaneParameters",
    "ComparisonLaneReceipt",
    "ComparisonLaneRetry",
    "ComparisonLaneSelection",
    "ComparisonLaneView",
    "ComparisonReadQuery",
    "ComparisonRunReceipt",
    "ComparisonSharedInput",
    "ComparisonSubmissionReceipt",
    "ComparisonSubmissionRequest",
    "FrozenComparisonGroup",
    "LaneNoteRecorder",
    "LaneQueueDecision",
    "LaneStopDecision",
    "LaneStopOutcome",
    "LaneWithdrawal",
    "LaneWithdrawer",
    "apply_persisted_lane_notes",
    "build_comparison_view",
    "build_lane_accept_request",
    "comparison_export_rows",
    "converge_comparison_group",
    "decode_comparison_manifest",
    "drive_comparison_group",
    "execute_group_stop",
    "freeze_comparison_group",
    "observations_from_executions",
    "plan_group_stop",
    "plan_lane_queue",
    "plan_lane_retry",
    "require_group_lane_consistency",
    "require_lane_connection",
    "require_lane_parameters",
    "verify_group_manifest_authority",
]
