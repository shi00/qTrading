"""MCP 前置接收/留存预算与 deadline 执行（E-02 / HANDOFF §9）。

合同：
- 预算必须在启连/发现/调用前冻结（:class:`MCPBudgetGuard` 拒绝未冻结配置）；
- 「有值」不等于「有限流」：凡需在 SDK 构造完整消息**之前**拒绝的族
  （大帧 = ``receive_bytes``、深 JSON = ``structure_depth``），必须存在**真实插入点**
  （见 :data:`MCP_INBOUND_LIMIT_BINDINGS`，每条证据标出 SDK/Hook 的符号位置），
  否则准入显式拒绝（``CAPABILITY_UNVERIFIED``）；
- 结构深度以增量扫描器在字节到达时判定，不做完整 JSON 解析；
- 只能在解析后得到的族（目录/解码/schema/单条/留存）以**增量计数**计量：累计一旦
  超过该族剩余额度立即中止，不为比较而完整物化规范 JSON 文本（见
  :func:`measure_json_bytes` / :func:`encode_json_text_capped`）；
- 单一总 deadline 不因 progress/分片续期；
- 本模块不新增任何产品预算数值，数值由冻结的 :class:`MCPBudgetProfile` 提供。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from typing import Any, NoReturn

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    MCPBudgetProfile,
    MCPTransportKind,
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
    单条结果（按单条比较）/ 受管留存。
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

    def limit(self, family: str) -> int:
        """族上限（已冻结；未冻结即拒绝，不按「无上限」放行）。"""

        limit = getattr(self._profile, family)
        if limit is None:
            raise AgentServiceError(
                AgentErrorCode.CAPABILITY_UNVERIFIED, f"mcp budget not frozen: {family}", self._identity
            )
        return limit

    def remaining(self, family: str) -> int:
        """族剩余额度（增量计量的提前中止阈值；``0`` 表示再收一个字节即超限）。"""

        return self.limit(family) - self._used[family]

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
        """单条结果上限：按单条比较（不跨调用累计），仅记录观测到的最大值。"""

        limit = self._profile.single_result_bytes
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise AgentValidationError("invalid_budget", "$.mcp.budget.single_result_bytes")
        if limit is None:
            raise AgentServiceError(
                AgentErrorCode.CAPABILITY_UNVERIFIED, "mcp budget not frozen: single_result_bytes", self._identity
            )
        if amount > limit:
            raise MCPBudgetExceeded("single_result_bytes", self._identity)
        if amount > self._used["single_result_bytes"]:
            self._used["single_result_bytes"] = amount

    def retention_bytes(self, amount: int) -> None:
        self._charge("retention_bytes", amount)


#: 规范 JSON 分隔符（无空白；与调用门/参数摘要同一形态）。
_JSON_SEPARATORS = (",", ":")


def _canonical_encoder(default: Callable[[Any], Any] | None = None) -> json.JSONEncoder:
    """规范 JSON 编码器：键序排序、无空白、保留非 ASCII 原字符。"""

    return json.JSONEncoder(ensure_ascii=False, separators=_JSON_SEPARATORS, sort_keys=True, default=default)


def measure_json_bytes(obj: Any, *, limit: int, default: Callable[[Any], Any] | None = None) -> int | None:
    """增量测量规范 JSON 的 UTF-8 字节数；超过 ``limit`` 立即返回 ``None``。

    逐编码片段累计，超限即停止推进——调用方据此拒绝，故超限对象不会被完整序列化。
    ``limit`` 为负表示该族额度已耗尽（同样不推进编码）。
    """

    if limit < 0:
        return None
    total = 0
    for piece in _canonical_encoder(default).iterencode(obj):
        total += len(piece.encode("utf-8"))
        if total > limit:
            return None
    return total


def encode_json_text_capped(obj: Any, *, limit: int) -> tuple[str | None, int]:
    """在不超过 ``limit`` 字节时产出规范 JSON 文本；超限返回 ``(None, 已计字节)``。

    保留的片段总量不超过 ``limit``（加单个编码片段），超限对象不会被完整物化。
    """

    if limit < 0:
        return None, 0
    total = 0
    pieces: list[str] = []
    for piece in _canonical_encoder().iterencode(obj):
        total += len(piece.encode("utf-8"))
        if total > limit:
            return None, total
        pieces.append(piece)
    return "".join(pieces), total


class JsonDepthScanner:
    """增量结构深度扫描器：逐字节推进，字符串内括号与转义不计。

    字节级扫描是安全的：UTF-8 多字节序列的续字节均 ≥ 0x80，不会与
    ``"`` ``\\`` ``{`` ``}`` ``[`` ``]`` 的 ASCII 码冲突。
    """

    _QUOTE = 0x22
    _BACKSLASH = 0x5C
    _OPEN = (0x7B, 0x5B)
    _CLOSE = (0x7D, 0x5D)

    def __init__(self) -> None:
        self._depth = 0
        self._max_depth = 0
        self._in_string = False
        self._escaped = False

    @property
    def depth(self) -> int:
        """当前未闭合的嵌套层数（顶层文档结束后回到 0）。"""

        return self._depth

    @property
    def max_depth(self) -> int:
        """自构造以来观测到的最大嵌套层数。"""

        return self._max_depth

    def feed(self, data: bytes) -> int:
        """消费一段字节，返回更新后的最大嵌套层数。"""

        for byte in data:
            if self._in_string:
                if self._escaped:
                    self._escaped = False
                elif byte == self._BACKSLASH:
                    self._escaped = True
                elif byte == self._QUOTE:
                    self._in_string = False
                continue
            if byte == self._QUOTE:
                self._in_string = True
            elif byte in self._OPEN:
                self._depth += 1
                if self._depth > self._max_depth:
                    self._max_depth = self._depth
            elif byte in self._CLOSE and self._depth:
                self._depth -= 1
        return self._max_depth


class MCPInboundLimitBinding:
    """一个预算族在某个传输上的真实插入点证据（符号位置，不含行号引用）。"""

    __slots__ = ("family", "insertion_point", "source")

    def __init__(self, family: str, insertion_point: str, source: str) -> None:
        self.family = family
        self.insertion_point = insertion_point
        self.source = source

    def __repr__(self) -> str:  # pragma: no cover - 诊断辅助
        return f"<MCPInboundLimitBinding {self.family} @ {self.insertion_point}>"


#: 必须在「SDK 构造完整消息之前」拒绝的族（大帧 / 深 JSON）。
PRE_PARSE_LIMIT_FAMILIES: tuple[str, ...] = ("receive_bytes", "structure_depth")

#: HTTP 传输的入站插入点：我们注入的 ``httpx2`` 传输在 SDK 之前看到每个响应字节，
#: 且 streamable HTTP 另有 SDK 级单事件上限。stdio 无任何前置插入点（SDK 的
#: ``mcp.client.stdio.stdio_client`` 不读字节给调用方、无帧长参数，逐行解析在库内
#: 完成），故 stdio 不在此表中：其 ``receive_bytes`` / ``structure_depth`` 在准入时被拒，
#: 而不是按「预算有值」放行。
_HTTP_INBOUND_BINDINGS: tuple[MCPInboundLimitBinding, ...] = (
    MCPInboundLimitBinding(
        family="receive_bytes",
        insertion_point="services.agent_service.mcp_fastmcp._BudgetedInboundStream.__aiter__",
        source=(
            "httpx2 2.16 Response.stream 由传输返回后仍可替换（httpx2/_models.py Response.__init__），"
            "SDK 的 event source 从同一 response 迭代原始字节"
        ),
    ),
    MCPInboundLimitBinding(
        family="receive_bytes",
        insertion_point="fastmcp.client.transports.http.StreamableHttpTransport.max_sse_event_size",
        source=(
            "fastmcp 4.1.6 client/transports/http.py 构造参数 max_sse_event_size 传给 "
            "mcp.client.streamable_http.streamable_http_client(max_sse_event_size=...)，"
            "由 mcp client/streamable_http.py 的 sse_within_origin(max_event_size=...) 在解析前拒绝"
        ),
    ),
    MCPInboundLimitBinding(
        family="structure_depth",
        insertion_point="services.agent_service.mcp_fastmcp._BudgetedInboundStream.__aiter__",
        source="同一插入点以 JsonDepthScanner 逐字节判定嵌套深度，发生在 SDK JSON 解析之前",
    ),
)

#: 传输 → 已绑定真实插入点的入站限额族（证据缺失即准入拒绝）。
MCP_INBOUND_LIMIT_BINDINGS: Mapping[MCPTransportKind, tuple[MCPInboundLimitBinding, ...]] = {
    MCPTransportKind.STREAMABLE_HTTP: _HTTP_INBOUND_BINDINGS,
    MCPTransportKind.SSE: _HTTP_INBOUND_BINDINGS,
    # I03 DECISION（2026-11-18）：本地 stdio 保持禁用。stdio 无 SDK 解析前的字节/深度
    # 插入点，配置了前置限额族即由 ``require_pre_parse_bindings`` 拒绝（CAPABILITY_UNVERIFIED）；
    # 若要放开，必须先实现受控 stdio 中继（自管子进程 + 帧/深度限额 + env 白名单 + 审核 + 停止），
    # 再把真实插入点登记到此处——不得只删限额族绕过准入。
    MCPTransportKind.STDIO: (),
}


def missing_pre_parse_bindings(kind: MCPTransportKind, profile: MCPBudgetProfile) -> tuple[str, ...]:
    """返回该传输上**已冻结但无前置插入点**的族（按固定顺序）。"""

    bound = {binding.family for binding in MCP_INBOUND_LIMIT_BINDINGS.get(kind, ())}
    return tuple(
        family for family in PRE_PARSE_LIMIT_FAMILIES if getattr(profile, family) is not None and family not in bound
    )


def require_pre_parse_bindings(kind: MCPTransportKind, profile: MCPBudgetProfile, identity: str) -> None:
    """准入：每个需前置拒绝的族都必须有真实插入点，否则拒绝（不以有值放行）。"""

    missing = missing_pre_parse_bindings(kind, profile)
    if missing:
        raise AgentServiceError(
            AgentErrorCode.CAPABILITY_UNVERIFIED,
            f"mcp inbound limits have no verified insertion point: {','.join(missing)}",
            identity,
        )


class MCPDeadline:
    """连接/工具各自独立的单一总 deadline（秒）；progress/分片不续期。"""

    def __init__(self, *, seconds: int | None, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._seconds = validate_mcp_deadline(seconds, "$.mcp.deadline_s")
        self._monotonic = monotonic
        self._started = monotonic()

    def remaining(self) -> float:
        elapsed = self._monotonic() - self._started
        return max(0.0, self._seconds - elapsed)

    def expired(self) -> bool:
        return self.remaining() <= 0.0
