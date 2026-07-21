"""ConnectionInfo 数据类：sidecar ready JSON 解析后的连接信息（Phase 2 §3.1）。

字段对齐 Rust sidecar ``protocol.rs::ReadyJson``（schema qtrading.embedded_postgres.run.ready.v1）。
frozen + slots 保证不可变与内存紧凑。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConnectionInfo:
    """sidecar ready 后返回的连接信息。

    Attributes:
        url: ``postgresql+asyncpg://user:password@host:port/database`` 形式的连接 URL（含明文密码，仅内存）。
        port: PostgreSQL 监听端口（>0）。
        pid: PostgreSQL 主进程 pid（来自 ready JSON ``pid`` 字段，可能为 0 表示 sidecar 未取到）。
        data_dir: PGDATA 绝对路径（来自 ready JSON ``data_dir`` 字段）。
        postgres_version: PostgreSQL 版本字符串（如 "17.2.0"），M4 扩展字段，默认空。
        host: PostgreSQL 监听 host（如 "127.0.0.1"），M4 扩展字段，默认空。
        sidecar_pid: sidecar 子进程 pid（区别于 postgres 主进程 pid），M4 扩展字段，默认 0。
        password_source: 密码来源（"password_file" / "keyring" 等），M4 扩展字段，默认空。
    """

    url: str
    port: int
    pid: int
    data_dir: str
    postgres_version: str = ""
    host: str = ""
    sidecar_pid: int = 0
    password_source: str = ""
