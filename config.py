import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Determine application root directory
if getattr(sys, 'frozen', False):
    # Running as compiled exe
    APP_ROOT = os.path.dirname(sys.executable)
else:
    # Running from source
    APP_ROOT = os.path.dirname(os.path.abspath(__file__))

# PostgreSQL connection URL (async driver for CacheManager / DAOs)
# To keep passwords secure, set the DATABASE_URL environment variable 
# in your system or use a .env file (requires python-dotenv).
DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:YOUR_PASSWORD_HERE@localhost:5432/astock"
)

# Synchronous connection URL (for DatabaseManager read-only queries)
DB_URL_SYNC = DB_URL.replace("+asyncpg", "")


