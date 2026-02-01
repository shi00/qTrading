import sys
import os

# Determine application root directory
if getattr(sys, 'frozen', False):
    # Running as compiled exe
    APP_ROOT = os.path.dirname(sys.executable)
else:
    # Running from source
    APP_ROOT = os.path.dirname(os.path.abspath(__file__))

DB_PATH = os.path.join(APP_ROOT, "astock.db")


