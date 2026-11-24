"""队列修订的来源成员登记契约测试（N24）。

修订只改正文时，仍选资料必须随新输入版登记执行成员边；否则精确读取按新输入版
（``consumer_version=N``）核成员会拒绝仍选资料。移除项不在新 manifest 中，不得
借旧版边读取；旧版历史（v1 边）不删不改。

真实事务原子性与 PG 唯一约束由集成测试覆盖；本文件用 capture conn 断言
manifest 解析、成员边插入值与 fail-closed 拒绝分支。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    ReviseQueuedRequest,
    SourceKind,
    SourceVersionRef,
    encode_source_version_ref,
    json_doc,
)
from data.persistence.daos.agent_dao import AgentDao, _revised_input_source_refs

pytestmark = pytest.mark.unit

_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_DIGEST_C = "c" * 64


def _ref(
    source_id: str,
    *,
    kind: SourceKind = SourceKind.ATTACHMENT,
    version: str = "v1",
    digest: str = _DIGEST_A,
) -> SourceVersionRef:
    return SourceVersionRef(source_kind=kind, source_id=source_id, source_version=version, source_digest=digest)


def _manifest(refs: list[SourceVersionRef], *, shared: bool = False) -> str:
    section: dict[str, Any] = {
        "schema_version": 1,
        "text": "改为研究两家公司",
        "mode": "discuss",
        "purpose": "concept",
        "cutoff": None,
        "source_refs": [encode_source_version_ref(ref) for ref in refs],
    }
    document = {"schema_version": 1, "shared": section} if shared else section
    return json_doc(document)


def _param(statement: Any, name: str) -> Any:
    """取编译参数中列 ``name`` 的绑定值（SQLAlchemy 生成 ``name_1`` 形式键）。"""

    for key, value in statement.compile().params.items():
        if key == name or key.startswith(f"{name}_"):
            return value
    return None


class _Result:
    def __init__(self, *, first: Any = None, rows: list[Any] | None = None, rowcount: int = 1) -> None:
        self._first = first
        self._rows = [] if rows is None else list(rows)
        self.rowcount = rowcount

    def first(self) -> Any:
        return self._first

    def all(self) -> list[Any]:
        return self._rows

    def scalars(self) -> _Result:
        return self

    def scalar_one(self) -> Any:
        return self._rows[0] if self._rows else 0


class _ReviseConn:
    """按表分派修订事务所需的最小行事实，并记录源边插入的编译参数。"""

    def __init__(
        self,
        *,
        execution_edges: dict[str, tuple[str, ...]] | None = None,
        asset_digests: dict[str, str] | None = None,
    ) -> None:
        self.execution_edges = {source_id: tuple(versions) for source_id, versions in (execution_edges or {}).items()}
        self.asset_digests = dict(asset_digests or {})
        self.source_link_inserts: list[dict[str, Any]] = []

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = str(statement)
        if sql.startswith("INSERT INTO agent_source_link"):
            self.source_link_inserts.append(dict(statement.compile().params))
            return _Result()
        if "max(agent_message.session_seq)" in sql:
            return _Result(rows=[0])
        if "max(agent_command.input_revision)" in sql:
            return _Result(rows=[1])
        if "FROM agent_source_link" in sql:
            source_id = _param(statement, "source_id")
            versions = self.execution_edges.get(str(source_id), ())
            return _Result(
                rows=[SimpleNamespace(consumer_version=version, locator_payload=None) for version in versions]
            )
        if "FROM agent_asset_version" in sql:
            asset_id = str(_param(statement, "asset_id"))
            digest = self.asset_digests.get(asset_id)
            return _Result(first=None if digest is None else SimpleNamespace(digest=digest))
        if "FROM agent_execution" in sql and "FOR UPDATE" in sql:
            return _Result(
                first=SimpleNamespace(session_id="s1", revision=1, display_state="queued", current_round_id=None)
            )
        if "FROM agent_round" in sql:
            return _Result(first=None)
        if "FROM agent_session" in sql and "FOR UPDATE" in sql:
            return _Result(first=SimpleNamespace(session_id="s1", archived=False))
        if "FROM agent_cleanup_job" in sql:
            return _Result(rows=[])
        if "FROM agent_command" in sql:
            return _Result(first=None)
        return _Result()


def _dao(conn: _ReviseConn) -> AgentDao:
    engine = MagicMock(spec=AsyncEngine)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    engine.begin = MagicMock(return_value=cm)
    return AgentDao(engine)


def _request(manifest: str) -> ReviseQueuedRequest:
    return ReviseQueuedRequest(
        user_scope="u1",
        session_id="s1",
        execution_id="exec-1",
        client_command_id="rev-1",
        expected_execution_revision=1,
        node_id="root",
        text="改为研究两家公司",
        manifest_payload=manifest,
        source_display_name="用户消息",
    )


class TestRevisedInputSourceRefs:
    """manifest → 仍选资产来源引用（去重保序；非资产类别不登记）。"""

    def test_flat_manifest_filters_non_asset_kinds(self) -> None:
        refs = _revised_input_source_refs(
            _manifest(
                [
                    _ref("src-a"),
                    _ref("msg-1", kind=SourceKind.USER_MESSAGE),
                    _ref("mcp-1", kind=SourceKind.MCP_RETURN),
                    _ref("src-b", kind=SourceKind.BUSINESS_SNAPSHOT, digest=_DIGEST_B),
                ]
            ),
            "u1/exec-1",
        )

        assert tuple(ref.source_id for ref in refs) == ("src-a", "src-b")

    def test_shared_manifest_reads_nested_section(self) -> None:
        refs = _revised_input_source_refs(_manifest([_ref("src-a")], shared=True), "u1/exec-1")

        assert tuple(ref.source_id for ref in refs) == ("src-a",)

    def test_duplicate_refs_deduped(self) -> None:
        refs = _revised_input_source_refs(_manifest([_ref("src-a"), _ref("src-a")]), "u1/exec-1")

        assert len(refs) == 1

    def test_missing_source_refs_is_empty(self) -> None:
        refs = _revised_input_source_refs(json_doc({"schema_version": 1, "text": "x"}), "u1/exec-1")

        assert refs == ()

    def test_non_list_source_refs_rejected(self) -> None:
        manifest = json_doc({"schema_version": 1, "text": "x", "source_refs": "src-a"})

        with pytest.raises(AgentServiceError) as excinfo:
            _revised_input_source_refs(manifest, "u1/exec-1")

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID

    def test_malformed_shared_section_rejected(self) -> None:
        manifest = json_doc({"schema_version": 1, "text": "x", "shared": ["bad"]})

        with pytest.raises(AgentServiceError) as excinfo:
            _revised_input_source_refs(manifest, "u1/exec-1")

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID

    def test_invalid_json_rejected(self) -> None:
        with pytest.raises(AgentServiceError) as excinfo:
            _revised_input_source_refs("{not json", "u1/exec-1")

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID

    def test_non_object_manifest_rejected(self) -> None:
        with pytest.raises(AgentServiceError) as excinfo:
            _revised_input_source_refs(json_doc(["refs"]), "u1/exec-1")

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID

    def test_invalid_source_ref_rejected(self) -> None:
        manifest = json_doc({"schema_version": 1, "text": "x", "source_refs": [{"source_id": "src-a"}]})

        with pytest.raises(AgentServiceError) as excinfo:
            _revised_input_source_refs(manifest, "u1/exec-1")

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID


class TestReviseQueuedSourceMembership:
    """N24：修订事务为仍选资料登记新输入版成员边。"""

    async def test_still_selected_sources_registered_at_new_input_version(self) -> None:
        conn = _ReviseConn(
            execution_edges={"src-a": ("1",), "src-b": ("1",)},
            asset_digests={"src-a": _DIGEST_A, "src-b": _DIGEST_B},
        )

        receipt = await _dao(conn).revise_queued(
            _request(
                _manifest(
                    [
                        _ref("src-a"),
                        _ref("src-b", kind=SourceKind.BUSINESS_SNAPSHOT, digest=_DIGEST_B),
                    ]
                )
            )
        )

        assert receipt.input_revision == 2
        accepted = [
            params
            for params in conn.source_link_inserts
            if params.get("consumer_kind") == "execution"
            and params.get("consumer_version") == "2"
            and params.get("source_id") != receipt.source_id
        ]
        assert {(params["source_id"], params["source_version"]) for params in accepted} == {
            ("src-a", "v1"),
            ("src-b", "v1"),
        }
        # 新消息源自身的执行边（M2/S2）仍是输入版 2；其余边不因修订改写旧版。
        assert not any(
            params.get("consumer_version") == "1" and params.get("consumer_kind") == "execution"
            for params in conn.source_link_inserts
        )

    async def test_removed_source_not_registered_at_new_input_version(self) -> None:
        conn = _ReviseConn(
            execution_edges={"src-a": ("1",), "src-removed": ("1",)},
            asset_digests={"src-a": _DIGEST_A, "src-removed": _DIGEST_C},
        )

        await _dao(conn).revise_queued(_request(_manifest([_ref("src-a")])))

        accepted_ids = {
            params["source_id"]
            for params in conn.source_link_inserts
            if params.get("consumer_kind") == "execution" and params.get("consumer_version") == "2"
        }
        assert "src-a" in accepted_ids
        assert "src-removed" not in accepted_ids

    async def test_source_not_previously_accepted_rejected(self) -> None:
        conn = _ReviseConn(execution_edges={}, asset_digests={"src-a": _DIGEST_A})

        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).revise_queued(_request(_manifest([_ref("src-a")])))

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        assert not any(
            params.get("source_id") == "src-a" and params.get("consumer_version") == "2"
            for params in conn.source_link_inserts
        )

    async def test_digest_mismatch_rejected(self) -> None:
        conn = _ReviseConn(execution_edges={"src-a": ("1",)}, asset_digests={"src-a": _DIGEST_C})

        with pytest.raises(AgentServiceError) as excinfo:
            await _dao(conn).revise_queued(_request(_manifest([_ref("src-a")])))

        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID

    async def test_existing_membership_edge_skipped(self) -> None:
        conn = _ReviseConn(execution_edges={"src-a": ("1", "2")}, asset_digests={"src-a": _DIGEST_A})

        receipt = await _dao(conn).revise_queued(_request(_manifest([_ref("src-a")])))

        assert not any(
            params.get("source_id") == "src-a" and params.get("consumer_version") == "2"
            for params in conn.source_link_inserts
        )
        assert receipt.input_revision == 2
