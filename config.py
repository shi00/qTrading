import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# 程序资源目录：只读，随安装包分发（tiktoken 编码文件、locales、PG sidecar 二进制等）。
# 打包后 = 可执行文件所在目录；源码运行 = 本文件所在目录。用户数据**不得**写于此
# （安装目录通常不可写，多用户共用互相覆盖，卸载/升级会丢失）。
if getattr(sys, "frozen", False):
    RESOURCE_ROOT = os.path.dirname(sys.executable)
else:
    RESOURCE_ROOT = os.path.dirname(os.path.abspath(__file__))

# 用户数据目录（D8-4）：可写、per-user、卸载不丢失，遵循各平台约定。
# 密钥/配置/日志必须位于此而非 RESOURCE_ROOT —— security_utils 的多用户隔离
# 意图（_get_machine_fingerprint 含 USERNAME）与机器级共享的文件位置矛盾。
# ASTOCK_USER_DATA_DIR 环境变量用于测试隔离与高级自定义。
_USER_DATA_DIR_ENV = "ASTOCK_USER_DATA_DIR"


def _user_data_dir(app_name: str = "AStockScreener") -> str:
    env = os.environ.get(_USER_DATA_DIR_ENV)
    if env:
        return env
    try:
        import platformdirs

        return str(platformdirs.user_data_dir(app_name, appauthor=False))
    except ImportError:
        pass
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return os.path.join(base, app_name)


USER_DATA_ROOT = _user_data_dir()

# 兼容别名：APP_ROOT 曾经同时承担"资源目录"与"用户数据目录"两个职责。
# 此处仅作策略别名指向 RESOURCE_ROOT；用户数据（密钥/配置/日志）一律用 USER_DATA_ROOT，
# 存量引用逐步迁移，禁止再以 APP_ROOT 作为用户数据写入根。
APP_ROOT = RESOURCE_ROOT

# Pre-configure tiktoken cache directory BEFORE litellm/tiktoken is imported.
# This ensures the bundled encoding files (cl100k_base, o200k_base) are used
# instead of downloading from openaipublic.blob.core.windows.net at runtime,
# which fails in mainland China due to SSL/GFW issues.
# NOTE: setdefault is used intentionally — tiktoken requires this env var
# before import and has no programmatic API to set cache_dir.  Unlike
# NO_PROXY (which affects all HTTP clients globally), TIKTOKEN_CACHE_DIR
# is only read by tiktoken itself, so the scope of pollution is minimal.
_tiktoken_cache = os.path.join(RESOURCE_ROOT, "data", "tiktoken_cache")
if os.path.isdir(_tiktoken_cache):
    os.environ.setdefault("TIKTOKEN_CACHE_DIR", _tiktoken_cache)

# PostgreSQL connection URL (async driver for CacheManager / DAOs)
# SECURITY: DATABASE_URL should be set via environment variable or .env file.
# Example: DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/astock
# If not set, the onboarding wizard will guide users to configure it.
DB_URL = os.environ.get("DATABASE_URL") or None

# Synchronous connection URL (for DataExplorerQueryClient read-only queries)
# Safe handling: DB_URL_SYNC is None when DB_URL is None
DB_URL_SYNC = DB_URL.replace("+asyncpg", "") if DB_URL else None
