"""离线新闻语料库采集器（N1-4）。

按规格 §3.3 采集规范，对每个新闻源插件：**限速**（默认 2.5s/请求，≥2~3s）逐请求抓取 →
解析 → **清洗**（去 HTML/模板语、快讯截断 120 字、例行条目过滤）→ **近重复去重**（SimHash）
→ **幂等入 SQLite 语料库**；单源**连续失败 N 次自动熔断并告警**（沿用 ``news_fetcher`` 的
CLS 熔断模式）。语料仅用于本地训练与展示，不对外分发原文（规格 §3.3）。

各源适配（插件）见 ``data/external/news_sources/registry.py``；解析内核见同包
``sina_7x24`` / ``sina_stock`` / ``cninfo``；本脚本只做编排，不复制解析 / 清洗 / 去重逻辑。

Usage:
    # 新浪 7x24 + 巨潮，采集某区间各抓若干页
    python scripts/collect_corpus.py --db data/corpus/news_corpus.db \\
        --sources sina_7x24,cninfo --start 2026-09-01 --end 2026-09-30 --pages 3

    # 新浪个股需显式提供 symbol 清单（每行一个，如 sh600519）
    python scripts/collect_corpus.py --sources sina_stock --symbols-file symbols.txt --pages 2

    # 只读统计，不发网络请求、不写库
    python scripts/collect_corpus.py --db data/corpus/news_corpus.db --stats-only

退出码：成功 0；参数 / IO 异常 1。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402 - sys.path 注入后导入

from data.external.news_sources import dedup  # noqa: E402 - sys.path 注入后导入
from data.external.news_sources.circuit_breaker import (  # noqa: E402 - sys.path 注入后导入
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_FAILURE_THRESHOLD,
    CircuitBreaker,
)
from data.external.news_sources.cleaning import (  # noqa: E402 - sys.path 注入后导入
    CLEAN_REASON_EMPTY,
    clean_document,
)
from data.external.news_sources.corpus import CorpusStore  # noqa: E402 - sys.path 注入后导入
from data.external.news_sources.registry import (  # noqa: E402 - sys.path 注入后导入
    DEFAULT_RATE_LIMIT_SECONDS,
    CollectParams,
    SourcePlugin,
    all_plugins,
    get_plugin,
)
from utils.time_utils import get_now  # noqa: E402 - sys.path 注入后导入

DEFAULT_DB_PATH = "data/corpus/news_corpus.db"


async def _rate_limit_sleep(seconds: float) -> None:
    """请求间隔（独立函数便于单测注入替身）。"""
    await asyncio.sleep(seconds)


def _new_index() -> dedup.NearDuplicateIndex:
    return dedup.NearDuplicateIndex(threshold=dedup.DEFAULT_HAMMING_THRESHOLD, bands=dedup.BANDS)


def _seed_indexes(store: CorpusStore) -> dict[str, dedup.NearDuplicateIndex]:
    """用库内既有 simhash 重建近重复索引，保证重复运行不重复收录。"""
    indexes: dict[str, dedup.NearDuplicateIndex] = {}
    for source_kind, value in store.iter_simhashes():
        indexes.setdefault(source_kind, _new_index()).add(value)
    return indexes


def _process_docs(
    docs: list[dict[str, Any]],
    plugin: SourcePlugin,
    indexes: dict[str, dedup.NearDuplicateIndex],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """清洗 + 近重复去重；返回 ``(保留文档, 计数)``。

    去重按 ``source_kind`` 分域（news / announcement），避免跨内容类型误删。
    """
    stats = {"kept": 0, "dropped_empty": 0, "dropped_routine": 0, "dropped_dup": 0}
    index = indexes.setdefault(plugin.source_kind, _new_index())
    kept: list[dict[str, Any]] = []
    for doc in docs:
        cleaned, reason = clean_document(doc, text_kind=plugin.text_kind, drop_routine=plugin.drop_routine)
        if cleaned is None:
            stats["dropped_empty" if reason == CLEAN_REASON_EMPTY else "dropped_routine"] += 1
            continue
        value = dedup.simhash(cleaned["text"])
        if not index.add_if_unique(value):
            stats["dropped_dup"] += 1
            continue
        kept.append({**cleaned, "source_kind": plugin.source_kind, "simhash": value})
        stats["kept"] += 1
    return kept, stats


async def _collect_source(
    plugin: SourcePlugin,
    params: CollectParams,
    store: CorpusStore,
    indexes: dict[str, dedup.NearDuplicateIndex],
    *,
    rate_limit: float,
    failure_threshold: int,
    cooldown_seconds: float,
    dry_run: bool,
) -> dict[str, Any]:
    """采集并入库单个源；返回该源汇总（含熔断消耗统计）。"""
    queries = plugin.build_queries(params)
    summary: dict[str, Any] = {
        "source": plugin.name,
        "queries": len(queries),
        "ok_requests": 0,
        "failed_requests": 0,
        "skipped_queries": 0,
        "circuit_opened": False,
        "inserted": 0,
        "kept": 0,
        "dropped_empty": 0,
        "dropped_routine": 0,
        "dropped_dup": 0,
    }
    if not queries:
        print(f"[{plugin.name}] 无请求单元（sina_stock 需 --symbols / --symbols-file），跳过")
        return summary

    breaker = CircuitBreaker(plugin.name, threshold=failure_threshold, cooldown_seconds=cooldown_seconds)
    async with httpx.AsyncClient(**plugin.client_kwargs()) as client:
        for index, query in enumerate(queries):
            if not breaker.allow_request():
                summary["circuit_opened"] = True
                summary["skipped_queries"] = len(queries) - index
                break
            try:
                raw = await plugin.request(client, query)
            except Exception as exc:  # noqa: BLE001  CancelledError 属 BaseException，不在此捕获（R2）
                breaker.record_failure(exc)
                summary["failed_requests"] += 1
            else:
                breaker.record_success()
                summary["ok_requests"] += 1
                kept, proc_stats = _process_docs(plugin.parse(raw, query), plugin, indexes)
                for key, value in proc_stats.items():
                    summary[key] += value
                summary["inserted"] += len(kept) if dry_run else store.add_documents(kept)
            if index < len(queries) - 1:
                await _rate_limit_sleep(rate_limit)
    return summary


def _load_symbols(args: argparse.Namespace) -> tuple[str, ...]:
    """合并 ``--symbols``（逗号分隔）与 ``--symbols-file``（每行一个），去重保序。"""
    symbols: list[str] = []
    if args.symbols:
        symbols.extend(part.strip() for part in args.symbols.split(",") if part.strip())
    if args.symbols_file:
        text = Path(args.symbols_file).read_text(encoding="utf-8")
        symbols.extend(line.strip() for line in text.splitlines() if line.strip())
    seen: set[str] = set()
    ordered: list[str] = []
    for symbol in symbols:
        if symbol not in seen:
            seen.add(symbol)
            ordered.append(symbol)
    return tuple(ordered)


def _print_stats(store: CorpusStore) -> None:
    stats = store.stats()
    print(f"语料库 {store.db_path} 共 {stats['total']} 条")
    print(f"  按源: {stats['by_source']}")
    print(f"  按类型: {stats['by_kind']}")
    print(f"  发布时间范围(UTC naive): {stats['publish_time_min']} ~ {stats['publish_time_max']}")


async def _run(args: argparse.Namespace) -> int:
    with CorpusStore(args.db) as store:
        if args.stats_only:
            _print_stats(store)
            return 0

        sources = [name.strip() for name in args.sources.split(",") if name.strip()]
        plugins = [get_plugin(name) for name in sources]
        params = CollectParams(
            pages=args.pages,
            page_size=args.page_size,
            start_date=args.start,
            end_date=args.end,
            symbols=_load_symbols(args),
            columns=tuple(col.strip() for col in args.columns.split(",") if col.strip()),
            search_key=args.search_key,
        )
        indexes = _seed_indexes(store)

        print(f"开始采集（dry-run={args.dry_run}，限速 {args.rate_limit}s/请求）: {sources}")
        for plugin in plugins:
            summary = await _collect_source(
                plugin,
                params,
                store,
                indexes,
                rate_limit=args.rate_limit,
                failure_threshold=args.failure_threshold,
                cooldown_seconds=args.cooldown,
                dry_run=args.dry_run,
            )
            circuit = "，已熔断跳过剩余请求" if summary["circuit_opened"] else ""
            print(
                f"[{summary['source']}] 请求 {summary['ok_requests']} 成功 / {summary['failed_requests']} 失败"
                f"{circuit}；保留 {summary['kept']}（空 {summary['dropped_empty']}、例行 {summary['dropped_routine']}、"
                f"近重复 {summary['dropped_dup']}）；入库 {summary['inserted']}"
            )

        if not args.dry_run:
            _print_stats(store)
        else:
            print("dry-run：未写入语料库")
    return 0


def main(argv: list[str] | None = None) -> int:
    today = get_now().date().isoformat()
    parser = argparse.ArgumentParser(description="离线新闻语料库采集器（N1-4）")
    parser.add_argument("--db", type=Path, default=Path(DEFAULT_DB_PATH), help="SQLite 语料库路径")
    parser.add_argument(
        "--sources",
        default=",".join(plugin.name for plugin in all_plugins()),
        help="逗号分隔的源（sina_7x24/sina_stock/cninfo）",
    )
    parser.add_argument("--start", default=today, help="起始日期 YYYY-MM-DD（仅巨潮使用）")
    parser.add_argument("--end", default=today, help="结束日期 YYYY-MM-DD（仅巨潮使用）")
    parser.add_argument("--pages", type=int, default=1, help="每个请求序列抓取页数")
    parser.add_argument("--page-size", type=int, default=None, help="每页条数（缺省用各源默认值）")
    parser.add_argument("--columns", default="", help="巨潮板块列（逗号分隔，缺省 szse）")
    parser.add_argument("--search-key", default="", help="巨潮关键词检索（非空时命中片段加 <em>）")
    parser.add_argument("--symbols", default="", help="新浪个股 symbol 清单（逗号分隔，如 sh600519,sz000001）")
    parser.add_argument("--symbols-file", default=None, help="新浪个股 symbol 清单文件（每行一个）")
    parser.add_argument(
        "--rate-limit", type=float, default=DEFAULT_RATE_LIMIT_SECONDS, help="每请求间隔秒数（规格 ≥2~3s）"
    )
    parser.add_argument("--failure-threshold", type=int, default=DEFAULT_FAILURE_THRESHOLD, help="单源连续失败熔断阈值")
    parser.add_argument("--cooldown", type=float, default=DEFAULT_COOLDOWN_SECONDS, help="熔断冷却秒数")
    parser.add_argument("--dry-run", action="store_true", help="只清洗/去重计数，不写入语料库")
    parser.add_argument("--stats-only", action="store_true", help="只打印语料库统计，不发网络请求")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
