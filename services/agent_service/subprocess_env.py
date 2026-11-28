"""受管子进程的最小系统环境白名单（stdio MCP 与附件 worker 共用）。

为什么需要本模块
----------------
启动子进程时绝不能继承父进程完整 ``os.environ``：父进程环境可能含数据库连接串、
Tushare Token、云端 API Key 等业务秘密，而子进程要处理的输入（如用户附件）属于
不可信数据。任何以 ``env = dict(os.environ)`` 派生的子进程环境都会把这些秘密复制
给不可信输入的同级进程，扩大泄露面（R9）。

本模块只保留**进程启动所必需的 OS 非秘密变量**（与锁定版 SDK
``mcp.client.stdio.get_default_environment`` 的隐式清单对齐），其余一律不继承。
调用方可显式追加本次运行所需的非秘密键（如 ``PYTHONPATH``），并把 ``TEMP`` / ``TMP``
强制指向本次运行专属的受管目录；追加项不得绕过白名单复制父进程环境。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

__all__ = ["OS_BASELINE_KEYS", "TEMP_KEYS", "build_minimal_child_env"]

#: 子进程显式环境基线（覆盖 SDK ``get_default_environment()`` 在 Windows / POSIX 的
#: 全部可注入键）：只为进程启动所必需的 OS 非秘密变量，不含应用秘密副本。
OS_BASELINE_KEYS: tuple[str, ...] = (
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "USERPROFILE",
    "USERNAME",
    "APPDATA",
    "LOCALAPPDATA",
    "HOMEDRIVE",
    "HOMEPATH",
    "HOME",
    "LOGNAME",
    "SHELL",
    "TERM",
    "USER",
)

#: 子进程临时目录键：调用方应指向本次运行专属受管目录，不继承应用/用户临时目录。
TEMP_KEYS: tuple[str, ...] = ("TEMP", "TMP")


def build_minimal_child_env(
    extra: Mapping[str, str] | None = None,
    *,
    temp_dir: Path | None = None,
) -> dict[str, str]:
    """白名单基线 + 显式追加项 + 强制受管 ``TEMP`` / ``TMP``。

    ``extra`` 只能承载调用方为启动子进程显式决定的非秘密键；基线之外的父进程
    环境变量（数据库/Token/AI Key/代理等）一律不会出现在结果中。
    """

    env = {key: os.environ[key] for key in OS_BASELINE_KEYS if key in os.environ}
    if extra:
        env.update(extra)
    if temp_dir is not None:
        env.update({key: str(temp_dir) for key in TEMP_KEYS})
    return env
