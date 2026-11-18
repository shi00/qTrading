"""Agent 统一服务入口（DD §4.1/§4.2）。

本模块只做**事实读写的服务边界**：所有持久事实由 ``AgentDao`` 在显式事务内读写，
服务层不另建执行推进器、不缓存可写状态。

当前分片交付只读查询面与 ``aclose``（F-01 前置）：

- ``read_command`` 查命令保存/投递/消费原事实（§4.2，不生成新决定）；
- ``read_session`` / ``get_snapshot`` / ``read_events`` / ``observe_session`` 为
  §4.1/§4.2 读取入口，直接复用 C-03 一致读取截点与观察实现；
- ``read_cost_projection`` 以 operation 行的 ``invocation_id`` / ``attempt_refs``
  引用原模型调度账本只读投影费用（0 / 已知金额 / 预留 / 未计价 / 读取失败分列，
  不写库、不估算补齐）；
- ``read_delivery`` 查回原交付 manifest 与当前修订（§10.5 unknown/中断核对）；
- ``control`` / ``answer`` / ``search`` / ``stop_using`` / ``mark_read`` 为 §4.1
  统一合同写入口，一律委托既有 DAO 事务；``wait_result`` 为只读等待（组合快照与
  事件，零持久化），超时返回「未收敛」事实而不伪造成果；
- ``submit`` / ``deliver_artifact`` 等写路径中，``submit`` 先落定受理事务；组合根
  注入 ``ResearchDispatcher`` 时，受理成功后立即把回执投递给持久父研究 workflow
  （派发失败/门闩关闭只产生零派发事实，不改写受理）；``submit_and_dispatch``
  额外返回派发事实，未注入派发器时零派发并如实返回 ``dispatch=None``。
- ``save_draft`` 只做本地草稿 CAS 持久（不产生派发/费用），随 F-03 交付；
- ``change_session`` 只做会话元数据 CAS（create/rename/pin/archive/unarchive）与
  修订/分支的准备（revise 只核源消息/截点、不改会话因果头；branch 新建会话并
  引用原消息身份/版本/截点，不复制正文、不派发）；因果头在 ``accept_request``
  以修订声明受理新意图时随该事务原子切换（R2-I25）；
- ``transfer_draft`` / ``read_draft_transfer`` 只做草稿跨会话移入与原阶段查回
  （不发送、不派发研究、零费用）。

``aclose`` 只标记服务关闭并拒绝后续读取；DAO/Engine 生命周期归应用组合根
（DD §10.6：宿主关闭由 Runtime 前置门闩编排，服务不越权释放共享引擎）。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from core.agent_types import (
    NON_RECOVERABLE_DISPLAY_STATES,
    AcceptRequest,
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    AnswerReceipt,
    ArtifactRead,
    ArtifactReadQuery,
    CommandReceipt,
    CommandRef,
    CostProjection,
    DeliveryRead,
    DraftReceipt,
    DraftSaveCommand,
    DraftTransferCommand,
    DraftTransferReceipt,
    EventPage,
    EventReadQuery,
    HistoryPage,
    HistoryQuery,
    OperationRead,
    OperationReadQuery,
    ReadMarkCommand,
    ReadMarkReceipt,
    ResearchEvent,
    ResearchSnapshot,
    RevisionReceipt,
    ReviseQueuedRequest,
    SaveAnswerRequest,
    SessionRead,
    SessionReadQuery,
    SessionChangeCommand,
    SourceRead,
    SourceReadQuery,
    SourceStopCommand,
    SourceStopReceipt,
    SubmissionReceipt,
    derive_accept_identity,
    validate_draft_save_command,
    validate_draft_transfer_command,
    validate_history_query,
    validate_read_mark_command,
    validate_session_change_command,
    decode_json_doc,
)
from data.persistence.daos.agent_dao import AgentDao
from data.persistence.daos.ai_usage_dao import AIUsageDao
from services.agent_service.comparison import (
    STOP_REASON_USER_ENDED_GROUP,
    ComparisonGroupStopPlan,
    ComparisonGroupView,
    ComparisonLaneObservation,
    ComparisonLaneReceipt,
    ComparisonReadQuery,
    ComparisonSubmissionReceipt,
    ComparisonSubmissionRequest,
    FrozenComparisonGroup,
    build_comparison_view,
    build_lane_accept_request,
    comparison_export_rows,
    decode_comparison_manifest,
    freeze_comparison_group,
    observations_from_executions,
    plan_group_stop,
)
from services.agent_service.dispatch import DispatchOutcome, ResearchDispatcher, ResearchWorkflowResult
from utils.error_classifier import classify_error


@dataclass(frozen=True, slots=True)
class WaitResult:
    """``wait_result`` 观察结果：收敛与否如实分列，超时不伪造成果（R21）。

    ``snapshot`` 恒为最后一次一致读取的事实（超时也返回该事实，调用方据此区分
    「还没结果」与「读失败」）；``events`` 为本次等待窗口内追平的事件增量，
    不带持久化副作用。
    """

    execution_id: str
    display_state: str
    converged: bool
    timed_out: bool
    elapsed_ms: int
    snapshot: ResearchSnapshot
    events: tuple[ResearchEvent, ...]


@dataclass(frozen=True, slots=True)
class SubmissionOutcome:
    """受理 + 派发结果：受理回执原样保留，派发事实单列（未接线时为 None）。

    ``dispatch.submitted=False`` 只表示本次零派发及其原因；受理事实仍是
    ``COMMITTED``，不得据未派发把受理读成失败（INV-06）。
    """

    receipt: SubmissionReceipt
    dispatch: DispatchOutcome | None


def _require_positive_seconds(value: object, path: str, identity: str) -> None:
    """等待参数边界校验：布尔/非数值/非正数一律 ``INPUT_INVALID``。"""

    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"invalid_range: {path}", identity)


def _dedupe_cost_refs(values: Sequence[str], path: str) -> tuple[str, ...]:
    """费用投影引用入参：非空文本、去重保序；空串/非文本 → ``INPUT_INVALID``。"""

    refs: list[str] = []
    for index, value in enumerate(values):
        if not isinstance(value, str) or value == "":
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"invalid_ref: {path}[{index}]", "cost_projection")
        refs.append(value)
    return tuple(dict.fromkeys(refs))


def _projected_cost(value: object) -> Decimal | None:
    """账本金额读数 → Decimal；``None``/布尔/坏值/非有限/负值 → ``None``（未知）。

    坏值不按 0 处理：把不可解析的存量垃圾读成「免费」即伪装缺失（R21），
    归入未计价列由调用方呈现为未知成本。
    """

    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, ArithmeticError):
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return amount


def _projected_tokens(value: object) -> int:
    """未计价用量规模读数：非非负整数一律 0（仅规模参考，不参与金额判定）。"""

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _failed_cost_projection(
    ledger_refs: tuple[str, ...], attempt_refs: tuple[str, ...], read_error: str
) -> CostProjection:
    """读取失败投影：只保引用与失败态，金额/计数不倒推为 0（R21）。"""

    return CostProjection(
        read_state="failed",
        read_error=read_error,
        ledger_refs=ledger_refs,
        attempt_refs=attempt_refs,
        zero_cost_attempts=0,
        known_cny=None,
        reserved_cny=None,
        unpriced_attempts=0,
        unpriced_input_tokens=0,
        unpriced_output_tokens=0,
    )


class AgentService:
    """统一受理/查询 facade（DD §4.1）。构造后由应用组合根持有同一实例。"""

    def __init__(
        self,
        dao: AgentDao,
        usage_dao: AIUsageDao | None = None,
        *,
        dispatcher: ResearchDispatcher | None = None,
    ) -> None:
        """``usage_dao`` 为共享模型调度账本（同一引擎）；缺省时费用投影如实报读取失败。

        ``dispatcher`` 由组合根在宿主就绪后注入（受理 → 持久 workflow 派发）；
        缺省时服务只提供受理事实与读取面（零派发，不伪造投递）。
        """

        self._dao = dao
        self._usage_dao = usage_dao
        self._dispatcher = dispatcher
        self._closed = False

    # ------------------------------------------------------------------
    # 读取面（§4.1 get_snapshot / §4.2 read_* / observe）
    # ------------------------------------------------------------------

    async def read_command(self, ref: CommandRef, *, user_scope: str, target_id: str | None = None) -> CommandReceipt:
        """查命令原回执（保存/投递/消费分列）；未知时不猜、不重发。

        ``target_id`` 可选：调用方已知准确 target 时给出，按该 target 下的命令记录
        定位（同一命令键跨 target 合法并存，检视 D20）。
        """

        self._require_open("read_command")
        return await self._dao.read_command(ref, user_scope=user_scope, target_id=target_id)

    async def read_session(self, query: SessionReadQuery, *, user_scope: str) -> SessionRead:
        """主会话/目录首屏（§4.2）：多执行摘要与首屏分页，不复制成果正文。"""

        self._require_open("read_session")
        return await self._dao.get_session_read(query, user_scope=user_scope)

    async def get_snapshot(
        self,
        execution_id: str,
        *,
        user_scope: str,
        limit: int = 50,
        round_id: str | None = None,
        message_cursor: str | None = None,
        artifact_cursor: str | None = None,
    ) -> ResearchSnapshot:
        """执行快照（§4.3）：同一可靠截点的水位/投影/分页。"""

        self._require_open("get_snapshot")
        return await self._dao.get_execution_snapshot(
            execution_id,
            user_scope=user_scope,
            limit=limit,
            round_id=round_id,
            message_cursor=message_cursor,
            artifact_cursor=artifact_cursor,
        )

    async def read_cost_projection(
        self,
        *,
        ledger_refs: Sequence[str],
        attempt_refs: Sequence[str] = (),
    ) -> CostProjection:
        """执行费用的只读投影（§4.3 usage 费用面；ADR-0014 引用原账本）。

        ``ledger_refs`` 为原账本 invocation_id（operation 行的 ``invocation_id``），
        ``attempt_refs`` 为调用方已知的 attempt 引用；二者去重保序回传。只读：
        仅调用账本读取方法，零写入、零估算补齐、零预留/结算状态推进。

        五态分别保真（见 :class:`CostProjection`）：读取失败返回
        ``read_state="failed"`` 且金额不倒推为 0；无已计价 attempt 时
        ``known_cny`` 为 ``None``（未知）而非 ``Decimal("0")``。引用存在但账本
        无行按未计价（未知成本）计入，不丢引用。``CancelledError`` 继续传播（R2）。
        """

        self._require_open("read_cost_projection")
        ledger = _dedupe_cost_refs(ledger_refs, "ledger_refs")
        declared_attempts = _dedupe_cost_refs(attempt_refs, "attempt_refs")
        usage_dao = self._usage_dao
        if usage_dao is None:
            return _failed_cost_projection(ledger, declared_attempts, "ledger_unavailable")
        try:
            rows: list[dict[str, Any]] = []
            for ref in ledger:
                rows.extend(await usage_dao.read_request_attempts(invocation_id=ref))
            reserved = await usage_dao.read_active_reserved_cny(invocation_ids=ledger) if ledger else {}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error_code = str(classify_error(exc, context="general").get("code", type(exc).__name__))
            return _failed_cost_projection(ledger, declared_attempts, error_code)

        observed: dict[str, dict[str, Any]] = {}
        for row in rows:
            attempt_id = row.get("attempt_id")
            if isinstance(attempt_id, str) and attempt_id:
                observed.setdefault(attempt_id, row)
        # 引用完整：调用方声明的引用 + 账本实读 attempt，去重保序。
        attempt_ids = tuple(dict.fromkeys((*declared_attempts, *observed)))

        known: Decimal | None = None
        zero_cost_attempts = 0
        unpriced_attempts = sum(1 for ref in declared_attempts if ref not in observed)
        unpriced_input_tokens = 0
        unpriced_output_tokens = 0
        for row in observed.values():
            amount = _projected_cost(row.get("cost_cny"))
            if amount is None:
                unpriced_attempts += 1
                unpriced_input_tokens += _projected_tokens(row.get("input_total_tokens"))
                unpriced_output_tokens += _projected_tokens(row.get("output_tokens"))
                continue
            known = amount if known is None else known + amount
            if amount == 0:
                zero_cost_attempts += 1

        reserved_cny = sum(reserved.values(), Decimal("0")) if ledger else None
        return CostProjection(
            read_state="ok",
            read_error=None,
            ledger_refs=ledger,
            attempt_refs=attempt_ids,
            zero_cost_attempts=zero_cost_attempts,
            known_cny=known,
            reserved_cny=reserved_cny,
            unpriced_attempts=unpriced_attempts,
            unpriced_input_tokens=unpriced_input_tokens,
            unpriced_output_tokens=unpriced_output_tokens,
        )

    async def read_events(self, query: EventReadQuery) -> EventPage:
        """可靠事件增量（§4.4）：用户域与水位由 query 承载。"""

        self._require_open("read_events")
        return await self._dao.read_events(query)

    async def read_source(self, query: SourceReadQuery, *, user_scope: str) -> SourceRead:
        """精确资料卡/依据详情（§4.2）：只读已准备好的选定表示。

        未准备表示由 DAO 返回 ``read_state`` 与原因（不现场解析、不扩大范围）；
        版本/摘要/定位不符为 ``REVISION_CONFLICT``，坏事实为 ``OPERATION_UNKNOWN``。
        """

        self._require_open("read_source")
        return await self._dao.read_source(query, user_scope=user_scope)

    async def read_artifact(self, query: ArtifactReadQuery, *, user_scope: str) -> ArtifactRead:
        """完整成果/历史/多模型单路（§4.2）：准确版本，正文按游标分块传输。

        正文未保存与已清理分别给出明确原因（不空正文冒充成果）；身份不符为
        ``INPUT_INVALID``，坏行/坏载荷为 ``OPERATION_UNKNOWN``/``PERSISTENCE_UNAVAILABLE``。
        """

        self._require_open("read_artifact")
        return await self._dao.read_artifact(query, user_scope=user_scope)

    async def read_delivery(self, *, user_scope: str, operation_id: str) -> DeliveryRead:
        """原交付查回（§10.5）：按原 operation 定位 manifest 与当前修订。

        unknown/中断核对只读原身份与事实，不按当前表单重推目标/格式；未开始
        交付返回 ``manifest=None``（初始态），坏行为 ``OPERATION_UNKNOWN``。
        """

        self._require_open("read_delivery")
        return await self._dao.read_delivery(user_scope=user_scope, operation_id=operation_id)

    async def read_operation(self, query: OperationReadQuery, *, user_scope: str) -> OperationRead:
        """公开过程/完整工具返回（§4.2）：调用元数据与 effect/io 事实单列。

        ``unknown`` 原样返回且不给结果（不猜成功/失败）；大结果按 ``result_ref``
        正文游标分页；身份不符为 ``INPUT_INVALID``。
        """

        self._require_open("read_operation")
        return await self._dao.read_operation(query, user_scope=user_scope)

    def observe_session(
        self,
        session_id: str,
        *,
        user_scope: str,
        after_seq: int = 0,
        poll_interval_s: float = 0.25,
    ) -> AsyncIterator[ResearchEvent]:
        """会话可靠事件订阅（§4.2 observe_session）：退订只关本观察者，不控制执行。"""

        self._require_open("observe_session")
        return self._dao.observe_session_events(
            session_id,
            user_scope=user_scope,
            after_seq=after_seq,
            poll_interval_s=poll_interval_s,
        )

    async def search(self, query: HistoryQuery, *, user_scope: str) -> HistoryPage:
        """历史搜索（§4.2 ``search``）：返回原查询页，不借当前表单重推范围。

        边界整份校验（R02 范围/值集/游标语法），失败为 ``INPUT_INVALID`` 且不
        触达 DAO；``user_scope`` 必填并作为用户域唯一来源（与 query 内 scope 分列，
        不互相顶替）。游标失效、覆盖域修订等原样由 DAO 抛类型化错误，不把读取
        失败读成「无命中」（R21）。
        """

        self._require_open("search")
        if not isinstance(user_scope, str) or not user_scope:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                "invalid_type: $.user_scope",
                "history",
            )
        try:
            validate_history_query(query)
        except AgentValidationError as exc:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                f"{exc.code}: {exc.field_path}",
                f"{user_scope}/history",
            ) from exc
        return await self._dao.search_history(query, user_scope=user_scope)

    async def wait_result(
        self,
        execution_id: str,
        *,
        user_scope: str,
        timeout_s: float,
        poll_interval_s: float = 0.5,
        limit: int = 50,
        after_seq: int = 0,
    ) -> WaitResult:
        """只读等待收敛（§4.1 ``wait_result``）：组合快照与事件，零持久化。

        每次轮询以 ``get_execution_snapshot`` 取一致截点、以 ``read_events`` 追平
        会话事件增量；``display_state`` 进入终态集（``NON_RECOVERABLE_DISPLAY_STATES``）
        才算收敛。到达调用方给定的 ``timeout_s`` 仍未收敛时返回 ``converged=False``/
        ``timed_out=True`` 与最后一次快照事实，**不伪造结果、不写任何事实**（R21）。
        等待用 ``asyncio.sleep`` 让出事件循环；取消按 ``CancelledError`` 原样传播
        （R2），不转译成 ACK/空结果。
        """

        self._require_open("wait_result")
        _require_positive_seconds(timeout_s, "$.timeout_s", execution_id)
        _require_positive_seconds(poll_interval_s, "$.poll_interval_s", execution_id)
        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + timeout_s
        cursor = after_seq
        observed: list[ResearchEvent] = []
        while True:
            snapshot = await self._dao.get_execution_snapshot(
                execution_id,
                user_scope=user_scope,
                limit=limit,
            )
            page = await self._dao.read_events(
                EventReadQuery(
                    user_scope=user_scope,
                    session_id=snapshot.identity.session_id,
                    after_seq=cursor,
                    limit=limit,
                )
            )
            observed.extend(page.events)
            cursor = page.last_seq
            state = snapshot.execution.display_state
            converged = state in NON_RECOVERABLE_DISPLAY_STATES
            elapsed_ms = int((loop.time() - started) * 1000)
            if converged:
                return WaitResult(
                    execution_id=execution_id,
                    display_state=state,
                    converged=True,
                    timed_out=False,
                    elapsed_ms=elapsed_ms,
                    snapshot=snapshot,
                    events=tuple(observed),
                )
            remaining = deadline - loop.time()
            if remaining <= 0:
                return WaitResult(
                    execution_id=execution_id,
                    display_state=state,
                    converged=False,
                    timed_out=True,
                    elapsed_ms=elapsed_ms,
                    snapshot=snapshot,
                    events=tuple(observed),
                )
            await asyncio.sleep(min(poll_interval_s, remaining))

    # ------------------------------------------------------------------
    # 多模型独立分析（F37/SQ-08）
    # ------------------------------------------------------------------

    async def submit_comparison(self, request: ComparisonSubmissionRequest) -> ComparisonSubmissionReceipt:
        """受理一组多模型独立分析（F37）：共同输入一次冻结，逐路独立完整任务。

        组内各路共享同一规范 manifest（相同正文/资料/用途/cutoff/历史前缀/角色
        Prompt/Skills/MCP 范围/输出要求），各自使用所选具体连接（冻结 ID/revision，
        运行期不得替换）。每路是 ``comparison_lane`` 独立总执行，按用户所选顺序
        逐路排队；某一路受理失败如实记录该路错误码，不伪造已受理，不影响其他
        路已保存事实。组受理成功不代表已派发或已消费（INV-06）。
        """

        self._require_open("submit_comparison")
        try:
            group = freeze_comparison_group(
                user_scope=request.user_scope,
                session_id=request.session_id,
                source_module=request.source_module,
                group_command_id=request.group_command_id,
                shared=request.shared,
                lanes=request.lanes,
            )
        except AgentValidationError as exc:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                f"{exc.code}: {exc.field_path}",
                f"{request.user_scope}/{request.session_id}",
            ) from exc
        receipts: list[ComparisonLaneReceipt] = []
        for lane in group.lanes:
            accept = build_lane_accept_request(
                group,
                lane,
                expected_revision=request.expected_revision,
                node_id=request.node_id,
                workflow_id=request.workflow_id,
                source_display_name=request.source_display_name,
            )
            try:
                receipt = await self._dao.accept_request(accept)
            except AgentServiceError as exc:
                receipts.append(
                    ComparisonLaneReceipt(
                        lane_ref=lane.lane_ref,
                        execution_id=None,
                        command_record_id=None,
                        commit_state=None,
                        error_code=exc.code.value,
                    )
                )
                continue
            receipts.append(
                ComparisonLaneReceipt(
                    lane_ref=lane.lane_ref,
                    execution_id=receipt.execution_id,
                    command_record_id=receipt.command_record_id,
                    commit_state=receipt.commit_state,
                    error_code=None,
                )
            )
        return ComparisonSubmissionReceipt(
            group_ref=group.group_ref,
            manifest_digest=group.manifest_digest,
            lane_count=len(group.lanes),
            lanes=tuple(receipts),
        )

    async def _load_comparison(self, query: ComparisonReadQuery) -> tuple[FrozenComparisonGroup, ComparisonGroupView]:
        """读比较组共同 manifest + 会话内逐路事实并收敛（只读，零派发）。"""

        self._require_open("read_comparison")
        identity = f"{query.user_scope}/{query.session_id}/{query.group_ref}"
        if not isinstance(query.user_scope, str) or query.user_scope == "":
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "user_scope must be non-empty text", identity)
        if not isinstance(query.session_id, str) or query.session_id == "":
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "session_id must be non-empty text", identity)
        if not isinstance(query.group_ref, str) or query.group_ref == "":
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "group_ref must be non-empty text", identity)
        if isinstance(query.limit, bool) or not isinstance(query.limit, int) or not 1 <= query.limit <= 50:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "invalid_range: $.limit", identity)
        payload = await self._dao.read_comparison_manifest(user_scope=query.user_scope, group_ref=query.group_ref)
        if payload is None:
            raise AgentServiceError(AgentErrorCode.OPERATION_UNKNOWN, "comparison group not found", identity)
        try:
            group = decode_comparison_manifest(decode_json_doc(payload))
        except (AgentValidationError, ValueError) as exc:
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN, "comparison manifest unreadable", identity
            ) from exc
        if group.user_scope != query.user_scope or group.session_id != query.session_id:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "comparison group outside target session", identity)
        session = await self._dao.get_session_read(
            SessionReadQuery(session_id=query.session_id, limit=query.limit),
            user_scope=query.user_scope,
        )
        observations = observations_from_executions(group, session.executions)
        return group, build_comparison_view(group, observations)

    async def read_comparison(self, query: ComparisonReadQuery) -> ComparisonGroupView:
        """只读比较（F37）：按准确组身份查各路既有成果与状态，零模型/零新派发。

        未知/未启动/结果未确认逐路如实保留，不伪造成功或失败（R21）；历史按组
        找回，不按当前设置重推连接或共同输入。
        """

        _, view = await self._load_comparison(query)
        return view

    async def export_comparison(self, query: ComparisonReadQuery) -> tuple[dict[str, str], ...]:
        """只读导出（沿 F26 原成果规则）：逐路原状态，不新增合并报告或模型调用。"""

        view = await self.read_comparison(query)
        return comparison_export_rows(view)

    async def stop_comparison_group(
        self,
        query: ComparisonReadQuery,
        *,
        reason: str = STOP_REASON_USER_ENDED_GROUP,
    ) -> ComparisonGroupStopPlan:
        """组一致停止计划（F37）：未派发撤回、在途停新派发、已完成成果保留。

        共同资料被停止使用时所有路共享同一冻结输入，故组内一致处理；在途未知
        仍核实（不伪回滚）。本入口只读事实并产出动作计划，不写库、不派发。
        """

        group, view = await self._load_comparison(query)
        observations = tuple(
            ComparisonLaneObservation(
                lane_ref=lane.lane_ref,
                display_state=lane.display_state,
                reliable_result=lane.reliable_result,
                artifact_refs=lane.artifact_refs,
            )
            for lane in view.lanes
        )
        return plan_group_stop(group, observations, reason=reason)

    # ------------------------------------------------------------------
    # 写入面（受理）
    # ------------------------------------------------------------------

    async def submit(self, request: AcceptRequest, *, command: CommandRef) -> SubmissionReceipt:
        """受理入口（§4.1/§5.1/INV-01）：可信命令引用必须与受理输入逐字段一致。

        受理事务落定后，若组合根注入了 ``ResearchDispatcher``，立即把回执投递给
        持久父研究 workflow（派发失败/门闩关闭只产生零派发事实，不回滚也不改写
        受理）；需要同时拿到派发事实时用 ``submit_and_dispatch``。``commit_state``
        始终只描述保存事实，不代表已投递或已消费（INV-06）。

        拒绝语义：命令引用与受理输入任一字段不一致 → ``INPUT_INVALID``（不以
        调用方声称的幂等键覆盖真实身份）；受理输入本身不合法 → 由
        ``derive_accept_identity`` 的校验转为 ``INPUT_INVALID``（类型错不吞）。
        """

        receipt, _ = await self._accept_and_dispatch(request, command=command)
        return receipt

    async def _accept_and_dispatch(
        self, request: AcceptRequest, *, command: CommandRef
    ) -> tuple[SubmissionReceipt, DispatchOutcome | None]:
        """校验 → 受理事务落定 → 按注入的派发器投递（未注入即零派发）。

        dispatch 的失败已在派发器内折叠为 ``submitted=False`` + 原因（不抛出），
        故受理成功语义不受派发结果影响；``CancelledError`` 照常传播（R2）。
        """

        self._require_open("submit")
        identity = f"{request.user_scope}/{request.session_id}"
        try:
            # 只借校验与稳定身份派生（§3.3.2）；持久 PK 仍由 DAO 事务内再派生一次。
            derive_accept_identity(request)
        except AgentValidationError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", identity) from exc
        mismatches = [
            name
            for name, ref_value, request_value in (
                ("user_scope", command.user_scope, request.user_scope),
                ("source_module", command.source_module, request.source_module),
                ("business_request_id", command.business_request_id, request.business_request_id),
                ("command_id", command.command_id, request.client_command_id),
                ("expected_revision", command.expected_revision, request.expected_revision),
            )
            if ref_value != request_value
        ]
        if mismatches:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                f"command ref does not match request: {','.join(mismatches)}",
                identity,
            )
        receipt = await self._dao.accept_request(request)
        dispatcher = self._dispatcher
        if dispatcher is None:
            return receipt, None
        outcome = await dispatcher.dispatch(receipt, user_scope=request.user_scope)
        return receipt, outcome

    async def submit_and_dispatch(self, request: AcceptRequest, *, command: CommandRef) -> SubmissionOutcome:
        """受理 + 派发（组合根注入派发器时的生产入口）：可靠保存后才投递。

        受理事实与派发事实分开返回：派发失败/门闩关闭不影响已保存的受理
        （``receipt`` 仍为原回执），``dispatch.submitted`` 如实标注零派发原因。
        未注入派发器时 ``dispatch=None``（零派发，不伪造已投递）。
        """

        receipt, outcome = await self._accept_and_dispatch(request, command=command)
        return SubmissionOutcome(receipt=receipt, dispatch=outcome)

    async def read_research_result(self, workflow_id: str) -> ResearchWorkflowResult:
        """读回已派发研究的持久结果（原生状态 + 成功输出；不等待不推断）。"""

        self._require_open("read_research_result")
        dispatcher = self._dispatcher
        if dispatcher is None:
            raise AgentServiceError(
                AgentErrorCode.PERSISTENCE_UNAVAILABLE,
                "research dispatcher is not wired",
                "agent_service",
            )
        return await dispatcher.read_result(workflow_id)

    async def control(self, request: ReviseQueuedRequest) -> RevisionReceipt:
        """控制入口（§4.1 ``control``）：只按既有类型能力暴露「排队期修订」。

        当前落库的控制命令族仅有 ``revise_queued``（SQ-07），故本入口只转交该
        类型，不臆造取消/暂停等未落库命令。同一入口兼「投递 + 查回」：同命令键
        同完整输入（正文/manifest/期望版/node 摘要相等）时由 DAO 事务查回**原
        回执**（零新行、不重发）；同键异输入 → ``INPUT_CONFLICT``；已派发或非
        ``queued`` → ``REVISION_CONFLICT``，均不以当前状态重推。

        原生投递接续归 Runtime（submit 回执 ``workflow_id`` 后的 launch，E-04/Q5）；
        本入口不派发、不越权推进。
        """

        self._require_open("control")
        return await self._dao.revise_queued(request)

    async def answer(self, request: SaveAnswerRequest) -> AnswerReceipt:
        """答复入口（§4.1 ``answer``）：委托答复线性化事务，回执三态分列。

        保存成功不代表已投递/已消费（INV-06）。同一入口兼「投递 + 查回」：同
        命令键同规范输入且决定已指向该命令 → DAO 查回**原回执**（零新行、不按
        当前状态重判）；同键异输入 → ``INPUT_CONFLICT``；决定已被其他命令应答
        或预期版不符 → ``REVISION_CONFLICT``。原生 send 后的投递核定与消费事实
        归 Runtime 路径，本入口不代做。
        """

        self._require_open("answer")
        return await self._dao.save_answer(request)

    async def stop_using(self, request: SourceStopCommand) -> SourceStopReceipt:
        """停止使用来源入口（§4.1 ``stop_using``）：委托源禁用事务，零新出站。

        DAO 层幂等键为**源身份**（source_id + source_version）：已禁用重放返回
        原事实 ``already_disabled=True``（零新事实、不复活）；``command_id`` 仅
        审计透传，命令键查重/同键异输入冲突由后续命令落库路径承载（本层不伪称
        已完成命令键去重）。禁用先胜：只改当前可用性投影，不改冻结事实。
        """

        self._require_open("stop_using")
        return await self._dao.stop_source_use(request)

    async def mark_read(self, command: ReadMarkCommand) -> ReadMarkReceipt:
        """已读标记入口（§4.1 ``mark_read``）：委托 GREATEST/并集合并事务。

        边界整份校验（水位与 scope digest 同给同缺、身份为字符串），失败为
        ``INPUT_INVALID`` 且不触达 DAO；merge 天然幂等，本次无状态变化即重放
        （``replayed=True``），不全局清零、不回退水位（R22）。
        """

        self._require_open("mark_read")
        identity = f"{command.user_scope}/{command.session_id}/{command.command_id}"
        try:
            validate_read_mark_command(command)
        except AgentValidationError as exc:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                f"{exc.code}: {exc.field_path}",
                identity,
            ) from exc
        return await self._dao.save_read_mark_max(command)

    async def save_draft(self, command: DraftSaveCommand) -> DraftReceipt:
        """本地自动草稿保存（§4.2 ``save_draft``）：可靠 draft revision，不发送。

        命令在边界处整份校验（全键/标签/集合/基线摘要/禁凭据），失败为
        ``INPUT_INVALID``，不触达 DAO；CAS 冲突、同键异输入与重放语义由 DAO
        事务裁决（``REVISION_CONFLICT`` / ``INPUT_CONFLICT`` / 查回原回执）。
        """

        self._require_open("save_draft")
        try:
            validate_draft_save_command(command)
        except AgentValidationError as exc:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                f"{exc.code}: {exc.field_path}",
                f"{command.command.user_scope}/{command.session_id}",
            ) from exc
        return await self._dao.save_draft(command)

    async def change_session(self, command: SessionChangeCommand) -> CommandReceipt:
        """会话元数据命令（§4.2 ``change_session``）：元数据五动作 + 修订/分支。

        边界整份校验（动作自身字段、预期版与命令引用一致、源消息身份/版本）；
        ``revise`` 只准备新稿（核源消息/截点、不改因果头），``branch`` 只新建
        会话并引用原前缀，均不复制正文、不派发；因果头由受理事务（
        ``accept_request`` 的修订声明）原子切换，CAS/重放/冲突由 DAO 事务裁决。
        """

        self._require_open("change_session")
        try:
            validate_session_change_command(command)
        except AgentValidationError as exc:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                f"{exc.code}: {exc.field_path}",
                f"{command.command.user_scope}/{command.session_id}",
            ) from exc
        return await self._dao.change_session(command)

    async def transfer_draft(self, command: DraftTransferCommand) -> DraftTransferReceipt:
        """草稿移入（§4.2 ``transfer_draft``）：唯一可发送归属，不发送。

        边界整份校验（双方身份/预期版、源类别约束、payload 摘要、确认位），失败
        为 ``INPUT_INVALID`` 且不触达 DAO；双方 CAS、源 claim 仲裁与源清空由 DAO
        单事务裁决（``REVISION_CONFLICT`` / ``INPUT_CONFLICT``）。
        """

        self._require_open("transfer_draft")
        try:
            validate_draft_transfer_command(command)
        except AgentValidationError as exc:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                f"{exc.code}: {exc.field_path}",
                f"{command.command.user_scope}/{command.target_session_id}",
            ) from exc
        return await self._dao.transfer_draft(command)

    async def read_draft_transfer(
        self, ref: CommandRef, *, user_scope: str, target_session_id: str | None = None
    ) -> DraftTransferReceipt:
        """只查原转移回执（§4.2 ``read_draft_transfer``）：零移动、零发送。

        读取失败由 DAO 抛类型化错误（不存在/歧义/非转移命令 → ``INPUT_INVALID``；
        坏回执 → ``OPERATION_UNKNOWN``），服务不把失败读成「未转移」。
        ``target_session_id`` 可选：已知准确目标会话时按该 target 定位转移记录
        （同一命令键跨 target 合法并存，检视 D20）。
        """

        self._require_open("read_draft_transfer")
        return await self._dao.read_draft_transfer(ref, user_scope=user_scope, target_session_id=target_session_id)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        """关闭服务入口（幂等）：只停止本 facade，不释放共享 DAO/Engine。"""

        self._closed = True

    def _require_open(self, operation: str) -> None:
        if self._closed:
            raise AgentServiceError(
                AgentErrorCode.PERSISTENCE_UNAVAILABLE,
                f"agent service is closed ({operation})",
                "agent_service",
            )
