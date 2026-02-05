import asyncio
import logging
import traceback

import flet as ft
import pandas as pd

from data.data_processor import DataProcessor
from data.review_manager import ReviewManager
from strategies.all_strategies import StrategyManager
from ui.components.ai_settings_dialog import AISettingsDialog
from ui.components.stock_detail_dialog import StockDetailDialog
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles

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

        # Lifecycle hooks
        self.did_mount = self._on_mount
        self.will_unmount = self._on_unmount

        # Controls (Improved readability)
        self.strategy_desc = ft.Text(
            value="",
            size=14,
            color=AppColors.TEXT_SECONDARY,
            weight=ft.FontWeight.W_500,
        )

        self.strategy_dropdown = ft.Dropdown(
            label=I18n.get("screener_select_strategy"),
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
            0: 'ts_code',  # 代码
            1: 'name',  # 名称
            2: 'close',  # 现价
            3: 'pct_chg',  # 涨跌幅
            4: 'pe_ttm',  # PE
            5: 'turnover_rate',  # 换手率
            6: 'ai_score'  # AI评分
        }

        self.results_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text(I18n.get("col_ts_code")), on_sort=lambda e: self._on_sort(0)),
                ft.DataColumn(ft.Text(I18n.get("col_name"))),  # 名称不需要排序
                ft.DataColumn(ft.Text(I18n.get("col_ai_score")), numeric=True, on_sort=lambda e: self._on_sort(6)),
                ft.DataColumn(ft.Text(I18n.get("col_price")), numeric=True, on_sort=lambda e: self._on_sort(2)),
                ft.DataColumn(ft.Text(I18n.get("col_chg")), numeric=True, on_sort=lambda e: self._on_sort(3)),
                ft.DataColumn(ft.Text(I18n.get("col_pe")), numeric=True, on_sort=lambda e: self._on_sort(4)),
                ft.DataColumn(ft.Text(I18n.get("col_turnover")), numeric=True, on_sort=lambda e: self._on_sort(5)),
                ft.DataColumn(ft.Text(I18n.get("col_details"))),
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
        self.status_text = ft.Text(I18n.get("status_ready"))
        self.save_switch = ft.Switch(label=I18n.get("screener_auto_save"), value=True)

        # Pagination UI
        self.prev_btn = ft.IconButton(
            ft.Icons.ARROW_BACK_IOS,
            tooltip=I18n.get("screener_page_prev"),
            on_click=lambda e: self.change_page(-1),
            disabled=True
        )
        self.next_btn = ft.IconButton(
            ft.Icons.ARROW_FORWARD_IOS,
            tooltip=I18n.get("screener_page_next"),
            on_click=lambda e: self.change_page(1),
            disabled=True
        )
        self.page_info = ft.Text(I18n.get("screener_page_info").format(current=0, total=0))

        # Stock detail dialog
        self.detail_dialog = StockDetailDialog(data_processor=self.data_processor)

        # Log View
        self.log_view = ft.ListView(
            expand=False,
            height=150,
            spacing=5,
            auto_scroll=True,
        )

        self.content = ft.Column(
            [
                ft.Text(I18n.get("screener_title"), size=24, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
                ft.Container(
                    content=ft.Column([
                        ft.Row([
                            self.strategy_dropdown,
                            ft.FilledButton(
                                text=I18n.get("screener_run"),
                                icon=ft.Icons.PLAY_ARROW,
                                on_click=self.on_run_screening,
                                style=AppStyles.accent_button(),
                            ),

                            ft.IconButton(icon=ft.Icons.REFRESH, tooltip=I18n.get("screener_reload_data"),
                                          on_click=self.on_init_data),
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

                # --- AI Reasoning Log ---
                ft.ExpansionTile(
                    title=ft.Text(I18n.get("screener_ai_log_title"), size=14, weight=ft.FontWeight.BOLD),
                    subtitle=ft.Text(I18n.get("screener_ai_log_subtitle"), size=12, color=ft.Colors.GREY),
                    initially_expanded=True,
                    controls=[
                        ft.Container(
                            content=self.log_view,
                            bgcolor=ft.Colors.BLACK12,
                            padding=10,
                            border_radius=8,
                        )
                    ]
                ),

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

    def _on_mount(self):
        """Subscribe to locale changes when mounted"""
        import time as _time
        _t0 = _time.perf_counter()
        logger.info("[PERF] >>> ScreenerView._on_mount START")
        I18n.subscribe(self.refresh_locale)
        logger.info(f"[PERF] <<< ScreenerView._on_mount END took {(_time.perf_counter()-_t0)*1000:.1f}ms")

    def _on_unmount(self):
        """Cleanup when view is detached"""
        I18n.unsubscribe(self.refresh_locale)

    def refresh_locale(self):
        """Update UI strings on locale change"""
        # Note: For full dynamic update, you would need to rebuild components
        # or update individual .label/.value properties. This is a minimal stub.
        self.strategy_dropdown.label = I18n.get("screener_select_strategy")
        self.save_switch.label = I18n.get("screener_auto_save")
        self.status_text.value = I18n.get("status_ready")
        self.page_info.value = I18n.get("screener_page_info").format(current=0, total=0)
        self._update_description()
        try:
            self.update()
        except Exception:
            pass  # Ignore if page not attached

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
            self.strategy_desc.value = I18n.get("screener_no_strategy_hint")

    async def on_init_data(self, e):
        await self.init_data_task()

    async def init_data_task(self):
        self.progress_ring.visible = True
        self.status_text.value = I18n.get("screener_loading_data")
        self.update()

        try:
            await self.data_processor.init_data()
            self.status_text.value = I18n.get("screener_data_loaded")
            self.status_text.color = ft.Colors.GREEN
        except Exception as ex:
            error_msg = I18n.get("screener_load_failed").format(error=str(ex)[:40])
            self.status_text.value = error_msg
            self.status_text.color = ft.Colors.RED
            logger.error(error_msg)
        finally:
            self.progress_ring.visible = False
            self.update()

    async def select_and_run_strategy(self, strategy_key: str):
        """Programmatically select and run a strategy"""
        # Verify key exists
        if strategy_key not in self.strategy_mgr.strategies:
            logger.warning(f"Strategy {strategy_key} not found")
            return

        # Select dropdown
        self.strategy_dropdown.value = strategy_key
        # Trigger update description
        self._update_description()
        self.update()

        # Run
        await self.run_screening_async()

    async def on_run_screening(self, e):
        if not self.strategy_dropdown.value:
            self.status_text.value = I18n.get("screener_please_select")
            self.status_text.color = ft.Colors.RED
            self.update()
            return
        await self.run_screening_async()

    async def run_screening_async(self):
        strategy_key = self.strategy_dropdown.value
        strategy = self.strategy_mgr.get_strategy(strategy_key)

        if strategy is None:
            self.status_text.value = I18n.get("screener_strategy_not_found")
            self.status_text.color = ft.Colors.RED
            self.update()
            return

        self.progress_ring.visible = True
        self.status_text.value = I18n.get("screener_syncing").format(name=strategy.name)
        self.status_text.color = ft.Colors.BLUE
        self.update()

        try:
            # 1. Prepare Data Context
            # Auto-Init Logic: Check if data needs loading
            context = await self.data_processor.get_strategy_data()
            # Inject Data Processor so strategies can fetch extra data (like History/News) for AI
            if context:
                context['data_processor'] = self.data_processor

            screening_df = context.get('screening_data') if context else None

            if screening_df is None or screening_df.empty:
                # Data not ready, Auto-Initialize
                self.status_text.value = I18n.get("screener_first_run")
                self.update()

                await self.data_processor.init_data()

                # Retry fetch after init
                context = await self.data_processor.get_strategy_data()
                if context:
                    context['data_processor'] = self.data_processor

                screening_df = context.get('screening_data') if context else None

            if screening_df is None or screening_df.empty:
                self.status_text.value = I18n.get("screener_data_ready")
                self.status_text.color = ft.Colors.ORANGE
                self.results_table.rows = []
                self._full_results = None
                self.update_pagination_controls()
                self.progress_ring.visible = False
                self.update()
                return

            # 2. Execute Strategy

            # --- Progress & Streaming Handlers ---
            total_candidates = 0

            def on_progress(done, total, msg):
                nonlocal total_candidates
                total_candidates = total
                pct = done / total if total > 0 else 0
                self.progress_ring.value = pct
                self.status_text.value = I18n.get("screener_ai_analyzing").format(done=done, total=total, msg=msg)
                self.update()

            def on_stream_result(row_data):
                """Handle single row result from AI"""
                if not row_data: return

                # Update Log View with Reasoning
                stock_name = row_data.get('name', 'Unknown')
                score = row_data.get('ai_score', 0)
                thinking = row_data.get('thinking', '')  # Need to ensure Strategies return this in row_data

                # Create Log Entry
                if thinking:
                    # Truncate for preview
                    preview = thinking[:100] + "..." if len(thinking) > 100 else thinking
                    log_entry = ft.Text(f"[{stock_name}] Score:{score} | {preview}", size=12, font_family="Consolas")
                    self.log_view.controls.append(log_entry)
                else:
                    self.log_view.controls.append(
                        ft.Text(f"[{stock_name}] Finished analysis (Score: {score})", size=12, color=ft.Colors.GREY))

                # Append to _full_results immediately?
                # Need to be careful about threading and DataFrame concat performance.
                # But for 30 items it's fine.

                new_df = pd.DataFrame([row_data])
                if self._full_results is None or self._full_results.empty:
                    self._full_results = new_df
                else:
                    self._full_results = pd.concat([self._full_results, new_df], ignore_index=True)

                # Sort descending by Score naturally (or keep as is)
                # Let's sort to keep best on top
                if 'ai_score' in self._full_results.columns:
                    self._full_results = self._full_results.sort_values('ai_score', ascending=False)

                # Render (throttle if needed, but 30 items is slow enough)
                self.render_table()
                self.update()

            # Inject callbacks into context
            context['on_progress'] = on_progress
            context['on_result'] = on_stream_result

            # Clear previous results before run if it's AI strategy
            if strategy_key == "ai_active":
                self._full_results = pd.DataFrame()
                self.results_table.rows = []
                self.log_view.controls.clear()  # Clear logs
                self.update()

            try:
                if asyncio.iscoroutinefunction(strategy.filter):
                    result_df = await strategy.filter(context)
                else:
                    result_df = strategy.filter(context)
            except Exception as filter_ex:
                self.status_text.value = I18n.get("screener_filter_error").format(error=str(filter_ex)[:40])
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
                self.status_text.value = I18n.get("screener_no_results")
                self.status_text.color = ft.Colors.ORANGE
                self.results_table.rows = []
                self.update_pagination_controls()
            else:
                count = len(result_df)
                msg = I18n.get("screener_done").format(count=count)

                # Auto-save results to Review System
                if self.save_switch.value:
                    await self.review_mgr.save_results(strategy.name, result_df)
                    msg += " " + I18n.get("screener_saved")

                self.status_text.value = msg
                self.status_text.color = ft.Colors.GREEN
                self.render_table()

        except Exception as ex:
            self.status_text.value = I18n.get("screener_exec_error").format(error=str(ex)[:50])
            self.status_text.color = ft.Colors.RED
            logger.error(f"Screening error: {traceback.format_exc()}")

        self.progress_ring.visible = False
        self.update()

    async def show_stock_detail(self, ts_code: str):
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

        # Load chart asynchronously
        await self.detail_dialog.load_chart(ts_code)

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

                # AI Score
            ai_score = row.get('ai_score', 0)
            score_str = f"{int(ai_score)}" if ai_score and ai_score > 0 else "-"
            score_color = ft.Colors.GREEN if (ai_score or 0) >= 80 else (
                ft.Colors.ORANGE if (ai_score or 0) >= 60 else ft.Colors.GREY)
            if score_str == "-": score_color = ft.Colors.BLACK

            # AI Reason for Tooltip
            ai_reason = row.get('ai_reason', '')
            score_tooltip = f"{I18n.get('col_ai_score')}: {score_str}\n{ai_reason}" if ai_reason else f"{I18n.get('col_ai_score')}: {score_str}"

            # Create detail button with stock code reference
            # Define async handler closure to verify loop execution
            def create_click_handler(ts_code):
                async def handler(e):
                    await self.show_stock_detail(ts_code)

                return handler

            detail_btn = ft.IconButton(
                ft.Icons.INFO_OUTLINE,
                tooltip=I18n.get("screener_view_details"),
                on_click=create_click_handler(code),
            )

            rows.append(ft.DataRow(cells=[
                ft.DataCell(ft.Text(code)),
                ft.DataCell(ft.Text(name)),
                ft.DataCell(ft.Container(
                    content=ft.Text(score_str, color=ft.Colors.WHITE, size=12, weight=ft.FontWeight.BOLD),
                    bgcolor=score_color,
                    padding=ft.padding.symmetric(horizontal=8, vertical=2),
                    border_radius=10,
                    alignment=ft.alignment.center,
                    tooltip=score_tooltip  # Show reasoning on hover
                )),
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
            self.page_info.value = I18n.get("screener_page_info").format(current=0, total=0)
            return

        total_items = len(self._full_results)
        total_pages = (total_items + self.page_size - 1) // self.page_size

        self.prev_btn.disabled = (self.page_no <= 1)
        self.next_btn.disabled = (self.page_no >= total_pages)
        self.page_info.value = I18n.get("screener_page_info").format(current=self.page_no,
                                                                     total=total_pages) + f" ({total_items})"

    def _open_ai_settings(self, e):
        dialog = AISettingsDialog(self.page)
        self.page.dialog = dialog
        dialog.open = True
        self.page.update()
