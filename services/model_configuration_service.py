"""统一模型管理配置应用服务（S1，reviews/模型统一.md §4.3 / §5 / §12.1）。

职责边界（§6.1）：
- 配置保存 / 回执恢复 / 迁移预览 / 外发授权命令 / authorize_attempt / 单次测试上下文；
- 安全命令只经 ConfigHandler 本域接口（compare_and_save_model_management /
  read_model_management_fresh），不新建授权文件或进程内授权单例；
- 同步 IO（文件锁 / keyring）经 ThreadPoolManager 提交（R16）；CancelledError 始终
  传播（R2）；本服务采用 DI，不注册单例（§6.1）。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import socket
import uuid
from typing import Any

from services.ai_service.model_management_types import (
    AttemptPermit,
    ConfigurationSaveOutcome,
    ModelConfigurationConflictError,
    ModelManagementSnapshot,
    SyntheticTestConsent,
    SyntheticTestContext,
)
from utils import config_handler as cfg
from utils.config_models import ModelManagementSaveResult
from utils.thread_pool import TaskType, ThreadPoolManager

_PURPOSES = ("news_annotation", "stock_analysis", "news_risk", "synthetic_test")


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _payload_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def _outcome_from_cas(result: ModelManagementSaveResult, expected_revision: int) -> ConfigurationSaveOutcome:
    """ConfigHandler 层四态转译服务层三态（R1：utils 不导入 services，转译在本层）。"""
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
        """新鲜读取并冻结已提交非敏感配置快照（§5.1：不经 load_config 缓存命中路径）。"""

        def _read() -> ModelManagementSnapshot:
            from types import MappingProxyType

            fresh = cfg.ConfigHandler.read_model_management_fresh()
            mm = fresh["model_management"]
            return ModelManagementSnapshot(
                snapshot_id=str(uuid.uuid4()),
                config_revision=mm["config_revision"],
                migration_state=mm["migration_state"],
                connections=tuple(MappingProxyType(c) for c in mm["connections"]),
                scenario_routes=tuple(MappingProxyType(r) for r in mm["scenario_routes"]),
                usage_policy=MappingProxyType(mm["usage_policy"]),
                security_revision=mm["security_state"]["security_revision"],
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
        """保存草稿：CAS 提交；安全子域以磁盘现值为准（§5.3 草稿不能改安全状态）。"""

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
    # 逐次许可（§4.3 authorize_attempt：线程池内配置文件锁重读，锁内无网络 IO）
    # ------------------------------------------------------------------

    async def authorize_attempt(
        self,
        connection_id: str,
        target_fingerprint: str,
        purpose: str,
        scope_version: int,
        attempt_id: str,
    ) -> AttemptPermit:
        """逐次安全许可（MM-01/MM-02）：新鲜读取 security_state，撤销 / 缺失即拒绝。

        许可取得是尝试开始的线性化点；安全状态读取 / 校验失败禁止发放许可，
        不用旧缓存、默认值或冻结快照放行。
        """

        def _authorize() -> AttemptPermit:
            fresh = cfg.ConfigHandler.read_model_management_fresh()
            mm = fresh["model_management"]
            security = mm["security_state"]

            connection = next((c for c in mm["connections"] if c["connection_id"] == connection_id), None)
            if connection is None:
                raise PermissionError(f"连接不存在: {connection_id}")

            # 凭据版本撤销立即阻止新许可（MM-02）
            cred_version = connection.get("credential_version")
            if cred_version is not None:
                if any(r["credential_version"] == cred_version for r in security["revoked_credential_versions"]):
                    raise PermissionError(f"凭据版本已撤销，拒绝发放许可: {cred_version}")

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
            )

        return await ThreadPoolManager().run_async(TaskType.IO, _authorize)

    # ------------------------------------------------------------------
    # 凭据版本编排（§5.2：版本标识独立 UUID；同 operation_id 复用不重复创建）
    # ------------------------------------------------------------------

    async def prepare_credential_version(self, operation_id: str, api_key: str) -> tuple[str, str]:
        """写入版本化 keyring 项并返回 (credential_ref, credential_version)。

        同 operation_id 的安全重试复用该新引用，不重复创建版本（MM-12）；
        配置原子提交成功后才生效，not_committed 时调用方保留旧引用即可。
        """
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
        """

        def _begin() -> SyntheticTestContext:
            fresh = cfg.ConfigHandler.read_model_management_fresh()
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
                config_revision=fresh["model_management"]["config_revision"],
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
