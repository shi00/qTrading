"""SQLite 语料库单测（N1-4）。

覆盖：schema 幂等初始化、幂等入库（主键去重）、计数 / 统计、simhash 迭代、空输入、
未打开守卫、缺失时间存 NULL（R21）。
"""

import datetime
import json
import sqlite3
from pathlib import Path

import pytest

from data.external.news_sources.corpus import CorpusStore, content_hash

pytestmark = [pytest.mark.unit, pytest.mark.no_auto_mock]

_PUBLISH_TIME = datetime.datetime(2026, 9, 28, 5, 30, 0)  # UTC tz-naive（与解析内核口径一致）


def _doc(source: str = "sina_7x24", source_id: str = "1", **overrides: object) -> dict:
    doc: dict = {
        "source": source,
        "source_id": source_id,
        "source_kind": "news",
        "text": "示例正文",
        "publish_time": _PUBLISH_TIME,
        "source_url": "https://example.com/1",
        "ts_code": "600519.SH",
        "tags": ["要闻"],
        "simhash": 12345,
    }
    doc.update(overrides)
    return doc


def test_open_creates_schema_and_is_idempotent(tmp_path: Path):
    db = tmp_path / "corpus.db"
    with CorpusStore(db) as store:
        assert db.exists()
        assert store.count() == 0
    # 再次打开不应报错（IF NOT EXISTS）
    with CorpusStore(db) as store:
        assert store.count() == 0


def test_add_documents_inserts_and_returns_count(tmp_path: Path):
    with CorpusStore(tmp_path / "c.db") as store:
        assert store.add_documents([_doc(source_id="1"), _doc(source_id="2")]) == 2
        assert store.count() == 2


def test_add_documents_ignores_primary_key_duplicates(tmp_path: Path):
    with CorpusStore(tmp_path / "c.db") as store:
        assert store.add_documents([_doc(source_id="1")]) == 1
        assert store.add_documents([_doc(source_id="1", text="改写正文")]) == 0
        assert store.count() == 1


def test_add_documents_empty_returns_zero(tmp_path: Path):
    with CorpusStore(tmp_path / "c.db") as store:
        assert store.add_documents([]) == 0


def test_add_documents_persists_expected_columns(tmp_path: Path):
    db = tmp_path / "c.db"
    with CorpusStore(db) as store:
        store.add_documents([_doc()])
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT source, source_kind, text, publish_time, ts_code, tags, content_hash, simhash FROM corpus_documents"
    ).fetchone()
    conn.close()
    assert row["source"] == "sina_7x24"
    assert row["source_kind"] == "news"
    assert row["text"] == "示例正文"
    assert row["publish_time"] == _PUBLISH_TIME.isoformat()
    assert row["ts_code"] == "600519.SH"
    assert json.loads(row["tags"]) == ["要闻"]
    assert row["content_hash"] == content_hash("示例正文")
    assert row["simhash"] == 12345


def test_missing_publish_time_stored_as_null(tmp_path: Path):
    with CorpusStore(tmp_path / "c.db") as store:
        store.add_documents([_doc(publish_time=None, tags=None, simhash=None)])
        stats = store.stats()
        assert stats["publish_time_min"] is None
        assert stats["publish_time_max"] is None


def test_count_by_source_and_stats(tmp_path: Path):
    with CorpusStore(tmp_path / "c.db") as store:
        store.add_documents([_doc(source="sina_7x24", source_id="1"), _doc(source="sina_7x24", source_id="2")])
        store.add_documents([_doc(source="cninfo", source_id="9", source_kind="announcement")])
        assert store.count(source="sina_7x24") == 2
        assert store.count(source="cninfo") == 1
        stats = store.stats()
        assert stats["total"] == 3
        assert stats["by_source"] == {"sina_7x24": 2, "cninfo": 1}
        assert stats["by_kind"] == {"news": 2, "announcement": 1}
        assert stats["publish_time_min"] == _PUBLISH_TIME.isoformat()


def test_iter_simhashes_skips_null(tmp_path: Path):
    with CorpusStore(tmp_path / "c.db") as store:
        store.add_documents([_doc(source_id="1", simhash=111), _doc(source_id="2", simhash=None)])
        pairs = sorted(store.iter_simhashes())
        assert pairs == [("news", 111)]


@pytest.mark.parametrize("value", [0, 12345, 2**63 - 1, 2**63, 2**64 - 1])
def test_simhash_unsigned_round_trips(tmp_path: Path, value: int):
    # SimHash 为无符号 64 位，超出 SQLite 有符号 64 位范围，须无损往返（two's complement）
    with CorpusStore(tmp_path / "c.db") as store:
        store.add_documents([_doc(source_id="1", simhash=value)])
        assert [simhash for _kind, simhash in store.iter_simhashes()] == [value]


def test_unopened_store_raises(tmp_path: Path):
    store = CorpusStore(tmp_path / "c.db")
    with pytest.raises(RuntimeError, match="未打开"):
        store.count()


def test_double_open_is_noop(tmp_path: Path):
    store = CorpusStore(tmp_path / "c.db")
    store.open()
    store.open()
    assert store.count() == 0
    store.close()
