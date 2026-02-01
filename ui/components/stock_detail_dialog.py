import flet as ft
import pandas as pd
from flet.plotly_chart import PlotlyChart # Keeping this import might be safer if used elsewhere, but we don't use it here anymore.
from ui.components.chart_utils import generate_kline_html
from ui.i18n import I18n
import logging
import tempfile
import os
import time
import webbrowser

logger = logging.getLogger(__name__)

class StockDetailDialog(ft.AlertDialog):
    """
    Stock detail popup dialog showing comprehensive stock information.
    """
    
    def __init__(self, stock_data: dict = None, data_processor=None):
        self.stock_data = stock_data or {}
        self.data_processor = data_processor
        
        super().__init__(
            modal=True,
            title=self._build_title(),
            content=self._build_content(),
            actions=[
                ft.TextButton(I18n.get("common_close"), on_click=self._close),
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
        
        # Chart placeholder
        self.chart_container = ft.Container(
            content=ft.Column([
                ft.ProgressRing(), 
                ft.Text(I18n.get("detail_loading_chart"), size=12, color=ft.Colors.GREY)
            ], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            height=350,
            alignment=ft.alignment.center,
            bgcolor=ft.Colors.GREY_50,
            border=ft.border.all(1, ft.Colors.GREY_200),
            border_radius=8,
        )

        # Price section
        close = self._format_val('close', '元')
        pct = self.stock_data.get('pct_chg', 0)
        pct_color = ft.Colors.RED if pct and pct > 0 else ft.Colors.GREEN
        pct_str = f"+{pct:.2f}%" if pct > 0 else f"{pct:.2f}%"
        
        price_section = ft.Column([
            ft.Text(I18n.get("detail_sec_price"), size=14, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE),
            ft.Divider(height=5),
            ft.Row([
                self._info_chip(I18n.get("detail_price"), close),
                self._info_chip(I18n.get("detail_pct_chg"), pct_str, color=pct_color),
                self._info_chip(I18n.get("detail_turnover"), self._format_val('turnover_rate', '%')),
            ]),
            ft.Row([
                self._info_chip(I18n.get("detail_vol"), self._format_vol('vol')),
                self._info_chip(I18n.get("detail_amount"), self._format_amount('amount')),
            ]),
        ])
        
        # Valuation section
        valuation_section = ft.Column([
            ft.Container(height=10),
            ft.Text(I18n.get("detail_sec_valuation"), size=14, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE),
            ft.Divider(height=5),
            ft.Row([
                self._info_chip(I18n.get("detail_pe"), self._format_val('pe_ttm')),
                self._info_chip(I18n.get("detail_pb"), self._format_val('pb')),
                self._info_chip(I18n.get("detail_ps"), self._format_val('ps_ttm')),
            ]),
            ft.Row([
                self._info_chip(I18n.get("detail_dividend"), self._format_val('dv_ttm', '%')),
                self._info_chip(I18n.get("detail_total_mv"), self._format_mv('total_mv')),
                self._info_chip(I18n.get("detail_circ_mv"), self._format_mv('circ_mv')),
            ]),
        ])
        
        # Financial section
        financial_section = ft.Column([
            ft.Container(height=10),
            ft.Text(I18n.get("detail_sec_financial"), size=14, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE),
            ft.Divider(height=5),
            ft.Row([
                self._info_chip(I18n.get("detail_roe"), self._format_val('roe', '%')),
                self._info_chip(I18n.get("detail_gpm"), self._format_val('grossprofit_margin', '%')),
                self._info_chip(I18n.get("detail_debt_ratio"), self._format_val('debt_to_assets', '%')),
            ]),
            ft.Row([
                self._info_chip(I18n.get("detail_rev_yoy"), self._format_val('or_yoy', '%')),
                self._info_chip(I18n.get("detail_profit_yoy"), self._format_val('netprofit_yoy', '%')),
            ]),
        ])
        
        # Basic info section
        basic_section = ft.Column([
            ft.Container(height=10),
            ft.Text(I18n.get("detail_sec_basic"), size=14, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE),
            ft.Divider(height=5),
            ft.Row([
                self._info_chip(I18n.get("detail_industry"), str(self.stock_data.get('industry', '-'))),
                self._info_chip(I18n.get("detail_list_date"), str(self.stock_data.get('list_date', '-'))),
            ]),
        ])
        
        # AI Analysis Section
        ai_section = ft.Container()
        ai_reason = self.stock_data.get('ai_reason')
        ai_score = self.stock_data.get('ai_score')
        
        if ai_reason or ai_score:
            try:
                score_val = float(ai_score) if ai_score is not None else 0
            except:
                score_val = 0
                
            score_color = ft.Colors.GREEN if score_val >= 80 else (ft.Colors.ORANGE if score_val >= 60 else ft.Colors.RED)
            
            ai_section = ft.Column([
                ft.Container(height=10),
                ft.Row([
                    ft.Icon(ft.Icons.AUTO_AWESOME, color=ft.Colors.PURPLE),
                    ft.Text(I18n.get("detail_ai_analysis"), size=16, weight=ft.FontWeight.BOLD, color=ft.Colors.PURPLE),
                    ft.Container(expand=True),
                    ft.Container(
                        content=ft.Text(f"{I18n.get('detail_ai_score_prefix')}{score_val}", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                        bgcolor=score_color,
                        padding=ft.padding.symmetric(horizontal=10, vertical=5),
                        border_radius=12
                    ) if ai_score is not None else ft.Container(),
                ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Divider(height=10, color=ft.Colors.PURPLE_100),
                ft.Container(
                    content=ft.Markdown(
                        str(ai_reason) if ai_reason else I18n.get("detail_ai_no_analysis"), 
                        extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                        selectable=True,
                    ),
                    padding=10,
                    bgcolor=ft.Colors.PURPLE_50,
                    border_radius=8,
                    border=ft.border.all(1, ft.Colors.PURPLE_100)
                ),
                
                # --- AI Thinking Chain ---
                ft.ExpansionTile(
                    title=ft.Text(I18n.get("detail_ai_thinking"), size=12, color=ft.Colors.GREY_700),
                    controls=[
                        ft.Container(
                            content=ft.Markdown(
                                self.stock_data.get('thinking', I18n.get("detail_ai_no_thinking")),
                                extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                                selectable=True,
                            ),
                            padding=10,
                            bgcolor=ft.Colors.GREY_50,
                            border_radius=8,
                            border=ft.border.all(1, ft.Colors.GREY_200)
                        )
                    ]
                ) if self.stock_data.get('thinking') else ft.Container(),
            ])

        return ft.Container(
            content=ft.Column([
                self.chart_container,
                ai_section,
                price_section,
                valuation_section,
                financial_section,
                basic_section,
            ], scroll=ft.ScrollMode.AUTO),
            width=900,
            height=700,
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

    async def load_chart(self, ts_code: str):
        """Asynchronously load and render the chart"""
        if not self.data_processor:
            self.chart_container.content = ft.Text(I18n.get("detail_err_no_processor"), color=ft.Colors.RED)
            self.chart_container.update()
            return
            
        try:
            # Show loading
            self.chart_container.content = ft.Column([
                ft.ProgressRing(),
                ft.Text(I18n.get("detail_loading_history"), size=12, color=ft.Colors.GREY)
            ], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER)
            self.chart_container.update()
            
            # Fetch data (History 365 days)
            df = await self.data_processor.get_stock_history(ts_code, days=365)
            
            if df.empty:
                self.chart_container.content = ft.Text(I18n.get("detail_no_history"), color=ft.Colors.GREY)
                self.chart_container.update()
                return

            # Check if we have volume data
            if 'vol' not in df.columns:
                 df['vol'] = 0
            
            # Generate HTML
            html_content = generate_kline_html(df, title=f"{self.stock_data.get('name', '')} ({ts_code})")
            
            # Save to temporary file
            # Use 'charts' subdir to keep organized
            tmp_dir = os.path.join(tempfile.gettempdir(), "astock_charts")
            os.makedirs(tmp_dir, exist_ok=True)
            
            # Lazy Cleanup: Remove files older than 10 minutes
            self._cleanup_old_charts(tmp_dir)
            
            filename = f"chart_{ts_code}_{int(time.time())}.html"
            file_path = os.path.join(tmp_dir, filename)
            
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(html_content)
                
            # Create WebView
            # Note: file:/// path needs 3 slashes on Windows? "file:///" + path
            file_uri = f"file:///{file_path.replace(os.sep, '/')}"
            logger.info(f"Loading chart from: {file_uri}")
            
            # Hybrid view: Open in Browser Button (Fallback for when WebView is not supported)
            
            def open_browser(e):
                webbrowser.open(file_uri)
                
            content_col = ft.Column([
                ft.Icon(ft.Icons.SHOW_CHART, size=48, color=ft.Colors.BLUE),
                ft.Text(I18n.get("detail_chart_generated"), size=16, weight=ft.FontWeight.BOLD),
                ft.Text(I18n.get("detail_chart_browser_hint"), size=12, color=ft.Colors.GREY),
                ft.Container(height=10),
                ft.ElevatedButton(I18n.get("detail_open_browser"), on_click=open_browser, icon=ft.Icons.OPEN_IN_BROWSER)
            ], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER)
            
            self.chart_container.content = content_col
            self.chart_container.update()
            
        except Exception as e:
            import traceback
            logger.error(f"Error loading chart: {e}\n{traceback.format_exc()}")
            self.chart_container.content = ft.Text(I18n.get("detail_err_load_chart").format(error=str(e)), color=ft.Colors.RED)
            self.chart_container.update()

    def _cleanup_old_charts(self, tmp_dir):
        """Cleanup chart files older than 10 minutes"""
        try:
            now = time.time()
            for f in os.listdir(tmp_dir):
                if not f.startswith("chart_") or not f.endswith(".html"):
                    continue
                
                f_path = os.path.join(tmp_dir, f)
                try:
                    if os.path.isfile(f_path):
                        mtime = os.path.getmtime(f_path)
                        if now - mtime > 600: # 10 minutes
                            os.remove(f_path)
                            logger.debug(f"Removed old chart file: {f}")
                except Exception:
                    # Ignore file lock errors etc.
                    pass
        except Exception as e:
            logger.warning(f"Failed to cleanup temp charts: {e}")
