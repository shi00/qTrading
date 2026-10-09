"""SQLite 语料库（N1-4，规格 §3.3 / §4）。

离线新闻语料的本地存储：每条归一化文档按 ``(source, source_id)`` 主键**幂等**入库（同一采集
带宽内去重键唯一，重复运行不重复写入）。语料仅用于本地训练与展示，不对外分发原文（规格 §3.3）。

设计取舍
--------
- 采用标准库 ``sqlite3`` 而非应用 PostgreSQL：语料属**离线训练资产**，与应用运行时库解耦，
  不进入 ORM / alembic 迁移链（故不触发 R12 的表注册约束，R12 只比对 ``models.py`` 与
  ``data_dictionary.TABLE_DEFINITIONS``）。
- SQL 一律参数化（``?`` 占位符），不拼接字面量（R4）。
- ``publish_time`` 为解析内核产出的 **UTC tz-naive**，以 ISO 字符串存储；缺失存 ``NULL``
  （R21：不以 epoch / 0 伪装缺失）。
- ``simhash`` 为**无符号** 64 位，超出 SQLite INTEGER（有符号 64 位）范围，落库时按 two's
  complement 映射、读回还原，保证无损往返。

本模块位于 ``data/`` 层，仅依赖标准库与 ``utils/``。
"""

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from utils.time_utils import get_now

_SCHEMA_DOCUMENTS = """
CREATE TABLE IF NOT EXISTS corpus_documents (
    source        TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    source_kind   TEXT NOT NULL,
    text          TEXT NOT NULL,
    publish_time  TEXT,
    source_url    TEXT,
    ts_code       TEXT,
    tags          TEXT,
    content_hash  TEXT,
    simhash       INTEGER,
    collected_at  TEXT NOT NULL,
    PRIMARY KEY (source, source_id)
)
"""

_SCHEMA_INDEX = "CREATE INDEX IF NOT EXISTS idx_corpus_publish_time ON corpus_documents(publish_time)"

_INSERT_SQL = """
INSERT OR IGNORE INTO corpus_documents
    (source, source_id, source_kind, text, publish_time, source_url, ts_code, tags, content_hash, simhash, collected_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

# SimHash 为无符号 64 位，取值可达 2^64-1，超出 SQLite INTEGER（有符号 64 位）范围，
# 直接绑定会触发 OverflowError；落库按 two's complement 映射、读回还原。
_SQLITE_INT_MAX = 2**63 - 1
_TWO_POW_64 = 2**64


def content_hash(text: object) -> str:
    """内容哈希（SHA-256）：用于应用层可见的精确重复标识。"""
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _simhash_to_sqlite(value: int | None) -> int | None:
    """无符号 64 位指纹 → SQLite 可存的有符号 64 位（two's complement）；``None`` 透传。"""
    if value is None:
        return None
    number = int(value)
    return number - _TWO_POW_64 if number > _SQLITE_INT_MAX else number


def _simhash_from_sqlite(value: int) -> int:
    """SQLite 有符号 64 位 → 无符号 64 位指纹（还原 :func:`_simhash_to_sqlite`）。"""
    return value + _TWO_POW_64 if value < 0 else value


class CorpusStore:
    """本地 SQLite 语料库（非线程安全；单线程离线采集器使用）。"""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    @property
    def db_path(self) -> Path:
        return self._db_path

    def open(self) -> None:
        """建立连接（必要时创建父目录）并确保 schema 就绪。"""
        if self._conn is not None:
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_SCHEMA_DOCUMENTS)
        self._conn.execute(_SCHEMA_INDEX)
        self._conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "CorpusStore":
        self.open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("CorpusStore 未打开，请先调用 open() 或使用 with 语句")
        return self._conn

    def add_documents(self, docs: Iterable[dict[str, Any]]) -> int:
        """批量幂等入库，返回本次**新插入**条数（主键冲突忽略）。

        每条文档需含 ``source`` / ``source_id`` / ``source_kind`` / ``text``；其余字段可缺省。
        """
        conn = self._connection()
        collected_at = get_now().isoformat()
        rows: list[tuple[Any, ...]] = []
        for doc in docs:
            publish_time = doc.get("publish_time")
            tags = doc.get("tags")
            rows.append(
                (
                    str(doc["source"]),
                    str(doc["source_id"]),
                    str(doc["source_kind"]),
                    str(doc["text"]),
                    publish_time.isoformat() if publish_time is not None else None,
                    doc.get("source_url") or None,
                    doc.get("ts_code") or None,
                    json.dumps(tags, ensure_ascii=False) if tags else None,
                    content_hash(doc["text"]),
                    _simhash_to_sqlite(doc.get("simhash")),
                    collected_at,
                )
            )
        if not rows:
            return 0
        before = conn.total_changes
        conn.executemany(_INSERT_SQL, rows)
        conn.commit()
        return conn.total_changes - before

    def count(self, *, source: str | None = None) -> int:
        """文档总数（可按源过滤）。"""
        conn = self._connection()
        if source is None:
            row = conn.execute("SELECT COUNT(*) AS n FROM corpus_documents").fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) AS n FROM corpus_documents WHERE source = ?", (source,)).fetchone()
        return int(row["n"])

    def iter_documents(self) -> Iterator[dict[str, Any]]:
        """迭代全部文档字典（用于离线标注 / 抽样 / 训练切分等只读消费）。

        返回字段与入库一致（``publish_time`` / ``tags`` / ``simhash`` 为 None 时原样透传，
        不做默认值伪装，R21）。
        """
        conn = self._connection()
        cursor = conn.execute(
            "SELECT source, source_id, source_kind, text, publish_time, source_url, ts_code, tags, simhash, content_hash "
            "FROM corpus_documents"
        )
        for row in cursor:
            value = dict(row)
            value["simhash"] = _simhash_from_sqlite(int(value["simhash"])) if value["simhash"] is not None else None
            yield value

    def iter_simhashes(self) -> Iterator[tuple[str, int]]:
        """迭代 ``(source_kind, simhash)``；用于跨运行重建近重复索引（simhash 为 NULL 时跳过）。"""
        conn = self._connection()
        cursor = conn.execute("SELECT source_kind, simhash FROM corpus_documents WHERE simhash IS NOT NULL")
        for row in cursor:
            yield str(row["source_kind"]), _simhash_from_sqlite(int(row["simhash"]))

    def stats(self) -> dict[str, Any]:
        """概览统计：总数、按源计数、发布时间范围（UTC naive ISO 字符串）。"""
        conn = self._connection()
        total = self.count()
        by_source = {
            str(row["source"]): int(row["n"])
            for row in conn.execute("SELECT source, COUNT(*) AS n FROM corpus_documents GROUP BY source")
        }
        by_kind = {
            str(row["source_kind"]): int(row["n"])
            for row in conn.execute("SELECT source_kind, COUNT(*) AS n FROM corpus_documents GROUP BY source_kind")
        }
        range_row = conn.execute(
            "SELECT MIN(publish_time) AS lo, MAX(publish_time) AS hi FROM corpus_documents"
        ).fetchone()
        return {
            "total": total,
            "by_source": by_source,
            "by_kind": by_kind,
            "publish_time_min": range_row["lo"],
            "publish_time_max": range_row["hi"],
        }
