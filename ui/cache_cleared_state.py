"""UI 层 CacheCleared 瞬时信号源 (Observable, 对齐 I18nState mode).

05-MVVM UIX-01 修复: 移除 CACHE_CLEARED 的 pubsub 中介, 改为模块级 Observable。

背景: Flet 0.86.5 ``unsubscribe_topic(topic)`` 移除 (topic, session_id) 整个 handler
集合。桌面单 session 下 ``home_view`` 与 ``data_view`` 的 CACHE_CLEARED handler 落
在同一集合, 任一方 effect cleanup 退订即连带删除对方 handler 且不再重订阅, 导致
"首页永久失联 CACHE_CLEARED"。发送方直连 Observable 后: 无 pubsub 中介 / 无 AppLayout
转发 / 无 executor 线程池面, 无跨线程风险, 订阅语义由 ``ft.Observable`` 保证。

与 ``I18nState``(ui/i18n.py) / ``AppColorsState``(ui/theme.py) 模式对齐:
- 声明式组件经 ``ft.use_state(get_cache_cleared_state)`` 订阅 Observable (自动重渲染)
- 发送方调 ``notify_cache_cleared()`` 就地递增 ``seq`` 触发通知
- 纯 ``ui/`` 层, 无跨层依赖, R1 / R16 通过
"""

from dataclasses import dataclass

import flet as ft


@ft.observable
@dataclass
class CacheClearedState(ft.Observable):
    """CacheCleared 瞬时信号 Observable 状态源 (UI 层).

    显式继承 ``ft.Observable`` 使 pyright 识别 ``subscribe`` 等方法;
    ``@ft.observable`` 检测 ``Observable in __mro__`` 后 no-op 返回原类.

    ``seq`` 单调递增作为信号序号: 接收方以 ref 记录 last_seq, 仅在 ``seq`` 变化
    时执行动作, 用于区分"值相同不重触发"与"真正的信号到达", 保证与现网
    "仅激活订阅、激活不补事件" 行为逐点一致 (失活不误清、激活不补触发)。
    """

    seq: int = 0


_cache_cleared_state: CacheClearedState | None = None


def get_cache_cleared_state() -> CacheClearedState:
    """获取 CacheCleared 信号源单例 (对齐 ui/i18n.py 的 Observable 单例模式).

    声明式组件通过 ``ft.use_state(get_cache_cleared_state)`` 订阅,
    ``notify_cache_cleared`` 递增 ``state.seq`` 触发自动重渲染.
    """
    global _cache_cleared_state
    if _cache_cleared_state is None:
        _cache_cleared_state = CacheClearedState()
    return _cache_cleared_state


def notify_cache_cleared() -> None:
    """通知一次缓存清除事件 (发送方直连, 替代原 pubsub broadcast).

    就地递增 ``seq``, 触发所有已订阅组件的重渲染. 操作仅 ``state.seq += 1``,
    无 raise 路径. 原发送语义经 ``state.cache_cleared_version`` 去重后触发
    (见 data_source_view_model), 语义不变.
    """
    get_cache_cleared_state().seq += 1
