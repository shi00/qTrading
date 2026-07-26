#!/usr/bin/env python3
"""E2E 字体同步脚本：从 flet_web 包的 main.dart.js 解析所有需缓存字体 URL，
批量下载到 tests/e2e/mock_assets/fonts/ 目录。

使用场景：
    升级 flet 版本后，main.dart.js 中字体 URL 可能变化（hash/版本号/分片 ID）。
    运行此脚本一次性同步所有字体分片，避免 E2E 测试因字体未命中而偶发失败。

用法：
    # 从项目根目录运行（需在 venv 中）
    python scripts/sync_e2e_fonts.py

    # 强制重新下载（即使文件已存在）
    python scripts/sync_e2e_fonts.py --force

设计要点：
    - 幂等：已存在的文件跳过（除非 --force）
    - 校验：下载后比对解析的 URL 数量与本地文件数量
    - 仅下载应用实际需要的字体族（notosanssc + roboto + notosans，见 _font_urls.py）
    - 失败时 fail-fast，给出明确错误指引
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

_DOWNLOAD_TIMEOUT_S = 30
_USER_AGENT = "Mozilla/5.0 (sync_e2e_fonts.py)"
# 单个 woff2 字体分片通常 < 200KB，设 5MB 上限防止异常响应污染仓库
_MAX_FONT_BYTES = 5 * 1024 * 1024
# woff2 文件魔数（RFC 8081）：b'wOF2'
_WOFF2_MAGIC = b"wOF2"
# 网络重试次数（含首次），指数 backoff：1s/2s/4s
_MAX_RETRIES = 3


def _download_one(url: str, dest: Path, *, force: bool) -> tuple[bool, str]:
    """下载单个字体文件。返回 (是否下载, 状态描述)。

    幂等：dest 已存在且非空时跳过（除非 force=True）。
    安全保护：
        - 限制响应大小 <= _MAX_FONT_BYTES（防止异常响应污染仓库）
        - 校验 woff2 魔数（防止非字体文件被 route handler 错误 fulfill）
        - 失败时重试 _MAX_RETRIES 次（指数 backoff）
    """
    if dest.exists() and dest.stat().st_size > 0 and not force:
        return False, f"skip (exists, {dest.stat().st_size} bytes)"
    last_err: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
            with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT_S) as response:
                # 限流读取：多读 1 字节用于大小校验
                data = response.read(_MAX_FONT_BYTES + 1)
            if len(data) > _MAX_FONT_BYTES:
                return False, f"FAILED: response too large (>{_MAX_FONT_BYTES} bytes)"
            if not data.startswith(_WOFF2_MAGIC):
                return False, f"FAILED: not a woff2 file (magic={data[:4]!r})"
            break
        # 仅重试可恢复的网络/IO 异常；编程错误（TypeError/ValueError 等）立即抛出
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            last_err = e
            if attempt < _MAX_RETRIES - 1:
                time.sleep(2**attempt)
    else:
        return False, f"FAILED: {type(last_err).__name__}: {last_err}"
    # 原子写入：先写临时文件再 os.replace，防止崩溃导致 dest 残留半写入内容
    # tempfile.NamedTemporaryFile 在同一目录（同 filesystem）保证 rename 原子性
    # 异常清理：mkdir / tmp.write / os.replace 抛 OSError 时均需清理临时文件，避免 .tmp 残留
    # R9: 不在异常消息中泄露 dest 绝对路径（traceback 含路径但调用方捕获后只输出类型）
    tmp_path: Path | None = None
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=dest.parent, prefix=f".{dest.name}.", suffix=".tmp", delete=False) as tmp:
            # tmp_path 必须在 write 之前赋值，否则 write 抛 OSError 时无法清理临时文件
            tmp_path = Path(tmp.name)
            tmp.write(data)
        os.replace(tmp_path, dest)
    except OSError:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        return False, "FAILED: write error (OSError)"
    return True, f"downloaded ({len(data)} bytes)"


def main() -> int:
    parser = argparse.ArgumentParser(description="同步 E2E 测试所需的字体分片到 mock_assets/fonts/")
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制重新下载，即使本地文件已存在",
    )
    args = parser.parse_args()

    # 延迟 import：让脚本能在不安装项目的情况下被 pytest import（如单元测试 main() 入口）
    # 仅在 main() 调用时才修改 sys.path，避免模块顶层副作用污染测试环境（R7 精神）
    _project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(_project_root))
    from tests.e2e._font_urls import (  # noqa: E402
        REQUIRED_FONT_FAMILIES,
        build_font_download_url,
        extract_font_filename,
        extract_required_font_paths,
        find_flet_web_main_dart_js,
        find_missing_fonts,
    )

    fonts_dir = _project_root / "tests" / "e2e" / "mock_assets" / "fonts"

    # 1. 定位 main.dart.js
    try:
        main_dart_js = find_flet_web_main_dart_js()
    except RuntimeError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1
    # R9: 不打印绝对路径（含用户名/工作区路径），仅确认已定位
    print("[INFO] 已定位 main.dart.js（flet_web/web/main.dart.js）")

    # 2. 解析需缓存的字体路径
    # 捕获 OSError/UnicodeDecodeError：main.dart.js 被并发删除或文件损坏时，
    # read_text 抛这些异常；若不捕获，traceback 会泄露 venv 绝对路径（R9）
    try:
        required_paths = extract_required_font_paths(main_dart_js)
    except (RuntimeError, OSError, UnicodeDecodeError) as e:
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    # 按字体族统计
    family_counts: dict[str, int] = {}
    for path in required_paths:
        family = path.split("/", 1)[0]
        family_counts[family] = family_counts.get(family, 0) + 1
    print(f"[INFO] 需缓存字体族: {sorted(REQUIRED_FONT_FAMILIES)}")
    for fam, count in sorted(family_counts.items()):
        print(f"  {fam}: {count} 个分片")
    print(f"[INFO] 总计: {len(required_paths)} 个字体分片")

    # 3. 批量下载
    fonts_dir.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    skipped = 0
    failed = 0
    failed_files: list[str] = []
    for path in sorted(required_paths):
        filename = extract_font_filename(path)
        if filename is None:
            failed += 1
            # R-5: failed_files 统一存 basename（与下载失败分支一致），便于后续输出
            failed_files.append(path.rsplit("/", 1)[-1])
            print(f"  [FAIL] {path}: FAILED: invalid filename (path traversal)", file=sys.stderr)
            continue
        url = build_font_download_url(path)
        dest = fonts_dir / filename
        ok, status = _download_one(url, dest, force=args.force)
        if ok:
            downloaded += 1
            print(f"  [DL] {filename}: {status}")
        elif status.startswith("FAILED"):
            failed += 1
            failed_files.append(filename)
            print(f"  [FAIL] {filename}: {status}", file=sys.stderr)
        else:
            skipped += 1
            print(f"  [SKIP] {filename}: {status}")

    # 4. 校验完整性（传入 step 2 已计算的 required_paths，避免重复解析 main.dart.js
    # 导致下载基线与校验基线不一致；同时与 step 1/2 错误处理风格一致）
    try:
        missing = find_missing_fonts(fonts_dir, required_paths=required_paths)
    except (RuntimeError, OSError, UnicodeDecodeError) as e:
        print(f"[ERROR] 字体完整性校验失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print()
    print("=" * 60)
    print(f"下载: {downloaded}, 跳过: {skipped}, 失败: {failed}")
    print(f"本地缓存文件总数: {len(list(fonts_dir.glob('*.woff2')))}")
    if missing:
        print(f"[ERROR] 仍有 {len(missing)} 个字体分片缺失:", file=sys.stderr)
        for f in missing[:10]:
            print(f"  - {f}", file=sys.stderr)
        if len(missing) > 10:
            print(f"  ... (其余 {len(missing) - 10} 个略)", file=sys.stderr)
        return 1
    if failed > 0:
        print(f"[ERROR] {failed} 个文件下载失败（已重试 {_MAX_RETRIES} 次）:", file=sys.stderr)
        for f in failed_files:
            print(f"  - {f}", file=sys.stderr)
        print(
            "  请检查网络连接后重试 `python scripts/sync_e2e_fonts.py`（已下载文件会跳过）",
            file=sys.stderr,
        )
        return 1
    print("[OK] 字体缓存完整，覆盖 main.dart.js 中所有需缓存字体分片")
    return 0


if __name__ == "__main__":
    sys.exit(main())
