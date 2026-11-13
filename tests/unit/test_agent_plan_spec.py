"""PlanSpec 提案程序校验（DD §8 F04 / SQ-04 / HANDOFF §7.2，D-02-1）。

设计正本：模型提交 PlanSpec 提案后，程序在派发前核「无环、自依赖、缺引用、
重复、角色能力、最多 20 叶、一层」；保存计划版与原生引用后才派发。本模块校验
纯数据结构（无 IO），失败一律以 :class:`AgentValidationError` 携带稳定错误码与
字段路径（服务边界负责转译，core 不日志不脱敏）。
"""

from __future__ import annotations

import pytest

from core.agent_types import (
    AgentValidationError,
    MAX_PLAN_LEAVES,
    PlanNodeSpec,
    PlanSpecProposal,
    SourceKind,
    SourceVersionRef,
    decode_json_doc,
    decode_plan_spec_proposal,
    normalize_plan_spec_proposal,
    plan_spec_digest,
    validate_plan_spec_proposal,
)

pytestmark = pytest.mark.unit

_RESEARCHER_CAPS = frozenset({"source.read", "web.search"})
_ANALYST_CAPS = frozenset({"compute"})
_ALLOWED: dict[str, frozenset[str]] = {
    "researcher": _RESEARCHER_CAPS,
    "analyst": _ANALYST_CAPS,
}


def _node(
    node_id: str,
    *,
    depends_on: tuple[str, ...] = (),
    capabilities: tuple[str, ...] = ("source.read",),
    role_key: str = "researcher",
    target: str = "研究目标",
) -> PlanNodeSpec:
    return PlanNodeSpec(
        node_id=node_id,
        target=target,
        role_key=role_key,
        depends_on=depends_on,
        capabilities=capabilities,
    )


def _source_ref(source_id: str, version: str = "v1") -> SourceVersionRef:
    return SourceVersionRef(
        source_kind=SourceKind.ATTACHMENT,
        source_id=source_id,
        source_version=version,
        source_digest="a" * 64,
    )


# ---------------------------------------------------------------------------
# 合法图（SQ-04：独立 A/B 前置 → C）
# ---------------------------------------------------------------------------


def test_linear_chain_accepted() -> None:
    """线性依赖链合法（A→B→C）。"""
    plan = PlanSpecProposal(nodes=(_node("a"), _node("b", depends_on=("a",)), _node("c", depends_on=("b",))))
    validate_plan_spec_proposal(plan, allowed_role_capabilities=_ALLOWED)


def test_fan_out_fan_in_accepted() -> None:
    """SQ-04 形态：独立 A/B 并行，C 依赖二者（一层 DAG）。"""
    plan = PlanSpecProposal(
        nodes=(
            _node("a"),
            _node("b"),
            _node("c", depends_on=("a", "b")),
        )
    )
    validate_plan_spec_proposal(plan, allowed_role_capabilities=_ALLOWED)


def test_max_leaves_boundary_accepted() -> None:
    """恰好 20 叶合法（上限恰等，不外扩）。"""
    plan = PlanSpecProposal(nodes=tuple(_node(f"n{i}") for i in range(MAX_PLAN_LEAVES)))
    validate_plan_spec_proposal(plan, allowed_role_capabilities=_ALLOWED)


# ---------------------------------------------------------------------------
# 结构性拒绝
# ---------------------------------------------------------------------------


def test_empty_plan_rejected() -> None:
    """空任务树拒绝（F03：无工具/分工时不制造空任务树）。"""
    with pytest.raises(AgentValidationError) as excinfo:
        validate_plan_spec_proposal(PlanSpecProposal(nodes=()), allowed_role_capabilities=_ALLOWED)
    assert excinfo.value.code == "empty_plan"


def test_too_many_leaves_rejected() -> None:
    """超过 20 叶拒绝（上限超一）。"""
    plan = PlanSpecProposal(nodes=tuple(_node(f"n{i}") for i in range(MAX_PLAN_LEAVES + 1)))
    with pytest.raises(AgentValidationError) as excinfo:
        validate_plan_spec_proposal(plan, allowed_role_capabilities=_ALLOWED)
    assert excinfo.value.code == "too_many_leaves"


def test_duplicate_node_id_rejected() -> None:
    """重复 node_id 拒绝（同名节点无法按身份定位）。"""
    plan = PlanSpecProposal(nodes=(_node("a"), _node("a")))
    with pytest.raises(AgentValidationError) as excinfo:
        validate_plan_spec_proposal(plan, allowed_role_capabilities=_ALLOWED)
    assert excinfo.value.code == "duplicate_node"
    assert excinfo.value.field_path == "$.nodes[1].node_id"


def test_self_dependency_rejected() -> None:
    """自依赖拒绝。"""
    plan = PlanSpecProposal(nodes=(_node("a", depends_on=("a",)),))
    with pytest.raises(AgentValidationError) as excinfo:
        validate_plan_spec_proposal(plan, allowed_role_capabilities=_ALLOWED)
    assert excinfo.value.code == "self_dependency"
    assert excinfo.value.field_path == "$.nodes[0].depends_on[0]"


def test_missing_dependency_reference_rejected() -> None:
    """缺引用拒绝（依赖了不存在的节点）。"""
    plan = PlanSpecProposal(nodes=(_node("a", depends_on=("ghost",)),))
    with pytest.raises(AgentValidationError) as excinfo:
        validate_plan_spec_proposal(plan, allowed_role_capabilities=_ALLOWED)
    assert excinfo.value.code == "missing_reference"
    assert excinfo.value.field_path == "$.nodes[0].depends_on[0]"


def test_cycle_rejected() -> None:
    """环拒绝（A→B→C→A）；非法图零执行。"""
    plan = PlanSpecProposal(
        nodes=(
            _node("a", depends_on=("c",)),
            _node("b", depends_on=("a",)),
            _node("c", depends_on=("b",)),
        )
    )
    with pytest.raises(AgentValidationError) as excinfo:
        validate_plan_spec_proposal(plan, allowed_role_capabilities=_ALLOWED)
    assert excinfo.value.code == "plan_cycle"


def test_duplicate_dependency_entry_rejected() -> None:
    """同一依赖重复列出视为重复（不静默去重）。"""
    plan = PlanSpecProposal(nodes=(_node("a"), _node("b", depends_on=("a", "a"))))
    with pytest.raises(AgentValidationError) as excinfo:
        validate_plan_spec_proposal(plan, allowed_role_capabilities=_ALLOWED)
    assert excinfo.value.code == "duplicate_dependency"


def test_blank_node_fields_rejected() -> None:
    """节点身份/目标/角色为空串拒绝（正文原样保留但不接受空身份）。"""
    plan = PlanSpecProposal(nodes=(_node("a", target=""),))
    with pytest.raises(AgentValidationError) as excinfo:
        validate_plan_spec_proposal(plan, allowed_role_capabilities=_ALLOWED)
    assert excinfo.value.code == "invalid_text"
    assert excinfo.value.field_path == "$.nodes[0].target"


# ---------------------------------------------------------------------------
# 角色能力
# ---------------------------------------------------------------------------


def test_unknown_role_rejected() -> None:
    """角色不在本次允许集合内拒绝（角色能力交集核验）。"""
    plan = PlanSpecProposal(nodes=(_node("a", role_key="unknown-role"),))
    with pytest.raises(AgentValidationError) as excinfo:
        validate_plan_spec_proposal(plan, allowed_role_capabilities=_ALLOWED)
    assert excinfo.value.code == "unknown_role"
    assert excinfo.value.field_path == "$.nodes[0].role_key"


def test_capability_outside_role_rejected() -> None:
    """能力超出该角色声明集合拒绝（模型提案不授自身权限）。"""
    plan = PlanSpecProposal(nodes=(_node("a", capabilities=("source.read", "shell.exec")),))
    with pytest.raises(AgentValidationError) as excinfo:
        validate_plan_spec_proposal(plan, allowed_role_capabilities=_ALLOWED)
    assert excinfo.value.code == "capability_not_allowed"
    assert excinfo.value.field_path == "$.nodes[0].capabilities[1]"


def test_duplicate_capability_entry_rejected() -> None:
    """重复能力项拒绝。"""
    plan = PlanSpecProposal(nodes=(_node("a", capabilities=("source.read", "source.read")),))
    with pytest.raises(AgentValidationError) as excinfo:
        validate_plan_spec_proposal(plan, allowed_role_capabilities=_ALLOWED)
    assert excinfo.value.code == "duplicate_capability"


def test_duplicate_source_ref_rejected() -> None:
    """同一源版（kind+id+version）重复引用拒绝。"""
    plan = PlanSpecProposal(
        nodes=(
            PlanNodeSpec(
                node_id="a",
                target="目标",
                role_key="researcher",
                capabilities=("source.read",),
                source_refs=(_source_ref("f1"), _source_ref("f1")),
            ),
        )
    )
    with pytest.raises(AgentValidationError) as excinfo:
        validate_plan_spec_proposal(plan, allowed_role_capabilities=_ALLOWED)
    assert excinfo.value.code == "duplicate_source_ref"


# ---------------------------------------------------------------------------
# 规范化与摘要（保存计划版用）
# ---------------------------------------------------------------------------


def test_normalize_preserves_target_verbatim() -> None:
    """正文原样保留：不 trim、不改 Unicode（摘要不含显示缓存）。"""
    target = "  研究 600519\t贵州茅台  "
    plan = PlanSpecProposal(nodes=(_node("a", target=target),))
    normalized = normalize_plan_spec_proposal(plan)
    assert '"target":"  研究 600519\\t贵州茅台  "' in normalized


def test_digest_stable_and_content_sensitive() -> None:
    """同内容同摘要；目标变化摘要随之变化（计划版身份可核）。"""
    plan_one = PlanSpecProposal(nodes=(_node("a"), _node("b", depends_on=("a",))))
    plan_two = PlanSpecProposal(nodes=(_node("a"), _node("b", depends_on=("a",))))
    changed = PlanSpecProposal(nodes=(_node("a", target="另一目标"), _node("b", depends_on=("a",))))
    assert plan_spec_digest(plan_one) == plan_spec_digest(plan_two)
    assert plan_spec_digest(plan_one) != plan_spec_digest(changed)
    assert len(plan_spec_digest(plan_one)) == 64


# ---------------------------------------------------------------------------
# 封闭 wire 编解码（结构版本 1，全键必填禁额外键）
# ---------------------------------------------------------------------------


def _wire_node(node_id: str, **overrides: object) -> dict[str, object]:
    wire: dict[str, object] = {
        "node_id": node_id,
        "target": "研究目标",
        "role_key": "researcher",
        "depends_on": [],
        "capabilities": ["source.read"],
        "source_refs": [],
    }
    wire.update(overrides)
    return wire


def test_decode_roundtrip() -> None:
    """解码 → 校验 → 规范化 → 再解码语义一致。"""
    wire = {
        "nodes": [
            _wire_node("a"),
            _wire_node("b", depends_on=["a"], capabilities=["source.read", "web.search"]),
        ]
    }
    plan = decode_plan_spec_proposal(wire)
    validate_plan_spec_proposal(plan, allowed_role_capabilities=_ALLOWED)
    normalized = normalize_plan_spec_proposal(plan)
    again = decode_plan_spec_proposal(decode_json_doc(normalized))
    assert plan_spec_digest(plan) == plan_spec_digest(again)


def test_decode_rejects_extra_key() -> None:
    """节点额外键拒绝（不静默忽略未冻结合同字段）。"""
    wire = {"nodes": [_wire_node("a", note="extra")]}
    with pytest.raises(AgentValidationError) as excinfo:
        decode_plan_spec_proposal(wire)
    assert excinfo.value.code == "unknown_field"


def test_decode_rejects_missing_key() -> None:
    """节点缺键拒绝（全键必填）。"""
    node = _wire_node("a")
    del node["capabilities"]
    with pytest.raises(AgentValidationError) as excinfo:
        decode_plan_spec_proposal({"nodes": [node]})
    assert excinfo.value.code == "missing_field"


def test_decode_rejects_non_list_containers() -> None:
    """容器类型错误拒绝（tuple 之外的裸标量不走隐式转换）。"""
    with pytest.raises(AgentValidationError) as excinfo:
        decode_plan_spec_proposal({"nodes": "a"})
    assert excinfo.value.code == "invalid_type"
