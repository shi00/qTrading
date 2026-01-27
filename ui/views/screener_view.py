import flet as ft
from strategies.all_strategies import StrategyManager
from data.data_processor import DataProcessor
from ui.components.stock_detail_dialog import StockDetailDialog
from data.review_manager import ReviewManager
import asyncio
import traceback
import logging

logger = logging.getLogger(__name__)

class ScreenerView(ft.Container):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self.expand = True
        self.strategy_mgr = StrategyManager()
        self.data_processor = DataProcessor()
        self.review_mgr = ReviewManager()
        self.current_results = None  # To be deprecated by full_results
        self.full_results = None
        self.page_no = 1
        self.page_size = 50
        
        # Controls
        self.strategy_dropdown = ft.Dropdown(
            label="选择策略",
            options=[
                ft.dropdown.Option(key=k, text=v) for k, v in self.strategy_mgr.get_all_names().items()
            ],
            width=300,
        )
        
        self.results_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("代码")),
                ft.DataColumn(ft.Text("名称")),
                ft.DataColumn(ft.Text("现价")),
                ft.DataColumn(ft.Text("涨跌幅")),
                ft.DataColumn(ft.Text("PE")),
                ft.DataColumn(ft.Text("换手率")),
                ft.DataColumn(ft.Text("详情")),
            ],
            rows=[],
            border=ft.border.all(1, ft.Colors.GREY_200),
            vertical_lines=ft.border.BorderSide(1, ft.Colors.GREY_200),
            horizontal_lines=ft.border.BorderSide(1, ft.Colors.GREY_200),
        )
        
        self.progress_ring = ft.ProgressRing(visible=False)
        self.status_text = ft.Text("准备就绪")
        self.save_switch = ft.Switch(label="自动保存复盘记录", value=True)
        
        # Pagination UI
        self.prev_btn = ft.IconButton(
            ft.Icons.ARROW_BACK_IOS, 
            tooltip="上一页",
            on_click=lambda e: self.change_page(-1),
            disabled=True
        )
        self.next_btn = ft.IconButton(
            ft.Icons.ARROW_FORWARD_IOS, 
            tooltip="下一页", 
            on_click=lambda e: self.change_page(1),
            disabled=True
        )
        self.page_info = ft.Text("第 0 页 / 共 0 页")
        
        # Stock detail dialog
        self.detail_dialog = StockDetailDialog()

        self.content = ft.Column(
            [
                ft.Text("智能选股器 (Pro)", size=24, weight=ft.FontWeight.BOLD),
                ft.Row([
                    self.strategy_dropdown,
                    ft.ElevatedButton("初始化数据", icon=ft.Icons.REFRESH, on_click=self.on_init_data),
                    ft.ElevatedButton("执行筛选", icon=ft.Icons.PLAY_ARROW, on_click=self.on_run_screening),
                    self.progress_ring
                ]),
                ft.Row([self.save_switch]),
                self.status_text,
                ft.Divider(),
                ft.Column(
                    controls=[self.results_table],
                    expand=True,
                    scroll=ft.ScrollMode.AUTO
                ),
                # Pagination Controls
                ft.Row([
                    self.prev_btn,
                    self.page_info,
                    self.next_btn
                ], alignment=ft.MainAxisAlignment.CENTER)
            ],
            expand=True
        )

    async def on_init_data(self, e):
        await self.init_data_task()

    async def init_data_task(self):
        self.progress_ring.visible = True
        self.status_text.value = "正在初始化数据库..."
        self.update()
        
        try:
            await self.data_processor.init_data()
            self.status_text.value = "数据库初始化完成，请点击筛选"
            self.status_text.color = ft.Colors.GREEN
        except Exception as ex:
            error_msg = f"初始化失败: {str(ex)[:40]}"
            self.status_text.value = error_msg
            self.status_text.color = ft.Colors.RED
            logger.error(error_msg)
        finally:
            self.progress_ring.visible = False
            self.update()

    async def on_run_screening(self, e):
        if not self.strategy_dropdown.value:
            self.status_text.value = "请先选择策略！"
            self.status_text.color = ft.Colors.RED
            self.update()
            return
        await self.run_screening_async()

    async def run_screening_async(self):
        strategy_key = self.strategy_dropdown.value
        strategy = self.strategy_mgr.get_strategy(strategy_key)
        
        if strategy is None:
            self.status_text.value = "未找到该策略"
            self.status_text.color = ft.Colors.RED
            self.update()
            return
        
        self.progress_ring.visible = True
        self.status_text.value = f"正在同步数据并运行 {strategy.name}..."
        self.status_text.color = ft.Colors.BLUE
        self.update()
        
        try:
            # 1. Prepare Data Context
            context = await self.data_processor.get_strategy_data()
            screening_df = context.get('screening_data') if context else None
            
            if screening_df is None or screening_df.empty:
                self.status_text.value = "无数据，请先在设置中同步数据"
                self.status_text.color = ft.Colors.ORANGE
                self.results_table.rows = []
                self.full_results = None
                self.update_pagination_controls()
                self.progress_ring.visible = False
                self.update()
                return
                
            # 2. Execute Strategy
            try:
                result_df = strategy.filter(context)
            except Exception as filter_ex:
                self.status_text.value = f"策略执行出错: {str(filter_ex)[:40]}"
                self.status_text.color = ft.Colors.RED
                logger.error(f"Strategy filter error: {traceback.format_exc()}")
                self.results_table.rows = []
                self.full_results = None
                self.update_pagination_controls()
                self.progress_ring.visible = False
                self.update()
                return
            
            # 3. Store results and update UI
            self.full_results = result_df
            self.page_no = 1
            
            if result_df is None or result_df.empty:
                self.status_text.value = "未找到符合条件的股票"
                self.status_text.color = ft.Colors.ORANGE
                self.results_table.rows = []
                self.update_pagination_controls()
            else:
                count = len(result_df)
                msg = f"筛选完成，命中 {count} 只股票"
                
                # Auto-save results to Review System
                if self.save_switch.value:
                    await self.review_mgr.save_results(strategy.name, result_df)
                    msg += " (已保存至复盘)"
                
                self.status_text.value = msg
                self.status_text.color = ft.Colors.GREEN
                self.render_table()
                
        except Exception as ex:
            self.status_text.value = f"执行出错: {str(ex)[:50]}"
            self.status_text.color = ft.Colors.RED
            logger.error(f"Screening error: {traceback.format_exc()}")
            
        self.progress_ring.visible = False
        self.update()

    def show_stock_detail(self, ts_code: str):
        """Show detail dialog for a stock"""
        if self.full_results is None:
            return
        
        # Find stock data
        stock_row = self.full_results[self.full_results['ts_code'] == ts_code]
        if stock_row.empty:
            return
        
        stock_data = stock_row.iloc[0].to_dict()
        self.detail_dialog.update_data(stock_data)
        self.detail_dialog.open = True
        self.page.dialog = self.detail_dialog
        self.page.update()

    def change_page(self, delta):
        if self.full_results is None:
            return
        
        start = (self.page_no + delta - 1) * self.page_size
        if start < 0 or start >= len(self.full_results):
            return
            
        self.page_no += delta
        self.render_table()
        self.update()

    def render_table(self):
        if self.full_results is None or self.full_results.empty:
            self.results_table.rows = []
            self.update_pagination_controls()
            return
            
        # Pagination Logic
        total_items = len(self.full_results)
        start_idx = (self.page_no - 1) * self.page_size
        end_idx = min(start_idx + self.page_size, total_items)
        
        # Slice for current page
        page_data = self.full_results.iloc[start_idx:end_idx]
        
        rows = []
        for _, row in page_data.iterrows():
            # Safe value extraction with defaults
            code = str(row.get('ts_code', ''))
            name = str(row.get('name', '')) if 'name' in row else ''
            
            close_val = row.get('close', 0)
            price = f"{float(close_val):.2f}" if close_val and close_val == close_val else "-"
            
            pct_val = row.get('pct_chg', 0)
            pct = f"{float(pct_val):.2f}%" if pct_val and pct_val == pct_val else "-"
            
            pe_val = row.get('pe_ttm', 0)
            pe = f"{float(pe_val):.1f}" if pe_val and pe_val == pe_val else "-"
            
            turn_val = row.get('turnover_rate', 0)
            turn = f"{float(turn_val):.2f}%" if turn_val and turn_val == turn_val else "-"
            
            # Color for price change
            try:
                pct_color = ft.Colors.RED if float(pct_val or 0) > 0 else ft.Colors.GREEN
            except:
                pct_color = ft.Colors.GREY
            
            # Create detail button with stock code reference
            detail_btn = ft.IconButton(
                ft.Icons.INFO_OUTLINE,
                tooltip="查看详情",
                on_click=lambda e, c=code: self.show_stock_detail(c),
            )
            
            rows.append(ft.DataRow(cells=[
                ft.DataCell(ft.Text(code)),
                ft.DataCell(ft.Text(name)),
                ft.DataCell(ft.Text(price)),
                ft.DataCell(ft.Text(pct, color=pct_color)),
                ft.DataCell(ft.Text(pe)),
                ft.DataCell(ft.Text(turn)),
                ft.DataCell(detail_btn),
            ]))
        self.results_table.rows = rows
        self.update_pagination_controls()
        
    def update_pagination_controls(self):
        if self.full_results is None or self.full_results.empty:
            self.prev_btn.disabled = True
            self.next_btn.disabled = True
            self.page_info.value = "第 0 页 / 共 0 页"
            return
            
        total_items = len(self.full_results)
        total_pages = (total_items + self.page_size - 1) // self.page_size
        
        self.prev_btn.disabled = (self.page_no <= 1)
        self.next_btn.disabled = (self.page_no >= total_pages)
        self.page_info.value = f"第 {self.page_no} 页 / 共 {total_pages} 页 (共 {total_items} 条)"
