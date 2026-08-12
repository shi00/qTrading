"""嵌入 PostgreSQL 主版本解析：以 sidecar 二进制为唯一权威来源。

通过运行 ``sidecar version --json``（schema qtrading.embedded_postgres.version.v1）动态解析
捆绑 PostgreSQL 主版本，避免数据目录路径中硬编码版本号（如 ``postgres/17``）与 sidecar 实际
内置版本（如 PG16）不匹配导致数据目录 PG_VERSION 与二进制不一致、数据库启动失败。

主版本 = JSON 字段 ``postgres_version``（如 "16.14.0"）第一个点号前的部分（如 "16"）。
解析结果缓存于模块级 ``_version_cache``，避免多次子进程调用；缓存仅缓存成功结果（异常不缓存），
且由 ``_version_lock`` 保护「查缓存→解析→写缓存」临界区，并发冷缓存 miss 时仅执行一次子进程。
同一进程生命周期内假定 sidecar 二进制不随版本变更而被替换；如运行期替换了 sidecar，
调用 ``clear_pg_version_cache()`` 使下次重新解析。
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

_VERSION_TIMEOUT_S = 10  # sidecar version --json 应近即时返回，10s 兜底防挂起
_VERSION_SCHEMA = "qtrading.embedded_postgres.version.v1"

_version_cache: dict[Path, str] = {}
_version_lock = threading.Lock()


def clear_pg_version_cache() -> None:
    """清空主版本解析缓存。

    应用运行期间 sidecar 二进制被重新安装为不同 PG 主版本后，调用本函数使下次
    ``resolve_pg_major_version`` 重新解析，避免数据目录指向陈旧版本路径。
    """
    with _version_lock:
        _version_cache.clear()


def _parse_pg_major_version(sidecar_binary: Path) -> str:
    """执行 ``sidecar version --json`` 并解析主版本（不缓存，由调用方负责缓存）。"""
    if not sidecar_binary.is_file():
        raise FileNotFoundError(f"sidecar binary not found: {sidecar_binary}")
    try:
        proc = subprocess.run(
            [str(sidecar_binary), "version", "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_VERSION_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"sidecar version --json timed out after {_VERSION_TIMEOUT_S}s") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"sidecar version --json exited {proc.returncode}; stderr={proc.stderr.strip()}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"sidecar version --json produced non-JSON stdout: {proc.stdout!r}") from exc
    if data.get("schema") != _VERSION_SCHEMA:
        raise RuntimeError(
            f"sidecar version --json unexpected schema: {data.get('schema')!r}; "
            f"expected {_VERSION_SCHEMA!r}; please reinstall qTrading to fix the version mismatch"
        )
    postgres_version = data.get("postgres_version")
    if not isinstance(postgres_version, str) or not postgres_version:
        raise RuntimeError(f"sidecar version --json postgres_version must be a non-empty string: {postgres_version!r}")
    major = postgres_version.split(".", 1)[0]
    if not major.isdigit():
        raise RuntimeError(f"invalid postgres_version: {postgres_version!r}")
    return major


def resolve_pg_major_version(sidecar_binary: Path) -> str:
    """解析 sidecar 捆绑的 PostgreSQL 主版本号（如 "16"）。

    结果缓存于模块级 ``_version_cache``；锁保护临界区，并发调用且缓存冷时仅执行一次子进程。

    Args:
        sidecar_binary: sidecar 可执行文件路径。

    Raises:
        FileNotFoundError: sidecar_binary 不存在。
        RuntimeError: 子进程超时 / 非零退出 / stdout 非 JSON / schema 不符 /
            缺 postgres_version 字段 / 主版本解析失败。
    """
    with _version_lock:
        cached = _version_cache.get(sidecar_binary)
        if cached is not None:
            return cached
        major = _parse_pg_major_version(sidecar_binary)
        _version_cache[sidecar_binary] = major
        return major
