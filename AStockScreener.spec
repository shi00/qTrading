# -*- mode: python ; coding: utf-8 -*-
import atexit
import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, get_package_paths

project_root = Path(SPECPATH)

datas = [
    (str(project_root / "locales"), "locales"),
    (str(project_root / "assets"), "assets"),
    (str(project_root / "alembic"), "alembic"),
    (str(project_root / "alembic.ini"), "."),
    (str(project_root / "data" / "tiktoken_cache"), os.path.join("data", "tiktoken_cache")),
]
# flet 包内 icons.json（material/cupertino）通过 PEP 562 __getattr__ 懒加载，
# PyInstaller 静态分析无法发现，需显式收集。flet 包数据文件仅 5 个（2 json + 2 pyi + 1 typed），全收集无副作用。
datas += collect_data_files("flet")
# flet_code_editor：历史版本包内含 Flutter 资源文件（语法高亮配置、CodeMirror 基础主题等），
# PyInstaller 静态分析无法发现，故显式收集。当前锁定版本实测 collect_data_files("flet_code_editor")
# 返回 0 个文件（包内仅 __init__.py / code_editor.py / types.py），该行退化为无副作用防御性收集：
# 保留以兼容未来版本重新附带资源，每次 Flet 升级后复核返回值即可（D6.3 复核记录）。
datas += collect_data_files("flet_code_editor")
# akshare 包内数据文件（calendar.json 交易日历 + 5 个加密/解密 .js + 1 个 .zip + 1 个 .json），
# 运行时通过 cons.get_calendar() 等函数读取，PyInstaller 静态分析无法发现，全收集（8 文件）。
datas += collect_data_files("akshare")
# litellm 启动时读取包根目录 JSON 数据文件（model_prices_and_context_window_backup.json 等）。
# NOTE(lazy): 只收集根目录 .json，不收集 proxy/llms 等子目录资源. ceiling: litellm 根目录 .json 文件. upgrade: litellm 升级后若运行时报 FileNotFoundError，需扩展收集范围（rglob 或 collect_data_files）.
_litellm_pkg_path = Path(get_package_paths("litellm")[1])
datas += [(str(f), "litellm") for f in _litellm_pkg_path.iterdir() if f.is_file() and f.suffix == ".json"]

hiddenimports = [
    "flet",
    "flet_desktop",
    "flet_charts",
    "flet_code_editor",
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
    "requests",
    "httpx",
    "numpy",
    "tzdata",
    "readerwriterlock",
    "apscheduler.schedulers.background",
    "apscheduler.triggers.cron",
    "apscheduler.triggers.interval",
    "llama_cpp",
]

# llama_cpp 通过 ctypes.CDLL 在 llama_cpp/lib/ 目录加载 llama.dll + ggml*.dll + mtmd.dll，
# PyInstaller 静态分析无法发现，需 collect_dynamic_libs 收集到 _internal/llama_cpp/lib/（保持原相对路径）。
binaries = collect_dynamic_libs("llama_cpp")

# qtrading-pg-sidecar Rust 二进制（pg_plan §15.4）：embedded PostgreSQL sidecar。
# 通过 cargo build --release 单独编译产物（见 .github/workflows/sidecar.yml build-artifacts job），
# PyInstaller 不参与 Rust 编译，仅打包已存在的 binary 到 _internal/sidecars/ 下。
# EmbeddedPostgresService.from_config 在 frozen 模式下从 sys._MEIPASS / "sidecars" 解析（P4-8）。
# 缺失时跳过：开发环境未编译 sidecar 也能构建普通发行物（installer.iss standard variant 不打包 sidecar）。
#
# UIR-F15-005：运行前完整性校验（EmbeddedPostgresService._verify_sidecar_sha256）要求
# 交付与 binary **同目录同名**的 ``<exe>.sha256``。构建时按实际打包的字节计算摘要
# （不依赖 release 资产 ``sha256sums-<target>.txt`` 的命名与清单格式），旁文件与 binary
# 一并收集到 ``_internal/sidecars/``；installer.iss 递归收集该目录并安装到
# ``{app}\_internal\sidecars\``。binary 以原字节复制（strip=False / upx=False），
# 故旁文件摘要即安装后 binary 的实际摘要。
def _cleanup_stage_dirs() -> None:
    """构建进程退出（atexit）时清理 sidecar 旁文件临时目录，不残留临时目录。

    spec 在 PyInstaller 主构建进程内执行：延时到进程退出才删除，保证 Analysis/COLLECT
    读取 ``datas`` 期间旁文件仍在（不破坏打包产物）；COLLECT 已把文件复制进 dist。
    """
    for stage_dir in _stage_dirs:
        shutil.rmtree(stage_dir, ignore_errors=True)
    _stage_dirs.clear()


_stage_dirs: list[Path] = []
atexit.register(_cleanup_stage_dirs)


def _stage_sidecar_with_sha256(binary_path: Path) -> None:
    binaries.append((str(binary_path), "sidecars"))
    stage_dir = Path(tempfile.mkdtemp(prefix="qtrading-sidecar-sha-"))
    _stage_dirs.append(stage_dir)  # 登记供 atexit 清理（构建结束不残留临时目录）
    side_file = stage_dir / f"{binary_path.name}.sha256"
    digest = hashlib.sha256(binary_path.read_bytes()).hexdigest()
    side_file.write_text(f"{digest}  {binary_path.name}\n", encoding="utf-8")
    datas.append((str(side_file), "sidecars"))


_sidecar_exe_name = "qtrading-pg-sidecar.exe" if os.name == "nt" else "qtrading-pg-sidecar"
_sidecar_src = project_root / "sidecars" / "qtrading-pg-sidecar" / "target" / "release" / _sidecar_exe_name
if _sidecar_src.exists():
    _stage_sidecar_with_sha256(_sidecar_src)
else:
    # 开发环境兜底：尝试 sidecars/qtrading-pg-sidecar/{exe_name}（cargo 输出软链接或本地构建）
    _sidecar_alt = project_root / "sidecars" / "qtrading-pg-sidecar" / _sidecar_exe_name
    if _sidecar_alt.exists():
        _stage_sidecar_with_sha256(_sidecar_alt)

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
for src, dst in binaries:
    skip = False
    for pattern in _key_patterns:
        if fnmatch.fnmatch(os.path.basename(src), pattern):
            skip = True
            break
    if not skip:
        _binaries_filtered.append((src, dst))
binaries = _binaries_filtered

icon_path = project_root / "assets" / "hope.ico"
if not icon_path.exists():
    icon_path = project_root / "assets" / "icon.png"

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
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
    upx=False,
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
    upx=False,
    upx_exclude=[],
    name="AStockScreener",
)

