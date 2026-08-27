"""Tushare 客户端子模块包（review01-A5c）。

``data.external.tushare_client.TushareClient`` 组合根的三个职责子模块：
- ``TushareRateLimiter``：限流器构建（档位预设 / per-API 慢桶 / probe 专用桶）
- ``CapabilityProbeService``：能力探测与缓存（档位覆盖 / probe / AppState 持久化）
- ``TushareApiWrapper``：API 转发与错误处理（重试 / 降速 / 分页 / 交易日历）

组合根 ``TushareClient`` 保持定义于 ``data.external.tushare_client``（既有 import 面与
测试 patch 目标不变），本包仅导出三个子类供组合根惰性实例化。
"""

from data.external.tushare.api_wrapper import TushareApiWrapper
from data.external.tushare.capability_probe import CapabilityProbeService
from data.external.tushare.rate_limiter import TushareRateLimiter

__all__ = ["TushareRateLimiter", "CapabilityProbeService", "TushareApiWrapper"]
