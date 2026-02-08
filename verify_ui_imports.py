
import flet as ft
import sys
import os
import logging

sys.path.append(os.getcwd())
logging.basicConfig(level=logging.INFO)

try:
    print("--- Testing UI Imports ---")
    from ui.app_layout import AppLayout, NavTabs
    print("[OK] ui.app_layout imported successfully.")
    
    # Mock Page
    class MockPage:
        def __init__(self):
            self.title = ""
            self.window_icon = ""
            self.padding = 0
            self.theme_mode = ""
            self.on_disconnect = None
            self.controls = []
            self.overlay = []
            
        def clean(self): pass
        def add(self, *args): pass
        def update(self): pass
        def run_task(self, task): return None
        def open(self, control): pass

    print("--- Instantiating AppLayout ---")
    # This will trigger __init__ of AppLayout and its sub-views
    # Note: Some views might try to access DB or Singletons.
    # We should ensure ConfigHandler defaults are present.
    from utils.config_handler import ConfigHandler
    ConfigHandler.ensure_defaults()
    
    layout = AppLayout(MockPage())
    print(f"[OK] AppLayout instantiated. NavTabs available: {list(NavTabs)}")
    
except Exception as e:
    print(f"[FAIL] UI Verification Failed: {e}")
    import traceback
    traceback.print_exc()
