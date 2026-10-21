"""ModelConnectionsViewModel — 「模型连接」页 ViewModel（S5 双入口，reviews/模型统一.md §11.1 / §11.3）。

职责（§11.2 状态所有权）：连接草稿、字段命令、静态检查与显式单次测试。
- 不可变 state snapshot（ModelConnectionsState frozen dataclass，容器一律 tuple）
- commands 即实例方法（View 经 use_viewmodel 消费，VM 不持有 View 引用）
- VM 不 import flet、不感知 locale：message 为 Message(key)（i18n key + 参数）
- 凭据仅经 ``ModelConfigurationService``（S1）读写，VM 不直接触 ConfigHandler / keyring
- 同步 IO 已由服务层 ``ThreadPoolManager`` offload（R16），VM 不再重复包装

关键语义（§11.3）：
- 三步创建（①连接信息 ②统计方式 ③检查并保存）前后切换保留输入
- 编辑直接展开相同字段分组；凭据不回显，留空 ≠ 显式清除
- 地址不擅自改 host / 端口 ``/v1``（原样透传）
- 静态保存不要求联网；保存新连接后进入详情、零引用反馈、不自动加入任务
- 三态保存：committed / not_committed / commit_unknown（commit_unknown 可读盘恢复）

R2：所有含 await 的方法显式 ``except asyncio.CancelledError: raise``。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Final
from urllib.parse import urlsplit

from core.model_management_types import (
    ConfigurationSaveOutcome,
    ExecutionSnapshot,
    InferenceRequest,
    ModelConfigurationConflictError,
    ModelManagementSnapshot,
    SyntheticTestConsent,
    SyntheticTestContext,
)
from services.model_configuration_service import ModelConfigurationService
from ui.viewmodels import Message
from ui.viewmodels.observable_mixin import ObservableViewModelMixin
from utils.config_models import model_management_payload_fingerprint
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)

# --- 适配器类型（与 utils.config_models.ADAPTER_KINDS 对齐） ---
ADAPTER_LITELLM = "litellm_chat"
ADAPTER_GGUF_CHAT = "gguf_chat"
ADAPTER_GGUF_SENTIMENT = "gguf_sentiment"

# --- 保存状态机（三态 + 过渡 / 失败态） ---
SAVE_IDLE = "idle"
SAVE_SAVING = "saving"
SAVE_COMMITTED = "committed"
SAVE_NOT_COMMITTED = "not_committed"
SAVE_COMMIT_UNKNOWN = "commit_unknown"
SAVE_CONFLICT = "conflict"
SAVE_ERROR = "error"

# --- 单次测试状态机 ---
TEST_IDLE = "idle"
TEST_RUNNING = "running"
TEST_SUCCESS = "success"
TEST_FAILED = "failed"

_TEST_SCENARIO_ID = "synthetic_test"
_TEST_TASK_ID = "model_connection_test"
_TEST_TIMEOUT_S = 30.0

# §3.1：「执行、凭据、能力或计费改变时单调递增；仅改显示名不改变推理指纹」。
# 修订指纹只取影响执行 / 凭据 / 能力的字段（+ billing 受管子键），排除显示名与
# billing 中本表单不管理的书签字段（price_source / 缓存价 / 汇率 / 生效信息）。
_REVISION_FINGERPRINT_FIELDS: Final[tuple[str, ...]] = (
    "enabled",
    "adapter_kind",
    "provider",
    "model",
    "api_base",
    "credential_ref",
    "credential_version",
    "adapter_options",
    "context_window",
    "concurrency_limit",
)
_BILLING_FINGERPRINT_FIELDS: Final[tuple[str, ...]] = (
    "mode",
    "pricing_unit",
    "input_price_per_m",
    "output_price_per_m",
    "encoder_price_per_m",
)


def _revision_fingerprint(connection: Mapping[str, Any]) -> dict[str, Any]:
    """连接的修订指纹（§3.1）：只含影响执行 / 凭据 / 能力的字段与受管计费子键。"""
    fingerprint: dict[str, Any] = {key: connection.get(key) for key in _REVISION_FINGERPRINT_FIELDS}
    billing = connection.get("billing")
    if isinstance(billing, Mapping):
        fingerprint["billing"] = {key: billing.get(key) for key in _BILLING_FINGERPRINT_FIELDS}
    else:
        fingerprint["billing"] = billing
    return fingerprint


def normalize_egress_target(api_base: str | None) -> str | None:
    """规范化外发目标（§4.3）：scheme / host 小写、IDNA host、显式有效端口、完整 base path。

    拒绝 userinfo / query / fragment（明确非法目标）；``api_base`` 缺失、无 http(s) scheme、
    host 非法或端口越界一律返回 ``None``——不臆造目标（R21）。
    """
    raw = (api_base or "").strip()
    if not raw:
        return None
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return None
    if parts.username or parts.password or parts.query or parts.fragment:
        return None
    host = parts.hostname
    if not host:
        return None
    try:
        host = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    if port is None:
        port = 443 if scheme == "https" else 80
    if not 0 < port <= 65535:
        return None
    return f"{scheme}://{host}:{port}{parts.path.rstrip('/')}"


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _identity_validator(response: Any) -> Any:
    """单次测试的占位输出校验（真实业务校验由宿主注入的 Executor 责任链承担）。"""
    return response


# ---------------------------------------------------------------------------
# 行 / 摘要 / 草稿 记录（frozen，本文件内自定义，不建跨文件共享模块）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BillingSummary:
    """连接计量摘要（§11.1 行「计量」列）。"""

    mode: str  # usage_only / estimate_cost
    pricing_unit: str  # llm_tokens / encoder_input_tokens
    price_display: str | None = None  # estimate_cost 时输入价，usage_only 为 None


@dataclass(frozen=True, slots=True)
class ConnectionRow:
    """连接列表行（紧凑表格，稳定创建顺序）。"""

    connection_id: str
    name: str
    model: str
    adapter_kind: str
    status: str  # enabled / disabled
    enabled: bool
    billing: BillingSummary
    credential_ref: str | None
    scenario_ref_count: int  # 引用该连接的场景数（零引用反馈）


@dataclass(frozen=True, slots=True)
class ConnectionsSummary:
    """页头紧凑摘要（§11.1）：未知费用与未统计数独立字段，缺失用 None（R21）。"""

    executable_scenarios: int = 0
    configs_needing_attention: int = 0
    known_cost_cny: str | None = None
    unknown_cost_count: int | None = None
    unpriced_count: int | None = None


@dataclass(frozen=True, slots=True)
class UsageCostSummary:
    """用量费用摘要（由宿主注入的 usage loader 提供，非本 VM 持久域）。"""

    known_cost_cny: str | None = None
    unknown_cost_count: int | None = None
    unpriced_count: int | None = None


@dataclass(frozen=True, slots=True)
class ModelConnectionDraft:
    """连接草稿字段组（创建向导与编辑共用同一分组）。

    ``connection_id`` 在新草稿创建时即生成稳定标识（默认工厂而非空串）：向导「保存
    并继续」成功后仅刷新已提交基线、不清草稿，若返回本步再次保存，同一 ``connection_id``
    经 ``merged_connections`` 覆盖而非追加，避免重复连接；编辑既有连接时由
    ``_draft_from_raw`` 填入真实 id。
    """

    connection_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    adapter_kind: str = ADAPTER_LITELLM
    enabled: bool = True
    provider: str = ""
    model: str = ""
    api_base: str = ""
    billing_mode: str = "usage_only"
    billing_unit: str = "llm_tokens"
    input_price_per_m: str = ""
    output_price_per_m: str = ""
    encoder_price_per_m: str = ""
    timeout_seconds: str = ""
    concurrency_limit: str = "1"
    context_window: str = ""
    # 凭据：编辑时不回显已存凭据；credential_api_key 留空 = 保持，credential_cleared = 显式清除
    credential_ref: str | None = None
    credential_version: str | None = None
    credential_api_key: str = ""
    credential_cleared: bool = False
    # gguf 文件路径（按 adapter_kind 条件字段）
    dialog_model_path: str = ""
    encoder_model_path: str = ""
    head_path: str = ""
    tokenizer_path: str = ""
    connection_revision: int = 1


@dataclass(frozen=True)
class ModelConnectionsState:
    """ModelConnectionsViewModel 的不可变 state snapshot。"""

    connections: tuple[ConnectionRow, ...] = ()
    summary: ConnectionsSummary = ConnectionsSummary()
    search_query: str = ""
    status_filter: str = "all"  # all / enabled / disabled
    selected_connection_id: str | None = None
    editor_open: bool = False
    editor_step: int = 1  # 1..3
    draft: ModelConnectionDraft = field(default_factory=ModelConnectionDraft)
    validation_errors: tuple[str, ...] = ()
    config_revision: int = 0
    save_state: str = SAVE_IDLE
    test_state: str = TEST_IDLE
    commit_unknown_pending: bool = False
    dirty: bool = False
    message: Message | None = None


class ModelConnectionsViewModel(ObservableViewModelMixin[ModelConnectionsState]):
    """「模型连接」页 ViewModel（连接草稿 / 字段命令 / 静态检查 / 显式测试）。

    构造 DI：``ModelConfigurationService``（非单例可注入 mock）、可选 usage loader
    （提供本月费用三字段）、可选 ``scenario_executor``（单次测试执行器，宿主注入）。
    """

    def __init__(
        self,
        service: ModelConfigurationService | None = None,
        *,
        usage_summary_loader: Callable[[], Awaitable[UsageCostSummary]] | None = None,
        scenario_executor: Any | None = None,
    ) -> None:
        self._service = service if service is not None else ModelConfigurationService()
        self._usage_summary_loader = usage_summary_loader
        self._scenario_executor = scenario_executor
        self._raw_connections: tuple[Mapping[str, Any], ...] = ()
        # 原始快照元数据（用于重建 CAS payload：save_draft 期望完整 model_management
        # 域，非单连接 dict；见 _build_payload）
        self._raw_routes: tuple[Mapping[str, Any], ...] = ()
        self._usage_policy: Mapping[str, Any] = {}
        # 目标级外发授权（§4.3）：授权摘要事实源；常规草稿路径不可写，仅安全命令刷新
        self._raw_grants: tuple[Mapping[str, Any], ...] = ()
        self._ref_counts: dict[str, int] = {}
        self._pending_operation_id: str | None = None
        self._state = ModelConnectionsState()
        self._init_mixin_fields()

    # ------------------------------------------------------------------
    # 查询 / 选择命令
    # ------------------------------------------------------------------

    def set_search(self, query: str) -> None:
        self._set_state(search_query=query or "")

    def set_status_filter(self, status_filter: str) -> None:
        self._set_state(status_filter=status_filter or "all")

    def visible_connections(self) -> tuple[ConnectionRow, ...]:
        """按当前搜索 / 状态过滤返回可见行（纯函数，不触 IO）。"""
        query = self._state.search_query.strip().lower()
        status = self._state.status_filter
        rows: list[ConnectionRow] = []
        for row in self._state.connections:
            if status != "all" and row.status != status:
                continue
            if query and query not in row.name.lower() and query not in row.model.lower():
                continue
            rows.append(row)
        return tuple(rows)

    # ------------------------------------------------------------------
    # 编辑器命令（三步创建 / 编辑展开同一分组）
    # ------------------------------------------------------------------

    def start_add(self) -> None:
        """空状态一次点击添加：进入三步创建第一步，刷新草稿。"""
        self._set_state(
            editor_open=True,
            editor_step=1,
            selected_connection_id=None,
            draft=ModelConnectionDraft(),
            validation_errors=(),
            dirty=False,
            save_state=SAVE_IDLE,
            test_state=TEST_IDLE,
            commit_unknown_pending=False,
        )

    def edit(self, connection_id: str) -> None:
        """编辑已有连接：直接展开相同字段分组，不走创建向导。"""
        raw = next((c for c in self._raw_connections if str(c.get("connection_id")) == connection_id), None)
        if raw is None:
            self._set_state(message=Message("model_connection_not_found"))
            return
        self._set_state(
            editor_open=True,
            editor_step=1,
            selected_connection_id=connection_id,
            draft=self._draft_from_raw(raw),
            validation_errors=(),
            dirty=False,
            save_state=SAVE_IDLE,
            test_state=TEST_IDLE,
            commit_unknown_pending=False,
        )

    def set_editor_step(self, step: int) -> None:
        """切换创建步骤（1..3），前后切换保留 draft 输入。"""
        self._set_state(editor_step=max(1, min(3, int(step))))

    # ------------------------------------------------------------------
    # 字段更新命令（按 adapter_kind 条件字段；下拉 / 开关 / 互斥 / 数字输入）
    # ------------------------------------------------------------------

    def _update_draft(self, **changes: Any) -> None:
        self._set_state(draft=replace(self._state.draft, **changes), dirty=True, validation_errors=())

    def update_adapter_kind(self, adapter_kind: str) -> None:
        self._update_draft(adapter_kind=adapter_kind)

    def update_enabled(self, enabled: bool) -> None:
        self._update_draft(enabled=bool(enabled))

    def update_name(self, value: str) -> None:
        self._update_draft(name=value or "")

    def update_provider(self, value: str) -> None:
        self._update_draft(provider=value or "")

    def update_model(self, value: str) -> None:
        self._update_draft(model=value or "")

    def update_api_base(self, value: str) -> None:
        # §11.3：地址不擅自改 host / 端口 / /v1，原样保存
        self._update_draft(api_base=value or "")

    def update_billing_mode(self, mode: str) -> None:
        self._update_draft(billing_mode=mode)

    def update_billing_unit(self, unit: str) -> None:
        self._update_draft(billing_unit=unit)

    def update_credential(self, value: str) -> None:
        """录入新凭据（留空 = 保持已存凭据，不视为清除）。"""
        self._update_draft(credential_api_key=value or "", credential_cleared=False)

    def update_credential_cleared(self, cleared: bool) -> None:
        """显式清除凭据（与留空严格分开）。"""
        self._update_draft(credential_cleared=bool(cleared))

    def update_timeout_seconds(self, value: str) -> None:
        self._update_draft(timeout_seconds=value or "")

    def update_concurrency_limit(self, value: str) -> None:
        self._update_draft(concurrency_limit=value or "")

    def update_context_window(self, value: str) -> None:
        self._update_draft(context_window=value or "")

    def update_input_price(self, value: str) -> None:
        self._update_draft(input_price_per_m=value or "")

    def update_output_price(self, value: str) -> None:
        self._update_draft(output_price_per_m=value or "")

    def update_encoder_price(self, value: str) -> None:
        self._update_draft(encoder_price_per_m=value or "")

    def update_dialog_model_path(self, value: str) -> None:
        self._update_draft(dialog_model_path=value or "")

    def update_encoder_model_path(self, value: str) -> None:
        self._update_draft(encoder_model_path=value or "")

    def update_head_path(self, value: str) -> None:
        self._update_draft(head_path=value or "")

    def update_tokenizer_path(self, value: str) -> None:
        self._update_draft(tokenizer_path=value or "")

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------

    async def load(self) -> None:
        """新鲜读取已提交配置快照并映射为行 / 摘要（§11.1）。"""
        self._track_inflight_cancellable()  # F40：只读加载，页面卸载可安全取消
        try:
            await self._refresh_snapshot()
        except asyncio.CancelledError:
            raise  # R2
        except Exception as ex:
            logger.error(
                "[ModelConnectionsVM] load snapshot failed: %s",
                DataSanitizer.sanitize_error(ex),
                exc_info=True,
            )
            self._set_state(message=Message("model_connections_load_failed"))

    async def _refresh_snapshot(self) -> None:
        snapshot = await self._service.get_snapshot()
        self._raw_connections = snapshot.connections
        self._raw_routes = snapshot.scenario_routes
        self._usage_policy = snapshot.usage_policy
        self._raw_grants = snapshot.security_grants
        self._ref_counts = self._count_refs(snapshot)
        usage = UsageCostSummary()
        if self._usage_summary_loader is not None:
            usage = await self._usage_summary_loader()
        rows = tuple(self._row_from_raw(c) for c in snapshot.connections)
        self._set_state(
            connections=rows,
            summary=self._summary_from_snapshot(snapshot, usage),
            config_revision=snapshot.config_revision,
        )

    @staticmethod
    def _count_refs(snapshot: ModelManagementSnapshot) -> dict[str, int]:
        counts: dict[str, int] = {}
        for route in snapshot.scenario_routes:
            for cid in route.get("candidates") or []:
                counts[str(cid)] = counts.get(str(cid), 0) + 1
            for sub in (route.get("task_routes") or {}).values():
                for cid in (sub or {}).get("candidates") or []:
                    counts[str(cid)] = counts.get(str(cid), 0) + 1
        return counts

    def _row_from_raw(self, raw: Mapping[str, Any]) -> ConnectionRow:
        billing_raw = raw.get("billing") or {}
        enabled = bool(raw.get("enabled", True))
        cid = str(raw.get("connection_id", ""))
        return ConnectionRow(
            connection_id=cid,
            name=str(raw.get("name", "")),
            model=str(raw.get("model", "")),
            adapter_kind=str(raw.get("adapter_kind", "")),
            status="enabled" if enabled else "disabled",
            enabled=enabled,
            billing=BillingSummary(
                mode=str(billing_raw.get("mode", "")),
                pricing_unit=str(billing_raw.get("pricing_unit", "")),
                price_display=self._billing_price_display(billing_raw),
            ),
            credential_ref=raw.get("credential_ref"),
            scenario_ref_count=self._ref_counts.get(cid, 0),
        )

    @staticmethod
    def _billing_price_display(billing_raw: Mapping[str, Any]) -> str | None:
        if billing_raw.get("mode") != "estimate_cost":
            return None
        price = billing_raw.get("input_price_per_m")
        return str(price) if price is not None else None

    @staticmethod
    def _summary_from_snapshot(snapshot: ModelManagementSnapshot, usage: UsageCostSummary) -> ConnectionsSummary:
        executable = sum(1 for r in snapshot.scenario_routes if r.get("enabled") and (r.get("candidates") or []))
        needs = 0
        for conn in snapshot.connections:
            caps = conn.get("capabilities") or {}
            unknown = any(
                isinstance(evidence, Mapping) and evidence.get("support") == "unknown" for evidence in caps.values()
            )
            if not bool(conn.get("enabled", True)) or not caps or unknown:
                needs += 1
        return ConnectionsSummary(
            executable_scenarios=executable,
            configs_needing_attention=needs,
            known_cost_cny=usage.known_cost_cny,
            unknown_cost_count=usage.unknown_cost_count,
            unpriced_count=usage.unpriced_count,
        )

    def _draft_from_raw(self, raw: Mapping[str, Any]) -> ModelConnectionDraft:
        billing = raw.get("billing") or {}
        adapter_options = raw.get("adapter_options") or {}
        return ModelConnectionDraft(
            connection_id=str(raw.get("connection_id", "")),
            name=str(raw.get("name", "")),
            adapter_kind=str(raw.get("adapter_kind", ADAPTER_LITELLM)),
            enabled=bool(raw.get("enabled", True)),
            provider=str(raw.get("provider", "") or ""),
            model=str(raw.get("model", "") or ""),
            api_base=str(raw.get("api_base", "") or ""),
            billing_mode=str(billing.get("mode", "usage_only")),
            billing_unit=str(billing.get("pricing_unit", "llm_tokens")),
            input_price_per_m=str(billing.get("input_price_per_m") or ""),
            output_price_per_m=str(billing.get("output_price_per_m") or ""),
            encoder_price_per_m=str(billing.get("encoder_price_per_m") or ""),
            context_window=str(raw.get("context_window") or ""),
            concurrency_limit=str(raw.get("concurrency_limit", 1)),
            credential_ref=raw.get("credential_ref"),
            credential_version=raw.get("credential_version"),
            credential_api_key="",  # 凭据不回显
            credential_cleared=False,
            dialog_model_path=str(adapter_options.get("dialog_model_path") or ""),
            encoder_model_path=str(adapter_options.get("encoder_model_path") or ""),
            head_path=str(adapter_options.get("head_path") or ""),
            tokenizer_path=str(adapter_options.get("tokenizer_path") or ""),
            connection_revision=max(1, int(raw.get("connection_revision") or 1)),
        )

    # ------------------------------------------------------------------
    # 静态校验（不要求联网 / 推理通过）
    # ------------------------------------------------------------------

    def _validate_draft(self) -> tuple[str, ...]:
        draft = self._state.draft
        errors: list[str] = []
        if not draft.name.strip():
            errors.append("model_connection_error_name_required")
        if draft.adapter_kind not in (ADAPTER_LITELLM, ADAPTER_GGUF_CHAT, ADAPTER_GGUF_SENTIMENT):
            errors.append("model_connection_error_adapter_invalid")
        elif draft.adapter_kind == ADAPTER_LITELLM:
            if not draft.model.strip():
                errors.append("model_connection_error_model_required")
        elif draft.adapter_kind == ADAPTER_GGUF_CHAT:
            if not draft.dialog_model_path.strip():
                errors.append("model_connection_error_dialog_required")
        else:
            paths = (draft.encoder_model_path, draft.head_path, draft.tokenizer_path)
            if not all(p.strip() for p in paths):
                errors.append("model_connection_error_gguf_paths_required")
        if draft.billing_mode == "estimate_cost" and not draft.input_price_per_m.strip():
            errors.append("model_connection_error_price_required")
        return tuple(errors)

    # ------------------------------------------------------------------
    # 草稿 → 连接 payload
    # ------------------------------------------------------------------

    async def _resolve_credential(
        self, draft: ModelConnectionDraft, operation_id: str
    ) -> tuple[str | None, str | None]:
        api_key = (draft.credential_api_key or "").strip()
        if api_key:
            return await self._service.prepare_credential_version(operation_id, api_key)
        if draft.credential_cleared:
            return None, None
        return draft.credential_ref, draft.credential_version

    def _next_connection_revision(self, connection_payload: Mapping[str, Any]) -> int:
        """计算本次保存应写入的 ``connection_revision``（§3.1 单调递增）。

        新连接从 1 起；既有连接仅当修订指纹（执行 / 凭据 / 能力 / 受管计费）变化时
        ``+1``，仅改显示名等非指纹字段保持现值——否则 §11.4 的风险确认会被无关编辑
        反复作废。
        """
        cid = str(connection_payload.get("connection_id", ""))
        raw = next((c for c in self._raw_connections if str(c.get("connection_id")) == cid), None)
        if raw is None:
            return 1
        current = max(1, int(raw.get("connection_revision") or 1))
        merged = {**dict(raw), **dict(connection_payload)}
        if _revision_fingerprint(merged) == _revision_fingerprint(raw):
            return current
        return current + 1

    def _draft_to_connection_payload(
        self,
        draft: ModelConnectionDraft,
        credential_ref: str | None,
        credential_version: str | None,
    ) -> dict[str, Any]:
        adapter_options: dict[str, Any] = {}
        if draft.adapter_kind == ADAPTER_GGUF_CHAT:
            provider, api_base, model = "", None, draft.model.strip() or draft.dialog_model_path.strip()
            credential_ref, credential_version = None, None
            adapter_options["dialog_model_path"] = draft.dialog_model_path.strip()
        elif draft.adapter_kind == ADAPTER_GGUF_SENTIMENT:
            provider, api_base, model = "", None, draft.model.strip() or draft.encoder_model_path.strip()
            credential_ref, credential_version = None, None
            adapter_options["encoder_model_path"] = draft.encoder_model_path.strip()
            adapter_options["head_path"] = draft.head_path.strip()
            adapter_options["tokenizer_path"] = draft.tokenizer_path.strip()
        else:
            provider, api_base, model = draft.provider.strip(), draft.api_base.strip() or None, draft.model.strip()
        return {
            "connection_id": draft.connection_id or str(uuid.uuid4()),
            "name": draft.name.strip(),
            "enabled": draft.enabled,
            "adapter_kind": draft.adapter_kind,
            "provider": provider,
            "model": model,
            "api_base": api_base,
            "credential_ref": credential_ref,
            "credential_version": credential_version,
            "adapter_options": adapter_options,
            "context_window": self._to_int_or_none(draft.context_window),
            "concurrency_limit": self._to_int_or_none(draft.concurrency_limit) or 1,
            "billing": self._billing_payload(draft),
            "connection_revision": max(1, int(draft.connection_revision or 1)),
        }

    @staticmethod
    def _billing_payload(draft: ModelConnectionDraft) -> dict[str, Any]:
        if draft.billing_mode == "estimate_cost":
            return {
                "mode": "estimate_cost",
                "pricing_unit": draft.billing_unit,
                "input_price_per_m": draft.input_price_per_m.strip() or None,
                "output_price_per_m": draft.output_price_per_m.strip() or None,
                "encoder_price_per_m": draft.encoder_price_per_m.strip() or None,
            }
        return {"mode": "usage_only", "pricing_unit": draft.billing_unit}

    def _build_payload(self, connection_payload: Mapping[str, Any]) -> dict[str, Any]:
        """重建完整 ``model_management`` CAS payload（§5.1）。

        ``save_draft`` 期望完整 model_management 域（connections / scenario_routes /
        usage_policy / schema_version），故把当前连接草稿合并进已提交连接的
        ``connections`` 列表（同 ``connection_id`` 覆盖、否则追加），其余域沿快照
        已提交值；``security_state`` 由 ConfigHandler CAS 以磁盘现值保留（草稿不能
        改安全状态，§5.3）；迁移子域（migration_state / migration_transition）同样
        不入草稿 payload，由迁移专用命令独占、CAS 以磁盘现值回填（F07）。
        """
        cid = str(connection_payload.get("connection_id", ""))
        connections = [dict(c) for c in self._raw_connections]
        for index, existing in enumerate(connections):
            if str(existing.get("connection_id")) == cid:
                merged = dict(existing)
                merged.update(connection_payload)
                connections[index] = merged
                break
        else:
            connections.append(dict(connection_payload))
        return {
            "schema_version": 1,
            "config_revision": self._state.config_revision,
            "connections": connections,
            "scenario_routes": [dict(r) for r in self._raw_routes],
            "usage_policy": dict(self._usage_policy),
        }

    @staticmethod
    def _to_int_or_none(value: str) -> int | None:
        text = (value or "").strip()
        if not text:
            return None
        try:
            return int(text)
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------
    # 保存（三态）
    # ------------------------------------------------------------------

    async def save(self) -> bool:
        """提交草稿（CAS）：三态处理 + revision 冲突保留草稿 + commit_unknown 读盘恢复入口。"""
        if self._state.save_state == SAVE_SAVING:
            return False
        self._track_inflight_cancellable()  # F40：凭据准备阶段可安全取消
        errors = self._validate_draft()
        if errors:
            self._set_state(
                validation_errors=errors,
                save_state=SAVE_ERROR,
                message=Message("model_connection_validation_failed"),
            )
            return False

        self._set_state(save_state=SAVE_SAVING, validation_errors=())
        operation_id = str(uuid.uuid4())
        try:
            draft = self._state.draft
            credential_ref, credential_version = await self._resolve_credential(draft, operation_id)
            connection_payload = self._draft_to_connection_payload(draft, credential_ref, credential_version)
            connection_payload["connection_revision"] = self._next_connection_revision(connection_payload)
            payload = self._build_payload(connection_payload)
            # F40：进入提交阶段——dispose 不再取消本任务（写可能经线程池最终落库，
            # 取消后宣称未提交即掩盖事实；CancelledError 传播不写任何状态，磁盘为 SSOT）。
            self._mark_inflight_committing()
            outcome = await self._service.save_draft(payload, self._state.config_revision, operation_id)
        except asyncio.CancelledError:
            raise  # R2
        except ModelConfigurationConflictError:
            # 冲突：保留草稿，不覆盖磁盘旧配置
            self._set_state(save_state=SAVE_CONFLICT, message=Message("model_connection_save_conflict"))
            return False
        except Exception as ex:
            logger.error(
                "[ModelConnectionsVM] save failed: %s",
                DataSanitizer.sanitize_error(ex),
                exc_info=True,
            )
            self._set_state(save_state=SAVE_ERROR, message=Message("model_connection_save_failed"))
            return False

        return await self._absorb_save_outcome(outcome, operation_id, connection_payload)

    async def _absorb_save_outcome(
        self,
        outcome: ConfigurationSaveOutcome,
        operation_id: str,
        payload: Mapping[str, Any],
    ) -> bool:
        if outcome.status == "committed":
            self._pending_operation_id = None
            await self._safe_refresh_snapshot()
            connection_id = str(payload.get("connection_id", ""))
            # §11.3：保存新连接后进入详情；零引用反馈「配置场景 / 保留未分配」，不自动加入任务
            if self._ref_counts.get(connection_id, 0) == 0:
                message = Message("model_connection_saved_unassigned")
            else:
                message = Message("model_connection_saved")
            self._set_state(
                save_state=SAVE_COMMITTED,
                commit_unknown_pending=False,
                dirty=False,
                editor_open=False,
                selected_connection_id=connection_id,
                validation_errors=(),
                message=message,
            )
            return True
        if outcome.status == "commit_unknown":
            self._pending_operation_id = operation_id
            self._set_state(
                save_state=SAVE_COMMIT_UNKNOWN,
                commit_unknown_pending=True,
                message=Message("model_connection_save_commit_unknown"),
            )
            return False
        self._set_state(save_state=SAVE_NOT_COMMITTED, message=Message("model_connection_save_not_committed"))
        return False

    async def resolve_commit_unknown(self) -> bool:
        """回执恢复：按相同锁顺序重读磁盘确认提交结果（§5.1）。"""
        if not self._state.commit_unknown_pending or self._pending_operation_id is None:
            return False
        self._track_inflight_cancellable()  # F40：回执恢复只读确认，可安全取消
        try:
            outcome = await self._service.resolve_save_outcome(self._pending_operation_id)
        except asyncio.CancelledError:
            raise  # R2
        except Exception as ex:
            logger.error(
                "[ModelConnectionsVM] resolve outcome failed: %s",
                DataSanitizer.sanitize_error(ex),
                exc_info=True,
            )
            self._set_state(save_state=SAVE_ERROR, message=Message("model_connection_save_failed"))
            return False

        if outcome.status == "committed":
            self._pending_operation_id = None
            await self._safe_refresh_snapshot()
            self._set_state(
                save_state=SAVE_COMMITTED,
                commit_unknown_pending=False,
                dirty=False,
                message=Message("model_connection_saved"),
            )
            return True
        self._set_state(
            save_state=SAVE_COMMIT_UNKNOWN,
            commit_unknown_pending=True,
            message=Message("model_connection_save_commit_unknown"),
        )
        return False

    async def _safe_refresh_snapshot(self) -> None:
        try:
            await self._refresh_snapshot()
        except asyncio.CancelledError:
            raise  # R2
        except Exception as ex:
            logger.error(
                "[ModelConnectionsVM] refresh snapshot failed: %s",
                DataSanitizer.sanitize_error(ex),
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # 单次测试（未保存连接）：begin_synthetic_test + 注入的 ScenarioExecutor
    # ------------------------------------------------------------------

    async def test(self) -> bool:
        """未保存连接的单次测试：不提交连接 / 场景 / 业务授权，不写 security_state。"""
        if self._state.test_state == TEST_RUNNING:
            return False
        self._track_inflight_cancellable()  # F40：单次测试无持久化语义，页面卸载可安全取消
        errors = self._validate_draft()
        if errors:
            self._set_state(
                validation_errors=errors,
                test_state=TEST_FAILED,
                message=Message("model_connection_validation_failed"),
            )
            return False
        if self._scenario_executor is None:
            self._set_state(test_state=TEST_FAILED, message=Message("model_connection_test_unavailable"))
            return False

        self._set_state(test_state=TEST_RUNNING, validation_errors=(), message=Message("model_connection_test_running"))
        try:
            draft = self._state.draft
            payload = self._draft_to_connection_payload(draft, draft.credential_ref, draft.credential_version)
            ctx = await self._service.begin_synthetic_test(
                payload, task_id=_TEST_TASK_ID, credential_version=draft.credential_version
            )
            consent = self._service.create_synthetic_test_consent(
                ctx,
                target_fingerprint=self._target_fingerprint(draft),
                deadline=self._deadline(),
            )
            request = InferenceRequest(
                scenario_id=_TEST_SCENARIO_ID,
                task_id=ctx.task_id,
                invocation_id=ctx.invocation_id,
                payload={},
                deadline=consent.deadline,
            )
            snapshot = self._build_test_snapshot(ctx, payload, consent)
            result = await self._scenario_executor.execute(request, snapshot, _identity_validator)
            succeeded = getattr(result, "status", None) == "succeeded"
            self._set_state(
                test_state=TEST_SUCCESS if succeeded else TEST_FAILED,
                message=Message("model_connection_test_success" if succeeded else "model_connection_test_failed"),
            )
            return succeeded
        except asyncio.CancelledError:
            raise  # R2
        except Exception as ex:
            logger.error(
                "[ModelConnectionsVM] synthetic test failed: %s",
                DataSanitizer.sanitize_error(ex),
                exc_info=True,
            )
            self._set_state(test_state=TEST_FAILED, message=Message("model_connection_test_failed"))
            return False

    @staticmethod
    def _deadline() -> float:
        return asyncio.get_running_loop().time() + _TEST_TIMEOUT_S

    @staticmethod
    def _target_fingerprint(draft: ModelConnectionDraft) -> str:
        return model_management_payload_fingerprint(
            {
                "adapter_kind": draft.adapter_kind,
                "provider": draft.provider.strip(),
                "model": draft.model.strip(),
                "api_base": draft.api_base.strip(),
            }
        )

    @staticmethod
    def _build_test_snapshot(
        ctx: SyntheticTestContext,
        payload: Mapping[str, Any],
        consent: SyntheticTestConsent,
    ) -> ExecutionSnapshot:
        candidate = MappingProxyType(
            {
                "task_id": ctx.task_id,
                "candidate_index": 0,
                "connection_id": ctx.draft_connection_id,
                "target_fingerprint": consent.target_fingerprint,
                "purpose": "synthetic_test",
            }
        )
        route = MappingProxyType({"scenario_id": _TEST_SCENARIO_ID, "candidates": (candidate,)})
        connection = MappingProxyType(
            {
                "connection_id": ctx.draft_connection_id,
                "connection_revision": ctx.draft_connection_revision,
                "adapter_kind": payload["adapter_kind"],
                "provider": payload["provider"],
                "model": payload["model"],
                "api_base": payload["api_base"],
                "adapter_options": dict(payload["adapter_options"]),
                "context_window": payload["context_window"],
                "concurrency_limit": payload["concurrency_limit"],
                "credential_ref": payload["credential_ref"],
                "credential_version": payload["credential_version"],
            }
        )
        return ExecutionSnapshot(
            snapshot_id=ctx.test_operation_id,
            run_id=None,
            config_revision=ctx.config_revision,
            routes=(route,),
            connections=(connection,),
            business_contract_versions={_TEST_SCENARIO_ID: "1"},
            security_scope_version=consent.scope_version,
            created_at=_utc_now_iso(),
        )

    # ------------------------------------------------------------------
    # 外发授权摘要查询（§4.3：设置与向导「场景检查区」共用，组件不触私有成员）
    # ------------------------------------------------------------------

    def connection_target(self, connection_id: str) -> str | None:
        """连接的规范化外发目标（§4.3）。

        本地适配器（gguf，无 ``api_base``）不外发，返回 ``None``——组件据此不渲染授权
        操作，不臆造目标（R21）。
        """
        raw = next((c for c in self._raw_connections if str(c.get("connection_id")) == connection_id), None)
        if raw is None:
            return None
        return normalize_egress_target(raw.get("api_base"))

    def grants_for(self, connection_id: str, purpose: str) -> tuple[Mapping[str, Any], ...]:
        """该连接在指定用途下当前有效的 grant（未撤销），供授权摘要展示（§4.3）。"""
        return tuple(
            g
            for g in self._raw_grants
            if str(g.get("connection_id")) == connection_id and g.get("purpose") == purpose and not g.get("revoked_at")
        )

    # ------------------------------------------------------------------
    # 向导原子提交的公开入口（§11.6：宿主不得触达 VM 私有成员）
    # ------------------------------------------------------------------

    def validate_draft(self) -> tuple[str, ...]:
        """当前连接草稿的静态校验结果（公开，供向导子步骤校验；不要求联网）。"""
        return self._validate_draft()

    async def draft_connection_entry(self, operation_id: str) -> dict[str, Any] | None:
        """把当前草稿转为连接 payload（含凭据解析与单调 ``connection_revision``）。

        Returns:
            ``None`` 表示草稿名称为空（本阶段无连接变更）；否则完整的连接 payload。
        """
        draft = self._state.draft
        if not draft.name.strip():
            return None
        credential_ref, credential_version = await self._resolve_credential(draft, operation_id)
        entry = self._draft_to_connection_payload(draft, credential_ref, credential_version)
        entry["connection_revision"] = self._next_connection_revision(entry)
        return entry

    def merged_connections(self, entry: Mapping[str, Any] | None) -> list[dict[str, Any]]:
        """已提交连接与草稿 entry 的合并列表（同 id 覆盖、否则追加；§11.6）。"""
        merged = [dict(c) for c in self._raw_connections]
        if entry is None:
            return merged
        connection_id = str(entry.get("connection_id", ""))
        for index, existing in enumerate(merged):
            if str(existing.get("connection_id")) == connection_id:
                merged[index] = dict(entry)
                return merged
        merged.append(dict(entry))
        return merged

    # ------------------------------------------------------------------
    # 外发授权命令（安全子域，§4.3）
    # ------------------------------------------------------------------

    async def grant_egress(
        self,
        connection_id: str,
        target_fingerprint: str,
        purpose: str,
        scope_version: int = 1,
    ) -> bool:
        operation_id = str(uuid.uuid4())
        self._track_inflight_cancellable()  # F40：授权请求发起前可安全取消
        try:
            self._mark_inflight_committing()
            outcome = await self._service.grant_egress(
                connection_id,
                target_fingerprint,
                purpose,
                scope_version,
                self._state.config_revision,
                operation_id,
            )
        except asyncio.CancelledError:
            raise  # R2
        except Exception as ex:
            logger.error(
                "[ModelConnectionsVM] grant egress failed: %s",
                DataSanitizer.sanitize_error(ex),
                exc_info=True,
            )
            self._set_state(message=Message("model_connection_egress_failed"))
            return False
        return await self._absorb_egress_outcome(outcome, operation_id)

    async def revoke_egress(self, grant_id: str) -> bool:
        operation_id = str(uuid.uuid4())
        self._track_inflight_cancellable()  # F40：撤销请求发起前可安全取消
        try:
            self._mark_inflight_committing()
            outcome = await self._service.revoke_egress(grant_id, self._state.config_revision, operation_id)
        except asyncio.CancelledError:
            raise  # R2
        except Exception as ex:
            logger.error(
                "[ModelConnectionsVM] revoke egress failed: %s",
                DataSanitizer.sanitize_error(ex),
                exc_info=True,
            )
            self._set_state(message=Message("model_connection_egress_failed"))
            return False
        return await self._absorb_egress_outcome(outcome, operation_id)

    async def _absorb_egress_outcome(self, outcome: ConfigurationSaveOutcome, operation_id: str) -> bool:
        if outcome.status == "committed":
            await self._safe_refresh_snapshot()
            self._set_state(message=Message("model_connection_egress_updated"))
            return True
        if outcome.status == "commit_unknown":
            self._pending_operation_id = operation_id
            self._set_state(
                save_state=SAVE_COMMIT_UNKNOWN,
                commit_unknown_pending=True,
                message=Message("model_connection_save_commit_unknown"),
            )
            return False
        self._set_state(message=Message("model_connection_egress_failed"))
        return False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def dispose(self) -> None:
        """清理敏感草稿（凭据 / gguf 路径不残留内存），随后执行可观察协议关闭。

        F40：基类 dispose 会取消 cancellable 在途任务（单次测试 / 加载 / 凭据准备），
        committing 任务（已进入提交的保存 / 授权）放行自然完成——取消后宣称未提交
        会掩盖线程池写最终落库的事实；回执恢复（commit_unknown）已清，恢复线索以
        磁盘 SSOT 为准，下次进入页面 load() 重读。
        """
        self._state = replace(
            self._state,
            draft=ModelConnectionDraft(),
            editor_open=False,
            selected_connection_id=None,
            commit_unknown_pending=False,
            test_state=TEST_IDLE,
        )
        self._pending_operation_id = None
        super().dispose()
