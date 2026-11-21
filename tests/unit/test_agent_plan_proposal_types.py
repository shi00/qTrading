"""计划提案 wire 契约（DD §8 F21 / §10.3；HANDOFF §7.3，E-04-1）。

设计正本：Harness ``PlanStore`` 的提案以不可变 asset 版本保存，绑定 execution
与 plan_revision；本模块只做结构 codec 与摘要（无 IO），坏结构一律以
:class:`AgentValidationError` 携带稳定错误码与字段路径。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.agent_types import (
    AgentValidationError,
    PLAN_ITEM_STATUSES,
    PLAN_STORE_VERSION,
    PlanItemSnapshot,
    PlanProposal,
    decode_json_doc,
    decode_plan_item_snapshot,
    decode_plan_proposal,
    decode_plan_proposal_document,
    normalize_plan_proposal,
    plan_asset_id,
    plan_proposal_digest,
)

pytestmark = pytest.mark.unit


def _item(
    item_id: str = "a1b2c3d4",
    *,
    content: str = "收集来源",
    status: str = "pending",
    active_form: str = "正在收集来源",
    parent_id: str | None = None,
    depends_on: tuple[str, ...] = (),
) -> PlanItemSnapshot:
    return PlanItemSnapshot(
        id=item_id,
        content=content,
        status=status,
        active_form=active_form,
        parent_id=parent_id,
        depends_on=depends_on,
    )


def _proposal(
    *,
    execution_id: str = "exec-1",
    plan_revision: int = 1,
    items: tuple[PlanItemSnapshot, ...] | None = None,
) -> PlanProposal:
    return PlanProposal(
        execution_id=execution_id,
        plan_revision=plan_revision,
        items=(_item(),) if items is None else items,
    )


def _wire(**overrides: object) -> dict[str, object]:
    doc: dict[str, object] = {
        "plan_store_version": PLAN_STORE_VERSION,
        "plan_revision": 1,
        "items": [
            {
                "id": "a1b2c3d4",
                "content": "收集来源",
                "status": "pending",
                "active_form": "正在收集来源",
                "parent_id": None,
                "depends_on": [],
            }
        ],
    }
    doc.update(overrides)
    return doc


def _assert_code(error: pytest.ExceptionInfo[AgentValidationError], code: str) -> None:
    assert error.value.code == code


# ---------------------------------------------------------------------------
# 合法往返
# ---------------------------------------------------------------------------


def test_minimal_proposal_roundtrip() -> None:
    proposal = _proposal()
    document = normalize_plan_proposal(proposal)
    assert decode_plan_proposal_document(document, execution_id="exec-1") == proposal


def test_document_is_canonical_json() -> None:
    document = normalize_plan_proposal(_proposal())
    assert " " not in document
    assert document.startswith('{"items":')
    assert decode_json_doc(document) is not None


def test_item_carries_all_six_keys() -> None:
    snapshot = decode_plan_item_snapshot(
        {
            "id": "b",
            "content": "c",
            "status": "blocked",
            "active_form": "f",
            "parent_id": "a",
            "depends_on": ["a"],
        }
    )
    assert snapshot == _item("b", content="c", status="blocked", active_form="f", parent_id="a", depends_on=("a",))


def test_empty_items_allowed() -> None:
    proposal = _proposal(items=())
    assert decode_plan_proposal_document(normalize_plan_proposal(proposal), execution_id="exec-1") == proposal


def test_item_order_is_identity() -> None:
    first = _proposal(items=(_item("a"), _item("b")))
    second = _proposal(items=(_item("b"), _item("a")))
    assert normalize_plan_proposal(first) != normalize_plan_proposal(second)
    assert plan_proposal_digest(first) != plan_proposal_digest(second)


def test_digest_binds_revision() -> None:
    assert plan_proposal_digest(_proposal(plan_revision=1)) != plan_proposal_digest(_proposal(plan_revision=2))


def test_digest_is_stable_across_roundtrip() -> None:
    proposal = _proposal()
    reread = decode_plan_proposal_document(normalize_plan_proposal(proposal), execution_id="exec-1")
    assert plan_proposal_digest(reread) == plan_proposal_digest(proposal)


def test_plan_asset_id_is_derived_from_execution() -> None:
    assert plan_asset_id("exec-1") == "plan:exec-1"
    with pytest.raises(AgentValidationError) as error:
        plan_asset_id("")
    _assert_code(error, "invalid_identity")


def test_frozen_statuses_match_harness_values() -> None:
    assert frozenset({"pending", "in_progress", "completed", "cancelled", "blocked"}) == PLAN_ITEM_STATUSES


# ---------------------------------------------------------------------------
# 文档级拒绝
# ---------------------------------------------------------------------------


def test_document_missing_key_rejected() -> None:
    doc = _wire()
    del doc["items"]
    with pytest.raises(AgentValidationError) as error:
        decode_plan_proposal(doc, execution_id="exec-1")
    _assert_code(error, "missing_field")


def test_document_extra_key_rejected() -> None:
    with pytest.raises(AgentValidationError) as error:
        decode_plan_proposal(_wire(extra=1), execution_id="exec-1")
    _assert_code(error, "unknown_field")


def test_document_bool_version_rejected() -> None:
    with pytest.raises(AgentValidationError) as error:
        decode_plan_proposal(_wire(plan_store_version=True), execution_id="exec-1")
    _assert_code(error, "unsupported_version")


@pytest.mark.parametrize("version", [True, 1.0, Decimal("1.0"), "1"])
def test_document_non_integer_version_rejected(version: object) -> None:
    """结构版本拒 bool 与一切非整数输入（``True``/``1.0`` 与 1 等值但类型不符）。"""
    with pytest.raises(AgentValidationError) as error:
        decode_plan_proposal(_wire(plan_store_version=version), execution_id="exec-1")
    _assert_code(error, "unsupported_version")
    assert error.value.field_path == "$.plan_proposal.plan_store_version"


def test_document_integer_version_accepted() -> None:
    """显式整数 1 接受（规范化保存形态可再解码）。"""
    proposal = decode_plan_proposal(_wire(plan_store_version=1), execution_id="exec-1")
    assert proposal.execution_id == "exec-1"


def test_document_missing_version_rejected() -> None:
    """版本键必填（无缺省值）：缺失即 missing_field，不静默按 1 处理。"""
    doc = _wire()
    del doc["plan_store_version"]
    with pytest.raises(AgentValidationError) as error:
        decode_plan_proposal(doc, execution_id="exec-1")
    _assert_code(error, "missing_field")


def test_document_unknown_version_rejected() -> None:
    with pytest.raises(AgentValidationError) as error:
        decode_plan_proposal(_wire(plan_store_version=2), execution_id="exec-1")
    _assert_code(error, "unsupported_version")


def test_document_bool_revision_rejected() -> None:
    with pytest.raises(AgentValidationError) as error:
        decode_plan_proposal(_wire(plan_revision=True), execution_id="exec-1")
    _assert_code(error, "invalid_type")


def test_document_zero_revision_rejected() -> None:
    with pytest.raises(AgentValidationError) as error:
        decode_plan_proposal(_wire(plan_revision=0), execution_id="exec-1")
    _assert_code(error, "invalid_range")


def test_document_items_not_list_rejected() -> None:
    with pytest.raises(AgentValidationError) as error:
        decode_plan_proposal(_wire(items="a"), execution_id="exec-1")
    _assert_code(error, "invalid_type")


def test_document_requires_execution_identity() -> None:
    with pytest.raises(AgentValidationError) as error:
        decode_plan_proposal(_wire(), execution_id="")
    _assert_code(error, "invalid_identity")


def test_document_duplicate_item_id_rejected() -> None:
    doc = _wire()
    items = doc["items"]
    assert isinstance(items, list)
    items.append(dict(items[0]))
    with pytest.raises(AgentValidationError) as error:
        decode_plan_proposal(doc, execution_id="exec-1")
    _assert_code(error, "duplicate_plan_item")


# ---------------------------------------------------------------------------
# 步骤级拒绝
# ---------------------------------------------------------------------------


def _item_wire(**overrides: object) -> dict[str, object]:
    item: dict[str, object] = {
        "id": "a",
        "content": "c",
        "status": "pending",
        "active_form": "",
        "parent_id": None,
        "depends_on": [],
    }
    item.update(overrides)
    return item


def test_item_missing_key_rejected() -> None:
    item = _item_wire()
    del item["parent_id"]
    with pytest.raises(AgentValidationError) as error:
        decode_plan_item_snapshot(item)
    _assert_code(error, "missing_field")


def test_item_extra_key_rejected() -> None:
    with pytest.raises(AgentValidationError) as error:
        decode_plan_item_snapshot(_item_wire(extra="x"))
    _assert_code(error, "unknown_field")


def test_item_unknown_status_rejected() -> None:
    with pytest.raises(AgentValidationError) as error:
        decode_plan_item_snapshot(_item_wire(status="done"))
    _assert_code(error, "invalid_enum")


@pytest.mark.parametrize("status", sorted(PLAN_ITEM_STATUSES))
def test_item_each_frozen_status_accepted(status: str) -> None:
    assert decode_plan_item_snapshot(_item_wire(status=status)).status == status


def test_item_empty_id_rejected() -> None:
    with pytest.raises(AgentValidationError) as error:
        decode_plan_item_snapshot(_item_wire(id=""))
    _assert_code(error, "invalid_identity")


def test_item_content_must_be_text() -> None:
    with pytest.raises(AgentValidationError) as error:
        decode_plan_item_snapshot(_item_wire(content=None))
    _assert_code(error, "invalid_type")


def test_item_active_form_must_be_text() -> None:
    with pytest.raises(AgentValidationError) as error:
        decode_plan_item_snapshot(_item_wire(active_form=1))
    _assert_code(error, "invalid_type")


def test_item_parent_id_must_be_text_or_null() -> None:
    with pytest.raises(AgentValidationError) as error:
        decode_plan_item_snapshot(_item_wire(parent_id=1))
    _assert_code(error, "invalid_identity")


def test_item_depends_on_must_be_list() -> None:
    with pytest.raises(AgentValidationError) as error:
        decode_plan_item_snapshot(_item_wire(depends_on="a"))
    _assert_code(error, "invalid_type")


def test_item_depends_on_entries_must_be_text() -> None:
    with pytest.raises(AgentValidationError) as error:
        decode_plan_item_snapshot(_item_wire(depends_on=[1]))
    _assert_code(error, "invalid_identity")


# ---------------------------------------------------------------------------
# 内存对象写入前校验
# ---------------------------------------------------------------------------


def test_normalize_rejects_bool_revision() -> None:
    with pytest.raises(AgentValidationError) as error:
        normalize_plan_proposal(_proposal(plan_revision=True))  # type: ignore[arg-type]  # 运行期越界形态须被拒绝
    _assert_code(error, "invalid_type")


def test_normalize_rejects_unknown_status() -> None:
    with pytest.raises(AgentValidationError) as error:
        normalize_plan_proposal(_proposal(items=(_item(status="done"),)))
    _assert_code(error, "invalid_enum")


def test_normalize_rejects_duplicate_ids() -> None:
    with pytest.raises(AgentValidationError) as error:
        normalize_plan_proposal(_proposal(items=(_item("a"), _item("a"))))
    _assert_code(error, "duplicate_plan_item")


def test_normalize_rejects_list_like_items() -> None:
    with pytest.raises(AgentValidationError) as error:
        normalize_plan_proposal(
            PlanProposal(execution_id="exec-1", plan_revision=1, items=[_item()]),  # type: ignore[arg-type]  # 越界形态
        )
    _assert_code(error, "invalid_type")
