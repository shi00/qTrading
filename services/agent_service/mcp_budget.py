"""MCP 前置接收/留存预算与 deadline 执行（E-02 / HANDOFF §9）。

合同：
- 预算必须在启连/发现/调用前冻结（:class:`MCPBudgetGuard` 拒绝未冻结配置），
  且每族在**大量分配之前**计量：恰等通过、超一拒绝；
- 结构深度在 JSON 完整解析前扫描（字符串内的花括号不计），不做事后遍历；
- progress/分片不重置单一总 deadline；
- 本模块不新增任何产品预算数值，数值由冻结的 :class:`MCPBudgetProfile` 提供。
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Callable
from typing import NoReturn

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    MCPBudgetProfile,
    validate_mcp_deadline,
)


class MCPBudgetExceeded(AgentServiceError):
    """预算超限：稳定族名 + 原身份；不截断冒完整成功。"""

    def __init__(self, family: str, identity: str) -> None:
        super().__init__(AgentErrorCode.BUDGET_BLOCKED, f"{family}_exceeded", identity)
        self.family = family


class MCPBudgetGuard:
    """按冻结预算累计各计量族；未冻结即不准入。

    计量族：接收字节 / 解码表示 / 结构深度 / 目录数量与字节 / schema 累计 /
    单结果 / 受管留存。
    """

    def __init__(self, profile: MCPBudgetProfile, *, identity: str) -> None:
        unfrozen = profile.unfrozen_fields()
        if unfrozen:
            raise AgentServiceError(
                AgentErrorCode.CAPABILITY_UNVERIFIED,
                f"mcp receive budgets not frozen: {','.join(unfrozen)}",
                identity,
            )
        self._profile = profile
        self._identity = identity
        self._used: dict[str, int] = dict.fromkeys(
            (
                "receive_bytes",
                "decoded_bytes",
                "catalog_items",
                "catalog_bytes",
                "schema_bytes",
                "single_result_bytes",
                "retention_bytes",
            ),
            0,
        )

    # -- 通用 ---------------------------------------------------------------

    def used(self, family: str) -> int:
        return self._used[family]

    def exceed(self, family: str) -> NoReturn:
        raise MCPBudgetExceeded(family, self._identity)

    def _charge(self, family: str, amount: int) -> None:
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise AgentValidationError("invalid_budget", f"$.mcp.budget.{family}")
        limit = getattr(self._profile, family)
        if limit is None:  # 已由构造期准入拦下；防御性保持拒绝而非放开
            raise AgentServiceError(
                AgentErrorCode.CAPABILITY_UNVERIFIED, f"mcp budget not frozen: {family}", self._identity
            )
        if self._used[family] + amount > limit:
            raise MCPBudgetExceeded(family, self._identity)
        self._used[family] += amount

    # -- 各计量族 -----------------------------------------------------------

    def receive_bytes(self, amount: int) -> None:
        self._charge("receive_bytes", amount)

    def decoded_bytes(self, amount: int) -> None:
        self._charge("decoded_bytes", amount)

    def structure_depth(self, observed_depth: int) -> None:
        limit = self._profile.structure_depth
        if limit is None:
            raise AgentServiceError(
                AgentErrorCode.CAPABILITY_UNVERIFIED, "mcp budget not frozen: structure_depth", self._identity
            )
        if isinstance(observed_depth, bool) or not isinstance(observed_depth, int) or observed_depth < 0:
            raise AgentValidationError("invalid_budget", "$.mcp.budget.structure_depth")
        if observed_depth > limit:
            raise MCPBudgetExceeded("structure_depth", self._identity)

    def catalog_item(self) -> None:
        self._charge("catalog_items", 1)

    def catalog_bytes(self, amount: int) -> None:
        self._charge("catalog_bytes", amount)

    def schema_bytes(self, amount: int) -> None:
        self._charge("schema_bytes", amount)

    def single_result_bytes(self, amount: int) -> None:
        self._charge("single_result_bytes", amount)

    def retention_bytes(self, amount: int) -> None:
        self._charge("retention_bytes", amount)


async def read_bounded_stream(source: AsyncIterator[bytes], guard: MCPBudgetGuard) -> bytes:
    """分块读取：每块在追加前计入接收字节；超限立即停止拉取后续块。"""

    chunks: list[bytes] = []
    async for chunk in source:
        guard.receive_bytes(len(chunk))
        chunks.append(chunk)
    return b"".join(chunks)


def bounded_json_loads(text: str, guard: MCPBudgetGuard) -> object:
    """先扫描结构深度（完整解析前拒绝），再按标准库 JSON 解析。"""

    guard.structure_depth(_json_nesting_depth(text))
    return json.loads(text)


def _json_nesting_depth(text: str) -> int:
    """增量扫描 JSON 嵌套深度；字符串内括号不计，转义字符正确处理。"""

    depth = 0
    max_depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            depth += 1
            max_depth = max(max_depth, depth)
        elif char in "}]":
            depth -= 1
    return max_depth


class MCPProgressGuard:
    """连续 progress 过限早拒绝；完成后重新计数（不无限缓冲 progress）。"""

    def __init__(self, guard: MCPBudgetGuard, *, max_consecutive: int) -> None:
        if isinstance(max_consecutive, bool) or not isinstance(max_consecutive, int) or max_consecutive < 1:
            raise AgentValidationError("invalid_budget", "$.mcp.progress.max_consecutive")
        self._guard = guard
        self._max = max_consecutive
        self._consecutive = 0

    def observe(self) -> None:
        self._consecutive += 1
        if self._consecutive > self._max:
            self._guard.exceed("progress_burst")

    def completed(self) -> None:
        self._consecutive = 0


class MCPDeadline:
    """连接/工具各自独立的单一总 deadline（秒）；progress 不续期。"""

    def __init__(self, *, seconds: int | None, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._seconds = validate_mcp_deadline(seconds, "$.mcp.deadline_s")
        self._monotonic = monotonic
        self._started = monotonic()

    def observe_progress(self) -> None:
        """分片/进度到达不重置起点（HANDOFF §9）。"""

    def remaining(self) -> float:
        elapsed = self._monotonic() - self._started
        return max(0.0, self._seconds - elapsed)

    def expired(self) -> bool:
        return self.remaining() <= 0.0
