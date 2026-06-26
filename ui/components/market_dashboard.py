import logging

import flet as ft

from ui.i18n import I18n
from ui.theme import AppColors, AppStyles

logger = logging.getLogger(__name__)


class MarketDashboard(ft.Column):
    """
    Market Dashboard Component
    Displays:
    - Market Indices (SH, SZ, CYB)
    - Northbound Funds (HSGT)
    - Hot Concepts

    Optimized for performance: uses fine-grained updates.
    """

    def __init__(self):
        super().__init__()
        self.spacing = 10

        # --- Internal State Refs for UI Updates ---
        # Indices Values
        self.sh_val = ft.Text(
            "--",
            size=20,
            weight=ft.FontWeight.BOLD,
            color=AppColors.TEXT_PRIMARY,
        )
        self.sh_chg = ft.Text(
            "--",
            size=14,
            weight=ft.FontWeight.BOLD,
            color=AppColors.TEXT_SECONDARY,
        )

        self.sz_val = ft.Text(
            "--",
            size=20,
            weight=ft.FontWeight.BOLD,
            color=AppColors.TEXT_PRIMARY,
        )
        self.sz_chg = ft.Text(
            "--",
            size=14,
            weight=ft.FontWeight.BOLD,
            color=AppColors.TEXT_SECONDARY,
        )

        self.cyb_val = ft.Text(
            "--",
            size=20,
            weight=ft.FontWeight.BOLD,
            color=AppColors.TEXT_PRIMARY,
        )
        self.cyb_chg = ft.Text(
            "--",
            size=14,
            weight=ft.FontWeight.BOLD,
            color=AppColors.TEXT_SECONDARY,
        )

        # Northbound Values
        self.hsgt_val = ft.Text(
            "--",
            size=20,
            weight=ft.FontWeight.BOLD,
            color=AppColors.TEXT_SECONDARY,
        )
        self.hsgt_sub = ft.Text("--", size=12, color=AppColors.TEXT_SECONDARY)

        # --- I18n Title Refs ---
        self.sh_title = ft.Text(
            I18n.get("home_index_sh"),
            size=14,
            color=AppColors.TEXT_SECONDARY,
            no_wrap=True,
        )
        self.sz_title = ft.Text(
            I18n.get("home_index_sz"),
            size=14,
            color=AppColors.TEXT_SECONDARY,
            no_wrap=True,
        )
        self.cyb_title = ft.Text(
            I18n.get("home_index_cyb"),
            size=14,
            color=AppColors.TEXT_SECONDARY,
            no_wrap=True,
        )
        self.hsgt_title = ft.Text(
            I18n.get("home_northbound"),
            size=14,
            color=AppColors.TEXT_SECONDARY,
            no_wrap=True,
        )
        self.concepts_title = ft.Text(
            I18n.get("home_hot_concepts"),
            size=16,
            weight=ft.FontWeight.BOLD,
            color=AppColors.TEXT_PRIMARY,
        )

        # Hot Concepts Container (Dynamic Content)
        self.concepts_row = ft.ResponsiveRow(run_spacing=10)
        self.concepts_placeholder = ft.Text(
            I18n.get("home_hot_concepts_empty"),
            size=12,
            color=AppColors.TEXT_HINT,
        )

        # --- Layout Construction ---
        self.indices_row = ft.ResponsiveRow(
            [
                self._build_card(self.sh_title, self.sh_val, self.sh_chg),
                self._build_card(self.sz_title, self.sz_val, self.sz_chg),
                self._build_card(self.cyb_title, self.cyb_val, self.cyb_chg),
                self._build_card(self.hsgt_title, self.hsgt_val, self.hsgt_sub),
            ],
        )

        self.concepts_section = ft.Column(
            [self.concepts_title, self.concepts_row],
            spacing=10,
        )
        # Init with placeholder
        self.concepts_row.controls = [self.concepts_placeholder]

        self.controls = [
            self.indices_row,
            ft.Container(height=10),
            self.concepts_section,
            ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
        ]

        self._last_data = {}  # Cache for theme reloading

    def update_theme(self):
        """Update colors on theme change"""
        # 1. Update Static Text Colors
        # Note: TEXT_PRIMARY/SECONDARY/HINT are semantic tokens and update automatically.
        # We only need to update if we are changing logic or non-semantic colors.

        # 2. Update Card Styles (Backgrounds, Borders)
        # We need to iterate over indices_row controls (Containers)
        style = AppStyles.dashboard_card()
        for card_container in self.indices_row.controls:
            if isinstance(card_container, ft.Container):
                card_container.bgcolor = style["bgcolor"]
                card_container.border = style["border"]
                card_container.shadow = style["shadow"]

        # 3. Re-apply Data Colors (UP/DOWN/TEXT)
        # Force re-run of update_data with cached data
        if self._last_data:
            self.update_data(self._last_data)

        # 4. Update Hot Concepts Styles
        # Concept cards are in self.concepts_row.controls
        for container in self.concepts_row.controls:
            if isinstance(container, ft.Container):
                container.bgcolor = AppColors.SURFACE
                container.border = ft.border.all(1, AppColors.BORDER)
                # Text inside might need update too...
                # Ideally update_data handles everything if we clear controls?
                # actually update_data rebuilds concept controls if data exists.
                pass

        if self.page:
            self.update()

    def _build_card(self, title_ctrl, control1, control2):
        style = AppStyles.dashboard_card()
        return ft.Container(
            content=ft.Column(
                [
                    title_ctrl,
                    control1,
                    control2,
                ],
                spacing=5,
            ),
            padding=style["padding"],
            bgcolor=style["bgcolor"],
            border_radius=style["border_radius"],
            border=style["border"],
            shadow=style["shadow"],
            col={"xs": 6, "sm": 6, "md": 3, "lg": 3},
        )

    def update_locale(self):
        """Update static text when locale changes"""
        try:
            self.sh_title.value = I18n.get("home_index_sh")
            self.sz_title.value = I18n.get("home_index_sz")
            self.cyb_title.value = I18n.get("home_index_cyb")
            self.hsgt_title.value = I18n.get("home_northbound")
            self.concepts_title.value = I18n.get("home_hot_concepts")
            self.concepts_placeholder.value = I18n.get("home_hot_concepts_empty")
            if self.page:
                self.update()
        except Exception as e:
            logger.warning(f"[MarketDashboard] update_locale failed: {e}")

    def update_data(self, data):
        """
        Update dashboard with new market data.
        Only updates changed properties and calls update() on specific controls.
        """
        if not data:
            return

        self._last_data = data  # Cache data

        # 1. Update Indices
        indices = data.get("indices", [])
        if len(indices) >= 3:
            self._update_index_card(self.sh_val, self.sh_chg, indices[0])
            self._update_index_card(self.sz_val, self.sz_chg, indices[1])
            self._update_index_card(self.cyb_val, self.cyb_chg, indices[2])

            if self.page:
                self.sh_val.update()
                self.sh_chg.update()
                self.sz_val.update()
                self.sz_chg.update()
                self.cyb_val.update()
                self.cyb_chg.update()

        # 2. Update HSGT
        hsgt = data.get("hsgt", {})
        if hsgt:
            self.hsgt_val.value = str(hsgt.get("value", "--"))
            self.hsgt_sub.value = str(hsgt.get("sub", "--"))

            color_str = hsgt.get("color", "GREY").upper()
            if color_str == "RED":
                self.hsgt_val.color = AppColors.UP
            elif color_str == "GREEN":
                self.hsgt_val.color = AppColors.DOWN
            else:
                self.hsgt_val.color = AppColors.TEXT_SECONDARY

            if self.page:
                self.hsgt_val.update()
                self.hsgt_sub.update()

        # 3. Update Hot Concepts (Optimized: Recycle Controls)
        hot_concepts = data.get("hot_concepts", [])

        if not hot_concepts:
            # Empty state
            if len(self.concepts_row.controls) != 1 or self.concepts_row.controls[0] != self.concepts_placeholder:
                self.concepts_row.controls = [self.concepts_placeholder]
                if self.page:
                    self.concepts_row.update()
            return

        # Ensure we have enough controls, create added ones
        current_count = len(self.concepts_row.controls)
        target_count = len(hot_concepts)

        # If placeholder is showing, clear it first
        if current_count == 1 and self.concepts_row.controls[0] == self.concepts_placeholder:
            self.concepts_row.controls.clear()
            current_count = 0

        # Add missing controls
        if current_count < target_count:
            for _ in range(target_count - current_count):
                self.concepts_row.controls.append(self._build_concept_card_skeleton())

        # Remove excess controls
        if current_count > target_count:
            self.concepts_row.controls = self.concepts_row.controls[:target_count]

        # Update content of all controls
        for i, item in enumerate(hot_concepts):
            if i < len(self.concepts_row.controls):
                self._update_concept_card(self.concepts_row.controls[i], item)  # type: ignore[untyped]
        if self.page:
            self.concepts_row.update()

    def _update_index_card(self, val_ctrl, chg_ctrl, info):
        if not isinstance(info, dict):
            info = {}
        val = str(info.get("value", "--"))
        chg = str(info.get("change", "--"))
        color_name = info.get("color", "GREY").upper()

        if color_name == "RED":
            color = AppColors.UP
        elif color_name == "GREEN":
            color = AppColors.DOWN
        else:
            color = AppColors.TEXT_SECONDARY

        if val_ctrl.value != val:
            val_ctrl.value = val

        if chg_ctrl.value != chg:
            chg_ctrl.value = chg

        if chg_ctrl.color != color:
            chg_ctrl.color = color

    def _build_concept_card_skeleton(self):
        """Create a blank card structure to be updated later"""
        # Structure: Container -> Column -> [Text(Name), Row(Icon, Text(Change))]
        # We assign data tags to find controls easily
        name_txt = ft.Text(
            "-",
            size=14,
            weight=ft.FontWeight.BOLD,
            color=AppColors.TEXT_PRIMARY,
            no_wrap=True,
        )
        icon = ft.Icon(ft.Icons.HELP, size=16)
        change_txt = ft.Text("-", size=16, weight=ft.FontWeight.BOLD)

        # Internal Row
        row_stats = ft.Row(
            [icon, change_txt],
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # Main Col
        col_main = ft.Column([name_txt, row_stats], spacing=5)

        container = ft.Container(
            content=col_main,
            padding=15,
            bgcolor=AppColors.SURFACE,
            border_radius=4,
            border=ft.border.all(1, AppColors.BORDER),
            col={"xs": 6, "sm": 4, "md": 3, "lg": 2},
            data={
                "name": name_txt,
                "icon": icon,
                "change": change_txt,
            },  # References for fast update
        )
        return container

    def _update_concept_card(self, container: ft.Container, item):
        """Update existing card without rebuilding"""
        refs = container.data
        if not refs:
            return  # Should not happen if created by skeleton

        name = item.get("name", "--")
        change = item.get("change", "0.00%")
        color_str = str(item.get("color", ""))
        is_up = "red" in color_str
        color = AppColors.UP if is_up else AppColors.DOWN

        # Update Name
        if refs["name"].value != name:
            refs["name"].value = name

        # Update Change Text
        if refs["change"].value != change:
            refs["change"].value = change
        if refs["change"].color != color:
            refs["change"].color = color

        # Update Icon
        target_icon = ft.Icons.TRENDING_UP if is_up else ft.Icons.TRENDING_DOWN
        if refs["icon"].name != target_icon:
            refs["icon"].name = target_icon
            refs["icon"].color = color
        elif refs["icon"].color != color:
            refs["icon"].color = color

    def _build_concept_card(self, item):
        # Legacy method kept if needed, but we use skeleton + update now
        c = self._build_concept_card_skeleton()
        self._update_concept_card(c, item)
        return c
