import flet as ft
from ui.theme import AppColors, AppStyles

class VirtualTable(ft.Column):
    """
    High performance table using ListView.
    """
    def __init__(self, on_sort=None):
        super().__init__(expand=True, spacing=0)
        self.on_sort = on_sort
        self.columns_def = []
        self.sort_col = None
        self.sort_asc = True
        
        # Header (static relative to vertical scroll, scrolls horizontally)
        self.header_row = ft.Row(spacing=0)
        self.header_container = ft.Container(
            content=self.header_row,
            bgcolor=AppColors.TABLE_HEADER_BG,
            height=35, # Compact header
            border=ft.border.only(bottom=ft.border.BorderSide(1, AppColors.TABLE_BORDER))
        )
        
        # Body (vertical scrolling)
        self.list_view = ft.ListView(
            expand=True,
            spacing=0,
            item_extent=30  # Compact rows
        )
        
        # Wrapping both Header and ListView inside a single Row that scrolls horizontally
        self.inner_column = ft.Column(
            controls=[self.header_container, self.list_view],
            spacing=0,
            # DO NOT set expand=True horizontally inside a scrolling Row, or Flutter layout will crash. 
            # We explicitly define width in _build_header.
        )
        
        # This is the magic wrapper: One single scrollbar for the entire table content
        self.horizontal_wrapper = ft.Row(
            controls=[self.inner_column],
            expand=True,
            scroll=ft.ScrollMode.ALWAYS,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH
        )
        
        self.controls = [self.horizontal_wrapper]
        
        # Expose a generic on_row_click callback that ScreenerView can listen to
        self.on_row_click = None

    def set_columns(self, columns):
        """
        columns: list of {"id": "col1", "label": "Col 1", "width": 100}
        If width is missing, expands.
        Pure data-setter: caller is responsible for calling page.update().
        """
        self.columns_def = columns
        self._build_header()

    def update_theme(self):
        """Refresh styles on theme change"""
        self.header_container.bgcolor = AppColors.TABLE_HEADER_BG
        self.header_container.border = ft.border.only(bottom=ft.border.BorderSide(1, AppColors.TABLE_BORDER))
        self._build_header() # Rebuild header text colors
        # Re-render rows to update cell colors
        # We don't store row data here easily (we build controls directly),
        # but list_view.controls has the rows. 
        # We can theoretically iterate controls and update colors, but that's hard.
        # Better: let parent re-call set_rows(), or we store data.
        # Given ScreenerView stores memory data, it will call set_rows().
        # So we just update header here.
        if self.page: self.header_container.update()

    def _build_header(self):
        row_controls = []
        total_width = 0
        
        for col in self.columns_def:
            col_id = col["id"]
            label = col.get("label", col_id)
            
            # Sort Indicator
            if self.sort_col == col_id:
                label += " ↑" if self.sort_asc else " ↓"
            
            # Clickable header
            text = ft.Text(label, weight=ft.FontWeight.BOLD, size=12, color=AppColors.TABLE_HEADER_TEXT, no_wrap=True)
            
            # Use a transparent container to capture clicks
            # Using GestureDetector might be better but Container on_click works
            content = ft.Container(
                content=text,
                alignment=ft.alignment.center_left,
                padding=ft.padding.only(left=8, right=8),
                on_click=lambda e, cid=col_id: self._handle_sort_click(cid)
            )
            
            width = col.get("width", 100) # Default to 100 if missing for safe width calculation
            total_width += width
            
            row_controls.append(ft.Container(content, width=width))
                
        self.header_row.controls = row_controls
        
        # Enforce minimum width to trigger horizontal scrolling in the parent Row
        self.inner_column.width = max(total_width, 800) # Ensure it doesn't shrink too small on empty
        self.header_container.width = max(total_width, 800)

    def _handle_sort_click(self, col_id):
        if self.sort_col == col_id:
            self.sort_asc = not self.sort_asc
        else:
            self.sort_col = col_id
            self.sort_asc = True # Default asc
            
        self._build_header()
        if self.page: self.header_container.update()
        
        if self.on_sort:
            self.on_sort(col_id, self.sort_asc)

    def set_rows(self, data_rows, sort_col=None, sort_asc=True):
        """
        data_rows: list of dicts.
        """
        self.sort_col = sort_col
        self.sort_asc = sort_asc
        # Rebuild header to sync sort state
        self._build_header()
        
        self.list_view.controls = []
        for i, row_data in enumerate(data_rows):
            cells = []
            for col in self.columns_def:
                col_id = col["id"]
                val = str(row_data.get(col_id, ""))
                
                # Check for numeric-like content for alignment/color (Simple heuristic)
                is_numeric = False
                try:
                    float(val.replace("%", "").replace(",", "")) # Handle commas in numbers
                    is_numeric = True
                except ValueError:
                    pass
                
                text_color = AppColors.TABLE_CELL_NUMERIC if is_numeric else AppColors.TABLE_CELL_TEXT
                alignment = ft.alignment.center_right if is_numeric else ft.alignment.center_left
                
                # Content
                # Red/Green numeric coloring for A-share (Red up, Green down)
                is_trend = col_id in ['pct_chg', 'change', 'chg']
                if is_trend and is_numeric:
                    try:
                        num_val = float(val.replace("%", "").replace(",", ""))
                        if num_val > 0:
                            text_color = AppColors.UP_RED if hasattr(AppColors, 'UP_RED') else "#F44336"
                        elif num_val < 0:
                            text_color = AppColors.DOWN_GREEN if hasattr(AppColors, 'DOWN_GREEN') else "#4CAF50"
                    except (ValueError, TypeError):
                        pass
                
                # Dim stock code extensions (.SH, .SZ)
                if col_id in ['ts_code', 'symbol'] and "." in val:
                    parts = val.split(".")
                    # We can use formatted text here
                    text = ft.Text(
                        spans=[
                            ft.TextSpan(parts[0], ft.TextStyle(weight=ft.FontWeight.BOLD, color=text_color)),
                            ft.TextSpan("." + parts[1], ft.TextStyle(size=10, color=AppColors.TEXT_TERTIARY if hasattr(AppColors, 'TEXT_TERTIARY') else "#888888"))
                        ],
                        size=12, no_wrap=True
                    )
                else:
                    text_weight = ft.FontWeight.BOLD if is_trend else None
                    text = ft.Text(val, size=12, no_wrap=True, weight=text_weight, color=text_color, font_family="Roboto Mono, monospace" if is_numeric else None)
                    
                content = ft.Container(
                    content=text,
                    alignment=alignment,
                    padding=ft.padding.only(left=8, right=8)
                )
                
                width = col.get("width")
                if width:
                    cells.append(ft.Container(content, width=width))
                else:
                    cells.append(ft.Container(content, expand=1))
            
            # Alternating colors via AppStyles
            bg = AppStyles.data_table_row(i)
            
            # Use sum of column widths to define exact row width and prevent cutoff on scroll
            total_width = sum([col.get("width", 100) for col in self.columns_def])
            
            row = ft.Container(
                content=ft.Row(cells, spacing=0),
                height=30, # Compact row height
                width=max(total_width, 800), # Ensure width matches header
                bgcolor=bg,
                # remove bottom border for cleaner look, relying on zebra striping
                on_click=lambda e, r=row_data: self._handle_row_click(r),
                ink=True # Visual ripple effect on click
            )
            self.list_view.controls.append(row)

        # Note: caller is responsible for calling page.update().
        # Do NOT call self.update() or self.page.update() here — it creates
        # a double-update race with the caller's page.update(), causing Flet
        # to encounter newly-created controls before UIDs are assigned.

    def _handle_row_click(self, row_data):
        if self.on_row_click:
            self.on_row_click(row_data)
