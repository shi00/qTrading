"""新浪个股新闻离线采集器（N1-2）。

按规格 §3.3 采集规范抓取个股新闻列表页（GB18030 HTML）原始内容并落盘，供解析内核的单测
与后续语料库构建（N1-4）复用；同时打印解析摘要（条数 / 去重后条数 / 时间范围 / ``ts_code``）。

- 限速：每请求间隔 ≥ :data:`RATE_LIMIT_SECONDS`（规格 §3.3「每源 ≥ 2~3 秒 / 请求」）。
- 落盘为 **GB18030 字节**（保留原始编码），便于单测按字节解码复现 GB18030 解码路径。
- 只发抓取与落盘，不做清洗 / 跨页去重 / 入库（属 N1-4 语料库职责）。
- 解析内核在 ``data/external/news_sources/sina_stock.py``；本脚本不复制解析逻辑。

Usage:
    # 抓 1 页落盘（离线核对用）
    python scripts/collect_sina_stock.py --symbol sh600519 --pages 1 --out D:\\tmp\\n1_2_samples

    # 仅解析已录制的 HTML（不发网络请求）
    python scripts/collect_sina_stock.py --parse D:\\tmp\\n1_2_samples\\sina_stock_sh600519_page1.html

退出码：成功 0；网络 / 解析异常 1。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402 - sys.path 注入后导入

from data.external.news_sources import sina_stock  # noqa: E402 - sys.path 注入后导入

RATE_LIMIT_SECONDS = 2.5


def _summarize(docs: list[dict]) -> None:
    """打印一页解析摘要（人工核对用）。"""
    times = [doc["publish_time"] for doc in docs if doc["publish_time"] is not None]
    ts_codes = {doc["ts_code"] for doc in docs if doc["ts_code"]}
    print(f"  解析条数: {len(docs)}   去重后 URL 数: {len({doc['source_id'] for doc in docs})}")
    if times:
        print(f"  时间范围(UTC naive): {min(times)} ~ {max(times)}")
    print(f"  ts_code: {sorted(ts_codes) or '未映射'}")
    for doc in docs[:3]:
        print(f"    - [{doc['publish_time']}] {doc['text'][:30]}")


async def _fetch_pages(symbol: str, pages: int) -> list[tuple[int, str]]:
    """按限速顺序抓取 pages 页，返回 ``[(page, html), ...]``（不落盘、不解析）。"""
    results: list[tuple[int, str]] = []
    async with httpx.AsyncClient(**sina_stock.build_client_kwargs()) as client:
        for page in range(1, pages + 1):
            html = await sina_stock.fetch_page(client, symbol, page)
            results.append((page, html))
            if page < pages:
                await asyncio.sleep(RATE_LIMIT_SECONDS)
    return results


def _parse_recorded(path: Path, symbol: str) -> int:
    html = sina_stock.decode_html(path.read_bytes())
    docs = sina_stock.parse_news_list(html, symbol=symbol)
    print(f"[parse] {path}")
    _summarize(docs)
    return len(docs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="新浪个股新闻离线采集器（N1-2）")
    parser.add_argument("--symbol", default="sh600519", help="新浪代码（sh/sz/bj + 6 位，默认 sh600519）")
    parser.add_argument("--pages", type=int, default=1, help="抓取页数（默认 1）")
    parser.add_argument("--out", type=Path, default=Path("."), help="HTML 落盘目录")
    parser.add_argument("--parse", type=Path, default=None, help="仅解析已录制的 HTML 文件，不发网络请求")
    args = parser.parse_args(argv)

    if args.parse is not None:
        count = _parse_recorded(args.parse, args.symbol)
        print(f"共解析 {count} 条（未发网络请求）")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    total = 0
    for page, html in asyncio.run(_fetch_pages(args.symbol, args.pages)):
        target = args.out / f"{sina_stock.SOURCE_NAME}_{args.symbol}_page{page}.html"
        target.write_text(html, encoding=sina_stock.NEWS_ENCODING)
        docs = sina_stock.parse_news_list(html, symbol=args.symbol)
        print(f"[page {page}] 已录制 {target}")
        _summarize(docs)
        total += len(docs)
    print(f"共解析 {total} 条，HTML 已落盘至 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
