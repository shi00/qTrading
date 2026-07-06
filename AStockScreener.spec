# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

project_root = Path(SPECPATH)

datas = [
    (str(project_root / "locales"), "locales"),
    (str(project_root / "assets"), "assets"),
    (str(project_root / "alembic"), "alembic"),
    (str(project_root / "alembic.ini"), "."),
    (str(project_root / "data" / "tiktoken_cache"), os.path.join("data", "tiktoken_cache")),
]

hiddenimports = [
    "flet",
    "flet_core",
    "flet_charts",
    "pandas",
    "polars",
    "pyarrow",
    "asyncpg",
    "psycopg2",
    "sqlalchemy",
    "alembic",
    "tushare",
    "akshare",
    "litellm",
    "apscheduler",
    "keyring",
    "cryptography",
    "matplotlib",
    "mplfinance",
    "requests",
    "httpx",
    "numpy",
    "pytz",
    "tzdata",
    "readerwriterlock",
    "apscheduler.schedulers.background",
    "apscheduler.triggers.cron",
    "apscheduler.triggers.interval",
    "llama_cpp",
]


_CUDA_DLL_KEYWORDS = ("cuda", "cublas", "cudart", "cufft", "curand", "cusolver", "cusparse", "nvrtc", "llama")


def collect_cuda_dlls():
    """Collect llama-cpp-python CUDA DLLs for packaging."""
    binaries = []
    try:
        import llama_cpp

        llama_dir = Path(llama_cpp.__file__).parent
        for dll_pattern in ["*.dll", "**/*.dll"]:
            for dll in llama_dir.glob(dll_pattern):
                if dll.is_file():
                    dll_lower = dll.name.lower()
                    if any(kw in dll_lower for kw in _CUDA_DLL_KEYWORDS):
                        binaries.append((str(dll), "."))
                        print(f"[CUDA Hook] Collected DLL: {dll.name}")
                    else:
                        print(f"[CUDA Hook] Skipped non-CUDA DLL: {dll.name}")
    except ImportError:
        print("[CUDA Hook] llama_cpp not installed, skipping CUDA DLL collection")
    except Exception as e:
        print(f"[CUDA Hook] Warning: Failed to collect CUDA DLLs: {e}")
    return binaries


cuda_binaries = collect_cuda_dlls()

excludes = [
    "tkinter",
    "test",
    "tests",
    "unittest",
    "IPython",
    "jupyter",
    "pytest",
    "_pytest",
]

import fnmatch
import glob as _glob

_key_patterns = ["*.key", "*.key.bak", "*.key.tmp", "*.salt", "*.salt.tmp", "*.legacy"]

_datas_filtered = []
for src, dst in datas:
    skip = False
    for pattern in _key_patterns:
        if fnmatch.fnmatch(os.path.basename(src), pattern):
            skip = True
            break
    if not skip:
        _datas_filtered.append((src, dst))
datas = _datas_filtered

_binaries_filtered = []
_binaries_key_excludes = []
for root, dirs, files in os.walk(str(project_root)):
    for f in files:
        for pattern in _key_patterns:
            if fnmatch.fnmatch(f, pattern):
                rel = os.path.relpath(os.path.join(root, f), str(project_root))
                _binaries_key_excludes.append(rel)
                break

icon_path = project_root / "assets" / "icon.ico"
if not icon_path.exists():
    icon_path = project_root / "assets" / "icon.png"

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=cuda_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AStockScreener",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path) if icon_path.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="AStockScreener",
)
