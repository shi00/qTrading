"""Harness 计划 / 摘要 / 边界适配（DD §10.3；HANDOFF §7.3，E-04）。

本模块只做「应用 DAO ↔ Harness 能力」的适配：

- :class:`PlanProposalStore` 实现 ``pydantic_ai_harness.planning.PlanStore``
  六方法，绑定 execution 与 plan_revision，以预期版 CAS 追加不可变提案；
  它只存提案，不改已派发图，也不是第二任务状态机；
- 后续分片在此追加 ``Planning`` / ``SummarizingCompaction`` / ``ResearchBoundary``
  装配（E-04-4）。

不在此处校验 DAG/角色/20 叶：那是派发前 ``validate_plan_spec_proposal`` 的职责。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol, cast

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import Model, ModelRequestContext
from pydantic_ai.tools import RunContext
from pydantic_ai_harness.compaction import SummarizingCompaction
from pydantic_ai_harness.planning import PlanItem, Planning, PlanStore, TaskStatus

from core.agent_types import AgentErrorCode, AgentServiceError, Mode, PlanItemSnapshot, PlanProposal

logger = logging.getLogger(__name__)


class PlanProposalDao(Protocol):
    """计划提案持久化端口（生产为 ``AgentDao``；测试可注入替身）。"""

    async def read_plan_proposal(
        self, *, user_scope: str, execution_id: str, plan_revision: int
    ) -> PlanProposal | None: ...

    async def save_plan_proposal(
        self,
        *,
        user_scope: str,
        execution_id: str,
        expected_revision: int,
        items: Sequence[PlanItemSnapshot],
    ) -> PlanProposal: ...


@dataclass(frozen=True, slots=True)
class PlanStoreBinding:
    """计划提案存储绑定：用户域 + 执行 + 起始计划版（0 = 尚无提案）。"""

    user_scope: str
    execution_id: str
    plan_revision: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.user_scope, str) or not self.user_scope:
            raise ValueError("plan store binding requires a non-empty user_scope")
        if not isinstance(self.execution_id, str) or not self.execution_id:
            raise ValueError("plan store binding requires a non-empty execution_id")
        if isinstance(self.plan_revision, bool) or not isinstance(self.plan_revision, int) or self.plan_revision < 0:
            raise ValueError("plan store binding requires plan_revision >= 0")


def _to_snapshot(item: object) -> PlanItemSnapshot:
    """Harness ``PlanItem`` → 冻结快照（越界形态明确拒绝，不静默丢弃字段）。"""

    if not isinstance(item, PlanItem):
        raise ValueError("plan item must be a pydantic_ai_harness PlanItem")
    status = item.status
    if not isinstance(status, TaskStatus):
        raise ValueError("plan item status must be a harness TaskStatus")
    depends_on = item.depends_on
    if not isinstance(depends_on, list) or any(not isinstance(dependency, str) for dependency in depends_on):
        raise ValueError("plan item depends_on must be a list of strings")
    if not isinstance(item.id, str) or not item.id:
        raise ValueError("plan item id must be non-empty text")
    if not isinstance(item.content, str):
        raise ValueError("plan item content must be text")
    if not isinstance(item.active_form, str):
        raise ValueError("plan item active_form must be text")
    if item.parent_id is not None and not isinstance(item.parent_id, str):
        raise ValueError("plan item parent_id must be text or None")
    return PlanItemSnapshot(
        id=item.id,
        content=item.content,
        status=status.value,
        active_form=item.active_form,
        parent_id=item.parent_id,
        depends_on=tuple(depends_on),
    )


def _to_plan_item(snapshot: PlanItemSnapshot) -> PlanItem:
    """冻结快照 → 独立 Harness ``PlanItem``（每次新建，不别名 store 状态）。"""

    return PlanItem(
        id=snapshot.id,
        content=snapshot.content,
        status=TaskStatus(snapshot.status),
        active_form=snapshot.active_form,
        parent_id=snapshot.parent_id,
        depends_on=list(snapshot.depends_on),
    )


class PlanProposalStore:
    """``PlanStore`` 六方法的应用 DAO 实现（绑定 execution + plan_revision）。

    读固定为当前绑定 revision（无提案 → 空）；写以**读取内容时的基线版**作
    ``expected_revision`` CAS 推进，成功后绑定 revision 同步前进。因此旧内容
    不可能配上较新的 CAS：同一实例上的并发增量编辑，先提交者成功，另一方以
    基线版 CAS 明确冲突（``REVISION_CONFLICT``），不会双成功而丢掉先提交者的
    步骤。冲突原样上抛：不静默重试、不覆盖他人新版本；冲突后绑定版不前移
    （不静默接受他人版本），调用方须以最新版重建 store 再重试。
    """

    def __init__(self, dao: PlanProposalDao, binding: PlanStoreBinding) -> None:
        self._dao = dao
        self._binding = binding
        self._revision = binding.plan_revision

    @property
    def binding(self) -> PlanStoreBinding:
        return self._binding

    @property
    def revision(self) -> int:
        """当前绑定的计划版（写入成功后前进）。"""

        return self._revision

    async def _read_items(self) -> tuple[int, list[PlanItem]]:
        """读绑定版并返回（内容基线版号, 步骤）；基线版号须随写入一起 CAS。"""

        baseline = self._revision
        if baseline == 0:
            return baseline, []
        proposal = await self._dao.read_plan_proposal(
            user_scope=self._binding.user_scope,
            execution_id=self._binding.execution_id,
            plan_revision=baseline,
        )
        if proposal is None:
            return baseline, []
        return baseline, [_to_plan_item(item) for item in proposal.items]

    async def _write(self, items: Sequence[PlanItemSnapshot], *, baseline_revision: int) -> PlanProposal:
        proposal = await self._dao.save_plan_proposal(
            user_scope=self._binding.user_scope,
            execution_id=self._binding.execution_id,
            expected_revision=baseline_revision,
            items=tuple(items),
        )
        self._revision = proposal.plan_revision
        return proposal

    async def get_items(self) -> list[PlanItem]:
        """返回当前绑定版的全部步骤（脱离副本，不别名 store 状态）。"""

        _, items = await self._read_items()
        return items

    async def set_items(self, items: list[PlanItem]) -> None:
        """整表替换（同内容不增版；空列表是合法计划）。"""

        await self._write([_to_snapshot(item) for item in items], baseline_revision=self._revision)

    async def get_item(self, item_id: str) -> PlanItem | None:
        """按 id 取一个步骤；不存在返回 ``None``。"""

        for item in await self.get_items():
            if item.id == item_id:
                return item
        return None

    async def add_item(self, item: PlanItem) -> PlanItem:
        """追加步骤；id 已存在抛 ``ValueError``（harness 契约，零 DB 写）。"""

        snapshot = _to_snapshot(item)
        baseline, current = await self._read_items()
        if any(existing.id == snapshot.id for existing in current):
            raise ValueError(f"A step with id {snapshot.id!r} is already in this plan.")
        await self._write([_to_snapshot(existing) for existing in current] + [snapshot], baseline_revision=baseline)
        return _to_plan_item(snapshot)

    async def update_item(
        self,
        item_id: str,
        *,
        content: str | None = None,
        status: TaskStatus | None = None,
        active_form: str | None = None,
        parent_id: str | None = None,
        depends_on: list[str] | None = None,
    ) -> PlanItem | None:
        """按非 ``None`` 字段更新步骤；不存在返回 ``None``（零 DB 写）。"""

        baseline, current = await self._read_items()
        for index, existing in enumerate(current):
            if existing.id != item_id:
                continue
            updated = existing.model_copy(deep=True)
            if content is not None:
                updated.content = content
            if status is not None:
                updated.status = status
            if active_form is not None:
                updated.active_form = active_form
            if parent_id is not None:
                updated.parent_id = parent_id
            if depends_on is not None:
                updated.depends_on = list(depends_on)
            snapshots = [_to_snapshot(item) for item in current]
            snapshots[index] = _to_snapshot(updated)
            await self._write(snapshots, baseline_revision=baseline)
            return _to_plan_item(_to_snapshot(updated))
        return None

    async def remove_item(self, item_id: str) -> bool:
        """删除步骤；不存在返回 ``False``（零 DB 写）。"""

        baseline, current = await self._read_items()
        kept = [item for item in current if item.id != item_id]
        if len(kept) == len(current):
            return False
        await self._write([_to_snapshot(item) for item in kept], baseline_revision=baseline)
        return True


def build_plan_store(dao: PlanProposalDao, binding: PlanStoreBinding) -> PlanProposalStore:
    """装配计划提案 store（生产由运行装配以受理的 execution/plan_revision 调用）。"""

    return PlanProposalStore(dao, binding)


# ---------------------------------------------------------------------------
# Planning 装配（HANDOFF §7.3；DD §10.3）
# ---------------------------------------------------------------------------


def resolve_plan_store(ctx: RunContext[Any]) -> PlanStore:
    """默认 store 解析器：从运行 deps 取已绑定的 store。

    不提供进程级隐式 store：deps 未暴露 ``plan_store`` 即报错，避免把不同
    execution 的计划串到同一份内存状态上。
    """

    deps = getattr(ctx, "deps", None)
    store = getattr(deps, "plan_store", None)
    if store is None:
        raise ValueError("agent run deps must expose plan_store to use Planning")
    return cast(PlanStore, store)


def build_planning_capability(
    *,
    tools: Sequence[str] | None = None,
    enable_subtasks: bool = True,
    store_resolver: Callable[[RunContext[Any]], PlanStore] | None = None,
) -> Planning[Any]:
    """装配 ``Planning``（计划在工具结果中以 ``<plan_reminder>`` 回显）。

    - ``store_resolver`` 缺省为 :func:`resolve_plan_store`（deps.plan_store）；
    - ``tools`` 为工具白名单（``None`` 注册全部）；
    - ``enable_subtasks`` 默认开启（子任务/依赖工具与 ``blocked`` 状态）。
    """

    return Planning(
        store_resolver=store_resolver or resolve_plan_store,
        tools=tools,
        enable_subtasks=enable_subtasks,
    )


# ---------------------------------------------------------------------------
# 摘要压缩装配（DD §10.3「SummarizingCompaction」）
# ---------------------------------------------------------------------------


def build_summarizing_compaction(
    *,
    model: Model | str | None = None,
    max_tokens: int,
    keep_messages: int,
) -> SummarizingCompaction[Any]:
    """装配摘要压缩，摘要出口与主轮同一绑定模型（summary 一账）。

    - ``model`` 只接受 ``None``：摘要必须继承被压缩那次运行自己的 ``ctx.model``
      （已绑定 GatewayModel），从而共用同一 executor/Recorder 账本、模型调用号
      与治理。传入模型实例会绑定到构造时那一次运行的身份——同一能力被主会话与
      子任务共用时，摘要会顶着主会话的账户与序号写子任务历史（跨角色混账）；
      传入模型 id 字符串则由 harness 自行构造未治理模型（脱离账本/权限/窗口
      治理）。两种形态都在此显式拒绝，不由上层装配兜底；
    - ``retries=0``：空摘要/模仿工具调用等失败不回退重试，直接显式失败（异常
      向被压缩的运行传播，依赖摘要的继续被阻断，不会带着旧历史静默续跑）；
    - ``receipts=True``：摘要写入后在历史里留确定性回执（被摘要消息数/估算
      token/摘要模型族/持久句柄），随历史一起（由运行装配持久化时）保留，供恢复
      后辨认「此前记忆是二手的」；摘要部件自身还带 harness 写入的 ``created_at``
      元数据；
    - ``tool_return_max_chars=None``：工具返回原文进入摘要，不静默截断；
    - ``preserve_first_user_message``/``incremental`` 显式冻结（早纠正保留、
      摘要增量化而非摘要的摘要）。

    残余（依赖上层装配，未在此伪造）：① 来源键级谱系——历史部件当前不携带
    ``source_id``/``version`` 身份，摘要无法登记逐源来源，也无法在摘要层剔除
    被禁用来源；需上层在工具返回/用户消息上补源身份后，经
    ``summarization_capabilities`` 注入过滤器；② 持久化与恢复闭环——摘要写入
    ``ctx.messages`` 后能否跨重启恢复，取决于运行装配是否持久化完整历史并原样
    回灌；本装配不另建摘要存储。
    """

    if model is not None:
        raise ValueError(
            "model must be None: the summary must inherit the run's bound GatewayModel "
            "(an explicit model instance pins another run's identity; a model id builds an ungoverned model)"
        )
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
        raise ValueError("max_tokens must be a positive int")
    if isinstance(keep_messages, bool) or not isinstance(keep_messages, int) or keep_messages < 0:
        raise ValueError("keep_messages must be an int >= 0")
    return SummarizingCompaction(
        model=None,
        max_tokens=max_tokens,
        keep_messages=keep_messages,
        retries=0,
        tool_return_max_chars=None,
        receipts=True,
        preserve_first_user_message=True,
        incremental=True,
    )


# ---------------------------------------------------------------------------
# ResearchBoundary（DD §10.3：核模式、来源撤用、窗口）
# ---------------------------------------------------------------------------

SourceKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class ResearchBoundaryPolicy:
    """一次模型请求前的边界策略（由运行装配按最新事实提供）。

    - ``allowed_tool_names``：当前模式允许暴露的工具名（讨论/计划模式收窄）；
    - ``active_source_keys``：本轮已登记来源 (source_id, version)；
    - ``disabled_source_keys``：当前已禁用的来源键；
    - ``context_window_tokens`` / ``candidate_context_windows``：本候选实际窗口
      与路由内其余候选的可用窗口；判定取所有可用窗口中最严者（I47）。本候选
      窗口未知（``None``）即无法核定，拒绝放行——不能用其它候选窗口替代；
    - ``estimated_input_tokens``：外部估计输入；``None`` 表示外部无法核定——
      由能力按最终请求全输入自测兜底，不用测试字节数代替；
    - ``output_reserve_tokens``：本轮必须为模型输出预留的 token（按真实授权模型
      的响应预算）；窗口门判定的是「输入 + 输出预留」，不把整窗都当作可用输入。
    - ``tokenizer_model``：冻结连接的实际模型引用（``provider/model``），用于按该
      模型的 tokenizer 计数；``None`` 表示无法证明模型，计数只能按保守估算并如实
      标记未核。不得传绑定别名（别名无法对应 tokenizer，等于用默认分词器冒充已核）。
    """

    mode: Mode
    allowed_tool_names: frozenset[str]
    active_source_keys: frozenset[SourceKey]
    disabled_source_keys: frozenset[SourceKey]
    context_window_tokens: int | None
    estimated_input_tokens: int | None
    candidate_context_windows: tuple[int, ...] = ()
    output_reserve_tokens: int = 0
    tokenizer_model: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, Mode):
            raise ValueError("research boundary mode must be a core Mode")
        for name in ("context_window_tokens", "estimated_input_tokens"):
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive int or None")
        tokenizer_model = self.tokenizer_model
        if tokenizer_model is not None and (not isinstance(tokenizer_model, str) or not tokenizer_model):
            raise ValueError("tokenizer_model must be a non-empty text model reference or None")
        reserve = self.output_reserve_tokens
        if isinstance(reserve, bool) or not isinstance(reserve, int) or reserve < 0:
            raise ValueError("output_reserve_tokens must be a non-negative int")
        for value in self.candidate_context_windows:
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("candidate_context_windows entries must be positive ints")
        for key_set_name in ("active_source_keys", "disabled_source_keys"):
            for key in getattr(self, key_set_name):
                if not isinstance(key, tuple) or len(key) != 2:
                    raise ValueError(f"{key_set_name} entries must be (source_id, version) tuples")
                if not all(isinstance(part, str) and part for part in key):
                    raise ValueError(f"{key_set_name} entries must carry non-empty text")

    def strictest_context_window_tokens(self) -> int | None:
        """所有候选可用窗口中最严者；本候选窗口未知 → ``None``（拒绝放行）。"""

        if self.context_window_tokens is None:
            return None
        return min((self.context_window_tokens, *self.candidate_context_windows))


def _contains_binary_content(messages: Sequence[Any]) -> bool:
    """待发消息是否含二进制内容（图片/文件）：文本估算无法计量，须显式不可估。"""

    from pydantic_ai.messages import BinaryContent

    for message in messages:
        for part in getattr(message, "parts", ()):
            content = getattr(part, "content", None)
            if isinstance(content, BinaryContent):
                return True
            if isinstance(content, Sequence) and not isinstance(content, (str, bytes, bytearray)):
                if any(isinstance(item, BinaryContent) for item in content):
                    return True
    return False


def _estimate_request_input_tokens(
    request_context: ModelRequestContext, *, tokenizer_model: str | None = None
) -> tuple[int | None, bool]:
    """按最终待发请求估算输入 token 下限（I47 窗口门，与费用预估同源）。

    覆盖 ``messages`` 全部分型（SDK 确定性序列化）+ ``function_tools`` /
    ``output_tools`` 的 name/description/schema + ``instruction_parts`` +
    ``output_object`` schema。含二进制内容或序列化失败 → ``(None, False)``（不可估，
    拒绝放行，不以正文估算冒充完整输入，R21）。

    ``tokenizer_model`` 为冻结连接的实际模型引用：只有它才对应真实 tokenizer；
    缺失时不得回退到 ``request_context.model.model_name``（叶模型桥的固定别名
    ``qtrading:bound`` 不映射任何 tokenizer，等于用默认分词器冒充已核）。返回值
    第二项如实标注本次计数是否已按该模型 tokenizer 核定。
    """

    from pydantic_ai.messages import ModelMessagesTypeAdapter

    from services.ai_service.token_budget import count_tokens_with_basis

    messages = request_context.messages
    if _contains_binary_content(messages):
        return None, False
    parameters = request_context.model_request_parameters
    try:
        messages_text = ModelMessagesTypeAdapter.dump_json(messages).decode("utf-8")
        tools_text = json.dumps(
            [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters_json_schema,
                }
                for tool in (*parameters.function_tools, *parameters.output_tools)
            ],
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        output_schema_text = (
            json.dumps(parameters.output_object.json_schema, ensure_ascii=False, sort_keys=True, default=str)
            if parameters.output_object is not None
            else ""
        )
    except (TypeError, ValueError):
        return None, False
    instructions_text = "\n".join(part.content for part in (parameters.instruction_parts or []))
    texts = (messages_text, tools_text, output_schema_text, instructions_text)
    total = 0
    verified = True
    for text in texts:
        count, part_verified = count_tokens_with_basis(text, tokenizer_model)
        total += count
        verified = verified and part_verified
    return total, verified


def evaluate_research_boundary(
    policy: ResearchBoundaryPolicy, *, measured_input_tokens: int | None = None
) -> str | None:
    """返回阻断原因；``None`` 表示未触发阻断（不代表授权已通过）。

    ``measured_input_tokens`` 为按最终请求全输入自测的下限；与外部估计取较大者，
    外部低估不能放宽闸门（I47）。窗口取所有候选可用窗口中最严者；比较的是
    「输入下限 + 输出预留」与窗口，预留为 0 只表示调用方未声明输出预算，不代表
    整窗可用作输入。
    """

    if policy.active_source_keys & policy.disabled_source_keys:
        return "source_disabled"
    estimates = [
        estimate for estimate in (policy.estimated_input_tokens, measured_input_tokens) if estimate is not None
    ]
    window = policy.strictest_context_window_tokens()
    if window is None or not estimates:
        return "window_unknown"
    if max(estimates) + policy.output_reserve_tokens > window:
        return "context_window_exceeded"
    return None


@dataclass(frozen=True, slots=True)
class ResearchBoundaryAdmission:
    """阶段①「安全准入」的结论（不含窗口超限判定）。

    ``request_context`` 为按模式裁剪工具后的待发请求；``policy`` 为本次请求新鲜
    取得的事实，供阶段③「压缩后窗口复核」复用同一份策略（不重复回读 DAO）。
    ``measured_input_tokens`` 为该裁剪后请求的全输入下限。
    """

    request_context: ModelRequestContext
    policy: ResearchBoundaryPolicy
    measured_input_tokens: int
    measured_verified: bool

    def exceeds_context_window(self) -> bool:
        """准入请求是否已超出窗口（压缩可挽救，因此阶段①不据此阻断）。"""

        return (
            evaluate_research_boundary(self.policy, measured_input_tokens=self.measured_input_tokens)
            == "context_window_exceeded"
        )


def _boundary_denied(message: str) -> AgentServiceError:
    return AgentServiceError(AgentErrorCode.POLICY_DENIED, message, "research_boundary")


def _context_window_exceeded_error(
    policy: ResearchBoundaryPolicy, *, measured: int, measured_verified: bool
) -> AgentServiceError:
    estimates = [estimate for estimate in (policy.estimated_input_tokens, measured) if estimate is not None]
    return _boundary_denied(
        f"context window exceeded: estimated={max(estimates)} "
        f"+output_reserve={policy.output_reserve_tokens} "
        f"window={policy.strictest_context_window_tokens()} "
        f"measured_basis={'verified' if measured_verified else 'estimated'}"
    )


@dataclass(kw_only=True)
class ResearchBoundary(AbstractCapability[Any]):
    """模型请求前的边界能力：模式裁剪工具、禁用来源阻断、窗口阻断。

    只做裁剪/阻断，不授权：真实授权仍在派发点按最新事实二次核定；策略由
    ``policy_provider`` 每次请求新鲜取得（异步，可回读 DAO）。不修改
    ``messages``/持久历史，不捕获 ``CancelledError``（R2）。

    为支持「准入 → 压缩 → 窗口复核」的三段式编排（D7-02），窗口超限判定从准入中
    拆出：:meth:`preflight` 只核来源/窗口事实可核定与否，:meth:`enforce_context_window`
    在压缩后复核完整请求。:meth:`before_model_request` 保持原语义（准入 + 窗口门）。
    """

    policy_provider: Callable[[RunContext[Any]], Awaitable[ResearchBoundaryPolicy]]
    id: str | None = "research_boundary"

    async def preflight(self, ctx: RunContext[Any], request_context: ModelRequestContext) -> ResearchBoundaryAdmission:
        """阶段①：模式裁剪工具 + 来源未禁用 + 窗口事实可核定；**不**判超窗。"""

        policy = await self.policy_provider(ctx)
        # 先按模式裁剪工具，再对「最终待发请求」自测全输入（I47）：先测会被裁剪掉
        # 的工具 schema 会高估，导致本可放行的请求被误拒。
        candidate_context = request_context
        if policy.mode is not Mode.EXECUTE:
            parameters = request_context.model_request_parameters
            allowed = [tool for tool in parameters.function_tools if tool.name in policy.allowed_tool_names]
            if len(allowed) != len(parameters.function_tools):
                candidate_context = replace(
                    request_context,
                    model_request_parameters=replace(parameters, function_tools=allowed),
                )
        measured, measured_verified = _estimate_request_input_tokens(
            candidate_context, tokenizer_model=policy.tokenizer_model
        )
        if measured is None:
            raise _boundary_denied(
                "request input size unestimable (binary or unencodable input); refusing to guess before dispatch"
            )
        if not measured_verified:
            # R21：模型 tokenizer 不可证明时只做保守估算，不把它当作已核事实。
            logger.warning(
                "[ResearchBoundary] input token count is an estimate (no verified tokenizer: model=%r)",
                policy.tokenizer_model,
            )
        reason = evaluate_research_boundary(policy, measured_input_tokens=measured)
        if reason == "source_disabled":
            raise _boundary_denied("source disabled for new dispatch")
        if reason == "window_unknown":
            raise _boundary_denied("context window unknown; refusing to guess before dispatch")
        return ResearchBoundaryAdmission(
            request_context=candidate_context,
            policy=policy,
            measured_input_tokens=measured,
            measured_verified=measured_verified,
        )

    def enforce_context_window(
        self, admission: ResearchBoundaryAdmission, request_context: ModelRequestContext
    ) -> None:
        """阶段③：对（压缩后的）完整请求复核窗口；超出/不可估即拒绝放行。

        只按该请求的全输入实测量判定：``policy.estimated_input_tokens`` 描述的是
        压缩前的原始请求，压缩后沿用会让压缩永远无法挽救超窗（D7-02）。
        """

        policy = admission.policy
        measured, measured_verified = _estimate_request_input_tokens(
            request_context, tokenizer_model=policy.tokenizer_model
        )
        if measured is None:
            raise _boundary_denied(
                "request input size unestimable (binary or unencodable input); refusing to guess before dispatch"
            )
        if not measured_verified:
            logger.warning(
                "[ResearchBoundary] input token count is an estimate (no verified tokenizer: model=%r)",
                policy.tokenizer_model,
            )
        # 压缩后只信实测量：``estimated_input_tokens`` 描述压缩前的原始请求，
        # 沿用会让压缩永远无法挽救超窗（D7-02）。
        reason = evaluate_research_boundary(
            replace(policy, estimated_input_tokens=None), measured_input_tokens=measured
        )
        if reason == "context_window_exceeded":
            raise _context_window_exceeded_error(policy, measured=measured, measured_verified=measured_verified)
        if reason == "window_unknown":
            raise _boundary_denied("context window unknown; refusing to guess before dispatch")
        if reason == "source_disabled":
            raise _boundary_denied("source disabled for new dispatch")

    async def before_model_request(
        self, ctx: RunContext[Any], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        admission = await self.preflight(ctx, request_context)
        if admission.exceeds_context_window():
            raise _context_window_exceeded_error(
                admission.policy,
                measured=admission.measured_input_tokens,
                measured_verified=admission.measured_verified,
            )
        return admission.request_context
