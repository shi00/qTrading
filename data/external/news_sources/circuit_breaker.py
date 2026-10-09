"""按源熔断器（N1-4，沿用 ``news_fetcher`` 的 CLS 熔断模式）。

离线采集器为每个新闻源维护一个 :class:`CircuitBreaker`：连续失败达阈值即「开路」，在冷却
窗口内**快速失败并告警**；冷却结束后进入「半开」放行一次探活，成功则「闭合」并清零计数
（规格 §3.3「单源连续失败 N 次后熔断跳过并告警，参照 news_fetcher 中已有的 Sina / CLS 熔断
计数实现」）。

状态机::

    closed --失败≥阈值--> open --冷却结束--> half_open --成功--> closed
    half_open --失败--> open（冷却窗口自失败时刻重新计时）

设计取舍
--------
- 与 ``data/external/news_fetcher.py`` 的 CLS 熔断语义一致（threshold / cooldown / half-open），
  但抽为可复用类，供离线采集器按源实例化。
- **非线程安全**：离线采集器为单线程 asyncio 脚本，无并发访问，故不引入锁；本类亦不持有
  ``asyncio`` 原语（R11 不适用）。
- 失败日志经 :class:`DataSanitizer` 脱敏（R9），级别由 :func:`classify_severity` 决定。
"""

import datetime
import logging
from collections.abc import Callable
from typing import Literal

from utils.error_classifier import classify_error, classify_severity
from utils.sanitizers import DataSanitizer
from utils.time_utils import get_now

logger = logging.getLogger(__name__)

BreakerState = Literal["closed", "open", "half_open"]
Clock = Callable[[], datetime.datetime]

DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_COOLDOWN_SECONDS = 60.0


class CircuitBreaker:
    """按源连续失败熔断器（非线程安全；单线程离线采集器使用）。"""

    def __init__(
        self,
        name: str,
        *,
        threshold: int = DEFAULT_FAILURE_THRESHOLD,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        clock: Clock = get_now,
    ) -> None:
        if threshold < 1:
            raise ValueError("threshold 必须 ≥ 1")
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds 不能为负")
        self.name = name
        self._threshold = threshold
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._consecutive_failures = 0
        self._opened_at = 0.0

    @property
    def consecutive_failures(self) -> int:
        """当前连续失败计数（成功后清零）。"""
        return self._consecutive_failures

    @property
    def is_open(self) -> bool:
        """连续失败是否已达阈值（与冷却窗口无关）。"""
        return self._consecutive_failures >= self._threshold

    def state(self) -> BreakerState:
        """当前状态：``closed`` / ``open``（冷却中）/ ``half_open``（冷却结束待探活）。"""
        if not self.is_open:
            return "closed"
        if self._clock().timestamp() - self._opened_at < self._cooldown_seconds:
            return "open"
        return "half_open"

    def allow_request(self) -> bool:
        """是否放行本次请求；``open`` 状态下返回 ``False`` 并告警（快速失败）。"""
        current = self.state()
        if current == "open":
            logger.warning(
                "[Corpus] 源 %s 熔断开启（连续失败 %d 次），冷却窗口内快速失败，跳过剩余请求",
                self.name,
                self._consecutive_failures,
            )
            return False
        if current == "half_open":
            logger.info("[Corpus] 源 %s 熔断半开，放行一次探活请求", self.name)
        return True

    def record_success(self) -> bool:
        """记录一次成功；若此前处于开路则闭合熔断器。返回是否发生了闭合恢复。"""
        was_open = self.is_open
        self._consecutive_failures = 0
        if was_open:
            logger.info("[Corpus] 源 %s 熔断闭合（恢复正常）", self.name)
        return was_open

    def record_failure(self, error: Exception, *, context: str = "news_corpus") -> bool:
        """记录一次失败；达阈值则开路并告警。返回本次是否**刚好触发**开路。"""
        self._consecutive_failures += 1
        just_opened = False
        if self._consecutive_failures >= self._threshold:
            self._opened_at = self._clock().timestamp()
            just_opened = self._consecutive_failures == self._threshold

        error_code = classify_error(error, context=context).get("code", "unknown")
        severity = classify_severity(error, context=context)
        level = logging.ERROR if severity == "system" else logging.WARNING
        sanitized = DataSanitizer.sanitize_error(error)

        if just_opened:
            logger.log(
                level,
                "[Corpus] 源 %s 连续失败 %d 次，熔断开启（告警）。code=%s: %s",
                self.name,
                self._threshold,
                error_code,
                sanitized,
            )
        else:
            logger.log(
                level,
                "[Corpus] 源 %s 请求失败 (%d/%d) code=%s: %s",
                self.name,
                self._consecutive_failures,
                self._threshold,
                error_code,
                sanitized,
            )
        return just_opened
