"""巨潮资讯公告离线采集器（N1-3）。

按规格 §3.3 采集规范抓取巨潮 ``hisAnnouncement/query`` 原始 JSON 并落盘，供解析内核的单测
与后续语料库构建（N1-4）复用；同时打印解析摘要（条数 / 时间范围 / 板块与 ts_code 分布）。

- 限速：每请求间隔 ≥ :data:`RATE_LIMIT_SECONDS`（规格 §3.3「每源 ≥ 2~3 秒 / 请求」）。
- 落盘为**原始 JSON 响应体**（UTF-8），便于单测离线复现解析路径。
- 只发抓取与落盘，不做清洗 / 跨页去重 / 入库（属 N1-4 语料库职责）。
- 解析内核在 ``data/external/news_sources/cninfo.py``；本脚本不复制解析逻辑。

Usage:
    # 抓 1 页落盘（离线核对用）
    python scripts/collect_cninfo.py --column szse --start 2026-09-28 --end 2026-09-28 --pages 1 --out D:\\tmp\\n1_3_samples

    # 仅解析已录制的 JSON（不发网络请求）
    python scripts/collect_cninfo.py --parse D:\\tmp\\n1_3_samples\\cninfo_szse_20260928_page1.json

退出码：成功 0；网络 / 解析异常 1。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402 - sys.path 注入后导入

from data.external.news_sources import cninfo  # noqa: E402 - sys.path 注入后导入
from utils.time_utils import get_now  # noqa: E402 - sys.path 注入后导入

RATE_LIMIT_SECONDS = 2.5


def _summarize(docs: list[dict]) -> None:
    """打印一页解析摘要（人工核对用）。"""
    times = [doc["publish_time"] for doc in docs if doc["publish_time"] is not None]
    ts_codes = {doc["ts_code"] for doc in docs if doc["ts_code"]}
    sec_names = Counter(doc["sec_name"] for doc in docs if doc["sec_name"])
    print(f"  解析条数: {len(docs)}   去重后 announcementId 数: {len({doc['source_id'] for doc in docs})}")
    if times:
        print(f"  时间范围(UTC naive): {min(times)} ~ {max(times)}")
    print(f"  公司数: {len(sec_names)}   样例: {sec_names.most_common(3)}")
    print(f"  ts_code: {len(ts_codes)} 个，样例 {sorted(ts_codes)[:5] or '未映射'}")
    for doc in docs[:3]:
        print(f"    - [{doc['publish_time']}] {doc['text'][:36]}")


async def _fetch_pages(
    column: str,
    start_date: str,
    end_date: str,
    pages: int,
    page_size: int,
    search_key: str,
) -> list[tuple[int, dict]]:
    """按限速顺序抓取 pages 页，返回 ``[(page, payload), ...]``（不落盘、不解析）。"""
    results: list[tuple[int, dict]] = []
    async with httpx.AsyncClient(**cninfo.build_client_kwargs()) as client:
        for page in range(1, pages + 1):
            payload = await cninfo.fetch_page(
                client, column, start_date, end_date, page_num=page, page_size=page_size, search_key=search_key
            )
            results.append((page, payload))
            if page < pages:
                await asyncio.sleep(RATE_LIMIT_SECONDS)
    return results


def _parse_recorded(path: Path) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    docs, page_info = cninfo.parse_announcement_payload(payload)
    print(f"[parse] {path}  分页信息: {page_info}")
    _summarize(docs)
    return len(docs)


def main(argv: list[str] | None = None) -> int:
    today = get_now().date().isoformat()
    parser = argparse.ArgumentParser(description="巨潮资讯公告离线采集器（N1-3）")
    parser.add_argument("--column", default=cninfo.DEFAULT_COLUMN, help="板块列（szse/sse/bj，默认 szse）")
    parser.add_argument("--start", default=today, help="起始日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--end", default=today, help="结束日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--pages", type=int, default=1, help="抓取页数（默认 1）")
    parser.add_argument("--page-size", type=int, default=cninfo.DEFAULT_PAGE_SIZE, help="每页条数（默认 30）")
    parser.add_argument("--search-key", default="", help="关键词检索（非空时服务端给命中片段加 <em>）")
    parser.add_argument("--out", type=Path, default=Path("."), help="JSON 落盘目录")
    parser.add_argument("--parse", type=Path, default=None, help="仅解析已录制的 JSON 文件，不发网络请求")
    args = parser.parse_args(argv)

    if args.parse is not None:
        count = _parse_recorded(args.parse)
        print(f"共解析 {count} 条（未发网络请求）")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    total = 0
    pages = asyncio.run(_fetch_pages(args.column, args.start, args.end, args.pages, args.page_size, args.search_key))
    for page, payload in pages:
        target = args.out / f"{cninfo.SOURCE_NAME}_{args.column}_{args.start}_page{page}.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        docs, page_info = cninfo.parse_announcement_payload(payload)
        print(f"[page {page}] 已录制 {target}  分页信息: {page_info}")
        _summarize(docs)
        total += len(docs)
    print(f"共解析 {total} 条，JSON 已落盘至 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
