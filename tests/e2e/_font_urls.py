"""E2E 字体 URL 解析与缓存完整性校验。

被 ``tests/e2e/conftest.py`` 启动期断言与 ``scripts/sync_e2e_fonts.py`` 同步脚本共同复用。

设计背景：
    Flet web app 启动时 CanvasKit 按需从 ``fonts.gstatic.com`` 加载字体分片。
    E2E 测试通过 ``conftest.py`` 的 route handler 拦截这些请求并从本地
    ``mock_assets/fonts/`` 提供。若本地缓存不完整，route handler abort 请求 →
    CJK 文本节点不生成 → Playwright 等待中文文本超时 → 整批 E2E 失败。

    ``flet_web/web/main.dart.js`` 中硬编码了所有字体注册（``A.m("FontName", "relpath")``），
    其中 ``notosanssc`` 有 ~100 个分片（按 flet 0.86.2 统计，跨版本可能变化）。本模块解析
    main.dart.js 提取应用实际需要的字体分片 URL，并校验本地缓存是否完整。

需缓存的字体族（应用 locale=zh，按需加载）：
    - ``notosanssc``：简体中文（核心，~100 个分片）
    - ``roboto``：默认 UI 字体（1 个分片）
    - ``notosans``：通用拉丁字符（1 个分片）

不缓存（应用不渲染对应字符，CanvasKit 不会请求）：
    - ``notosanshk`` / ``notosanstc`` / ``notosansjp`` / ``notosanskr``：其他 CJK
    - 其他 Noto Sans 变体：少数民族语言
"""

from __future__ import annotations

import importlib.machinery
import re
import site
from pathlib import Path, PurePosixPath

# 应用实际需要的字体族（locale=zh + 默认 UI 字体）
# 升级 flet 时若应用新增其他 locale（如日文/韩文），需在此处补充对应字体族
REQUIRED_FONT_FAMILIES: frozenset[str] = frozenset(
    {
        "notosanssc",  # 简体中文（应用 locale=zh）
        "roboto",  # 默认 UI 字体
        "notosans",  # 通用拉丁字符
    }
)

# main.dart.js 中字体注册模式：A.m("Font Name", "fontfamily/vN/<hash>.<id>.woff2")
# minified JS 中可能用双引号或单引号，正则需兼容
# [^"'\n\r] 限制单行匹配，防止非 minified JS 跨行误匹配
_FONT_PATH_PATTERN = re.compile(r'["\']([a-z0-9_]+/v\d+/[^"\'\n\r]+\.woff2)["\']')

# 字体基础 URL（main.dart.js 中是相对路径，运行时拼接此基础 URL）
_FONT_BASE_URL = "https://fonts.gstatic.com/s/"

# flet 版本查询命令，用于错误信息指引（避免硬编码版本号占位符）
_FLET_VERSION_QUERY_CMD = 'python -c "import flet; print(flet.__version__)"'


def extract_font_filename(path: str) -> str | None:
    """从字体相对路径或 URL path 中提取本地文件名。

    被 ``tests/e2e/conftest.py`` route handler 与 ``scripts/sync_e2e_fonts.py`` 共同复用，
    保证两处 path traversal 防御行为一致。

    安全性：用 ``PurePosixPath`` 仅按 ``/`` 分割（跨平台一致），
    防御 Windows 路径分隔符 ``\\`` 注入导致的 path traversal。
    返回 None 表示路径异常，调用方应跳过下载或 abort 请求。

    Args:
        path: 字体相对路径（如 ``"notosanssc/v37/hash.4.woff2"``）或
            URL path（如 ``"/s/notosanssc/v37/hash.4.woff2"``）。
            传 URL path 时应先经 ``urlparse(url).path`` 去除 query/fragment。
    """
    filename = PurePosixPath(path).name
    if not filename or "\\" in filename or filename in (".", ".."):
        return None
    return filename


def find_flet_web_main_dart_js() -> Path:
    """定位已安装 flet_web 包的 web/main.dart.js 文件。

    flet_web 是 flet 主包在 web 模式下按需安装的依赖（见
    ``flet/utils/pip.py::ensure_flet_web_package_installed``），可能位于
    venv site-packages 或 user site-packages。

    搜索顺序：
        1. 直接 ``import flet_web``（覆盖 venv site-packages 场景）
        2. ``importlib.util.find_spec("flet_web", [user_site])`` 显式搜索
           user site-packages（不修改 sys.path，避免测试状态污染）
        3. 在 ``site.getusersitepackages()`` 下直接查找目录（兜底）

    Returns:
        main.dart.js 的绝对路径

    Raises:
        RuntimeError: flet_web 未安装或 main.dart.js 不存在
    """
    flet_web_dir: Path | None = None

    # 1. 直接 import（venv site-packages 场景）
    try:
        import flet_web  # type: ignore[import-not-found]

        flet_web_dir = Path(flet_web.__file__).resolve().parent
    except ImportError:
        pass

    # 2. importlib 显式搜索 user site-packages（不污染 sys.path）
    if flet_web_dir is None:
        user_site = site.getusersitepackages()
        # PathFinder.find_spec 是 classmethod，第一个参数是包名，第二个是搜索路径
        # 显式指定 path 不修改全局 sys.path，避免测试状态污染（R7）
        spec = importlib.machinery.PathFinder.find_spec("flet_web", [user_site])
        if spec is not None and spec.origin:
            flet_web_dir = Path(spec.origin).resolve().parent

    # 3. 兜底：直接在 user site-packages 下查找目录
    if flet_web_dir is None:
        user_site = Path(site.getusersitepackages())
        candidate = user_site / "flet_web"
        if candidate.is_dir():
            flet_web_dir = candidate

    if flet_web_dir is None:
        raise RuntimeError(
            "flet_web 包未安装。E2E 启动期断言需要解析其 main.dart.js 中的字体 URL。"
            "请在 E2E 运行的同一 venv 中执行 "
            f"`pip install flet-web==<version>`（<version> 与 pyproject.toml 锁定的 flet 版本一致，"
            f"可通过 {_FLET_VERSION_QUERY_CMD} 查询），然后重试 E2E。"
        )

    main_path = flet_web_dir / "web" / "main.dart.js"
    if not main_path.exists():
        # R9: 不在错误信息中泄露绝对路径（含用户名/工作区路径）
        raise RuntimeError(
            "main.dart.js 未找到于 flet_web/web/ 目录下。"
            "flet_web 包可能损坏，请在 E2E 运行的同一 venv 中执行 "
            "`pip install --force-reinstall flet-web==<version>`（<version> 同上）。"
        )
    return main_path


def extract_required_font_paths(main_dart_js_path: Path) -> set[str]:
    """从 main.dart.js 解析应用需要的字体分片相对路径集合。

    相对路径格式：``notosanssc/v37/<hash>.<id>.woff2``（不含基础 URL）。
    文件名（含 hash 和 id）唯一标识一个分片，是 route handler 匹配本地缓存的 key。

    Args:
        main_dart_js_path: main.dart.js 文件路径

    Returns:
        相对路径集合，如 ``{"notosanssc/v37/hash.4.woff2", "roboto/v32/hash.woff2", ...}``

    Raises:
        RuntimeError: main.dart.js 中未找到任何 REQUIRED_FONT_FAMILIES 字体注册
    """
    content = main_dart_js_path.read_text(encoding="utf-8")
    all_paths: set[str] = set()
    for match in _FONT_PATH_PATTERN.finditer(content):
        path = match.group(1)
        family = path.split("/", 1)[0]
        if family in REQUIRED_FONT_FAMILIES:
            all_paths.add(path)

    if not all_paths:
        # R9: 不在错误信息中泄露绝对路径；不引用 minified JS 函数名（实现细节，跨版本可能变）
        raise RuntimeError(
            f"main.dart.js 中未找到任何需缓存字体族 {sorted(REQUIRED_FONT_FAMILIES)} 的注册。"
            "可能原因：① flet 版本变化导致字体注册模式改变（需更新 _FONT_PATH_PATTERN）；"
            "② main.dart.js 损坏。请检查 main.dart.js 中的字体注册字符串模式"
            '（形如 "fontfamily/vN/<hash>.<id>.woff2"）。'
        )
    return all_paths


def get_cached_font_filenames(fonts_dir: Path) -> set[str]:
    """获取本地字体缓存目录中所有 .woff2 文件名集合。

    Args:
        fonts_dir: mock_assets/fonts/ 目录路径

    Returns:
        文件名集合（不含路径），如 ``{"hash.4.woff2", "hash.5.woff2", ...}``
    """
    if not fonts_dir.exists():
        return set()
    return {p.name for p in fonts_dir.glob("*.woff2")}


def find_missing_fonts(fonts_dir: Path, required_paths: set[str] | None = None) -> list[str]:
    """校验本地字体缓存是否覆盖 main.dart.js 中所有需缓存的字体分片。

    Args:
        fonts_dir: mock_assets/fonts/ 目录路径
        required_paths: 预计算的字体相对路径集合（如已由调用方通过
            ``extract_required_font_paths`` 获取）。传入时跳过重复发现，
            保证校验基线与下载基线一致；为 None 时内部发现。

    Returns:
        缺失的字体文件名列表（按文件名排序）；空列表表示缓存完整
    """
    if required_paths is None:
        main_dart_js = find_flet_web_main_dart_js()
        required_paths = extract_required_font_paths(main_dart_js)
    # 与 sync_e2e_fonts.py / conftest.py route handler 保持一致的 path traversal 防御
    required_filenames: set[str] = set()
    for p in required_paths:
        filename = extract_font_filename(p)
        if filename is not None:
            required_filenames.add(filename)
    cached_filenames = get_cached_font_filenames(fonts_dir)
    missing = required_filenames - cached_filenames
    return sorted(missing)


def build_font_download_url(rel_path: str) -> str:
    """将 main.dart.js 中的字体相对路径转换为完整下载 URL。

    Args:
        rel_path: 相对路径，如 ``"notosanssc/v37/hash.4.woff2"``

    Returns:
        完整 URL，如 ``"https://fonts.gstatic.com/s/notosanssc/v37/hash.4.woff2"``
    """
    return _FONT_BASE_URL + rel_path
