import flet as ft
from ui.i18n import I18n
from ui.theme import AppColors

class HealthReportDialog(ft.AlertDialog):
    def __init__(self, page, report, on_dismiss=None):
        self.page_ref = page
        self.report = report
        
        super().__init__(
            title=ft.Text(I18n.get("health_report_title")),
            actions=[ft.TextButton(I18n.get("common_close"), on_click=self.close_dialog)],
            content=self._build_content()
        )
        self.on_dismiss_callback = on_dismiss

    def close_dialog(self, e):
        self.open = False
        if self.page_ref:
            self.page_ref.update()
        if self.on_dismiss_callback:
            self.on_dismiss_callback()

    def _build_content(self):
        # Parse Result
        status = self.report.get('status', 'red')
        market = self.report.get('market', {})
        fundamentals = self.report.get('fundamentals', {})
        tables = fundamentals.get('tables', {})
        
        color_map = {'green': AppColors.SUCCESS, 'yellow': AppColors.WARNING, 'red': AppColors.ERROR}
        icon_map = {'green': ft.Icons.CHECK_CIRCLE, 'yellow': ft.Icons.WARNING, 'red': ft.Icons.ERROR}
        
        status_color = color_map.get(status, AppColors.TEXT_HINT)
        status_icon = icon_map.get(status, ft.Icons.HELP)
        
        # Helper to create table row
        def create_table_row(name, stats):
            ratio = stats.get('ratio', 0)
            fresh_ratio = stats.get('fresh_ratio', 0)
            # Color code coverage
            col = AppColors.SUCCESS if ratio > 0.98 else (AppColors.WARNING if ratio > 0.9 else AppColors.ERROR)
            
            return ft.Row([
                ft.Text(name, width=120, size=12, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
                ft.ProgressBar(value=ratio, width=80, color=col, bgcolor=AppColors.SURFACE_VARIANT),
                ft.Text(f"{ratio*100:.1f}%", width=50, size=12, color=AppColors.TEXT_SECONDARY),
                ft.Icon(ft.Icons.ACCESS_TIME, size=12, color=AppColors.INFO if fresh_ratio > 0.8 else AppColors.TEXT_HINT),
                ft.Text(f"{fresh_ratio*100:.0f}%", size=12, color=AppColors.TEXT_SECONDARY)
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)

        # Build Table List (Priority Order)
        table_rows = []
        priority_order = ['financial_reports', 'fina_forecast', 'pledge_stat', 'margin_daily', 'suspend_d']
        for t in priority_order:
            if t in tables:
                table_rows.append(create_table_row(t, tables[t]))
        
        # Reasons List
        reasons = self.report.get('reasons', [])
        reason_controls = []
        if reasons:
                reason_controls = [ft.Text("⚠️ " + r, size=12, color=AppColors.ERROR) for r in reasons]
                reason_controls.insert(0, ft.Text(I18n.get("common_reason"), weight=ft.FontWeight.BOLD, size=12, color=AppColors.TEXT_PRIMARY))
                reason_controls.append(ft.Divider(color=AppColors.DIVIDER))

        # Data Quality Metrics
        gap_count = fundamentals.get('gap_count', 0)
        sanity_errors = fundamentals.get('sanity_errors', 0)
        
        content = ft.Column([
            ft.Row([
                ft.Icon(status_icon, color=status_color, size=40),
                ft.Column([
                    ft.Text(I18n.get("health_status_label").format(status=status.upper()), size=20, weight=ft.FontWeight.BOLD, color=status_color),
                    ft.Text(I18n.get("health_checked_count").format(count=len(tables)), size=12, color=AppColors.TEXT_SECONDARY)
                ])
            ], alignment=ft.MainAxisAlignment.CENTER),
            ft.Divider(color=AppColors.DIVIDER),
            
            # Reasons
            *reason_controls,
            
            # Market Section
            ft.Text(I18n.get("health_market_ts"), weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
            ft.Row([
                ft.Column([
                    ft.Text(I18n.get("health_sync_latest"), size=12, color=AppColors.TEXT_SECONDARY),
                    ft.Text(f"{market.get('latest_local', 'N/A')}", size=14, color=AppColors.TEXT_PRIMARY)
                ]),
                ft.Column([
                    ft.Text(I18n.get("health_sync_official"), size=12, color=AppColors.TEXT_SECONDARY),
                    ft.Text(f"{market.get('latest_official', 'N/A')}", size=14, color=AppColors.TEXT_PRIMARY)
                ]),
                ft.Column([
                    ft.Text(I18n.get("health_lag_days"), size=12, color=AppColors.TEXT_SECONDARY),
                    ft.Text(f"{market.get('lag_days', 0)} {I18n.get('common_suffix_day')}", size=14, color=AppColors.ERROR if market.get('lag_days', 0) > 0 else AppColors.SUCCESS)
                ]),
            ], alignment=ft.MainAxisAlignment.SPACE_AROUND),
            
            ft.Divider(color=AppColors.DIVIDER),
            
            # Quality Assurance Section (v2.0)
            ft.Text(I18n.get("health_qa"), weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
            ft.Row([
                ft.Column([
                    ft.Text(I18n.get("health_gap_count"), size=12, color=AppColors.TEXT_SECONDARY),
                    ft.Text(f"{gap_count} {I18n.get('common_suffix_place')}", size=14, color=AppColors.ERROR if gap_count > 0 else AppColors.SUCCESS, weight=ft.FontWeight.BOLD)
                ]),
                ft.Column([
                    ft.Text(I18n.get("health_sanity_err"), size=12, color=AppColors.TEXT_SECONDARY),
                    ft.Text(f"{sanity_errors} {I18n.get('common_items')}", size=14, color=AppColors.ERROR if sanity_errors > 0 else AppColors.SUCCESS, weight=ft.FontWeight.BOLD)
                ]),
            ], alignment=ft.MainAxisAlignment.SPACE_AROUND),
            ft.Divider(color=AppColors.DIVIDER),
            
            # Fundamentals Section
            ft.Row([
                ft.Text(I18n.get("health_coverage"), weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
                ft.Text(I18n.get("health_threshold"), size=10, color=AppColors.TEXT_HINT)
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ft.Container(height=10),
            *table_rows,
            
            ft.Container(height=10),
            ft.Text(f"{I18n.get('health_missing_sample')}: {len(fundamentals.get('missing_samples', []))} {I18n.get('common_items')}", size=10, color=AppColors.TEXT_HINT),
        ], width=450, height=550, scroll=ft.ScrollMode.AUTO)
        return content
