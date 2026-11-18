"""Agent 运行时应用表 schema 契约测试（任务 C-01，设计正本 §9.2—§9.11）。

离线元数据断言（不依赖数据库）：
- 14 张 agent_* 表注册于 ORM 元数据与 TABLE_DEFINITIONS（R12）；
- §9.2 共用列（user_scope/schema_version/created_at/updated_at/revision）与
  共用 CHECK；
- digest 列 CHAR(64)、复合 FK 全部携 user_scope、session/execution/round 引用
  RESTRICT、body/owner 环引用 DEFERRABLE（§9.10.1）；
- 部分唯一索引（根意图键/活跃轮/transfer claim/执行序号等）带 WHERE；
- 已冻结枚举的 CHECK 值域与 §3.1/§4.4/§5.1 wire 拼写一致。

迁移行为（upgrade/downgrade/alembic check）由
tests/integration/test_embedded_pg_migration_regression.py 在真实 embedded PG
上回归；本文件只锁元数据合同。
"""

import pytest
from sqlalchemy import CHAR, CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint

from data.data_dictionary import TABLE_DEFINITIONS
from data.persistence.models import AGENT_RUNTIME_TABLE_ORDER, Base

ALL_AGENT_TABLES = frozenset(
    {
        "agent_session",
        "agent_execution",
        "agent_round",
        "agent_message",
        "agent_artifact",
        "agent_command",
        "agent_decision",
        "agent_operation",
        "agent_asset_version",
        "agent_source_link",
        "agent_body_payload",
        "agent_resource_slot",
        "agent_read_mark",
        "agent_cleanup_job",
    }
)

#: §9.2：digest 列 CHAR(64)（列名 → 所在表集合）。
DIGEST_COLUMNS = {
    "intent_digest": {"agent_execution"},
    "frozen_digest": {"agent_round"},
    "actual_digest": {"agent_round"},
    "actual_input_digest": {"agent_artifact"},
    "result_digest": {"agent_artifact"},
    "input_digest": {"agent_command"},
    "parameter_digest": {"agent_decision", "agent_operation"},
    "source_scope_digest": {"agent_decision"},
    "digest": {"agent_asset_version"},
    "preview_digest": {"agent_cleanup_job"},
    "payload_digest": {"agent_body_payload"},
}

#: 循环 FK（use_alter 延后补建）：名称 → (源表, 可延迟)。
CIRCULAR_FKS = {
    "fk_agent_session_draft_transfer": ("agent_session", False),
    "fk_agent_execution_current_round": ("agent_execution", False),
    "fk_agent_message_body": ("agent_message", True),
    "fk_agent_artifact_body": ("agent_artifact", True),
    "fk_agent_asset_version_body": ("agent_asset_version", True),
    "fk_agent_body_payload_message": ("agent_body_payload", True),
    "fk_agent_body_payload_artifact": ("agent_body_payload", True),
    "fk_agent_body_payload_asset": ("agent_body_payload", True),
}


def _table(name: str):
    return Base.metadata.tables[name]


def _index(table_name: str, index_name: str) -> Index:
    return next(i for i in _table(table_name).indexes if i.name == index_name)


def _sd_text(column) -> str:
    """server_default 文本（TextClause.text）。"""

    assert column.server_default is not None
    return column.server_default.arg.text


def _check_names(table_name: str) -> set[str]:
    names = {c.name for c in _table(table_name).constraints if isinstance(c, CheckConstraint)}
    return {n for n in names if isinstance(n, str)}


def _fks(table_name: str) -> dict[str, ForeignKeyConstraint]:
    constraints = {c.name: c for c in _table(table_name).constraints if isinstance(c, ForeignKeyConstraint)}
    return {n: c for n, c in constraints.items() if isinstance(n, str)}


def _unique_index(table_name: str, index_name: str) -> Index:
    idx = _index(table_name, index_name)
    assert idx.unique, f"{index_name} must be a unique index"
    return idx


class TestTableRegistration:
    """14 张表注册面（R12）与建表顺序。"""

    def test_agent_runtime_table_order_matches_metadata(self) -> None:
        assert set(AGENT_RUNTIME_TABLE_ORDER) == ALL_AGENT_TABLES
        assert len(AGENT_RUNTIME_TABLE_ORDER) == len(ALL_AGENT_TABLES) == 14
        for name in AGENT_RUNTIME_TABLE_ORDER:
            assert name in Base.metadata.tables

    def test_all_agent_tables_registered_in_table_definitions(self) -> None:
        for name in ALL_AGENT_TABLES:
            assert name in TABLE_DEFINITIONS, f"{name} missing from TABLE_DEFINITIONS (R12)"
            definition = TABLE_DEFINITIONS[name]
            assert definition["alias"] == f"tab_{name}"

    def test_agent_tables_do_not_shadow_existing_tables(self) -> None:
        agent_tables = {t for t in Base.metadata.tables if t.startswith("agent_")}
        assert agent_tables == ALL_AGENT_TABLES

    def test_no_reserved_word_column_names(self) -> None:
        # R17：列名不得为 SQL 保留字/数字开头/含特殊字符（设计 §9.1.7）。
        reserved = {"order", "group", "select", "desc", "from", "where", "user", "check"}
        for name in ALL_AGENT_TABLES:
            for column in _table(name).columns:
                assert column.name.isidentifier(), f"{name}.{column.name} not a valid identifier"
                assert column.name.lower() not in reserved, f"{name}.{column.name} uses reserved word"


class TestCommonColumns:
    """§9.2 共用列与共用 CHECK（除明示例外每表必有）。"""

    def test_common_columns_present_on_every_agent_table(self) -> None:
        for name in ALL_AGENT_TABLES:
            table = _table(name)
            user_scope = table.columns["user_scope"]
            assert user_scope.nullable is False, f"{name}.user_scope must be NOT NULL"
            schema_version = table.columns["schema_version"]
            assert schema_version.nullable is False
            assert _sd_text(schema_version) == "1"
            revision = table.columns["revision"]
            assert revision.nullable is False
            assert _sd_text(revision) == "1"
            for ts_col in ("created_at", "updated_at"):
                column = table.columns[ts_col]
                # §9.2：TIMESTAMPTZ（新表按设计正本，与既有 ai_* timezone=False 不同）
                assert column.nullable is False, f"{name}.{ts_col} must be NOT NULL"
                assert column.type.timezone is True, f"{name}.{ts_col} must be TIMESTAMPTZ"
                assert column.server_default is not None

    def test_common_checks_present_on_every_agent_table(self) -> None:
        for name in ALL_AGENT_TABLES:
            checks = _check_names(name)
            assert f"ck_{name}_schema_version" in checks
            assert f"ck_{name}_revision" in checks


class TestColumnTypes:
    """ID 列 TEXT、digest 列 CHAR(64)、水位/计数 BIGINT。"""

    @pytest.mark.parametrize(("column_name", "tables"), sorted(DIGEST_COLUMNS.items()))
    def test_digest_columns_are_char64(self, column_name: str, tables: set[str]) -> None:
        for table_name in tables:
            column = _table(table_name).columns[column_name]
            assert isinstance(column.type, CHAR), f"{table_name}.{column_name} must be CHAR"
            assert column.type.length == 64

    def test_id_primary_keys_are_text(self) -> None:
        text_pk = {
            "agent_session": ("session_id",),
            "agent_execution": ("execution_id",),
            "agent_round": ("round_id",),
            "agent_message": ("message_id",),
            "agent_command": ("command_record_id",),
            "agent_decision": ("decision_id",),
            "agent_operation": ("operation_id",),
            "agent_source_link": ("link_id",),
            "agent_body_payload": ("body_ref",),
            "agent_resource_slot": ("slot_id",),
            "agent_read_mark": ("marker_id",),
            "agent_cleanup_job": ("cleanup_job_id",),
        }
        for table_name, pk_cols in text_pk.items():
            pk = _table(table_name).primary_key.columns
            assert tuple(c.name for c in pk) == pk_cols
            for col_name in pk_cols:
                col_type = _table(table_name).columns[col_name].type
                assert col_type.python_type is str, f"{table_name}.{col_name} PK must be TEXT"

    def test_artifact_composite_pk(self) -> None:
        pk = _table("agent_artifact").primary_key.columns
        assert tuple(c.name for c in pk) == ("artifact_id", "artifact_version")

    def test_asset_version_composite_pk_is_text(self) -> None:
        pk = _table("agent_asset_version").primary_key.columns
        assert tuple(c.name for c in pk) == ("asset_id", "asset_version")
        assert all(c.type.python_type is str for c in pk)


class TestForeignKeys:
    """复合 FK 携 user_scope；RESTRICT/DEFERRABLE 语义（§9.1.6、§9.10.1）。"""

    def test_all_fks_carry_user_scope(self) -> None:
        for table_name in ALL_AGENT_TABLES:
            for constraint in _table(table_name).constraints:
                if isinstance(constraint, ForeignKeyConstraint):
                    local = [c.parent.name for c in constraint.elements]
                    assert local[0] == "user_scope", f"{constraint.name} must carry user_scope (composite FK, §9.2)"

    def test_session_execution_round_refs_are_restrict(self) -> None:
        restrict_fk_prefixes = (
            "fk_agent_message_session",
            "fk_agent_message_execution",
            "fk_agent_message_round",
            "fk_agent_artifact_session",
            "fk_agent_artifact_execution",
            "fk_agent_artifact_round",
            "fk_agent_command_session",
            "fk_agent_command_execution",
            "fk_agent_command_round",
            "fk_agent_decision_session",
            "fk_agent_decision_execution",
            "fk_agent_decision_round",
            "fk_agent_operation_session",
            "fk_agent_operation_execution",
            "fk_agent_operation_round",
            "fk_agent_round_execution",
            "fk_agent_execution_session",
            "fk_agent_session_parent",
            "fk_agent_execution_parent",
            "fk_agent_round_previous",
        )
        for table_name in ALL_AGENT_TABLES:
            fks = _fks(table_name)
            for fk_name in restrict_fk_prefixes:
                if fk_name in fks:
                    assert fks[fk_name].ondelete == "RESTRICT", f"{fk_name} must be RESTRICT"

    def test_circular_fks_use_alter_with_expected_deferrability(self) -> None:
        for fk_name, (table_name, deferrable) in CIRCULAR_FKS.items():
            fk = _fks(table_name)[fk_name]
            assert fk.use_alter is True, f"{fk_name} must use_alter"
            if deferrable:
                # §9.10.1：body/owner 环引用 NO ACTION DEFERRABLE，提交时核定。
                assert fk.deferrable is True
                assert fk.initially == "DEFERRED"
            else:
                assert fk.deferrable is not True, f"{fk_name} must be non-deferrable RESTRICT"

    def test_body_payload_owner_fks_are_deferred(self) -> None:
        fks = _fks("agent_body_payload")
        assert set(fks) >= {
            "fk_agent_body_payload_message",
            "fk_agent_body_payload_artifact",
            "fk_agent_body_payload_asset",
        }

    def test_source_link_source_fk_targets_asset_version(self) -> None:
        fk = _fks("agent_source_link")["fk_agent_source_link_source"]
        remote = [c.column.table.name + "." + c.column.name for c in fk.elements]
        assert remote == [
            "agent_asset_version.user_scope",
            "agent_asset_version.asset_id",
            "agent_asset_version.asset_version",
        ]


class TestCandidateKeys:
    """作用域候选键 UNIQUE(user_scope, id)（§9.2）。"""

    @pytest.mark.parametrize(
        ("table_name", "key_cols"),
        [
            ("agent_session", ("session_id",)),
            ("agent_execution", ("execution_id",)),
            ("agent_round", ("round_id",)),
            ("agent_message", ("message_id",)),
            ("agent_command", ("command_record_id",)),
            ("agent_decision", ("decision_id",)),
            ("agent_operation", ("operation_id",)),
            ("agent_body_payload", ("body_ref",)),
            ("agent_resource_slot", ("slot_id",)),
            ("agent_read_mark", ("marker_id",)),
            ("agent_cleanup_job", ("cleanup_job_id",)),
        ],
    )
    def test_user_scope_candidate_key(self, table_name: str, key_cols: tuple[str, ...]) -> None:
        expected = ("user_scope", *key_cols)
        uniques = {
            tuple(c.name for c in u.columns) for u in _table(table_name).constraints if isinstance(u, UniqueConstraint)
        }
        assert expected in uniques, f"{table_name} missing UNIQUE(user_scope, {','.join(key_cols)})"

    def test_asset_version_artifact_user_scope_candidate_keys(self) -> None:
        uniques = {
            tuple(c.name for c in u.columns)
            for u in _table("agent_asset_version").constraints
            if isinstance(u, UniqueConstraint)
        }
        assert ("user_scope", "asset_id", "asset_version") in uniques
        uniques = {
            tuple(c.name for c in u.columns)
            for u in _table("agent_artifact").constraints
            if isinstance(u, UniqueConstraint)
        }
        assert ("user_scope", "artifact_id", "artifact_version") in uniques

    def test_message_dedup_and_seq_uniques(self) -> None:
        uniques = {
            tuple(c.name for c in u.columns)
            for u in _table("agent_message").constraints
            if isinstance(u, UniqueConstraint)
        }
        # §9.6：UNIQUE(session_id, event_id) 去重；双序号唯一。
        assert ("session_id", "event_id") in uniques
        assert ("session_id", "session_seq") in uniques

    def test_command_idempotency_uniques(self) -> None:
        uniques = {
            tuple(c.name for c in u.columns)
            for u in _table("agent_command").constraints
            if isinstance(u, UniqueConstraint)
        }
        assert ("user_scope", "target_id", "client_command_id") in uniques

    def test_source_link_edge_uniqueness(self) -> None:
        uniques = {
            tuple(c.name for c in u.columns)
            for u in _table("agent_source_link").constraints
            if isinstance(u, UniqueConstraint)
        }
        assert ("source_id", "source_version", "consumer_kind", "consumer_id", "consumer_version") in uniques

    def test_body_payload_owner_tuple_unique(self) -> None:
        uniques = {
            tuple(c.name for c in u.columns)
            for u in _table("agent_body_payload").constraints
            if isinstance(u, UniqueConstraint)
        }
        assert ("user_scope", "owner_kind", "owner_id", "owner_version") in uniques


class TestPartialIndexes:
    """部分唯一/筛选索引带 WHERE（§9.3—§9.11）。"""

    def test_execution_intent_key_partial_unique(self) -> None:
        idx = _unique_index("agent_execution", "idx_agent_execution_intent_key")
        where = idx.dialect_options["postgresql"]["where"]
        assert where is not None and "comparison_lane" in str(where.compile())

    def test_execution_parent_node_partial_unique(self) -> None:
        idx = _unique_index("agent_execution", "idx_agent_execution_parent_node")
        where = idx.dialect_options["postgresql"]["where"]
        assert where is not None and "parent_execution_id" in str(where.compile())

    def test_round_active_partial_unique(self) -> None:
        idx = _unique_index("agent_round", "idx_agent_round_active")
        where = idx.dialect_options["postgresql"]["where"]
        assert where is not None and "active_round" in str(where.compile())

    def test_message_execution_seq_partial_unique(self) -> None:
        idx = _unique_index("agent_message", "idx_agent_message_execution_seq")
        where = idx.dialect_options["postgresql"]["where"]
        assert where is not None and "execution_id" in str(where.compile())

    def test_message_search_gin_expression_index(self) -> None:
        idx = _index("agent_message", "idx_agent_message_search")
        assert idx.dialect_options["postgresql"]["using"] == "gin"
        assert idx.dialect_options["postgresql"]["where"] is not None
        sql = str(idx.dialect_options["postgresql"]["where"].compile())
        assert "search_text" in sql

    def test_command_transfer_claim_partial_unique(self) -> None:
        idx = _unique_index("agent_command", "idx_agent_command_transfer_source_claimed")
        where = idx.dialect_options["postgresql"]["where"]
        assert where is not None and "transfer_source_claimed" in str(where.compile())

    def test_command_transfer_id_partial_unique(self) -> None:
        idx = _unique_index("agent_command", "idx_agent_command_transfer_id")
        where = idx.dialect_options["postgresql"]["where"]
        assert where is not None and "transfer_id" in str(where.compile())

    def test_resource_slot_open_index(self) -> None:
        idx = _index("agent_resource_slot", "idx_agent_resource_slot_open")
        where = idx.dialect_options["postgresql"]["where"]
        assert where is not None and "released_at" in str(where.compile())

    def test_disabled_filter_indexes(self) -> None:
        for table_name, index_name in (
            ("agent_asset_version", "idx_agent_asset_version_disabled"),
            ("agent_source_link", "idx_agent_source_link_disabled"),
        ):
            idx = _index(table_name, index_name)
            where = idx.dialect_options["postgresql"]["where"]
            assert where is not None and (
                "tombstoned" in str(where.compile()) or "disabled_inherited" in str(where.compile())
            )


class TestFrozenEnumChecks:
    """已冻结 wire 拼写的 CHECK 值域（§3.1/§4.4/§5.1；未冻结列不加值域 CHECK）。"""

    def test_execution_display_state_values(self) -> None:
        # §5.1 执行维度 15 值。
        expected = (
            "'queued','running','waiting_dependency','waiting_user','pausing','paused',"
            "'ending','unknown','ended','failed','complete','partial','planned','withdrawn',"
            "'closed_unknown'"
        )
        checks = [str(c.sqltext) for c in _table("agent_execution").constraints if isinstance(c, CheckConstraint)]
        assert any(expected in text for text in checks)

    def test_execution_kind_check(self) -> None:
        checks = [str(c.sqltext) for c in _table("agent_execution").constraints if isinstance(c, CheckConstraint)]
        assert any("root" in text and "leaf" in text and "comparison_lane" in text for text in checks)

    def test_goal_coverage_check(self) -> None:
        for table_name in ("agent_execution", "agent_round"):
            checks = [str(c.sqltext) for c in _table(table_name).constraints if isinstance(c, CheckConstraint)]
            assert any(all(v in text for v in ("full", "partial", "none", "unknown")) for text in checks), (
                f"{table_name} goal_coverage CHECK missing"
            )

    def test_commit_state_check_matches_core_enum(self) -> None:
        from core.agent_types import CommitState

        checks = [str(c.sqltext) for c in _table("agent_command").constraints if isinstance(c, CheckConstraint)]
        commit_check = next(text for text in checks if "commit_state" in text)
        for state in CommitState:
            assert f"'{state.value}'" in commit_check

    def test_operation_phase_and_effect_state_checks_match_core_enums(self) -> None:
        from core.agent_types import EffectState

        checks = [str(c.sqltext) for c in _table("agent_operation").constraints if isinstance(c, CheckConstraint)]
        phase_check = next(text for text in checks if "phase" in text)
        for value in ("prepared", "dispatch_claimed", "dispatched", "result_saved", "failed_confirmed", "unknown"):
            assert f"'{value}'" in phase_check
        effect_check = next(text for text in checks if "effect_state" in text)
        for state in EffectState:
            assert f"'{state.value}'" in effect_check

    def test_message_event_kind_check_covers_section_44(self) -> None:
        # §4.4 事件类型表 17 种。
        kinds = (
            "session_changed",
            "draft_saved",
            "execution_accepted",
            "input_bound",
            "execution_changed",
            "node_changed",
            "public_message_saved",
            "source_read_saved",
            "source_disabled",
            "tool_call_recorded",
            "tool_result_saved",
            "decision_required",
            "command_changed",
            "artifact_saved",
            "usage_changed",
            "read_mark_saved",
            "cleanup_changed",
        )
        checks = [str(c.sqltext) for c in _table("agent_message").constraints if isinstance(c, CheckConstraint)]
        event_check = next(text for text in checks if "event_kind" in text)
        for kind in kinds:
            assert f"'{kind}'" in event_check

    def test_role_check(self) -> None:
        checks = [str(c.sqltext) for c in _table("agent_message").constraints if isinstance(c, CheckConstraint)]
        role_check = next(text for text in checks if "role IN" in text)
        for value in ("user", "assistant", "tool", "public_status"):
            assert f"'{value}'" in role_check

    def test_artifact_kind_check(self) -> None:
        checks = [str(c.sqltext) for c in _table("agent_artifact").constraints if isinstance(c, CheckConstraint)]
        kind_check = next(text for text in checks if "artifact_kind" in text)
        for value in ("plan", "explanation", "comparison", "candidate", "news", "backtest", "period", "stock_final"):
            assert f"'{value}'" in kind_check

    def test_asset_kind_check(self) -> None:
        checks = [str(c.sqltext) for c in _table("agent_asset_version").constraints if isinstance(c, CheckConstraint)]
        kind_check = next(text for text in checks if "asset_kind" in text)
        for value in (
            "source",
            "skill",
            "role",
            "prompt",
            "mcp_schema",
            "plan",
            "comparison_manifest",
            "summary",
        ):
            assert f"'{value}'" in kind_check

    def test_read_and_verification_state_checks_match_core_enums(self) -> None:
        from core.agent_types import ReadState, VerificationState

        checks = [str(c.sqltext) for c in _table("agent_asset_version").constraints if isinstance(c, CheckConstraint)]
        read_check = next(text for text in checks if "read_state" in text)
        for state in ReadState:
            assert f"'{state.value}'" in read_check
        verification_check = next(text for text in checks if "verification_state" in text)
        for state in VerificationState:
            assert f"'{state.value}'" in verification_check

    def test_body_payload_owner_checks(self) -> None:
        checks = _check_names("agent_body_payload")
        assert "ck_agent_body_payload_owner_exclusive" in checks
        assert "ck_agent_body_payload_owner_kind" in checks
        assert "ck_agent_body_payload_owner_identity" in checks
        assert "ck_agent_body_payload_encoding" in checks
        assert "ck_agent_body_payload_block_ordinal" in checks
        assert "ck_agent_body_payload_byte_length_exact" in checks

    def test_source_link_consumer_null_combination_checks(self) -> None:
        # 单条组合 CHECK 的 OR 分支覆盖六类 consumer（§9.10：只验证本行类型/列 NULL 组合）。
        checks = [str(c.sqltext) for c in _table("agent_source_link").constraints if isinstance(c, CheckConstraint)]
        combined = next(text for text in checks if "consumer_kind = 'draft'" in text)
        for kind in ("draft", "execution", "round", "message", "artifact", "asset"):
            assert f"consumer_kind = '{kind}'" in combined

    def test_pending_freeze_columns_have_no_value_check(self) -> None:
        # 未冻结 wire 值的 TEXT 列不得臆造 CHECK（本文件 docstring 待冻结清单）。
        pending = {
            "agent_command": ("delivery_state", "consumption_state", "command_kind"),
            "agent_decision": ("decision_kind", "grant_state"),
            "agent_operation": ("io_state",),
            "agent_message": ("payload_kind",),
            "agent_source_link": ("relation_kind",),
            "agent_read_mark": ("marker_kind",),
            "agent_round": ("native_state", "stop_reason"),
            "agent_artifact": ("source_available_state",),
            "agent_cleanup_job": ("phase",),
        }
        for table_name, columns in pending.items():
            checks = [str(c.sqltext) for c in _table(table_name).constraints if isinstance(c, CheckConstraint)]
            for column in columns:
                offending = [text for text in checks if f"{column} IN (" in text]
                assert not offending, f"{table_name}.{column} must not have a value CHECK before freeze"


class TestOwnerIdentityAndPairChecks:
    """R2-I32：正文 owner 与 FK 列成对一致、派生成对声明的结构性 CHECK。"""

    def test_body_payload_owner_identity_matches_fk_columns(self) -> None:
        # 正文 owner 三元组必须与 owner 外键列成对一致（不是只比文字三元组）。
        checks = [str(c.sqltext) for c in _table("agent_body_payload").constraints if isinstance(c, CheckConstraint)]
        identity = next(text for text in checks if "owner_version" in text and "artifact_version" in text)
        assert "owner_kind = 'message'" in identity and "message_id = owner_id" in identity
        assert "owner_kind = 'artifact'" in identity and "artifact_id = owner_id" in identity
        assert "owner_version = artifact_version::text" in identity
        assert "owner_kind = 'asset'" in identity and "asset_id = owner_id" in identity
        assert "asset_version = owner_version" in identity

    def test_source_link_derived_asset_pair_check(self) -> None:
        # 派生来源必须成对声明 id/version（禁止只给 version 的孤立派生边）。
        checks = [str(c.sqltext) for c in _table("agent_source_link").constraints if isinstance(c, CheckConstraint)]
        assert any("derived_asset_id IS NULL" in text and "derived_asset_version IS NULL" in text for text in checks)


class TestMiscContract:
    """杂项列级合同。"""

    def test_body_payload_byte_length_columns(self) -> None:
        table = _table("agent_body_payload")
        assert table.columns["payload_bytes"].type.python_type in (bytes, bytearray, memoryview)
        assert table.columns["byte_length"].nullable is False
        assert table.columns["payload_digest"].nullable is False

    def test_round_defaults(self) -> None:
        table = _table("agent_round")
        assert _sd_text(table.columns["goal_coverage"]) == "'unknown'"
        assert _sd_text(table.columns["final_saved"]) == "false"
        assert _sd_text(table.columns["convergence_confirmed"]) == "false"
        assert _sd_text(table.columns["active_round"]) == "false"

    def test_read_mark_watermark_columns(self) -> None:
        table = _table("agent_read_mark")
        for col in ("last_seq", "batch_seq", "postponed_through_seq"):
            assert _sd_text(table.columns[col]) == "0"

    def test_session_event_seq_default_zero(self) -> None:
        table = _table("agent_session")
        assert _sd_text(table.columns["event_seq"]) == "0"

    def test_execution_owner_epoch_default_one(self) -> None:
        table = _table("agent_execution")
        assert _sd_text(table.columns["owner_epoch"]) == "1"

    def test_command_transfer_source_claimed_default_false(self) -> None:
        table = _table("agent_command")
        assert _sd_text(table.columns["transfer_source_claimed"]) == "false"
