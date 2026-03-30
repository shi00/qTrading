import os
import sys

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# Determine application root directory
if getattr(sys, "frozen", False):
    # Running as compiled exe
    APP_ROOT = os.path.dirname(sys.executable)
else:
    # Running from source
    APP_ROOT = os.path.dirname(os.path.abspath(__file__))

# PostgreSQL connection URL (async driver for CacheManager / DAOs)
# SECURITY: DATABASE_URL should be set via environment variable or .env file.
# Example: DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/astock
# If not set, the onboarding wizard will guide users to configure it.
DB_URL = os.environ.get("DATABASE_URL") or None

# Synchronous connection URL (for DatabaseManager read-only queries)
# Safe handling: DB_URL_SYNC is None when DB_URL is None
DB_URL_SYNC = DB_URL.replace("+asyncpg", "") if DB_URL else None
