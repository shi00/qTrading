"""原生一层叶 DAG workflow 与就绪叶调度（DD §8 F04/SQ-04；HANDOFF §7.2/§8.3，D-02）。

职责边界：

- **本模块只做编排**：父研究 workflow 与一层叶 workflow 的稳定命名、封闭载荷、
  就绪叶事件驱动推进与叶事实汇总；实际取数/模型/工具执行由组装期登记的叶执行器
  （:class:`LeafRuntimeRegistry`）承担（D-03/E 系列接线）。
- **DBOS 是唯一持久执行推进器**（INV-02）：应用表只保存业务事实与可核对投影，
  不以应用镜像状态驱动第二调度。
- **一层由结构保证**：计划为扁平节点表（:class:`PlanSpecProposal`），叶不再委派、
  不暴露间接 Agent 工具（工具白名单在 E 系列装配处约束）。

调度语义（DD F04/F05、SQ-04）：

- 父 workflow 只启动就绪叶：其全部前置为 ``complete``（"适用成功前置"）；
- 消费完成事实后**立即**启动新就绪叶（A/B→C、B→D 时 B 结束即启 D，不按固定
  批次 gather）；
- 失败/终止/``partial`` 只阻相关后继（不借成功兄弟补答案），独立分支继续；
- ``paused``/``waiting_*``/``unknown`` 等不自动结束父：不伪造终态、不启动后继、
  父不收敛（未知不等于失败或成功）；``closed_unknown`` 是**不可恢复终态**（F10）：
  执行已结束但结果未知，按结案收口（全部叶结案后可收敛、迟到事实不复活），
  单独列入汇总不并入失败，并阻其下游（未知结果不能作为适用成功前置）；
- 汇总只报叶事实（``leaves_all_succeeded``），**不以成功叶数量代替目标完整完成**；
  目标覆盖与收敛结论由服务层按 F04 保存。

恢复/重放：``application_version`` 必须为真实工作流代码版本（HANDOFF §8.3），
workflow/step 名称一经发布即稳定标识，重命名等同新版本；载荷带 ``payload_version``
封闭校验，跨版本恢复前须核对兼容矩阵（§8.4）。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Set as AbstractSet
from dataclasses import dataclass, field, replace
from typing import Any, Final, NoReturn, Protocol

from dbos import DBOS, SetWorkflowID, WorkflowSerializationFormat, WorkflowStatus, WorkflowStatusString

from core.agent_types import (
    DISPLAY_STATES,
    PLAN_SPEC_VERSION,
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    ConsumptionFact,
    ConsumptionState,
    PlanNodeSpec,
    PlanSpecProposal,
    decode_plan_node_spec,
    decode_plan_spec_proposal,
    is_digest,
    node_input_digest,
    plan_node_digest,
    plan_spec_digest,
    require_identity,
    stable_id,
    validate_plan_spec_proposal,
)
from utils.singleton_registry import register_singleton

# ---------------------------------------------------------------------------
# 冻结常量：workflow/step 名称与载荷版本（发布后不得改写，重命名等同新版本）
# ---------------------------------------------------------------------------

#: 父研究 workflow 稳定名（DBOS 重放身份，HANDOFF §8.3）。
RESEARCH_WORKFLOW_NAME: Final[str] = "agent_research"
#: 一层叶 workflow 稳定名。
LEAF_WORKFLOW_NAME: Final[str] = "agent_leaf"
#: 叶执行 step 稳定名（唯一实际 IO 段，DBOS 按 step 持久化）。
LEAF_EXECUTE_STEP_NAME: Final[str] = "agent_leaf_execute"
#: 叶观察 step 稳定名（父 workflow 的完成集合判定检查点，B1 重放面）。
LEAF_OBSERVE_STEP_NAME: Final[str] = "agent_leaf_observe"
#: 计划准入 step 稳定名（父 workflow 的首个检查点：校验并记录计划版 + 实际输入清单）。
PLAN_ADMIT_STEP_NAME: Final[str] = "agent_plan_admit"
#: 执行终态投影 step 稳定名（父 workflow 收敛后把已核叶事实写成应用执行终态）。
RESEARCH_TERMINAL_STEP_NAME: Final[str] = "agent_research_terminal"
#: 实际输入绑定 step 稳定名（父 workflow 在派发任何叶之前建立应用轮次并冻结输入版）。
ACTUAL_INPUT_BIND_STEP_NAME: Final[str] = "agent_actual_input_bind"
#: 叶资源票据认领/释放 step 稳定名（树级额度闭环：认领容量后才启动叶）。
LEAF_TICKET_CLAIM_STEP_NAME: Final[str] = "agent_leaf_ticket_claim"
LEAF_TICKET_RELEASE_STEP_NAME: Final[str] = "agent_leaf_ticket_release"
#: 答复消费 step 稳定名（原生侧确认已投递答复生效的唯一写入口）。
CONSUME_ANSWER_STEP_NAME: Final[str] = "agent_consume_answer"
#: 答复消息主题（与生产发送器 ``_NativeAnswerSender`` 同源；答复只经此主题消费）。
ANSWER_MESSAGE_TOPIC: Final[str] = "answer"
#: 答复消息结构版本（进入封闭校验；跨版本恢复前须核对兼容矩阵 §8.4）。
ANSWER_MESSAGE_VERSION: Final[int] = 1
#: 单次答复接收等待上限（0 = 非阻塞轮询：无答复立即返回，不拖慢叶推进）。
ANSWER_RECV_TIMEOUT_SEC: Final[float] = 0.0

#: 载荷结构版本（进入封闭校验；跨版本恢复前须核对兼容矩阵 §8.4）。
PAYLOAD_VERSION: Final[int] = 1

#: 观察 step 内部轮询间隔（仅影响单步耗时，不影响推进正确性）。
OBSERVE_POLL_INTERVAL_SEC: Final[float] = 0.2

#: 单次观察 step 的内部轮询上限（毫秒级检查点密度；重放按记录还原，不重跑）。
OBSERVE_SLICE_SEC: Final[float] = 2.0

#: 连续无进展观察预算（HANDOFF §8.3：观察超时只停观察、不取消执行）。
#: 取值覆盖单叶长任务（多轮模型调用 + 工具）并留余量；预算耗尽**不结束父**：
#: 生产父 workflow 经 :func:`wait_next_observation_window` 转入 DBOS 持久等待后
#: 开启新观察窗口（见 :func:`drive_leaf_dag`），超预算完成的长叶仍推进其后继，
#: 不伪造叶终态（§8.2 未知不等于失败）。以「连续无进展观察次数」计量：该计数
#: 只来自被记录的 step 结果，重放可确定复现，不引入墙钟依赖（不把超时升级为
#: workflow ERROR，避免恢复在同点反复失败）。
CHILD_OBSERVE_TIMEOUT_SEC: Final[float] = 900.0

#: 观察预算耗尽后的持久等待间隔（DBOS 原生可恢复等待）：父 workflow 以
#: ``DBOS.sleep_async`` 让出长叶期间的推进（恢复时已完成的 sleep 按记录跳过），
#: 等待结束后重新观察，不把「叶子仍在运行」误记为父批次结束。
IDLE_WAIT_INTERVAL_SEC: Final[float] = 60.0


_PAYLOAD_VERSION_KEY: Final = "payload_version"
_RESEARCH_PAYLOAD_KEYS: Final = frozenset({"payload_version", "execution_id", "round_id", "plan_digest", "nodes"})
#: 父载荷兼容扩展键（缺省语义见各解码器）：重试轮保留前置、角色能力策略、
#: 输入版本指纹。缺省不放开任意键。
_RESEARCH_PAYLOAD_OPTIONAL_KEYS: Final = frozenset(
    {
        "preserved_outcomes",
        "role_capabilities",
        "input_version_digest",
        "preserved_input_manifest",
        "binding_identity",
    }
)
_LEAF_PAYLOAD_KEYS: Final = frozenset({"payload_version", "execution_id", "round_id", "plan_digest", "node"})
#: 叶载荷兼容扩展键：采用的输入指纹、叶持久身份、应用轮次输入版、已验证前置成果引用。
_LEAF_PAYLOAD_OPTIONAL_KEYS: Final = frozenset({"input_digest", "leaf_id", "input_revision", "predecessor_artifacts"})
_NODE_OUTCOME_KEYS: Final = frozenset({"node_id", "state", "error_code", "artifact_ref"})
#: 叶结果兼容扩展键：该叶实际采用的输入指纹（缺省 = 未报告，不伪造）、实际退出是否已核。
_NODE_OUTCOME_OPTIONAL_KEYS: Final = frozenset({"input_digest", "exit_verified"})
#: 答复消息封闭键（与生产发送器同一 wire；缺键/额外键均拒绝）。
_ANSWER_MESSAGE_KEYS: Final = frozenset(
    {"schema_version", "decision_id", "execution_id", "round_id", "client_command_id", "answer"}
)

#: 叶状态语义分类（值域 = 冻结的 display_state 15 值，不自造词表）。
_SUCCESS_STATES: Final[frozenset[str]] = frozenset({"complete"})
_BLOCKING_STATES: Final[frozenset[str]] = frozenset({"failed", "withdrawn", "ended", "partial"})
#: 已结案但结果未知（不可恢复终态，F10 / ``NON_RECOVERABLE_DISPLAY_STATES``）。
#: 执行已结束、成果不可知：必须收口为终态（不保持可恢复占位、迟到事实不复活），
#: 但不得当作适用成功前置，故与阻塞态一样阻其下游；汇总单独列出，不与失败混同。
_CLOSED_UNKNOWN_STATES: Final[frozenset[str]] = frozenset({"closed_unknown"})
_TERMINAL_STATES: Final[frozenset[str]] = _SUCCESS_STATES | _BLOCKING_STATES | _CLOSED_UNKNOWN_STATES
_NON_TERMINAL_STATES: Final[frozenset[str]] = DISPLAY_STATES - _TERMINAL_STATES


# ---------------------------------------------------------------------------
# 封闭载荷与叶结果 wire
# ---------------------------------------------------------------------------


def _fail(code: str, field_path: str) -> NoReturn:
    raise AgentValidationError(code, field_path)


def _require_digest(value: object, path: str) -> str:
    if not isinstance(value, str) or not is_digest(value):
        _fail("invalid_digest", path)
    return value


def _require_str(value: object, path: str) -> str:
    return require_identity(value, path)


def _node_to_wire(node: PlanNodeSpec) -> dict[str, Any]:
    """节点 wire（与 core 规范化形态一致；PORTABLE JSON 可序列化）。"""

    return {
        "node_id": node.node_id,
        "target": node.target,
        "role_key": node.role_key,
        "depends_on": list(node.depends_on),
        "capabilities": list(node.capabilities),
        "source_refs": [
            {
                "source_kind": ref.source_kind.value,
                "source_id": ref.source_id,
                "source_version": ref.source_version,
                "source_digest": ref.source_digest,
            }
            for ref in node.source_refs
        ],
    }


def _require_exact_keys(value: Mapping[str, object], required: frozenset[str], path: str) -> None:
    """全键必填、禁额外键（封闭 wire；缺失/额外分别给出稳定错误码）。"""

    missing = sorted(required - value.keys())
    if missing:
        _fail("missing_field", f"{path}.{missing[0]}")
    extra = sorted(value.keys() - required)
    if extra:
        _fail("unknown_field", f"{path}.{extra[0]}")


def _require_keys_with_optional(
    value: Mapping[str, object], required: frozenset[str], optional: frozenset[str], path: str
) -> None:
    """必填键全在、额外键仅限已声明可选键（兼容扩展不放开任意键）。"""

    missing = sorted(required - value.keys())
    if missing:
        _fail("missing_field", f"{path}.{missing[0]}")
    extra = sorted(value.keys() - required - optional)
    if extra:
        _fail("unknown_field", f"{path}.{extra[0]}")


def _require_payload_version(value: Mapping[str, object], path: str) -> int:
    version = value[_PAYLOAD_VERSION_KEY]
    if isinstance(version, bool) or not isinstance(version, int) or version != PAYLOAD_VERSION:
        _fail("unsupported_version", f"{path}.{_PAYLOAD_VERSION_KEY}")
    return version


def _require_positive_int(value: object, path: str) -> int:
    """正整数身份（版本/代次下限 1；布尔不算整数，不冒充已绑定）。"""

    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _fail("invalid_range", path)
    return value


@dataclass(frozen=True, slots=True)
class NodeOutcome:
    """叶终态事实（一条叶执行的可核对结果；状态取冻结 display_state 值）。

    ``error_code``/``artifact_ref``/``input_digest`` 缺失以 ``None`` 表示（不填充
    业务合法值，R21）。``input_digest`` 为该叶**实际采用**的输入指纹（R2-I09）：
    有值时才可作为「仍适用」证据被重试轮复用；未报告不得推断为同一输入。
    ``exit_verified`` 表示叶执行的实际退出是否已被核对（有落盘执行记录或成功
    返回）；未核退出不得被当作已结束（票据保持占用，见 :class:`LeafTicketLease`）。
    """

    node_id: str
    state: str
    error_code: str | None = None
    artifact_ref: str | None = None
    input_digest: str | None = None
    exit_verified: bool = True

    def to_wire(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "state": self.state,
            "error_code": self.error_code,
            "artifact_ref": self.artifact_ref,
            "input_digest": self.input_digest,
            "exit_verified": self.exit_verified,
        }


def _opt_str_or_none(value: object, path: str) -> str | None:
    if value is None:
        return None
    return _require_str(value, path)


def _opt_digest_or_none(value: object, path: str) -> str | None:
    if value is None:
        return None
    return _require_digest(value, path)


def _require_bool_default_true(value: object, path: str) -> bool:
    if value is None:
        return True
    if not isinstance(value, bool):
        _fail("invalid_type", path)
    return value


def decode_node_outcome(value: object, path: str = "$.node_outcome") -> NodeOutcome:
    """严格解码叶结果（四键必填 + ``input_digest`` 兼容可选；状态须为冻结 display_state）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys_with_optional(value, _NODE_OUTCOME_KEYS, _NODE_OUTCOME_OPTIONAL_KEYS, path)
    state = value["state"]
    if not isinstance(state, str) or state not in DISPLAY_STATES:
        _fail("invalid_display_state", f"{path}.state")
    return NodeOutcome(
        node_id=_require_str(value["node_id"], f"{path}.node_id"),
        state=state,
        error_code=_opt_str_or_none(value["error_code"], f"{path}.error_code"),
        artifact_ref=_opt_str_or_none(value["artifact_ref"], f"{path}.artifact_ref"),
        input_digest=_opt_digest_or_none(value.get("input_digest"), f"{path}.input_digest"),
        exit_verified=_require_bool_default_true(value.get("exit_verified"), f"{path}.exit_verified"),
    )


@dataclass(frozen=True, slots=True)
class AnswerMessage:
    """一条原生答复消息的可核验身份（答复正文不进入编排层）。"""

    decision_id: str
    execution_id: str
    round_id: str
    client_command_id: str


def decode_answer_message(value: object, path: str = "$.answer_message") -> AnswerMessage:
    """严格解码答复消息 wire（全键必填、禁额外键；结构版本封闭校验）。

    会话归属不在消息中单列：答复命令保存时已由原决定绑定 ``session_id``，消费
    时由 DAO 在决定行锁内核对（``AgentDao.consume_answer``），故编排层只核本
    workflow 的执行/轮次身份，会话/决定身份由权威行裁决。
    """

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_exact_keys(value, _ANSWER_MESSAGE_KEYS, path)
    version = value["schema_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != ANSWER_MESSAGE_VERSION:
        _fail("unsupported_version", f"{path}.schema_version")
    if not isinstance(value["answer"], dict):
        _fail("invalid_type", f"{path}.answer")
    return AnswerMessage(
        decision_id=_require_str(value["decision_id"], f"{path}.decision_id"),
        execution_id=_require_str(value["execution_id"], f"{path}.execution_id"),
        round_id=_require_str(value["round_id"], f"{path}.round_id"),
        client_command_id=_require_str(value["client_command_id"], f"{path}.client_command_id"),
    )


def build_leaf_payload(
    *,
    execution_id: str,
    round_id: str,
    plan_digest: str,
    node: PlanNodeSpec,
    input_digest: str | None = None,
    leaf_id: str | None = None,
    input_revision: int | None = None,
    predecessor_artifacts: tuple[tuple[str, str], ...] = (),
) -> dict[str, Any]:
    """构造叶 workflow 载荷（PORTABLE JSON 可序列化；封闭键 + 可选身份扩展）。

    ``input_digest`` 为父在准入清单中为该节点确定的前置输入指纹：叶据此执行
    并在终态事实中回报，缺失时不写入键（不以占位值冒充已记录）。
    ``leaf_id``/``input_revision`` 为父绑定应用轮次后冻结的叶身份与输入版；
    ``predecessor_artifacts`` 为父核定的本轮前置成果引用（``node_id → ref``），
    叶读取并核对后才可外发。
    """

    payload: dict[str, Any] = {
        _PAYLOAD_VERSION_KEY: PAYLOAD_VERSION,
        "execution_id": _require_str(execution_id, "$.execution_id"),
        "round_id": _require_str(round_id, "$.round_id"),
        "plan_digest": _require_digest(plan_digest, "$.plan_digest"),
        "node": _node_to_wire(node),
    }
    if input_digest is not None:
        payload["input_digest"] = _require_digest(input_digest, "$.input_digest")
    if leaf_id is not None:
        payload["leaf_id"] = _require_str(leaf_id, "$.leaf_id")
    if input_revision is not None:
        payload["input_revision"] = _require_positive_int(input_revision, "$.input_revision")
    if predecessor_artifacts:
        payload["predecessor_artifacts"] = [_artifact_ref_wire(ref) for ref in predecessor_artifacts]
    return payload


def _artifact_ref_wire(ref: tuple[str, str]) -> list[str]:
    node_id, artifact_ref = ref
    return [
        _require_str(node_id, "$.predecessor_artifacts.node_id"),
        _require_str(artifact_ref, "$.predecessor_artifacts.artifact_ref"),
    ]


@dataclass(frozen=True, slots=True)
class LeafExecutionContext:
    """叶执行上下文（身份三元组 + 节点 + 采用的输入指纹；不靠隐式全局）。

    轮次进入上下文是重试轮隔离的前提：新轮新身份，不复用旧轮成果。
    ``input_digest`` 为父准入清单确定的输入版本指纹（缺省 = 未记录，不推断）；
    ``leaf_id`` 为父绑定的叶持久身份（缺省 = 旧载荷，叶按三元组派生核对）；
    ``input_revision`` 为父冻结的应用轮次输入版（缺省 = 未绑定，叶零外发）；
    ``predecessor_artifacts`` 为父核定的本轮前置成果引用（``node_id → ref``）。
    """

    execution_id: str
    round_id: str
    plan_digest: str
    node: PlanNodeSpec
    input_digest: str | None = None
    leaf_id: str | None = None
    input_revision: int | None = None
    predecessor_artifacts: tuple[tuple[str, str], ...] = ()


def _decode_predecessor_artifacts(value: object, path: str) -> tuple[tuple[str, str], ...]:
    """严格解码前置成果引用表（节点唯一；引用为非空文本；缺省为空）。"""

    if value is None:
        return ()
    if not isinstance(value, list):
        _fail("invalid_type", path)
    refs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, list) or len(item) != 2:
            _fail("invalid_type", item_path)
        node_id = _require_str(item[0], f"{item_path}.node_id")
        if node_id in seen:
            _fail("duplicate_node", f"{item_path}.node_id")
        seen.add(node_id)
        refs.append((node_id, _require_str(item[1], f"{item_path}.artifact_ref")))
    return tuple(refs)


def decode_leaf_payload(value: object, path: str = "$.leaf_payload") -> LeafExecutionContext:
    """严格解码叶载荷为 :class:`LeafExecutionContext`。

    ``leaf_id`` 出现时必须等于按三元组派生的稳定叶身份（父绑定身份不得与
    载荷身份脱钩）；``input_revision`` 出现时必须为正整数。
    """

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys_with_optional(value, _LEAF_PAYLOAD_KEYS, _LEAF_PAYLOAD_OPTIONAL_KEYS, path)
    _require_payload_version(value, path)
    execution_id = _require_str(value["execution_id"], f"{path}.execution_id")
    round_id = _require_str(value["round_id"], f"{path}.round_id")
    node = decode_plan_node_spec(value["node"], f"{path}.node")
    leaf_id = _opt_str_or_none(value.get("leaf_id"), f"{path}.leaf_id")
    if leaf_id is not None and leaf_id != leaf_workflow_id(
        execution_id=execution_id, round_id=round_id, node_id=node.node_id
    ):
        _fail("leaf_id_mismatch", f"{path}.leaf_id")
    raw_revision = value.get("input_revision")
    return LeafExecutionContext(
        execution_id=execution_id,
        round_id=round_id,
        plan_digest=_require_digest(value["plan_digest"], f"{path}.plan_digest"),
        node=node,
        input_digest=_opt_digest_or_none(value.get("input_digest"), f"{path}.input_digest"),
        leaf_id=leaf_id,
        input_revision=(
            None if raw_revision is None else _require_positive_int(raw_revision, f"{path}.input_revision")
        ),
        predecessor_artifacts=_decode_predecessor_artifacts(
            value.get("predecessor_artifacts"), f"{path}.predecessor_artifacts"
        ),
    )


def leaf_workflow_id(*, execution_id: str, round_id: str, node_id: str) -> str:
    """叶 workflow 稳定身份（按计划身份而非调用顺序；B1 修复之身份面）。

    重放顺序稳定性由父 workflow 的确定性操作序列保证（启动/观察均由父协程按
    计划顺序发出、完成集合经 ``agent_leaf_observe`` 记录还原，见
    :func:`drive_leaf_dag`）；本函数提供与语义身份绑定的显式 ID：恢复/观察按
    ID 复接同一子执行，重试轮因 ``round_id`` 不同不复用旧成果。各段按
    ``%``／``:`` 转义后再拼接，保证拼接映射单射（不同三元组不产生同一 ID）。
    """

    def _escape(part: object) -> str:
        text = _require_str(part, "$.leaf_workflow_id")
        return text.replace("%", "%25").replace(":", "%3A")

    return "agent_leaf:" + ":".join((_escape(execution_id), _escape(round_id), _escape(node_id)))


_BINDING_IDENTITY_KEYS: Final = frozenset(
    {"user_scope", "session_id", "workflow_code_version", "run_epoch", "expected_input_revision"}
)


@dataclass(frozen=True, slots=True)
class ParentBindIdentity:
    """派发侧冻结的应用轮次身份（父 workflow 建立真实轮次的唯一来源）。

    父载荷本身只带 execution/round/计划，**不得**自造用户域或输入版；本结构由
    派发侧在受理后随载荷冻结，父据此调用 :func:`record_actual_input_binding`
    建立 ``agent_round``（叶归属读取才可能成功）。
    """

    user_scope: str
    session_id: str
    workflow_code_version: str
    run_epoch: int
    expected_input_revision: int

    def to_wire(self) -> dict[str, Any]:
        return {
            "user_scope": self.user_scope,
            "session_id": self.session_id,
            "workflow_code_version": self.workflow_code_version,
            "run_epoch": self.run_epoch,
            "expected_input_revision": self.expected_input_revision,
        }


def decode_binding_identity(value: object, path: str = "$.binding_identity") -> ParentBindIdentity:
    """严格解码派发侧冻结身份（全键必填、禁额外键、版本/代次下限 1）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_exact_keys(value, _BINDING_IDENTITY_KEYS, path)
    return ParentBindIdentity(
        user_scope=_require_str(value["user_scope"], f"{path}.user_scope"),
        session_id=_require_str(value["session_id"], f"{path}.session_id"),
        workflow_code_version=_require_str(value["workflow_code_version"], f"{path}.workflow_code_version"),
        run_epoch=_require_positive_int(value["run_epoch"], f"{path}.run_epoch"),
        expected_input_revision=_require_positive_int(
            value["expected_input_revision"], f"{path}.expected_input_revision"
        ),
    )


@dataclass(frozen=True, slots=True)
class ResearchPayload:
    """父载荷完整解码结果（严格封闭；可选兼容键缺省以 ``None``/空元组表示）。

    ``role_capabilities`` 为本次准入使用的角色 → 允许能力键策略（派发侧随载荷
    冻结，避免工作流侧无策略来源时默认放行）；``input_version_digest`` 为本次
    执行所依据的输入版本指纹（缺省 = 未核定，不作「仍适用」证据，R2-I09）。
    ``binding_identity`` 为派发侧冻结的应用轮次身份（缺省 = 未接线，父零派发）。
    """

    execution_id: str
    round_id: str
    plan_digest: str
    plan: PlanSpecProposal
    preserved: tuple[NodeOutcome, ...]
    role_capabilities: Mapping[str, frozenset[str]] | None
    input_version_digest: str | None
    preserved_input_manifest: Mapping[str, str] | None
    binding_identity: ParentBindIdentity | None


def _decode_digest_map(value: object, path: str) -> dict[str, str]:
    """严格解码 ``node_id → 摘要`` 映射（键非空且唯一，值须为摘要）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    mapping: dict[str, str] = {}
    for node_id, raw_digest in value.items():
        key = _require_str(node_id, f"{path}.node_id")
        if key in mapping:
            _fail("duplicate_node", f"{path}.{key}")
        mapping[key] = _require_digest(raw_digest, f"{path}.{key}")
    return mapping


def _decode_role_capabilities(value: object, path: str) -> dict[str, frozenset[str]]:
    """严格解码角色能力策略（角色非空且唯一、能力键为非空字符串）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    policy: dict[str, frozenset[str]] = {}
    for role_key, raw_capabilities in value.items():
        role = _require_str(role_key, f"{path}.role_key")
        if role in policy:
            _fail("duplicate_role", f"{path}.{role}")
        if not isinstance(raw_capabilities, list):
            _fail("invalid_type", f"{path}.{role}")
        capabilities = tuple(
            _require_str(item, f"{path}.{role}[{index}]") for index, item in enumerate(raw_capabilities)
        )
        if len(set(capabilities)) != len(capabilities):
            _fail("duplicate_capability", f"{path}.{role}")
        policy[role] = frozenset(capabilities)
    return policy


def decode_research_payload_full(value: object, path: str = "$.research_payload") -> ResearchPayload:
    """严格解码父载荷（含准入所需的角色策略与输入版本指纹）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys_with_optional(value, _RESEARCH_PAYLOAD_KEYS, _RESEARCH_PAYLOAD_OPTIONAL_KEYS, path)
    _require_payload_version(value, path)
    raw_nodes = value["nodes"]
    if not isinstance(raw_nodes, list):
        _fail("invalid_type", f"{path}.nodes")
    raw_preserved = value.get("preserved_outcomes", [])
    if not isinstance(raw_preserved, list):
        _fail("invalid_type", f"{path}.preserved_outcomes")
    preserved = tuple(
        decode_node_outcome(item, f"{path}.preserved_outcomes[{index}]") for index, item in enumerate(raw_preserved)
    )
    raw_policy = value.get("role_capabilities")
    raw_input_manifest = value.get("preserved_input_manifest")
    return ResearchPayload(
        execution_id=_require_str(value["execution_id"], f"{path}.execution_id"),
        round_id=_require_str(value["round_id"], f"{path}.round_id"),
        plan_digest=_require_digest(value["plan_digest"], f"{path}.plan_digest"),
        plan=decode_plan_spec_proposal({"nodes": raw_nodes}, f"{path}.plan_spec"),
        preserved=preserved,
        role_capabilities=(
            None if raw_policy is None else _decode_role_capabilities(raw_policy, f"{path}.role_capabilities")
        ),
        input_version_digest=_opt_digest_or_none(value.get("input_version_digest"), f"{path}.input_version_digest"),
        preserved_input_manifest=(
            None
            if raw_input_manifest is None
            else _decode_digest_map(raw_input_manifest, f"{path}.preserved_input_manifest")
        ),
        binding_identity=(
            None
            if value.get("binding_identity") is None
            else decode_binding_identity(value["binding_identity"], f"{path}.binding_identity")
        ),
    )


def decode_research_payload(
    value: object, path: str = "$.research_payload"
) -> tuple[str, str, str, PlanSpecProposal, tuple[NodeOutcome, ...]]:
    """严格解码父载荷：``(execution_id, round_id, plan_digest, plan, preserved)``。

    ``preserved_outcomes`` 为重试轮冻结的「仍适用成功前置」旧轮事实；首轮缺省
    为空（版本 1 内的兼容扩展键，不放开任意键）。准入所需的角色策略与输入版本
    指纹经 :func:`decode_research_payload_full` 读取（本函数为兼容视图）。
    """

    payload = decode_research_payload_full(value, path)
    return payload.execution_id, payload.round_id, payload.plan_digest, payload.plan, payload.preserved


# ---------------------------------------------------------------------------
# 计划准入（R2-I09：非法图零派发、计划版 + 实际输入清单先落盘再派发）
# ---------------------------------------------------------------------------

_ADMISSION_KEYS: Final = frozenset({"plan_digest", "plan_spec_version", "input_version_digest", "nodes"})
#: 准入记录兼容扩展键：已采纳保留前置的输入版本（旧记录无此键时按「无可复用」）。
_ADMISSION_OPTIONAL_KEYS: Final = frozenset({"preserved_inputs"})
_NODE_INPUT_KEYS: Final = frozenset({"node_id", "node_digest", "input_digest"})
_PRESERVED_INPUT_KEYS: Final = frozenset({"node_id", "input_digest"})


@dataclass(frozen=True, slots=True)
class NodeInputRecord:
    """单节点实际输入记录：节点内容摘要 + 该节点采用的输入指纹。"""

    node_id: str
    node_digest: str
    input_digest: str

    def to_wire(self) -> dict[str, str]:
        return {"node_id": self.node_id, "node_digest": self.node_digest, "input_digest": self.input_digest}


@dataclass(frozen=True, slots=True)
class ActualInputManifest:
    """本次实际采用的前置输入版本清单（ActualInputManifest 等价结构，R2-I09）。

    ``input_version_digest`` 为本次执行依据的输入版本指纹；``None`` 表示无法
    核定（不伪造），此时保留前置**不得**被判定为「仍适用」。``nodes`` 按计划
    顺序（拓扑序）给出每个节点内容摘要与其输入指纹。
    """

    input_version_digest: str | None
    nodes: tuple[NodeInputRecord, ...]

    def digest_for(self, node_id: str) -> str | None:
        for record in self.nodes:
            if record.node_id == node_id:
                return record.input_digest
        return None

    def to_wire(self) -> dict[str, Any]:
        return {
            "input_version_digest": self.input_version_digest,
            "nodes": [record.to_wire() for record in self.nodes],
        }


@dataclass(frozen=True, slots=True)
class PlanAdmission:
    """计划准入事实：计划版摘要 + 结构版本 + 实际输入清单 + 保留前置适用指纹。

    ``preserved_expected`` 为本次判定仍适用的保留前置及其输入指纹（空表示无可
    复用旧成果）；不在其中的保留前置不得被下游采用。
    """

    plan_digest: str
    plan_spec_version: int
    manifest: ActualInputManifest
    preserved_expected: Mapping[str, str] = field(default_factory=dict)

    def to_wire(self) -> dict[str, Any]:
        return {
            "plan_digest": self.plan_digest,
            "plan_spec_version": self.plan_spec_version,
            "input_version_digest": self.manifest.input_version_digest,
            "nodes": [record.to_wire() for record in self.manifest.nodes],
            "preserved_inputs": [
                {"node_id": node_id, "input_digest": digest} for node_id, digest in self.preserved_expected.items()
            ],
        }


def _topological_order(plan: PlanSpecProposal) -> tuple[str, ...]:
    """计划节点的拓扑序（依赖先于后继；只计计划内边，环明确拒绝）。

    计划外依赖（重试轮保留前置）不参与入度与边：其满足性由准入与调度器按输入
    指纹单独核定（:func:`compute_actual_input_manifest` 的 ``external_input_digests``）。
    """

    order = tuple(node.node_id for node in plan.nodes)
    in_degree: dict[str, int] = {
        node.node_id: sum(1 for dependency in node.depends_on if dependency in order) for node in plan.nodes
    }
    dependents: dict[str, list[str]] = {node_id: [] for node_id in order}
    for node in plan.nodes:
        for dependency in node.depends_on:
            if dependency in dependents:
                dependents[dependency].append(node.node_id)
    ready = [node_id for node_id in order if in_degree[node_id] == 0]
    resolved: list[str] = []
    while ready:
        node_id = ready.pop(0)
        resolved.append(node_id)
        for dependent in dependents[node_id]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                ready.append(dependent)
    if len(resolved) != len(order):
        _fail("plan_cycle", "$.nodes")
    return tuple(resolved)


def compute_actual_input_manifest(
    plan: PlanSpecProposal,
    *,
    input_version_digest: str | None,
    external_input_digests: Mapping[str, str] | None = None,
) -> ActualInputManifest:
    """按拓扑序计算每个节点的输入指纹（节点内容 + 前置输入指纹 + 输入版本）。

    前置输入指纹指该前置节点被派发时采用的输入版本（父据此启动叶，故本轮内
    节点天然满足）；计划外前置（重试轮保留前置）的指纹由
    ``external_input_digests`` 提供——仅当该前置已被判定仍适用时才可计入。
    任一前置指纹不可得时该节点（及其不可解后继）不进入清单：其下游按阻断处理，
    不以不可核对的输入推进（R21）。
    """

    by_id = {node.node_id: node for node in plan.nodes}
    external = dict(external_input_digests or {})
    digests: dict[str, str] = {}
    records: list[NodeInputRecord] = []
    for node_id in _topological_order(plan):
        node = by_id[node_id]
        dependency_digests: list[str] = []
        unresolved = False
        for dependency in node.depends_on:
            digest = digests.get(dependency) or external.get(dependency)
            if digest is None:
                unresolved = True
                break
            dependency_digests.append(digest)
        if unresolved:
            continue
        node_digest = plan_node_digest(node)
        digest = node_input_digest(
            node_digest=node_digest,
            dependency_input_digests=tuple(dependency_digests),
            input_version_digest=input_version_digest,
        )
        digests[node_id] = digest
        records.append(NodeInputRecord(node_id=node_id, node_digest=node_digest, input_digest=digest))
    return ActualInputManifest(input_version_digest=input_version_digest, nodes=tuple(records))


def admit_research_plan(
    plan: PlanSpecProposal,
    *,
    plan_digest: str,
    role_capabilities: Mapping[str, AbstractSet[str]] | None,
    preserved: tuple[NodeOutcome, ...] = (),
    preserved_input_manifest: Mapping[str, str] | None = None,
    input_version_digest: str | None = None,
) -> PlanAdmission:
    """计划准入（派发前唯一判定）：图/角色/能力校验 → 保留前置适用性 → 输入清单。

    - ``plan_digest`` 必须等于计划内容摘要（计划版身份不得与内容脱钩）；重试轮
      载荷的 ``plan_digest`` 指原计划版身份、内容为范围内节点子集，故有保留前置
      时改由派发前 :func:`build_retry_payload` 的范围内派生与指纹核对保证；
    - ``role_capabilities`` 缺省即无策略来源，**fail-closed**（不以提案自述
      能力自授权）；
    - :func:`validate_plan_spec_proposal` 覆盖空计划/超 20 叶/重复节点/自依赖/
      缺引用/环/重复依赖/重复能力/重复来源/未知角色/越权能力（重试轮计划外依赖
      由保留前置满足，不判缺引用）；
    - 保留前置「仍适用」证据 = 结果回报的输入指纹与 ``preserved_input_manifest``
      记录一致且输入版本已核定；不一致只标记为不适用（不采纳、阻其下游），不
      伪造其成功。

    Raises:
        AgentValidationError: ``plan_digest_mismatch`` / ``plan_admission_policy_missing``
            及 :func:`validate_plan_spec_proposal` 的稳定错误码。
    """

    if not isinstance(plan, PlanSpecProposal):
        _fail("invalid_type", "$.plan_spec")
    expected_digest = _require_digest(plan_digest, "$.plan_digest")
    if not preserved and plan_spec_digest(plan) != expected_digest:
        _fail("plan_digest_mismatch", "$.plan_digest")
    if role_capabilities is None:
        _fail("plan_admission_policy_missing", "$.role_capabilities")
    validate_plan_spec_proposal(
        plan,
        allowed_role_capabilities=role_capabilities,
        satisfied_external_dependencies={outcome.node_id for outcome in preserved},
    )
    _validate_preserved(preserved, scope_node_ids={node.node_id for node in plan.nodes})

    recorded = dict(preserved_input_manifest or {})
    preserved_expected: dict[str, str] = {}
    if input_version_digest is not None:
        for outcome in preserved:
            expected = recorded.get(outcome.node_id)
            if expected is not None and outcome.input_digest == expected:
                preserved_expected[outcome.node_id] = expected
    external = {node_id: digest for node_id, digest in preserved_expected.items()}
    return PlanAdmission(
        plan_digest=expected_digest,
        plan_spec_version=PLAN_SPEC_VERSION,
        manifest=compute_actual_input_manifest(
            plan,
            input_version_digest=input_version_digest,
            external_input_digests=external,
        ),
        preserved_expected=preserved_expected,
    )


def decode_plan_admission(value: object, path: str = "$.plan_admission") -> PlanAdmission:
    """严格解码准入记录（计划版摘要/结构版本/输入版本/节点输入指纹）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys_with_optional(value, _ADMISSION_KEYS, _ADMISSION_OPTIONAL_KEYS, path)
    version = value["plan_spec_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != PLAN_SPEC_VERSION:
        _fail("unsupported_version", f"{path}.plan_spec_version")
    raw_nodes = value["nodes"]
    if not isinstance(raw_nodes, list):
        _fail("invalid_type", f"{path}.nodes")
    records: list[NodeInputRecord] = []
    seen: set[str] = set()
    for index, raw_node in enumerate(raw_nodes):
        node_path = f"{path}.nodes[{index}]"
        if not isinstance(raw_node, dict):
            _fail("invalid_type", node_path)
        _require_exact_keys(raw_node, _NODE_INPUT_KEYS, node_path)
        node_id = _require_str(raw_node["node_id"], f"{node_path}.node_id")
        if node_id in seen:
            _fail("duplicate_node", f"{node_path}.node_id")
        seen.add(node_id)
        records.append(
            NodeInputRecord(
                node_id=node_id,
                node_digest=_require_digest(raw_node["node_digest"], f"{node_path}.node_digest"),
                input_digest=_require_digest(raw_node["input_digest"], f"{node_path}.input_digest"),
            )
        )
    raw_preserved = value.get("preserved_inputs", [])
    if not isinstance(raw_preserved, list):
        _fail("invalid_type", f"{path}.preserved_inputs")
    preserved_expected: dict[str, str] = {}
    for index, raw_input in enumerate(raw_preserved):
        input_path = f"{path}.preserved_inputs[{index}]"
        if not isinstance(raw_input, dict):
            _fail("invalid_type", input_path)
        _require_exact_keys(raw_input, _PRESERVED_INPUT_KEYS, input_path)
        node_id = _require_str(raw_input["node_id"], f"{input_path}.node_id")
        if node_id in preserved_expected:
            _fail("duplicate_node", f"{input_path}.node_id")
        preserved_expected[node_id] = _require_digest(raw_input["input_digest"], f"{input_path}.input_digest")
    return PlanAdmission(
        plan_digest=_require_digest(value["plan_digest"], f"{path}.plan_digest"),
        plan_spec_version=version,
        manifest=ActualInputManifest(
            input_version_digest=_opt_digest_or_none(value["input_version_digest"], f"{path}.input_version_digest"),
            nodes=tuple(records),
        ),
        preserved_expected=preserved_expected,
    )


@DBOS.step(name=PLAN_ADMIT_STEP_NAME)
async def record_plan_admission(admission: dict[str, Any]) -> dict[str, Any]:
    """计划准入检查点（父 workflow 的首个持久 step）。

    作为单个 step 记录输出：计划版摘要、结构版本与实际输入清单随原生执行记录
    一并持久化（重放按记录还原，不重算、不改写）；解码失败即拒绝，后续叶零派发。
    计划全文（含角色/能力/来源引用）已随 workflow 输入载荷持久化，本 step 是其
    可核对的准入引用，不另建应用侧第二套计划存储。
    """

    return decode_plan_admission(admission).to_wire()


@DBOS.step(name=RESEARCH_TERMINAL_STEP_NAME)
async def record_research_terminal_state(
    execution_id: str,
    round_id: str,
    display_state: str | None,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """父 workflow 收敛后落应用执行终态（单个持久 step；重放按记录还原）。

    ``display_state=None`` 表示叶事实未可核定（仍有非终态叶）：本 step 不写任何
    终态并回报原因（未知保持未知，R21）。可核定时经共享账本写执行终态与
    ``execution_changed`` 可靠事件；已终态/非当前轮/已清理 fence 时不覆写，
    由 DAO 事实如实分列。
    """

    if display_state is None:
        return {"projected": False, "display_state": None, "reason": "unverified_facts"}
    dao = TerminalStateDaoRegistry.require()
    fact = await dao.record_execution_terminal_state(
        execution_id=execution_id,
        round_id=round_id,
        display_state=display_state,
        evidence=evidence,
    )
    return {
        "projected": bool(fact.projected),
        "display_state": fact.display_state,
        "reason": fact.reason,
    }


# ---------------------------------------------------------------------------
# 实际输入绑定（应用轮次 + 准确前置身身份；父派发任何叶之前的强制检查点）
# ---------------------------------------------------------------------------

_BINDING_KEYS: Final = frozenset({"execution_id", "round_id", "plan_digest", "input_revision", "owner_epoch", "leaves"})
_BINDING_LEAF_KEYS: Final = frozenset({"node_id", "leaf_id", "input_digest"})


def _canonical_json(payload: object) -> str:
    """规范 JSON 文本（键序稳定、无空白；冻结输入由 DAO 视作 CanonicalJson）。"""

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def leaf_ticket_slot_id(execution_id: str, round_id: str, node_id: str) -> str:
    """叶票据稳定身份（同执行/轮次/节点幂等重放；已释放不得复用）。"""

    return stable_id(
        "leaf_ticket",
        [
            _require_str(execution_id, "$.leaf_ticket.execution_id"),
            _require_str(round_id, "$.leaf_ticket.round_id"),
            _require_str(node_id, "$.leaf_ticket.node_id"),
        ],
    )


@dataclass(frozen=True, slots=True)
class LeafIdentity:
    """一张叶的冻结派发身份：节点 + 持久叶 workflow 身份 + 采用的输入指纹。"""

    node_id: str
    leaf_id: str
    input_digest: str

    def to_wire(self) -> dict[str, str]:
        return {"node_id": self.node_id, "leaf_id": self.leaf_id, "input_digest": self.input_digest}


@dataclass(frozen=True, slots=True)
class ActualInputBinding:
    """父绑定的实际输入事实（应用轮次 + 每叶身份；叶派发前必须冻结）。

    ``input_revision`` 为 DAO 建立的有效输入版（叶据此核对归属事实未过期）；
    ``owner_epoch`` 为执行代次（票据认领/释放的权威身份）。``leaves`` 覆盖
    本次准入清单可派发的全部节点（无输入指纹的节点不进入清单，由调度器阻断）。
    """

    execution_id: str
    round_id: str
    plan_digest: str
    input_revision: int
    owner_epoch: int
    leaves: tuple[LeafIdentity, ...]

    def leaf(self, node_id: str) -> LeafIdentity | None:
        for leaf in self.leaves:
            if leaf.node_id == node_id:
                return leaf
        return None

    def to_wire(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "round_id": self.round_id,
            "plan_digest": self.plan_digest,
            "input_revision": self.input_revision,
            "owner_epoch": self.owner_epoch,
            "leaves": [leaf.to_wire() for leaf in self.leaves],
        }


def decode_actual_input_binding(value: object, path: str = "$.actual_input_binding") -> ActualInputBinding:
    """严格解码实际输入绑定（全键必填、叶节点唯一、身份下限 1）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_exact_keys(value, _BINDING_KEYS, path)
    raw_leaves = value["leaves"]
    if not isinstance(raw_leaves, list):
        _fail("invalid_type", f"{path}.leaves")
    leaves: list[LeafIdentity] = []
    seen: set[str] = set()
    for index, raw_leaf in enumerate(raw_leaves):
        leaf_path = f"{path}.leaves[{index}]"
        if not isinstance(raw_leaf, dict):
            _fail("invalid_type", leaf_path)
        _require_exact_keys(raw_leaf, _BINDING_LEAF_KEYS, leaf_path)
        node_id = _require_str(raw_leaf["node_id"], f"{leaf_path}.node_id")
        if node_id in seen:
            _fail("duplicate_node", f"{leaf_path}.node_id")
        seen.add(node_id)
        leaves.append(
            LeafIdentity(
                node_id=node_id,
                leaf_id=_require_str(raw_leaf["leaf_id"], f"{leaf_path}.leaf_id"),
                input_digest=_require_digest(raw_leaf["input_digest"], f"{leaf_path}.input_digest"),
            )
        )
    return ActualInputBinding(
        execution_id=_require_str(value["execution_id"], f"{path}.execution_id"),
        round_id=_require_str(value["round_id"], f"{path}.round_id"),
        plan_digest=_require_digest(value["plan_digest"], f"{path}.plan_digest"),
        input_revision=_require_positive_int(value["input_revision"], f"{path}.input_revision"),
        owner_epoch=_require_positive_int(value["owner_epoch"], f"{path}.owner_epoch"),
        leaves=tuple(leaves),
    )


def validate_actual_input_binding(
    binding: ActualInputBinding,
    *,
    execution_id: str,
    round_id: str,
    plan_digest: str,
    plan: PlanSpecProposal,
    manifest: ActualInputManifest,
) -> None:
    """核对绑定与本次计划/轮次/准入清单一致（否则父零派发）。

    - 身份三元组与计划版必须相等（绑定不得跨执行/轮次/计划版冒充）；
    - 叶集合必须恰好覆盖准入清单记录的全部节点（无重复、无遗漏、无多余）；
    - 每张叶的 ``leaf_id`` 必须等于按三元组派生的稳定身份、输入指纹等于清单；
    - 其余计划节点（清单未记录 = 输入不可核）不进入绑定，由调度器阻断其派发。
    """

    if binding.execution_id != execution_id:
        _fail("binding_execution_mismatch", "$.actual_input_binding.execution_id")
    if binding.round_id != round_id:
        _fail("binding_round_mismatch", "$.actual_input_binding.round_id")
    if binding.plan_digest != plan_digest:
        _fail("binding_plan_mismatch", "$.actual_input_binding.plan_digest")
    expected = {record.node_id: record.input_digest for record in manifest.nodes}
    bound = {leaf.node_id: leaf for leaf in binding.leaves}
    if set(bound) != set(expected):
        _fail("binding_coverage_mismatch", "$.actual_input_binding.leaves")
    plan_nodes = {node.node_id for node in plan.nodes}
    for node_id, leaf in bound.items():
        if node_id not in plan_nodes:
            _fail("binding_unknown_node", f"$.actual_input_binding.leaves[{node_id}]")
        if leaf.input_digest != expected[node_id]:
            _fail("binding_input_mismatch", f"$.actual_input_binding.leaves[{node_id}].input_digest")
        if leaf.leaf_id != leaf_workflow_id(execution_id=execution_id, round_id=round_id, node_id=node_id):
            _fail("binding_leaf_identity_mismatch", f"$.actual_input_binding.leaves[{node_id}].leaf_id")


#: 父绑定请求 wire 的边界键（由父按载荷 + 派发侧身份构造，PORTABLE JSON）。
_PARENT_BIND_REQUEST_KEYS: Final = frozenset(
    {
        "user_scope",
        "session_id",
        "execution_id",
        "round_id",
        "plan_digest",
        "workflow_id",
        "workflow_code_version",
        "run_epoch",
        "expected_input_revision",
        "frozen_manifest",
        "actual_input",
        "input_digests",
    }
)


def build_parent_bind_request(
    identity: ParentBindIdentity,
    *,
    execution_id: str,
    round_id: str,
    plan_digest: str,
    workflow_id: str,
    frozen_manifest: str,
    actual_input: str,
    input_digests: Mapping[str, str],
) -> dict[str, Any]:
    """构造父绑定请求 wire（派发侧身份 + 父冻结的载荷/实际输入清单）。"""

    return {
        "user_scope": _require_str(identity.user_scope, "$.binding_identity.user_scope"),
        "session_id": _require_str(identity.session_id, "$.binding_identity.session_id"),
        "execution_id": _require_str(execution_id, "$.execution_id"),
        "round_id": _require_str(round_id, "$.round_id"),
        "plan_digest": _require_digest(plan_digest, "$.plan_digest"),
        "workflow_id": _require_str(workflow_id, "$.workflow_id"),
        "workflow_code_version": _require_str(
            identity.workflow_code_version, "$.binding_identity.workflow_code_version"
        ),
        "run_epoch": _require_positive_int(identity.run_epoch, "$.binding_identity.run_epoch"),
        "expected_input_revision": _require_positive_int(
            identity.expected_input_revision, "$.binding_identity.expected_input_revision"
        ),
        "frozen_manifest": _require_str(frozen_manifest, "$.frozen_manifest"),
        "actual_input": _require_str(actual_input, "$.actual_input"),
        "input_digests": {
            _require_str(node_id, "$.input_digests.node_id"): _require_digest(digest, f"$.input_digests.{node_id}")
            for node_id, digest in input_digests.items()
        },
    }


#: 父绑定器：接收绑定请求 wire，返回实际输入绑定 wire（生产为 DAO 适配）。
ActualInputBinder = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@register_singleton
class ActualInputBinderRegistry:
    """父实际输入绑定器登记表（组装期注入；未登记 = 父零派发，fail-closed）。"""

    _instance: ActualInputBinder | None = None

    @classmethod
    def install(cls, binder: ActualInputBinder) -> None:
        if not callable(binder):
            raise TypeError("actual input binder must be callable")
        cls._instance = binder

    @classmethod
    def require(cls) -> ActualInputBinder:
        binder = cls._instance
        if binder is None:
            raise RuntimeError("actual_input_binding_unavailable (assemble agent runtime first)")
        return binder

    @classmethod
    def _reset_singleton(cls) -> None:
        cls._instance = None


@DBOS.step(name=ACTUAL_INPUT_BIND_STEP_NAME)
async def record_actual_input_binding(request: dict[str, Any]) -> dict[str, Any]:
    """实际输入绑定检查点（父首个叶派发前的强制 step）。

    以单个 step 记录输出：应用轮次、输入版、执行代次与每叶身份随原生执行记录
    持久化，重放按记录还原；绑定失败即父零叶派发（不以缺轮次推进）。
    """

    if not isinstance(request, dict):
        _fail("invalid_type", "$.bind_request")
    _require_exact_keys(request, _PARENT_BIND_REQUEST_KEYS, "$.bind_request")
    binder = ActualInputBinderRegistry.require()
    return decode_actual_input_binding(await binder(request)).to_wire()


# ---------------------------------------------------------------------------
# 叶资源票据门（树级额度闭环：认领容量后才启动叶，退出核对后才释放）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LeafTicketLease:
    """一张叶资源票据的落定身份（释放必须完整匹配，不用旧 owner）。"""

    slot_id: str
    user_scope: str
    execution_id: str
    node_id: str
    owner_epoch: int

    def to_wire(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "user_scope": self.user_scope,
            "execution_id": self.execution_id,
            "node_id": self.node_id,
            "owner_epoch": self.owner_epoch,
        }


class LeafTicketGate(Protocol):
    """叶资源票据端口（生产为 ``AgentDao.claim_resource``/``release_resource``）。

    未接线即父零派发：树级额度是安全不变量，缺票据不放行任何叶。
    """

    async def claim(self, *, execution_id: str, round_id: str, node_id: str) -> LeafTicketLease | None: ...

    async def release(self, lease: LeafTicketLease, *, exit_verified: bool) -> None: ...


@register_singleton
class LeafTicketGateRegistry:
    """叶票据门登记表（组装期注入；未登记 = 父零派发，fail-closed）。"""

    _instance: LeafTicketGate | None = None

    @classmethod
    def install(cls, gate: LeafTicketGate) -> None:
        if gate is None:
            raise TypeError("leaf ticket gate must not be None")
        cls._instance = gate

    @classmethod
    def require(cls) -> LeafTicketGate:
        gate = cls._instance
        if gate is None:
            raise RuntimeError("leaf_ticket_gate_unavailable (assemble agent runtime first)")
        return gate

    @classmethod
    def _reset_singleton(cls) -> None:
        cls._instance = None


_TICKET_CLAIM_KEYS: Final = frozenset({"execution_id", "round_id", "node_id", "owner_epoch"})
_TICKET_RELEASE_KEYS: Final = frozenset(
    {"slot_id", "user_scope", "execution_id", "node_id", "owner_epoch", "exit_verified"}
)


@DBOS.step(name=LEAF_TICKET_CLAIM_STEP_NAME)
async def claim_leaf_ticket(request: dict[str, Any]) -> dict[str, Any]:
    """叶票据认领 step：返回 ``{"lease": {...} | None, "reason": str | None}``。

    ``lease`` 为空表示容量/策略受阻（原因随结果记录）；父据此阻断该节点而不
    启动叶（不借超限派发冒充已推进）。登记缺失即 fail-closed（RuntimeError）。
    """

    if not isinstance(request, dict):
        _fail("invalid_type", "$.ticket_claim")
    _require_exact_keys(request, _TICKET_CLAIM_KEYS, "$.ticket_claim")
    execution_id = _require_str(request["execution_id"], "$.ticket_claim.execution_id")
    round_id = _require_str(request["round_id"], "$.ticket_claim.round_id")
    node_id = _require_str(request["node_id"], "$.ticket_claim.node_id")
    _require_positive_int(request["owner_epoch"], "$.ticket_claim.owner_epoch")
    gate = LeafTicketGateRegistry.require()
    try:
        lease = await gate.claim(execution_id=execution_id, round_id=round_id, node_id=node_id)
    except AgentServiceError as exc:
        if exc.code is AgentErrorCode.BUDGET_BLOCKED:
            return {"lease": None, "reason": "budget_blocked"}
        if exc.code is AgentErrorCode.POLICY_DENIED:
            return {"lease": None, "reason": "policy_denied"}
        raise
    return {"lease": None if lease is None else lease.to_wire(), "reason": None if lease is not None else "unavailable"}


@DBOS.step(name=LEAF_TICKET_RELEASE_STEP_NAME)
async def release_leaf_ticket(request: dict[str, Any]) -> bool:
    """叶票据释放 step：按 ``exit_verified`` 决定是否真正放空容量。

    未核退出（``exit_verified=False``）时保持占用、返回 ``False``——不以未知
    结果回收树级额度。登记缺失即 fail-closed（RuntimeError）。
    """

    if not isinstance(request, dict):
        _fail("invalid_type", "$.ticket_release")
    _require_exact_keys(request, _TICKET_RELEASE_KEYS, "$.ticket_release")
    lease = LeafTicketLease(
        slot_id=_require_str(request["slot_id"], "$.ticket_release.slot_id"),
        user_scope=_require_str(request["user_scope"], "$.ticket_release.user_scope"),
        execution_id=_require_str(request["execution_id"], "$.ticket_release.execution_id"),
        node_id=_require_str(request["node_id"], "$.ticket_release.node_id"),
        owner_epoch=_require_positive_int(request["owner_epoch"], "$.ticket_release.owner_epoch"),
    )
    exit_verified = request["exit_verified"]
    if not isinstance(exit_verified, bool):
        _fail("invalid_type", "$.ticket_release.exit_verified")
    gate = LeafTicketGateRegistry.require()
    await gate.release(lease, exit_verified=exit_verified)
    return exit_verified


# ---------------------------------------------------------------------------
# 汇总（只报叶事实，不宣称目标覆盖）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlanRunSummary:
    """一次父执行的叶事实汇总。

    ``leaves_all_succeeded`` 只说明全部叶``complete``，**不代表目标已覆盖**；
    目标覆盖/收敛结论由服务层按 F04 保存（不以成功叶数量代替）。
    ``blocked`` 为上游失败/终止/partial/``closed_unknown`` 导致未启动的节点，
    ``block_reasons`` 给出 ``(node_id, 原因)`` 供保存受阻原因。``pending`` 为非
    终态节点（暂停/等待/``unknown`` 等），非空中则 ``converged`` 为 False。
    ``closed_unknown`` 为已结束但结果未知的结案叶（不可恢复终态）：单独列出，
    不并入 ``failed``/``ended``，也不进入 ``pending``（F10）。

    ``adopted_inputs`` 为本次**实际采用**的输入版本记录 ``(node_id, 输入指纹)``
    （已启动叶与已采纳保留前置）；``stale_preserved`` 为输入版本已不适用而未
    采纳的保留前置（其下游按阻断记录，不启动），二者共同构成 R2-I09 的可核对面。
    """

    succeeded: tuple[str, ...]
    failed: tuple[str, ...]
    withdrawn: tuple[str, ...]
    ended: tuple[str, ...]
    partial: tuple[str, ...]
    closed_unknown: tuple[str, ...]
    blocked: tuple[str, ...]
    pending: tuple[str, ...]
    block_reasons: tuple[tuple[str, str], ...]
    outcomes: tuple[NodeOutcome, ...]
    leaves_all_succeeded: bool
    converged: bool
    adopted_inputs: tuple[tuple[str, str], ...] = ()
    stale_preserved: tuple[str, ...] = ()

    def to_wire(self) -> dict[str, Any]:
        return {
            _PAYLOAD_VERSION_KEY: PAYLOAD_VERSION,
            "succeeded": list(self.succeeded),
            "failed": list(self.failed),
            "withdrawn": list(self.withdrawn),
            "ended": list(self.ended),
            "partial": list(self.partial),
            "closed_unknown": list(self.closed_unknown),
            "blocked": list(self.blocked),
            "pending": list(self.pending),
            "block_reasons": [[node_id, reason] for node_id, reason in self.block_reasons],
            "outcomes": [outcome.to_wire() for outcome in self.outcomes],
            "leaves_all_succeeded": self.leaves_all_succeeded,
            "converged": self.converged,
            "adopted_inputs": [[node_id, digest] for node_id, digest in self.adopted_inputs],
            "stale_preserved": list(self.stale_preserved),
        }


# ---------------------------------------------------------------------------
# 应用执行终态投影（P1-08：父不只回原生汇总，已核叶事实落应用状态）
# ---------------------------------------------------------------------------


class TerminalStateDaoRegistry:
    """应用执行终态写入端口登记（组合根安装；进程内单例，测试显式复位）。

    未安装时惰性取共享 ``CacheManager().agent_dao``；仍不可得则明确拒绝——
    不静默跳过终态落库（R21）。
    """

    _instance: Any | None = None

    @classmethod
    def install(cls, dao: Any | None) -> None:
        cls._instance = dao

    @classmethod
    def require(cls) -> Any:
        dao = cls._instance
        if dao is not None:
            return dao
        from data.cache.cache_manager import CacheManager

        shared = getattr(CacheManager(), "agent_dao", None)
        if shared is None:
            raise RuntimeError("agent dao unavailable for execution terminal projection")
        return shared

    @classmethod
    def _reset_singleton(cls) -> None:
        cls._instance = None


def install_terminal_state_dao(dao: Any | None) -> None:
    """注入应用执行终态写入端口（组装期一次；与叶执行器登记同契约）。"""

    TerminalStateDaoRegistry.install(dao)


def terminal_display_state_from_summary(summary: PlanRunSummary) -> str | None:
    """叶事实汇总 → 应用执行终态；不可核定时返回 ``None``（不伪造结论，R21）。

    只依据已核叶事实（不含墙钟/原生状态）：

    - 仍有非终态叶（``pending``）→ ``None``：本批推进结束不等于研究结束；
    - 全部叶 ``complete`` 且无阻断 → ``complete``；
    - 有失败：已有成果性事实（成功/部分）→ ``partial``；否则 → ``failed``；
    - 无失败：有成功叶但并非全部 → ``partial``；仅部分 → ``partial``；
      仅结案未知 → ``closed_unknown``；仅撤回 → ``withdrawn``；仅终止 → ``ended``；
    - 其余（如只有阻断、无可解释的终态来源）→ ``None``，不臆断终态。
    """

    if summary.pending:
        return None
    if summary.leaves_all_succeeded and not summary.blocked:
        return "complete"
    if summary.failed:
        has_any_result = bool(summary.succeeded or summary.partial)
        return "partial" if has_any_result else "failed"
    if summary.succeeded or summary.partial:
        return "partial"
    if summary.closed_unknown:
        return "closed_unknown"
    if summary.withdrawn:
        return "withdrawn"
    if summary.ended:
        return "ended"
    return None


def _summary_evidence(summary: PlanRunSummary) -> dict[str, Any]:
    """终态投影的核对证据（只含叶身份与状态，不复制成果正文）。"""

    return {
        "succeeded": list(summary.succeeded),
        "failed": list(summary.failed),
        "withdrawn": list(summary.withdrawn),
        "ended": list(summary.ended),
        "partial": list(summary.partial),
        "closed_unknown": list(summary.closed_unknown),
        "blocked": list(summary.blocked),
        "leaves_all_succeeded": summary.leaves_all_succeeded,
    }


# ---------------------------------------------------------------------------
# 就绪叶调度（纯逻辑，不依赖 DBOS）
# ---------------------------------------------------------------------------


class LeafDagScheduler:
    """一层叶 DAG 调度状态机（依赖已通过派发前校验，见 ``validate_plan_spec_proposal``）。

    只依据叶终态事实推进：前置全部 ``complete`` 才就绪；失败/终止/partial/
    ``closed_unknown`` 使下游阻塞并逐层传播（未知结果不能作为成功前置）；
    非终态（暂停/等待/``unknown``）保持 pending，不伪造结论。

    提供准入清单（:class:`ActualInputManifest`）时额外核对输入版本（R2-I09）：
    重试轮保留前置只有输入指纹与当前清单一致且输入版本已核定时才可复用，否则
    不予采纳并阻断其下游；本轮叶回报的输入指纹与清单不符时同样阻断其下游。
    """

    def __init__(
        self,
        plan: PlanSpecProposal,
        *,
        manifest: ActualInputManifest | None = None,
        preserved_expected: Mapping[str, str] | None = None,
    ) -> None:
        self._order: tuple[str, ...] = tuple(node.node_id for node in plan.nodes)
        self._nodes: dict[str, PlanNodeSpec] = {node.node_id: node for node in plan.nodes}
        dependents: dict[str, list[str]] = {node_id: [] for node_id in self._order}
        for node in plan.nodes:
            for dependency in node.depends_on:
                dependents.setdefault(dependency, []).append(node.node_id)
        self._dependents: dict[str, tuple[str, ...]] = {
            node_id: tuple(targets) for node_id, targets in dependents.items()
        }
        self._started: set[str] = set()
        self._outcomes: dict[str, NodeOutcome] = {}
        self._blocked: dict[str, str] = {}
        #: 重试轮冻结的「仍适用成功前置」旧轮事实（计划外节点；不属本轮成果）。
        self._preserved: dict[str, NodeOutcome] = {}
        #: 准入清单：节点 → 本次采用的输入指纹（缺省 = 无清单，保持既有结构判据）。
        self._expected: dict[str, str] | None = (
            None if manifest is None else {record.node_id: record.input_digest for record in manifest.nodes}
        )
        #: 输入版本是否已核定；未核定时保留前置不得被判定为「仍适用」（不推断）。
        self._input_version_known: bool = manifest is not None and manifest.input_version_digest is not None
        #: 准入判定仍适用的保留前置 → 输入指纹（空 = 无可用旧成果）。
        self._preserved_expected: dict[str, str] = dict(preserved_expected or {})
        self._adopted: dict[str, str] = {}
        self._preserved_order: list[str] = []
        self._stale_preserved: list[str] = []

    def node(self, node_id: str) -> PlanNodeSpec:
        """按身份取节点（未知身份明确拒绝）。"""

        node = self._nodes.get(node_id)
        if node is None:
            _fail("unknown_node", f"$.nodes[{node_id}]")
        return node

    def seed_preserved(self, outcomes: tuple[NodeOutcome, ...]) -> None:
        """登记重试轮保留的成功前置（只接受 ``complete`` 且不得在本轮范围内）。

        保留事实来自旧轮已保存成果（不重做、不复制许可与费用）；非成功前置
        不能作为「适用成功前置」，冲突必须显式拒绝而非静默降级。判据与派发前
        :func:`_validate_preserved` 同组（此处为纵深防御）。

        有准入清单时额外核「输入版本仍适用」：旧事实必须回报输入指纹、输入版本
        已核定且与当前清单一致；任一条不成立即不采纳该前置，并阻断其下游（记录
        ``preserved_input_stale:<node_id>``）——不以陈旧前置静默推进后继。
        """

        _validate_preserved(outcomes, scope_node_ids=set(self._nodes))
        for outcome in outcomes:
            if self._expected is None:
                self._preserved[outcome.node_id] = outcome
                self._preserved_order.append(outcome.node_id)
                continue
            expected = self._preserved_expected.get(outcome.node_id)
            applicable = expected is not None and self._input_version_known and outcome.input_digest == expected
            if expected is None or not applicable:
                self._stale_preserved.append(outcome.node_id)
                self._block_dependents(outcome.node_id, f"preserved_input_stale:{outcome.node_id}")
                continue
            self._preserved[outcome.node_id] = outcome
            self._preserved_order.append(outcome.node_id)
            self._adopted[outcome.node_id] = expected

    def _dependency_satisfied(self, dependency: str) -> bool:
        """前置满足：本轮已 ``complete``，或重试轮保留的 ``complete`` 旧事实。"""

        outcome = self._outcomes.get(dependency) or self._preserved.get(dependency)
        return outcome is not None and outcome.state in _SUCCESS_STATES

    def ready(self) -> tuple[str, ...]:
        """当前就绪叶：未启动、未阻塞且全部前置 ``complete``（按计划顺序）。"""

        ready: list[str] = []
        for node_id in self._order:
            if node_id in self._started or node_id in self._blocked:
                continue
            node = self._nodes[node_id]
            if all(self._dependency_satisfied(dependency) for dependency in node.depends_on):
                ready.append(node_id)
        return tuple(ready)

    def start(self, node_id: str) -> None:
        """登记叶启动（仅允许就绪叶；重复启动/越过依赖明确拒绝）。"""

        self.node(node_id)
        if node_id in self._started:
            _fail("node_already_started", f"$.nodes[{node_id}]")
        if node_id not in self.ready():
            _fail("node_not_ready", f"$.nodes[{node_id}]")
        self._started.add(node_id)
        if self._expected is not None:
            expected = self._expected.get(node_id)
            if expected is not None:
                self._adopted[node_id] = expected

    def block(self, node_id: str, reason: str) -> None:
        """显式阻断未启动节点（票据受阻/前置成果缺失等），并逐层阻断其下游。

        已启动节点不得事后阻断（其事实由叶回报决定，不覆盖）；同一节点保留
        首次阻断原因（不覆盖已记录的确定原因）。
        """

        self.node(node_id)
        if node_id in self._started:
            _fail("node_already_started", f"$.nodes[{node_id}]")
        if node_id in self._blocked:
            return
        self._blocked[node_id] = _require_str(reason, "$.block_reason")
        self._block_dependents(node_id, f"blocked_by:{node_id}")

    def predecessor_artifacts(self, node_id: str) -> tuple[tuple[str, str], ...] | None:
        """本轮已成功前置的成果引用（``node_id → artifact_ref``）；不可核即 ``None``。

        只取**本轮**已启动并要求成功前置的成果：任一前置缺少 ``artifact_ref``
        即返回 ``None``（父不得以无成果前置启动后继）。重试轮保留前置属旧轮
        成果，其轮次不在本轮载荷内，不在此处返回（由保留事实承载）。
        """

        node = self.node(node_id)
        refs: list[tuple[str, str]] = []
        for dependency in node.depends_on:
            outcome = self._outcomes.get(dependency)
            if outcome is None:
                continue
            if outcome.state not in _SUCCESS_STATES or outcome.artifact_ref is None:
                return None
            refs.append((dependency, outcome.artifact_ref))
        return tuple(refs)

    def record(self, outcome: NodeOutcome) -> bool:
        """登记叶终态事实；返回是否为新事实（幂等重复事实返回 ``False``）。

        非成功终态递归阻塞其下游。

        终态单调（同类 F07「迟到 running 不覆终态」）：已登记成功/阻塞/结案未知
        终态后再记任何事实都属冲突（迟到/重复事实不得把收敛翻回未收敛，也不得
        复活已结案的 ``closed_unknown``，F10）。非终态重复事实不刷新进度（父据此
        累计无进展观察，不因同一未知事实空转）。

        有准入清单时核该叶回报的输入指纹：成功但指纹与清单不符（输入已变）时
        阻断其全部下游（``upstream_input_changed:<node_id>``），不使其成果被下游
        采用；未回报指纹的叶视为由父按清单派发（本轮内天然满足，不推断旧轮）。
        """

        self.node(outcome.node_id)
        if outcome.state not in _TERMINAL_STATES | _NON_TERMINAL_STATES:
            _fail("invalid_display_state", f"$.outcomes[{outcome.node_id}].state")
        existing = self._outcomes.get(outcome.node_id)
        if existing is not None and existing.state in _TERMINAL_STATES:
            if existing == outcome:
                return False  # 幂等：同一终态事实重复登记（观察重投）不视为冲突
            _fail("node_outcome_conflict", f"$.outcomes[{outcome.node_id}]")
        if existing is not None and existing == outcome:
            return False
        self._outcomes[outcome.node_id] = outcome
        if outcome.state in _BLOCKING_STATES | _CLOSED_UNKNOWN_STATES:
            self._block_dependents(outcome.node_id, f"upstream_{outcome.state}:{outcome.node_id}")
            return True
        if outcome.state in _SUCCESS_STATES and self._expected is not None:
            expected = self._expected.get(outcome.node_id)
            if expected is not None and outcome.input_digest is not None and outcome.input_digest != expected:
                self._block_dependents(outcome.node_id, f"upstream_input_changed:{outcome.node_id}")
        return True

    def _block_dependents(self, node_id: str, reason: str) -> None:
        for dependent in self._dependents.get(node_id, ()):
            if dependent in self._blocked:
                continue
            self._blocked[dependent] = reason
            self._block_dependents(dependent, f"blocked_by:{dependent}")

    def converged(self) -> bool:
        """全部节点均已有终态或阻塞（非终态既不算收敛也不算终态）。"""

        return all(
            node_id in self._blocked
            or (node_id in self._outcomes and self._outcomes[node_id].state in _TERMINAL_STATES)
            for node_id in self._order
        )

    def summary(self) -> PlanRunSummary:
        """叶事实汇总（不做目标覆盖结论）。"""

        def _state_ids(state: str) -> tuple[str, ...]:
            return tuple(
                node_id
                for node_id in self._order
                if node_id in self._outcomes and self._outcomes[node_id].state == state
            )

        succeeded = _state_ids("complete")
        blocked = tuple(node_id for node_id in self._order if node_id in self._blocked)
        # 非终态事实（暂停/等待/unknown）仍属 pending：不参与收敛、不启动后继；
        # closed_unknown 为不可恢复终态，单列于 closed_unknown、不保持可恢复占位。
        pending = tuple(
            node_id
            for node_id in self._order
            if node_id not in self._blocked
            and (node_id not in self._outcomes or self._outcomes[node_id].state in _NON_TERMINAL_STATES)
        )
        return PlanRunSummary(
            succeeded=succeeded,
            failed=_state_ids("failed"),
            withdrawn=_state_ids("withdrawn"),
            ended=_state_ids("ended"),
            partial=_state_ids("partial"),
            closed_unknown=_state_ids("closed_unknown"),
            blocked=blocked,
            pending=pending,
            block_reasons=tuple((node_id, self._blocked[node_id]) for node_id in blocked),
            outcomes=tuple(self._outcomes[node_id] for node_id in self._order if node_id in self._outcomes),
            leaves_all_succeeded=bool(succeeded) and len(succeeded) == len(self._order),
            converged=self.converged(),
            adopted_inputs=tuple(
                (node_id, self._adopted[node_id])
                for node_id in (*self._order, *self._preserved_order)
                if node_id in self._adopted
            ),
            stale_preserved=tuple(self._stale_preserved),
        )


LeafRunner = Callable[[LeafExecutionContext], Awaitable[NodeOutcome]]
"""叶执行器（装配期登记；接收身份上下文与节点，据此保存叶事实）。"""

LeafStart = Callable[[PlanNodeSpec, tuple[tuple[str, str], ...]], Awaitable[str]]
"""启动就绪叶，返回观察键（DBOS 内为子 workflow ID）。

第二个参数为本轮已核定前置成果引用 ``(node_id, artifact_ref)``（叶据此读取并
核对前置成果，缺失时父不启动该叶）。

**必须由父 workflow 协程按计划顺序调用**（不得包进并发任务）：启动记录按
父体内操作序号（``parent_fid``）匹配重放；并发启动会让序号随真实时序漂移，
重放时复接到错误的子执行记录（B1）。
"""

LeafObserve = Callable[[tuple[tuple[str, str], ...]], Awaitable[dict[str, NodeOutcome]]]
"""观察已启动叶：输入 ``(node_id, 观察键)`` 序列，返回本轮新到达的终态事实。

DBOS 内由单个观察 step 承载（其结果被记录），重放时按记录还原完成集合与
迭代次数，父体操作序号因此确定（B1）；无新事实时返回空字典。
"""

LeafWaitCheckpoint = Callable[[], Awaitable[bool]]
"""观察预算耗尽时的持久等待检查点（返回 ``True`` 继续新观察窗口）。

生产实现为 :func:`wait_next_observation_window`（DBOS 持久 sleep，崩溃恢复时
跳过已完成睡眠）；纯逻辑调用可不提供，此时预算耗尽即结束本批次。
"""

LeafTicketClaim = Callable[[PlanNodeSpec], Awaitable[str | None]]
"""启动叶前的票据认领：返回阻断原因（``None`` = 已认领可启动）。

生产实现为 :func:`claim_leaf_ticket` 的父协程包装（DBOS step 记录）；缺失/
受阻即阻断节点，不以超限派发冒充已推进。
"""

LeafTicketRelease = Callable[[PlanNodeSpec, bool], Awaitable[None]]
"""叶终态后的票据释放：第二参数为实际退出是否已核（未核保持占用）。
"""


async def wait_next_observation_window(sleep: Callable[[float], Awaitable[None]] = DBOS.sleep_async) -> bool:
    """观察预算耗尽后执行一次持久等待，返回 ``True`` 表示继续观察。

    默认使用 ``DBOS.sleep_async``：它是 DBOS 原生可恢复等待（已完成睡眠在恢复
    时被跳过），故父 workflow 崩溃重启后从记录点继续观察，而不是当作正常结束。
    ``sleep`` 可注入以便单测核验等待间隔而不启动 DBOS 运行时。
    """

    await sleep(IDLE_WAIT_INTERVAL_SEC)
    return True


async def drive_leaf_dag(
    plan: PlanSpecProposal,
    start_leaf: LeafStart,
    observe_leaves: LeafObserve,
    *,
    preserved: tuple[NodeOutcome, ...] = (),
    manifest: ActualInputManifest | None = None,
    preserved_expected: Mapping[str, str] | None = None,
    idle_observation_limit: int | None = None,
    wait_checkpoint: LeafWaitCheckpoint | None = None,
    claim_leaf: LeafTicketClaim | None = None,
    release_leaf: LeafTicketRelease | None = None,
    drain_answers: Callable[[], Awaitable[None]] | None = None,
) -> PlanRunSummary:
    """事件驱动推进一层叶 DAG，返回叶事实汇总。

    每个完成事实到达后立即启动新就绪叶（不按固定批次 gather）。``preserved``
    为重试轮冻结的旧轮成功前置（计划外节点，不重做、不重计费用）。``start_leaf``
    须把「已判定的业务失败」作为 ``NodeOutcome(state="failed")`` 返回；未判定
    异常与 ``CancelledError`` 向上传播（不伪造 failed、不吞取消，R2/R21）。
    非终态结果（暂停/等待/``unknown``）使本次推进在没有可运行叶时结束且不收敛；
    ``closed_unknown`` 为不可恢复终态，按结案收口并阻其下游（F10）。

    观察预算（``idle_observation_limit`` 个连续无进展切片）耗尽**不是父终态**：
    提供 ``wait_checkpoint``（生产为 DBOS 持久等待）时，本函数持久等待后开启新
    观察窗口继续推进——超预算完成的长叶仍只启动一次后继（R02）；未提供时（纯
    逻辑调用）按本批次无可恢复接续者结束并保持 ``converged=False``，不伪造终态。

    确定性（B1 重放面）：本循环只由**父协程顺序**发出启动/观察操作——不创建
    并发任务，不以真实时序决定操作顺序；完成集合由 ``observe_leaves`` 的
    检查点结果决定。故重放时父体操作序号逐项对齐，子 workflow 记录不会错配。
    ``manifest`` 为计划准入确定的实际输入清单（R2-I09）：提供时保留前置与叶回报
    的输入指纹按清单核对，不适用即阻断下游；缺省保持既有结构判据。
    ``drain_answers`` 为每轮推进前的答复消费入口（生产为原生 ``recv``，无答复
    即空转）：只消费本执行/本轮的答复，不阻塞叶观察；缺省不接收答复（纯逻辑
    调用）。
    """

    scheduler = LeafDagScheduler(plan, manifest=manifest, preserved_expected=preserved_expected)
    scheduler.seed_preserved(preserved)
    running: dict[str, str] = {}
    idle_observations = 0
    while True:
        if drain_answers is not None:
            # 受治理答复消费：每轮推进前接收一条（无消息即返回，不拖慢叶推进）。
            await drain_answers()
        for node_id in scheduler.ready():
            node = scheduler.node(node_id)
            if manifest is not None and manifest.digest_for(node_id) is None:
                # 输入指纹不可核（清单未记录）的节点不得派发：其叶无法回报可核输入。
                scheduler.block(node_id, f"input_manifest_unresolved:{node_id}")
                continue
            if claim_leaf is not None:
                blocked_reason = await claim_leaf(node)
                if blocked_reason is not None:
                    scheduler.block(node_id, blocked_reason)
                    continue
            predecessor_artifacts = scheduler.predecessor_artifacts(node_id)
            if predecessor_artifacts is None:
                scheduler.block(node_id, f"predecessor_artifact_missing:{node_id}")
                continue
            scheduler.start(node_id)
            running[node_id] = await start_leaf(node, predecessor_artifacts)
        if scheduler.converged():
            break
        if not running:
            # 无可运行叶且未收敛：剩余节点为非终态事实或不可达（不伪造结论）。
            break
        facts = await observe_leaves(tuple(running.items()))
        if not facts:
            idle_observations += 1
            if idle_observation_limit is not None and idle_observations >= idle_observation_limit:
                if wait_checkpoint is None:
                    # §8.3：无持久等待接续者时只停观察，不取消子执行、不伪造终态。
                    break
                # 预算耗尽：持久等待检查点后重开观察窗口（叶子仍在运行，父不结束）。
                idle_observations = 0
                if not await wait_checkpoint():
                    break
            continue
        progressed = False
        new_terminal_facts: list[tuple[str, NodeOutcome]] = []
        for node_id, outcome in facts.items():
            if node_id not in running:
                # 未启动节点的完成事实：不得计入本轮成果（身份核验）
                _fail("outcome_unknown_node", f"$.outcomes[{node_id}]")
            if outcome.node_id != node_id:
                # 错归属结果不得记到本节点（身份核验，DD §4.1）
                _fail("outcome_identity_mismatch", f"$.outcomes[{node_id}].node_id")
            if scheduler.record(outcome):
                progressed = True
                if outcome.state in _TERMINAL_STATES:
                    new_terminal_facts.append((node_id, outcome))
        for node_id, outcome in new_terminal_facts:
            running.pop(node_id, None)
            if release_leaf is not None:
                # 已核退出才放空容量；未核退出（unknown）保持占用（树级额度闭环）。
                await release_leaf(scheduler.node(node_id), outcome.exit_verified)
        if progressed:
            idle_observations = 0
            continue
        # 仅观察到已登记的非终态事实：无新进展。无持久等待接续者时结束本批次
        # （不空转）；有接续者则计入无进展预算，耗尽后持久等待再观察。
        if idle_observation_limit is None:
            break
        idle_observations += 1
        if idle_observations >= idle_observation_limit:
            if wait_checkpoint is None:
                break
            idle_observations = 0
            if not await wait_checkpoint():
                break
    return scheduler.summary()


# ---------------------------------------------------------------------------
# 叶执行器登记（组装期注入；未登记明确拒绝）
# ---------------------------------------------------------------------------


@register_singleton
class LeafRuntimeRegistry:
    """叶执行器登记表（D-03/E 系列组装期安装；进程内单例，测试自动复位）。

    未登记时 ``require`` 明确拒绝——不默认放行、不以占位执行器冒充真实叶执行。
    """

    _instance: LeafRunner | None = None

    @classmethod
    def install(cls, runner: LeafRunner) -> None:
        if not callable(runner):
            raise TypeError("leaf runner must be callable")
        cls._instance = runner

    @classmethod
    def require(cls) -> LeafRunner:
        runner = cls._instance
        if runner is None:
            raise RuntimeError("leaf runner not installed (assemble agent runtime first)")
        return runner

    @classmethod
    def _reset_singleton(cls) -> None:
        cls._instance = None


# ---------------------------------------------------------------------------
# 原生 workflow（唯一持久推进面；名称发布后稳定）
# ---------------------------------------------------------------------------


@DBOS.step(name=LEAF_EXECUTE_STEP_NAME)
async def execute_leaf_step(payload: dict[str, Any]) -> dict[str, Any]:
    """叶执行 step：解析封闭载荷 → 已登记执行器 → 终态事实 wire。"""

    context = decode_leaf_payload(payload)
    outcome = await LeafRuntimeRegistry.require()(context)
    if outcome.node_id != context.node.node_id:
        _fail("outcome_identity_mismatch", "$.node_outcome.node_id")
    return outcome.to_wire()


@DBOS.workflow(name=LEAF_WORKFLOW_NAME, serialization_type=WorkflowSerializationFormat.PORTABLE)
async def run_leaf_workflow(payload: dict[str, Any]) -> dict[str, Any]:
    """一层叶 workflow（独立上下文；不暴露继续委派）。"""

    decode_leaf_payload(payload)
    return await execute_leaf_step(payload)


#: 需要读取叶执行 step 落盘记录的原生状态（原生状态本身不足以判定实际退出与成果）。
_LEAF_EVIDENCE_STATUSES: Final[frozenset[str]] = frozenset(
    {
        WorkflowStatusString.ERROR.value,
        WorkflowStatusString.MAX_RECOVERY_ATTEMPTS_EXCEEDED.value,
        WorkflowStatusString.CANCELLED.value,
    }
)


async def _leaf_step_evidence(workflow_id: str) -> tuple[bool, object]:
    """读取叶执行 step 的落盘记录：``(已有执行记录, 已落盘成果)``。

    DBOS 默认不抢占在途 step（``preemptible=False``）：取消可能先落到工作流状态，
    而在途 IO 结束后仍会把 ``agent_leaf_execute`` 的成果写入 step 记录。故只能在
    step 落盘后断言「实际退出」，不得据原生状态冒充（R04）。
    """

    for step in await DBOS.list_workflow_steps_async(workflow_id):
        if step["function_name"] == LEAF_EXECUTE_STEP_NAME:
            return True, step["output"]
    return False, None


def _terminal_outcome(
    node_id: str,
    *,
    state: str,
    output: object,
    executed: bool = False,
    step_output: object = None,
) -> NodeOutcome | None:
    """把子 workflow 状态投影为叶终态事实；非终态返回 ``None``（不伪造结论）。

    原生状态、实际退出与成果覆盖分开核对，互不冒充（R04）：

    - ``state`` 为 DBOS 原生状态，本身不等于业务结论；
    - ``executed`` 表示叶执行 step 已有落盘记录（实际 IO 已结束并有可核对结果）；
    - ``step_output`` 为已落盘成果——取消/工作流级错误后到达的成果仍按真实成果
      投影（含 ``partial``），不被 ``ended``/``failed`` 覆盖。

    - ``SUCCESS``：返回值必须是本节点的封闭终态 wire（身份核验）；
    - 有落盘成果：直接投影该成果（实际退出已核）；
    - ``ERROR`` / 恢复耗尽：``failed``（通用错误码，明细留在 DBOS 状态，R9）；
    - ``CANCELLED``：已有执行记录但无成果（step 报错）→ ``failed``（退出已核）；
      无任何执行记录（如启动隔离取消）→ ``unknown`` + ``exit_verified=False``：
      执行已结束但成果不可知，**不得**报成 ``ended``（把未核退出伪装成正常结束），
      父保持观察、票据保持占用（树级额度不因未知放空）；
    - 其余（PENDING/ENQUEUED/DELAYED）：非终态，不参与收敛。
    """

    if state == WorkflowStatusString.SUCCESS.value:
        outcome = decode_node_outcome(output, f"$.children[{node_id}].output")
        if outcome.node_id != node_id:
            _fail("outcome_identity_mismatch", f"$.children[{node_id}].output.node_id")
        return replace(outcome, exit_verified=True)
    if state in _LEAF_EVIDENCE_STATUSES and step_output is not None:
        outcome = decode_node_outcome(step_output, f"$.children[{node_id}].step_output")
        if outcome.node_id != node_id:
            _fail("outcome_identity_mismatch", f"$.children[{node_id}].step_output.node_id")
        return replace(outcome, exit_verified=True)
    if state == WorkflowStatusString.ERROR.value:
        return NodeOutcome(node_id=node_id, state="failed", error_code="leaf_workflow_error")
    if state == WorkflowStatusString.MAX_RECOVERY_ATTEMPTS_EXCEEDED.value:
        return NodeOutcome(node_id=node_id, state="failed", error_code="leaf_workflow_recovery_exhausted")
    if state == WorkflowStatusString.CANCELLED.value:
        if executed:
            return NodeOutcome(node_id=node_id, state="failed", error_code="leaf_workflow_error")
        # 无执行记录不能证明实际退出：报未知（非终态）而非 ended，票据不放空。
        return NodeOutcome(
            node_id=node_id,
            state="unknown",
            error_code="leaf_workflow_exit_unverified",
            exit_verified=False,
        )
    return None


@DBOS.step(name=LEAF_OBSERVE_STEP_NAME)
async def observe_leaf_children(children: list[dict[str, str]], slice_seconds: float) -> list[dict[str, Any]]:
    """观察已启动叶：返回本轮新到达的终态事实 wire（无终态则空列表）。

    作为**单个 step** 记录输出：父 workflow 重放时按记录还原完成集合与迭代
    次数，父体操作序号逐项对齐（B1 重放面）。内部按 ``slice_seconds`` 轮询，
    只影响检查点密度与观察延迟，不参与任何业务判定。
    """

    deadline = time.monotonic() + max(slice_seconds, 0.0)
    # 每个工作流在本 slice 内只读一次执行记录（实际退出/成果覆盖证据）。
    evidence: dict[str, tuple[bool, object]] = {}
    while True:
        facts: list[dict[str, Any]] = []
        for child in children:
            node_id = _require_str(child.get("node_id"), "$.children[].node_id")
            workflow_id = _require_str(child.get("workflow_id"), "$.children[].workflow_id")
            status: WorkflowStatus | None = await DBOS.get_workflow_status_async(workflow_id)
            if status is None:
                _fail("unknown_leaf_execution", f"$.children[{node_id}]")
            executed, step_output = False, None
            if status.status in _LEAF_EVIDENCE_STATUSES:
                cached = evidence.get(workflow_id)
                if cached is None:
                    cached = await _leaf_step_evidence(workflow_id)
                    evidence[workflow_id] = cached
                executed, step_output = cached
            outcome = _terminal_outcome(
                node_id,
                state=status.status,
                output=status.output,
                executed=executed,
                step_output=step_output,
            )
            if outcome is not None:
                facts.append(outcome.to_wire())
        if facts or time.monotonic() >= deadline:
            return facts
        await asyncio.sleep(OBSERVE_POLL_INTERVAL_SEC)


# ---------------------------------------------------------------------------
# 答复消费（原生 recv → 身份核验 → DAO 幂等 CAS；投递后唯一消费入口）
# ---------------------------------------------------------------------------

ConsumeAnswer = Callable[[str, str], Awaitable[ConsumptionFact]]
"""(decision_id, client_command_id) → 消费事实；装配期注入以绑定用户域与 DAO。"""


@dataclass(frozen=True, slots=True)
class AnswerConsumption:
    """一条原生答复消息的处理结论（拒绝不等于已消费，R21）。"""

    decision_id: str | None
    status: str
    command_record_id: str | None
    consumption_state: str | None
    reason: str | None = None


@register_singleton
class AnswerConsumerRegistry:
    """答复消费者登记（组合根安装；未安装明确拒绝，不伪造已消费）。"""

    _instance: ConsumeAnswer | None = None

    @classmethod
    def install(cls, consumer: ConsumeAnswer) -> None:
        if not callable(consumer):
            raise TypeError("answer consumer must be callable")
        cls._instance = consumer

    @classmethod
    def require(cls) -> ConsumeAnswer:
        consumer = cls._instance
        if consumer is None:
            raise RuntimeError("answer consumer not installed (assemble agent runtime first)")
        return consumer

    @classmethod
    def _reset_singleton(cls) -> None:
        cls._instance = None


@DBOS.step(name=CONSUME_ANSWER_STEP_NAME)
async def consume_answer_step(decision_id: str, client_command_id: str) -> dict[str, Any]:
    """受治理答复消费步骤：按原答复命令身份确认生效（DAO CAS 幂等）。

    身份/期限/轮次不符时 DAO 在写 ``consumed`` 前拒绝（``AgentServiceError``）：
    本步骤把该拒绝作为**步骤成功结果**返回（保留 ``not_consumed``），不让单条
    无效答复把父 workflow 拖入恢复耗尽。取消与未预期异常原样上抛（R2）。
    """

    consumer = AnswerConsumerRegistry.require()
    try:
        fact = await consumer(decision_id, client_command_id)
    except AgentServiceError as e:
        return {
            "status": "rejected",
            "decision_id": decision_id,
            "command_record_id": None,
            "consumption_state": ConsumptionState.NOT_CONSUMED.value,
            "reason": e.code.value,
        }
    return {
        "status": "consumed",
        "decision_id": fact.decision_id,
        "command_record_id": fact.command_record_id,
        "consumption_state": fact.consumption_state.value,
        "reason": None,
    }


async def receive_pending_answers(
    *,
    execution_id: str,
    round_id: str,
    recv: Callable[..., Awaitable[Any]] = DBOS.recv_async,
    consume: Callable[[str, str], Awaitable[dict[str, Any]]] = consume_answer_step,
    timeout_seconds: float = ANSWER_RECV_TIMEOUT_SEC,
) -> AnswerConsumption | None:
    """从原生答复主题接收一条答复，核验本执行/本轮身份后消费。

    - 无答复：``recv`` 超时返回 ``None``，本入口返回 ``None``（不阻塞叶推进）；
    - 消息结构非法：如实拒绝（不解析为任意身份，不调用消费入口）；
    - 身份不符（执行/轮次不是本 workflow 所属）：**拒绝**——不调用消费入口，
      答复命令在应用侧保留 ``not_consumed``；
    - 匹配：交受治理消费步骤（DAO 幂等 CAS；重复投递返回原事实，只消费一次）；
    - 取消原样上抛（R2），不把取消读成「无消息」。
    """

    raw = await recv(ANSWER_MESSAGE_TOPIC, timeout_seconds=timeout_seconds)
    if raw is None:
        return None
    try:
        message = decode_answer_message(raw)
    except AgentValidationError as e:
        return AnswerConsumption(
            decision_id=None,
            status="rejected",
            command_record_id=None,
            consumption_state=None,
            reason=f"invalid_answer_message:{e.code}",
        )
    if message.execution_id != execution_id or message.round_id != round_id:
        return AnswerConsumption(
            decision_id=message.decision_id,
            status="rejected",
            command_record_id=None,
            consumption_state=None,
            reason="answer_identity_mismatch",
        )
    wire = await consume(message.decision_id, message.client_command_id)
    return AnswerConsumption(
        decision_id=wire["decision_id"],
        status=wire["status"],
        command_record_id=wire["command_record_id"],
        consumption_state=wire["consumption_state"],
        reason=wire["reason"],
    )


@DBOS.workflow(name=RESEARCH_WORKFLOW_NAME, serialization_type=WorkflowSerializationFormat.PORTABLE)
async def run_research_workflow(payload: dict[str, Any]) -> dict[str, Any]:
    """父研究 workflow：计划准入 → 启动就绪叶 → 消费完成事实 → 汇总叶事实。

    准入（R2-I09）：派发前先核对计划版摘要与内容一致、经
    :func:`validate_plan_spec_proposal` 校验任务依赖图（空计划/超 20 叶/重复/
    自依赖/缺引用/环/角色/能力），并把计划版摘要与实际输入清单写入首个持久
    step（:func:`record_plan_admission`）。任一步失败即零叶派发：不以 workflow
    继续执行冒充「已准入」，也不以空清单推进后继。

    语义边界：
    - 返回 ``converged=False`` 的汇总（暂停/等待/决定/未知）**不等于研究结束**，
      父 workflow 结束只表示本推进批次结束；收敛后由 :func:`record_research_terminal_state`
      step 把已核叶事实写成应用执行终态（非终态叶不写终态，未知保持未知，R21）。
    - 叶事实在叶执行内部保存（应用侧），父观察被取消/失败不丢失已完成叶成果。
    - 观察预算耗尽转入 DBOS 持久等待（:func:`wait_next_observation_window`）后
      继续观察；父不因超预算正常结束，超预算完成的长叶仍推进其后继（R02）。
      观察异常上抛由恢复机制重接；不伪造叶终态。
    - 重放顺序（B1）：启动/观察操作全部由父协程按计划顺序发出，完成集合由
      ``agent_leaf_observe`` step 的记录还原；子 workflow ID 按身份固定
      （``leaf_workflow_id``）作为去重、观察复接与重试轮隔离。
    - 答复消费（P1-05）：每轮推进前经原生 ``recv`` 接收一条 ``topic=answer``
      答复（非阻塞），只消费本执行/本轮的答复；身份不符如实拒绝并保留
      ``not_consumed``（详见 :func:`receive_pending_answers`）。
    """

    decoded = decode_research_payload_full(payload)
    admission = admit_research_plan(
        decoded.plan,
        plan_digest=decoded.plan_digest,
        role_capabilities=decoded.role_capabilities,
        preserved=decoded.preserved,
        preserved_input_manifest=decoded.preserved_input_manifest,
        input_version_digest=decoded.input_version_digest,
    )
    # 准入检查点先于任何叶启动：结果随原生执行记录持久化，失败即零派发（R21）。
    await record_plan_admission(admission.to_wire())
    execution_id = decoded.execution_id
    round_id = decoded.round_id
    identity = decoded.binding_identity
    if identity is None:
        # 未冻结应用轮次身份即无可建立的轮次：父零叶派发（不伪造轮次、不裸派发）。
        raise RuntimeError("actual_input_binding_unavailable (binding identity not dispatched)")
    input_digests = {record.node_id: record.input_digest for record in admission.manifest.nodes}
    workflow_id = DBOS.workflow_id
    if not workflow_id:
        raise RuntimeError("actual_input_binding_unavailable (workflow identity missing)")
    binding = decode_actual_input_binding(
        await record_actual_input_binding(
            build_parent_bind_request(
                identity,
                execution_id=execution_id,
                round_id=round_id,
                plan_digest=admission.plan_digest,
                workflow_id=workflow_id,
                frozen_manifest=_canonical_json(payload),
                actual_input=_canonical_json(admission.to_wire()),
                input_digests=input_digests,
            )
        )
    )
    validate_actual_input_binding(
        binding,
        execution_id=execution_id,
        round_id=round_id,
        plan_digest=admission.plan_digest,
        plan=decoded.plan,
        manifest=admission.manifest,
    )
    idle_limit = max(1, round(CHILD_OBSERVE_TIMEOUT_SEC / OBSERVE_SLICE_SEC))
    leases: dict[str, dict[str, Any]] = {}

    async def start_leaf(node: PlanNodeSpec, predecessor_artifacts: tuple[tuple[str, str], ...]) -> str:
        leaf = binding.leaf(node.node_id)
        if leaf is None:
            # 绑定未覆盖该节点：父不启动（无轮次身份/输入版的叶不得派发）。
            _fail("actual_input_binding_incomplete", f"$.actual_input_binding.leaves[{node.node_id}]")
        leaf_payload = build_leaf_payload(
            execution_id=execution_id,
            round_id=round_id,
            plan_digest=admission.plan_digest,
            node=node,
            input_digest=leaf.input_digest,
            leaf_id=leaf.leaf_id,
            input_revision=binding.input_revision,
            predecessor_artifacts=predecessor_artifacts,
        )
        with SetWorkflowID(
            leaf.leaf_id,
            workflow_id_reuse_policy="return-existing",
        ):
            handle = await DBOS.start_workflow_async(run_leaf_workflow, leaf_payload)
        return handle.get_workflow_id()

    async def claim_leaf(node: PlanNodeSpec) -> str | None:
        wire = await claim_leaf_ticket(
            {
                "execution_id": execution_id,
                "round_id": round_id,
                "node_id": node.node_id,
                "owner_epoch": binding.owner_epoch,
            }
        )
        lease = wire.get("lease")
        if lease is None:
            return f"leaf_ticket_blocked:{wire.get('reason') or 'unavailable'}"
        # 保留票据的准确归属（owner_epoch 以认领回执为准，释放必须完整匹配）。
        leases[node.node_id] = lease
        return None

    async def release_leaf(node: PlanNodeSpec, exit_verified: bool) -> None:
        lease = leases.pop(node.node_id, None)
        if lease is None:
            # 无认领回执即无释放身份：fail-loud，不构造虚假释放。
            raise RuntimeError("leaf_ticket_release_without_lease")
        await release_leaf_ticket({**lease, "exit_verified": exit_verified})

    async def observe_leaves(children: tuple[tuple[str, str], ...]) -> dict[str, NodeOutcome]:
        wires = await observe_leaf_children(
            [{"node_id": node_id, "workflow_id": workflow_id} for node_id, workflow_id in children],
            OBSERVE_SLICE_SEC,
        )
        outcomes: dict[str, NodeOutcome] = {}
        for wire in wires:
            outcome = decode_node_outcome(wire)
            outcomes[outcome.node_id] = outcome
        return outcomes

    async def drain_answers() -> None:
        # 受治理消费：只接收本执行/本轮的答复；无消息即返回，不阻塞叶观察。
        await receive_pending_answers(execution_id=execution_id, round_id=round_id)

    summary = await drive_leaf_dag(
        decoded.plan,
        start_leaf,
        observe_leaves,
        preserved=decoded.preserved,
        manifest=admission.manifest,
        preserved_expected=admission.preserved_expected,
        idle_observation_limit=idle_limit,
        wait_checkpoint=wait_next_observation_window,
        claim_leaf=claim_leaf,
        release_leaf=release_leaf,
        drain_answers=drain_answers,
    )
    # 已核叶事实 → 应用执行终态（P1-08）：父不只回原生汇总；``wait_result`` 只认
    # 应用状态。未核定（pending）由 step 如实不写，未知保持未知（R21）。
    await record_research_terminal_state(
        execution_id,
        round_id,
        terminal_display_state_from_summary(summary),
        _summary_evidence(summary),
    )
    return summary.to_wire()


def submit_research_workflow(workflow_id: str, payload: dict[str, Any]) -> None:
    """宿主入口（同步）：在稳定 workflow 身份内启动父研究 workflow（§8.1）。

    ``SetWorkflowID`` 内调用同步 ``DBOS.start_workflow``；调用方须经受管线程执行
    （不堵 UI，R16）。``return-existing`` 使同身份重放（受理重传/重复派发）返回
    原执行，不重复启动研究。
    """

    if not isinstance(workflow_id, str) or not workflow_id:
        raise ValueError("workflow_id must be non-empty text")
    with SetWorkflowID(workflow_id, workflow_id_reuse_policy="return-existing"):
        DBOS.start_workflow(run_research_workflow, payload)


def build_research_payload(
    plan: PlanSpecProposal,
    *,
    execution_id: str,
    round_id: str,
    plan_digest: str,
    preserved: tuple[NodeOutcome, ...] = (),
    role_capabilities: Mapping[str, AbstractSet[str]] | None = None,
    input_version_digest: str | None = None,
    preserved_input_manifest: Mapping[str, str] | None = None,
    binding_identity: ParentBindIdentity | None = None,
) -> dict[str, Any]:
    """构造父 workflow 载荷（与保存的计划版同源内容；PORTABLE JSON 可序列化）。

    ``preserved`` 为重试轮冻结的旧轮成功前置事实（首轮为空）。空计划在派发前
    拒绝（F03：不制造空任务树；DBOS 侧无法给出可回执的准入拒绝）。
    ``role_capabilities`` 为本次准入的角色能力策略（写入载荷供工作流入口复核；
    缺省不写键，入口按无策略 fail-closed）。``input_version_digest`` 为输入版本
    指纹（缺省不写键，表示未核定，不作「仍适用」证据，R2-I09）。
    ``binding_identity`` 为派发侧冻结的应用轮次身份（缺省不写键，父零叶派发）。
    """

    if not plan.nodes:
        _fail("empty_plan", "$.nodes")
    _validate_preserved(preserved, scope_node_ids={node.node_id for node in plan.nodes})
    payload: dict[str, Any] = {
        _PAYLOAD_VERSION_KEY: PAYLOAD_VERSION,
        "execution_id": _require_str(execution_id, "$.execution_id"),
        "round_id": _require_str(round_id, "$.round_id"),
        "plan_digest": _require_digest(plan_digest, "$.plan_digest"),
        "nodes": [_node_to_wire(node) for node in plan.nodes],
        "preserved_outcomes": [outcome.to_wire() for outcome in preserved],
    }
    if role_capabilities is not None:
        payload["role_capabilities"] = _role_capabilities_to_wire(role_capabilities)
    if input_version_digest is not None:
        payload["input_version_digest"] = _require_digest(input_version_digest, "$.input_version_digest")
    if preserved_input_manifest is not None:
        payload["preserved_input_manifest"] = {
            _require_str(node_id, "$.preserved_input_manifest.node_id"): _require_digest(
                digest, f"$.preserved_input_manifest.{node_id}"
            )
            for node_id, digest in preserved_input_manifest.items()
        }
    if binding_identity is not None:
        payload["binding_identity"] = decode_binding_identity(
            binding_identity.to_wire(), "$.binding_identity"
        ).to_wire()
    return payload


def _role_capabilities_to_wire(policy: Mapping[str, AbstractSet[str]]) -> dict[str, list[str]]:
    """角色能力策略 → 载荷 wire（角色/能力排序固定，便于同内容重放比对）。"""

    wire: dict[str, list[str]] = {}
    for role_key in sorted(policy):
        capabilities = policy[role_key]
        if len(capabilities) != len(set(capabilities)):
            _fail("duplicate_capability", f"$.role_capabilities.{role_key}")
        wire[_require_str(role_key, "$.role_capabilities.role")] = sorted(
            _require_str(capability, f"$.role_capabilities.{role_key}") for capability in capabilities
        )
    return wire


def _validate_preserved(outcomes: tuple[NodeOutcome, ...], *, scope_node_ids: set[str]) -> None:
    """派发前校验保留前置：必须是成功事实且不在本轮范围内。

    与 :meth:`LeafDagScheduler.seed_preserved` 同组判据，但**前移到派发前**
    （HANDOFF §7.2：保存后才派发；非法载荷不得以 workflow ERROR 形式暴露）。
    """

    for outcome in outcomes:
        if outcome.state != "complete":
            _fail("invalid_preserved_outcome", f"$.preserved_outcomes[{outcome.node_id}]")
        if outcome.node_id in scope_node_ids:
            _fail("preserved_node_in_retry_scope", f"$.preserved_outcomes[{outcome.node_id}]")
    seen: set[str] = set()
    for outcome in outcomes:
        if outcome.node_id in seen:
            _fail("duplicate_preserved_outcome", f"$.preserved_outcomes[{outcome.node_id}]")
        seen.add(outcome.node_id)


# ---------------------------------------------------------------------------
# 失败重试范围（F04/F05：开新轮只重做目标及受影响下游，保旧成果与费用）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RetryScope:
    """重试新轮范围：``redo`` 按计划顺序含目标及传递下游；``preserved`` 为不在
    范围内、仍可复用的旧轮成功前置（或被保留的独立分支）。

    独立兄弟不在 ``redo`` 内（失败仅阻相关后继）；范围外旧成果与费用保持原样。
    """

    redo: tuple[str, ...]
    preserved: tuple[str, ...]


def compute_retry_scope(plan: PlanSpecProposal, *, targets: tuple[str, ...]) -> RetryScope:
    """计算重试范围：目标 → 传递下游（前置变更同理使下游失效）。

    Args:
        plan: 已校验的一层叶计划（原计划版，含全部节点）。
        targets: 需重做的节点身份（失败叶或前置输入变更的叶）。

    Raises:
        AgentValidationError: 目标为空、未知目标或重复目标（``unknown_node`` /
            ``invalid_targets``）。
    """

    if not targets:
        _fail("invalid_targets", "$.targets")
    if len(set(targets)) != len(targets):
        _fail("invalid_targets", "$.targets")
    order = tuple(node.node_id for node in plan.nodes)
    nodes = {node.node_id: node for node in plan.nodes}
    dependents: dict[str, list[str]] = {node_id: [] for node_id in order}
    for node in plan.nodes:
        for dependency in node.depends_on:
            dependents[dependency].append(node.node_id)

    affected: set[str] = set()
    stack = list(targets)
    while stack:
        node_id = stack.pop()
        if node_id not in nodes:
            _fail("unknown_node", f"$.targets[{node_id}]")
        if node_id in affected:
            continue
        affected.add(node_id)
        stack.extend(dependents[node_id])

    redo = tuple(node_id for node_id in order if node_id in affected)
    preserved = tuple(node_id for node_id in order if node_id not in affected)
    return RetryScope(redo=redo, preserved=preserved)


def build_retry_payload(
    plan: PlanSpecProposal,
    *,
    execution_id: str,
    round_id: str,
    plan_digest: str,
    targets: tuple[str, ...],
    preserved_outcomes: tuple[NodeOutcome, ...],
    role_capabilities: Mapping[str, AbstractSet[str]] | None = None,
    input_version_digest: str | None = None,
    binding_identity: ParentBindIdentity | None = None,
) -> tuple[dict[str, Any], RetryScope]:
    """构造重试轮父载荷：``nodes`` 为范围内节点，``preserved_outcomes`` 为旧轮
    成功前置。

    派发前校验（HANDOFF §7.2：保存后才派发）：保留前置须为成功事实且不得落在
    本轮范围内；范围外前置必须由 ``preserved_outcomes`` 全部覆盖。均显式拒绝
    （不以 workflow ERROR 暴露非法载荷、不静默不满足依赖）。

    输入版本已核定时额外核「仍适用」（R2-I09）：每项保留前置必须回报输入指纹且
    等于按当前计划重算的清单指纹，否则显式拒绝（``preserved_input_stale``）——
    不以陈旧前置构造新轮；未提供 ``input_version_digest`` 时推迟到工作流入口
    按清单判定（不适用即阻断下游）。

    Args:
        role_capabilities: 角色能力策略（随载荷冻结，供入口复核）。
        input_version_digest: 本次输入版本指纹（缺省 = 未核定）。
    """

    scope = compute_retry_scope(plan, targets=targets)
    redo_ids = set(scope.redo)
    _validate_preserved(preserved_outcomes, scope_node_ids=redo_ids)
    redo_nodes = tuple(node for node in plan.nodes if node.node_id in redo_ids)
    preserved_ids = {outcome.node_id for outcome in preserved_outcomes}
    for node in redo_nodes:
        for dependency in node.depends_on:
            if dependency not in redo_ids and dependency not in preserved_ids:
                _fail("missing_preserved_predecessor", f"$.nodes[{node.node_id}].depends_on")
    if input_version_digest is not None:
        full_manifest = compute_actual_input_manifest(plan, input_version_digest=input_version_digest)
        expected: dict[str, str] = {}
        for outcome in preserved_outcomes:
            expected_digest = full_manifest.digest_for(outcome.node_id)
            if expected_digest is None or outcome.input_digest != expected_digest:
                _fail("preserved_input_stale", f"$.preserved_outcomes[{outcome.node_id}]")
            expected[outcome.node_id] = expected_digest
    else:
        expected = {}
    payload = build_research_payload(
        PlanSpecProposal(nodes=redo_nodes),
        execution_id=execution_id,
        round_id=round_id,
        plan_digest=plan_digest,
        preserved=preserved_outcomes,
        role_capabilities=role_capabilities,
        input_version_digest=input_version_digest,
        preserved_input_manifest=expected or None,
        binding_identity=binding_identity,
    )
    return payload, scope


__all__ = [
    "ACTUAL_INPUT_BIND_STEP_NAME",
    "IDLE_WAIT_INTERVAL_SEC",
    "LEAF_EXECUTE_STEP_NAME",
    "LEAF_OBSERVE_STEP_NAME",
    "LEAF_TICKET_CLAIM_STEP_NAME",
    "LEAF_TICKET_RELEASE_STEP_NAME",
    "LEAF_WORKFLOW_NAME",
    "PAYLOAD_VERSION",
    "PLAN_ADMIT_STEP_NAME",
    "RESEARCH_TERMINAL_STEP_NAME",
    "RESEARCH_WORKFLOW_NAME",
    "ActualInputBinder",
    "ActualInputBinderRegistry",
    "ActualInputBinding",
    "ActualInputManifest",
    "LeafDagScheduler",
    "LeafExecutionContext",
    "LeafIdentity",
    "LeafObserve",
    "LeafRunner",
    "LeafRuntimeRegistry",
    "LeafStart",
    "LeafTicketClaim",
    "LeafTicketGate",
    "LeafTicketGateRegistry",
    "LeafTicketLease",
    "LeafTicketRelease",
    "LeafWaitCheckpoint",
    "NodeInputRecord",
    "NodeOutcome",
    "ParentBindIdentity",
    "PlanAdmission",
    "PlanRunSummary",
    "ResearchPayload",
    "RetryScope",
    "TerminalStateDaoRegistry",
    "admit_research_plan",
    "build_leaf_payload",
    "build_parent_bind_request",
    "build_research_payload",
    "build_retry_payload",
    "claim_leaf_ticket",
    "compute_actual_input_manifest",
    "compute_retry_scope",
    "decode_actual_input_binding",
    "decode_binding_identity",
    "decode_leaf_payload",
    "decode_node_outcome",
    "decode_plan_admission",
    "decode_research_payload",
    "decode_research_payload_full",
    "drive_leaf_dag",
    "execute_leaf_step",
    "install_terminal_state_dao",
    "leaf_ticket_slot_id",
    "leaf_workflow_id",
    "observe_leaf_children",
    "record_actual_input_binding",
    "record_plan_admission",
    "record_research_terminal_state",
    "release_leaf_ticket",
    "run_leaf_workflow",
    "run_research_workflow",
    "submit_research_workflow",
    "terminal_display_state_from_summary",
    "validate_actual_input_binding",
    "wait_next_observation_window",
]
