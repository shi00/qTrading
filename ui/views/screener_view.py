import flet as ft
from strategies.all_strategies import StrategyManager
from data.data_processor import DataProcessor
from ui.components.stock_detail_dialog import StockDetailDialog
from ui.theme import AppColors, AppStyles
from data.review_manager import ReviewManager
import asyncio
import traceback
import logging

logger = logging.getLogger(__name__)

class ScreenerView(ft.Container):
    def __init__(self):
        super().__init__()
        self.expand = True
        self.strategy_mgr = StrategyManager()
        self.data_processor = DataProcessor()
        self.review_mgr = ReviewManager()
        self._current_results = None  # To be deprecated by full_results
        self._full_results = None
        self.page_no = 1
        self.page_size = 50
        
        # Controls (Improved readability)
        self.strategy_desc = ft.Text(
            value="", 
            size=14, 
            color=AppColors.TEXT_SECONDARY,
            weight=ft.FontWeight.W_500,
        )

        self.strategy_dropdown = ft.Dropdown(
            label="选择策略",
            options=[
                ft.dropdown.Option(key=k, text=v) for k, v in self.strategy_mgr.get_all_names().items()
            ],
            width=300,
            on_change=self._on_strategy_changed,  # Flet 0.27.x 支持构造函数参数
        )
        
        # Set default value
        first_strategy = list(self.strategy_mgr.strategies.keys())[0] if self.strategy_mgr.strategies else None
        if first_strategy:
            self.strategy_dropdown.value = first_strategy
        
        # Sorting state
        self._sort_column = None  # 当前排序的列 (DataFrame 列名)
        self._sort_ascending = True
        
        # 列映射：显示名 -> DataFrame列名
        self._col_map = {
            0: 'ts_code',      # 代码
            1: 'name',         # 名称
            2: 'close',        # 现价
            3: 'pct_chg',      # 涨跌幅
            4: 'pe_ttm',       # PE
            5: 'turnover_rate' # 换手率
        }
        
        self.results_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("代码"), on_sort=lambda e: self._on_sort(0)),
                ft.DataColumn(ft.Text("名称")),  # 名称不需要排序
                ft.DataColumn(ft.Text("现价"), numeric=True, on_sort=lambda e: self._on_sort(2)),
                ft.DataColumn(ft.Text("涨跌幅"), numeric=True, on_sort=lambda e: self._on_sort(3)),
                ft.DataColumn(ft.Text("PE"), numeric=True, on_sort=lambda e: self._on_sort(4)),
                ft.DataColumn(ft.Text("换手率"), numeric=True, on_sort=lambda e: self._on_sort(5)),
                ft.DataColumn(ft.Text("详情")),
            ],
            rows=[],
            sort_column_index=None,
            sort_ascending=True,
            border=ft.border.all(1, AppColors.BORDER),
            vertical_lines=ft.border.BorderSide(1, AppColors.BORDER),
            horizontal_lines=ft.border.BorderSide(1, AppColors.BORDER),
            heading_row_color=ft.Colors.with_opacity(0.05, AppColors.PRIMARY),
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
                ft.Text("智能选股器 (Pro)", size=24, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
                ft.Container(
                    content=ft.Column([
                        ft.Row([
                            self.strategy_dropdown,
                            ft.FilledButton(
                                text="执行筛选", 
                                icon=ft.Icons.PLAY_ARROW, 
                                on_click=self.on_run_screening,
                                style=AppStyles.accent_button(),
                            ),
                            ft.IconButton(icon=ft.Icons.REFRESH, tooltip="重载数据", on_click=self.on_init_data),
                            self.progress_ring
                        ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        ft.Container(
                            content=self.strategy_desc,
                            bgcolor=ft.Colors.with_opacity(0.1, AppColors.PRIMARY),
                            padding=15,
                            border_radius=8,
                        ),
                    ], spacing=10),
                    bgcolor=AppColors.SURFACE,
                    padding=20,
                    border_radius=12,
                    shadow=ft.BoxShadow(
                        spread_radius=0,
                        blur_radius=8,
                        color=ft.Colors.with_opacity(0.08, ft.Colors.BLACK),
                        offset=ft.Offset(0, 2),
                    ),
                ),
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
                ], alignment=ft.MainAxisAlignment.CENTER, vertical_alignment=ft.CrossAxisAlignment.CENTER)
            ],
            expand=True
        )
        
        # Initial update of description
        self._update_description()

    def _on_strategy_changed(self, e):
        """当用户切换策略时，更新下方的描述文字"""
        self._update_description()
        self.update()

    def _on_sort(self, column_index):
        """处理表格列排序"""
        column_name = self._col_map.get(column_index)
        if not column_name:
            return
        
        # 如果点击同一列，切换排序方向；否则默认降序
        if self._sort_column == column_name:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_column = column_name
            self._sort_ascending = False  # 默认降序（大的在前）
        
        # 更新表格的排序指示器
        self.results_table.sort_column_index = column_index
        self.results_table.sort_ascending = self._sort_ascending
        
        # 对数据进行排序
        if self._full_results is not None and not self._full_results.empty:
            try:
                self._full_results = self._full_results.sort_values(
                    by=column_name, 
                    ascending=self._sort_ascending,
                    na_position='last'  # 空值放最后
                )
                # 重置到第一页并刷新
                self.page_no = 1
                self.render_table()
            except KeyError:
                pass  # 如果列不存在，忽略
        
        self.update()

    def _update_description(self):
        key = self.strategy_dropdown.value
        if key:
            st = self.strategy_mgr.get_strategy(key)
            if st:
                self.strategy_desc.value = f"{st.description}"
                # self.strategy_desc.color = ft.Colors.BLACK87 # Already set in init
            else:
                self.strategy_desc.value = ""
        else:
             self.strategy_desc.value = "请选择策略以查看逻辑说明"

    async def on_init_data(self, e):
        await self.init_data_task()

    async def init_data_task(self):
        self.progress_ring.visible = True
        self.status_text.value = "正在初始化/重载数据库..."
        self.update()
        
        try:
            await self.data_processor.init_data()
            self.status_text.value = "数据加载完成"
            self.status_text.color = ft.Colors.GREEN
        except Exception as ex:
            error_msg = f"加载失败: {str(ex)[:40]}"
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
            # Auto-Init Logic: Check if data needs loading
            context = await self.data_processor.get_strategy_data()
            screening_df = context.get('screening_data') if context else None
            
            if screening_df is None or screening_df.empty:
                # Data not ready, Auto-Initialize
                self.status_text.value = "首次运行，正在自动加载数据..."
                self.update()
                
                await self.data_processor.init_data()
                
                # Retry fetch after init
                context = await self.data_processor.get_strategy_data()
                screening_df = context.get('screening_data') if context else None
            
            if screening_df is None or screening_df.empty:
                self.status_text.value = "数据库无数据，请先在设置页同步数据"
                self.status_text.color = ft.Colors.ORANGE
                self.results_table.rows = []
                self._full_results = None
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
                self._full_results = None
                self.update_pagination_controls()
                self.progress_ring.visible = False
                self.update()
                return
            
            # 3. Store results and update UI
            self._full_results = result_df
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
        if self._full_results is None:
            return
        
        # Find stock data
        stock_row = self._full_results[self._full_results['ts_code'] == ts_code]
        if stock_row.empty:
            return
        
        stock_data = stock_row.iloc[0].to_dict()
        self.detail_dialog.update_data(stock_data)
        self.detail_dialog.open = True
        # Flet 0.27.x: Use overlay instead of page.dialog
        if self.detail_dialog not in self.page.overlay:
            self.page.overlay.append(self.detail_dialog)
        self.page.update()

    def change_page(self, delta):
        if self._full_results is None:
            return
        
        start = (self.page_no + delta - 1) * self.page_size
        if start < 0 or start >= len(self._full_results):
            return
            
        self.page_no += delta
        self.render_table()
        self.update()

    def render_table(self):
        if self._full_results is None or self._full_results.empty:
            self.results_table.rows = []
            self.update_pagination_controls()
            return
            
        # Pagination Logic
        total_items = len(self._full_results)
        start_idx = (self.page_no - 1) * self.page_size
        end_idx = min(start_idx + self.page_size, total_items)
        
        # Slice for current page
        page_data = self._full_results.iloc[start_idx:end_idx]
        
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
        if self._full_results is None or self._full_results.empty:
            self.prev_btn.disabled = True
            self.next_btn.disabled = True
            self.page_info.value = "第 0 页 / 共 0 页"
            return
            
        total_items = len(self._full_results)
        total_pages = (total_items + self.page_size - 1) // self.page_size
        
        self.prev_btn.disabled = (self.page_no <= 1)
        self.next_btn.disabled = (self.page_no >= total_pages)
        self.page_info.value = f"第 {self.page_no} 页 / 共 {total_pages} 页 (共 {total_items} 条)"
