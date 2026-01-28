import flet as ft
import pandas as pd

class StockDetailDialog(ft.AlertDialog):
    """
    Stock detail popup dialog showing comprehensive stock information.
    """
    
    def __init__(self, stock_data: dict = None):
        self.stock_data = stock_data or {}
        
        super().__init__(
            modal=True,
            title=self._build_title(),
            content=self._build_content(),
            actions=[
                ft.TextButton("关闭", on_click=self._close),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
    
    def _build_title(self):
        code = self.stock_data.get('ts_code', '')
        name = self.stock_data.get('name', '')
        return ft.Row([
            ft.Text(f"{name}", size=20, weight=ft.FontWeight.BOLD),
            ft.Text(f"({code})", size=14, color=ft.Colors.GREY_600),
        ], vertical_alignment=ft.CrossAxisAlignment.CENTER)
    
    def _build_content(self):
        """Build detail content with sections"""
        
        # Price section
        close = self._format_val('close', '元')
        pct = self.stock_data.get('pct_chg', 0)
        pct_color = ft.Colors.RED if pct and pct > 0 else ft.Colors.GREEN
        pct_str = f"+{pct:.2f}%" if pct > 0 else f"{pct:.2f}%"
        
        price_section = ft.Column([
            ft.Text("行情数据", size=14, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE),
            ft.Divider(height=5),
            ft.Row([
                self._info_chip("现价", close),
                self._info_chip("涨跌幅", pct_str, color=pct_color),
                self._info_chip("换手率", self._format_val('turnover_rate', '%')),
            ]),
            ft.Row([
                self._info_chip("成交量", self._format_vol('vol')),
                self._info_chip("成交额", self._format_amount('amount')),
            ]),
        ])
        
        # Valuation section
        valuation_section = ft.Column([
            ft.Container(height=10),
            ft.Text("估值指标", size=14, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE),
            ft.Divider(height=5),
            ft.Row([
                self._info_chip("PE(TTM)", self._format_val('pe_ttm')),
                self._info_chip("PB", self._format_val('pb')),
                self._info_chip("PS(TTM)", self._format_val('ps_ttm')),
            ]),
            ft.Row([
                self._info_chip("股息率", self._format_val('dv_ttm', '%')),
                self._info_chip("总市值", self._format_mv('total_mv')),
                self._info_chip("流通市值", self._format_mv('circ_mv')),
            ]),
        ])
        
        # Financial section
        financial_section = ft.Column([
            ft.Container(height=10),
            ft.Text("财务指标", size=14, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE),
            ft.Divider(height=5),
            ft.Row([
                self._info_chip("ROE", self._format_val('roe', '%')),
                self._info_chip("毛利率", self._format_val('grossprofit_margin', '%')),
                self._info_chip("资产负债率", self._format_val('debt_to_assets', '%')),
            ]),
            ft.Row([
                self._info_chip("营收同比", self._format_val('or_yoy', '%')),
                self._info_chip("净利润同比", self._format_val('netprofit_yoy', '%')),
            ]),
        ])
        
        # Basic info section
        basic_section = ft.Column([
            ft.Container(height=10),
            ft.Text("基本信息", size=14, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE),
            ft.Divider(height=5),
            ft.Row([
                self._info_chip("行业", str(self.stock_data.get('industry', '-'))),
                self._info_chip("上市日期", str(self.stock_data.get('list_date', '-'))),
            ]),
        ])
        
        return ft.Container(
            content=ft.Column([
                price_section,
                valuation_section,
                financial_section,
                basic_section,
            ], scroll=ft.ScrollMode.AUTO),
            width=450,
            height=400,
        )
    
    def _info_chip(self, label, value, color=None):
        """Create an info chip with label and value"""
        return ft.Container(
            content=ft.Column([
                ft.Text(label, size=11, color=ft.Colors.GREY_600),
                ft.Text(str(value), size=14, weight=ft.FontWeight.W_500, 
                       color=color or ft.Colors.BLACK),
            ], spacing=2, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.padding.all(8),
            bgcolor=ft.Colors.GREY_100,
            border_radius=8,
            width=120,
        )
    
    def _format_val(self, key, suffix=''):
        """Format a value with handling for NaN"""
        val = self.stock_data.get(key)
        if val is None or (isinstance(val, float) and val != val):  # NaN check
            return '-'
        try:
            return f"{float(val):.2f}{suffix}"
        except:
            return '-'
    
    def _format_mv(self, key):
        """Format market value in 亿"""
        val = self.stock_data.get(key)
        if val is None or (isinstance(val, float) and val != val):
            return '-'
        try:
            # Tushare returns in 万元, convert to 亿
            return f"{float(val) / 10000:.1f}亿"
        except:
            return '-'
    
    def _format_vol(self, key):
        """Format volume"""
        val = self.stock_data.get(key)
        if val is None or (isinstance(val, float) and val != val):
            return '-'
        try:
            v = float(val)
            if v >= 10000:
                return f"{v / 10000:.1f}万手"
            return f"{v:.0f}手"
        except:
            return '-'
    
    def _format_amount(self, key):
        """Format amount in 亿"""
        val = self.stock_data.get(key)
        if val is None or (isinstance(val, float) and val != val):
            return '-'
        try:
            # Amount is in 千元
            return f"{float(val) / 100000:.2f}亿"
        except:
            return '-'
    
    def _close(self, e):
        self.open = False
        if self.page:
            self.page.update()

    def update_data(self, stock_data: dict):
        """Update the dialog with new stock data"""
        self.stock_data = stock_data
        self.title = self._build_title()
        self.content = self._build_content()
