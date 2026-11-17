"""0046 迁移的冻结 DDL 快照契约（检视 D33）。

0046 是**历史迁移**：它必须固化发布时的 14 张 agent_* 表 DDL，``upgrade`` /
``downgrade`` 不得读取 ``data.persistence.models`` 的 ORM 元数据。否则将来任何
模型变更都会让同一条已发布 revision 在不同时间点建出不同结构（重写历史），既有库
与新库结构静默分叉。

本文件离线断言（不依赖数据库）：
- revision/down_revision 链未变；
- 快照覆盖且仅覆盖 14 张表，每条建表语句各 1 条，语句总数稳定（59 = 14 建表 +
  37 索引 + 8 延后补建循环 FK）；
- 迁移模块不导入 ORM 元数据（AST 导入面仅 stdlib + alembic，模块内无
  ``Base``/``metadata``），即冻结不依赖当前模型；
- 延后补建的循环 FK 保持 8 条（§9.10.1 body/owner 环引用 + execution/session 环）。

DDL 与模型的一致性由 CI 的 ``alembic check``（apply 后与 ORM 元数据逐列比对）与
tests/integration/test_orm_migration_consistency.py 在真实 PG 上验证。模型变更
（增列/改类型/改约束/加索引/加表）必须新增后续迁移，而不是改写本快照——本文件的
计数断言会在有人改写快照时失败，迫使走新迁移。
"""

import ast
import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0046_add_agent_runtime_application_tables.py"
)

#: 0046 冻结的 14 张 agent_* 表（历史契约，不随当前模型变化）。
FROZEN_TABLES = frozenset(
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

_CREATE_TABLE_RE = re.compile(r"^CREATE TABLE (\w+) \(")
_INDEX_PREFIXES = ("CREATE INDEX ", "CREATE UNIQUE INDEX ")


def _load_migration() -> Any:
    """按文件路径加载数字开头的迁移模块（不能作为普通模块 import）。"""

    spec = importlib.util.spec_from_file_location("migration_0046", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def migration() -> Any:
    return _load_migration()


class TestFrozenSnapshot:
    """冻结快照的结构契约（内容变更必须走后续迁移）。"""

    def test_revision_chain_unchanged(self, migration: Any) -> None:
        assert migration.revision == "0046"
        assert migration.down_revision == "0045"

    def test_creates_exactly_the_14_agent_tables(self, migration: Any) -> None:
        statements = migration._FROZEN_UPGRADE_STATEMENTS
        created = [m.group(1) for s in statements if (m := _CREATE_TABLE_RE.match(s))]
        assert len(created) == 14
        assert set(created) == FROZEN_TABLES
        assert set(migration._FROZEN_TABLE_NAMES) == FROZEN_TABLES

    def test_statement_composition_is_pinned(self, migration: Any) -> None:
        statements = migration._FROZEN_UPGRADE_STATEMENTS
        indexes = [s for s in statements if s.startswith(_INDEX_PREFIXES)]
        alters = [s for s in statements if s.startswith("ALTER TABLE ")]
        assert len(statements) == 59
        assert len(indexes) == 37
        assert len(alters) == 8

    def test_deferred_circular_fks_are_frozen(self, migration: Any) -> None:
        alters = [s for s in migration._FROZEN_UPGRADE_STATEMENTS if s.startswith("ALTER TABLE ")]
        # §9.10.1：body/owner 环引用 6 条 + execution/session 环 2 条，均为 use_alter 补建。
        deferred = {match.group(1) for s in alters if (match := re.search(r"ADD CONSTRAINT (\w+)", s))}
        assert deferred == {
            "fk_agent_message_body",
            "fk_agent_artifact_body",
            "fk_agent_asset_version_body",
            "fk_agent_body_payload_message",
            "fk_agent_body_payload_artifact",
            "fk_agent_body_payload_asset",
            "fk_agent_execution_current_round",
            "fk_agent_session_draft_transfer",
        }


class TestNoOrmDependency:
    """历史迁移不得依赖当前 ORM 元数据（D33 的结构性保证）。"""

    def test_imports_only_stdlib_and_alembic(self) -> None:
        tree = ast.parse(MIGRATION_PATH.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module)
        assert imported == {"collections.abc", "alembic"}

    def test_module_exposes_no_orm_metadata(self, migration: Any) -> None:
        namespace = vars(migration)
        assert "Base" not in namespace
        assert "metadata" not in namespace
