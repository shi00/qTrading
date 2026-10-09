"""离线新闻语料库采集器编排单测（N1-4）。

覆盖：采集器插件注册表（内置插件清单 / 未知源报错 / 限速 ≥2s / 各源 ``build_queries`` 展开）、
编排函数（清洗 + 去重计数 ``_process_docs``、跨运行索引重建 ``_seed_indexes``、symbol 合并
``_load_symbols``）、端到端 ``main``（假插件：写库 / 跨请求去重 / dry-run / stats-only）与单源
连续失败熔断跳过剩余请求（``_collect_source``）。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import collect_corpus  # noqa: E402 - sys.path 注入后导入
from data.external.news_sources import cninfo  # noqa: E402 - sys.path 注入后导入
from data.external.news_sources.corpus import CorpusStore  # noqa: E402 - sys.path 注入后导入
from data.external.news_sources.registry import (  # noqa: E402 - sys.path 注入后导入
    DEFAULT_RATE_LIMIT_SECONDS,
    CollectParams,
    CollectQuery,
    SourcePlugin,
    all_plugins,
    get_plugin,
)

pytestmark = [pytest.mark.unit, pytest.mark.no_auto_mock]


class _FakePlugin(SourcePlugin):
    """测试替身：``build_queries`` 按 payload 数展开，``request`` 直接返回预置 payload。"""

    name = "fake"
    source_kind = "news"
    text_kind = "title"
    rate_limit_seconds = DEFAULT_RATE_LIMIT_SECONDS
    drop_routine = False

    def __init__(self, payloads: list[list[dict[str, Any]]], *, fail_at: set[int] | None = None) -> None:
        self._payloads = payloads
        self._fail_at = fail_at or set()
        self.calls = 0

    def build_queries(self, params: CollectParams) -> list[CollectQuery]:
        return [CollectQuery(page=index + 1) for index in range(len(self._payloads))]

    def client_kwargs(self) -> dict[str, Any]:
        return {}

    async def request(self, client: httpx.AsyncClient, query: CollectQuery) -> Any:
        await asyncio.sleep(0)  # 保持异步 IO 语义（RUF029）
        index = self.calls
        self.calls += 1
        if index in self._fail_at:
            raise RuntimeError("boom")
        return self._payloads[index]

    def parse(self, raw: Any, query: CollectQuery) -> list[dict[str, Any]]:
        return raw


class _RoutinePlugin(_FakePlugin):
    """带例行条目过滤的替身（对应巨潮 ``drop_routine=True``）。"""

    drop_routine = True


def _doc(source_id: str, text: str, *, source: str = "sina_7x24") -> dict[str, Any]:
    return {"source": source, "source_id": source_id, "text": text}


def _store_doc(source_id: str, text: str, simhash: int | None, *, source_kind: str = "news") -> dict[str, Any]:
    return {
        "source": "sina_7x24",
        "source_id": source_id,
        "source_kind": source_kind,
        "text": text,
        "simhash": simhash,
    }


async def _no_sleep(_seconds: float) -> None:
    await asyncio.sleep(0)


def _run_main(monkeypatch: pytest.MonkeyPatch, fake: _FakePlugin, tmp_path: Path, *extra: str) -> int:
    monkeypatch.setattr(collect_corpus, "get_plugin", lambda name: fake)
    monkeypatch.setattr(collect_corpus, "_rate_limit_sleep", _no_sleep)
    argv = ["--db", str(tmp_path / "corpus.db"), "--sources", "fake", *extra]
    return collect_corpus.main(argv)


# --------------------------------------------------------------------------- #
# 采集器插件注册表（registry）
# --------------------------------------------------------------------------- #
def test_all_plugins_lists_builtin_sources():
    assert {plugin.name for plugin in all_plugins()} == {"sina_7x24", "sina_stock", "cninfo"}


def test_all_plugins_returns_fresh_instances():
    first, second = all_plugins(), all_plugins()
    assert first[0] is not second[0]


def test_get_plugin_returns_expected_kinds():
    flash = get_plugin("sina_7x24")
    assert (flash.source_kind, flash.text_kind, flash.drop_routine) == ("news", "flash", False)
    announcement = get_plugin("cninfo")
    assert (announcement.source_kind, announcement.text_kind, announcement.drop_routine) == (
        "announcement",
        "title",
        True,
    )


def test_get_plugin_unknown_source_raises():
    with pytest.raises(ValueError, match="未知新闻源"):
        get_plugin("not_a_source")


def test_builtin_plugins_rate_limit_meets_spec():
    # 规格 §3.3：每源每请求间隔 ≥ 2~3 秒
    for plugin in all_plugins():
        assert plugin.rate_limit_seconds >= 2.0


def test_sina7x24_build_queries_paginates():
    queries = get_plugin("sina_7x24").build_queries(CollectParams(pages=3, page_size=50))
    assert [query.page for query in queries] == [1, 2, 3]
    assert all(query.page_size == 50 for query in queries)


def test_sina_stock_build_queries_expands_symbols_and_pages():
    queries = get_plugin("sina_stock").build_queries(CollectParams(pages=2, symbols=("sh600519", "sz000001")))
    assert [(query.symbol, query.page) for query in queries] == [
        ("sh600519", 1),
        ("sh600519", 2),
        ("sz000001", 1),
        ("sz000001", 2),
    ]


def test_sina_stock_build_queries_empty_without_symbols():
    assert get_plugin("sina_stock").build_queries(CollectParams(pages=2)) == []


def test_cninfo_build_queries_expands_columns_and_propagates_params():
    queries = get_plugin("cninfo").build_queries(
        CollectParams(
            pages=2,
            columns=("szse", "sse"),
            start_date="2026-09-01",
            end_date="2026-09-30",
            search_key="回购",
        )
    )
    assert [(query.column, query.page) for query in queries] == [("szse", 1), ("szse", 2), ("sse", 1), ("sse", 2)]
    assert all(
        query.start_date == "2026-09-01" and query.end_date == "2026-09-30" and query.search_key == "回购"
        for query in queries
    )


def test_cninfo_build_queries_defaults_column():
    queries = get_plugin("cninfo").build_queries(CollectParams(pages=1))
    assert queries[0].column == cninfo.DEFAULT_COLUMN


# --------------------------------------------------------------------------- #
# _process_docs：清洗 + 近重复去重
# --------------------------------------------------------------------------- #
def test_process_docs_cleans_and_dedups():
    docs = [_doc("1", "<b>甲</b>"), _doc("2", "甲"), _doc("3", "   "), _doc("4", "乙")]
    kept, stats = collect_corpus._process_docs(docs, _FakePlugin([]), {})
    assert [doc["source_id"] for doc in kept] == ["1", "4"]
    assert stats == {"kept": 2, "dropped_empty": 1, "dropped_routine": 0, "dropped_dup": 1}
    assert kept[0]["text"] == "甲"  # HTML 标签已剥离
    assert kept[0]["source_kind"] == "news" and "simhash" in kept[0]


def test_process_docs_drops_routine_when_enabled():
    docs = [_doc("1", "H股公告-翌日披露报表"), _doc("2", "重大事项公告")]
    kept, stats = collect_corpus._process_docs(docs, _RoutinePlugin([]), {})
    assert [doc["source_id"] for doc in kept] == ["2"]
    assert stats["dropped_routine"] == 1


def test_process_docs_keeps_distinct_text():
    docs = [_doc("1", "完全不同的第一条内容"), _doc("2", "毫不相关的第二条内容")]
    kept, stats = collect_corpus._process_docs(docs, _FakePlugin([]), {})
    assert len(kept) == 2
    assert stats["dropped_dup"] == 0


# --------------------------------------------------------------------------- #
# _seed_indexes / _load_symbols
# --------------------------------------------------------------------------- #
def test_seed_indexes_rebuilds_from_store(tmp_path: Path):
    with CorpusStore(tmp_path / "c.db") as store:
        store.add_documents([_store_doc("1", "正文一", 111), _store_doc("2", "正文二", 222)])
        indexes = collect_corpus._seed_indexes(store)
    assert indexes["news"].size == 2
    assert indexes["news"].find_duplicate(111) == 111


def test_load_symbols_merges_and_dedups(tmp_path: Path):
    symbol_file = tmp_path / "symbols.txt"
    symbol_file.write_text("sz000001\nbj920670\n\n", encoding="utf-8")
    args = argparse.Namespace(symbols="sh600519, sz000001", symbols_file=str(symbol_file))
    assert collect_corpus._load_symbols(args) == ("sh600519", "sz000001", "bj920670")


def test_load_symbols_empty_returns_empty_tuple():
    args = argparse.Namespace(symbols="", symbols_file=None)
    assert collect_corpus._load_symbols(args) == ()


# --------------------------------------------------------------------------- #
# 端到端 main（假插件）
# --------------------------------------------------------------------------- #
def test_main_end_to_end_inserts_documents(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    fake = _FakePlugin([[_doc("1", "正文一")], [_doc("2", "正文二")]])
    assert _run_main(monkeypatch, fake, tmp_path) == 0
    with CorpusStore(tmp_path / "corpus.db") as store:
        assert store.count() == 2


def test_main_dedups_across_requests(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    fake = _FakePlugin([[_doc("1", "同一事件")], [_doc("2", "同一事件")]])
    assert _run_main(monkeypatch, fake, tmp_path) == 0
    with CorpusStore(tmp_path / "corpus.db") as store:
        assert store.count() == 1


def test_main_dry_run_does_not_write(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    fake = _FakePlugin([[_doc("1", "正文一")]])
    assert _run_main(monkeypatch, fake, tmp_path, "--dry-run") == 0
    with CorpusStore(tmp_path / "corpus.db") as store:
        assert store.count() == 0


def test_main_stats_only_skips_collection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    def _fail(_name: str) -> Any:
        pytest.fail("stats-only 不应解析新闻源")

    monkeypatch.setattr(collect_corpus, "get_plugin", _fail)
    argv = ["--db", str(tmp_path / "corpus.db"), "--stats-only"]
    assert collect_corpus.main(argv) == 0


# --------------------------------------------------------------------------- #
# _collect_source：计数、入库与熔断
# --------------------------------------------------------------------------- #
async def test_collect_source_counts_and_inserts(tmp_path: Path):
    fake = _FakePlugin([[_doc("1", "甲")], [_doc("2", "乙")]])
    with CorpusStore(tmp_path / "c.db") as store:
        summary = await collect_corpus._collect_source(
            fake,
            CollectParams(pages=2),
            store,
            {},
            rate_limit=0.0,
            failure_threshold=3,
            cooldown_seconds=60.0,
            dry_run=False,
        )
        assert store.count() == 2
    assert (summary["ok_requests"], summary["kept"], summary["inserted"]) == (2, 2, 2)
    assert summary["circuit_opened"] is False


async def test_collect_source_circuit_opens_and_skips_remaining(tmp_path: Path):
    payloads = [[_doc(str(index), f"文本{index}")] for index in range(5)]
    fake = _FakePlugin(payloads, fail_at=set(range(5)))
    with CorpusStore(tmp_path / "c.db") as store:
        summary = await collect_corpus._collect_source(
            fake,
            CollectParams(pages=5),
            store,
            {},
            rate_limit=0.0,
            failure_threshold=2,
            cooldown_seconds=60.0,
            dry_run=True,
        )
    assert summary["failed_requests"] == 2  # 达阈值即熔断，不再发第 3 个请求
    assert summary["ok_requests"] == 0
    assert summary["circuit_opened"] is True
    assert summary["skipped_queries"] == 3
