"""Unit tests for scripts.sync_e2e_fonts helper functions.

Tests the non-trivial logic of _download_one() and main():
- idempotent skip when dest exists and non-empty
- woff2 magic number validation
- response size limit enforcement
- retry behavior on network errors
- successful download + file write
- atomic write temp file cleanup on failure (incl. tmp.write failure path)
- main() entry point: error paths, success path, --force flag propagation

Network access is fully mocked; no real HTTP requests are made.

Path traversal defense tests for extract_font_filename() are in
``test_e2e_font_urls.py::TestExtractFontFilename``（共享函数的统一测试位置）。
"""

from __future__ import annotations

import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# scripts.sync_e2e_fonts 不再在模块顶层 import tests.e2e._font_urls（延迟到 main() 内）
# 单元测试直接 import 模块级常量与 _download_one 函数
from scripts.sync_e2e_fonts import (  # noqa: E402
    _MAX_FONT_BYTES,
    _MAX_RETRIES,
    _WOFF2_MAGIC,
    _download_one,
    main,
)

pytestmark = pytest.mark.unit


def _make_fake_response(data: bytes) -> MagicMock:
    """构造 mock response 对象，模拟 urllib.request.urlopen 返回的 context manager。"""
    response = MagicMock()
    response.__enter__ = MagicMock(return_value=response)
    response.__exit__ = MagicMock(return_value=False)
    response.read = MagicMock(return_value=data)
    return response


class TestDownloadOneIdempotent:
    """Tests for _download_one() idempotent skip behavior."""

    def test_skips_when_dest_exists_and_nonempty(self, tmp_path: Path) -> None:
        """Existing non-empty file is skipped (idempotent)."""
        dest = tmp_path / "existing.woff2"
        dest.write_bytes(_WOFF2_MAGIC + b"\x00" * 100)  # 104 bytes, non-empty

        ok, status = _download_one("https://example.com/x.woff2", dest, force=False)

        assert ok is False
        assert "skip" in status
        assert "exists" in status
        # 文件内容未被覆盖
        assert dest.read_bytes() == _WOFF2_MAGIC + b"\x00" * 100

    def test_redownloads_when_force_true(self, tmp_path: Path) -> None:
        """--force flag bypasses idempotent skip."""
        dest = tmp_path / "existing.woff2"
        dest.write_bytes(b"old content")
        new_data = _WOFF2_MAGIC + b"new content"

        with patch("scripts.sync_e2e_fonts.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _make_fake_response(new_data)
            ok, status = _download_one("https://example.com/x.woff2", dest, force=True)

        assert ok is True
        assert "downloaded" in status
        assert dest.read_bytes() == new_data

    def test_redownloads_when_dest_empty(self, tmp_path: Path) -> None:
        """Empty dest file (0 bytes) is treated as missing and redownloaded."""
        dest = tmp_path / "empty.woff2"
        dest.write_bytes(b"")
        new_data = _WOFF2_MAGIC + b"data"

        with patch("scripts.sync_e2e_fonts.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _make_fake_response(new_data)
            ok, status = _download_one("https://example.com/x.woff2", dest, force=False)

        assert ok is True
        assert dest.read_bytes() == new_data

    def test_creates_dest_parent_when_not_exists(self, tmp_path: Path) -> None:
        """dest.parent 不存在时自动创建（mkdir parents=True 实际执行）。

        覆盖 _download_one 的 dest.parent.mkdir 路径（main() 已预创建但函数应有自身健壮性）。
        """
        dest = tmp_path / "nested" / "deep" / "dir" / "font.woff2"
        valid_data = _WOFF2_MAGIC + b"data"

        with patch("scripts.sync_e2e_fonts.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _make_fake_response(valid_data)
            ok, _ = _download_one("https://example.com/x.woff2", dest, force=False)

        assert ok is True
        assert dest.exists()
        assert dest.parent.is_dir()


class TestDownloadOneValidation:
    """Tests for _download_one() content validation (magic + size)."""

    def test_fails_when_response_not_woff2_magic(self, tmp_path: Path) -> None:
        """Response with wrong magic bytes fails immediately."""
        dest = tmp_path / "bad.woff2"
        # 不是 woff2 魔数（前 4 字节应为 b'wOF2'）
        bad_data = b"XXXX" + b"\x00" * 100

        with patch("scripts.sync_e2e_fonts.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _make_fake_response(bad_data)
            ok, status = _download_one("https://example.com/x.woff2", dest, force=False)

        assert ok is False
        assert "FAILED" in status
        assert "not a woff2 file" in status
        assert not dest.exists()

    def test_fails_when_response_exceeds_size_limit(self, tmp_path: Path) -> None:
        """Response larger than _MAX_FONT_BYTES fails immediately."""
        dest = tmp_path / "huge.woff2"
        # 构造超限数据：魔数正确但大小超过 _MAX_FONT_BYTES
        huge_data = _WOFF2_MAGIC + b"\x00" * (_MAX_FONT_BYTES + 1)

        with patch("scripts.sync_e2e_fonts.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _make_fake_response(huge_data)
            ok, status = _download_one("https://example.com/x.woff2", dest, force=False)

        assert ok is False
        assert "FAILED" in status
        assert "too large" in status
        assert not dest.exists()

    def test_succeeds_with_valid_woff2_data(self, tmp_path: Path) -> None:
        """Valid woff2 data within size limit is written to dest."""
        dest = tmp_path / "valid.woff2"
        valid_data = _WOFF2_MAGIC + b"\x00\x01\x02\x03" * 50  # 204 bytes

        with patch("scripts.sync_e2e_fonts.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _make_fake_response(valid_data)
            ok, status = _download_one("https://example.com/x.woff2", dest, force=False)

        assert ok is True
        assert "downloaded" in status
        assert str(len(valid_data)) in status
        assert dest.read_bytes() == valid_data

    def test_succeeds_when_data_exactly_at_size_limit(self, tmp_path: Path) -> None:
        """Data exactly equal to _MAX_FONT_BYTES is accepted (boundary inclusive).

        校验 `len(data) > _MAX_FONT_BYTES` 边界：等于上限应通过，仅超过 1 字节才失败。
        防止 off-by-one 错误（如误用 `>=`）。
        """
        dest = tmp_path / "boundary.woff2"
        # 构造恰好等于 _MAX_FONT_BYTES 的合法 woff2 数据
        # _WOFF2_MAGIC (4 bytes) + padding (_MAX_FONT_BYTES - 4 bytes)
        boundary_data = _WOFF2_MAGIC + b"\x00" * (_MAX_FONT_BYTES - 4)

        with patch("scripts.sync_e2e_fonts.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _make_fake_response(boundary_data)
            ok, status = _download_one("https://example.com/x.woff2", dest, force=False)

        assert ok is True
        assert "downloaded" in status
        assert dest.read_bytes() == boundary_data
        assert dest.stat().st_size == _MAX_FONT_BYTES

    def test_fails_with_empty_response(self, tmp_path: Path) -> None:
        """空响应（0 bytes）返回 FAILED 而非崩溃。

        验证 `data[:4]` 在 data 为空时不报 IndexError，且返回明确的 magic 校验失败。
        """
        dest = tmp_path / "empty_resp.woff2"

        with patch("scripts.sync_e2e_fonts.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _make_fake_response(b"")
            ok, status = _download_one("https://example.com/x.woff2", dest, force=False)

        assert ok is False
        assert "FAILED" in status
        assert "not a woff2 file" in status
        # magic=b'' 而非崩溃
        assert "magic=b''" in status
        assert not dest.exists()

    def test_leaves_no_temp_files_after_successful_write(self, tmp_path: Path) -> None:
        """Atomic write via temp file + os.replace must not leave temp files behind.

        防止临时文件残留导致 mock_assets/fonts/ 目录污染。
        """
        dest = tmp_path / "atomic.woff2"
        valid_data = _WOFF2_MAGIC + b"data"

        with patch("scripts.sync_e2e_fonts.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _make_fake_response(valid_data)
            _download_one("https://example.com/x.woff2", dest, force=False)

        # dest 已写入
        assert dest.exists()
        # 目录中不应有 .tmp 临时文件残留
        temp_files = list(tmp_path.glob(".*.tmp"))
        assert not temp_files, f"临时文件残留: {temp_files}"

    def test_cleans_temp_file_when_os_replace_fails(self, tmp_path: Path) -> None:
        """os.replace 抛 OSError 时清理临时文件并返回 FAILED（不 re-raise）。

        覆盖异常清理路径，防止 .tmp 残留 + 防止 traceback 泄露 dest 绝对路径。
        """
        dest = tmp_path / "replace_fail.woff2"
        valid_data = _WOFF2_MAGIC + b"data"

        with (
            patch("scripts.sync_e2e_fonts.urllib.request.urlopen") as mock_urlopen,
            patch("scripts.sync_e2e_fonts.os.replace", side_effect=OSError("perm denied")),
        ):
            mock_urlopen.return_value = _make_fake_response(valid_data)
            ok, status = _download_one("https://example.com/x.woff2", dest, force=False)

        assert ok is False
        assert "FAILED" in status
        assert "write error" in status
        # 临时文件应被清理
        temp_files = list(tmp_path.glob(".*.tmp"))
        assert not temp_files, f"临时文件残留: {temp_files}"
        # dest 不应存在
        assert not dest.exists()

    def test_cleans_temp_file_when_tmp_write_fails(self, tmp_path: Path) -> None:
        """tmp.write 抛 OSError 时清理临时文件并返回 FAILED（不 re-raise）。

        覆盖 R5 修复：tmp_path 必须在 tmp.write 之前赋值，否则 write 失败时无法清理。
        场景：磁盘满 / 权限拒绝 / 文件系统损坏导致 write 失败。
        """
        dest = tmp_path / "write_fail.woff2"
        valid_data = _WOFF2_MAGIC + b"data"

        # 用真实 NamedTemporaryFile 创建临时文件，但 patch 其 write 方法抛 OSError
        # 这样可以验证 tmp_path 已提前赋值，能被 unlink 清理
        created_temp_files: list[Path] = []

        import scripts.sync_e2e_fonts as sync_mod

        original_ntf = sync_mod.tempfile.NamedTemporaryFile

        def _failing_ntf(*args: object, **kwargs: object) -> object:
            real_tmp = original_ntf(*args, **kwargs)  # type: ignore[arg-type]
            # 记录创建的临时文件路径，用于后续验证清理
            created_temp_files.append(Path(real_tmp.name))
            # patch write 方法抛 OSError
            real_tmp.write = MagicMock(side_effect=OSError("disk full"))
            return real_tmp

        with (
            patch("scripts.sync_e2e_fonts.urllib.request.urlopen") as mock_urlopen,
            patch("scripts.sync_e2e_fonts.tempfile.NamedTemporaryFile", side_effect=_failing_ntf),
        ):
            mock_urlopen.return_value = _make_fake_response(valid_data)
            ok, status = _download_one("https://example.com/x.woff2", dest, force=False)

        assert ok is False
        assert "FAILED" in status
        assert "write error" in status
        # 临时文件应被清理（tmp_path 提前赋值使 unlink 生效）
        for tmp in created_temp_files:
            assert not tmp.exists(), f"临时文件残留: {tmp}"
        # dest 不应存在
        assert not dest.exists()

    def test_fails_when_mkdir_raises_oserror(self, tmp_path: Path) -> None:
        """dest.parent.mkdir 抛 OSError 时返回 FAILED（不 re-raise，不泄露路径）。

        覆盖 R1 修复：mkdir 移入 try/except，防止 OSError traceback 泄露绝对路径（R9）。
        场景：权限拒绝 / 文件系统只读 / 磁盘满。
        """
        dest = tmp_path / "mkdir_fail.woff2"
        valid_data = _WOFF2_MAGIC + b"data"

        with (
            patch("scripts.sync_e2e_fonts.urllib.request.urlopen") as mock_urlopen,
            patch("pathlib.Path.mkdir", side_effect=OSError("permission denied")),
        ):
            mock_urlopen.return_value = _make_fake_response(valid_data)
            ok, status = _download_one("https://example.com/x.woff2", dest, force=False)

        assert ok is False
        assert "FAILED" in status
        assert "write error" in status
        # dest 不应存在
        assert not dest.exists()


class TestDownloadOneRetry:
    """Tests for _download_one() retry behavior on network errors."""

    def test_retries_on_urlerror_then_succeeds(self, tmp_path: Path) -> None:
        """Network error on first attempt, success on second."""
        dest = tmp_path / "retry.woff2"
        valid_data = _WOFF2_MAGIC + b"data"

        with (
            patch("scripts.sync_e2e_fonts.urllib.request.urlopen") as mock_urlopen,
            patch("scripts.sync_e2e_fonts.time.sleep") as mock_sleep,
        ):
            # 第一次抛 URLError，第二次返回成功响应
            mock_urlopen.side_effect = [
                urllib.error.URLError("connection refused"),
                _make_fake_response(valid_data),
            ]
            ok, status = _download_one("https://example.com/x.woff2", dest, force=False)

        assert ok is True
        assert "downloaded" in status
        assert dest.read_bytes() == valid_data
        # 第一次失败后 sleep(1) 被调用
        mock_sleep.assert_called_once_with(1)

    def test_retries_on_oserror_then_succeeds(self, tmp_path: Path) -> None:
        """OSError（如连接重置）触发重试，第二次成功。

        覆盖 except (URLError, OSError, TimeoutError) 中 OSError 分支。
        """
        dest = tmp_path / "retry_oserror.woff2"
        valid_data = _WOFF2_MAGIC + b"data"

        with (
            patch("scripts.sync_e2e_fonts.urllib.request.urlopen") as mock_urlopen,
            patch("scripts.sync_e2e_fonts.time.sleep"),
        ):
            mock_urlopen.side_effect = [
                OSError("connection reset"),
                _make_fake_response(valid_data),
            ]
            ok, status = _download_one("https://example.com/x.woff2", dest, force=False)

        assert ok is True
        assert dest.read_bytes() == valid_data

    def test_fails_after_max_retries(self, tmp_path: Path) -> None:
        """All retries exhausted; returns FAILED with last error.

        验证指数 backoff 序列：1s / 2s / 4s（2**attempt）。
        重试 _MAX_RETRIES 次，sleep 调用 _MAX_RETRIES - 1 次（最后一次失败后不 sleep）。
        """
        dest = tmp_path / "fail.woff2"

        with (
            patch("scripts.sync_e2e_fonts.urllib.request.urlopen") as mock_urlopen,
            patch("scripts.sync_e2e_fonts.time.sleep") as mock_sleep,
        ):
            mock_urlopen.side_effect = urllib.error.URLError("permanent network down")
            ok, status = _download_one("https://example.com/x.woff2", dest, force=False)

        assert ok is False
        assert "FAILED" in status
        assert "URLError" in status
        # 重试 _MAX_RETRIES 次，sleep 调用 _MAX_RETRIES - 1 次（最后一次失败后不 sleep）
        assert mock_sleep.call_count == _MAX_RETRIES - 1
        # 验证指数 backoff 序列：sleep(1) / sleep(2) / ... / sleep(2**(N-2))
        expected_calls = [call(2**i) for i in range(_MAX_RETRIES - 1)]
        assert mock_sleep.call_args_list == expected_calls
        assert not dest.exists()

    def test_does_not_retry_on_programming_error(self, tmp_path: Path) -> None:
        """Non-network exceptions (e.g., TypeError) are not retried; propagated immediately."""
        dest = tmp_path / "prog.woff2"

        with (
            patch("scripts.sync_e2e_fonts.urllib.request.urlopen") as mock_urlopen,
            patch("scripts.sync_e2e_fonts.time.sleep") as mock_sleep,
        ):
            mock_urlopen.side_effect = TypeError("programming bug")
            # TypeError 不在 (URLError, OSError, TimeoutError) 中，应立即抛出
            with pytest.raises(TypeError, match="programming bug"):
                _download_one("https://example.com/x.woff2", dest, force=False)

        # 未重试
        mock_sleep.assert_not_called()
        assert not dest.exists()


class TestDownloadOneUserAgent:
    """Tests for _download_one() User-Agent header."""

    def test_sends_custom_user_agent(self, tmp_path: Path) -> None:
        """Request includes the configured User-Agent header."""
        dest = tmp_path / "ua.woff2"
        valid_data = _WOFF2_MAGIC + b"data"

        with (
            patch("scripts.sync_e2e_fonts.urllib.request.urlopen") as mock_urlopen,
            patch("scripts.sync_e2e_fonts.urllib.request.Request") as mock_request_cls,
        ):
            mock_request_cls.return_value = MagicMock()
            mock_urlopen.return_value = _make_fake_response(valid_data)
            _download_one("https://example.com/x.woff2", dest, force=False)

        # Request 构造时传入了 headers={"User-Agent": ...}
        _, kwargs = mock_request_cls.call_args
        assert "headers" in kwargs
        assert "User-Agent" in kwargs["headers"]


class TestMain:
    """Tests for main() entry point.

    覆盖 main() 的 6 条主要分支：
    - find_flet_web_main_dart_js 抛 RuntimeError
    - extract_required_font_paths 抛 RuntimeError
    - 所有字体已缓存（_download_one 全部 skip + find_missing_fonts 返回空）
    - 下载失败（_download_one 返回 FAILED）
    - 完整性校验失败（find_missing_fonts 返回非空）
    - _extract_filename 返回 None（path traversal）
    - --force 参数透传
    - find_missing_fonts 抛 RuntimeError（异常路径）

    通过 mock 所有外部依赖（find_flet_web_main_dart_js / extract_required_font_paths /
    _download_one / find_missing_fonts），让 main() 可在无 flet_web / 无网络下运行。
    """

    def _setup_common_mocks(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        *,
        required_paths: set[str] | None = None,
        download_results: dict[str, tuple[bool, str]] | None = None,
        missing_fonts: list[str] | None = None,
        find_missing_raises: Exception | None = None,
    ) -> Path:
        """配置 main() 依赖的 mock，返回 fonts_dir 路径供断言使用。"""
        if required_paths is None:
            required_paths = {"notosanssc/v37/hash.4.woff2"}
        if download_results is None:
            download_results = {}  # 默认所有 filename 未在 dict 中，使用默认 skip
        if missing_fonts is None:
            missing_fonts = []

        # mock find_flet_web_main_dart_js 返回 fake path（不实际读取）
        fake_main_js = tmp_path / "main.dart.js"
        fake_main_js.write_text("// fake")
        # 隔离 main() 内部 sys.path.insert 的副作用：用副本替换，测试结束后由 monkeypatch 自动恢复
        monkeypatch.setattr("sys.path", list(sys.path))
        # 由于 main() 内部会执行 sys.path.insert + import，需 mock import 后的符号
        # 通过 patch tests.e2e._font_urls 模块的属性来间接 mock
        import tests.e2e._font_urls as font_urls_mod

        monkeypatch.setattr(font_urls_mod, "find_flet_web_main_dart_js", lambda: fake_main_js)
        monkeypatch.setattr(
            font_urls_mod,
            "extract_required_font_paths",
            lambda _: required_paths,
        )
        if find_missing_raises is not None:
            monkeypatch.setattr(
                font_urls_mod,
                "find_missing_fonts",
                lambda _, required_paths=None: (_ for _ in ()).throw(find_missing_raises),
            )
        else:
            monkeypatch.setattr(font_urls_mod, "find_missing_fonts", lambda _, required_paths=None: missing_fonts)

        # mock _download_one：按 filename 查 dict 决定返回值
        def _fake_download_one(url: str, dest: Path, *, force: bool) -> tuple[bool, str]:
            # 从 url 提取 filename 作为 key（与 main() 逻辑一致）
            # url = _FONT_BASE_URL + rel_path，rel_path 含 filename
            filename = url.rsplit("/", 1)[-1]
            return download_results.get(filename, (False, "skip (exists, 100 bytes)"))

        monkeypatch.setattr("scripts.sync_e2e_fonts._download_one", _fake_download_one)

        # 重定向 _project_root 到 tmp_path，fonts_dir 在 tmp_path 下
        fonts_dir = tmp_path / "tests" / "e2e" / "mock_assets" / "fonts"
        fonts_dir.mkdir(parents=True, exist_ok=True)
        # main() 内部用 _project_root = Path(__file__).resolve().parent.parent，无法 patch 局部变量
        # 但 main() 通过 sys.path.insert(0, str(_project_root)) 后 import tests.e2e._font_urls
        # 我们已 mock tests.e2e._font_urls 的属性，所以 _project_root 的值不影响逻辑
        # fonts_dir 也由 main() 内部计算 _project_root / "tests" / "e2e" / "mock_assets" / "fonts"
        # 我们直接 mock fonts_dir.glob 调用结果即可（用于打印 "本地缓存文件总数"）
        # 实际上 main() 调用 fonts_dir.glob('*.woff2')，我们让 fonts_dir 存在即可
        return fonts_dir

    def test_returns_1_when_flet_web_not_installed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """find_flet_web_main_dart_js 抛 RuntimeError 时返回 1 并打印错误。"""
        import tests.e2e._font_urls as font_urls_mod

        def _raise() -> Path:
            raise RuntimeError("flet_web 包未安装")

        monkeypatch.setattr(font_urls_mod, "find_flet_web_main_dart_js", _raise)
        # sys.argv 不需要 --force
        monkeypatch.setattr("sys.argv", ["sync_e2e_fonts.py"])

        rc = main()

        assert rc == 1

    def test_returns_1_when_extract_required_font_paths_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """extract_required_font_paths 抛 RuntimeError 时返回 1。"""
        import tests.e2e._font_urls as font_urls_mod

        fake_main_js = tmp_path / "main.dart.js"
        fake_main_js.write_text("// fake")
        monkeypatch.setattr(font_urls_mod, "find_flet_web_main_dart_js", lambda: fake_main_js)

        def _raise(_: Path) -> set[str]:
            raise RuntimeError("未找到任何需缓存字体族")

        monkeypatch.setattr(font_urls_mod, "extract_required_font_paths", _raise)
        monkeypatch.setattr("sys.argv", ["sync_e2e_fonts.py"])

        rc = main()

        assert rc == 1

    def test_returns_0_when_all_fonts_cached(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """所有字体已缓存时返回 0（_download_one 全部 skip + find_missing_fonts 返回空）。"""
        self._setup_common_mocks(
            monkeypatch,
            tmp_path,
            required_paths={"notosanssc/v37/hash.4.woff2"},
            download_results={},  # 默认 skip
            missing_fonts=[],
        )
        monkeypatch.setattr("sys.argv", ["sync_e2e_fonts.py"])

        rc = main()

        assert rc == 0

    def test_returns_0_when_download_succeeds(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """下载成功且完整性校验通过时返回 0。"""
        self._setup_common_mocks(
            monkeypatch,
            tmp_path,
            required_paths={"notosanssc/v37/hash.4.woff2"},
            download_results={"hash.4.woff2": (True, "downloaded (100 bytes)")},
            missing_fonts=[],
        )
        monkeypatch.setattr("sys.argv", ["sync_e2e_fonts.py"])

        rc = main()

        assert rc == 0

    def test_returns_1_when_download_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """下载失败时返回 1（即使 find_missing_fonts 为空也 fail-fast）。"""
        self._setup_common_mocks(
            monkeypatch,
            tmp_path,
            required_paths={"notosanssc/v37/hash.4.woff2"},
            download_results={"hash.4.woff2": (False, "FAILED: network down")},
            missing_fonts=[],
        )
        monkeypatch.setattr("sys.argv", ["sync_e2e_fonts.py"])

        rc = main()

        assert rc == 1

    def test_returns_1_when_missing_fonts_after_download(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """下载成功但完整性校验失败（find_missing_fonts 返回非空）时返回 1。"""
        self._setup_common_mocks(
            monkeypatch,
            tmp_path,
            required_paths={"notosanssc/v37/hash.4.woff2"},
            download_results={"hash.4.woff2": (True, "downloaded (100 bytes)")},
            missing_fonts=["hash.4.woff2"],  # 模拟下载后仍缺失（极端场景）
        )
        monkeypatch.setattr("sys.argv", ["sync_e2e_fonts.py"])

        rc = main()

        assert rc == 1

    def test_returns_1_when_find_missing_fonts_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """find_missing_fonts 抛 RuntimeError 时返回 1（错误处理一致性）。

        覆盖 step 4 try/except RuntimeError 分支，避免未捕获异常 traceback 泄露路径。
        """
        self._setup_common_mocks(
            monkeypatch,
            tmp_path,
            required_paths={"notosanssc/v37/hash.4.woff2"},
            download_results={"hash.4.woff2": (True, "downloaded (100 bytes)")},
            find_missing_raises=RuntimeError("main.dart.js 被删除"),
        )
        monkeypatch.setattr("sys.argv", ["sync_e2e_fonts.py"])

        rc = main()

        assert rc == 1

    def test_invalid_filename_counted_as_failed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """extract_font_filename 返回 None 时计入 failed（path traversal 防御触发）。

        验证 main() 中 extract_font_filename 返回 None 的分支：
        - failed += 1
        - failed_files append basename
        - 不调用 _download_one
        - 最终返回 1
        """
        # 用 spy 替代 dict mock：spy 在被调用时抛 AssertionError，确保 _download_one 未被调用
        spy = MagicMock(side_effect=AssertionError("_download_one should not be called for invalid filename"))
        self._setup_common_mocks(
            monkeypatch,
            tmp_path,
            # 构造 path traversal 路径：..\\evil.woff2 → extract_font_filename 返回 None
            required_paths={"notosanssc/v37/..\\evil.woff2"},
            download_results={},
            missing_fonts=[],
        )
        # 覆盖 _download_one mock 为 spy
        monkeypatch.setattr("scripts.sync_e2e_fonts._download_one", spy)
        monkeypatch.setattr("sys.argv", ["sync_e2e_fonts.py"])

        rc = main()

        assert rc == 1
        spy.assert_not_called()

    def test_force_flag_propagated_to_download_one(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """--force 参数透传到 _download_one（验证 argparse 解析）。"""
        captured_force: list[bool] = []

        def _spy_download_one(url: str, dest: Path, *, force: bool) -> tuple[bool, str]:
            captured_force.append(force)
            return True, "downloaded (100 bytes)"

        self._setup_common_mocks(
            monkeypatch,
            tmp_path,
            required_paths={"notosanssc/v37/hash.4.woff2"},
            download_results=None,  # 不使用 dict，直接用 spy
            missing_fonts=[],
        )
        # 覆盖 _download_one mock 为 spy
        monkeypatch.setattr("scripts.sync_e2e_fonts._download_one", _spy_download_one)
        monkeypatch.setattr("sys.argv", ["sync_e2e_fonts.py", "--force"])

        rc = main()

        assert rc == 0
        assert captured_force == [True]
