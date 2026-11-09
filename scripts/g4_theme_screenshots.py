"""G4-202 四主题截图判读脚本（Flet Windows 原生客户端，Impeller 默认后端 + Skia 回退）。

背景与用途
----------
``reviews/flet upgrade 1.1.0.md`` 的 G4-202 要求：用默认 Impeller 完成四主题
（DARK / LIGHT / NAVY / DRACULA）关键页、图标、图表、CodeEditor、中文文字/输入、
Dialog、Dropdown、窗口缩放、最小化恢复和关闭的截图；再用 §5.11 的 Skia 回退
（``FLET_NO_IMPELLER``）重跑关键路径；截图与 G0 对照并做人工判读。

本脚本负责「采集可判读的证据」，不负责判定 G4-202 通过：
- 后端 × 主题矩阵：默认 Impeller 与 ``FLET_NO_IMPELLER=true``（Skia 回退）；
- 自动形态：默认窗口、窄屏、宽屏、最小化后恢复、关闭确认弹窗；
- 人工导航：交互模式下由操作者把应用导航到关键页后按键截图；
- 元数据：每张图记录窗口尺寸、DPI、后端、主题、文件 SHA-256；manifest 记录
  OS / GPU / 驱动 / locale / 客户端摘要，取不到的字段写 ``null`` + ``reason``
  （R21：不得用空串或「看起来正常」的占位值冒充已核对）；
- 主题核验：取截图主色与该主题 ``THEME_COLOR_SCHEMES[theme].surface`` 比对，
  防止「配置写了 navy、实际回退 dark」被当成 navy 证据；
- 基线差分：``--compare <基线目录>`` 用 Pillow 做像素差分。G0 四主题截图基线未随
  仓库交付时 manifest 明确记 ``baseline.present=false``，差分不会被记成「无差异」。

运行（Windows PowerShell，仓库根目录）::

    # 四主题 × 双后端，自动形态 + 关键页人工导航截图
    python scripts/g4_theme_screenshots.py --exe "C:\\Users\\me\\AppData\\Local\\Programs\\AStockScreener\\AStockScreener.exe"

    # 先快速验证：只跑 Impeller 的 dark/navy
    python scripts/g4_theme_screenshots.py --exe <exe> --backends impeller --themes dark,navy

    # 与 G0 基线（或上一轮候选）做像素差分
    python scripts/g4_theme_screenshots.py --compare evidence/g4-202-g0 --compare-only

退出码
------
- 0：请求的采集全部完成（不含人工判读结论），或差分执行成功；
- 1：主题未真正生效、主题主色无法核验，或出现空白截图（证据无效）；
- 2：环境/参数错误（非 Windows、启动失败、找不到窗口、基线目录缺失）。

产出目录
--------
``<output>/<backend>/<theme>/``：截图 PNG、``launch.log``、隔离配置 ``user_settings.json``；
``<output>/manifest.json``：环境、客户端摘要、每次运行的元数据与全部截图条目；
``<output>/compare.json`` / ``compare.md``：``--compare`` 的差分结果。
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageGrab

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    # 以 ``python scripts/xxx.py`` 运行时 sys.path[0] 是 scripts/，补入仓库根以便导入 ui.theme。
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_OUTPUT = REPO_ROOT / "evidence" / "g4-202"
MANIFEST_NAME = "manifest.json"
MANIFEST_SCHEMA_VERSION = 1

# 主题白名单与 ui/theme.py 的 ThemeName 一致；config 校验也以 SUPPORTED_THEME_NAMES 为准。
THEMES = ("dark", "light", "navy", "dracula")
BACKENDS = ("impeller", "skia")
AUTO_SCENES = ("default", "narrow", "wide", "restored")
IMPELLER_SCENES = (
    "selection",
    "data_source",
    "watchlist",
    "charts",
    "code_editor",
    "markdown",
    "dialog",
    "dropdown",
    "chinese_input",
    "icons",
)
SKIA_SCENES = ("charts", "code_editor", "chinese_input", "dialog")
WINDOW_SIZES = {"narrow": (1024, 720), "wide": (1680, 960)}

SCENE_LABELS = {
    "selection": "选股页（含筛选结果表格）",
    "data_source": "数据源页",
    "watchlist": "自选股页",
    "charts": "图表（K 线/指标）",
    "code_editor": "CodeEditor（策略编辑）",
    "markdown": "Markdown 渲染（如公告/说明）",
    "dialog": "Dialog（弹窗）",
    "dropdown": "Dropdown（下拉选择）",
    "chinese_input": "中文文字显示与输入",
    "icons": "图标（导航图标/按钮图标）",
}

THEME_DISTANCE_LIMIT = 40.0
BLACK_RATIO_THRESHOLD = 0.05
BLANK_STD_THRESHOLD = 2.0
SETTLE_SECONDS = 0.9
MINIMIZE_SETTLE_SECONDS = 1.2
RESTORE_SETTLE_SECONDS = 1.2
CLOSE_SETTLE_SECONDS = 2.0
CREATE_NO_WINDOW = 0x08000000
SW_MINIMIZE = 6
SW_RESTORE = 9
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
WM_CLOSE = 0x0010
PW_RENDERFULLCONTENT = 0x00000002
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


# ============================================================================
# 纯函数（可在非 Windows 环境单测）
# ============================================================================
def parse_choices(raw: str, allowed: tuple[str, ...], label: str) -> tuple[str, ...]:
    """解析逗号分隔选项并按 ``allowed`` 顺序去重；非法值直接报错。"""
    requested = [item.strip().lower() for item in raw.split(",") if item.strip()]
    if not requested:
        print(f"2: {label} 不能为空", file=sys.stderr)
        raise SystemExit(2)
    invalid = [item for item in requested if item not in allowed]
    if invalid:
        print(f"2: 非法 {label}：{invalid}；可选值 {list(allowed)}", file=sys.stderr)
        raise SystemExit(2)
    return tuple(item for item in allowed if item in requested)


def build_launch_config(theme: str) -> dict[str, Any]:
    """构造隔离的用户配置：固定主题、跳过向导、中文界面。"""
    if theme not in THEMES:
        raise ValueError(f"未知主题：{theme}")
    return {
        "theme_name": theme,
        "locale": "zh_CN",
        "onboarding_complete": True,
        "risk_disclaimer_acknowledged": True,
    }


def build_launch_env(
    theme: str,
    backend: str,
    config_path: Path,
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """构造子进程环境：隔离配置 + 后端开关（Skia 依赖 ``FLET_NO_IMPELLER``）。"""
    if backend not in BACKENDS:
        raise ValueError(f"未知后端：{backend}")
    env = os.environ.copy()
    env["ASTOCK_CONFIG_FILE"] = str(config_path)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("FLET_FORCE_WEB_SERVER", None)
    env.pop("FLET_SERVER_PORT", None)
    if backend == "skia":
        env["FLET_NO_IMPELLER"] = "true"
    else:
        env.pop("FLET_NO_IMPELLER", None)
    env.update(extra_env or {})
    return env


def scene_plan(backend: str, overrides: tuple[str, ...] | None = None) -> tuple[str, ...]:
    """返回该后端的历史形态场景 + 关键页场景。"""
    if overrides:
        return overrides
    return AUTO_SCENES + (IMPELLER_SCENES if backend == "impeller" else SKIA_SCENES)


def sha256_file(path: Path) -> str | None:
    """文件不存在或不可读时返回 None（由调用方写明 reason，不填占位值）。"""
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def hex_to_rgb(value: str) -> tuple[int, int, int] | None:
    """解析 ``#RRGGBB`` / ``#AARRGGBB``；失败返回 None。"""
    text = value.strip().lstrip("#")
    if len(text) == 8:  # AARRGGBB
        text = text[2:]
    if len(text) != 6 or re.fullmatch(r"[0-9a-fA-F]{6}", text) is None:
        return None
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def dominant_color(image: Image.Image) -> tuple[int, int, int]:
    """截图主色：缩样后按 8 级量化取众数，抵消抗锯齿与渐变噪声。"""
    import numpy as np

    sample = image.convert("RGB").resize((64, 64))
    pixels = np.asarray(sample, dtype=np.uint8).reshape(-1, 3)
    quantized = (pixels // 8 * 8).tolist()
    counts = Counter(tuple(pixel) for pixel in quantized)
    red, green, blue = counts.most_common(1)[0][0]
    return int(red), int(green), int(blue)


def expected_surface_color(theme: str) -> str | None:
    """读取 ``ui.theme.THEME_COLOR_SCHEMES`` 中该主题的 surface；不可用时返回 None。"""
    try:
        from ui.theme import THEME_COLOR_SCHEMES
    except Exception:
        return None
    scheme = THEME_COLOR_SCHEMES.get(theme)
    if scheme is None:
        return None
    surface = getattr(scheme, "surface", None)
    return str(surface) if surface is not None else None


def verify_theme_color(image: Image.Image, theme: str) -> dict[str, Any]:
    """用截图主色核验「请求主题 == 实际渲染主题」。

    NAVY(#1E293B) 与 DRACULA(#282A36) 的底色很接近，绝对容差无法可靠区分，
    因此先取「最接近的主题」，再要求它等于请求主题且距离不超过阈值。
    """
    dominant = dominant_color(image)
    dominant_hex = "#{:02X}{:02X}{:02X}".format(*dominant)
    surfaces = {name: hex_to_rgb(expected_surface_color(name) or "") for name in THEMES}
    available = {name: rgb for name, rgb in surfaces.items() if rgb is not None}
    if theme not in available:
        return {
            "expected_surface": None,
            "dominant_color": dominant_hex,
            "nearest_theme": None,
            "distance": None,
            "match": None,
            "reason": "ui.theme.THEME_COLOR_SCHEMES 不可用（脚本环境未安装 flet 或导入失败）",
        }
    distances = {name: _rgb_distance(dominant, rgb) for name, rgb in available.items()}
    nearest = min(distances, key=lambda name: distances[name])
    distance = distances[nearest]
    if nearest != theme:
        reason = f"主色最接近 {nearest}（距离 {distance:.1f}），与请求主题 {theme} 不符"
    elif distance > THEME_DISTANCE_LIMIT:
        reason = f"主色与 {theme} surface 距离 {distance:.1f}，超过阈值 {THEME_DISTANCE_LIMIT:.0f}"
    else:
        reason = None
    return {
        "expected_surface": "#{:02X}{:02X}{:02X}".format(*available[theme]),
        "dominant_color": dominant_hex,
        "nearest_theme": nearest,
        "distance": round(distance, 1),
        "match": reason is None,
        "reason": reason,
    }


def _rgb_distance(left: tuple[int, int, int], right: tuple[int, int, int]) -> float:
    return sum((a - b) ** 2 for a, b in zip(left, right, strict=True)) ** 0.5


def diff_images(baseline: Path, candidate: Path, threshold: int) -> dict[str, Any]:
    """逐像素差分；尺寸不一致或不可读时返回不可比而非「无差异」。"""
    try:
        with Image.open(baseline) as base_img, Image.open(candidate) as cand_img:
            base = base_img.convert("RGB")
            cand = cand_img.convert("RGB")
            if base.size != cand.size:
                return {
                    "comparable": False,
                    "reason": f"尺寸不一致：基线 {base.size} vs 候选 {cand.size}",
                }
            import numpy as np

            base_arr = np.asarray(base, dtype=np.int16)
            cand_arr = np.asarray(cand, dtype=np.int16)
            delta = np.abs(base_arr - cand_arr).max(axis=2)
            changed = int((delta > threshold).sum())
            total = int(delta.size)
            return {
                "comparable": True,
                "reason": None,
                "changed_pixels": changed,
                "total_pixels": total,
                "changed_ratio": round(changed / total, 6) if total else 0.0,
                "max_channel_diff": int(delta.max()) if total else 0,
            }
    except OSError as exc:
        return {"comparable": False, "reason": f"图片不可读：{exc}"}


def compare_trees(baseline: Path, candidate: Path, threshold: int) -> dict[str, Any]:
    """按相对路径对齐两张目录树，输出可人工复核的差分清单。"""
    baseline_files = {path.relative_to(baseline).as_posix(): path for path in baseline.rglob("*.png")}
    candidate_files = {path.relative_to(candidate).as_posix(): path for path in candidate.rglob("*.png")}
    items: list[dict[str, Any]] = []
    for rel in sorted(candidate_files):
        if rel not in baseline_files:
            items.append({"file": rel, "status": "missing_baseline"})
            continue
        detail = diff_images(baseline_files[rel], candidate_files[rel], threshold)
        if not detail.get("comparable"):
            items.append({"file": rel, "status": "not_comparable", "detail": detail})
        elif detail["changed_ratio"] == 0.0:
            items.append({"file": rel, "status": "identical", "detail": detail})
        else:
            items.append({"file": rel, "status": "different", "detail": detail})
    for rel in sorted(set(baseline_files) - set(candidate_files)):
        items.append({"file": rel, "status": "missing_candidate"})

    counts = Counter(item["status"] for item in items)
    return {
        "baseline": str(baseline),
        "candidate": str(candidate),
        "threshold": threshold,
        "summary": {
            "total": len(items),
            "identical": counts.get("identical", 0),
            "different": counts.get("different", 0),
            "not_comparable": counts.get("not_comparable", 0),
            "missing_baseline": counts.get("missing_baseline", 0),
            "missing_candidate": counts.get("missing_candidate", 0),
        },
        "items": items,
    }


def write_compare_report(result: dict[str, Any], candidate: Path) -> None:
    """写 compare.json + compare.md（Markdown 便于贴进验收记录）。"""
    (candidate / "compare.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = result["summary"]
    lines = [
        "# G4-202 四主题截图差分",
        "",
        f"- 基线：`{result['baseline']}`",
        f"- 候选：`{result['candidate']}`",
        f"- 单像素差异阈值：{result['threshold']}（0-255 通道最大绝对差）",
        "",
        "| 状态 | 数量 |",
        "|------|------|",
        f"| identical | {summary['identical']} |",
        f"| different | {summary['different']} |",
        f"| not_comparable | {summary['not_comparable']} |",
        f"| missing_baseline | {summary['missing_baseline']} |",
        f"| missing_candidate | {summary['missing_candidate']} |",
        "",
    ]
    different = [item for item in result["items"] if item["status"] in {"different", "not_comparable"}]
    if different:
        lines += [
            "## 需人工判读的差异",
            "",
            "| 文件 | 状态 | 变化像素占比 | 最大通道差 |",
            "|------|------|--------------|------------|",
        ]
        for item in different:
            detail = item.get("detail") or {}
            ratio = detail.get("changed_ratio")
            max_diff = detail.get("max_channel_diff")
            lines.append(
                f"| {item['file']} | {item['status']} | "
                f"{'-' if ratio is None else f'{ratio:.4%}'} | {'-' if max_diff is None else max_diff} |"
            )
    (candidate / "compare.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ============================================================================
# Win32 窗口捕获（仅 Windows；非 Windows 导入模块供单测使用）
# ============================================================================
class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("cntUsage", ctypes.c_uint32),
        ("th32ProcessID", ctypes.c_uint32),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", ctypes.c_uint32),
        ("cntThreads", ctypes.c_uint32),
        ("th32ParentProcessID", ctypes.c_uint32),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_uint32),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 3)]


_WIN32: Win32Window | None = None


def win32() -> Win32Window:
    """惰性创建 Win32 包装（非 Windows 直接报错）。"""
    global _WIN32
    if sys.platform != "win32":
        raise RuntimeError("窗口截图仅支持 Windows")
    if _WIN32 is None:
        _WIN32 = Win32Window()
    return _WIN32


class Win32Window:
    """最小 Win32 封装：枚举窗口、PrintWindow 截图、DPI、缩放/最小化/关闭。"""

    def __init__(self) -> None:
        windll = ctypes.WinDLL  # type: ignore[attr-defined]  # typeshed 仅在 Windows 平台声明 WinDLL
        self.user32 = windll("user32", use_last_error=True)
        self.gdi32 = windll("gdi32", use_last_error=True)
        self.kernel32 = windll("kernel32", use_last_error=True)
        self._configure_signatures()
        self.console_hwnd = int(self.kernel32.GetConsoleWindow() or 0)
        self._enable_dpi_awareness()

    # ---- 初始化 ---------------------------------------------------------
    def _configure_signatures(self) -> None:
        u, g, k = self.user32, self.gdi32, self.kernel32
        callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)

        u.EnumWindows.argtypes = [callback_type(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p), ctypes.c_void_p]
        u.EnumWindows.restype = ctypes.c_bool
        u.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
        u.GetWindowTextLengthW.restype = ctypes.c_int
        u.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        u.GetWindowTextW.restype = ctypes.c_int
        u.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        u.GetWindowThreadProcessId.restype = ctypes.c_uint32
        u.IsWindowVisible.argtypes = [ctypes.c_void_p]
        u.IsWindowVisible.restype = ctypes.c_bool
        u.IsIconic.argtypes = [ctypes.c_void_p]
        u.IsIconic.restype = ctypes.c_bool
        u.IsWindow.argtypes = [ctypes.c_void_p]
        u.IsWindow.restype = ctypes.c_bool
        u.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(_RECT)]
        u.GetWindowRect.restype = ctypes.c_bool
        u.GetWindowDC.argtypes = [ctypes.c_void_p]
        u.GetWindowDC.restype = ctypes.c_void_p
        u.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        u.ReleaseDC.restype = ctypes.c_int
        u.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
        u.PrintWindow.restype = ctypes.c_bool
        u.SetWindowPos.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint32,
        ]
        u.SetWindowPos.restype = ctypes.c_bool
        u.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        u.ShowWindow.restype = ctypes.c_bool
        u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        u.SetForegroundWindow.restype = ctypes.c_bool
        u.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p]
        u.PostMessageW.restype = ctypes.c_bool
        u.GetSystemMetrics.argtypes = [ctypes.c_int]
        u.GetSystemMetrics.restype = ctypes.c_int
        g.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        g.CreateCompatibleDC.restype = ctypes.c_void_p
        g.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        g.CreateCompatibleBitmap.restype = ctypes.c_void_p
        g.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        g.SelectObject.restype = ctypes.c_void_p
        g.DeleteObject.argtypes = [ctypes.c_void_p]
        g.DeleteObject.restype = ctypes.c_bool
        g.DeleteDC.argtypes = [ctypes.c_void_p]
        g.DeleteDC.restype = ctypes.c_bool
        g.GetDIBits.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(_BITMAPINFO),
            ctypes.c_uint32,
        ]
        g.GetDIBits.restype = ctypes.c_int
        k.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        k.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PROCESSENTRY32W)]
        k.Process32FirstW.restype = ctypes.c_bool
        k.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PROCESSENTRY32W)]
        k.Process32NextW.restype = ctypes.c_bool
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        k.CloseHandle.restype = ctypes.c_bool
        k.GetConsoleWindow.restype = ctypes.c_void_p
        for name in ("GetDpiForWindow", "GetDpiForSystem"):
            function = getattr(u, name, None)
            if function is None:
                continue
            if name == "GetDpiForWindow":
                function.argtypes = [ctypes.c_void_p]
            function.restype = ctypes.c_uint32

    def _enable_dpi_awareness(self) -> None:
        """让脚本按物理像素取窗口矩形，避免高 DPI 下截图被系统缩放。"""
        try:
            context = ctypes.c_void_p(-4)  # PER_MONITOR_AWARE_V2 = (HANDLE)-4
            self.user32.SetProcessDpiAwarenessContext(context)
        except (AttributeError, OSError):
            try:
                self.user32.SetProcessDPIAware()
            except (AttributeError, OSError):
                pass

    # ---- 查询 -----------------------------------------------------------
    def window_rect(self, hwnd: int) -> _RECT:
        rect = _RECT()
        if not self.user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect)):
            raise RuntimeError(f"GetWindowRect 失败：hwnd={hwnd}")
        return rect

    def dpi_for_window(self, hwnd: int) -> int | None:
        try:
            value = int(self.user32.GetDpiForWindow(ctypes.c_void_p(hwnd)))
        except AttributeError:
            return None
        return value or None

    def system_dpi(self) -> int | None:
        try:
            return int(self.user32.GetDpiForSystem()) or None
        except AttributeError:
            return None

    def screen_size(self) -> tuple[int, int] | None:
        """主屏分辨率（物理像素）；用于把宽屏截图裁到可见范围内。"""
        try:
            width = int(self.user32.GetSystemMetrics(0))
            height = int(self.user32.GetSystemMetrics(1))
        except AttributeError:
            return None
        return (width, height) if width > 0 and height > 0 else None

    def is_visible(self, hwnd: int) -> bool:
        return bool(self.user32.IsWindowVisible(ctypes.c_void_p(hwnd)))

    def window_title(self, hwnd: int) -> str:
        length = self.user32.GetWindowTextLengthW(ctypes.c_void_p(hwnd))
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        self.user32.GetWindowTextW(ctypes.c_void_p(hwnd), buffer, length + 1)
        return buffer.value

    def window_pid(self, hwnd: int) -> int:
        pid = ctypes.c_uint32()
        self.user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
        return int(pid.value)

    def top_level_windows(self) -> list[tuple[int, int, str]]:
        results: list[tuple[int, int, str]] = []

        def _callback(hwnd: int, _lparam: int) -> bool:
            handle = int(hwnd)
            results.append((handle, self.window_pid(handle), self.window_title(handle)))
            return True

        callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
        self.user32.EnumWindows(callback_type(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(_callback), 0)
        return results

    def descendant_pids(self, root_pid: int) -> set[int]:
        """用 Toolhelp 快照构建进程树（不依赖 psutil）。"""
        snapshot = self.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snapshot or int(snapshot) == int(INVALID_HANDLE_VALUE or 0):
            return {root_pid}
        parents: dict[int, int] = {}
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        try:
            ok = self.kernel32.Process32FirstW(ctypes.c_void_p(snapshot), ctypes.byref(entry))
            while ok:
                parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                ok = self.kernel32.Process32NextW(ctypes.c_void_p(snapshot), ctypes.byref(entry))
        finally:
            self.kernel32.CloseHandle(ctypes.c_void_p(snapshot))

        tree = {root_pid}
        changed = True
        while changed:
            changed = False
            for pid, parent in parents.items():
                if parent in tree and pid not in tree:
                    tree.add(pid)
                    changed = True
        return tree

    def wait_for_window(self, root_pid: int, timeout: float) -> tuple[int, str]:
        """在进程树内挑选面积最大的可见有标题窗口（排除脚本自身控制台）。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pids = self.descendant_pids(root_pid)
            candidates: list[tuple[int, int, str]] = []
            for hwnd, pid, title in self.top_level_windows():
                if pid not in pids or not title or not self.is_visible(hwnd) or hwnd == self.console_hwnd:
                    continue
                rect = self.window_rect(hwnd)
                area = (rect.right - rect.left) * (rect.bottom - rect.top)
                if area > 0:
                    candidates.append((area, hwnd, title))
            if candidates:
                candidates.sort(reverse=True)
                return candidates[0][1], candidates[0][2]
            time.sleep(0.5)
        raise TimeoutError(f"{timeout:.0f}s 内未找到应用窗口（root pid={root_pid}）")

    # ---- 动作 -----------------------------------------------------------
    def resize(self, hwnd: int, width: int, height: int) -> None:
        rect = self.window_rect(hwnd)
        self.user32.SetWindowPos(
            ctypes.c_void_p(hwnd),
            None,
            rect.left,
            rect.top,
            width,
            height,
            SWP_NOZORDER | SWP_NOACTIVATE,
        )

    def minimize(self, hwnd: int) -> None:
        self.user32.ShowWindow(ctypes.c_void_p(hwnd), SW_MINIMIZE)

    def restore(self, hwnd: int) -> None:
        self.user32.ShowWindow(ctypes.c_void_p(hwnd), SW_RESTORE)

    def bring_to_front(self, hwnd: int) -> None:
        self.user32.SetForegroundWindow(ctypes.c_void_p(hwnd))

    def request_close(self, hwnd: int) -> None:
        self.user32.PostMessageW(ctypes.c_void_p(hwnd), WM_CLOSE, None, None)

    def is_alive(self, hwnd: int) -> bool:
        return bool(self.user32.IsWindow(ctypes.c_void_p(hwnd)))

    # ---- 截图 -----------------------------------------------------------
    def capture(self, hwnd: int) -> tuple[Image.Image, str]:
        """优先 PrintWindow（可截被遮挡窗口），内容区空白时回退到屏幕抓取。

        部分 GPU 合成窗口（含部分 Flutter/Tk 窗口）的 PrintWindow 只返回窗口边框、
        内容区全黑；只看整窗标准差会被标题栏掩盖，因此判空只看内容区（去掉标题栏与边框）。
        """
        rect = self.window_rect(hwnd)
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width <= 0 or height <= 0:
            raise RuntimeError(f"窗口尺寸无效：{width}x{height}")
        printed = self._print_window(hwnd, width, height)
        if not _is_unreliable(printed):
            return printed, "print_window"
        self.bring_to_front(hwnd)
        time.sleep(0.5)
        captured = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True)
        if not _is_unreliable(captured):
            return captured, "screen"
        return printed, "blank"

    def _print_window(self, hwnd: int, width: int, height: int) -> Image.Image:
        u, g = self.user32, self.gdi32
        hwnd_dc = u.GetWindowDC(ctypes.c_void_p(hwnd))
        mem_dc = g.CreateCompatibleDC(ctypes.c_void_p(hwnd_dc))
        bitmap = g.CreateCompatibleBitmap(ctypes.c_void_p(hwnd_dc), width, height)
        if not hwnd_dc or not mem_dc or not bitmap:
            raise RuntimeError("创建兼容 DC/位图失败")
        previous = g.SelectObject(ctypes.c_void_p(mem_dc), ctypes.c_void_p(bitmap))
        try:
            u.PrintWindow(ctypes.c_void_p(hwnd), ctypes.c_void_p(mem_dc), PW_RENDERFULLCONTENT)
            buffer = (ctypes.c_ubyte * (width * height * 4))()
            info = _BITMAPINFO()
            info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            info.bmiHeader.biWidth = width
            info.bmiHeader.biHeight = -height  # 负数 = 自上而下
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            info.bmiHeader.biCompression = 0  # BI_RGB
            copied = g.GetDIBits(
                ctypes.c_void_p(mem_dc),
                ctypes.c_void_p(bitmap),
                0,
                height,
                ctypes.cast(buffer, ctypes.c_void_p),
                ctypes.byref(info),
                0,
            )
            if copied != height:
                raise RuntimeError(f"GetDIBits 行数不符：{copied} != {height}")
            return Image.frombuffer("RGB", (width, height), bytes(buffer), "raw", "BGRX", 0, 1)
        finally:
            g.SelectObject(ctypes.c_void_p(mem_dc), ctypes.c_void_p(previous))
            g.DeleteObject(ctypes.c_void_p(bitmap))
            g.DeleteDC(ctypes.c_void_p(mem_dc))
            u.ReleaseDC(ctypes.c_void_p(hwnd), ctypes.c_void_p(hwnd_dc))


def _content_std(image: Image.Image) -> float:
    """内容区（去掉标题栏与边框）灰度标准差；整窗含边框会把全黑内容掩盖成「非空」。"""
    import numpy as np

    width, height = image.size
    inset_x = max(4, int(width * 0.02))
    inset_top = max(4, int(height * 0.05))
    inset_bottom = max(4, int(height * 0.02))
    content = image.convert("L").crop((inset_x, inset_top, width - inset_x, height - inset_bottom))
    return float(np.asarray(content, dtype=np.float32).std())


def _black_ratio(image: Image.Image) -> float:
    """纯黑像素占比。项目四主题的 surface 均非 #000000，黑块即 GPU 窗口未绘制的信号。"""
    import numpy as np

    sample = np.asarray(image.convert("RGB").resize((64, 64)), dtype=np.uint8)
    return float((sample.max(axis=2) <= 8).mean())


def _is_unreliable(image: Image.Image) -> bool:
    """内容区接近单色或含明显黑块时，PrintWindow 结果不采信。"""
    return _content_std(image) < BLANK_STD_THRESHOLD or _black_ratio(image) > BLACK_RATIO_THRESHOLD


# ============================================================================
# 环境与客户端元数据
# ============================================================================
def _powershell_json(script: str) -> Any:
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None


def collect_environment() -> dict[str, Any]:
    """OS/CPU/GPU/驱动/locale；取不到的字段 null + reason（R21）。"""
    wmi = _powershell_json(
        "$os = Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,BuildNumber,OSArchitecture;"
        "$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name;"
        "$gpu = Get-CimInstance Win32_VideoController | Select-Object -First 1 Name,DriverVersion;"
        "[pscustomobject]@{os=$os;cpu=$cpu;gpu=$gpu} | ConvertTo-Json -Compress -Depth 4"
    )
    gpu = (wmi or {}).get("gpu") if isinstance(wmi, dict) else None
    if isinstance(gpu, list):
        gpu = gpu[0] if gpu else None
    os_info = (wmi or {}).get("os") if isinstance(wmi, dict) else None

    def _text(value: Any) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    user_locale = _user_locale()
    return {
        "platform": platform.platform(),
        "os_caption": _text(os_info.get("Caption") if isinstance(os_info, dict) else None),
        "os_version": _text(os_info.get("Version") if isinstance(os_info, dict) else None),
        "os_build": _text(os_info.get("BuildNumber") if isinstance(os_info, dict) else None),
        "os_arch": _text(os_info.get("OSArchitecture") if isinstance(os_info, dict) else None),
        "cpu": _text((wmi or {}).get("cpu") if isinstance(wmi, dict) else None),
        "gpu": _text(gpu.get("Name") if isinstance(gpu, dict) else None),
        "gpu_driver": _text(gpu.get("DriverVersion") if isinstance(gpu, dict) else None),
        "wmi_reason": None if isinstance(wmi, dict) else "PowerShell CIM 查询失败或无输出",
        "locale": user_locale,
        "locale_reason": None if user_locale else "GetUserDefaultLocaleName 不可用",
    }


def _user_locale() -> str | None:
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]  # 仅 Windows
        buffer = ctypes.create_unicode_buffer(85)
        if kernel32.GetUserDefaultLocaleName(buffer, 85):
            return buffer.value
    except (AttributeError, OSError):
        return None
    return None


def collect_client_info() -> dict[str, Any]:
    """记录 Flet 客户端可执行文件与其 SHA-256；缺失时 null + reason。"""
    client_root = Path.home() / ".flet" / "client"
    candidates = sorted(client_root.rglob("flet*.exe")) if client_root.exists() else []
    if not candidates:
        return {
            "path": None,
            "sha256": None,
            "reason": f"未在 {client_root} 找到 Flet 客户端可执行文件",
        }
    client = candidates[-1]
    digest = sha256_file(client)
    if digest is None:
        return {"path": str(client), "sha256": None, "reason": "客户端文件不可读"}
    return {"path": str(client), "sha256": digest, "reason": None}


def flet_package_version() -> str | None:
    try:
        return importlib.metadata.version("flet")
    except importlib.metadata.PackageNotFoundError:
        return None


def run_contrast_check() -> dict[str, Any]:
    """复用 scripts/check_theme_contrast.py 的静态对比度检查（§185 要求）。"""
    script = REPO_ROOT / "scripts" / "check_theme_contrast.py"
    if not script.exists():
        return {"ran": False, "returncode": None, "reason": f"缺少 {script}"}
    try:
        completed = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
            cwd=str(REPO_ROOT),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ran": False, "returncode": None, "reason": f"执行失败：{exc}"}
    tail = (completed.stdout or "")[-2000:]
    return {"ran": True, "returncode": completed.returncode, "output_tail": tail, "reason": None}


# ============================================================================
# 采集编排
# ============================================================================
def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _write_manifest(output: Path, manifest: dict[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_config(config_path: Path, theme: str) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(build_launch_config(theme), ensure_ascii=False, indent=2), encoding="utf-8")


def _save_capture(
    image: Image.Image,
    path: Path,
    backend: str,
    theme: str,
    scene: str,
    method: str,
    dpi: int | None,
) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    check: dict[str, Any] | None
    if scene in {"default", "restored"}:
        check = verify_theme_color(image, theme)
    else:
        check = {"skipped": True, "reason": "仅 default/restored 场景核验主题主色（其余场景可能被弹窗/内容页占据）"}
    return {
        "backend": backend,
        "theme": theme,
        "scene": scene,
        "file": path.as_posix(),
        "width": image.width,
        "height": image.height,
        "dpi": dpi,
        "dpi_reason": None if dpi else "GetDpiForWindow 不可用",
        "capture_method": method,
        "content_std": round(_content_std(image), 2),
        "black_ratio": round(_black_ratio(image), 4),
        "sha256": sha256_file(path),
        "theme_check": check,
    }


def _terminate_tree(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        check=False,
        timeout=30,
    )


def run_one(
    output: Path,
    backend: str,
    theme: str,
    exe: Path | None,
    interactive: bool,
    scenes_override: tuple[str, ...] | None,
    extra_env: dict[str, str],
    launch_timeout: float,
) -> dict[str, Any]:
    """运行一个 (backend, theme) 组合，返回该组合的 run 记录与截图条目。"""
    api = win32()
    run_dir = output / backend / theme
    config_path = run_dir / "user_settings.json"
    _write_config(config_path, theme)

    if exe is not None:
        command = [str(exe)]
    else:
        command = [sys.executable, str(REPO_ROOT / "main.py")]
    env = build_launch_env(theme, backend, config_path, extra_env)

    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "launch.log"
    captures: list[dict[str, Any]] = []
    run: dict[str, Any] = {
        "backend": backend,
        "theme": theme,
        "command": command,
        "config": build_launch_config(theme),
        "started_at": _utc_now(),
        "window_title": None,
        "window_size": None,
        "theme_check": None,
        "skipped_scenes": [],
        "interactive": interactive,
        "error": None,
    }

    # 宽屏截图必须落在可见屏幕内，否则窗口出屏部分会截成黑块并被判为无效证据。
    screen = api.screen_size()
    run["screen_size"] = list(screen) if screen else None
    run["screen_size_reason"] = None if screen else "GetSystemMetrics 不可用"
    window_targets: dict[str, tuple[int, int]] = {}
    for scene, (width, height) in WINDOW_SIZES.items():
        if screen:
            width = min(width, screen[0] - 60)
            height = min(height, screen[1] - 120)
        window_targets[scene] = (width, height)
    run["window_targets"] = {scene: list(size) for scene, size in window_targets.items()}

    with log_path.open("wb") as log_handle:
        process = subprocess.Popen(
            command,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        try:
            hwnd, title = api.wait_for_window(process.pid, launch_timeout)
            run["window_title"] = title
            dpi = api.dpi_for_window(hwnd)
            rect = api.window_rect(hwnd)
            original = (rect.right - rect.left, rect.bottom - rect.top)
            run["window_size"] = {"width": original[0], "height": original[1]}

            image, method = api.capture(hwnd)
            captures.append(_save_capture(image, run_dir / "default.png", backend, theme, "default", method, dpi))
            run["theme_check"] = captures[0]["theme_check"]

            for scene, (width, height) in window_targets.items():
                api.resize(hwnd, width, height)
                time.sleep(SETTLE_SECONDS)
                image, method = api.capture(hwnd)
                captures.append(_save_capture(image, run_dir / f"{scene}.png", backend, theme, scene, method, dpi))

            api.resize(hwnd, original[0], original[1])
            time.sleep(SETTLE_SECONDS)
            api.minimize(hwnd)
            time.sleep(MINIMIZE_SETTLE_SECONDS)
            api.restore(hwnd)
            time.sleep(RESTORE_SETTLE_SECONDS)
            image, method = api.capture(hwnd)
            captures.append(_save_capture(image, run_dir / "restored.png", backend, theme, "restored", method, dpi))

            # 关键页导航必须在发送关闭消息之前完成：WM_CLOSE 会触发关闭确认弹窗并阻塞交互。
            if interactive:
                planned = scene_plan(backend, scenes_override)
                for scene in planned:
                    if scene in AUTO_SCENES:
                        continue
                    label = SCENE_LABELS.get(scene, scene)
                    print(f"[{backend}/{theme}] 请导航到「{label}」")
                    answer = input("  按 Enter 截图，输入 s 跳过：").strip().lower()
                    if answer == "s":
                        run["skipped_scenes"].append({"scene": scene, "reason": "操作者跳过"})
                        continue
                    image, method = api.capture(hwnd)
                    captures.append(_save_capture(image, run_dir / f"{scene}.png", backend, theme, scene, method, dpi))

            api.bring_to_front(hwnd)
            api.request_close(hwnd)
            time.sleep(CLOSE_SETTLE_SECONDS)
            if api.is_alive(hwnd):
                image, method = api.capture(hwnd)
                captures.append(
                    _save_capture(image, run_dir / "close_dialog.png", backend, theme, "close_dialog", method, dpi)
                )
            else:
                run["skipped_scenes"].append(
                    {"scene": "close_dialog", "reason": "窗口在 WM_CLOSE 后直接关闭，未出现确认弹窗"}
                )
        except (TimeoutError, RuntimeError, OSError) as exc:
            run["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            run["blank_scenes"] = [item["scene"] for item in captures if item["capture_method"] == "blank"]
            if process.poll() is None:
                _terminate_tree(process.pid)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                run["error"] = (run["error"] or "") + " 进程未在 30s 内退出"

    run["finished_at"] = _utc_now()
    run["log"] = log_path.as_posix()
    return {"run": run, "captures": captures}


def build_manifest(args: argparse.Namespace, backends: tuple[str, ...], themes: tuple[str, ...]) -> dict[str, Any]:
    exe = Path(args.exe).resolve() if args.exe else None
    baseline = Path(args.compare).resolve() if args.compare else None
    app_info: dict[str, Any] = {
        "mode": "exe" if exe else "source",
        "path": str(exe) if exe else str(REPO_ROOT / "main.py"),
        "sha256": sha256_file(exe) if exe else sha256_file(REPO_ROOT / "main.py"),
        "flet_package_version": flet_package_version(),
    }
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "task": "G4-202",
        "source": "reviews/flet upgrade 1.1.0.md §202 / §5.11",
        "generated_at": _utc_now(),
        "tool": "scripts/g4_theme_screenshots.py",
        "requested": {"backends": list(backends), "themes": list(themes), "interactive": args.interactive},
        "baseline": {
            "path": str(baseline) if baseline else None,
            "present": bool(baseline and baseline.is_dir()),
            "reason": None if baseline and baseline.is_dir() else "未提供 G0 四主题截图基线（仓库内无此基线）",
        },
        "environment": collect_environment(),
        "client": collect_client_info(),
        "app": app_info,
        "contrast_check": None,
        "runs": [],
        "captures": [],
        "compare": None,
        "verdict": None,
    }


def finalize_verdict(manifest: dict[str, Any]) -> None:
    mismatches = [
        {"backend": run["backend"], "theme": run["theme"], "detail": run["theme_check"]}
        for run in manifest["runs"]
        if (run.get("theme_check") or {}).get("match") is False
    ]
    errors = [run for run in manifest["runs"] if run.get("error")]
    blanks = [
        {"backend": run["backend"], "theme": run["theme"], "scenes": run.get("blank_scenes", [])}
        for run in manifest["runs"]
        if run.get("blank_scenes")
    ]
    # match 为 None 表示无法核验（不是「通过」）；R21：不得把未知当已知。
    unverified = [
        {"backend": run["backend"], "theme": run["theme"], "detail": run["theme_check"]}
        for run in manifest["runs"]
        if not run.get("error") and (run.get("theme_check") or {}).get("match") is None
    ]
    notes = [
        "本脚本只产出证据；G4-202 需按方案 §202 逐项人工判读截图，脚本不判定通过。",
    ]
    if not manifest["baseline"]["present"]:
        notes.append("G0 四主题截图基线缺失：本次仅为候选证据，不得以「无差异」宣称与 G0 对照通过。")
    if mismatches:
        notes.append("存在主题未生效：截图主色与请求主题不符，对应主题证据无效。")
    if unverified:
        notes.append("存在无法核验主题的截图（截图主色与四主题底色均不接近，或 surface 不可读）。")
    if blanks:
        notes.append("存在空白截图：PrintWindow 与屏幕抓取的内容区都接近单色，该场景证据无效。")
    if errors:
        notes.append("存在采集失败：见 runs[].error 与 launch.log。")
    manifest["verdict"] = {
        "g4_202": "pending_manual_review",
        "manual_review_required": True,
        "theme_mismatches": mismatches,
        "unverified_runs": unverified,
        "blank_runs": blanks,
        "failed_runs": [{"backend": run["backend"], "theme": run["theme"], "error": run["error"]} for run in errors],
        "notes": notes,
    }


def parse_env_pairs(values: list[str]) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for item in values:
        if "=" not in item:
            print(f"2: --env 需为 KEY=VALUE：{item}", file=sys.stderr)
            raise SystemExit(2)
        key, value = item.split("=", 1)
        pairs[key.strip()] = value
    return pairs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="G4-202 四主题截图判读（Impeller 默认后端 + FLET_NO_IMPELLER Skia 回退）",
    )
    parser.add_argument("--exe", help="已安装应用可执行文件；省略则用当前解释器运行 main.py")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help=f"证据目录（默认 {DEFAULT_OUTPUT}）")
    parser.add_argument("--backends", default="impeller,skia", help="逗号分隔：impeller,skia")
    parser.add_argument("--themes", default="dark,light,navy,dracula", help="逗号分隔：dark,light,navy,dracula")
    parser.add_argument("--scenes", default=None, help="覆盖关键页场景列表（逗号分隔）")
    parser.add_argument(
        "--interactive",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="是否进入关键页人工导航截图；默认按 stdin 是否为终端判定",
    )
    parser.add_argument("--env", action="append", default=[], help="透传给应用的额外环境变量 KEY=VALUE，可重复")
    parser.add_argument("--launch-timeout", type=float, default=180.0, help="等待应用窗口出现的秒数")
    parser.add_argument("--compare", default=None, help="基线目录（G0 或上一轮候选），用于像素差分")
    parser.add_argument("--compare-only", action="store_true", help="只做差分，不启动应用")
    parser.add_argument("--compare-threshold", type=int, default=12, help="单像素通道差异阈值（0-255）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if sys.platform != "win32":
        print("2: 本脚本依赖 Win32 窗口 API，仅支持 Windows", file=sys.stderr)
        return 2

    output = Path(args.output).resolve()
    if args.compare_only:
        if not args.compare:
            print("2: --compare-only 需要同时提供 --compare", file=sys.stderr)
            return 2
        baseline = Path(args.compare).resolve()
        if not baseline.is_dir():
            print(f"2: 基线目录不存在：{baseline}", file=sys.stderr)
            return 2
        if not output.is_dir():
            print(f"2: 候选目录不存在：{output}", file=sys.stderr)
            return 2
        result = compare_trees(baseline, output, args.compare_threshold)
        output.mkdir(parents=True, exist_ok=True)
        write_compare_report(result, output)
        print(json.dumps(result["summary"], ensure_ascii=False))
        return 0

    backends = parse_choices(args.backends, BACKENDS, "backends")
    themes = parse_choices(args.themes, THEMES, "themes")
    scenes_override = tuple(item.strip() for item in args.scenes.split(",") if item.strip()) if args.scenes else None
    interactive = args.interactive if args.interactive is not None else sys.stdin.isatty()

    manifest = build_manifest(args, backends, themes)
    manifest["requested"]["interactive"] = interactive
    manifest["contrast_check"] = run_contrast_check()
    _write_manifest(output, manifest)

    extra_env = parse_env_pairs(args.env)
    for backend in backends:
        for theme in themes:
            print(f"[{backend}/{theme}] 启动应用并采集截图…")
            result = run_one(
                output,
                backend,
                theme,
                Path(args.exe).resolve() if args.exe else None,
                interactive,
                scenes_override,
                extra_env,
                args.launch_timeout,
            )
            manifest["runs"].append(result["run"])
            manifest["captures"].extend(result["captures"])
            _write_manifest(output, manifest)

    if args.compare:
        baseline = Path(args.compare).resolve()
        if baseline.is_dir():
            comparison = compare_trees(baseline, output, args.compare_threshold)
            write_compare_report(comparison, output)
            manifest["compare"] = comparison["summary"]
        else:
            manifest["compare"] = {"error": f"基线目录不存在：{baseline}"}

    finalize_verdict(manifest)
    _write_manifest(output, manifest)

    mismatches = manifest["verdict"]["theme_mismatches"]
    failed = manifest["verdict"]["failed_runs"]
    unverified = manifest["verdict"]["unverified_runs"]
    blanks = manifest["verdict"]["blank_runs"]
    print(f"证据目录：{output}")
    print(
        f"截图 {len(manifest['captures'])} 张；失败运行 {len(failed)} 个；"
        f"主题未生效 {len(mismatches)} 个；主题无法核验 {len(unverified)} 个；空白截图 {len(blanks)} 个"
    )
    if not manifest["baseline"]["present"]:
        print("注意：G0 四主题截图基线缺失，本次只产出候选证据，不能据此宣称 G4-202 通过。")
    return 1 if mismatches or failed or unverified or blanks else 0


if __name__ == "__main__":
    raise SystemExit(main())
