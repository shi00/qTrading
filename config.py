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

# Pre-configure tiktoken cache directory BEFORE litellm/tiktoken is imported.
# This ensures the bundled encoding files (cl100k_base, o200k_base) are used
# instead of downloading from openaipublic.blob.core.windows.net at runtime,
# which fails in mainland China due to SSL/GFW issues.
# NOTE: setdefault is used intentionally — tiktoken requires this env var
# before import and has no programmatic API to set cache_dir.  Unlike
# NO_PROXY (which affects all HTTP clients globally), TIKTOKEN_CACHE_DIR
# is only read by tiktoken itself, so the scope of pollution is minimal.
_tiktoken_cache = os.path.join(APP_ROOT, "data", "tiktoken_cache")
if os.path.isdir(_tiktoken_cache):
    os.environ.setdefault("TIKTOKEN_CACHE_DIR", _tiktoken_cache)

# PostgreSQL connection URL (async driver for CacheManager / DAOs)
# SECURITY: DATABASE_URL should be set via environment variable or .env file.
# Example: DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/astock
# If not set, the onboarding wizard will guide users to configure it.
DB_URL = os.environ.get("DATABASE_URL") or None

# Synchronous connection URL (for DatabaseManager read-only queries)
# Safe handling: DB_URL_SYNC is None when DB_URL is None
DB_URL_SYNC = DB_URL.replace("+asyncpg", "") if DB_URL else None
