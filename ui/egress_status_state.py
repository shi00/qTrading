"""UI 层 Egress 会话计数瞬时信号源 (Observable, 对齐 cache_cleared_state 模式).

SEC-03 状态栏指示器「本会话外发 N 次」的实时刷新信号源。

架构约定（避免引入新抽象，复用 cache_cleared_state / I18nState 模式）:
- 声明式组件（app_layout）经 ``ft.use_state(get_egress_status_state)`` 订阅 Observable, 自动重渲染
- 发送方（app_layout 挂载时注册的 EgressAudit listener）调 ``notify_egress_count(count)`` 就地写
  ``count`` 触发通知
- ``count`` 即会话计数（进程内单调, 重启归零）, 值变化触发重渲染; 语义与 `load` 一致
- 纯 ``ui/`` 层, 不 import utils 业务层（utils 是横切叶子, ui→utils 合法, 但本模块不依赖以
  避免反向; 订阅关系在 app_layout 组合根建立）, R1 / R16 通过
"""

from dataclasses import dataclass

import flet as ft


@ft.observable
@dataclass
class EgressStatusState(ft.Observable):
    """Egress 会话计数 Observable 状态源 (UI 层).

    显式继承 ``ft.Observable`` 使 pyright 识别 ``subscribe`` 等方法;
    ``@ft.observable`` 检测 ``Observable in __mro__`` 后 no-op 返回原类.

    ``count`` 为本次进程启动以来累计外发次数（内存单调, 重启归零）。值变化时
    Flet 自动通知已订阅组件重渲染（app_layout 状态栏文本）。
    """

    count: int = 0


_egress_status_state: EgressStatusState | None = None


def get_egress_status_state() -> EgressStatusState:
    """获取 Egress 会话计数信号源单例（对齐 cache_cleared_state 的 Observable 单例模式）。"""
    global _egress_status_state
    if _egress_status_state is None:
        _egress_status_state = EgressStatusState()
    return _egress_status_state


def notify_egress_count(count: int) -> None:
    """更新会话外发计数并触发重渲染（EgressAudit 会话计数 listener 回调）。

    仅在值变化（即发生新的外发）时更新, 由 EgressAudit 会话计数单调递增驱动。
    操作仅 ``state.count = count``, 无 raise 路径。
    """
    get_egress_status_state().count = int(count)


def _reset_state_for_test() -> None:
    """测试隔离：丢弃当前单例（连带其 Observable 订阅者）。

    app_layout 组件经 ``ft.use_state(get_egress_status_state)`` 订阅单例；单元测试
    挂载组件后不卸载, 订阅者（ObservableSubscription）会随单例残留到后续测试,
    在无 page 上下文的测试中触发 ``notify_egress_count`` 时抛
    ``RuntimeError: The context is not associated with any page``。
    重置为 None 后旧实例连同订阅者一起被丢弃, 与 toast_manager._reset_state_for_test
    等模块级可变状态隔离惯例一致（R7）。
    """
    global _egress_status_state
    _egress_status_state = None
