# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

project_root = Path(SPECPATH)

datas = [
    (str(project_root / "locales"), "locales"),
    (str(project_root / "assets"), "assets"),
    (str(project_root / "alembic"), "alembic"),
    (str(project_root / "alembic.ini"), "."),
]

hiddenimports = [
    "flet",
    "flet_core",
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

icon_path = project_root / "assets" / "icon.ico"
if not icon_path.exists():
    icon_path = project_root / "assets" / "icon.png"

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
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

