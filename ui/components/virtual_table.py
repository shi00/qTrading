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
        
        # Header
        self.header_row = ft.Row(spacing=0)
        self.header_container = ft.Container(
            content=self.header_row,
            bgcolor=AppColors.TABLE_HEADER_BG,
            height=35, # Compact header
            border=ft.border.only(bottom=ft.border.BorderSide(1, AppColors.TABLE_BORDER))
        )
        
        # Body
        self.list_view = ft.ListView(
            expand=True,
            spacing=0,
            item_extent=30 # Compact rows
        )
        
        self.controls = [self.header_container, self.list_view]

    def set_columns(self, columns):
        """
        columns: list of {"id": "col1", "label": "Col 1", "width": 100}
        If width is missing, expands.
        """
        self.columns_def = columns
        self._build_header()
        if self.page: self.header_container.update()

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
            
            width = col.get("width")
            
            if width:
                row_controls.append(ft.Container(content, width=width))
            else:
                # Default expand=1 for flexibility
                row_controls.append(ft.Container(content, expand=1))
                
        self.header_row.controls = row_controls

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
                text = ft.Text(val, size=12, no_wrap=True, color=text_color, font_family="Roboto Mono, monospace" if is_numeric else None)
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
            
            row = ft.Container(
                content=ft.Row(cells, spacing=0),
                height=30, # Compact row height
                bgcolor=bg,
                # remove bottom border for cleaner look, relying on zebra striping
            )
            self.list_view.controls.append(row)
            
        if self.page: self.update() # Update full component
