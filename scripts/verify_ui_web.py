
import flet as ft
import logging
import sys
import os

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.components.health_report_dialog import HealthReportDialog
from ui.i18n import I18n

# Initialize I18n (Mock or Real)
try:
    I18n.load_locale('zh_CN') 
except:
    pass 

def main(page: ft.Page):
    page.title = "UI Verification"
    page.vertical_alignment = ft.MainAxisAlignment.CENTER
    page.horizontal_alignment = ft.CrossAxisAlignment.CENTER

    # Dummy Report Data
    dummy_report = {
        'status': 'yellow',
        'reasons': ['Market Lag > 3 Days', 'Financial Data Missing'],
        'market': {'lag_days': 5, 'latest_local': '20231001'},
        'fundamentals': {
            'tables': {
                'daily_quotes': {'ratio': 1.0, 'covered': 5000},
                'financial_reports': {'ratio': 0.8, 'covered': 4000},
                'macro_economy': {'ratio': 1.0, 'covered': 120, 'type': 'global'}
            },
            'gap_count': 10,
            'sanity_errors': 2
        }
    }

    def open_dialog(e):
        dialog = HealthReportDialog(page, dummy_report)
        page.dialog = dialog
        dialog.open = True
        page.update()

    btn = ft.ElevatedButton("Open Health Report", on_click=open_dialog)
    page.add(btn)
    
    # Auto-open for testing
    open_dialog(None)

if __name__ == "__main__":
    print("Starting Flet Web App on port 8550...")
    ft.app(target=main, port=8550, view=ft.AppView.WEB_BROWSER)
