"""Agent §4.2 R02 历史查询合同单测（C-03-3a：HistoryQuery/HistoryHit/HistoryPage）。

覆盖：decode strict 全键必填禁额外键；R02 三范围规则（all_saved 不带局部目标/
current_session 必带主 session_id/current_child 必带 child_target）；round_selection
两 kind；agent_filter 两 kind 不互换（exact 版本必携、all_versions 禁带）；
query_digest 规范化（全部条件/空值含义入 digest、limit 与游标不入）；游标
h:{上界ts}:{排序键ts}:{锚ID} 语法；HistoryHit 地址组互斥归属；HistoryPage 指纹摘要。
"""

from __future__ import annotations

import pytest

from core.agent_types import (
    AgentValidationError,
    HistoryAgentFilter,
    HistoryChildTarget,
    HistoryHit,
    HistoryPage,
    HistoryQuery,
    HistoryRoundSelection,
    compute_history_query_digest,
    decode_history_agent_filter,
    decode_history_child_target,
    decode_history_hit,
    decode_history_page,
    decode_history_query,
    decode_history_round_selection,
    normalize_history_query,
    sha256_hex,
    validate_history_query,
)

ALL_ROUNDS = HistoryRoundSelection(kind="all_rounds")
EXACT_ROUND = HistoryRoundSelection(kind="exact", execution_id="exec-leaf", round_id="round-1")


def base_query(**overrides: object) -> HistoryQuery:
    values: dict[str, object] = {
        "scope": "current_session",
        "round_selection": ALL_ROUNDS,
        "query_text": "回撤",
        "time_basis": "created",
        "sort": "newest_first",
    }
    values.update(overrides)
    return HistoryQuery(**values)  # pyright: ignore[reportArgumentType]


def full_wire(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "scope": "current_session",
        "round_selection": {"kind": "all_rounds"},
        "query_text": "回撤",
        "time_basis": "created",
        "sort": "newest_first",
        "limit": 50,
        "session_id": "s-1",
        "child_target": None,
        "agent_filter": None,
        "message_role_filter": None,
        "type_filter": None,
        "state_filter": None,
        "date_from": None,
        "date_to": None,
        "cursor": None,
    }
    values.update(overrides)
    return values


def wire_history_hit(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "hit_kind": "message",
        "session_id": "s-1",
        "sort_moment": "2026-06-01T12:00:00.000000Z",
        "created_at": "2026-06-01T12:00:00.000000Z",
        "session_seq": 3,
        "execution_id": "exec-1",
        "node_id": "root",
        "round_id": "round-1",
        "message_id": "m-3",
        "artifact_id": None,
        "artifact_version": None,
        "role": "assistant",
        "payload_kind": "public_message_saved.v1",
        "artifact_kind": None,
        "summary_text": "命中摘要",
    }
    values.update(overrides)
    return values


def wire_history_page(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "query_digest": "a" * 64,
        "fixed_upper_bound": "2026-06-01T12:00:00.000000Z",
        "revision_fingerprint": "b" * 64,
        "hits": [wire_history_hit()],
        "next_cursor": "h:2026-06-01T12:00:00.000000Z:2026-06-01T11:59:59.000000Z:m-3",
        "read_moment": "2026-06-01T12:00:01.000000Z",
    }
    values.update(overrides)
    return values


class TestHistoryChildTarget:
    def test_decode_strict_roundtrip(self) -> None:
        wire = {"parent_execution_id": "exec-root", "execution_id": "exec-leaf", "node_id": "leaf"}
        target = decode_history_child_target(wire)
        assert target.parent_execution_id == "exec-root"
        assert target.execution_id == "exec-leaf"

    def test_missing_key_rejected(self) -> None:
        with pytest.raises(AgentValidationError, match="missing_field"):
            decode_history_child_target({"parent_execution_id": "exec-root", "execution_id": "exec-leaf"})

    def test_unknown_key_rejected(self) -> None:
        wire = {"parent_execution_id": "exec-root", "execution_id": "exec-leaf", "node_id": "leaf", "extra": 1}
        with pytest.raises(AgentValidationError, match="unknown_field"):
            decode_history_child_target(wire)

    def test_self_parent_rejected(self) -> None:
        wire = {"parent_execution_id": "exec-root", "execution_id": "exec-root", "node_id": "root"}
        with pytest.raises(AgentValidationError, match="identity_mismatch"):
            decode_history_child_target(wire)


class TestRoundSelection:
    def test_all_rounds_forbids_extra_keys(self) -> None:
        with pytest.raises(AgentValidationError, match="unknown_field"):
            decode_history_round_selection({"kind": "all_rounds", "execution_id": "exec-1"})

    def test_exact_requires_both_ids(self) -> None:
        with pytest.raises(AgentValidationError, match="missing_field"):
            decode_history_round_selection({"kind": "exact", "execution_id": "exec-1"})
        with pytest.raises(AgentValidationError, match="missing_field"):
            decode_history_round_selection({"kind": "exact", "round_id": "round-1"})

    def test_unknown_kind_rejected(self) -> None:
        with pytest.raises(AgentValidationError, match="invalid_round_selection_kind"):
            decode_history_round_selection({"kind": "latest"})


class TestAgentFilter:
    def test_role_identity_exact_requires_version(self) -> None:
        with pytest.raises(AgentValidationError, match="identity_mismatch"):
            decode_history_agent_filter({"kind": "role_identity", "role_id": "role-1", "version_selection": "exact"})

    def test_role_identity_all_versions_forbids_version(self) -> None:
        with pytest.raises(AgentValidationError, match="identity_mismatch"):
            decode_history_agent_filter(
                {
                    "kind": "role_identity",
                    "role_id": "role-1",
                    "version_selection": "all_versions",
                    "role_version": "v1",
                }
            )

    def test_role_identity_exact_valid(self) -> None:
        decoded = decode_history_agent_filter(
            {
                "kind": "role_identity",
                "role_id": "role-1",
                "version_selection": "exact",
                "role_version": "v1",
            }
        )
        assert decoded.role_id == "role-1"
        assert decoded.role_version == "v1"

    def test_execution_agent_requires_full_address(self) -> None:
        with pytest.raises(AgentValidationError, match="missing_field"):
            decode_history_agent_filter({"kind": "execution_agent", "execution_id": "exec-1", "node_id": "root"})

    def test_execution_agent_valid(self) -> None:
        decoded = decode_history_agent_filter(
            {"kind": "execution_agent", "execution_id": "exec-1", "node_id": "root", "round_id": "round-1"}
        )
        assert decoded.round_id == "round-1"

    def test_unknown_kind_rejected(self) -> None:
        with pytest.raises(AgentValidationError, match="invalid_agent_filter_kind"):
            decode_history_agent_filter({"kind": "by_name", "name": "模型A"})


class TestMemoryKindWhitelist:
    """D15：内存直接构造 HistoryQuery 的 kind 亦按严格白名单拒绝（无 else 兜底）。"""

    def test_unknown_round_selection_kind_rejected_even_with_valid_ids(self) -> None:
        bogus = HistoryRoundSelection(kind="latest", execution_id="exec-leaf", round_id="round-1")
        query = base_query(session_id="s-1", round_selection=bogus)
        with pytest.raises(AgentValidationError) as exc:
            validate_history_query(query)
        assert exc.value.code == "invalid_round_selection_kind"

    def test_unknown_agent_filter_kind_rejected_even_with_valid_address(self) -> None:
        bogus = HistoryAgentFilter(kind="by_name", execution_id="exec-1", node_id="root", round_id="round-1")
        query = base_query(session_id="s-1", agent_filter=bogus)
        with pytest.raises(AgentValidationError) as exc:
            validate_history_query(query)
        assert exc.value.code == "invalid_agent_filter_kind"

    def test_known_kinds_accepted(self) -> None:
        validate_history_query(base_query(session_id="s-1"))
        validate_history_query(base_query(session_id="s-1", round_selection=EXACT_ROUND))
        validate_history_query(
            base_query(
                session_id="s-1",
                agent_filter=HistoryAgentFilter(
                    kind="role_identity", role_id="role-1", version_selection="all_versions"
                ),
            )
        )
        validate_history_query(
            base_query(
                session_id="s-1",
                agent_filter=HistoryAgentFilter(
                    kind="execution_agent", execution_id="exec-1", node_id="root", round_id="round-1"
                ),
            )
        )


class TestScopeRules:
    def test_all_saved_rejects_session_target(self) -> None:
        query = base_query(scope="all_saved", session_id="s-1")
        with pytest.raises(AgentValidationError, match="identity_mismatch"):
            validate_history_query(query)

    def test_all_saved_rejects_child_target(self) -> None:
        query = base_query(
            scope="all_saved",
            child_target=HistoryChildTarget(parent_execution_id="exec-root", execution_id="exec-leaf", node_id="leaf"),
        )
        with pytest.raises(AgentValidationError, match="identity_mismatch"):
            validate_history_query(query)

    def test_current_session_requires_session(self) -> None:
        query = base_query(scope="current_session")
        with pytest.raises(AgentValidationError, match="identity_mismatch"):
            validate_history_query(query)

    def test_current_session_rejects_child_target(self) -> None:
        query = base_query(
            scope="current_session",
            session_id="s-1",
            child_target=HistoryChildTarget(parent_execution_id="exec-root", execution_id="exec-leaf", node_id="leaf"),
        )
        with pytest.raises(AgentValidationError, match="identity_mismatch"):
            validate_history_query(query)

    def test_current_child_requires_target(self) -> None:
        query = base_query(scope="current_child", session_id="s-1")
        with pytest.raises(AgentValidationError, match="identity_mismatch"):
            validate_history_query(query)

    def test_current_child_exact_round_must_belong_to_leaf(self) -> None:
        query = base_query(
            scope="current_child",
            session_id="s-1",
            child_target=HistoryChildTarget(parent_execution_id="exec-root", execution_id="exec-leaf", node_id="leaf"),
            round_selection=EXACT_ROUND,
        )
        validate_history_query(query)
        wrong_round = HistoryRoundSelection(kind="exact", execution_id="exec-other", round_id="round-1")
        query_wrong = base_query(
            scope="current_child",
            session_id="s-1",
            child_target=HistoryChildTarget(parent_execution_id="exec-root", execution_id="exec-leaf", node_id="leaf"),
            round_selection=wrong_round,
        )
        with pytest.raises(AgentValidationError, match="identity_mismatch"):
            validate_history_query(query_wrong)


class TestValueSetsAndBounds:
    @pytest.mark.parametrize(
        ("field", "code"),
        [
            ("scope", "invalid_scope"),
            ("time_basis", "invalid_time_basis"),
            ("sort", "invalid_sort"),
            ("type_filter", "invalid_type_filter"),
            ("message_role_filter", "invalid_message_role_filter"),
        ],
    )
    def test_unknown_enum_values_rejected(self, field: str, code: str) -> None:
        bad_values = {
            "scope": "latest",
            "time_basis": "magic",
            "sort": "random",
            "type_filter": "both",
            "message_role_filter": "model",
        }
        query = base_query(**{field: bad_values[field]})
        with pytest.raises(AgentValidationError, match=code):
            validate_history_query(query)

    @pytest.mark.parametrize("bad_limit", [0, 51, True])
    def test_limit_bounds(self, bad_limit: int | bool) -> None:
        query = base_query(limit=bad_limit)  # pyright: ignore[reportArgumentType]
        with pytest.raises(AgentValidationError) as exc:
            validate_history_query(query)
        assert exc.value.code == ("invalid_type" if isinstance(bad_limit, bool) else "invalid_range")

    def test_empty_state_filter_rejected(self) -> None:
        query = base_query(state_filter="")
        with pytest.raises(AgentValidationError, match="invalid_range"):
            validate_history_query(query)

    def test_bad_date_rejected(self) -> None:
        query = base_query(date_from="2026/06/01")
        with pytest.raises(AgentValidationError, match="invalid_date"):
            validate_history_query(query)

    def test_date_order_reversed_rejected(self) -> None:
        query = base_query(date_from="2026-06-02", date_to="2026-06-01")
        with pytest.raises(AgentValidationError, match="invalid_range"):
            validate_history_query(query)

    @pytest.mark.parametrize(
        "bad_cursor",
        [
            "not-a-cursor",
            "h:3:4:m-1",
            "m:3",
            "a:2026-06-01T12:00:00.000000Z:x:1",
            "h:2026-06-01T12:00:00Z:2026-06-01T11:00:00.000000Z:m-1",
        ],
    )
    def test_cursor_syntax_rejected(self, bad_cursor: str) -> None:
        query = base_query(cursor=bad_cursor)
        with pytest.raises(AgentValidationError, match="invalid_cursor"):
            validate_history_query(query)

    def test_valid_cursor_accepted(self) -> None:
        query = base_query(session_id="s-1", cursor="h:2026-06-01T12:00:00.000000Z:2026-06-01T11:59:59.000000Z:m-1")
        validate_history_query(query)


class TestDigest:
    def test_digest_stable_and_condition_sensitive(self) -> None:
        query = base_query(session_id="s-1")
        assert compute_history_query_digest(query) == compute_history_query_digest(
            base_query(session_id="s-1", limit=20)
        )
        other = compute_history_query_digest(base_query(session_id="s-1", query_text="波动"))
        assert compute_history_query_digest(query) != other

    def test_digest_binds_empty_vs_none_semantics(self) -> None:
        with_empty = compute_history_query_digest(base_query(query_text=""))
        with_text = compute_history_query_digest(base_query(query_text="回撤"))
        assert with_empty != with_text

    def test_digest_excludes_cursor_and_limit(self) -> None:
        q1 = base_query(session_id="s-1", cursor="h:2026-06-01T12:00:00.000000Z:2026-06-01T11:59:59.000000Z:m-1")
        q2 = base_query(session_id="s-1", limit=10)
        assert compute_history_query_digest(q1) == compute_history_query_digest(q2)

    def test_digest_binds_round_selection_and_agent_filter(self) -> None:
        q_all = base_query(session_id="s-1", round_selection=ALL_ROUNDS)
        q_exact = base_query(session_id="s-1", round_selection=EXACT_ROUND)
        assert compute_history_query_digest(q_all) != compute_history_query_digest(q_exact)
        q_with_agent = base_query(
            session_id="s-1",
            agent_filter=HistoryAgentFilter(kind="role_identity", role_id="role-1", version_selection="all_versions"),
        )
        assert compute_history_query_digest(base_query(session_id="s-1")) != compute_history_query_digest(q_with_agent)

    def test_normalize_writes_explicit_nulls(self) -> None:
        wire = normalize_history_query(base_query())
        assert '"session_id":null' in wire
        assert '"agent_filter":null' in wire
        assert '"limit"' not in wire


class TestHistoryQueryDecode:
    def test_strict_roundtrip(self) -> None:
        query = base_query(session_id="s-1", limit=20)
        wire = full_wire(limit=20)
        decoded = decode_history_query(wire)
        assert decoded == query

    def test_missing_key_rejected(self) -> None:
        wire = full_wire()
        del wire["time_basis"]
        with pytest.raises(AgentValidationError, match="missing_field"):
            decode_history_query(wire)

    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(AgentValidationError, match="unknown_field"):
            decode_history_query(full_wire(extra=1))

    def test_bad_round_selection_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            decode_history_query(full_wire(round_selection={"kind": "latest"}))
        assert exc.value.code == "invalid_round_selection_kind"
        assert exc.value.field_path == "$.history_query.round_selection.kind"

    def test_decode_validates_scope_rules(self) -> None:
        with pytest.raises(AgentValidationError, match="identity_mismatch"):
            decode_history_query(full_wire(session_id=None))


class TestHistoryHit:
    def test_message_hit_valid(self) -> None:
        hit = decode_history_hit(wire_history_hit())
        assert hit.hit_kind == "message"
        assert hit.message_id == "m-3"
        assert hit.session_seq == 3

    def test_artifact_hit_valid(self) -> None:
        wire = wire_history_hit(
            hit_kind="artifact",
            message_id=None,
            session_seq=None,
            artifact_id="art-1",
            artifact_version=1,
            role=None,
            payload_kind=None,
            artifact_kind="plan",
        )
        hit = decode_history_hit(wire)
        assert hit.artifact_id == "art-1"
        assert hit.artifact_version == 1

    def test_message_hit_requires_session_seq(self) -> None:
        with pytest.raises(AgentValidationError, match="identity_mismatch"):
            decode_history_hit(wire_history_hit(session_seq=None))

    def test_message_hit_forbids_artifact_address(self) -> None:
        with pytest.raises(AgentValidationError, match="identity_mismatch"):
            decode_history_hit(wire_history_hit(artifact_id="art-1", artifact_version=1))

    def test_artifact_hit_requires_version(self) -> None:
        with pytest.raises(AgentValidationError, match="identity_mismatch"):
            decode_history_hit(
                wire_history_hit(hit_kind="artifact", message_id=None, artifact_id="art-1", artifact_version=None)
            )

    def test_unknown_hit_kind_rejected(self) -> None:
        with pytest.raises(AgentValidationError, match="invalid_hit_kind"):
            decode_history_hit(wire_history_hit(hit_kind="tool"))

    def test_bad_timestamp_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            decode_history_hit(wire_history_hit(sort_moment="2026-06-01 12:00:00"))
        assert exc.value.field_path == "$.hits.sort_moment"

    def test_missing_key_rejected(self) -> None:
        wire = wire_history_hit()
        del wire["summary_text"]
        with pytest.raises(AgentValidationError, match="missing_field"):
            decode_history_hit(wire)


class TestHistoryPage:
    def test_decode_strict_roundtrip(self) -> None:
        page = decode_history_page(wire_history_page())
        assert len(page.hits) == 1
        assert page.next_cursor is not None
        assert page.revision_fingerprint == "b" * 64

    def test_empty_page_is_explicit_no_hits(self) -> None:
        page = decode_history_page(wire_history_page(hits=[], next_cursor=None))
        assert page.hits == ()
        assert page.next_cursor is None

    def test_next_cursor_syntax_enforced(self) -> None:
        with pytest.raises(AgentValidationError, match="invalid_cursor"):
            decode_history_page(wire_history_page(next_cursor="m:3"))

    def test_digest_format_enforced(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            decode_history_page(wire_history_page(query_digest="ZZ"))
        assert exc.value.field_path == "$.history_page.query_digest"

    def test_missing_key_rejected(self) -> None:
        wire = wire_history_page()
        del wire["read_moment"]
        with pytest.raises(AgentValidationError, match="missing_field"):
            decode_history_page(wire)

    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(AgentValidationError, match="unknown_field"):
            decode_history_page(wire_history_page(extra=None))

    def test_hit_order_preserved(self) -> None:
        page = decode_history_page(
            wire_history_page(
                hits=[
                    wire_history_hit(message_id="m-9", session_seq=9),
                    wire_history_hit(message_id="m-3", session_seq=3),
                ]
            )
        )
        assert [hit.message_id for hit in page.hits] == ["m-9", "m-3"]
        assert sha256_hex("x") == sha256_hex("x")
