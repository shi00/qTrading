"""Embedded PostgreSQL 测试根目录隔离（D21-OTHER-001）。

E2E 子进程若未显式配置 ``embedded_pg_data_root`` / ``embedded_pg_install_root`` /
``embedded_pg_log_dir``，``EmbeddedPostgresService.from_config`` 与
``EmbeddedPgMaintenanceService`` 会回退到 platformdirs 用户目录
（``~/.local/share/qTrading/postgres/<pg_major>`` 等），测试将写真实用户数据，
并可能覆写 ``runtime/password`` 运行期凭据。

本模块为每个 fixture / xdist worker 在 tmp 内生成三个独立根目录，供 E2E fixture
注入 AppConfig，保证测试不读写真实用户目录。
"""

from __future__ import annotations

import os
from pathlib import Path

# 目录名 → AppConfig 字段名；三者互不重叠，避免 data/install/log 相互覆盖。
_ROOT_DIRS: tuple[tuple[str, str], ...] = (
    ("data_root", "embedded_pg_data_root"),
    ("install_root", "embedded_pg_install_root"),
    ("logs", "embedded_pg_log_dir"),
)


def _make_private_dir(path: Path) -> None:
    """创建目录；POSIX 下收紧到 0700（Windows 无权限位，跳过 chmod）。"""
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o700)


def create_isolated_embedded_roots(tmp_dir: Path) -> dict[str, str]:
    """在 ``tmp_dir`` 下创建隔离的 embedded PG 根目录，返回 AppConfig 覆盖项。

    Args:
        tmp_dir: 调用方提供的临时目录（通常来自 ``tmp_path_factory.mktemp``），
            每个 worker/fixture 唯一。

    Returns:
        含 ``embedded_pg_data_root`` / ``embedded_pg_install_root`` /
        ``embedded_pg_log_dir`` 三个绝对路径的字典，均为 ``str`` 以便直接写入
        JSON 配置文件（Windows 反斜杠由 ``json.dumps`` 转义）。

    Raises:
        OSError: 目录创建失败（如权限不足）时向上抛出，不回退到用户目录。
    """
    base = Path(tmp_dir).resolve()
    roots = {config_key: base / dir_name for dir_name, config_key in _ROOT_DIRS}
    for path in roots.values():
        _make_private_dir(path)
    return {key: str(path) for key, path in roots.items()}
