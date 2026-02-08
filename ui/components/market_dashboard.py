import flet as ft
from ui.theme import AppColors
from ui.i18n import I18n

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
        self.sh_val = ft.Text("--", size=20, weight=ft.FontWeight.BOLD)
        self.sh_chg = ft.Text("--", size=14, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY)
        
        self.sz_val = ft.Text("--", size=20, weight=ft.FontWeight.BOLD)
        self.sz_chg = ft.Text("--", size=14, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY)
        
        self.cyb_val = ft.Text("--", size=20, weight=ft.FontWeight.BOLD)
        self.cyb_chg = ft.Text("--", size=14, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY)
        
        # Northbound Values
        self.hsgt_val = ft.Text("--", size=20, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY)
        self.hsgt_sub = ft.Text("--", size=12, color=ft.Colors.GREY_500)
        
        # --- I18n Title Refs ---
        self.sh_title = ft.Text(I18n.get("home_index_sh"), size=14, color=AppColors.TEXT_SECONDARY, no_wrap=True)
        self.sz_title = ft.Text(I18n.get("home_index_sz"), size=14, color=AppColors.TEXT_SECONDARY, no_wrap=True)
        self.cyb_title = ft.Text(I18n.get("home_index_cyb"), size=14, color=AppColors.TEXT_SECONDARY, no_wrap=True)
        self.hsgt_title = ft.Text(I18n.get("home_northbound"), size=14, color=AppColors.TEXT_SECONDARY, no_wrap=True)
        self.concepts_title = ft.Text(I18n.get("home_hot_concepts"), size=16, weight=ft.FontWeight.BOLD)
        
        # Hot Concepts Container (Dynamic Content)
        self.concepts_row = ft.ResponsiveRow(run_spacing=10)
        self.concepts_placeholder = ft.Text(I18n.get("home_hot_concepts_empty"), size=12, color=ft.Colors.GREY)
        
        # --- Layout Construction ---
        self.indices_row = ft.ResponsiveRow([
            self._build_card(self.sh_title, self.sh_val, self.sh_chg),
            self._build_card(self.sz_title, self.sz_val, self.sz_chg),
            self._build_card(self.cyb_title, self.cyb_val, self.cyb_chg),
            self._build_card(self.hsgt_title, self.hsgt_val, self.hsgt_sub),
        ])
        
        self.concepts_section = ft.Column([
            self.concepts_title,
            self.concepts_row 
        ], spacing=10)
        # Init with placeholder
        self.concepts_row.controls = [self.concepts_placeholder]

        self.controls = [
            self.indices_row,
            ft.Container(height=10),
            self.concepts_section,
            ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
        ]

    def _build_card(self, title_ctrl, control1, control2):
        return ft.Container(
            content=ft.Column([
                title_ctrl,
                control1,
                control2,
            ], spacing=5),
            padding=20,
            bgcolor=AppColors.SURFACE,
            border_radius=12,
            border=ft.border.all(1, AppColors.BORDER),
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=8,
                color=ft.Colors.with_opacity(0.08, ft.Colors.BLACK),
                offset=ft.Offset(0, 2),
            ),
            col={"xs": 6, "sm": 6, "md": 3, "lg": 3},
        )

    def update_locale(self):
        """Update static text when locale changes"""
        self.sh_title.value = I18n.get("home_index_sh")
        self.sz_title.value = I18n.get("home_index_sz")
        self.cyb_title.value = I18n.get("home_index_cyb")
        self.hsgt_title.value = I18n.get("home_northbound")
        self.concepts_title.value = I18n.get("home_hot_concepts")
        self.concepts_placeholder.value = I18n.get("home_hot_concepts_empty")
        self.update()

    def update_data(self, data: dict):
        """
        Update dashboard with new market data.
        Only updates changed properties and calls update() on specific controls.
        """
        if not data:
            return

        # 1. Update Indices
        indices = data.get('indices', [])
        if len(indices) >= 3:
            self._update_index_card(self.sh_val, self.sh_chg, indices[0])
            self._update_index_card(self.sz_val, self.sz_chg, indices[1])
            self._update_index_card(self.cyb_val, self.cyb_chg, indices[2])
            
            self.sh_val.update()
            self.sh_chg.update()
            self.sz_val.update()
            self.sz_chg.update()
            self.cyb_val.update()
            self.cyb_chg.update()

        # 2. Update HSGT
        hsgt = data.get('hsgt', {})
        if hsgt:
            self.hsgt_val.value = str(hsgt.get('value', '--'))
            self.hsgt_sub.value = str(hsgt.get('sub', '--'))
            
            color_str = hsgt.get('color', 'GREY').upper()
            self.hsgt_val.color = getattr(ft.Colors, color_str, ft.Colors.GREY)
            
            self.hsgt_val.update()
            self.hsgt_sub.update()

        # 3. Update Hot Concepts
        hot_concepts = data.get('hot_concepts', [])
        new_controls = []
        
        if hot_concepts:
            for item in hot_concepts:
                if item.get('name'):
                    new_controls.append(self._build_concept_card(item))
        else:
            new_controls.append(self.concepts_placeholder)
            
        self.concepts_row.controls = new_controls
        self.concepts_row.update()

    def _update_index_card(self, val_ctrl, chg_ctrl, info):
        if not isinstance(info, dict): info = {}
        val = str(info.get('value', '--'))
        chg = str(info.get('change', '--'))
        color_name = info.get('color', 'GREY').upper()
        color = getattr(ft.Colors, color_name, ft.Colors.GREY)
        
        if val_ctrl.value != val:
            val_ctrl.value = val
            
        if chg_ctrl.value != chg:
            chg_ctrl.value = chg
        
        if chg_ctrl.color != color:
            chg_ctrl.color = color

    def _build_concept_card(self, item):
        name = item.get('name', '--')
        change = item.get('change', '0.00%')
        color_str = str(item.get('color', ''))
        is_up = 'red' in color_str
        color = ft.Colors.RED if is_up else ft.Colors.GREEN

        return ft.Container(
            content=ft.Column([
                ft.Text(name, size=14, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY, no_wrap=True),
                ft.Row([
                    ft.Icon(ft.Icons.TRENDING_UP if is_up else ft.Icons.TRENDING_DOWN,
                            color=color, size=16),
                    ft.Text(change, size=16, weight=ft.FontWeight.BOLD, color=color)
                ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER)
            ], spacing=5),
            padding=15,
            bgcolor=AppColors.SURFACE,
            border_radius=12,
            border=ft.border.all(1, AppColors.BORDER),
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=5,
                color=ft.Colors.with_opacity(0.05, ft.Colors.BLACK),
                offset=ft.Offset(0, 2)
            ),
            col={"xs": 6, "sm": 4, "md": 3, "lg": 2}
        )
