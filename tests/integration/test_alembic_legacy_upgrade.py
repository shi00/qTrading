import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _make_cfg(db_url: str, alembic_ini_path: str = "alembic.ini") -> Config:
    cfg = Config(alembic_ini_path)
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


@pytest.fixture
def legacy_db(tmp_path, monkeypatch):
    import config as cfg_mod

    monkeypatch.setattr(cfg_mod, "DB_URL", None)
    db_url = f"sqlite:///{tmp_path}/legacy.db"
    eng = create_engine(db_url)
    with eng.begin() as conn:
        conn.execute(
            text(
                """
            CREATE TABLE daily_quotes (
                ts_code VARCHAR, trade_date DATE,
                open REAL, high REAL, low REAL, close REAL,
                qfq_open REAL, qfq_high REAL, qfq_low REAL, qfq_close REAL,
                PRIMARY KEY (ts_code, trade_date)
            )
        """
            )
        )
        conn.execute(
            text(
                """
            CREATE TABLE screening_history (
                id INTEGER PRIMARY KEY,
                ts_code VARCHAR,
                created_at TIMESTAMP,
                t1_pct REAL,
                prediction_result VARCHAR
            )
        """
            )
        )
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR PRIMARY KEY)"))
    return db_url


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    import config as cfg_mod

    monkeypatch.setattr(cfg_mod, "DB_URL", None)
    return f"sqlite:///{tmp_path}/fresh.db"


def test_legacy_upgrade_adds_screening_history_new_columns(legacy_db, monkeypatch):
    import config as cfg_mod

    monkeypatch.setattr(cfg_mod, "DB_URL", legacy_db)
    command.upgrade(_make_cfg(legacy_db), "head")
    eng = create_engine(legacy_db)
    cols = {c["name"] for c in inspect(eng).get_columns("screening_history")}
    for must in ("t1_price", "t5_price", "t5_pct", "alpha", "index_pct", "review_status"):
        assert must in cols, f"Missing column after legacy upgrade: {must}"


def test_legacy_upgrade_drops_qfq_columns(legacy_db, monkeypatch):
    import config as cfg_mod

    monkeypatch.setattr(cfg_mod, "DB_URL", legacy_db)
    command.upgrade(_make_cfg(legacy_db), "head")
    eng = create_engine(legacy_db)
    cols = {c["name"] for c in inspect(eng).get_columns("daily_quotes")}
    for legacy in ("qfq_open", "qfq_high", "qfq_low", "qfq_close"):
        assert legacy not in cols, f"Legacy column not removed: {legacy}"


def test_fresh_db_upgrade_still_works(fresh_db, monkeypatch):
    import config as cfg_mod

    monkeypatch.setattr(cfg_mod, "DB_URL", fresh_db)
    command.upgrade(_make_cfg(fresh_db), "head")
    eng = create_engine(fresh_db)
    tables = set(inspect(eng).get_table_names())
    assert {"daily_quotes", "screening_history", "task_history", "app_state"}.issubset(tables)
    dq_cols = {c["name"] for c in inspect(eng).get_columns("daily_quotes")}
    for qfq in ("qfq_open", "qfq_high", "qfq_low", "qfq_close"):
        assert qfq not in dq_cols


def test_legacy_upgrade_creates_screening_thinking(legacy_db, monkeypatch):
    import config as cfg_mod

    monkeypatch.setattr(cfg_mod, "DB_URL", legacy_db)
    command.upgrade(_make_cfg(legacy_db), "head")
    eng = create_engine(legacy_db)
    tables = set(inspect(eng).get_table_names())
    assert "screening_thinking" in tables


def test_idempotent_upgrade_rerun(legacy_db, monkeypatch):
    import config as cfg_mod

    monkeypatch.setattr(cfg_mod, "DB_URL", legacy_db)
    cfg = _make_cfg(legacy_db)
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")
    eng = create_engine(legacy_db)
    cols = {c["name"] for c in inspect(eng).get_columns("screening_history")}
    assert "alpha" in cols
