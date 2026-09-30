"""产品级投资风险提示统一出口（UX-09 / MAJOR-01）。

界面会主动给出「AI 推荐」（选股结果分区、AI 分析简报、个股详情的 AI 预测），
但风险提示此前按功能模块零散添加（见 reviews/09-24/09.md MAJOR-01）。本模块提供
唯一构建入口，供选股结果区 / AI 分析报告区 / 个股详情固定复用，避免提示再次零散
漂移，也保证同一文案（``risk_disclaimer_notice``）在多个位置口径一致。

约束：
- 文案经 ``I18n.get`` 按当前 locale 渲染（View 感知 locale，VM 不参与）；
- 颜色/字号一律走 ``AppColors`` / ``AppStyles`` token（R23）。
"""

import flet as ft

from ui.i18n import I18n
from ui.theme import AppColors, AppStyles


def build_risk_disclaimer(*, compact: bool = False) -> ft.Container:
    """构建固定展示的投资风险提示条。

    Args:
        compact: 紧凑形态（更小字号与内边距）。用于结果区 / 日志区顶部这类
            垂直空间受限、需避免挤压表格高度的位置。
    """
    return ft.Container(
        content=ft.Row(
            [
                ft.Icon(
                    ft.Icons.INFO_OUTLINE,
                    color=AppColors.WARNING,
                    size=AppStyles.FONT_SIZE_TITLE,
                ),
                ft.Text(
                    I18n.get("risk_disclaimer_notice"),
                    size=AppStyles.FONT_SIZE_CAPTION if compact else AppStyles.FONT_SIZE_BODY_SM,
                    color=AppColors.TEXT_SECONDARY,
                    no_wrap=False,
                    expand=True,
                ),
            ],
            spacing=AppStyles.SPACING_SM,
            vertical_alignment=ft.CrossAxisAlignment.START,
        ),
        padding=AppStyles.SPACING_XS if compact else AppStyles.SPACING_SM,
        border_radius=6,
        bgcolor=AppColors.SURFACE_VARIANT,
        border=ft.Border.all(1, AppColors.BORDER),
    )
