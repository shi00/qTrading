"""真实 sidecar binary 定位工具。

供集成/E2E 测试复用，定位真实 Rust sidecar binary 路径。
缺失时返回 None，由调用方 fixture 触发 pytest.skip。

定位顺序：
1. ``SIDECAR_BINARY_PATH`` 环境变量（CI 注入，最高优先级）
2. 开发模式默认路径 ``sidecars/qtrading-pg-sidecar[.exe]``（cwd-relative，对齐 ``from_config`` 开发模式分支）
3. 本地 cargo build 产物 ``sidecars/qtrading-pg-sidecar/target/release/qtrading-pg-sidecar[.exe]``
"""

from __future__ import annotations

import os
from pathlib import Path

_EXE_SUFFIX = ".exe" if os.name == "nt" else ""
_BINARY_NAME = f"qtrading-pg-sidecar{_EXE_SUFFIX}"


def find_sidecar_binary() -> Path | None:
    """定位真实 sidecar binary，缺失返回 None。

    定位顺序见模块 docstring。CI 通过 ``SIDECAR_BINARY_PATH`` 注入；
    本地开发可通过 cargo build 生成或手动下载后设置环境变量。
    """
    # 1. 环境变量（CI 注入）
    env_path = os.environ.get("SIDECAR_BINARY_PATH")
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return p.resolve()

    # 2. 开发模式默认路径（cwd-relative，对齐 from_config 开发模式分支）
    dev_path = Path("sidecars") / _BINARY_NAME
    if dev_path.is_file():
        return dev_path.resolve()

    # 3. cargo build 产物
    cargo_path = Path("sidecars") / "qtrading-pg-sidecar" / "target" / "release" / _BINARY_NAME
    if cargo_path.is_file():
        return cargo_path.resolve()

    return None


def ensure_sidecar_sha256_file(binary: Path) -> Path:
    """确保 ``<binary>.sha256`` 文件存在，覆盖真实校验路径。

    ``EmbeddedPostgresService._verify_sidecar_sha256`` 期望 ``<binary>.sha256``
    文件（格式 ``<hex>  <filename>``），缺失时跳过校验。CI 下载 sidecar 后
    通过 ``SIDECAR_SHA256`` 环境变量提供 hash，本函数写入 ``<binary>.sha256``
    使真实校验路径被执行。

    若文件已存在则跳过（本地 cargo build 场景可能已有）；若环境变量未提供
    且文件不存在，则不生成（``_verify_sidecar_sha256`` 会跳过校验，开发场景容错）。
    """
    sha256_path = binary.with_suffix(binary.suffix + ".sha256")
    if sha256_path.exists():
        return sha256_path

    sha256_hex = os.environ.get("SIDECAR_SHA256")
    if not sha256_hex:
        return sha256_path  # 开发场景容错：_verify_sidecar_sha256 会跳过

    sha256_path.write_text(f"{sha256_hex}  {binary.name}", encoding="utf-8")
    return sha256_path
