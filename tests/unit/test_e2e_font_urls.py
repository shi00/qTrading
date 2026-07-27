"""Unit tests for tests.e2e._font_urls helper.

Tests font URL parsing and cache completeness verification logic:
- extract_required_font_paths(): parse main.dart.js for required font registrations
- get_cached_font_filenames(): scan local fonts directory
- find_missing_fonts(): verify local cache covers all required fonts
- build_font_download_url(): construct full download URL from relative path

These tests do NOT depend on the real flet_web package; they mock
find_flet_web_main_dart_js() to point at synthetic main.dart.js fixtures.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.e2e import _font_urls
from tests.e2e._font_urls import (
    REQUIRED_FONT_FAMILIES,
    build_font_download_url,
    extract_font_filename,
    extract_required_font_paths,
    find_missing_fonts,
    get_cached_font_filenames,
    is_font_cdn_url,
)

pytestmark = pytest.mark.unit


def _write_main_dart_js(tmp_path: Path, content: str) -> Path:
    """Write synthetic main.dart.js content to a tmp file and return its path."""
    main_path = tmp_path / "main.dart.js"
    main_path.write_text(content, encoding="utf-8")
    return main_path


# Synthetic main.dart.js snippet with mixed font registrations
# Includes: notosanssc (required), roboto (required), notosans (required),
#           notosansjp (should be filtered out), notosanskr (should be filtered out)
_SAMPLE_MAIN_DART_JS = """
var x = A.m("Noto Sans SC 0", "notosanssc/v37/hash123.4.woff2");
A.m("Noto Sans SC 1", "notosanssc/v37/hash123.5.woff2");
A.m("Roboto", "roboto/v32/robotohash.woff2");
A.m("Noto Sans", 'notosans/v37/notosanshash.woff2');
A.m("Noto Sans JP 0", "notosansjp/v53/jphash.0.woff2");
A.m("Noto Sans KR 0", 'notosanskr/v36/krhash.0.woff2');
"""


class TestExtractFontFilename:
    r"""Tests for extract_font_filename().

    path traversal 防御核心函数，被 conftest.py route handler 与 sync_e2e_fonts.py 共享。
    必须覆盖所有边界分支：
    - 正常相对路径 / URL path
    - PurePosixPath.name 提取 basename（forward slash traversal 被剥离为 basename，安全）
    - Windows `\` 注入防御
    - `.` / `..` / 空串防御
    - URL 编码不被解码（安全，但需锁定契约）
    """

    def test_extracts_basename_from_relative_path(self) -> None:
        """正常相对路径：返回 basename。"""
        assert extract_font_filename("notosanssc/v37/hash.4.woff2") == "hash.4.woff2"

    def test_extracts_basename_from_url_path(self) -> None:
        """URL path（含前导 /s/）：返回 basename（覆盖 conftest 调用场景）。"""
        assert extract_font_filename("/s/notosanssc/v37/hash.4.woff2") == "hash.4.woff2"

    def test_extracts_basename_from_forward_slash_traversal(self) -> None:
        """forward slash traversal `../evil.woff2` → PurePosixPath.name 剥离为 basename。

        返回 evil.woff2（安全，仅 basename，无法逃逸 mock_assets/fonts/ 目录）。
        """
        assert extract_font_filename("../evil.woff2") == "evil.woff2"

    def test_returns_none_for_dotdot_alone(self) -> None:
        """`..` 单独传入 → PurePosixPath.name 返回 `..`，命中 `in (".", "..")` 检查。"""
        assert extract_font_filename("..") is None

    def test_returns_none_for_dotdot_in_path(self) -> None:
        """路径末段为 `..` → 命中 `in (".", "..")` 检查。"""
        assert extract_font_filename("notosanssc/v37/..") is None

    def test_returns_none_for_dot_alone(self) -> None:
        """`.` 单独传入 → PurePosixPath.name 返回 `''`，命中 `not filename` 检查。"""
        assert extract_font_filename(".") is None

    def test_returns_none_for_empty_string(self) -> None:
        """空字符串 → PurePosixPath.name 返回 `''`，命中 `not filename` 检查。"""
        assert extract_font_filename("") is None

    def test_returns_none_for_backslash_injection(self) -> None:
        r"""Windows `\` 注入：`..\\evil.woff2` → PurePosixPath.name 含 `\\`，命中检查。"""
        assert extract_font_filename("..\\evil.woff2") is None

    def test_returns_none_for_backslash_in_path(self) -> None:
        r"""路径中间含 `\`：`notosanssc/v37/..\\evil.woff2` → name 含 `\\`，命中检查。"""
        assert extract_font_filename("notosanssc/v37/..\\evil.woff2") is None

    def test_does_not_decode_url_encoded_traversal(self) -> None:
        """URL 编码的 `%2e%2e%2f` 不被解码，作为整体 filename（安全，无 path separator）。

        PurePosixPath 不解码 `%XX`，`%2e%2e%2fevil.woff2` 被视为单个文件名段。
        """
        result = extract_font_filename("%2e%2e%2fevil.woff2")
        assert result == "%2e%2e%2fevil.woff2"
        # 不含 `\` 且不在 (".", "..") 中，安全
        assert "\\" not in result
        assert result not in (".", "..")


class TestExtractRequiredFontPaths:
    """Tests for extract_required_font_paths()."""

    def test_extracts_required_families_from_double_quoted_paths(self, tmp_path: Path) -> None:
        """Double-quoted font paths for required families are extracted."""
        main_path = _write_main_dart_js(tmp_path, _SAMPLE_MAIN_DART_JS)

        paths = extract_required_font_paths(main_path)

        assert "notosanssc/v37/hash123.4.woff2" in paths
        assert "notosanssc/v37/hash123.5.woff2" in paths
        assert "roboto/v32/robotohash.woff2" in paths

    def test_extracts_single_quoted_paths(self, tmp_path: Path) -> None:
        """Single-quoted font paths are also extracted (minified JS may use either)."""
        main_path = _write_main_dart_js(tmp_path, _SAMPLE_MAIN_DART_JS)

        paths = extract_required_font_paths(main_path)

        assert "notosans/v37/notosanshash.woff2" in paths

    def test_filters_out_non_required_families(self, tmp_path: Path) -> None:
        """Font families not in REQUIRED_FONT_FAMILIES are excluded."""
        main_path = _write_main_dart_js(tmp_path, _SAMPLE_MAIN_DART_JS)

        paths = extract_required_font_paths(main_path)

        assert not any(p.startswith("notosansjp/") for p in paths)
        assert not any(p.startswith("notosanskr/") for p in paths)

    def test_returns_set_of_unique_paths(self, tmp_path: Path) -> None:
        """Duplicate registrations are deduplicated (set semantics)."""
        content = (
            'A.m("Noto Sans SC 0", "notosanssc/v37/hash.4.woff2");'
            'A.m("Noto Sans SC 0 dup", "notosanssc/v37/hash.4.woff2");'
        )
        main_path = _write_main_dart_js(tmp_path, content)

        paths = extract_required_font_paths(main_path)

        assert len(paths) == 1
        assert "notosanssc/v37/hash.4.woff2" in paths

    def test_raises_runtime_error_when_no_required_fonts_found(self, tmp_path: Path) -> None:
        """When main.dart.js has no required font registrations, raise RuntimeError."""
        content = 'A.m("Noto Sans JP", "notosansjp/v53/hash.woff2");'
        main_path = _write_main_dart_js(tmp_path, content)

        with pytest.raises(RuntimeError, match="未找到任何需缓存字体族"):
            extract_required_font_paths(main_path)

    def test_raises_runtime_error_on_empty_main_dart_js(self, tmp_path: Path) -> None:
        """Empty main.dart.js raises RuntimeError."""
        main_path = _write_main_dart_js(tmp_path, "")

        with pytest.raises(RuntimeError, match="未找到任何需缓存字体族"):
            extract_required_font_paths(main_path)


class TestGetCachedFontFilenames:
    """Tests for get_cached_font_filenames()."""

    def test_returns_empty_set_when_dir_not_exists(self, tmp_path: Path) -> None:
        """Non-existent directory returns empty set."""
        fonts_dir = tmp_path / "nonexistent"

        result = get_cached_font_filenames(fonts_dir)

        assert result == set()

    def test_returns_empty_set_when_dir_empty(self, tmp_path: Path) -> None:
        """Empty directory returns empty set."""
        fonts_dir = tmp_path / "fonts"
        fonts_dir.mkdir()

        result = get_cached_font_filenames(fonts_dir)

        assert result == set()

    def test_returns_woff2_filenames_only(self, tmp_path: Path) -> None:
        """Only .woff2 files are returned; other files are excluded."""
        fonts_dir = tmp_path / "fonts"
        fonts_dir.mkdir()
        (fonts_dir / "hash.4.woff2").write_bytes(b"\x00\x01")
        (fonts_dir / "roboto.woff2").write_bytes(b"\x00\x01")
        (fonts_dir / "README.txt").write_text("not a font")
        (fonts_dir / "canvaskit.wasm").write_bytes(b"\x00")

        result = get_cached_font_filenames(fonts_dir)

        assert result == {"hash.4.woff2", "roboto.woff2"}

    def test_returns_basenames_not_full_paths(self, tmp_path: Path) -> None:
        """Returned names are basenames (no directory prefix)."""
        fonts_dir = tmp_path / "fonts"
        fonts_dir.mkdir()
        (fonts_dir / "abc.woff2").write_bytes(b"\x00")

        result = get_cached_font_filenames(fonts_dir)

        assert all("/" not in name and "\\" not in name for name in result)
        assert "abc.woff2" in result


class TestFindMissingFonts:
    """Tests for find_missing_fonts()."""

    def test_returns_empty_list_when_cache_complete(self, tmp_path: Path) -> None:
        """When local cache covers all required fonts, returns empty list."""
        main_path = _write_main_dart_js(tmp_path, _SAMPLE_MAIN_DART_JS)
        fonts_dir = tmp_path / "fonts"
        fonts_dir.mkdir()
        # Write all required font files
        for filename in [
            "hash123.4.woff2",
            "hash123.5.woff2",
            "robotohash.woff2",
            "notosanshash.woff2",
        ]:
            (fonts_dir / filename).write_bytes(b"\x00\x01")

        with patch("tests.e2e._font_urls.find_flet_web_main_dart_js", return_value=main_path):
            missing = find_missing_fonts(fonts_dir)

        assert missing == []

    def test_returns_missing_filenames_sorted(self, tmp_path: Path) -> None:
        """Missing font filenames are returned in sorted order."""
        main_path = _write_main_dart_js(tmp_path, _SAMPLE_MAIN_DART_JS)
        fonts_dir = tmp_path / "fonts"
        fonts_dir.mkdir()
        # Only cache one of the two notosanssc files
        (fonts_dir / "hash123.4.woff2").write_bytes(b"\x00")
        # Missing: hash123.5.woff2, notosanshash.woff2, robotohash.woff2

        with patch("tests.e2e._font_urls.find_flet_web_main_dart_js", return_value=main_path):
            missing = find_missing_fonts(fonts_dir)

        # Sorted by filename
        assert missing == ["hash123.5.woff2", "notosanshash.woff2", "robotohash.woff2"]

    def test_ignores_extra_cached_files_not_in_required(self, tmp_path: Path) -> None:
        """Extra local files (not in required set) are not reported as missing."""
        main_path = _write_main_dart_js(tmp_path, _SAMPLE_MAIN_DART_JS)
        fonts_dir = tmp_path / "fonts"
        fonts_dir.mkdir()
        # Cache all required + an extra stale file
        for filename in [
            "hash123.4.woff2",
            "hash123.5.woff2",
            "robotohash.woff2",
            "notosanshash.woff2",
            "stale_old_font.woff2",  # leftover from previous flet version
        ]:
            (fonts_dir / filename).write_bytes(b"\x00")

        with patch("tests.e2e._font_urls.find_flet_web_main_dart_js", return_value=main_path):
            missing = find_missing_fonts(fonts_dir)

        assert missing == []

    def test_returns_all_required_when_dir_empty(self, tmp_path: Path) -> None:
        """Empty cache directory returns all required filenames as missing."""
        main_path = _write_main_dart_js(tmp_path, _SAMPLE_MAIN_DART_JS)
        fonts_dir = tmp_path / "fonts"
        fonts_dir.mkdir()

        with patch("tests.e2e._font_urls.find_flet_web_main_dart_js", return_value=main_path):
            missing = find_missing_fonts(fonts_dir)

        assert len(missing) == 4
        assert "hash123.4.woff2" in missing
        assert "robotohash.woff2" in missing

    def test_propagates_runtime_error_when_flet_web_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """find_flet_web_main_dart_js 抛 RuntimeError 时向上传播（契约锁定）。

        场景：flet_web 未安装，conftest.py e2e_browser fixture 调用 find_missing_fonts
        时应抛 RuntimeError 而非吞掉。
        """
        fonts_dir = tmp_path / "fonts"
        fonts_dir.mkdir()

        def _raise() -> Path:
            raise RuntimeError("flet_web 包未安装")

        monkeypatch.setattr("tests.e2e._font_urls.find_flet_web_main_dart_js", _raise)

        with pytest.raises(RuntimeError, match="flet_web 包未安装"):
            find_missing_fonts(fonts_dir)

    def test_uses_provided_required_paths_without_rediscovering(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """传入 required_paths 时跳过 find_flet_web_main_dart_js + extract_required_font_paths。

        场景：sync_e2e_fonts.py main() 已在 step 2 计算 required_paths，
        step 4 传入以避免重复解析 main.dart.js + 保证下载基线与校验基线一致。
        """
        fonts_dir = tmp_path / "fonts"
        fonts_dir.mkdir()
        # 缓存一个文件，另一个缺失
        (fonts_dir / "hash.4.woff2").write_bytes(b"\x00")
        required_paths = {"notosanssc/v37/hash.4.woff2", "notosanssc/v37/hash.5.woff2"}

        # 用 spy 验证 find_flet_web_main_dart_js / extract_required_font_paths 未被调用
        spy_find = MagicMock(side_effect=AssertionError("不应调用 find_flet_web_main_dart_js"))
        spy_extract = MagicMock(side_effect=AssertionError("不应调用 extract_required_font_paths"))
        monkeypatch.setattr("tests.e2e._font_urls.find_flet_web_main_dart_js", spy_find)
        monkeypatch.setattr("tests.e2e._font_urls.extract_required_font_paths", spy_extract)

        missing = find_missing_fonts(fonts_dir, required_paths=required_paths)

        assert missing == ["hash.5.woff2"]
        spy_find.assert_not_called()
        spy_extract.assert_not_called()

    def test_filters_invalid_filenames_in_required_paths(self, tmp_path: Path) -> None:
        """required_paths 中含 path traversal 路径时，被 extract_font_filename 过滤为 None。

        场景：main.dart.js 被篡改注入 `..\\evil.woff2`，find_missing_fonts 不应将其
        加入 required_filenames（与 sync_e2e_fonts.py / conftest.py 防御一致）。
        """
        fonts_dir = tmp_path / "fonts"
        fonts_dir.mkdir()
        # required_paths 含一个 path traversal 路径
        required_paths = {
            "notosanssc/v37/hash.4.woff2",
            "notosanssc/v37/..\\evil.woff2",  # 应被过滤
        }

        missing = find_missing_fonts(fonts_dir, required_paths=required_paths)

        # 仅 hash.4.woff2 被视为缺失，evil.woff2 被过滤
        assert missing == ["hash.4.woff2"]


class TestBuildFontDownloadUrl:
    """Tests for build_font_download_url()."""

    def test_constructs_full_url_from_relative_path(self) -> None:
        """Relative path is prefixed with gstatic base URL."""
        url = build_font_download_url("notosanssc/v37/hash.4.woff2")

        assert url == "https://fonts.gstatic.com/s/notosanssc/v37/hash.4.woff2"

    def test_handles_roboto_path(self) -> None:
        """Roboto path is constructed correctly."""
        url = build_font_download_url("roboto/v32/robotohash.woff2")

        assert url == "https://fonts.gstatic.com/s/roboto/v32/robotohash.woff2"

    def test_does_not_double_encode(self) -> None:
        """Already-encoded characters in path are not double-encoded."""
        url = build_font_download_url("notosanssc/v37/hash%20name.woff2")

        assert url == "https://fonts.gstatic.com/s/notosanssc/v37/hash%20name.woff2"


class TestIsFontCdnUrl:
    """Tests for is_font_cdn_url().

    Security: 修复 CodeQL "Incomplete URL substring sanitization" (high severity)。
    必须校验 URL host 而非子串匹配，防止 ``https://evil.com/?x=fonts.gstatic.com``
    等伪造 URL 绕过拦截器。
    """

    def test_returns_true_for_gstatic_font_url(self) -> None:
        """正常 gstatic 字体 URL → True。"""
        assert is_font_cdn_url("https://fonts.gstatic.com/s/notosanssc/v37/hash.4.woff2") is True

    def test_returns_true_for_googleapis_font_url(self) -> None:
        """正常 googleapis 字体 CSS URL → True。"""
        assert is_font_cdn_url("https://fonts.googleapis.com/css?family=Roboto") is True

    def test_returns_false_for_evil_url_with_gstatic_in_query(self) -> None:
        """CodeQL 关注点：域名出现在 query 而非 host 时必须返回 False。

        旧子串匹配 ``"fonts.gstatic.com" in url`` 会被此 URL 绕过。
        """
        assert is_font_cdn_url("https://evil.com/?x=fonts.gstatic.com") is False

    def test_returns_false_for_evil_url_with_googleapis_in_path(self) -> None:
        """CodeQL 关注点：域名出现在 path 而非 host 时必须返回 False。"""
        assert is_font_cdn_url("https://evil.com/path/fonts.googleapis.com/font.woff2") is False

    def test_returns_false_for_evil_url_with_gstatic_substring_in_host(self) -> None:
        """伪造 host 含目标域名子串（非精确匹配）→ False。

        ``evil-fonts.gstatic.com.attacker.net`` 不是受信任 host。
        """
        assert is_font_cdn_url("https://evil-fonts.gstatic.com.attacker.net/x") is False

    def test_returns_false_for_non_font_url(self) -> None:
        """无关域名 URL → False。"""
        assert is_font_cdn_url("https://example.com/font.woff2") is False

    def test_returns_false_for_data_url(self) -> None:
        """data: URL 无 host → False（conftest.py 已在上游用 is_internal 放行，
        此处仅锁定 is_font_cdn_url 自身契约：无 host 不视为字体 CDN）。"""
        assert is_font_cdn_url("data:text/css;base64,abc") is False

    def test_returns_false_for_blob_url(self) -> None:
        """blob: URL 无 host → False。"""
        assert is_font_cdn_url("blob:https://example.com/abc") is False

    def test_hostname_case_insensitive(self) -> None:
        """urlparse().hostname 按 RFC 3986 转小写，大写 host 仍应匹配。"""
        assert is_font_cdn_url("https://FONTS.GSTATIC.com/s/hash.woff2") is True

    def test_returns_false_for_empty_string(self) -> None:
        """空字符串 → False。"""
        assert is_font_cdn_url("") is False


class TestRequiredFontFamilies:
    """Tests for REQUIRED_FONT_FAMILIES constant."""

    def test_includes_noto_sans_sc(self) -> None:
        """notosanssc (Simplified Chinese) is required for locale=zh."""
        assert "notosanssc" in REQUIRED_FONT_FAMILIES

    def test_includes_roboto(self) -> None:
        """roboto is required (default UI font)."""
        assert "roboto" in REQUIRED_FONT_FAMILIES

    def test_includes_noto_sans(self) -> None:
        """notosans is required (Latin characters)."""
        assert "notosans" in REQUIRED_FONT_FAMILIES

    def test_excludes_other_cjk_families(self) -> None:
        """Other CJK families (JP/KR/HK/TC) are not required for locale=zh."""
        assert "notosansjp" not in REQUIRED_FONT_FAMILIES
        assert "notosanskr" not in REQUIRED_FONT_FAMILIES
        assert "notosanshk" not in REQUIRED_FONT_FAMILIES
        assert "notosanstc" not in REQUIRED_FONT_FAMILIES

    def test_is_frozenset(self) -> None:
        """REQUIRED_FONT_FAMILIES is a frozenset (immutable, hashable)."""
        assert isinstance(REQUIRED_FONT_FAMILIES, frozenset)


class TestFindFletWebMainDartJs:
    """Tests for find_flet_web_main_dart_js() import-error path."""

    def test_raises_runtime_error_when_flet_web_not_installed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When flet_web is not installed, raise RuntimeError with install guidance."""
        # find_flet_web_main_dart_js 有 3 步兜底搜索，全部需 mock 为「找不到」：
        # 1. import flet_web → ImportError（通过 sys.modules[name] = None 实现）
        # 2. importlib.machinery.PathFinder().find_spec("flet_web", [user_site]) → None
        # 3. site.getusersitepackages() 下查 flet_web 目录 → 不存在
        import importlib.machinery
        import site
        import sys

        # 第 1 步：sys.modules[name] = None 会使 `import name` 抛 ImportError
        monkeypatch.setitem(sys.modules, "flet_web", None)
        # 第 2 步：mock PathFinder.find_spec 返回 None（覆盖 user_site 搜索）
        # PathFinder.find_spec 是 classmethod，通过类访问时不会自动绑定 cls 参数
        # （classmethod descriptor 仅对类外访问生效，setattr 替换后是普通函数）
        monkeypatch.setattr(
            importlib.machinery.PathFinder,
            "find_spec",
            lambda name, path=None: None,
        )
        # 第 3 步：mock getusersitepackages 返回不存在的目录
        monkeypatch.setattr(site, "getusersitepackages", lambda: str(tmp_path / "nonexistent"))

        with pytest.raises(RuntimeError, match="flet_web 包未安装"):
            _font_urls.find_flet_web_main_dart_js()

    def test_returns_main_dart_js_path_when_flet_web_installed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When flet_web is installed, returns path to web/main.dart.js."""
        # 构造 fake flet_web 包结构
        fake_pkg = tmp_path / "flet_web"
        fake_pkg.mkdir()
        (fake_pkg / "__init__.py").write_text("")
        web_dir = fake_pkg / "web"
        web_dir.mkdir()
        main_js = web_dir / "main.dart.js"
        main_js.write_text("// fake main.dart.js")

        # 让 import flet_web 命中 fake 包
        # 1. 清除已缓存的 flet_web 模块（若真实环境已安装）
        # 2. prepend tmp_path 到 sys.path，使 import 优先命中 fake 包
        # monkeypatch.delitem 会在测试结束自动恢复 sys.modules 条目
        import sys

        monkeypatch.syspath_prepend(str(tmp_path))
        for k in [k for k in list(sys.modules) if k == "flet_web" or k.startswith("flet_web.")]:
            monkeypatch.delitem(sys.modules, k, raising=False)

        result = _font_urls.find_flet_web_main_dart_js()

        assert result == main_js.resolve()

    def test_raises_runtime_error_when_main_dart_js_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When flet_web is installed but main.dart.js missing, raise RuntimeError."""
        # 构造 fake flet_web 包结构（无 web/main.dart.js）
        fake_pkg = tmp_path / "flet_web"
        fake_pkg.mkdir()
        (fake_pkg / "__init__.py").write_text("")
        # 故意不创建 web/main.dart.js

        import sys

        monkeypatch.syspath_prepend(str(tmp_path))
        for k in [k for k in list(sys.modules) if k == "flet_web" or k.startswith("flet_web.")]:
            monkeypatch.delitem(sys.modules, k, raising=False)

        with pytest.raises(RuntimeError, match="main.dart.js 未找到"):
            _font_urls.find_flet_web_main_dart_js()

    def test_returns_path_via_pathfinder_fallback_when_import_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Step 2 fallback: import fails but PathFinder finds flet_web in user site.

        覆盖 find_flet_web_main_dart_js 的 3 步搜索中第 2 步兜底路径：
        - 步骤 1（import flet_web）失败 → ImportError
        - 步骤 2（PathFinder.find_spec 在 user site-packages 搜索）成功
        - 函数返回 main.dart.js 路径

        场景：flet_web 安装在 user site-packages 而非 venv site-packages，
        且测试环境通过 sys.modules["flet_web"] = None 模拟 import 失败。
        """
        import site
        import sys

        # 构造 fake user site-packages 目录，包含 flet_web 包
        fake_user_site = tmp_path / "user_site"
        fake_user_site.mkdir()
        fake_pkg = fake_user_site / "flet_web"
        fake_pkg.mkdir()
        (fake_pkg / "__init__.py").write_text("")
        web_dir = fake_pkg / "web"
        web_dir.mkdir()
        main_js = web_dir / "main.dart.js"
        main_js.write_text("// fake main.dart.js")

        # 步骤 1：让 import flet_web 抛 ImportError
        monkeypatch.setitem(sys.modules, "flet_web", None)
        # 步骤 2：让 site.getusersitepackages() 返回 fake user site 目录
        # PathFinder.find_spec 是 classmethod，会真实搜索给定路径，
        # 不需要 mock find_spec 本身，只需让 getusersitepackages 指向 fake 目录
        monkeypatch.setattr(site, "getusersitepackages", lambda: str(fake_user_site))

        result = _font_urls.find_flet_web_main_dart_js()

        assert result == main_js.resolve()

    def test_returns_path_via_directory_fallback_when_pathfinder_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Step 3 fallback: import + PathFinder both fail, but directory exists.

        覆盖 find_flet_web_main_dart_js 的 3 步搜索中第 3 步兜底路径：
        - 步骤 1（import flet_web）失败 → ImportError
        - 步骤 2（PathFinder.find_spec）返回 None（如包无 __init__.py 等）
        - 步骤 3（直接查 user_site/flet_web 目录）成功

        场景：flet_web 目录存在于 user site-packages 但缺少 __init__.py
        （PathFinder 找不到 spec，但目录结构完整）。
        """
        import importlib.machinery
        import site
        import sys

        # 构造 fake user site-packages 目录，包含 flet_web 包（无 __init__.py）
        fake_user_site = tmp_path / "user_site"
        fake_user_site.mkdir()
        fake_pkg = fake_user_site / "flet_web"
        fake_pkg.mkdir()
        # 故意不创建 __init__.py，使 PathFinder.find_spec 返回 None
        web_dir = fake_pkg / "web"
        web_dir.mkdir()
        main_js = web_dir / "main.dart.js"
        main_js.write_text("// fake main.dart.js")

        # 步骤 1：import 失败
        monkeypatch.setitem(sys.modules, "flet_web", None)
        # 步骤 2：PathFinder.find_spec 返回 None（无 __init__.py，find_spec 找不到包）
        # PathFinder.find_spec 是 classmethod，通过类访问时不会自动绑定 cls 参数
        monkeypatch.setattr(
            importlib.machinery.PathFinder,
            "find_spec",
            lambda name, path=None: None,
        )
        # 步骤 3：getusersitepackages 指向 fake 目录
        monkeypatch.setattr(site, "getusersitepackages", lambda: str(fake_user_site))

        result = _font_urls.find_flet_web_main_dart_js()

        assert result == main_js.resolve()
