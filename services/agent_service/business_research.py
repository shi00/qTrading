"""业务对象 → 研究草稿准备（R2-I06）：准确对象/版本冻结，只准备不发送。

链路口径（检视 R2-I06）：

- 业务入口（股票详情 / 候选列表 / 所选资讯 / 已有回测报告）只把**准确对象与
  版本**（source_id + 内容摘要版本 + 可得时点）登记为业务来源并写入草稿；
- 草稿期不判断 PIT 准入（此时没有用户确认的截止界，补一个就是伪造时点，R21/R24），
  准入在发送/受理时按用户确认的截止界重判；
- 本模块**不发送**：不调用 ``submit``、不派发 workflow、不产生费用；用户必须
  在自己的发送路径上明确确认后才开始研究；
- 来源不可用（对象已删除 / 回测结果读不到 / 给定截止界而时点未知）时如实抛
  :class:`BusinessSourceUnavailable`，不生成「看起来可用」的空草稿（R21）；
- 回测结果的执行时点未带时区（存量记录，绝对时刻不可证）时走**受限准备**：
  草稿可建，但 ``available_at`` 保持 ``None``、上下文显式标注时点未知，发送/受理期
  由 PIT 准入拒绝；禁止把 naive 时间按 UTC 贴成已知（R21/R24）；
- 已存在的草稿不被覆盖：同一对象已有非空稿时抛 :class:`BusinessDraftAlreadyPrepared`，
  保留原稿（来源失效也保稿）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from core.agent_types import (
    DEFAULT_ATTACHMENT_BUDGET,
    DRAFT_PAYLOAD_SCHEMA_VERSION,
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    BusinessSourceItem,
    BusinessSourceRegistrationReceipt,
    CommandRef,
    DraftPayload,
    DraftRead,
    DraftReadQuery,
    DraftReceipt,
    DraftSaveCommand,
    DraftTransferCommand,
    DraftTransferReceipt,
    DraftTransferSourceKind,
    Mode,
    Purpose,
    ReadState,
    Selection,
    SessionAction,
    SessionChangeCommand,
    SessionReadQuery,
    SourceKind,
    SourceLocator,
    SourceRef,
    SourceVersionRef,
    VerificationState,
    derive_draft_transfer_id,
    draft_transfer_payload_digest,
    encode_draft_payload,
    json_doc,
    sha256_hex,
    stable_id,
)
from services.agent_service.business_sources import BusinessSnapshotRead, read_backtest_snapshot

#: 草稿正文模板（用户可编辑的草稿内容，不是 i18n 文案；默认中文与产品默认语言一致）。
_DRAFT_TEXT_TEMPLATE = "请研究以下业务对象并说明结论与依据：{display_name}（{kind}:{object_id}，版本 {version}）。"

#: 研究发起草稿的稳定用途：回测解释按事后解释，其余业务对象按日终研究。
_BACKTEST_PURPOSE = Purpose.POST_HOC_EXPLANATION
_OBJECT_PURPOSE = Purpose.END_OF_DAY_RESEARCH

#: 来源时点在草稿上下文中的显式状态（``known`` / ``unknown``；未知不填具体时刻，R21）。
_AVAILABILITY_KNOWN = "known"
_AVAILABILITY_UNKNOWN = "unknown"
#: 严格读给出的「时区不可证」原因（与 ``business_sources`` 同值域）。
_AVAILABILITY_ZONE_UNKNOWN = "available_at_zone_unknown"
#: 摘要自带截断标记时来源降级原因（可按原引用读回的正文仅为截断表示）。
_SNAPSHOT_TRUNCATED_REASON = "content_truncated"
#: 冻结摘要字段缺失的显式标注（不填业务上合法的具体值，R21）。
_MISSING_FIELD = "未提供"
#: 受限稿正文提示：草稿期可用，发送期会被 PIT 准入拒绝（用户可见、不误导）。
_RESTRICTED_AVAILABILITY_NOTE = (
    "\n注意：该回测结果的执行时点未带时区（未知），未按 UTC 猜测；发送前须补齐可证时点，否则 PIT 准入会拒绝。"
)

_LOCAL_TZ = ZoneInfo("Asia/Shanghai")


class BacktestResultReader(Protocol):
    """回测结果严格读端口（生产为 ``BacktestService`` / ``BacktestDao``）。"""

    async def get_result(self, run_id: str, *, suppress_errors: bool = True) -> dict | None: ...


class BusinessResearchFacade(Protocol):
    """本模块需要的 facade 子集（``AgentService`` 结构性满足，UI 不 import app）。"""

    async def read_session(self, query: SessionReadQuery, *, user_scope: str) -> Any: ...

    async def change_session(self, command: SessionChangeCommand) -> Any: ...

    async def register_draft_sources(
        self,
        *,
        user_scope: str,
        session_id: str,
        draft_revision: int,
        items: tuple[BusinessSourceItem, ...],
    ) -> BusinessSourceRegistrationReceipt: ...

    async def save_draft(self, command: DraftSaveCommand) -> DraftReceipt: ...

    async def read_draft(self, query: DraftReadQuery, *, user_scope: str) -> DraftRead: ...

    async def transfer_draft(self, command: DraftTransferCommand) -> DraftTransferReceipt: ...


class BusinessSourceUnavailable(Exception):
    """来源不可用（不可用它冒充可用草稿；原因可审计）。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class BusinessDraftCommitUnknown(Exception):
    """草稿写入结果未知（提交已发起但确认中断）：保原键查回，不换键补写（N12）。

    ``session_id`` 为本次写目标：草稿可能已落库，也可能未落库，调用方不得把它当
    「确定失败」重发新键，也不得先清草稿；下次准备会按原命令（原键/原输入）查回。
    """

    def __init__(self, session_id: str) -> None:
        super().__init__(session_id)
        self.session_id = session_id


class BusinessDraftAlreadyPrepared(Exception):
    """同一对象已有草稿：保留原稿，不覆盖（重复发起不产生第二份草稿）。"""

    def __init__(self, session_id: str, draft_revision: int) -> None:
        super().__init__(session_id)
        self.session_id = session_id
        self.draft_revision = draft_revision


#: 研究资料类别（文件 / 片段 / 引用；与 SourceKind 不混用）。
MATERIAL_KIND_FILE = "file"
MATERIAL_KIND_FRAGMENT = "fragment"
MATERIAL_KIND_REFERENCE = "reference"
MATERIAL_KINDS: frozenset[str] = frozenset({MATERIAL_KIND_FILE, MATERIAL_KIND_FRAGMENT, MATERIAL_KIND_REFERENCE})

#: 可解析的拒绝原因码（UI 由码映射 i18n key，不解析自由文本）。
MATERIAL_EMPTY = "material_empty"
MATERIAL_COUNT_EXCEEDED = "material_count_exceeded"
MATERIAL_BYTES_EXCEEDED = "material_bytes_exceeded"
MATERIAL_KIND_INVALID = "material_kind_invalid"
#: 目标草稿冲突/失效（会话不存在、归档、已清理、已被推进）。
TARGET_SESSION_UNAVAILABLE = "target_session_unavailable"
TARGET_DRAFT_CONFLICT = "target_draft_conflict"
TARGET_NOT_TRANSFERABLE = "target_not_transferable"
TRANSFER_CONFLICT = "transfer_conflict"


@dataclass(frozen=True, slots=True)
class ResearchMaterial:
    """一件研究资料的只读事实（已读状态 + 三类时点，未知一律 ``None``）。"""

    kind: str
    source_id: str
    source_version: str
    display_name: str
    read_state: ReadState
    missing_reason: str | None
    captured_at: str | None
    content_at: str | None
    byte_size: int | None


@dataclass(frozen=True, slots=True)
class MaterialLimitViolation:
    """D06 限额拒绝（``code`` 可解析；``index`` 为越限资料序号）。"""

    code: str
    index: int
    limit: int
    actual: int


def material_limit_violation(
    materials: Sequence[ResearchMaterial],
    *,
    budget: Any = DEFAULT_ATTACHMENT_BUDGET,
) -> MaterialLimitViolation | None:
    """按 D06 逐值核验件数与单件字节；未知字节不据此放开（不制造通过）。

    恰等通过、超一即拒绝；返回第一个违规（件数优先），``None`` 表示全部在限内。
    """

    max_files = int(getattr(budget, "max_files", 5))
    max_bytes = int(getattr(budget, "max_source_bytes", 20_000_000))
    if len(materials) > max_files:
        return MaterialLimitViolation(MATERIAL_COUNT_EXCEEDED, max_files, max_files, len(materials))
    for index, material in enumerate(materials):
        if material.byte_size is not None and material.byte_size > max_bytes:
            return MaterialLimitViolation(MATERIAL_BYTES_EXCEEDED, index, max_bytes, material.byte_size)
    return None


def material_read_counts(materials: Sequence[ResearchMaterial]) -> tuple[int, int]:
    """返回 ``(已读, 未读)``；``PARTIAL`` 计入未读（部分读取不得当作完整已读）。"""

    read = sum(1 for material in materials if material.read_state is ReadState.READY)
    return read, len(materials) - read


class MaterialLimitExceeded(Exception):
    """资料超限或非法（如实拒绝，不静默丢弃）。"""

    def __init__(self, violation: MaterialLimitViolation) -> None:
        super().__init__(violation.code)
        self.code = violation.code
        self.index = violation.index
        self.limit = violation.limit
        self.actual = violation.actual


class BusinessDraftTargetUnavailable(Exception):
    """目标会话/草稿不可用（不存在、归档、已清理或已转移）。"""

    def __init__(self, code: str = TARGET_SESSION_UNAVAILABLE) -> None:
        super().__init__(code)
        self.code = code


class BusinessDraftTransferConflict(Exception):
    """草稿转移/并入冲突（错误码由 UI 映射 i18n；不伪装成功）。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class BusinessMaterialsReceipt:
    """资料并入既有会话草稿的回执（只保存，不发送）。"""

    target_session_id: str
    draft_revision: int
    added: int
    read: int
    unread: int
    sent: bool = False


@dataclass(frozen=True, slots=True)
class BusinessDraftTransferReceipt:
    """草稿转移回执（``sent`` 恒为 ``False``；转移不等于发送）。"""

    target_session_id: str
    target_revision: int
    transfer_id: str
    material_count: int
    sent: bool = False


@dataclass(frozen=True, slots=True)
class BusinessObjectSnapshot:
    """业务入口冻结的准确对象（只含已显示给用户的字段，不推断业务语义）。"""

    source_module: str
    object_kind: str
    object_id: str
    display_name: str
    summary: Mapping[str, Any]
    available_at: str | None
    period: str | None = None


@dataclass(frozen=True, slots=True)
class BusinessDraftPreparation:
    """准备草稿所需的来源事实（来源对象 + 冻结上下文 + 用途）。"""

    item: BusinessSourceItem
    object_context: Mapping[str, Any]
    purpose: Purpose
    display_name: str


@dataclass(frozen=True, slots=True)
class BusinessPreparationReceipt:
    """草稿准备回执：``sent`` 恒为 ``False``（准备不等于发送）。"""

    session_id: str
    draft_revision: int
    source: SourceVersionRef
    sent: bool = False


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _write_command_id(prefix: str, *parts: str) -> str:
    """确定性写命令键：同一写身份重试同键，未知结果按原键查回、不换键补写（N12）。"""

    return f"{prefix}-{sha256_hex('|'.join(parts))[:32]}"


def _today_local() -> str:
    return datetime.now(_LOCAL_TZ).date().isoformat()


def snapshot_source(snapshot: BusinessObjectSnapshot, *, user_scope: str) -> BusinessSourceItem:
    """由业务对象快照构造受管源（版本 = 内容摘要；未程序核验如实标注）。"""

    for name, value in (
        ("source_module", snapshot.source_module),
        ("object_kind", snapshot.object_kind),
        ("object_id", snapshot.object_id),
        ("display_name", snapshot.display_name),
    ):
        if not isinstance(value, str) or not value:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{name} must be non-empty", "business_research")
    if not isinstance(snapshot.available_at, str) or not snapshot.available_at:
        # 时点未知不能按「可得」登记（R21/R24），也不伪造抓取时刻。
        raise BusinessSourceUnavailable("available_at_unknown")
    try:
        summary = json_doc(dict(snapshot.summary))
    except AgentValidationError as exc:
        raise AgentServiceError(
            AgentErrorCode.INPUT_INVALID,
            f"object summary is not canonical json: {exc.code}",
            "business_research",
        ) from exc
    digest = sha256_hex(
        _canonical_json(
            {
                "schema_version": 1,
                "source_module": snapshot.source_module,
                "object_kind": snapshot.object_kind,
                "object_id": snapshot.object_id,
                "summary": summary,
            }
        )
    )
    object_ref = f"{snapshot.object_kind}:{snapshot.object_id}"
    body_text, truncated = _render_snapshot_body(snapshot, digest=digest)
    source = SourceRef(
        user_scope=user_scope,
        source_id=object_ref,
        version=digest,
        digest=digest,
        source_kind=SourceKind.BUSINESS_SNAPSHOT,
        display_name=snapshot.display_name,
        object_ref=object_ref,
        locator=SourceLocator(
            locator_kind="source_range",
            origin_ref=SourceVersionRef(
                source_kind=SourceKind.BUSINESS_SNAPSHOT,
                source_id=object_ref,
                source_version=digest,
                source_digest=digest,
            ),
            selection=Selection(kind="all"),
        ),
        available_at=snapshot.available_at,
        period=snapshot.period,
        unit_note=None,
        # T-08：READY 必须有可按原引用读回的正文表示——此处以内联受管正文承载冻结
        # 摘要；摘要自带截断标记时降级为 PARTIAL（不把「显示截断」伪装成完整）。
        read_state=ReadState.PARTIAL if truncated else ReadState.READY,
        missing_reason=_SNAPSHOT_TRUNCATED_REASON if truncated else None,
        provenance_refs=(),
    )
    # UI 捕获的业务快照未经程序读取核验：不得自称 program_verified（R21）。
    return BusinessSourceItem(source=source, verification=VerificationState.UNVERIFIED, body_text=body_text)


def _render_snapshot_body(snapshot: BusinessObjectSnapshot, *, digest: str) -> tuple[str, bool]:
    """把冻结的业务快照渲染为确定性内联正文（可按源版引用读回）。

    只输出已冻结字段（键排序，缺失显式标注），不推断业务语义、不回填 0。若摘要
    自带 ``content_truncated=true``（如资讯正文被截断），返回 ``truncated=True``，
    由调用方把来源降级为 ``PARTIAL``。
    """

    truncated = str(snapshot.summary.get("content_truncated", "")).strip().lower() == "true"
    lines = [
        f"# 业务对象快照：{snapshot.display_name}",
        f"- 来源模块：{snapshot.source_module}",
        f"- 对象类别：{snapshot.object_kind}",
        f"- 对象标识：{snapshot.object_id}",
        f"- 可得时点（UTC）：{snapshot.available_at or _AVAILABILITY_UNKNOWN}",
        f"- 周期：{snapshot.period or _MISSING_FIELD}",
        f"- 来源版本（内容摘要前缀）：{digest[:12]}",
        "",
        "## 冻结摘要",
    ]
    for key in sorted(snapshot.summary, key=str):
        lines.append(f"- {key}：{_snapshot_field_text(snapshot.summary[key])}")
    if truncated:
        lines.append("")
        lines.append("- 注意：该对象正文已截断，未按完整正文登记（PARTIAL）。")
    return "\n".join(lines) + "\n", truncated


def _snapshot_field_text(value: Any) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return _MISSING_FIELD
    return str(value)


def _batch_reference(summary: Mapping[str, Any]) -> str | None:
    """从冻结摘要提取原批次定位（筛选日期/策略/运行/模式）；缺失返回 ``None``。

    只组合已冻结的字段，不推断、不补默认（缺哪项就不声称有该项）。
    """

    parts: list[str] = []
    for key in ("trade_date", "screening_date", "strategy", "strategy_name", "run_id", "screener_mode"):
        value = summary.get(key)
        if value is None or not str(value).strip():
            continue
        parts.append(f"{key}={value}")
    return ";".join(parts) or None


def snapshot_preparation(snapshot: BusinessObjectSnapshot, *, user_scope: str) -> BusinessDraftPreparation:
    """业务对象快照 → 草稿准备载荷（上下文冻结准确对象、版本与摘要）。"""

    item = snapshot_source(snapshot, user_scope=user_scope)
    context = {
        "source_module": snapshot.source_module,
        "object_kind": snapshot.object_kind,
        "object_id": snapshot.object_id,
        "object_version": item.source.version,
        "digest": item.source.digest,
        "display_name": snapshot.display_name,
        "available_at": snapshot.available_at,
        "period": snapshot.period,
        "availability_state": _AVAILABILITY_KNOWN,
        "summary": json.loads(json_doc(dict(snapshot.summary))),
    }
    batch_ref = _batch_reference(snapshot.summary)
    if batch_ref is not None:
        # 原批次引用：候选/资讯取自哪一次筛选运行的定位（不重跑、不按「当前快照」冒充）。
        context["batch_ref"] = batch_ref
    return BusinessDraftPreparation(
        item=item,
        object_context=context,
        purpose=_OBJECT_PURPOSE,
        display_name=snapshot.display_name,
    )


def backtest_preparation(read: BusinessSnapshotRead, *, result_id: str) -> BusinessDraftPreparation:
    """回测严格读结果 → 草稿准备载荷；不可用时 fail-closed（不造空草稿）。

    - ``ready``：内容与绝对时点均可证，正常准备；
    - ``unknown`` + ``available_at_zone_unknown``：内容可读但时点未知 → **受限准备**
      （``available_at=None``、上下文标注 ``availability_state=unknown``）；草稿期
      不判断 PIT 准入，故仍可建稿；发送/受理期按截止界重判会拒绝（R21/R24）；
    - 其余（未找到 / DB 失败 / 给定截止界而时点未知）：抛 ``BusinessSourceUnavailable``。
    """

    if read.source is None:
        raise BusinessSourceUnavailable(read.missing_reason or "source_unavailable")
    item = read.source
    if read.read_state is ReadState.READY and item.source.available_at:
        availability_state = _AVAILABILITY_KNOWN
    elif read.read_state is ReadState.UNKNOWN and read.missing_reason == _AVAILABILITY_ZONE_UNKNOWN:
        availability_state = _AVAILABILITY_UNKNOWN
    else:
        raise BusinessSourceUnavailable(read.missing_reason or "source_unavailable")
    context = {
        "source_module": "backtest",
        "object_kind": "backtest_result",
        "object_id": result_id,
        "object_version": item.source.version,
        "digest": item.source.digest,
        "display_name": item.source.display_name,
        "available_at": item.source.available_at,
        "period": item.source.period,
        "availability_state": availability_state,
    }
    return BusinessDraftPreparation(
        item=item,
        object_context=context,
        purpose=_BACKTEST_PURPOSE,
        display_name=item.source.display_name,
    )


class BusinessResearchPreparer:
    """准备「从业务对象发起研究」的草稿：登记来源 + 保存草稿，不发送。"""

    def __init__(self, facade: BusinessResearchFacade) -> None:
        self._facade = facade
        #: 提交结果未知的存稿写（session_id → 原命令）：下次按原键/原输入查回，不换键补写（N12）。
        self._pending_writes: dict[str, DraftSaveCommand] = {}

    async def prepare_object(
        self,
        snapshot: BusinessObjectSnapshot,
        *,
        user_scope: str,
        session_title: str | None = None,
    ) -> BusinessPreparationReceipt:
        """业务对象快照 → 草稿（不发送）。"""

        preparation = snapshot_preparation(snapshot, user_scope=user_scope)
        return await self.prepare(preparation, user_scope=user_scope, session_title=session_title)

    async def prepare_backtest(
        self,
        reader: BacktestResultReader,
        *,
        user_scope: str,
        result_id: str,
        session_title: str | None = None,
    ) -> BusinessPreparationReceipt:
        """既有回测报告 → 草稿（严格读结果后再冻结，不重跑回测）。"""

        if not isinstance(result_id, str) or not result_id:
            raise BusinessSourceUnavailable("result_id_missing")
        read = await read_backtest_snapshot(reader, user_scope=user_scope, result_id=result_id)
        preparation = backtest_preparation(read, result_id=result_id)
        return await self.prepare(preparation, user_scope=user_scope, session_title=session_title)

    async def add_materials_to_session(
        self,
        *,
        user_scope: str,
        target_session_id: str,
        materials: Sequence[ResearchMaterial],
        command_id: str,
    ) -> BusinessMaterialsReceipt:
        """把已选资料并入指定既有会话的草稿（只保存，不发送、不派发）。

        目标会话不存在/归档/已清理 → :class:`BusinessDraftTargetUnavailable`；
        草稿版被推进 → :class:`BusinessDraftTransferConflict`（不覆盖用户已编辑内容）；
        超限/空清单 → :class:`MaterialLimitExceeded`（原因码可解析，R21）。
        """

        _require_non_empty(target_session_id, "target_session_id")
        _require_non_empty(command_id, "command_id")
        _raise_if_material_limit(materials)
        read = await self._facade.read_draft(DraftReadQuery(session_id=target_session_id), user_scope=user_scope)
        if read.cleaned or read.transferred or read.archived:
            raise BusinessDraftTargetUnavailable(TARGET_SESSION_UNAVAILABLE)
        base = read.payload
        existing = list(base.source_refs) if base is not None else []
        new_refs: list[SourceVersionRef] = []
        new_items: list[BusinessSourceItem] = []
        for material in materials:
            ref = _material_version_ref(material)
            if any(item.source_id == ref.source_id and item.source_version == ref.source_version for item in existing):
                continue
            existing.append(ref)
            new_refs.append(ref)
            new_items.append(_material_source_item(material, user_scope=user_scope))
        if new_items:
            await self._facade.register_draft_sources(
                user_scope=user_scope,
                session_id=target_session_id,
                draft_revision=read.draft_revision,
                items=tuple(new_items),
            )
        if base is None:
            payload = _materials_payload(materials)
        elif new_refs:
            payload = replace(base, source_refs=tuple(existing))
        else:
            # 全部资料已在草稿引用中：不写新稿版（幂等并入），如实返回当前版本。
            read_count, unread_count = material_read_counts(materials)
            return BusinessMaterialsReceipt(
                target_session_id=target_session_id,
                draft_revision=read.draft_revision,
                added=0,
                read=read_count,
                unread=unread_count,
            )
        command = DraftSaveCommand(
            command=CommandRef(
                user_scope=user_scope,
                source_module="business_research",
                business_request_id=command_id,
                command_id=_write_command_id(
                    "ui-materials",
                    user_scope,
                    target_session_id,
                    str(read.draft_revision),
                    sha256_hex(_canonical_json(encode_draft_payload(payload))),
                ),
                expected_revision=read.draft_revision,
            ),
            session_id=target_session_id,
            expected_draft_revision=read.draft_revision,
            payload=payload,
        )
        try:
            receipt = await self._save_draft(command)
        except BusinessDraftCommitUnknown:
            raise
        except AgentServiceError as exc:
            raise _transfer_conflict(exc) from exc
        read_count, unread_count = material_read_counts(materials)
        return BusinessMaterialsReceipt(
            target_session_id=target_session_id,
            draft_revision=receipt.draft_revision,
            added=len(new_refs),
            read=read_count,
            unread=unread_count,
        )

    async def transfer_draft(
        self,
        *,
        user_scope: str,
        target_session_id: str,
        materials: Sequence[ResearchMaterial],
        command_id: str,
        target_draft_revision: int,
    ) -> BusinessDraftTransferReceipt:
        """把资料包作为草稿转移到目标（须为空稿）会话：幂等 ``command_id``，不发送。

        以 ``business_preparation`` 为源类别（本库无源行，claim 由命令唯一仲裁）。
        冲突/失效如实抛类型化异常，不静默降级为「已转移」。
        """

        _require_non_empty(target_session_id, "target_session_id")
        _require_non_empty(command_id, "command_id")
        if isinstance(target_draft_revision, bool) or not isinstance(target_draft_revision, int):
            raise AgentValidationError("input_invalid", "target_draft_revision")
        if target_draft_revision < 1:
            raise AgentValidationError("invalid_revision", "target_draft_revision")
        _raise_if_material_limit(materials)
        payload = _materials_payload(materials)
        source_id = stable_id("material_bundle", [user_scope, command_id])
        provisional = DraftTransferCommand(
            command=CommandRef(
                user_scope=user_scope,
                source_module="business_research",
                business_request_id=command_id,
                command_id=command_id,
                expected_revision=target_draft_revision,
            ),
            transfer_id="pending",
            source_kind=DraftTransferSourceKind.BUSINESS_PREPARATION,
            source_id=source_id,
            source_session_id=None,
            expected_source_draft_revision=1,
            target_session_id=target_session_id,
            expected_target_draft_revision=target_draft_revision,
            payload=payload,
            payload_digest=draft_transfer_payload_digest(payload),
            confirmed=True,
        )
        command = replace(provisional, transfer_id=derive_draft_transfer_id(provisional))
        try:
            receipt = await self._facade.transfer_draft(command)
        except AgentServiceError as exc:
            if exc.code is AgentErrorCode.OPERATION_UNKNOWN:
                raise BusinessDraftCommitUnknown(target_session_id) from exc
            raise _transfer_conflict(exc) from exc
        return BusinessDraftTransferReceipt(
            target_session_id=receipt.target_session_id,
            target_revision=receipt.target_revision if receipt.target_revision is not None else target_draft_revision,
            transfer_id=receipt.transfer_id,
            material_count=len(materials),
        )

    async def prepare(
        self,
        preparation: BusinessDraftPreparation,
        *,
        user_scope: str,
        session_title: str | None = None,
    ) -> BusinessPreparationReceipt:
        """登记来源（草稿消费者）并保存草稿；零发送、零派发、零费用。

        写命令键由写身份确定性派生（同一写重试同键，N12）。上次存稿「提交已发起但
        确认中断」时，本次先按原命令（原键/原输入）查回原回执：查回成功即原结果，
        查不到（原写未落库且草稿已被推进）按既有草稿保留原稿——不换键补写、不先清
        草稿；确定未写的失败照常上抛（同一命令键重试安全）。
        """

        context = _canonical_context(preparation.object_context)
        source = preparation.item.source
        session_id = stable_id(
            "session",
            [user_scope, source.source_id, source.version],
        )
        title = session_title or f"研究：{preparation.display_name}"
        payload = _draft_payload(preparation, source, context)
        pending = self._pending_writes.pop(session_id, None)
        if pending is not None:
            return await self._replay_pending(pending, source=source)
        revision = await self._existing_draft_revision(session_id, user_scope=user_scope)
        if revision is None:
            await self._create_session(
                SessionChangeCommand(
                    command=CommandRef(
                        user_scope=user_scope,
                        source_module=str(context["source_module"]),
                        business_request_id=session_id,
                        # 确定性键：建会话结果未知后重试走同键查回，不建第二份（N12）。
                        command_id=_write_command_id("ui-session", user_scope, session_id),
                        expected_revision=0,
                    ),
                    action=SessionAction.CREATE,
                    session_id=session_id,
                    expected_session_revision=0,
                    title=title,
                )
            )
            revision = 1
        elif revision > 1:
            # 已有非空草稿（用户可能已编辑）：保留原稿，不覆盖（重复发起不产第二份）。
            raise BusinessDraftAlreadyPrepared(session_id, revision)

        await self._facade.register_draft_sources(
            user_scope=user_scope,
            session_id=session_id,
            draft_revision=revision,
            items=(preparation.item,),
        )
        command = DraftSaveCommand(
            command=CommandRef(
                user_scope=user_scope,
                source_module=str(context["source_module"]),
                business_request_id=session_id,
                # 键含草稿版与载荷摘要：同一写重试同键；变输入不冒用旧键（N12）。
                command_id=_write_command_id(
                    "ui-draft",
                    user_scope,
                    session_id,
                    str(revision),
                    sha256_hex(_canonical_json(encode_draft_payload(payload))),
                ),
                expected_revision=revision,
            ),
            session_id=session_id,
            expected_draft_revision=revision,
            payload=payload,
        )
        receipt = await self._save_draft(command)
        return BusinessPreparationReceipt(
            session_id=session_id,
            draft_revision=receipt.draft_revision,
            source=SourceVersionRef(
                source_kind=source.source_kind,
                source_id=source.source_id,
                source_version=source.version,
                source_digest=source.digest,
            ),
            sent=False,
        )

    async def _create_session(self, command: SessionChangeCommand) -> None:
        """建会话写：提交结果未知如实转 ``BusinessDraftCommitUnknown``（N12）。"""

        try:
            await self._facade.change_session(command)
        except AgentServiceError as exc:
            if exc.code is AgentErrorCode.OPERATION_UNKNOWN:
                raise BusinessDraftCommitUnknown(command.session_id) from exc
            raise

    async def _save_draft(self, command: DraftSaveCommand) -> DraftReceipt:
        """存稿写：确定未写原样上抛（可同键重试）；结果未知转类型化状态并保原命令（N12）。"""

        try:
            receipt = await self._facade.save_draft(command)
        except AgentServiceError as exc:
            if exc.code is AgentErrorCode.OPERATION_UNKNOWN:
                self._pending_writes[command.session_id] = command
                raise BusinessDraftCommitUnknown(command.session_id) from exc
            raise
        self._pending_writes.pop(command.session_id, None)
        return receipt

    async def _replay_pending(self, command: DraftSaveCommand, *, source: SourceRef) -> BusinessPreparationReceipt:
        """按原命令（原键/原输入）查回上次结果未知的存稿写（N12）。

        查回成功即原结果；``REVISION_CONFLICT`` / ``INPUT_CONFLICT`` 表示原写未落库
        且草稿已被推进（用户编辑）→ 保留原稿、不覆盖；确定未写（存储不可用）原样
        上抛并保留原命令待重试。
        """

        try:
            receipt = await self._save_draft(command)
        except BusinessDraftCommitUnknown:
            raise
        except AgentServiceError as exc:
            if exc.code in (AgentErrorCode.REVISION_CONFLICT, AgentErrorCode.INPUT_CONFLICT):
                revision = await self._existing_draft_revision(
                    command.session_id, user_scope=command.command.user_scope
                )
                if revision is None:
                    # 目标会话已不存在（清理/fence）：无从保留原稿，按原错误如实上抛。
                    raise
                raise BusinessDraftAlreadyPrepared(command.session_id, revision) from exc
            # 确定未写（存储不可用等）：保留原命令，下次仍按原键查回。
            self._pending_writes[command.session_id] = command
            raise
        return BusinessPreparationReceipt(
            session_id=receipt.session_id,
            draft_revision=receipt.draft_revision,
            source=SourceVersionRef(
                source_kind=source.source_kind,
                source_id=source.source_id,
                source_version=source.version,
                source_digest=source.digest,
            ),
            sent=False,
        )

    async def _existing_draft_revision(self, session_id: str, *, user_scope: str) -> int | None:
        """读取既有会话草稿版；不存在返回 ``None``（存在性未知不冒充可写）。"""

        try:
            read = await self._facade.read_session(
                SessionReadQuery(session_id=session_id, limit=1),
                user_scope=user_scope,
            )
        except AgentServiceError as exc:
            if exc.code is AgentErrorCode.INPUT_INVALID:
                return None
            raise
        return int(read.summary.draft_revision)


def _require_non_empty(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{name} must be non-empty", "business_research")


def _raise_if_material_limit(materials: Sequence[ResearchMaterial]) -> None:
    """空清单与 D06 越限都如实拒绝（不返回空成功）。"""

    if not materials:
        raise MaterialLimitExceeded(MaterialLimitViolation(MATERIAL_EMPTY, 0, 1, 0))
    for index, material in enumerate(materials):
        if material.kind not in MATERIAL_KINDS:
            raise MaterialLimitExceeded(MaterialLimitViolation(MATERIAL_KIND_INVALID, index, 0, 0))
    violation = material_limit_violation(materials)
    if violation is not None:
        raise MaterialLimitExceeded(violation)


def _transfer_conflict(exc: AgentServiceError) -> BusinessDraftTransferConflict:
    """facade 类型化错误 → 可解析冲突码（未知码保留原名，不伪装成功）。"""

    if exc.code is AgentErrorCode.REVISION_CONFLICT:
        return BusinessDraftTransferConflict(TARGET_DRAFT_CONFLICT)
    if exc.code is AgentErrorCode.INPUT_CONFLICT:
        return BusinessDraftTransferConflict(TRANSFER_CONFLICT)
    if exc.code is AgentErrorCode.POLICY_DENIED:
        return BusinessDraftTransferConflict(TARGET_SESSION_UNAVAILABLE)
    if exc.code is AgentErrorCode.INPUT_INVALID:
        return BusinessDraftTransferConflict(TARGET_NOT_TRANSFERABLE)
    return BusinessDraftTransferConflict(str(getattr(exc.code, "value", exc.code)))


def _material_version_ref(material: ResearchMaterial) -> SourceVersionRef:
    return SourceVersionRef(
        source_kind=SourceKind.ATTACHMENT,
        source_id=material.source_id,
        source_version=material.source_version,
        source_digest=material.source_version,
    )


def _material_source_item(material: ResearchMaterial, *, user_scope: str) -> BusinessSourceItem:
    """资料 → 可登记来源（``available_at`` 用内容时点；未知保持 ``None``，不填当前时间）。"""

    object_ref = f"attachment:{material.source_id}"
    source = SourceRef(
        user_scope=user_scope,
        source_id=material.source_id,
        version=material.source_version,
        digest=material.source_version,
        source_kind=SourceKind.ATTACHMENT,
        display_name=material.display_name,
        object_ref=object_ref,
        locator=SourceLocator(
            locator_kind="source_range",
            origin_ref=SourceVersionRef(
                source_kind=SourceKind.ATTACHMENT,
                source_id=material.source_id,
                source_version=material.source_version,
                source_digest=material.source_version,
            ),
            selection=Selection(kind="all"),
        ),
        available_at=material.content_at,
        period=None,
        unit_note=None,
        read_state=material.read_state,
        missing_reason=material.missing_reason,
        provenance_refs=(),
    )
    return BusinessSourceItem(source=source, verification=VerificationState.UNVERIFIED, body_text=None)


def _materials_payload(materials: Sequence[ResearchMaterial]) -> DraftPayload:
    """资料包草稿载荷：逐件列出名称与版本，并如实标注未读/部分读取数量。"""

    lines = ["请研究以下资料并说明结论与依据："]
    for material in materials:
        lines.append(f"- {material.display_name}（{material.kind}，版本 {material.source_version[:12]}）")
    read_count, unread_count = material_read_counts(materials)
    if unread_count:
        lines.append("")
        lines.append(f"注意：{unread_count} 件资料未读或仅部分读取，未读内容不进入完整结论。")
    return DraftPayload(
        schema_version=DRAFT_PAYLOAD_SCHEMA_VERSION,
        text="\n".join(lines),
        mode=Mode.DISCUSS,
        model_selection=None,
        source_refs=tuple(_material_version_ref(material) for material in materials),
        skill_versions=(),
        mcp_selection=(),
        purpose=_OBJECT_PURPOSE,
        # 研究发起时间：资料包本身未提供发起时点，保持 None（不填当前时间，R21）。
        research_date=None,
        cutoff=None,
        send_target=None,
        object_context=json_doc(
            {
                "source_module": "business_research",
                "material_count": len(materials),
                "material_read": read_count,
                "material_unread": unread_count,
            }
        ),
        role_prompt_selection=None,
        customized_fields=(),
        defaults_baseline_digest=None,
    )


def _canonical_context(context: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(json_doc(dict(context)))
    except AgentValidationError as exc:
        raise AgentServiceError(
            AgentErrorCode.INPUT_INVALID,
            f"object_context is not canonical json: {exc.code}",
            "business_research",
        ) from exc


def _draft_payload(
    preparation: BusinessDraftPreparation,
    source: SourceRef,
    context: Mapping[str, Any],
) -> DraftPayload:
    """草稿载荷：来源引用 + 冻结对象上下文；截止界留空（发送前由用户确认）。"""

    version = source.version
    text = _DRAFT_TEXT_TEMPLATE.format(
        display_name=preparation.display_name,
        kind=context.get("object_kind"),
        object_id=context.get("object_id"),
        version=version[:12],
    )
    if context.get("availability_state") == _AVAILABILITY_UNKNOWN:
        # 受限稿：用户可见地标注时点未知与发送期后果，不把未知伪装成已可证。
        text += _RESTRICTED_AVAILABILITY_NOTE
    return DraftPayload(
        schema_version=DRAFT_PAYLOAD_SCHEMA_VERSION,
        text=text,
        mode=Mode.DISCUSS,
        model_selection=None,
        source_refs=(
            SourceVersionRef(
                source_kind=source.source_kind,
                source_id=source.source_id,
                source_version=version,
                source_digest=source.digest,
            ),
        ),
        skill_versions=(),
        mcp_selection=(),
        purpose=preparation.purpose,
        research_date=_today_local(),
        # 截止界未知不填（R21/R24）：发送前必须由用户确认，不以准备时刻冒充。
        cutoff=None,
        send_target=None,
        object_context=json_doc(dict(context)),
        role_prompt_selection=None,
        customized_fields=(),
        defaults_baseline_digest=None,
    )


def utc_now() -> str:
    """当前时刻的 UTC 微秒文本（业务入口抓取快照时的可得时点）。"""

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
