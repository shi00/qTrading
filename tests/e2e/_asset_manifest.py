"""E2E mock 资产来源清单（manifest）读写与启动期校验。

设计背景：
    ``tests/e2e/mock_assets/`` 是从 ``flet_web`` wheel 复制的离线资源快照。
    仅校验「文件存在」无法发现版本错配（引擎 revision 变化后 canvaskit 二进制不兼容）
    或静默损坏。本模块以 manifest 为唯一事实来源，校验三项：

    1. 清单声明的 ``engine_revision`` 与当前安装的 ``flet_web`` 一致；
    2. 清单条目路径合法（拒绝绝对路径、``..``、反斜杠），文件存在；
    3. 文件 SHA-256 与清单一致。

    字体分片由 ``_font_urls.find_missing_fonts`` 按文件名完整性校验，不纳入摘要清单；
    原因见 manifest 的 ``fonts.digest_exempt_reason``。

被 ``tests/e2e/conftest.py`` 启动期断言与 ``scripts/sync_e2e_assets.py`` 复用。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

MANIFEST_NAME = "manifest.json"
MANIFEST_SCHEMA_VERSION = 1

# manifest 覆盖的资产子树（相对 mock_assets 根）
MANAGED_TREES: tuple[str, ...] = ("canvaskit", "rive")

_ENGINE_REVISION_PATTERN = re.compile(r"engineRevision[^0-9a-f]{0,8}([0-9a-f]{40})")


def find_flet_web_web_dir() -> Path:
    """返回当前解释器可见的 ``flet_web/web`` 目录。

    Raises:
        RuntimeError: ``flet_web`` 未安装或缺少 web 目录。
    """
    try:
        import flet_web  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("flet_web 包未安装，无法校验 E2E 资源 manifest 的版本与 engineRevision。") from exc
    web_dir = Path(flet_web.__file__).resolve().parent / "web"
    if not web_dir.is_dir():
        raise RuntimeError("flet_web/web 目录缺失，包可能损坏。")
    return web_dir


def read_engine_revision(web_dir: Path) -> str:
    """从 ``flutter_bootstrap.js`` 读取引擎 revision。"""
    bootstrap = web_dir / "flutter_bootstrap.js"
    content = bootstrap.read_text(encoding="utf-8")
    match = _ENGINE_REVISION_PATTERN.search(content)
    if match is None:
        raise RuntimeError("flutter_bootstrap.js 中未找到 engineRevision，无法校验资产版本一致性。")
    return match.group(1)


def installed_flet_web_version() -> str:
    """返回已安装 flet-web 的版本号。"""
    from importlib.metadata import version

    return version("flet-web")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_safe_relative_path(rel_path: str) -> bool:
    """拒绝绝对路径、``..``、``.``、反斜杠与空路径段。"""
    if not rel_path or "\\" in rel_path:
        return False
    pure = PurePosixPath(rel_path)
    if pure.is_absolute():
        return False
    return all(part not in ("", ".", "..") for part in pure.parts)


def build_manifest(mock_root: Path) -> dict[str, Any]:
    """扫描 ``mock_root`` 下受管子树，生成 manifest 数据（不写盘）。"""
    web_dir = find_flet_web_web_dir()
    assets: list[dict[str, Any]] = []
    for tree in MANAGED_TREES:
        tree_root = mock_root / tree
        if not tree_root.is_dir():
            continue
        for path in sorted(tree_root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(mock_root).as_posix()
            assets.append({"path": rel, "sha256": sha256_file(path), "size": path.stat().st_size})
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "flet_web_version": installed_flet_web_version(),
        "engine_revision": read_engine_revision(web_dir),
        "generated_by": "python -m tests.e2e._asset_manifest",
        "sources": [
            {
                "tree": "canvaskit",
                "origin": "PyPI wheel flet-web 的 flet_web/web/canvaskit（同版预装）",
                "transform": "逐字节复制，未修改；子目录集合随 engineRevision 变化",
            },
            {
                "tree": "rive",
                "origin": "@rive-app/flutter-native-wasm（运行时 CDN 已缓存副本）",
                "transform": "逐字节复制，未修改",
            },
        ],
        "licenses": {
            "canvaskit": "Apache-2.0（Flutter engine / Skia）",
            "rive": "MIT（@rive-app/flutter-native-wasm）",
        },
        "fonts": {
            "path_glob": "fonts/*.woff2",
            "digest_exempt_reason": (
                "字体分片由 _font_urls.find_missing_fonts 按 main.dart.js 所需文件名完整性校验；"
                "分片内容随 Google Fonts 版本滚动，逐文件摘要会随 CDN 更新产生无意义噪声"
            ),
        },
        "assets": assets,
    }


def write_manifest(mock_root: Path) -> Path:
    """重新生成并写入 manifest，返回写入路径。"""
    manifest = build_manifest(mock_root)
    target = mock_root / MANIFEST_NAME
    target.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return target


def load_manifest(mock_root: Path) -> dict[str, Any]:
    """读取 manifest。

    Raises:
        RuntimeError: manifest 缺失或不是合法 JSON 对象。
    """
    manifest_path = mock_root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise RuntimeError(
            f"E2E 资源清单缺失：tests/e2e/mock_assets/{MANIFEST_NAME}。"
            "运行 `python -m tests.e2e._asset_manifest`（在 E2E 使用的 venv 内）重新生成。"
        )
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"E2E 资源清单不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("E2E 资源清单顶层必须是 JSON 对象。")
    return data


def verify_assets(mock_root: Path, *, verify_digests: bool = True) -> list[str]:
    """校验 mock 资产与 manifest 一致，返回问题列表（空列表表示通过）。

    Args:
        mock_root: ``tests/e2e/mock_assets`` 目录。
        verify_digests: 为 False 时只校验路径合法性与文件存在（快速启动场景）。
    """
    problems: list[str] = []
    manifest = load_manifest(mock_root)

    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        problems.append(f"manifest schema_version={manifest.get('schema_version')!r}，期望 {MANIFEST_SCHEMA_VERSION}")

    expected_version = installed_flet_web_version()
    if manifest.get("flet_web_version") != expected_version:
        problems.append(
            f"manifest flet_web_version={manifest.get('flet_web_version')!r}，"
            f"当前安装 {expected_version}；请重新同步 mock 资产"
        )

    try:
        current_revision = read_engine_revision(find_flet_web_web_dir())
    except RuntimeError as exc:
        problems.append(str(exc))
        current_revision = ""
    if current_revision and manifest.get("engine_revision") != current_revision:
        problems.append(
            f"manifest engine_revision={manifest.get('engine_revision')!r}，"
            f"当前 flet_web 为 {current_revision}；canvaskit 二进制不兼容，必须先同步资产"
        )

    assets = manifest.get("assets")
    if not isinstance(assets, list) or not assets:
        problems.append("manifest assets 为空或类型错误")
        return problems

    for entry in assets:
        if not isinstance(entry, dict):
            problems.append("manifest assets 存在非对象条目")
            continue
        rel_path = entry.get("path")
        if not isinstance(rel_path, str) or not _is_safe_relative_path(rel_path):
            problems.append(f"manifest 非法资产路径：{rel_path!r}")
            continue
        file_path = mock_root / rel_path
        if not file_path.is_file():
            problems.append(f"资产缺失：{rel_path}")
            continue
        expected_digest = entry.get("sha256")
        if verify_digests:
            if not isinstance(expected_digest, str) or len(expected_digest) != 64:
                problems.append(f"资产缺少合法 sha256：{rel_path}")
            elif sha256_file(file_path) != expected_digest:
                problems.append(f"资产摘要不匹配：{rel_path}")
    return problems


def main() -> int:
    """重新生成 manifest（供同步脚本与人工维护调用）。"""
    mock_root = Path(__file__).resolve().parent / "mock_assets"
    target = write_manifest(mock_root)
    print(f"已写入 {target.name}：{len(load_manifest(mock_root)['assets'])} 个资产")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
