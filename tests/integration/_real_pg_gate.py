"""真实 PostgreSQL 证据的「本地可选 / required-real」判定（D21-OTHER-010）。

集成测试在本机没有 PostgreSQL 时可跳过，但「跳过」只允许用于**明确没有可用服务**：
鉴权、配置、协议错误（坏密码 / 坏库名 / 坏端口 / 协议不兼容）说明目标存在却不可用，
必须让异常传播；CI（``GITHUB_ACTIONS=true``）或显式 ``TEST_DB_REQUIRED=1`` 下连接
不可达同样必须失败，避免 required-real 证据被静默降级为 skip。

本模块只依赖 stdlib 与 asyncpg，便于单测直接验证分支，不加载业务/CacheManager。
"""

from __future__ import annotations

import os
from collections.abc import Mapping

import asyncpg

_TRUTHY = frozenset({"1", "true", "yes", "on"})

# 「明确没有服务」只会落在连接建立阶段：DNS/连接拒绝/连接超时/服务器启动中。
_UNREACHABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    OSError,
    TimeoutError,
    asyncpg.CannotConnectNowError,
)


def is_required_real_pg(environ: Mapping[str, str] | None = None) -> bool:
    """是否为 required-real 证据运行（CI 或显式 ``TEST_DB_REQUIRED=1``）。"""
    env = os.environ if environ is None else environ
    if env.get("TEST_DB_REQUIRED", "").strip().lower() in _TRUTHY:
        return True
    return env.get("GITHUB_ACTIONS", "").strip().lower() == "true"


def describe_connect_failure(exc: BaseException) -> str:
    """脱敏摘要：只保留异常类型与 SQLSTATE，不回显 DSN / 密码（R9）。"""
    sqlstate = getattr(exc, "sqlstate", None)
    return f"{type(exc).__name__} (sqlstate={sqlstate})" if sqlstate else type(exc).__name__


def should_skip_connect_failure(exc: BaseException, *, required: bool) -> bool:
    """连接失败是否可降级为 skip。

    - required：任何失败都不得 skip；
    - 非 required：仅「明确没有服务」可 skip；服务器可达后的鉴权/配置/协议错误必须抛出。
    """
    if required:
        return False
    if isinstance(exc, asyncpg.CannotConnectNowError):
        return True
    if isinstance(exc, asyncpg.PostgresError):
        return False
    return isinstance(exc, _UNREACHABLE_EXCEPTIONS)
