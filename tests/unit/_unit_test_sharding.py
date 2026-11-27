"""单测分片选择的纯函数（P3-CI-Unit-Suite-Shard-Required）。

CI 的 ``ci-checks`` job 以 4 个 matrix 分片并行执行 ``tests/unit/``；
``tests/unit/conftest.py`` 的 ``pytest_ignore_collect`` 借助本模块把不属于当前分片的
``test_*.py`` 排除出收集阶段。分片映射必须满足：

- **稳定**：同一 POSIX 相对路径在任意 runner / 任意运行下落到同一分片，便于复现；
- **完整且互斥**：所有分片并集覆盖全部测试文件，任一分片不会漏跑或重复执行同一文件；
- **显式失败**：环境变量半配置或取值非法时必须报错，禁止静默退化为「每个分片都跑全量」
  （那会让门禁看似并行、实际重复执行且失去分片收益）。
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping

#: 当前分片下标（0 起）。与 ``UNIT_TEST_SHARD_COUNT`` 必须同时设置。
SHARD_INDEX_ENV = "UNIT_TEST_SHARD_INDEX"
#: 分片总数。
SHARD_COUNT_ENV = "UNIT_TEST_SHARD_COUNT"


def parse_shard_env(env: Mapping[str, str] | None = None) -> tuple[int, int] | None:
    """解析分片环境变量。

    两者均未设置时返回 ``None``（分片未启用，按全量执行）；只设置其中一个、取值非整数、
    分片总数为 0 或下标越界时抛出 ``ValueError``。
    """
    values = os.environ if env is None else env
    raw_index = values.get(SHARD_INDEX_ENV)
    raw_count = values.get(SHARD_COUNT_ENV)
    if raw_index is None and raw_count is None:
        return None
    if raw_index is None or raw_count is None:
        raise ValueError(f"{SHARD_INDEX_ENV} 与 {SHARD_COUNT_ENV} 必须同时设置")
    try:
        index = int(raw_index)
        count = int(raw_count)
    except ValueError as exc:
        raise ValueError(f"分片参数必须为整数：index={raw_index!r}, count={raw_count!r}") from exc
    if count < 1:
        raise ValueError(f"{SHARD_COUNT_ENV} 必须 >= 1，实际 {count}")
    if not 0 <= index < count:
        raise ValueError(f"{SHARD_INDEX_ENV} 必须位于 [0, {count})，实际 {index}")
    return index, count


def shard_bucket(relative_path: str, count: int) -> int:
    """按 POSIX 相对路径稳定映射到 ``[0, count)`` 分片。"""
    if count < 1:
        raise ValueError(f"分片总数必须 >= 1，实际 {count}")
    digest = hashlib.blake2b(relative_path.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % count
