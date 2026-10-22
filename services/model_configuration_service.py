"""统一模型管理配置应用服务（S1，reviews/模型统一.md §4.3 / §5 / §12.1）。

职责边界（§6.1）：
- 配置保存 / 回执恢复 / 迁移预览 / 外发授权命令 / authorize_attempt / 单次测试上下文；
- 安全命令只经 ConfigHandler 本域接口（compare_and_save_model_management /
  read_model_management_fresh），不新建授权文件或进程内授权单例；
- 同步 IO（文件锁 / keyring）经 ThreadPoolManager 提交（R16）；CancelledError 始终
  传播（R2）；本服务采用 DI，不注册单例（§6.1）。
"""

from __future__ import annotations

import asyncio
import copy
import datetime as dt
import hashlib
import json
import re
import socket
import uuid
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from core.model_management_types import (
    AttemptPermit,
    ConfigurationSaveOutcome,
    MigrationTransitionError,
    ModelConfigurationConflictError,
    ModelManagementSnapshot,
    SyntheticTestConsent,
    SyntheticTestContext,
)
from utils import config_handler as cfg
from utils.config_models import ModelManagementSaveResult
from utils.loop_local import get_loop_local
from utils.thread_pool import TaskType, ThreadPoolManager

_PURPOSES = ("news_annotation", "stock_analysis", "news_risk", "synthetic_test")

# legacy 封口月份格式（S6 D4）：与 LegacySealConfig.month 正则一致，服务侧先行校验给出精确错误
_MONTH_SEAL_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class _FrozenMapping(dict):
    """嵌套层只读 dict（F38 运行快照契约）：任何变更入口一律 TypeError。

    刻意选 dict 子类而非 MappingProxyType：嵌套冻结容器会随消费方 ``dict()``
    浅拷贝进入 CAS 保存 payload，而 storage 层对 payload 做 ``copy.deepcopy``
    与 ``json.dumps``（回执指纹）——mappingproxy 两者均不支持（deepcopy 抛
    cannot pickle、json 抛 TypeError）；dict 子类对 deepcopy / json / Pydantic
    校验原生兼容，仅封禁变更面，磁盘 JSON 内容与指纹语义不变。
    """

    def __setitem__(self, key: Any, value: Any) -> None:
        raise TypeError("frozen snapshot mapping is read-only")

    def __delitem__(self, key: Any) -> None:
        raise TypeError("frozen snapshot mapping is read-only")

    def clear(self) -> None:
        raise TypeError("frozen snapshot mapping is read-only")

    def pop(self, *args: Any, **kwargs: Any) -> Any:
        raise TypeError("frozen snapshot mapping is read-only")

    def popitem(self) -> tuple[Any, Any]:
        raise TypeError("frozen snapshot mapping is read-only")

    def setdefault(self, *args: Any, **kwargs: Any) -> Any:
        raise TypeError("frozen snapshot mapping is read-only")

    def update(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("frozen snapshot mapping is read-only")

    def __ior__(self, other: Any) -> _FrozenMapping:
        raise TypeError("frozen snapshot mapping is read-only")

    def __copy__(self) -> _FrozenMapping:
        return _FrozenMapping(self)

    def __deepcopy__(self, memo: dict[int, Any]) -> _FrozenMapping:
        return _FrozenMapping(copy.deepcopy(dict(self), memo))


def _freeze_value(value: Any) -> Any:
    """递归冻结快照叶子（F38）：dict → :class:`_FrozenMapping`，list → 冻结项 tuple。

    标量与其余类型原样透传；tuple 容器本身已不可变，仅逐项冻结。
    """
    if isinstance(value, dict):
        return _FrozenMapping((key, _freeze_value(item)) for key, item in value.items())
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_value(item) for item in value)
    return value


def _freeze_view(mapping: Mapping[str, Any]) -> Mapping[str, Any]:
    """顶层只读视图 + 嵌套叶子递归冻结（F38）。

    顶层保持既有 MappingProxyType 契约（只读语义与现有测试锚不变）；
    嵌套 dict / list 递归冻结，使 candidates、adapter_options、capabilities、
    legacy_seals 等嵌套叶子同获「发布后不可变」的运行快照契约。
    """
    return MappingProxyType({key: _freeze_value(item) for key, item in mapping.items()})


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _lease_expired(lease_expires_at: Any) -> bool:
    """迁移租约过期判定（S6 D4）：``now(UTC) >= lease_expires_at`` 解析值即过期。

    缺失 / 格式非法按已过期处理（保守：坏数据不无限续权，接管方会以合法租约覆写）。
    """
    if not isinstance(lease_expires_at, str) or not lease_expires_at:
        return True
    try:
        expires = dt.datetime.fromisoformat(lease_expires_at)
    except ValueError:
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=dt.UTC)
    return dt.datetime.now(dt.UTC) >= expires


def _payload_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def _outcome_from_cas(result: ModelManagementSaveResult, expected_revision: int) -> ConfigurationSaveOutcome:
    """ConfigHandler 层状态转译服务层三态（R1：utils 不导入 services，转译在本层）。"""
    if result.status == "committed":
        return ConfigurationSaveOutcome(
            status="committed",
            current_revision=result.current_revision,
            committed_revision=result.committed_revision,
            last_config_commit=result.last_config_commit,
        )
    if result.status == "revision_conflict":
        raise ModelConfigurationConflictError(
            expected_revision=expected_revision, current_revision=result.current_revision
        )
    if result.status == "commit_unknown":
        # F11：替换成功后故障且读回无法证明——不臆断未提交，交调用方 resolve_save_outcome 恢复
        return ConfigurationSaveOutcome(status="commit_unknown", current_revision=result.current_revision)
    # not_committed / lock_timeout：替换前确定失败，旧配置仍有效
    return ConfigurationSaveOutcome(status="not_committed", current_revision=result.current_revision)


class ModelConfigurationService:
    """统一模型配置 / 凭据 / 授权应用服务（非单例，DI 组装）。"""

    def __init__(self) -> None:
        # 同 operation_id 的安全重试复用同一凭据版本（MM-12）：进程内租约映射，
        # 不持久化；服务实例销毁即释放（keyring 项由保守清理策略保留）。
        self._credential_leases: dict[str, tuple[str, str]] = {}

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    async def get_snapshot(self) -> ModelManagementSnapshot:
        """新鲜读取并冻结已提交非敏感配置快照（§5.1：不经 load_config 缓存命中路径）。

        F38：冻结为递归语义——嵌套 dict / list（candidates、adapter_options、
        capabilities、legacy_seals 等）同获只读契约，嵌套写入一律失败；
        顶层仍为 MappingProxyType（契约不变）。
        """

        def _read() -> ModelManagementSnapshot:
            fresh = cfg.ConfigHandler.read_model_management_fresh()
            mm = fresh["model_management"]
            transition = mm.get("migration_transition")
            return ModelManagementSnapshot(
                snapshot_id=str(uuid.uuid4()),
                config_revision=mm["config_revision"],
                migration_state=mm["migration_state"],
                connections=tuple(_freeze_view(c) for c in mm["connections"]),
                scenario_routes=tuple(_freeze_view(r) for r in mm["scenario_routes"]),
                usage_policy=_freeze_view(mm["usage_policy"]),
                security_revision=mm["security_state"]["security_revision"],
                # F01：授权范围版本独立于 security_revision（磁盘缺键 → schema 默认 1，
                # 与旧配置及 UI grant 创建默认同域）
                security_scope_version=int(mm["security_state"].get("scope_version", 1)),
                security_grants=tuple(_freeze_view(g) for g in mm["security_state"]["grants"]),
                migration_transition=_freeze_view(transition) if transition is not None else None,
                created_at=_utc_now_iso(),
            )

        return await ThreadPoolManager().run_async(TaskType.IO, _read)

    # ------------------------------------------------------------------
    # 保存（§5.1）
    # ------------------------------------------------------------------

    async def save_draft(
        self,
        payload: dict[str, Any],
        expected_revision: int,
        operation_id: str,
    ) -> ConfigurationSaveOutcome:
        """保存草稿：CAS 提交；安全子域与迁移子域均以磁盘现值为准（§5.3 草稿
        不能改安全状态；F07 迁移子域由 begin / seal / activate 专用命令独占）。"""

        def _save() -> ModelManagementSaveResult:
            return cfg.ConfigHandler.compare_and_save_model_management(
                expected_revision, payload, operation_id, update_security_state=False
            )

        return _outcome_from_cas(await ThreadPoolManager().run_async(TaskType.IO, _save), expected_revision)

    async def resolve_save_outcome(self, operation_id: str) -> ConfigurationSaveOutcome:
        """按相同锁顺序重读磁盘凭回执确认提交结果（§5.1 / MM-12）。

        回执 operation_id 匹配即确认 committed 并刷新缓存，不再次写配置；
        不能证明提交结果或回执已被后续写入替换则保持 commit_unknown。
        """

        def _resolve() -> ConfigurationSaveOutcome:
            fresh = cfg.ConfigHandler.read_model_management_fresh()
            mm = fresh["model_management"]
            receipt = mm.get("last_config_commit")
            if isinstance(receipt, dict) and receipt.get("operation_id") == operation_id:
                return ConfigurationSaveOutcome(
                    status="committed",
                    current_revision=mm["config_revision"],
                    committed_revision=receipt.get("committed_revision"),
                    last_config_commit=receipt,
                )
            return ConfigurationSaveOutcome(status="commit_unknown", current_revision=mm["config_revision"])

        return await ThreadPoolManager().run_async(TaskType.IO, _resolve)

    # ------------------------------------------------------------------
    # 安全命令（§4.3：security_revision 单调；安全命令也递增 config_revision）
    # ------------------------------------------------------------------

    async def grant_egress(
        self,
        connection_id: str,
        target_fingerprint: str,
        purpose: str,
        scope_version: int,
        expected_revision: int,
        operation_id: str,
    ) -> ConfigurationSaveOutcome:
        """目标级外发授权 grant（MM-01）：仅追加安全子域，普通草稿路径不可及。"""
        if purpose not in _PURPOSES:
            raise ValueError(f"非法外发用途: {purpose}")

        def _grant() -> ModelManagementSaveResult:
            fresh = cfg.ConfigHandler.read_model_management_fresh()
            mm = dict(fresh["model_management"])
            security = dict(mm["security_state"])
            grants = list(security["grants"])
            grants.append(
                {
                    "grant_id": str(uuid.uuid4()),
                    "connection_id": connection_id,
                    "target_fingerprint": target_fingerprint,
                    "purpose": purpose,
                    "scope_version": scope_version,
                    "confirmed_at": _utc_now_iso(),
                    "revoked_at": None,
                }
            )
            security["grants"] = grants
            security["security_revision"] = int(security["security_revision"]) + 1
            mm["security_state"] = security
            return cfg.ConfigHandler.compare_and_save_model_management(
                expected_revision, mm, operation_id, update_security_state=True
            )

        return _outcome_from_cas(await ThreadPoolManager().run_async(TaskType.IO, _grant), expected_revision)

    async def revoke_egress(
        self,
        grant_id: str,
        expected_revision: int,
        operation_id: str,
    ) -> ConfigurationSaveOutcome:
        """撤销 grant（MM-02）：旧版本不允许取消撤销；撤销立即阻止新许可。"""

        def _revoke() -> ModelManagementSaveResult:
            fresh = cfg.ConfigHandler.read_model_management_fresh()
            mm = dict(fresh["model_management"])
            security = dict(mm["security_state"])
            grants = [dict(g) for g in security["grants"]]
            target = next((g for g in grants if g["grant_id"] == grant_id), None)
            if target is None:
                raise ValueError(f"grant 不存在: {grant_id}")
            target["revoked_at"] = _utc_now_iso()
            security["grants"] = grants
            security["security_revision"] = int(security["security_revision"]) + 1
            mm["security_state"] = security
            return cfg.ConfigHandler.compare_and_save_model_management(
                expected_revision, mm, operation_id, update_security_state=True
            )

        return _outcome_from_cas(await ThreadPoolManager().run_async(TaskType.IO, _revoke), expected_revision)

    async def revoke_credential(
        self,
        credential_version: str,
        expected_revision: int,
        operation_id: str,
    ) -> ConfigurationSaveOutcome:
        """凭据版本撤销（§5.2）：追加 revoked_credential_versions，不允许取消撤销。"""

        def _revoke() -> ModelManagementSaveResult:
            fresh = cfg.ConfigHandler.read_model_management_fresh()
            mm = dict(fresh["model_management"])
            security = dict(mm["security_state"])
            revoked = list(security["revoked_credential_versions"])
            if any(r["credential_version"] == credential_version for r in revoked):
                raise ValueError(f"凭据版本已撤销: {credential_version}")
            revoked.append({"credential_version": credential_version, "revoked_at": _utc_now_iso()})
            security["revoked_credential_versions"] = revoked
            security["security_revision"] = int(security["security_revision"]) + 1
            mm["security_state"] = security
            return cfg.ConfigHandler.compare_and_save_model_management(
                expected_revision, mm, operation_id, update_security_state=True
            )

        return _outcome_from_cas(await ThreadPoolManager().run_async(TaskType.IO, _revoke), expected_revision)

    # ------------------------------------------------------------------
    # 迁移切换（S6 D4，§12.2：begin / seal / activate 全经 compare_and_save CAS）
    # ------------------------------------------------------------------

    async def begin_migration(
        self,
        expected_revision: int,
        operation_id: str,
        owner_session_id: str,
        lease_seconds: int = 300,
    ) -> ConfigurationSaveOutcome:
        """开启 / 续租迁移屏障（§12.2）：pending → draining。

        - 无 transition → draining（transition_id 新建、epoch=1、租约 now+lease_seconds）；
        - 同 owner 租约未过期 → 续租（epoch+1、新租期）；
        - 异 owner 租约未过期 → :class:`MigrationTransitionError`（不抢占）；
        - 租约过期 → 接管（沿用原 transition_id，epoch 单调递增，R22）；
        - 已 sealed → 幂等：屏障数据不变，经 CAS 重写回执（sealed 不可回退）。
        """

        def _begin() -> ModelManagementSaveResult:
            fresh = cfg.ConfigHandler.read_model_management_fresh()
            mm = dict(fresh["model_management"])
            lease_expires_at = (dt.datetime.now(dt.UTC) + dt.timedelta(seconds=lease_seconds)).isoformat()
            raw = mm.get("migration_transition")
            if raw is not None:
                transition = dict(raw)
                if transition.get("phase") == "sealed":
                    # sealed 不可回退：幂等重写屏障数据不变（epoch / transition_id 保持）
                    mm["migration_transition"] = transition
                    return cfg.ConfigHandler.compare_and_save_model_management(
                        expected_revision, mm, operation_id, update_security_state=False, update_migration_state=True
                    )
                if (
                    not _lease_expired(transition.get("lease_expires_at"))
                    and transition.get("owner_session_id") != owner_session_id
                ):
                    raise MigrationTransitionError(
                        f"迁移屏障租约未过期且 owner 不同，拒绝接管: owner={transition.get('owner_session_id')}"
                    )
                # 同 owner 续租 / 租约过期接管：epoch 单调递增，transition_id 保持稳定恢复锚
                transition["transition_epoch"] = int(transition["transition_epoch"]) + 1
                transition["owner_session_id"] = owner_session_id
                transition["lease_expires_at"] = lease_expires_at
                mm["migration_transition"] = transition
            else:
                mm["migration_transition"] = {
                    "transition_id": str(uuid.uuid4()),
                    "owner_session_id": owner_session_id,
                    "transition_epoch": 1,
                    "phase": "draining",
                    "lease_expires_at": lease_expires_at,
                    "source_fingerprint": _payload_fingerprint(mm),
                    "legacy_seals": [],
                }
            return cfg.ConfigHandler.compare_and_save_model_management(
                expected_revision, mm, operation_id, update_security_state=False, update_migration_state=True
            )

        return _outcome_from_cas(await ThreadPoolManager().run_async(TaskType.IO, _begin), expected_revision)

    async def seal_legacy(
        self,
        expected_revision: int,
        operation_id: str,
        month_seals: Mapping[str, str],
        owner_session_id: str,
    ) -> ConfigurationSaveOutcome:
        """封存 legacy 累计并翻 sealed（§12.2）：phase 必须 draining 或 sealed 幂等。

        仅限屏障 owner（``begin_migration`` 的 ``owner_session_id``）调用：异 owner
        须先经 begin 接管（租约过期）后才能封口，防止其他进程绕过排水收敛直接封存
        （MM-10「两进程旧批次收敛」）。sealed 状态追加跨月封口时 owner 须与屏障
        记录一致（接管后 owner 已变更，以现值为准）。

        调用顺序契约：调用方先逐月 ``AIUsageDao.seal_legacy_month`` 取 seal_id，
        再一次性本方法全量封口（缺月不自动补，须显式提供）。month_seals 非空、
        month 形如 YYYY-MM、seal_id 非空；与既有 legacy_seals 合并：同月同 seal_id
        幂等（保留原 sealed_at）、同月异 seal_id 拒绝（:class:`MigrationTransitionError`）、
        新月追加（跨月分别封口）；成功后 phase=sealed。
        """
        if not month_seals:
            raise ValueError("month_seals 不能为空（缺月不自动补，须显式全量封口）")
        for month, seal_id in month_seals.items():
            if not _MONTH_SEAL_PATTERN.match(month):
                raise ValueError(f"month 形如 YYYY-MM: {month!r}")
            if not seal_id:
                raise ValueError(f"seal_id 不能为空: month={month!r}")

        def _seal() -> ModelManagementSaveResult:
            fresh = cfg.ConfigHandler.read_model_management_fresh()
            mm = dict(fresh["model_management"])
            raw = mm.get("migration_transition")
            if raw is None:
                raise MigrationTransitionError("未取得迁移屏障（migration_transition 为空），禁止 seal_legacy")
            transition = dict(raw)
            if transition.get("owner_session_id") != owner_session_id:
                raise MigrationTransitionError(
                    f"seal_legacy 仅限屏障 owner 调用: owner={transition.get('owner_session_id')!r}"
                )
            existing: dict[str, dict[str, Any]] = {s["month"]: dict(s) for s in transition.get("legacy_seals", [])}
            for month, seal_id in month_seals.items():
                prior = existing.get(month)
                if prior is not None and prior.get("legacy_seal_id") != seal_id:
                    raise MigrationTransitionError(
                        f"月 {month} 已存在不同 seal_id 封口: {prior.get('legacy_seal_id')!r} != {seal_id!r}"
                    )
                if prior is None:
                    existing[month] = {"month": month, "legacy_seal_id": seal_id, "sealed_at": _utc_now_iso()}
            transition["legacy_seals"] = list(existing.values())
            transition["phase"] = "sealed"
            mm["migration_transition"] = transition
            return cfg.ConfigHandler.compare_and_save_model_management(
                expected_revision, mm, operation_id, update_security_state=False, update_migration_state=True
            )

        return _outcome_from_cas(await ThreadPoolManager().run_async(TaskType.IO, _seal), expected_revision)

    async def activate(
        self,
        expected_revision: int,
        operation_id: str,
    ) -> ConfigurationSaveOutcome:
        """激活新链（§12.2）：phase 必须 sealed → migration_state="active"。

        transition 保留作审计；CAS not_committed（及回执恢复 commit_unknown）时
        磁盘保持 sealed，由调用方重试（幂等：凭 transition_id / seal 锚重试，
        不重开旧写者、不双记）。
        """

        def _activate() -> ModelManagementSaveResult:
            fresh = cfg.ConfigHandler.read_model_management_fresh()
            mm = dict(fresh["model_management"])
            raw = mm.get("migration_transition")
            if raw is None:
                raise MigrationTransitionError("未取得迁移屏障（migration_transition 为空），禁止 activate")
            if raw.get("phase") != "sealed":
                raise MigrationTransitionError(f"phase={raw.get('phase')!r} 未封存，禁止 activate")
            mm["migration_state"] = "active"
            return cfg.ConfigHandler.compare_and_save_model_management(
                expected_revision, mm, operation_id, update_security_state=False, update_migration_state=True
            )

        return _outcome_from_cas(await ThreadPoolManager().run_async(TaskType.IO, _activate), expected_revision)

    # ------------------------------------------------------------------
    # 逐次许可（§4.3 authorize_attempt：线程池内配置文件锁重读，锁内无网络 IO）
    # ------------------------------------------------------------------

    async def authorize_attempt(
        self,
        connection_id: str,
        target_fingerprint: str,
        purpose: str,
        scope_version: int,
        credential_version: str | None,
        attempt_id: str,
        *,
        requires_egress_grant: bool = True,
    ) -> AttemptPermit:
        """逐次安全许可（MM-01/MM-02）：新鲜读取 security_state，撤销 / 缺失即拒绝。

        许可取得是尝试开始的线性化点；安全状态读取 / 校验失败禁止发放许可，
        不用旧缓存、默认值或冻结快照放行。

        F06：凭据版本撤销校验绑定**实际执行对象**——``credential_version`` 为
        快照冻结连接的版本（出站解析 ``credential_ref`` 即由该版本派生），而非
        磁盘当前连接版本：快照与磁盘可能已轮换（v1 快照 → 切 v2 → 撤销 v1），
        检查磁盘现值会让已撤销版本继续出站。``None`` 表示无版本化凭据
        （ref 缺失或 ``env:`` 形态），无撤销校验对象。

        F01：``requires_egress_grant`` 按真实执行位置声明——``litellm_chat``
        （网络出站）必须存在目标级 grant 严格匹配；设备内本地模型（``gguf_*``，
        不产生外发）免 grant 匹配，但仍校验连接存在与凭据撤销。默认 ``True``
        （安全默认：未声明本地的调用一律要求外发授权）。空用途 / 外发缺目标
        一律拒绝（授权服务拒绝空身份是正确安全行为，不因本地路径放宽）。
        """

        def _authorize() -> AttemptPermit:
            fresh = cfg.ConfigHandler.read_model_management_fresh()
            mm = fresh["model_management"]
            security = mm["security_state"]

            connection = next((c for c in mm["connections"] if c["connection_id"] == connection_id), None)
            if connection is None:
                raise PermissionError(f"连接不存在: {connection_id}")

            # 凭据版本撤销立即阻止新许可（MM-02）：校验对象为实际执行版本（F06）
            if credential_version is not None:
                if any(r["credential_version"] == credential_version for r in security["revoked_credential_versions"]):
                    raise PermissionError(f"凭据版本已撤销，拒绝发放许可: {credential_version}")

            # F01：空身份拒绝（用途是许可的审计身份，外发目标缺失即无法匹配授权）
            if not purpose:
                raise PermissionError("用途缺失（purpose 为空），拒绝发放许可")

            if requires_egress_grant:
                if not target_fingerprint:
                    raise PermissionError(f"外发目标缺失（target_fingerprint 为空），拒绝发放许可: {connection_id}")

                # 目标级 grant：同连接、同目标、同用途、当前 scope_version 且未撤销（MM-01）
                matched = any(
                    g["connection_id"] == connection_id
                    and g["target_fingerprint"] == target_fingerprint
                    and g["purpose"] == purpose
                    and g["scope_version"] == scope_version
                    and g["revoked_at"] is None
                    for g in security["grants"]
                )
                if not matched:
                    raise PermissionError(
                        f"无匹配外发授权（connection={connection_id}, purpose={purpose}, "
                        f"scope_version={scope_version}），拒绝发放许可"
                    )

            return AttemptPermit(
                attempt_id=attempt_id,
                connection_id=connection_id,
                security_revision=security["security_revision"],
                granted_at=_utc_now_iso(),
                credential_version=credential_version,
            )

        return await ThreadPoolManager().run_async(TaskType.IO, _authorize)

    # ------------------------------------------------------------------
    # 凭据版本编排（§5.2：版本标识独立 UUID；同 operation_id 复用不重复创建）
    # ------------------------------------------------------------------

    async def prepare_credential_version(self, operation_id: str, api_key: str) -> tuple[str, str]:
        """写入版本化 keyring 项并返回 (credential_ref, credential_version)。

        同 operation_id 的安全重试复用该新引用，不重复创建版本（MM-12）；
        配置原子提交成功后才生效，not_committed 时调用方保留旧引用即可。

        T4 并发幂等：检查（lease）-创建-登记（lease）整体处于 per-operation_id
        锁内——查与写之间唯一的 await 点（keyring 线程池写入）期间，同 id 第二个
        并发调用在锁上等待而非并行穿透检查，进锁后命中 lease 返回同一版本，
        不重复创建 keyring 项。创建失败时 lease 未登记且锁随 ``async with`` 释放，
        无占位残留，后续同 id 调用可重新创建。锁经 ``get_loop_local`` 绑定当前
        事件循环（R11），锁内不触碰文件锁 / RWLock，不与既有锁顺序（F10）交互。
        """
        async with self._get_prepare_lock(operation_id):
            existing = self._credential_leases.get(operation_id)
            if existing is not None:
                return existing

            def _write() -> tuple[str, str]:
                version = str(uuid.uuid4())
                item = f"model-connection-{version}"
                cfg.keyring.set_password(cfg.KEYRING_SERVICE_NAME, item, api_key)
                return f"keyring:{item}", version

            ref_version = await ThreadPoolManager().run_async(TaskType.IO, _write)
            self._credential_leases[operation_id] = ref_version
            return ref_version

    def _get_prepare_lock(self, operation_id: str) -> asyncio.Lock:
        """按 operation_id 取绑定当前事件循环的准备锁（R11：loop-local，
        不得作为类属性跨循环复用；同 task_manager 组锁的 dict 惰性创建先例）。"""
        locks: dict[str, asyncio.Lock] = get_loop_local("model_config_prepare_locks", dict)
        lock = locks.get(operation_id)
        if lock is None:
            lock = asyncio.Lock()
            locks[operation_id] = lock
        return lock

    # ------------------------------------------------------------------
    # 单次测试上下文（§4.4，MM-09：不写 grants、不改 config_revision、不落盘）
    # ------------------------------------------------------------------

    async def begin_synthetic_test(
        self,
        draft_connection: dict[str, Any],
        task_id: str,
        credential_version: str | None = None,
    ) -> SyntheticTestContext:
        """冻结当前草稿建立不可复用的单次测试上下文。

        新连接在草稿创建时取得 UUID，不要求已保存或已分配场景；配置归属使用
        新鲜读取的 config_revision；不提交连接、场景或业务授权。

        S6 D7 pending 费用型测试屏障：pending 状态下云端（``litellm_chat`` = 费用型）
        单次测试须存在未过期 draining 迁移屏障，防止绕过迁移流程继续产生云端费用；
        ``gguf_*``（本地，无云费用）与 active（新链接管）不受限。屏障判定在同一
        新鲜读取点完成，不加第二次读。
        """

        def _begin() -> SyntheticTestContext:
            fresh = cfg.ConfigHandler.read_model_management_fresh()
            mm = fresh["model_management"]
            if draft_connection.get("adapter_kind") == "litellm_chat" and mm["migration_state"] == "pending":
                transition = mm.get("migration_transition")
                if (
                    not isinstance(transition, dict)
                    or transition.get("phase") != "draining"
                    or _lease_expired(transition.get("lease_expires_at"))
                ):
                    raise MigrationTransitionError(
                        "pending 状态下的云端（费用型）单次测试须先暂停旧 AI 任务并取得"
                        "未过期的 draining 迁移屏障（先 begin_migration）"
                    )
            conn_id = draft_connection.get("connection_id") or str(uuid.uuid4())
            return SyntheticTestContext(
                test_operation_id=str(uuid.uuid4()),
                invocation_id=str(uuid.uuid4()),
                host_identity=socket.gethostname(),
                draft_connection_id=conn_id,
                draft_connection_revision=int(draft_connection.get("connection_revision", 1)),
                draft_fingerprint=_payload_fingerprint(draft_connection),
                task_id=task_id,
                credential_version=credential_version,
                config_revision=mm["config_revision"],
            )

        return await ThreadPoolManager().run_async(TaskType.IO, _begin)

    def create_synthetic_test_consent(
        self,
        ctx: SyntheticTestContext,
        target_fingerprint: str,
        deadline: float,
        scope_version: int = 1,
    ) -> SyntheticTestConsent:
        """用户明确点击后生成单次操作许可：仅存于该测试上下文，不写 security_state。"""
        return SyntheticTestConsent(
            test_operation_id=ctx.test_operation_id,
            host_identity=ctx.host_identity,
            target_fingerprint=target_fingerprint,
            draft_fingerprint=ctx.draft_fingerprint,
            scope_version=scope_version,
            deadline=deadline,
        )

    # ------------------------------------------------------------------
    # 迁移预览（§12.1：只读映射建议，needs_review 状态，不写配置）
    # ------------------------------------------------------------------

    async def build_migration_preview(self) -> dict[str, Any]:
        """旧配置 → 新对象 / 路由映射预览（S1 侧最小集）。

        七键情绪映射（§12.1）：news_sentiment_enabled / model_path / head_path /
        tokenizer_path / n_threads / max_length / timeout_s → 一个 gguf_sentiment
        连接，缺项沿 scorer 当前默认。预览只读，不提交、不改 migration_state。
        """

        def _preview() -> dict[str, Any]:
            loaded = cfg.ConfigHandler.load_config()
            main = {
                "adapter_kind": "litellm_chat",
                "provider": str(loaded.get("llm_provider", "") or ""),
                "model": str(loaded.get("llm_model", "") or ""),
                "api_base": loaded.get("llm_base_url"),
                "billing_mode_pending": True,  # 每连接要求确认 usage_only / estimate_cost
            }
            sentiment = {
                "adapter_kind": "gguf_sentiment",
                "enabled": bool(loaded.get("news_sentiment_enabled", False)),
                "model_path": loaded.get("news_sentiment_model_path"),
                "head_path": loaded.get("news_sentiment_head_path"),
                "tokenizer_path": loaded.get("news_sentiment_tokenizer_path"),
                "n_threads": loaded.get("news_sentiment_n_threads"),
                "max_length": loaded.get("news_sentiment_max_length"),
                "timeout_s": loaded.get("news_sentiment_timeout_s"),
                "billing_mode_pending": True,
            }
            return {
                "main_connection": main,
                "sentiment_connection": sentiment,
                "fallback_connections": [],  # 全局 fallbacks 分别建连接保持旧序（S5 UI 确认）
                "needs_review": True,  # 迁移草稿状态：billing / 授权需逐项确认，非第三种 mode
            }

        return await ThreadPoolManager().run_async(TaskType.IO, _preview)
