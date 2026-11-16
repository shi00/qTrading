"""E2E seeding 页面状态探测（D21-OTHER-004）。

把「探测」与「判定」分离，避免自诊断异常被同层的探测重试吞掉：

- 只有调用方显式声明为可重试的暂态异常（等待/定位失败）才被降级为重试；
- 发现 DB 初始化错误 UI 属确定性业务失败，``require_seeding_page_ready``
  立即抛 ``SeedingProbeFailure``，不返回 ``False`` 让调用方继续 sleep 到总超时；
- 其它异常（含 ``asyncio.CancelledError``）原样传播（R2）。

独立成模块以便用伪 page 做单元测试：``tests/e2e/conftest.py`` 在模块级替换
``keyring``，单测不应导入它。
"""

from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class _Locator(Protocol):
    async def count(self) -> int: ...


class _Page(Protocol):
    def locator(self, selector: str) -> _Locator: ...


class SeedingProbeFailure(RuntimeError):
    """seeding 前已确定的失败（如应用已渲染 DB 初始化错误 UI），必须立即上抛。"""


async def _count_matching(page: _Page, selector: str, retryable_errors: tuple[type[BaseException], ...]) -> int | None:
    """统计匹配元素数。

    ``retryable_errors`` 必须是 ``Exception`` 子类元组（不得含 ``CancelledError``/
    ``BaseException``，R2）；命中的异常仅表示本次探测暂态失败，返回 ``None``。
    """
    try:
        return await page.locator(selector).count()
    except retryable_errors as exc:
        logger.debug("[E2E Seeding] probe %r failed (retryable): %s", selector, exc, exc_info=True)
        return None


async def require_seeding_page_ready(
    page: _Page,
    *,
    error_text: str,
    ready_text: str,
    retryable_errors: tuple[type[BaseException], ...],
) -> bool:
    """探测一次 seeding 页面状态。

    - 返回 ``True``：页面已就绪（``ready_text`` 可见）；
    - 返回 ``False``：尚未就绪，调用方可继续等待；
    - 抛 ``SeedingProbeFailure``：``error_text`` 已出现，属确定性失败。

    错误 UI 优先于就绪文本：两者同时出现时判定为失败，避免把错误页当成功。
    """
    error_count = await _count_matching(page, f"text={error_text}", retryable_errors)
    if error_count:
        raise SeedingProbeFailure(
            "Flet app shows DB initialization error UI during seeding setup. "
            "See sanitized log artifact: logs/e2e-flet-app.log"
        )
    ready_count = await _count_matching(page, f"text={ready_text}", retryable_errors)
    return bool(ready_count)
