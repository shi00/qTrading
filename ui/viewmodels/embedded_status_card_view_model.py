"""EmbeddedStatusCardViewModel — EmbeddedStatusCard 的 ViewModel (P3-9, CLAUDE.md §3.2 MVVM)。

EmbeddedStatusCard 用于 Onboarding/Settings 中 embedded 模式的只读状态显示:
- 显示本地数据库真实状态（准备中 / 已就绪 / 有诊断问题 / 已停止 / 刷新失败）
- 显示 "无需配置主机/端口/密码" 说明

D1-12: 本 VM 组合 ``DatabaseStatusViewModel``（同一实时状态源，doctor 诊断），
不再固定输出绿色「本地数据库已自动准备」；未取得状态前保持「检测中」，
不得把未知渲染为成功 (R21)。残留物扫描仅覆盖数据目录同级。

VM 不感知 locale: state 用 Message dataclass 产出 (key, params),
View 渲染时 I18n.get(msg.key, **msg.params)。

线程模型:
- refresh_status() (async): 委托内层 DatabaseStatusViewModel.refresh_status
- 内层状态变化经订阅转发为本卡 state 更新
- dispose 时退订并释放内层 VM
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from ui.viewmodels import Message
from ui.viewmodels.database_status_view_model import DatabaseStatusState, DatabaseStatusViewModel
from ui.viewmodels.observable_mixin import ObservableViewModelMixin

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmbeddedStatusCardState:
    """EmbeddedStatusCard 的不可变 state snapshot。

    与 DatabaseConfigPanelViewModel.DatabaseConfigState 字段不同 (R-A6 新 VM),
    仅含只读状态显示所需字段, 不含 host/port/user/password 等表单字段。
    """

    # 状态消息 (例如 "本地数据库已自动准备")
    status_message: Message | None = None
    # 状态类型 (success/error/warning/info), 控制 icon + color
    status_type: str = "info"
    # 信息说明 (例如 "无需配置主机/端口/密码")
    info_message: Message | None = None


class EmbeddedStatusCardViewModel(ObservableViewModelMixin[EmbeddedStatusCardState]):
    """ViewModel for EmbeddedStatusCard.

    MVVM + declarative rendering paradigm (CLAUDE.md §3.2):
    - Immutable state snapshot (EmbeddedStatusCardState) via subscribe/_notify
    - VM 不感知 locale, state 用 Message 产出 (key, params)
    - 内部 VM 模式 (use_viewmodel(factory=...)): 由 EmbeddedStatusCard 实例化,
      生命周期由 hook 管理 (dispose_on_unmount=True)

    D1-12: 组合 DatabaseStatusViewModel，从其 state 派生本卡状态；
    挂载期由 View 触发 refresh_status 拉取真实 doctor 状态。

    Args:
        database_status_view_model: 可选 DI（测试注入）；为 None 时自建
    """

    def __init__(self, database_status_view_model: DatabaseStatusViewModel | None = None) -> None:
        self._database_status_vm = database_status_view_model or DatabaseStatusViewModel()
        self._state = self._derive_state(self._database_status_vm.state)
        self._init_mixin_fields()
        self._unsubscribe_db: Callable[[], None] | None = self._database_status_vm.subscribe(self._on_database_state)

    async def refresh_status(self) -> None:
        """拉取真实数据库状态（委托内层 VM，异常/取消语义同 DatabaseStatusViewModel）。"""
        await self._database_status_vm.refresh_status()

    def _on_database_state(self, db_state: DatabaseStatusState) -> None:
        """内层状态变化 → 派生并提交本卡 state（触发 View 重渲染）。"""
        derived = self._derive_state(db_state)
        self._set_state(
            status_message=derived.status_message,
            status_type=derived.status_type,
            info_message=derived.info_message,
        )

    @staticmethod
    def _derive_state(db_state: DatabaseStatusState) -> EmbeddedStatusCardState:
        """按真实状态派生卡片展示（未知不得伪装成成功，R21）。"""
        info = Message("embedded_pg_no_config_needed")
        if db_state.error_message is not None:
            # 最近一次刷新失败：状态已知不可信，不得沿用旧快照显示「已就绪」
            return EmbeddedStatusCardState(
                status_message=Message("db_status_refresh_failed"),
                status_type="error",
                info_message=info,
            )
        if db_state.status_message is None:
            # 尚未取得状态（挂载后首次刷新未完成）
            return EmbeddedStatusCardState(
                status_message=Message("embedded_pg_checking"),
                status_type="info",
                info_message=info,
            )
        if not db_state.is_running:
            return EmbeddedStatusCardState(
                status_message=db_state.status_message,
                status_type="warning",
                info_message=info,
            )
        if db_state.issues is None or db_state.restore_residuals is None or db_state.dump_partials is None:
            # 运行中但诊断渠道未知：不得宣称「无异常」
            return EmbeddedStatusCardState(
                status_message=Message("embedded_pg_checking"),
                status_type="info",
                info_message=info,
            )
        problem_count = len(db_state.issues) + len(db_state.restore_residuals) + len(db_state.dump_partials)
        if problem_count:
            return EmbeddedStatusCardState(
                status_message=Message("embedded_pg_ready_with_issues", {"count": problem_count}),
                status_type="warning",
                info_message=info,
            )
        return EmbeddedStatusCardState(
            status_message=Message("embedded_pg_ready"),
            status_type="success",
            info_message=info,
        )

    def dispose(self) -> None:
        """退订并释放内层 VM，再走 mixin 关闭协议（幂等）。"""
        if self._unsubscribe_db is not None:
            self._unsubscribe_db()
            self._unsubscribe_db = None
        self._database_status_vm.dispose()
        super().dispose()
