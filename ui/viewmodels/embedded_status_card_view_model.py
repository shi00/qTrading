"""EmbeddedStatusCardViewModel — EmbeddedStatusCard 的 ViewModel (P3-9, CLAUDE.md §3.2 MVVM)。

EmbeddedStatusCard 用于 Onboarding/Settings 中 embedded 模式的只读状态显示:
- 显示 "本地数据库已自动准备" 提示
- 显示 "无需配置主机/端口/密码" 说明

VM 不感知 locale: state 用 Message dataclass 产出 (key, params),
View 渲染时 I18n.get(msg.key, **msg.params)。

线程模型:
- VM 无 async 命令 (只读状态卡片)
- P3-10 DatabaseStatusPanel / P3-11 BackupRestorePanel 提供操作命令
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ui.viewmodels import Message
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

    P3-9 仅实现默认状态显示 (静态 "ready" 消息), 实际 sidecar 状态查询
    在 P3-10 DatabaseStatusPanel 中实现 (避免 P3-9 范围膨胀)。
    """

    def __init__(self) -> None:
        self._state = EmbeddedStatusCardState()
        self._subscribers: list = []
        # 初始化默认状态: embedded 模式已就绪
        self._state = EmbeddedStatusCardState(
            status_message=Message(
                "embedded_pg_ready",
                {"default": "本地数据库已自动准备"},
            ),
            status_type="success",
            info_message=Message(
                "embedded_pg_no_config_needed",
                {"default": "无需配置主机/端口/密码"},
            ),
        )
