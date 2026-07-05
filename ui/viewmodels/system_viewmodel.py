"""SystemViewModel — Phase 2A.1 §3.2.10 档位驱动基础设施 ViewModel。

半迁移策略（v1.6.0 P0-5）：本 ViewModel 仅承担档位变更全链路 + probe 执行逻辑，
其他 SystemTab 业务（语言/主题/线程池/DB 池等）暂保留在 SystemTab 中，后续 Phase
再逐步迁移。**不使用 @register_singleton**（半迁移阶段由 SystemTab 单一实例化持有）。

MVVM 职责：
- probe 执行由 ``run_probe()`` / ``on_tier_changed()`` 承担；
- View（TierApiPanel）通过给 ``on_probe_completed`` 回调字段赋值接收结果
  （同 DataSourceViewModel 的 ``on_show_snack`` 等单回调字段模式，不引入事件总线）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from utils.config_handler import ConfigHandler

logger = logging.getLogger(__name__)


class SystemViewModel:
    """档位驱动基础设施 ViewModel（半迁移阶段）。

    暴露方法：
        - ``get_current_tier() -> str``：读取当前档位
        - ``on_tier_changed(new_tier, progress_callback) -> dict``：档位变更全链路
          （set_tier → reload_rate_limiters → clear_capability_cache → probe → _emit_probe_result）
        - ``run_probe(progress_callback) -> dict``：执行 probe
        - ``_emit_probe_result(tier, results) -> dict``：分类 probe 结果并通过回调推送
        - ``get_capability_cache() -> dict``：返回 capability cache 副本

    View 回调字段（同 DataSourceViewModel 模式，单回调足够，§1.3）：
        - ``on_probe_completed: Callable[[dict], None] | None = None``
    """

    def __init__(self) -> None:
        # View 回调字段（TierApiPanel.did_mount 时赋值，will_unmount 时置 None）
        self.on_probe_completed: Callable[[dict], None] | None = None

    def get_current_tier(self) -> str:
        """读取当前档位。"""
        return ConfigHandler.get_tushare_point_tier()

    def get_capability_cache(self) -> dict[str, bool | None]:
        """返回 capability cache 副本（供 TierApiPanel.did_mount 拉取最新缓存）。"""
        from data.external.tushare_client import TushareClient

        return TushareClient().get_capability_cache()

    async def on_tier_changed(
        self,
        new_tier: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> dict:
        """档位变更全链路响应（异步，不阻塞 UI）。

        链路:
        1. ``set_tushare_point_tier(new_tier)``  # 持久化新档位
        2. ``TushareClient().reload_rate_limiters()``  # 重建 limiter
        3. ``TushareClient().clear_capability_cache()``  # 清除旧 probe 结果
        4. ``await TushareClient().probe_api_capabilities()``  # 重新 probe（按新档位预筛）
        5. UI 提示通过 ``on_probe_completed`` 回调通知 View

        v1.9.0 M-1/M-3 修订：
        - M-1：clear 之前拍快照，probe 返回空 dict 或失败时恢复快照
          （应对 probe 进行中互斥命中返回空 dict 的竞态）。
        - M-3：probe 失败时 ``probe_api_capabilities`` 内部已回退入口快照，无需额外处理。

        Returns:
            probe 结果 dict（供测试断言）；正常路径下通过 ``on_probe_completed`` 推送给 View。
        """
        from data.external.tushare_client import TushareClient
        from utils.thread_pool import TaskType, ThreadPoolManager

        # 1. 持久化新档位（config 文件写属 IO，投递到 io_pool，避免阻塞事件循环 —— R16/§5.5）
        # v1.6.0 P1-2：set_tier 失败时回滚 UI 档位下拉框
        old_tier = self.get_current_tier()
        try:
            success = await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_tushare_point_tier, new_tier)
        except Exception as exc:
            # IO 失败 → 直接调 on_probe_completed 通知 View 显示失败消息
            logger.warning("[SystemViewModel] set_tushare_point_tier failed: %s", exc)
            if self.on_probe_completed is not None:
                self.on_probe_completed(
                    {
                        "type": "set_tier_failed",
                        "tier": old_tier,
                        "message": "档位保存失败，请检查配置文件权限",
                        "error": str(exc),
                    }
                )
            return {"type": "set_tier_failed", "tier": old_tier, "error": str(exc)}

        if not success:
            # set_typed 返回 False（tier 不在白名单，UI 下拉框已限制可选值，防御性兜底）
            logger.warning("[SystemViewModel] set_tushare_point_tier returned False for tier=%s", new_tier)
            if self.on_probe_completed is not None:
                self.on_probe_completed(
                    {
                        "type": "set_tier_failed",
                        "tier": old_tier,
                        "message": f"档位 {new_tier} 无效，已回滚",
                    }
                )
            return {"type": "set_tier_failed", "tier": old_tier}

        # 2. 重建 limiter（reload_rate_limiters 内部持 threading.Lock 重建 TokenBucket，
        #    属短同步操作，但同样投递到 io_pool 避免在事件循环内持锁）
        client = TushareClient()
        await ThreadPoolManager().run_async(TaskType.IO, client.reload_rate_limiters)

        # 3. 清除旧 probe 结果（避免旧 False 阻塞升级后的 API；纯内存操作，可直接调用）
        # v1.9.0 M-1：clear 之前先拍快照，probe 返回空 dict 或失败时恢复快照
        cache_snapshot_before_clear = client.get_capability_cache()
        client.clear_capability_cache()

        # 4. 重新 probe（异步，按新档位预筛；progress_callback 推送进度给 UI）
        # v1.9.0 M-1：若 probe 进行中，probe_api_capabilities 返回当前缓存（已被 clear 清空 = 空 dict），
        # 此时恢复 clear 之前的快照，避免 UI 显示"可用 0"与实际不符
        results = await client.probe_api_capabilities(progress_callback=progress_callback)
        if not results and cache_snapshot_before_clear:
            # probe 返回空 dict（互斥命中或失败回退），恢复 clear 之前的快照
            logger.info("[SystemViewModel] probe returned empty, restoring pre-clear cache snapshot")
            for api_name, available in cache_snapshot_before_clear.items():
                if available is True:
                    client.mark_api_available(api_name)
                elif available is False:
                    client.mark_api_unavailable(api_name)
            results = client.get_capability_cache()

        # 5. UI 提示（通过 on_probe_completed 回调通知 View，由 Panel 分派到对应 _notify_* 方法）
        return self._emit_probe_result(new_tier, results)

    async def run_probe(
        self,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> dict:
        """执行 probe 并通过 ``on_probe_completed`` 回调通知 View。

        与 ``on_tier_changed`` 复用 ``_emit_probe_result`` 的结果分类逻辑。
        """
        from data.external.tushare_client import TushareClient

        client = TushareClient()
        results = await client.probe_api_capabilities(progress_callback=progress_callback)
        tier = ConfigHandler.get_tushare_point_tier()
        return self._emit_probe_result(tier, results)

    def _emit_probe_result(self, tier: str, results: dict[str, bool | None]) -> dict:
        """将 probe 结果分类后通过 ``on_probe_completed`` 回调推送给 View。

        v1.9.0 M-5/M-7 修订：
        - M-5：``on_probe_completed`` 为 None 时 logger.warning（避免自动 probe 在
          TierApiPanel 未挂载时静默丢失），由 TierApiPanel.did_mount 主动拉取最新缓存刷新。
        - M-7：增加 unknown_count，UI 反馈"探测完成（可用 X，不可用 Y，未知 Z）"。

        Returns:
            分类后的 result dict（type 字段：completed / tier_too_high / all_failed），
            供测试断言；正常路径下推送给 View。
        """
        available_count = sum(1 for v in results.values() if v is True)
        unavailable_count = sum(1 for v in results.values() if v is False)
        unknown_count = sum(1 for v in results.values() if v is None)
        total = len(results)

        # 分类：全部 None（服务不可用/网络问题）或全部 False（Token 无效/积分严重不足）
        # P2-2：显式括号标明 ((A and B) or C) 语义，避免依赖 and/or 优先级推断
        if total > 0 and ((available_count == 0 and unavailable_count == 0) or unavailable_count == total):
            result = {"type": "all_failed", "tier": tier}
        elif total > 0 and unavailable_count / total > 0.5:
            # 档位声明过高检测（False 比例 > 50% 视为档位过高）
            result = {
                "type": "tier_too_high",
                "tier": tier,
                "false_count": unavailable_count,
                "total": total,
            }
        else:
            # v1.9.0 M-7：payload 增加 unknown_count，UI 据此显示"未知 Z"并提示重新探测
            result = {
                "type": "completed",
                "tier": tier,
                "available": available_count,
                "unavailable": unavailable_count,
                "unknown": unknown_count,
            }

        if self.on_probe_completed is None:
            # v1.9.0 M-5：自动 probe 在 TierApiPanel 未挂载时 on_probe_completed 为 None，
            # 静默丢失会让用户错过 probe 结果。改为 warning 提示，由 did_mount 主动拉取刷新。
            logger.warning(
                "[SystemViewModel] on_probe_completed is None, probe result dropped; "
                "TierApiPanel should pull on did_mount"
            )
            return result

        self.on_probe_completed(result)
        return result
