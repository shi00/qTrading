"""PlanStore 六方法适配（DD §10.3；HANDOFF §7.3，E-04-3）。

以假 DAO 验证适配语义：读固定绑定版、写以预期版 CAS 推进、冲突原样上抛、
重复/缺失操作零 DB 写、返回值为脱离副本。真实持久化由
``tests/integration/test_agent_dao_plan_proposal.py`` 覆盖。
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from pydantic_ai_harness.planning import PlanItem, TaskStatus

from core.agent_types import AgentErrorCode, AgentServiceError, PlanItemSnapshot, PlanProposal
from services.agent_service.capabilities import PlanProposalStore, PlanStoreBinding, build_plan_store

pytestmark = pytest.mark.unit


class FakePlanDao:
    """最小 CAS 语义替身：head 匹配才追加新版本。"""

    def __init__(self) -> None:
        self.rows: dict[int, PlanProposal] = {}
        self.reads: list[int] = []
        self.writes: list[tuple[int, tuple[PlanItemSnapshot, ...]]] = []
        self.fail_with: Exception | None = None

    async def read_plan_proposal(
        self, *, user_scope: str, execution_id: str, plan_revision: int
    ) -> PlanProposal | None:
        self.reads.append(plan_revision)
        return self.rows.get(plan_revision)

    async def save_plan_proposal(
        self,
        *,
        user_scope: str,
        execution_id: str,
        expected_revision: int,
        items: Sequence[PlanItemSnapshot],
    ) -> PlanProposal:
        if self.fail_with is not None:
            raise self.fail_with
        self.writes.append((expected_revision, tuple(items)))
        head = max(self.rows) if self.rows else 0
        if head != expected_revision:
            raise AgentServiceError(AgentErrorCode.REVISION_CONFLICT, "plan revision conflict", "fake")
        existing = self.rows.get(expected_revision)
        if existing is not None and existing.items == tuple(items):
            return existing
        proposal = PlanProposal(execution_id=execution_id, plan_revision=expected_revision + 1, items=tuple(items))
        self.rows[proposal.plan_revision] = proposal
        return proposal


def _item(item_id: str = "a1", *, content: str = "第一步", status: TaskStatus = TaskStatus.pending) -> PlanItem:
    return PlanItem(id=item_id, content=content, status=status, active_form="")


def _store(dao: FakePlanDao, *, revision: int = 0) -> PlanProposalStore:
    return build_plan_store(dao, PlanStoreBinding(user_scope="u1", execution_id="exec-1", plan_revision=revision))


class TestReadPaths:
    @pytest.mark.asyncio
    async def test_empty_binding_reads_without_dao(self) -> None:
        dao = FakePlanDao()
        store = _store(dao)
        assert await store.get_items() == []
        assert dao.reads == []
        assert store.revision == 0

    @pytest.mark.asyncio
    async def test_read_is_pinned_to_bound_revision(self) -> None:
        dao = FakePlanDao()
        dao.rows[1] = PlanProposal("exec-1", 1, (PlanItemSnapshot("a1", "旧版", "pending", "", None, ()),))
        dao.rows[2] = PlanProposal("exec-1", 2, (PlanItemSnapshot("a1", "新版", "pending", "", None, ()),))
        store = _store(dao, revision=1)

        assert [item.content for item in await store.get_items()] == ["旧版"]
        assert dao.reads == [1]

    @pytest.mark.asyncio
    async def test_get_items_returns_detached_copies(self) -> None:
        dao = FakePlanDao()
        store = _store(dao)
        await store.set_items([_item()])

        first = await store.get_items()
        first[0].content = "被外部改写"
        assert [item.content for item in await store.get_items()] == ["第一步"]

    @pytest.mark.asyncio
    async def test_get_item_found_and_missing(self) -> None:
        dao = FakePlanDao()
        store = _store(dao)
        await store.set_items([_item(), _item("b2", content="第二步")])

        found = await store.get_item("b2")
        assert found is not None
        assert (found.id, found.content) == ("b2", "第二步")
        assert await store.get_item("missing") is None


class TestWritePaths:
    @pytest.mark.asyncio
    async def test_set_items_advances_revision(self) -> None:
        dao = FakePlanDao()
        store = _store(dao)
        await store.set_items([_item()])

        assert store.revision == 1
        assert dao.writes == [(0, (PlanItemSnapshot("a1", "第一步", "pending", "", None, ()),))]
        assert [item.id for item in await store.get_items()] == ["a1"]

    @pytest.mark.asyncio
    async def test_set_items_empty_is_legal(self) -> None:
        dao = FakePlanDao()
        store = _store(dao)
        await store.set_items([])

        assert store.revision == 1
        assert await store.get_items() == []

    @pytest.mark.asyncio
    async def test_add_item_appends(self) -> None:
        dao = FakePlanDao()
        store = _store(dao)
        await store.set_items([_item()])
        added = await store.add_item(_item("b2", content="第二步", status=TaskStatus.in_progress))

        assert added.id == "b2"
        assert [item.id for item in await store.get_items()] == ["a1", "b2"]
        assert store.revision == 2

    @pytest.mark.asyncio
    async def test_add_duplicate_id_is_value_error_without_write(self) -> None:
        dao = FakePlanDao()
        store = _store(dao)
        await store.set_items([_item()])
        writes_before = list(dao.writes)

        with pytest.raises(ValueError) as error:
            await store.add_item(_item())
        assert "already in this plan" in str(error.value)
        assert dao.writes == writes_before
        assert store.revision == 1

    @pytest.mark.asyncio
    async def test_update_only_non_none_fields(self) -> None:
        dao = FakePlanDao()
        store = _store(dao)
        await store.set_items([_item("a1", status=TaskStatus.in_progress)])

        updated = await store.update_item("a1", content="改名")

        assert updated is not None
        assert updated.content == "改名"
        assert updated.status is TaskStatus.in_progress
        stored = await store.get_item("a1")
        assert stored is not None
        assert (stored.content, stored.status) == ("改名", TaskStatus.in_progress)

    @pytest.mark.asyncio
    async def test_update_can_set_status_and_dependencies(self) -> None:
        dao = FakePlanDao()
        store = _store(dao)
        await store.set_items([_item("a1"), _item("b2")])

        updated = await store.update_item("b2", status=TaskStatus.blocked, active_form="等待上游", depends_on=["a1"])

        assert updated is not None
        assert updated.status is TaskStatus.blocked
        assert updated.active_form == "等待上游"
        assert updated.depends_on == ["a1"]

    @pytest.mark.asyncio
    async def test_update_missing_item_is_none_without_write(self) -> None:
        dao = FakePlanDao()
        store = _store(dao)
        await store.set_items([_item()])
        writes_before = list(dao.writes)

        assert await store.update_item("missing", content="x") is None
        assert dao.writes == writes_before

    @pytest.mark.asyncio
    async def test_remove_item(self) -> None:
        dao = FakePlanDao()
        store = _store(dao)
        await store.set_items([_item("a1"), _item("b2")])

        assert await store.remove_item("a1") is True
        assert [item.id for item in await store.get_items()] == ["b2"]
        assert await store.remove_item("a1") is False
        assert store.revision == 2

    @pytest.mark.asyncio
    async def test_noop_rewrite_keeps_revision(self) -> None:
        dao = FakePlanDao()
        store = _store(dao)
        await store.set_items([_item()])

        await store.set_items([_item()])

        assert store.revision == 1

    @pytest.mark.asyncio
    async def test_conflict_propagates_and_revision_stays(self) -> None:
        dao = FakePlanDao()
        store = _store(dao)
        dao.fail_with = AgentServiceError(AgentErrorCode.REVISION_CONFLICT, "plan revision conflict", "fake")

        with pytest.raises(AgentServiceError) as error:
            await store.set_items([_item()])
        assert error.value.code is AgentErrorCode.REVISION_CONFLICT
        assert store.revision == 0

    @pytest.mark.asyncio
    async def test_non_plan_item_rejected_before_write(self) -> None:
        dao = FakePlanDao()
        store = _store(dao)

        with pytest.raises(ValueError) as error:
            await store.set_items([{"id": "x"}])  # type: ignore[list-item]  # 越界形态须被拒绝
        assert "PlanItem" in str(error.value)
        assert dao.writes == []


class TestBinding:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"user_scope": "", "execution_id": "exec-1"},
            {"user_scope": "u1", "execution_id": ""},
            {"user_scope": "u1", "execution_id": "exec-1", "plan_revision": -1},
            {"user_scope": "u1", "execution_id": "exec-1", "plan_revision": True},
        ],
    )
    def test_invalid_binding_rejected(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(ValueError) as error:
            PlanStoreBinding(**kwargs)  # type: ignore[arg-type]  # 越界形态须被拒绝
        assert "plan store binding requires" in str(error.value)

    @pytest.mark.asyncio
    async def test_binding_is_exposed_read_only(self) -> None:
        dao = FakePlanDao()
        store = _store(dao, revision=3)
        assert store.binding == PlanStoreBinding(user_scope="u1", execution_id="exec-1", plan_revision=3)
        assert store.revision == 3
