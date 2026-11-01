"""scripts/migrate_strategy_name_to_i18n_key.py 单测。

覆盖：历史 ``strategy_name`` → i18n key 映射（幂等 / 未知值透传）、SQLAlchemy → asyncpg
DSN 归一化，以及 ``main`` 打印 DSN 前必须脱敏（R9：不得输出明文数据库密码）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.meta]

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import migrate_strategy_name_to_i18n_key as migrate_script  # noqa: E402 - sys.path 注入后导入


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


def test_main_masks_plaintext_db_password(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """main 打印 DSN 时必须脱敏 userinfo 密码（R9），不得输出明文密码。"""

    async def _fake_migrate(dry_run: bool, db_url: str) -> dict[str, Any]:
        return {"scanned": 0, "migrated": 0, "skipped_already_i18n_key": 0, "unknown": []}

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
