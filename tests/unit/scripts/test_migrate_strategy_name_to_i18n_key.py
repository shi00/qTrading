"""scripts/migrate_strategy_name_to_i18n_key.py 单测。

覆盖：历史 ``strategy_name`` → i18n key 映射（幂等 / 未知值透传）、SQLAlchemy → asyncpg
DSN 归一化、Alembic revision 支持窗口、归一化唯一键冲突预检、单事务写入与回滚，以及
``main`` 打印 DSN 前必须脱敏（R9：不得输出明文数据库密码）。
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import asyncpg
import pytest

pytestmark = [pytest.mark.unit, pytest.mark.meta]

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import migrate_strategy_name_to_i18n_key as migrate_script  # noqa: E402 - sys.path 注入后导入


def _row(
    row_id: int,
    name: str | None,
    *,
    trade_date: date = date(2024, 1, 2),
    ts_code: str = "000001.SZ",
    run_id: str = "run-a",
) -> dict[str, Any]:
    return {
        "id": row_id,
        "trade_date": trade_date,
        "strategy_name": name,
        "ts_code": ts_code,
        "run_id": run_id,
    }


def _empty_stats(**overrides: Any) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "revision": "0033",
        "revision_supported": True,
        "scanned": 0,
        "migrated": 0,
        "rows_to_update": 0,
        "skipped_already_i18n_key": 0,
        "unknown": [],
        "conflicts": [],
        "conflict_key_scope": "head-4key",
        "executed": False,
    }
    stats.update(overrides)
    return stats


class _FakeTransaction:
    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn

    async def __aenter__(self) -> _FakeConn:
        self.conn.events.append("tx_begin")
        return self.conn

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        self.conn.events.append("tx_rollback" if exc_type is not None else "tx_commit")
        self.conn.exit_exc_type = exc_type
        return False


class _FakeConn:
    def __init__(
        self,
        *,
        revision_rows: Sequence[Mapping[str, Any]] | None = None,
        table_rows: Sequence[Mapping[str, Any]] | None = None,
        revision_error: BaseException | None = None,
        fail_on_update_index: int | None = None,
    ) -> None:
        self.revision_rows = list(revision_rows or [])
        self.table_rows = list(table_rows or [])
        self.revision_error = revision_error
        self.fail_on_update_index = fail_on_update_index
        self.events: list[str] = []
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.exit_exc_type: Any = None
        self.closed = False
        self._update_index = 0

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction(self)

    async def fetch(self, query: str, *args: Any) -> list[Mapping[str, Any]]:
        if "alembic_version" in query:
            if self.revision_error is not None:
                raise self.revision_error
            return list(self.revision_rows)
        if "screening_history" in query:
            return list(self.table_rows)
        raise AssertionError(f"unexpected query: {query}")

    async def execute(self, query: str, *args: Any) -> str:
        self.execute_calls.append((query, args))
        self.events.append("execute")
        index = self._update_index
        self._update_index += 1
        if self.fail_on_update_index is not None and index == self.fail_on_update_index:
            raise RuntimeError("simulated mid-transaction failure")
        return "UPDATE 1"

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def install_fake_conn(monkeypatch: pytest.MonkeyPatch):
    """把 ``asyncpg.connect`` 替换为返回给定 fake 连接的协程。"""

    def _install(conn: _FakeConn) -> _FakeConn:
        async def _connect(*, dsn: str) -> _FakeConn:
            _ = dsn
            return conn

        monkeypatch.setattr(migrate_script.asyncpg, "connect", _connect)
        return conn

    return _install


# --------------------------------------------------------------------------- #
# 纯函数：映射 / DSN / revision 支持窗口 / 冲突检测 / 更新计划
# --------------------------------------------------------------------------- #


def test_migrate_strategy_name_maps_known_values() -> None:
    """历史 zh / en / identifier 三种写法均映射到同一 i18n key。"""
    assert migrate_script.migrate_strategy_name("价值投资") == "strategy_value_name"
    assert migrate_script.migrate_strategy_name("Value Investing") == "strategy_value_name"
    assert migrate_script.migrate_strategy_name("AI_Auto_Nightly") == "strategy_ai_nightly_name"


def test_migrate_strategy_name_is_idempotent_and_passes_through_unknown() -> None:
    """已是 i18n key 的值原样返回（幂等）；未知值 / 空值原样透传（不猜测性修复）。"""
    assert migrate_script.migrate_strategy_name("strategy_value_name") == "strategy_value_name"
    assert migrate_script.migrate_strategy_name("未知策略") == "未知策略"
    assert migrate_script.migrate_strategy_name(None) is None
    assert migrate_script.migrate_strategy_name("") == ""


def test_normalize_dsn_strips_asyncpg_driver() -> None:
    """``postgresql+asyncpg://`` 转为 asyncpg 可接受的 ``postgresql://``，其余原样。"""
    assert (
        migrate_script._normalize_dsn("postgresql+asyncpg://user:pwd@host:5432/db")
        == "postgresql://user:pwd@host:5432/db"
    )
    assert migrate_script._normalize_dsn("postgresql://host/db") == "postgresql://host/db"


def test_revision_support_walks_ancestors_not_string_order() -> None:
    """支持窗口按 revision 祖先链判定：0033 及其后代可写，之前/未知 revision 拒绝。"""
    parents = {"0024": ("0023",), "0032": ("0031",), "0033": ("0032",), "0034": ("0033",)}
    assert migrate_script.revision_is_supported("0033", parents) is True
    assert migrate_script.revision_is_supported("0034", parents) is True
    assert migrate_script.revision_is_supported("0032", parents) is False
    assert migrate_script.revision_is_supported("0024", parents) is False
    assert migrate_script.revision_is_supported("9999", parents) is False
    assert migrate_script.revision_is_supported(None, parents) is False
    assert migrate_script.revision_is_supported("", parents) is False


def test_revision_support_handles_merge_parents() -> None:
    """合并 revision 的多父链上任一祖先命中 0033 即视为支持。"""
    parents = {"0033": ("0032",), "0034": ("0033", "0031"), "0031": ("0030",)}
    assert migrate_script.revision_is_supported("0034", parents) is True


def test_revision_support_uses_repo_alembic_chain() -> None:
    """真实 Alembic 链：0033 可写、0024 不可写（不硬编码未来/head revision）。"""
    parents = migrate_script._load_revision_parents()
    assert parents["0033"] == ("0032",)
    assert migrate_script.revision_is_supported("0033", parents) is True
    assert migrate_script.revision_is_supported("0024", parents) is False


def test_single_revision_rejects_missing_or_ambiguous_rows() -> None:
    """alembic_version 无行 / 多行 / 空值均视为未知 revision（fail closed）。"""
    assert migrate_script._single_revision([]) is None
    assert migrate_script._single_revision([{"version_num": None}]) is None
    assert migrate_script._single_revision([{"version_num": "0033"}, {"version_num": "0034"}]) is None
    assert migrate_script._single_revision([{"version_num": "0033"}]) == "0033"


def test_collision_key_includes_run_id_for_head_schema() -> None:
    """head 四键包含 run_id；legacy 三键不包含 run_id。"""
    row = _row(1, "Value Investing", run_id="run-a")
    assert migrate_script.normalized_collision_key(row, include_run_id=True) == (
        date(2024, 1, 2),
        "strategy_value_name",
        "000001.SZ",
        "run-a",
    )
    assert migrate_script.normalized_collision_key(row, include_run_id=False) == (
        date(2024, 1, 2),
        "strategy_value_name",
        "000001.SZ",
    )


def test_collision_groups_separate_runs_under_head_key() -> None:
    """相同日期/股票、不同 run 的双别名在 head 四键下不冲突；legacy 三键下冲突。"""
    rows = [_row(1, "Value Investing", run_id="run-a"), _row(2, "strategy_value_name", run_id="run-b")]
    assert migrate_script.find_collision_groups(rows, include_run_id=True) == []
    legacy_groups = migrate_script.find_collision_groups(rows, include_run_id=False)
    assert len(legacy_groups) == 1
    assert {member["id"] for member in legacy_groups[0]["rows"]} == {1, 2}


def test_collision_groups_detect_same_run_aliases() -> None:
    """同一 run 内同一策略的两种历史写法归一化后键相同 → 冲突组。"""
    rows = [_row(1, "Value Investing", run_id="run-a"), _row(2, "strategy_value_name", run_id="run-a")]
    groups = migrate_script.find_collision_groups(rows, include_run_id=True)
    assert len(groups) == 1
    assert {member["id"] for member in groups[0]["rows"]} == {1, 2}
    assert all(member["normalized_name"] == "strategy_value_name" for member in groups[0]["rows"])
    assert {member["will_update"] for member in groups[0]["rows"]} == {True, False}


def test_collision_groups_ignore_null_names_and_empty_table() -> None:
    """NULL strategy_name 不参与唯一键冲突（PG 允许多 NULL）；空表无冲突。"""
    assert migrate_script.find_collision_groups([], include_run_id=True) == []
    rows = [_row(1, None, run_id="run-a"), _row(2, None, run_id="run-a")]
    assert migrate_script.find_collision_groups(rows, include_run_id=True) == []


def test_build_update_plan_preserves_unknown_values() -> None:
    """未知策略名保留原值并单独统计；已知别名生成按值去重的更新计划。"""
    rows = [_row(1, "自定义策略"), _row(2, "自定义策略"), _row(3, "Value Investing")]
    updates, unknown = migrate_script.build_update_plan(rows)
    assert updates == [("Value Investing", "strategy_value_name", 1)]
    assert unknown == [{"name": "自定义策略", "count": 2}]


# --------------------------------------------------------------------------- #
# 异步：revision 门控、冲突门控、单事务写入 / 回滚、dry-run 不写
# --------------------------------------------------------------------------- #


async def test_migrate_execute_wraps_all_updates_in_one_transaction(install_fake_conn) -> None:
    """revision >= 0033 且无冲突时，所有 UPDATE 在同一个事务内执行。"""
    conn = install_fake_conn(
        _FakeConn(
            revision_rows=[{"version_num": "0033"}],
            table_rows=[_row(1, "Value Investing"), _row(2, "价值投资", run_id="run-b")],
        )
    )

    stats = await migrate_script.migrate(dry_run=False, db_url="postgresql://host/db")

    assert stats["revision_supported"] is True
    assert stats["executed"] is True
    assert stats["rows_to_update"] == 2
    assert stats["conflicts"] == []
    assert len(conn.execute_calls) == 2
    assert conn.events[0] == "tx_begin"
    assert conn.events[-1] == "tx_commit"
    assert conn.events[1:-1] == ["execute", "execute"]
    assert conn.execute_calls[0][1] == ("strategy_value_name", "Value Investing")
    assert conn.closed is True


async def test_migrate_execute_refused_on_legacy_revision(install_fake_conn) -> None:
    """revision 0024（三键 UK）时 --execute 不写任何行。"""
    conn = install_fake_conn(
        _FakeConn(revision_rows=[{"version_num": "0024"}], table_rows=[_row(1, "Value Investing")])
    )

    stats = await migrate_script.migrate(dry_run=False, db_url="postgresql://host/db")

    assert stats["revision"] == "0024"
    assert stats["revision_supported"] is False
    assert stats["conflict_key_scope"] == "legacy-3key"
    assert stats["executed"] is False
    assert conn.execute_calls == []
    assert conn.events == []


async def test_migrate_execute_refused_on_unknown_revision(install_fake_conn) -> None:
    """revision 未知（本 checkout 链中不存在）时 fail closed，不写入。"""
    conn = install_fake_conn(
        _FakeConn(revision_rows=[{"version_num": "9999"}], table_rows=[_row(1, "Value Investing")])
    )

    stats = await migrate_script.migrate(dry_run=False, db_url="postgresql://host/db")

    assert stats["revision_supported"] is False
    assert stats["conflict_key_scope"] is None
    assert stats["executed"] is False
    assert conn.execute_calls == []


async def test_migrate_execute_refused_when_alembic_version_missing(install_fake_conn) -> None:
    """alembic_version 表不存在（无表/无行）时视为未知 revision，--execute 拒绝。"""
    conn = install_fake_conn(
        _FakeConn(
            revision_error=asyncpg.UndefinedTableError("missing alembic_version"),
            table_rows=[_row(1, "Value Investing")],
        )
    )

    stats = await migrate_script.migrate(dry_run=False, db_url="postgresql://host/db")

    assert stats["revision"] is None
    assert stats["revision_supported"] is False
    assert stats["executed"] is False
    assert conn.execute_calls == []


async def test_migrate_execute_refused_on_normalized_key_conflicts(install_fake_conn) -> None:
    """归一化后键冲突时整批不写，并报告冲突行明细。"""
    conn = install_fake_conn(
        _FakeConn(
            revision_rows=[{"version_num": "0033"}],
            table_rows=[_row(1, "Value Investing", run_id="run-a"), _row(2, "strategy_value_name", run_id="run-a")],
        )
    )

    stats = await migrate_script.migrate(dry_run=False, db_url="postgresql://host/db")

    assert stats["executed"] is False
    assert len(stats["conflicts"]) == 1
    assert {member["id"] for member in stats["conflicts"][0]["rows"]} == {1, 2}
    assert conn.execute_calls == []
    assert conn.events == []


async def test_migrate_dry_run_never_writes(install_fake_conn) -> None:
    """dry-run 输出 revision / 冲突 / would-update 统计，但不执行任何 UPDATE。"""
    conn = install_fake_conn(
        _FakeConn(revision_rows=[{"version_num": "0033"}], table_rows=[_row(1, "Value Investing")])
    )

    stats = await migrate_script.migrate(dry_run=True, db_url="postgresql://host/db")

    assert stats["revision"] == "0033"
    assert stats["revision_supported"] is True
    assert stats["rows_to_update"] == 1
    assert stats["conflicts"] == []
    assert stats["executed"] is False
    assert conn.execute_calls == []
    assert conn.events == []


async def test_migrate_rolls_back_whole_batch_on_mid_transaction_failure(install_fake_conn) -> None:
    """事务中任一 UPDATE 失败时异常传播，事务回滚（不留下半批写入）。"""
    conn = install_fake_conn(
        _FakeConn(
            revision_rows=[{"version_num": "0033"}],
            table_rows=[_row(1, "Value Investing"), _row(2, "价值投资", run_id="run-b")],
            fail_on_update_index=1,
        )
    )

    with pytest.raises(RuntimeError, match="simulated mid-transaction failure"):
        await migrate_script.migrate(dry_run=False, db_url="postgresql://host/db")

    assert conn.events[-1] == "tx_rollback"
    assert conn.exit_exc_type is RuntimeError
    assert conn.closed is True


async def test_migrate_empty_table_produces_no_updates(install_fake_conn) -> None:
    """空表：无冲突、无 UPDATE，正常结束。"""
    conn = install_fake_conn(_FakeConn(revision_rows=[{"version_num": "0033"}], table_rows=[]))

    stats = await migrate_script.migrate(dry_run=False, db_url="postgresql://host/db")

    assert stats["scanned"] == 0
    assert stats["rows_to_update"] == 0
    assert stats["conflicts"] == []
    assert stats["executed"] is True
    assert conn.execute_calls == []


# --------------------------------------------------------------------------- #
# CLI：DSN 脱敏 R9 / 非零退出码
# --------------------------------------------------------------------------- #


def test_main_masks_plaintext_db_password(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """main 打印 DSN 时必须脱敏 userinfo 密码（R9），不得输出明文密码。"""

    async def _fake_migrate(dry_run: bool, db_url: str) -> dict[str, Any]:
        return _empty_stats()

    monkeypatch.setattr(migrate_script, "migrate", _fake_migrate)
    monkeypatch.setattr(
        sys,
        "argv",
        ["migrate_strategy_name_to_i18n_key.py", "--dry-run", "--db-url", "postgresql://user:s3cr3t@host:5432/db"],
    )

    migrate_script.main()

    out = capsys.readouterr().out
    assert "s3cr3t" not in out
    assert "postgresql://user:***@host:5432/db" in out


def test_main_exits_nonzero_on_conflicts(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """存在归一化冲突时 dry-run 也报告明细并退出非 0（默认不写）。"""
    conflicts = [
        {
            "key": (date(2024, 1, 2), "strategy_value_name", "000001.SZ", "run-a"),
            "rows": [
                {
                    "id": 1,
                    "strategy_name": "Value Investing",
                    "normalized_name": "strategy_value_name",
                    "will_update": True,
                },
                {
                    "id": 2,
                    "strategy_name": "strategy_value_name",
                    "normalized_name": "strategy_value_name",
                    "will_update": False,
                },
            ],
        }
    ]

    async def _fake_migrate(dry_run: bool, db_url: str) -> dict[str, Any]:
        return _empty_stats(conflicts=conflicts)

    monkeypatch.setattr(migrate_script, "migrate", _fake_migrate)
    monkeypatch.setattr(
        sys,
        "argv",
        ["migrate_strategy_name_to_i18n_key.py", "--dry-run", "--db-url", "postgresql://host/db"],
    )

    with pytest.raises(SystemExit) as exc_info:
        migrate_script.main()

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "[CONFLICT]" in captured.out
    assert "resolve the colliding rows manually" in captured.err


def test_main_exits_nonzero_when_execute_on_unsupported_revision(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """revision 不支持时 --execute 退出非 0，并提示先 upgrade head。"""

    async def _fake_migrate(dry_run: bool, db_url: str) -> dict[str, Any]:
        return _empty_stats(revision="0024", revision_supported=False, conflict_key_scope="legacy-3key")

    monkeypatch.setattr(migrate_script, "migrate", _fake_migrate)
    monkeypatch.setattr(
        sys,
        "argv",
        ["migrate_strategy_name_to_i18n_key.py", "--execute", "--db-url", "postgresql://host/db"],
    )

    with pytest.raises(SystemExit) as exc_info:
        migrate_script.main()

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "alembic upgrade head" in captured.out
