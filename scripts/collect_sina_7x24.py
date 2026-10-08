"""新浪财经 7x24 快讯离线采集器（N1-1）。

按规格 §3.3 采集规范抓取 ``zhibo`` feed 的原始 JSON 并落盘，供解析内核的单测与
后续语料库构建（N1-4）复用；同时打印解析摘要（去重键条数 / 关联标的 market 分布 /
A 股 ``ts_code`` 映射样例 / 指数条数），用于人工核对映射规则。

- 限速：每请求间隔 ≥ :data:`RATE_LIMIT_SECONDS`（规格 §3.3「每源 ≥ 2~3 秒 / 请求」）。
- 只发抓取与落盘，不做清洗 / 去重 / 入库（属 N1-4 语料库职责）。
- 解析内核在 ``data/external/news_sources/sina_7x24.py``；本脚本不复制解析逻辑。

Usage:
    # 抓 1 页原始 JSON 落到 tests/fixtures 目录前的临时目录（离线核对用）
    python scripts/collect_sina_7x24.py --pages 1 --out D:\\tmp\\n1_1_samples

    # 仅解析已录制的 JSON（不发网络请求）
    python scripts/collect_sina_7x24.py --parse D:\\tmp\\n1_1_samples\\sina_7x24_page1.json

退出码：成功 0；网络 / 解析异常 1。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402 - sys.path 注入后导入

from data.external.news_sources import sina_7x24  # noqa: E402 - sys.path 注入后导入

RATE_LIMIT_SECONDS = 2.5


def _summarize(docs: list[dict[str, Any]], page_info: dict[str, Any]) -> None:
    """打印一页解析摘要（人工核对映射规则用）。"""
    market_counter: Counter[str] = Counter()
    ts_code_samples: list[str] = []
    index_samples: list[str] = []
    for doc in docs:
        for stock in doc["stocks"]:
            market_counter[stock["market"]] += 1
            if stock["ts_code"] and len(ts_code_samples) < 8:
                ts_code_samples.append(f"{stock['symbol']}->{stock['ts_code']}({stock['name']})")
            if stock["market"] == "cn" and stock["is_index"] and len(index_samples) < 8:
                index_samples.append(f"{stock['symbol']}({stock['name']})")
    print(f"  条目数: {len(docs)}   page_info: {json.dumps(page_info, ensure_ascii=False)}")
    print(f"  关联标的 market 分布: {dict(market_counter)}")
    print(f"  A 股 ts_code 样例(前 8): {ts_code_samples}")
    print(f"  cn 指数/板块样例(前 8): {index_samples}")


async def _fetch_pages(pages: int, page_size: int) -> list[tuple[int, dict[str, Any]]]:
    """按限速顺序抓取 pages 页，返回 ``[(page, payload), ...]``（不落盘、不解析）。"""
    results: list[tuple[int, dict[str, Any]]] = []
    async with httpx.AsyncClient(**sina_7x24.build_client_kwargs()) as client:
        for page in range(1, pages + 1):
            payload = await sina_7x24.fetch_page(client, page, page_size)
            results.append((page, payload))
            if page < pages:
                await asyncio.sleep(RATE_LIMIT_SECONDS)
    return results


def _parse_recorded(path: Path) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    docs, page_info = sina_7x24.parse_feed_payload(payload)
    print(f"[parse] {path}")
    _summarize(docs, page_info)
    return len(docs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="新浪财经 7x24 快讯离线采集器（N1-1）")
    parser.add_argument("--pages", type=int, default=1, help="抓取页数（默认 1）")
    parser.add_argument("--page-size", type=int, default=sina_7x24.DEFAULT_PAGE_SIZE, help="每页条数")
    parser.add_argument("--out", type=Path, default=Path("."), help="原始 JSON 落盘目录")
    parser.add_argument("--parse", type=Path, default=None, help="仅解析已录制的 JSON 文件，不发网络请求")
    args = parser.parse_args(argv)

    if args.parse is not None:
        count = _parse_recorded(args.parse)
        print(f"共解析 {count} 条（未发网络请求）")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    total = 0
    for page, payload in asyncio.run(_fetch_pages(args.pages, args.page_size)):
        target = args.out / f"{sina_7x24.SOURCE_NAME}_page{page}.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        docs, page_info = sina_7x24.parse_feed_payload(payload)
        print(f"[page {page}] 已录制 {target}")
        _summarize(docs, page_info)
        total += len(docs)
    print(f"共解析 {total} 条，原始 JSON 已落盘至 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
