"""嵌入 PostgreSQL 主版本解析：以 sidecar 二进制为唯一权威来源。

通过运行 ``sidecar version --json``（schema qtrading.embedded_postgres.version.v1）动态解析
捆绑 PostgreSQL 主版本，避免数据目录路径中硬编码版本号（如 ``postgres/17``）与 sidecar 实际
内置版本（如 PG16）不匹配导致数据目录 PG_VERSION 与二进制不一致、数据库启动失败。

主版本 = JSON 字段 ``postgres_version``（如 "16.14.0"）第一个点号前的部分（如 "16"）。
解析结果经 ``functools.cache`` 缓存，避免多次子进程调用；缓存仅缓存成功结果（异常不缓存），
且在同一进程生命周期内假定 sidecar 二进制不随版本变更而被替换。
"""

from __future__ import annotations

import json
import subprocess
from functools import cache
from pathlib import Path

_VERSION_TIMEOUT_S = 10


@cache
def resolve_pg_major_version(sidecar_binary: Path) -> str:
    """解析 sidecar 捆绑的 PostgreSQL 主版本号（如 "16"）。

    Args:
        sidecar_binary: sidecar 可执行文件路径。

    Raises:
        FileNotFoundError: sidecar_binary 不存在。
        RuntimeError: 子进程超时 / 非零退出 / stdout 非 JSON / 缺 postgres_version 字段 /
            主版本解析失败。
    """
    if not sidecar_binary.is_file():
        raise FileNotFoundError(f"sidecar binary not found: {sidecar_binary}")
    try:
        proc = subprocess.run(
            [str(sidecar_binary), "version", "--json"],
            capture_output=True,
            text=True,
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
    postgres_version = data.get("postgres_version")
    if "postgres_version" not in data:
        raise RuntimeError(f"sidecar version --json missing postgres_version field: {data!r}")
    if not isinstance(postgres_version, str) or not postgres_version:
        raise RuntimeError(f"sidecar version --json postgres_version must be a non-empty string: {postgres_version!r}")
    major = postgres_version.split(".", 1)[0]
    if not major.isdigit():
        raise RuntimeError(f"invalid postgres_version: {postgres_version!r}")
    return major
