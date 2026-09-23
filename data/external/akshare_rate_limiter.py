"""Akshare 出站调用共享限速器（review08-B4）。

``AkshareConceptClient``（async，``consume_async``）与 ``NewsFetcher`` 同步内核
（IO 线程池线程，``consume``）共用同一 TokenBucket，使所有 akshare 出站调用
共用一个令牌桶（1 QPS / burst 2），防止 AI 批量选股时高频 ``stock_news_em``
触发东财封禁。

sync/async 混用安全性（``TokenBucket`` 类 docstring 的补充论证）：
- 状态一致性：``_consume_reserve`` 全部可变状态读写位于 ``threading.Lock``
  临界区内，跨线程（IO 线程 ↔ 事件循环线程）并发访问被锁串行化；
- 单事件循环约束：``consume_async`` 依赖 ``asyncio.sleep``（loop-local 调度），
  本项目为 asyncio 单线程 UI 模型，``consume_async`` 只被唯一主事件循环调用；
  ``consume`` 不依赖事件循环（IO 线程无 running loop，内置守卫放行同步阻塞）。
"""

import threading

from utils.rate_limiter import TokenBucket

# 1 QPS, capacity 2 — 与 AkshareConceptClient 原配置一致（东财/巨潮公开接口软限制）。
AKSHARE_RATE_LIMIT_PER_SEC: float = 1.0
AKSHARE_RATE_LIMIT_CAPACITY: float = 2.0

_lock = threading.Lock()
_limiter: TokenBucket = TokenBucket(
    start_tokens=AKSHARE_RATE_LIMIT_CAPACITY,
    capacity=AKSHARE_RATE_LIMIT_CAPACITY,
    rate=AKSHARE_RATE_LIMIT_PER_SEC,
)


def get_akshare_rate_limiter() -> TokenBucket:
    """返回进程级共享 TokenBucket（所有 akshare 出站调用的唯一限速入口）。"""
    return _limiter


def reset_akshare_rate_limiter() -> None:
    """重建共享限速器（仅测试隔离用，R7）。NEVER call in production."""
    global _limiter
    _limiter = TokenBucket(
        start_tokens=AKSHARE_RATE_LIMIT_CAPACITY,
        capacity=AKSHARE_RATE_LIMIT_CAPACITY,
        rate=AKSHARE_RATE_LIMIT_PER_SEC,
    )
