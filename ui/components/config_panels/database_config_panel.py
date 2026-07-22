"""DatabaseConfigPanel — 声明式路由容器 (P3-9).

P3-9 将原 DatabaseConfigPanel 拆分为:
- EmbeddedStatusCard: embedded 模式只读状态显示
- ExternalPgForm: external 模式 host/port/user/password/database 表单

原 DatabaseConfigPanel 改为路由容器按 ConfigHandler.is_embedded_mode() 切换:
- embedded 模式 → 渲染 EmbeddedStatusCard (无表单字段, 只读状态)
- external 模式 → 渲染 ExternalPgForm (原表单行为不变)

签名保持兼容 (vm + show_header + compact + show_save_button),
消费方 (OnboardingWizard / DatabaseTab) 无需修改。

CLAUDE.md §3.2 MVVM:
- 路由容器不持有业务状态, 仅按 is_embedded_mode() 分发
- embedded 模式忽略 vm 参数 (用 EmbeddedStatusCard 内部 VM)
- external 模式把 vm 透传给 ExternalPgForm
"""

import flet as ft

from ui.components.config_panels.embedded_status_card import EmbeddedStatusCard
from ui.components.config_panels.external_pg_form import ExternalPgForm
from ui.i18n import I18n, get_observable_state  # noqa: F401  # 保留 import 供测试 patch
from ui.theme import AppColors, AppStyles  # noqa: F401  # 保留 import 供测试 patch
from ui.viewmodels.database_config_panel_view_model import DatabaseConfigPanelViewModel


@ft.component
def DatabaseConfigPanel(
    vm: DatabaseConfigPanelViewModel,
    *,
    show_header: bool = True,
    compact: bool = False,
    show_save_button: bool = True,
) -> ft.Container:
    """Database 配置面板路由容器 (声明式, P3-9)。

    按 ConfigHandler.is_embedded_mode() 切换渲染:
    - embedded 模式 → EmbeddedStatusCard (只读状态, 忽略 vm)
    - external 模式 → ExternalPgForm (host/port/user/password/database 表单)

    CLAUDE.md §3.2 MVVM:
    - 路由容器不持有业务状态, 仅按 is_embedded_mode() 分发
    - 签名保持兼容, 消费方 (OnboardingWizard / DatabaseTab) 无需修改

    Args:
        vm: 由消费方实例化的 DatabaseConfigPanelViewModel (external 模式使用,
            embedded 模式忽略)
        show_header: 是否显示 section headers (仅 external 模式生效)
        compact: 保留参数兼容消费方调用 (仅 external 模式生效)
        show_save_button: 是否显示保存按钮 (仅 external 模式生效)
    """
    # --- Subscribe to i18n changes (auto-rerender on locale switch) ---
    ft.use_state(get_observable_state)

    # --- Route by is_embedded_mode() ---
    if vm.is_embedded_mode:
        # embedded 模式: 只读状态卡片, 忽略 vm/show_header/compact/show_save_button
        return EmbeddedStatusCard()
    # external 模式: 透传所有参数到 ExternalPgForm
    return ExternalPgForm(
        vm=vm,
        show_header=show_header,
        compact=compact,
        show_save_button=show_save_button,
    )
