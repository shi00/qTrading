"""ModelConfigurationService：CAS 转译 / 回执恢复 / 授权与撤销 / 单次测试 / 迁移预览。

S1「配置与凭据」服务层判定回归（reviews/模型统一.md §5.1、§4.3、§4.4、§12.1；
ADR-0014 §4 三态契约；MM-01 / MM-02(S1 侧) / MM-09(S1 侧) / MM-12(S1 侧)）。
S6「切换交付」批 A：迁移三方法 begin/seal/activate（s6_plan.md §1 D4）与
D7 pending 费用型测试屏障。
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from types import MappingProxyType
from unittest import mock

import pytest

from core.model_management_types import (
    ConfigurationSaveOutcome,
    MigrationTransitionError,
    ModelConfigurationConflictError,
)
from services.model_configuration_service import ModelConfigurationService
import utils.config_handler as cfg_mod

CONN_A = "11111111-1111-4111-8111-111111111111"
CONN_B = "22222222-2222-4222-8222-222222222222"


@pytest.fixture()
def isolated_config(tmp_path):
    """为单个测试提供独立配置文件与干净缓存（与 test_model_management_config 同语义）。"""
    config_file = tmp_path / "user_settings.json"
    with (
        mock.patch.object(cfg_mod, "CONFIG_FILE", str(config_file)),
        mock.patch.object(cfg_mod.ConfigHandler, "_config_cache", None),
    ):
        yield config_file


@pytest.fixture()
def service(isolated_config):
    """isolated_config 来自 test_model_management_config 同名 fixture 语义：独立配置文件。"""
    cfg_mod.ConfigHandler.ensure_defaults()
    return ModelConfigurationService()


def _read_raw(config_file: Path) -> dict:
    return json.loads(config_file.read_text(encoding="utf-8"))


def _write_raw(config_file: Path, data: dict) -> None:
    config_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _base_connection(conn_id: str, api_base: str, adapter_kind: str = "litellm_chat") -> dict:
    return {
        "connection_id": conn_id,
        "name": "官方 API",
        "adapter_kind": adapter_kind,
        "provider": "deepseek",
        "model": "deepseek-chat",
        "api_base": api_base,
        "billing": {"mode": "usage_only", "pricing_unit": "llm_tokens"},
    }


def _load_mm() -> dict:
    return dict(cfg_mod.ConfigHandler.read_model_management_fresh()["model_management"])


def _fresh_transition() -> dict:
    transition = _load_mm().get("migration_transition")
    assert transition is not None
    return dict(transition)


def _expire_lease_on_disk(isolated_config: Path) -> None:
    """把磁盘上的迁移租约改为过期（S6 租约过期接管 / D7 过期屏障用例）。"""
    raw = _read_raw(isolated_config)
    raw["model_management"]["migration_transition"]["lease_expires_at"] = "2000-01-01T00:00:00+00:00"
    _write_raw(isolated_config, raw)
    cfg_mod.ConfigHandler._clear_cache()


def _set_active_state_on_disk(isolated_config: Path) -> None:
    """把磁盘 migration_state 改为 active（D7 active 不受限用例）。"""
    raw = _read_raw(isolated_config)
    raw["model_management"]["migration_state"] = "active"
    _write_raw(isolated_config, raw)
    cfg_mod.ConfigHandler._clear_cache()


class TestSaveDraft:
    @pytest.mark.asyncio
    async def test_save_draft_commits_and_increments_revision(self, service, isolated_config):
        mm = _load_mm()
        expected = mm["config_revision"]
        mm["connections"] = [_base_connection(CONN_A, "https://api.deepseek.com")]

        outcome = await service.save_draft(mm, expected, "op-d1")

        assert isinstance(outcome, ConfigurationSaveOutcome)
        assert outcome.status == "committed"
        assert outcome.committed_revision == expected + 1
        raw = _read_raw(isolated_config)
        assert raw["model_management"]["connections"][0]["connection_id"] == CONN_A

    @pytest.mark.asyncio
    async def test_save_draft_conflict_raises_conflict_error(self, service):
        mm = _load_mm()
        with pytest.raises(ModelConfigurationConflictError) as excinfo:
            await service.save_draft(mm, 999, "op-c1")
        assert excinfo.value.current_revision == mm["config_revision"]

    @pytest.mark.asyncio
    async def test_save_draft_invalid_payload_not_committed(self, service):
        mm = _load_mm()
        mm["migration_state"] = "bogus"
        outcome = await service.save_draft(mm, mm["config_revision"], "op-bad")
        assert outcome.status == "not_committed"

    @pytest.mark.asyncio
    async def test_save_draft_preserves_disk_security_state(self, service):
        """save_draft 不修改安全子域：payload 安全键被磁盘现值替换（§5.3）。"""
        mm = _load_mm()
        expected = mm["config_revision"]
        mm["connections"] = [_base_connection(CONN_A, "https://api.deepseek.com")]
        mm["security_state"] = {"security_revision": 99, "grants": [], "revoked_credential_versions": []}

        outcome = await service.save_draft(mm, expected, "op-sec-guard")

        assert outcome.status == "committed"
        fresh = _load_mm()
        assert fresh["security_state"]["security_revision"] == 0


class TestResolveOutcome:
    @pytest.mark.asyncio
    async def test_resolve_confirms_committed_by_receipt(self, service, isolated_config):
        """MM-12：os.replace 后缓存更新失败 / 回执核对——按相同锁顺序重读磁盘凭回执确认。"""
        mm = _load_mm()
        expected = mm["config_revision"]

        outcome = await service.save_draft(mm, expected, "op-r1")
        assert outcome.status == "committed"

        # 模拟「缓存更新失败」：清掉进程缓存后仅凭磁盘回执裁决
        cfg_mod.ConfigHandler._clear_cache()
        resolved = await service.resolve_save_outcome("op-r1")

        assert resolved.status == "committed"
        assert resolved.committed_revision == expected + 1
        # 确认后不重写配置：revision 保持 N+1
        assert _load_mm()["config_revision"] == expected + 1

    @pytest.mark.asyncio
    async def test_resolve_unknown_when_receipt_overwritten(self, service):
        """回执被后续提交覆写：不能证明提交结果 → commit_unknown，保守不重试。"""
        mm = _load_mm()
        expected = mm["config_revision"]
        await service.save_draft(mm, expected, "op-lost")

        # 另一次提交覆写回执
        mm2 = _load_mm()
        await service.save_draft(mm2, mm2["config_revision"], "op-newer")

        resolved = await service.resolve_save_outcome("op-lost")
        assert resolved.status == "commit_unknown"

    @pytest.mark.asyncio
    async def test_resolve_unknown_when_receipt_missing(self, service):
        resolved = await service.resolve_save_outcome("never-seen")
        assert resolved.status == "commit_unknown"


class TestCredentialVersioning:
    @pytest.mark.asyncio
    async def test_same_operation_id_reuses_version(self, service):
        """MM-12：同 operation_id 安全重试复用新引用，不重复创建版本。"""
        ref1, ver1 = await service.prepare_credential_version("op-k1", "sk-test-123")
        ref2, ver2 = await service.prepare_credential_version("op-k1", "sk-test-123")
        assert ver1 == ver2
        assert ref1 == ref2

    @pytest.mark.asyncio
    async def test_different_operation_id_gets_new_version(self, service):
        ref1, ver1 = await service.prepare_credential_version("op-a", "sk-1")
        ref2, ver2 = await service.prepare_credential_version("op-b", "sk-2")
        assert ver1 != ver2

    @pytest.mark.asyncio
    async def test_credential_written_to_keyring(self, service):
        import keyring

        ref, ver = await service.prepare_credential_version("op-kr", "sk-secret")
        # ref 形如 keyring:<service>:<item>；keyring 中可读回
        _, item = ref.split(":", 1)
        assert keyring.get_password(cfg_mod.KEYRING_SERVICE_NAME, item) == "sk-secret"


class TestEgressGrantAndAuthorize:
    @pytest.mark.asyncio
    async def test_grant_then_authorize_attempt_permits(self, service):
        """MM-01：目标 + 用途 grant 后 authorize_attempt 发放许可。"""
        mm = _load_mm()
        expected = mm["config_revision"]
        mm["connections"] = [_base_connection(CONN_A, "https://api.deepseek.com")]
        await service.save_draft(mm, expected, "op-g0")

        outcome = await service.grant_egress(
            connection_id=CONN_A,
            target_fingerprint="https://api.deepseek.com:443",
            purpose="stock_analysis",
            scope_version=1,
            expected_revision=_load_mm()["config_revision"],
            operation_id="op-g1",
        )
        assert outcome.status == "committed"

        fresh = _load_mm()
        assert fresh["security_state"]["security_revision"] == 1
        assert len(fresh["security_state"]["grants"]) == 1

        permit = await service.authorize_attempt(
            connection_id=CONN_A,
            target_fingerprint="https://api.deepseek.com:443",
            purpose="stock_analysis",
            scope_version=1,
            attempt_id="att-1",
        )
        assert permit.security_revision == 1

    @pytest.mark.asyncio
    async def test_authorize_without_matching_grant_rejects(self, service):
        """MM-01：无匹配 grant 零出站——服务层拒绝且不发起任何网络 IO。"""
        mm = _load_mm()
        mm["connections"] = [_base_connection(CONN_A, "https://api.deepseek.com")]
        await service.save_draft(mm, mm["config_revision"], "op-n0")

        with pytest.raises(PermissionError, match="无匹配外发授权"):
            await service.authorize_attempt(
                connection_id=CONN_A,
                target_fingerprint="https://other.example.com:443",
                purpose="stock_analysis",
                scope_version=1,
                attempt_id="att-2",
            )

    @pytest.mark.asyncio
    async def test_authorize_purpose_mismatch_rejects(self, service):
        mm = _load_mm()
        expected = mm["config_revision"]
        mm["connections"] = [_base_connection(CONN_A, "https://api.deepseek.com")]
        await service.save_draft(mm, expected, "op-p0")
        await service.grant_egress(
            connection_id=CONN_A,
            target_fingerprint="https://api.deepseek.com:443",
            purpose="stock_analysis",
            scope_version=1,
            expected_revision=_load_mm()["config_revision"],
            operation_id="op-p1",
        )

        with pytest.raises(PermissionError, match="无匹配外发授权"):
            await service.authorize_attempt(
                connection_id=CONN_A,
                target_fingerprint="https://api.deepseek.com:443",
                purpose="news_annotation",
                scope_version=1,
                attempt_id="att-3",
            )

    @pytest.mark.asyncio
    async def test_revoke_egress_blocks_new_permits(self, service):
        """MM-02：撤销提交后不再发新许可；security_revision 单调递增。"""
        mm = _load_mm()
        expected = mm["config_revision"]
        mm["connections"] = [_base_connection(CONN_A, "https://api.deepseek.com")]
        await service.save_draft(mm, expected, "op-v0")
        await service.grant_egress(
            connection_id=CONN_A,
            target_fingerprint="https://api.deepseek.com:443",
            purpose="stock_analysis",
            scope_version=1,
            expected_revision=_load_mm()["config_revision"],
            operation_id="op-v1",
        )

        # grant_id 由服务生成，先读真实 grant_id 再撤销
        grant_id = _load_mm()["security_state"]["grants"][0]["grant_id"]
        outcome = await service.revoke_egress(
            grant_id=grant_id,
            expected_revision=_load_mm()["config_revision"],
            operation_id="op-v2",
        )
        assert outcome.status == "committed"

        fresh = _load_mm()
        assert fresh["security_state"]["security_revision"] == 2
        assert fresh["security_state"]["grants"][0]["revoked_at"] is not None

        with pytest.raises(PermissionError, match="无匹配外发授权"):
            await service.authorize_attempt(
                connection_id=CONN_A,
                target_fingerprint="https://api.deepseek.com:443",
                purpose="stock_analysis",
                scope_version=1,
                attempt_id="att-4",
            )

    @pytest.mark.asyncio
    async def test_authorize_fails_closed_when_revocation_key_dropped(self, service, isolated_config):
        """F08 端到端：真实撤销后仅删磁盘撤销列表键（grants 存活），许可必须失败关闭。

        缺陷形态：Pydantic 把缺失的 revoked_credential_versions 补成空列表 →
        撤销事实被消除 + grant 仍匹配 → 许可被错误放行。
        """
        mm = _load_mm()
        expected = mm["config_revision"]
        mm["connections"] = [_base_connection(CONN_A, "https://api.deepseek.com")]
        await service.save_draft(mm, expected, "op-f08-0")
        outcome = await service.grant_egress(
            connection_id=CONN_A,
            target_fingerprint="https://api.deepseek.com:443",
            purpose="stock_analysis",
            scope_version=1,
            expected_revision=_load_mm()["config_revision"],
            operation_id="op-f08-1",
        )
        assert outcome.status == "committed"
        revoke_outcome = await service.revoke_credential(
            credential_version="v-old",
            expected_revision=_load_mm()["config_revision"],
            operation_id="op-f08-2",
        )
        assert revoke_outcome.status == "committed"

        # 模拟「合法 JSON 的部分字段丢失」：仅删除撤销列表键，grants 原样保留
        raw = _read_raw(isolated_config)
        del raw["model_management"]["security_state"]["revoked_credential_versions"]
        _write_raw(isolated_config, raw)

        from utils.config.storage import ConfigFreshReadError

        with pytest.raises(ConfigFreshReadError, match="security subdomain corruption"):
            await service.authorize_attempt(
                connection_id=CONN_A,
                target_fingerprint="https://api.deepseek.com:443",
                purpose="stock_analysis",
                scope_version=1,
                attempt_id="att-f08",
            )

    @pytest.mark.asyncio
    async def test_revoke_credential_blocks_new_permits(self, service):
        """MM-02：版本撤销立即阻止后续新许可。"""
        mm = _load_mm()
        expected = mm["config_revision"]
        conn = _base_connection(CONN_A, "https://api.deepseek.com")
        conn["credential_version"] = "cred-v1"
        mm["connections"] = [conn]
        await service.save_draft(mm, expected, "op-rc0")
        await service.grant_egress(
            connection_id=CONN_A,
            target_fingerprint="https://api.deepseek.com:443",
            purpose="stock_analysis",
            scope_version=1,
            expected_revision=_load_mm()["config_revision"],
            operation_id="op-rc1",
        )

        outcome = await service.revoke_credential(
            credential_version="cred-v1",
            expected_revision=_load_mm()["config_revision"],
            operation_id="op-rc2",
        )
        assert outcome.status == "committed"

        fresh = _load_mm()
        assert fresh["security_state"]["revoked_credential_versions"][0]["credential_version"] == "cred-v1"

        with pytest.raises(PermissionError, match="凭据版本已撤销"):
            await service.authorize_attempt(
                connection_id=CONN_A,
                target_fingerprint="https://api.deepseek.com:443",
                purpose="stock_analysis",
                scope_version=1,
                attempt_id="att-5",
            )


class TestSyntheticTestContext:
    @pytest.mark.asyncio
    async def test_begin_synthetic_test_freezes_draft_without_persist(self, service):
        """MM-09(S1)：单次测试上下文不写 grants、不改 config_revision（本地 gguf 无屏障要求）。"""
        draft_conn = _base_connection(CONN_B, "http://127.0.0.1:8000/v1", adapter_kind="gguf_chat")
        revision_before = _load_mm()["config_revision"]
        grants_before = _load_mm()["security_state"]["grants"]

        ctx = await service.begin_synthetic_test(draft_connection=draft_conn, task_id="news_annotation")

        assert ctx.test_operation_id
        assert ctx.draft_connection_id == CONN_B
        assert ctx.purpose == "synthetic_test"
        # 未落盘：config_revision 与 grants 均未变化
        assert _load_mm()["config_revision"] == revision_before
        assert _load_mm()["security_state"]["grants"] == grants_before

    @pytest.mark.asyncio
    async def test_synthetic_consent_not_valid_for_business_entry(self, service):
        """测试许可仅受控测试入口接受；业务入口（stock_analysis 用途）不认。"""
        draft_conn = _base_connection(CONN_B, "http://127.0.0.1:8000/v1", adapter_kind="gguf_chat")
        ctx = await service.begin_synthetic_test(draft_connection=draft_conn, task_id="news_annotation")
        consent = service.create_synthetic_test_consent(
            ctx, target_fingerprint="http://127.0.0.1:8000:8000", deadline=1_000.0
        )
        assert consent.purpose == "synthetic_test"

        with pytest.raises(PermissionError, match="连接不存在"):
            await service.authorize_attempt(
                connection_id=CONN_B,
                target_fingerprint="http://127.0.0.1:8000:8000",
                purpose="stock_analysis",
                scope_version=1,
                attempt_id="att-6",
            )


class TestMigrationPreview:
    @pytest.mark.asyncio
    async def test_seven_sentiment_keys_map_to_gguf_sentiment_connection(self, service, isolated_config):
        """七键情绪映射：旧 news_sentiment_* 七键 → 一个 gguf_sentiment 连接预览。"""
        legacy = {
            "news_sentiment_enabled": True,
            "news_sentiment_model_path": "D:/models/finbert.gguf",
            "news_sentiment_head_path": "D:/models/head.npz",
            "news_sentiment_tokenizer_path": "D:/models/tokenizer.json",
            "news_sentiment_n_threads": 4,
            "news_sentiment_max_length": 256,
            "news_sentiment_timeout_s": 30,
            "llm_provider": "deepseek",
            "llm_model": "deepseek-chat",
        }
        with mock.patch.object(cfg_mod.ConfigHandler, "load_config", return_value=legacy):
            preview = await service.build_migration_preview()

        sentiment = preview["sentiment_connection"]
        assert sentiment["adapter_kind"] == "gguf_sentiment"
        assert sentiment["model_path"] == "D:/models/finbert.gguf"
        assert sentiment["head_path"] == "D:/models/head.npz"
        assert sentiment["tokenizer_path"] == "D:/models/tokenizer.json"
        assert sentiment["n_threads"] == 4
        assert sentiment["max_length"] == 256
        assert sentiment["timeout_s"] == 30
        assert preview["needs_review"] is True

    @pytest.mark.asyncio
    async def test_preview_maps_main_connection(self, service):
        legacy = {"llm_provider": "deepseek", "llm_model": "deepseek-chat"}
        with mock.patch.object(cfg_mod.ConfigHandler, "load_config", return_value=legacy):
            preview = await service.build_migration_preview()

        assert preview["main_connection"]["adapter_kind"] == "litellm_chat"
        assert preview["main_connection"]["provider"] == "deepseek"
        assert preview["main_connection"]["billing_mode_pending"] is True


class TestPendingNotActive:
    @pytest.mark.asyncio
    async def test_snapshot_keeps_pending_state(self, service):
        """pending 不切生产：快照保留 migration_state=pending，不自动转 active。"""
        snapshot = await service.get_snapshot()
        assert snapshot.migration_state == "pending"


class TestMigrationBegin:
    @pytest.mark.asyncio
    async def test_first_begin_creates_draining_barrier(self, service):
        """D4 begin：无 transition → draining（epoch=1、租约未来、id / fingerprint 就位）。"""
        mm = _load_mm()
        outcome = await service.begin_migration(
            expected_revision=mm["config_revision"], operation_id="op-m1", owner_session_id="owner-a"
        )
        assert outcome.status == "committed"
        transition = _fresh_transition()
        assert transition["phase"] == "draining"
        assert transition["transition_epoch"] == 1
        assert transition["owner_session_id"] == "owner-a"
        assert transition["transition_id"]
        assert transition["source_fingerprint"]
        assert transition["legacy_seals"] == []
        assert dt.datetime.fromisoformat(transition["lease_expires_at"]) > dt.datetime.now(dt.UTC)

    @pytest.mark.asyncio
    async def test_same_owner_renew_bumps_epoch(self, service):
        """D4 begin：同 owner 租约未过期 → 续租 epoch+1、transition_id 不变。"""
        mm = _load_mm()
        await service.begin_migration(mm["config_revision"], "op-m2", "owner-a")
        first = _fresh_transition()
        outcome = await service.begin_migration(_load_mm()["config_revision"], "op-m3", "owner-a")
        assert outcome.status == "committed"
        second = _fresh_transition()
        assert second["transition_epoch"] == 2
        assert second["transition_id"] == first["transition_id"]
        assert second["owner_session_id"] == "owner-a"

    @pytest.mark.asyncio
    async def test_other_owner_with_active_lease_rejected(self, service):
        """D4 begin：异 owner 租约未过期 → MigrationTransitionError（不抢占，屏障数据不变）。"""
        mm = _load_mm()
        await service.begin_migration(mm["config_revision"], "op-m4", "owner-a")
        with pytest.raises(MigrationTransitionError, match="拒绝接管"):
            await service.begin_migration(_load_mm()["config_revision"], "op-m5", "owner-b")
        transition = _fresh_transition()
        assert transition["owner_session_id"] == "owner-a"
        assert transition["transition_epoch"] == 1

    @pytest.mark.asyncio
    async def test_expired_lease_takeover_keeps_transition_id(self, service, isolated_config):
        """D4 begin：租约过期 → 异 owner 接管，epoch 单调递增且沿用原 transition_id。"""
        mm = _load_mm()
        await service.begin_migration(mm["config_revision"], "op-m6", "owner-a")
        original_id = _fresh_transition()["transition_id"]
        _expire_lease_on_disk(isolated_config)
        outcome = await service.begin_migration(_load_mm()["config_revision"], "op-m7", "owner-b")
        assert outcome.status == "committed"
        transition = _fresh_transition()
        assert transition["phase"] == "draining"
        assert transition["owner_session_id"] == "owner-b"
        assert transition["transition_epoch"] == 2
        assert transition["transition_id"] == original_id
        assert dt.datetime.fromisoformat(transition["lease_expires_at"]) > dt.datetime.now(dt.UTC)

    @pytest.mark.asyncio
    async def test_sealed_begin_idempotent(self, service):
        """D4 begin：已 sealed → 幂等返回，屏障数据不变（sealed 不可回退）。"""
        mm = _load_mm()
        await service.begin_migration(mm["config_revision"], "op-m8", "owner-a")
        await service.seal_legacy(_load_mm()["config_revision"], "op-m9", {"2026-10": "seal-x"}, "owner-a")
        sealed = _fresh_transition()
        outcome = await service.begin_migration(_load_mm()["config_revision"], "op-m10", "owner-b")
        assert outcome.status == "committed"
        after = _fresh_transition()
        assert after["phase"] == "sealed"
        assert after["transition_id"] == sealed["transition_id"]
        assert after["transition_epoch"] == sealed["transition_epoch"]
        assert after["legacy_seals"] == sealed["legacy_seals"]


class TestMigrationSeal:
    @pytest.mark.asyncio
    async def test_seal_without_transition_rejected(self, service):
        """D4 seal：未取得屏障 → MigrationTransitionError。"""
        with pytest.raises(MigrationTransitionError, match="未取得迁移屏障"):
            await service.seal_legacy(_load_mm()["config_revision"], "op-s0", {"2026-10": "s"}, "owner-a")

    @pytest.mark.asyncio
    async def test_seal_empty_month_seals_value_error(self, service):
        """D4 seal：month_seals 非空（缺月不自动补）。"""
        with pytest.raises(ValueError, match="不能为空"):
            await service.seal_legacy(_load_mm()["config_revision"], "op-s1", {}, "owner-a")

    @pytest.mark.asyncio
    async def test_seal_bad_month_format_value_error(self, service):
        """D4 seal：month 形如 YYYY-MM。"""
        with pytest.raises(ValueError, match="YYYY-MM"):
            await service.seal_legacy(_load_mm()["config_revision"], "op-s2", {"202610": "s"}, "owner-a")

    @pytest.mark.asyncio
    async def test_seal_empty_seal_id_value_error(self, service):
        """D4 seal：seal_id 非空。"""
        with pytest.raises(ValueError, match="seal_id 不能为空"):
            await service.seal_legacy(_load_mm()["config_revision"], "op-s3", {"2026-10": ""}, "owner-a")

    @pytest.mark.asyncio
    async def test_seal_non_owner_rejected(self, service):
        """D4 seal（检视修复）：异 owner 未接管屏障直接封存 → MigrationTransitionError（MM-10 两进程收敛）。"""
        mm = _load_mm()
        await service.begin_migration(mm["config_revision"], "op-s15", "owner-a")
        with pytest.raises(MigrationTransitionError, match="仅限屏障 owner"):
            await service.seal_legacy(_load_mm()["config_revision"], "op-s16", {"2026-10": "seal-a"}, "owner-b")

    @pytest.mark.asyncio
    async def test_seal_draining_to_sealed_merges(self, service):
        """D4 seal：draining + month_seals → sealed，legacy_seals 合并写入。"""
        mm = _load_mm()
        await service.begin_migration(mm["config_revision"], "op-s4", "owner-a")
        outcome = await service.seal_legacy(_load_mm()["config_revision"], "op-s5", {"2026-10": "seal-a"}, "owner-a")
        assert outcome.status == "committed"
        transition = _fresh_transition()
        assert transition["phase"] == "sealed"
        assert transition["legacy_seals"] == [
            {"month": "2026-10", "legacy_seal_id": "seal-a", "sealed_at": transition["legacy_seals"][0]["sealed_at"]}
        ]
        assert transition["legacy_seals"][0]["sealed_at"]

    @pytest.mark.asyncio
    async def test_seal_same_seal_id_idempotent_keeps_sealed_at(self, service):
        """D4 seal：同月同 seal_id 幂等（保留原 sealed_at），phase 保持 sealed。"""
        mm = _load_mm()
        await service.begin_migration(mm["config_revision"], "op-s6", "owner-a")
        await service.seal_legacy(_load_mm()["config_revision"], "op-s7", {"2026-10": "seal-a"}, "owner-a")
        first = _fresh_transition()["legacy_seals"][0]
        outcome = await service.seal_legacy(_load_mm()["config_revision"], "op-s8", {"2026-10": "seal-a"}, "owner-a")
        assert outcome.status == "committed"
        assert _fresh_transition()["legacy_seals"] == [first]

    @pytest.mark.asyncio
    async def test_seal_conflicting_seal_id_rejected(self, service):
        """D4 seal：同月异 seal_id 拒绝（MigrationTransitionError），磁盘保持原封口。"""
        mm = _load_mm()
        await service.begin_migration(mm["config_revision"], "op-s9", "owner-a")
        await service.seal_legacy(_load_mm()["config_revision"], "op-s10", {"2026-10": "seal-a"}, "owner-a")
        with pytest.raises(MigrationTransitionError, match="不同 seal_id"):
            await service.seal_legacy(_load_mm()["config_revision"], "op-s11", {"2026-10": "seal-b"}, "owner-a")
        transition = _fresh_transition()
        assert transition["phase"] == "sealed"
        assert transition["legacy_seals"][0]["legacy_seal_id"] == "seal-a"

    @pytest.mark.asyncio
    async def test_seal_append_new_month_after_sealed(self, service):
        """Q4 跨月封口：sealed 后新月按同规则追加，phase 保持 sealed。"""
        mm = _load_mm()
        await service.begin_migration(mm["config_revision"], "op-s12", "owner-a")
        await service.seal_legacy(_load_mm()["config_revision"], "op-s13", {"2026-10": "seal-a"}, "owner-a")
        outcome = await service.seal_legacy(_load_mm()["config_revision"], "op-s14", {"2026-11": "seal-b"}, "owner-a")
        assert outcome.status == "committed"
        transition = _fresh_transition()
        assert transition["phase"] == "sealed"
        assert {s["month"]: s["legacy_seal_id"] for s in transition["legacy_seals"]} == {
            "2026-10": "seal-a",
            "2026-11": "seal-b",
        }


class TestMigrationActivate:
    @pytest.mark.asyncio
    async def test_activate_without_transition_rejected(self, service):
        """D4 activate：未取得屏障 → MigrationTransitionError。"""
        with pytest.raises(MigrationTransitionError, match="未取得迁移屏障"):
            await service.activate(_load_mm()["config_revision"], "op-a0")

    @pytest.mark.asyncio
    async def test_activate_requires_sealed(self, service):
        """D4 activate：draining 未封存 → 拒绝，migration_state 保持 pending。"""
        mm = _load_mm()
        await service.begin_migration(mm["config_revision"], "op-a1", "owner-a")
        with pytest.raises(MigrationTransitionError, match="未封存"):
            await service.activate(_load_mm()["config_revision"], "op-a2")
        assert _load_mm()["migration_state"] == "pending"

    @pytest.mark.asyncio
    async def test_activate_flips_active_and_keeps_transition(self, service):
        """D4 activate：sealed → active；transition 保留作审计。"""
        mm = _load_mm()
        await service.begin_migration(mm["config_revision"], "op-a3", "owner-a")
        await service.seal_legacy(_load_mm()["config_revision"], "op-a4", {"2026-10": "seal-a"}, "owner-a")
        sealed = _fresh_transition()
        outcome = await service.activate(_load_mm()["config_revision"], "op-a5")
        assert outcome.status == "committed"
        fresh = _load_mm()
        assert fresh["migration_state"] == "active"
        assert fresh["migration_transition"] == sealed

    @pytest.mark.asyncio
    async def test_activate_revision_conflict_raises_conflict_error(self, service):
        """CAS revision 冲突：磁盘保持 sealed，ModelConfigurationConflictError 携带 current_revision。"""
        mm = _load_mm()
        await service.begin_migration(mm["config_revision"], "op-a6", "owner-a")
        await service.seal_legacy(_load_mm()["config_revision"], "op-a7", {"2026-10": "seal-a"}, "owner-a")
        current = _load_mm()["config_revision"]
        with pytest.raises(ModelConfigurationConflictError) as excinfo:
            await service.activate(current - 1, "op-a8")
        assert excinfo.value.current_revision == current
        assert _load_mm()["migration_state"] == "pending"
        assert _fresh_transition()["phase"] == "sealed"


class TestSnapshotMigrationTransition:
    @pytest.mark.asyncio
    async def test_snapshot_exposes_readonly_transition(self, service):
        """D2：get_snapshot 把 transition 转只读视图；未开启迁移时为 None。"""
        snapshot = await service.get_snapshot()
        assert snapshot.migration_transition is None
        mm = _load_mm()
        await service.begin_migration(mm["config_revision"], "op-n1", "owner-a")
        snapshot = await service.get_snapshot()
        view = snapshot.migration_transition
        assert isinstance(view, MappingProxyType)
        assert view["phase"] == "draining"
        assert view["transition_epoch"] == 1


class TestPendingFeeTestBarrier:
    """D7 pending 费用型测试屏障：pending + 云端（litellm_chat）须未过期 draining 屏障。"""

    @pytest.mark.asyncio
    async def test_pending_litellm_without_barrier_rejected(self, service):
        """pending + litellm_chat + 无屏障 → MigrationTransitionError（费用型受限）。"""
        draft = _base_connection(CONN_B, "https://api.deepseek.com")
        with pytest.raises(MigrationTransitionError, match="draining 迁移屏障"):
            await service.begin_synthetic_test(draft_connection=draft, task_id="news_annotation")

    @pytest.mark.asyncio
    async def test_pending_litellm_with_active_barrier_allowed(self, service):
        """pending + litellm_chat + 未过期 draining 屏障 → 放行。"""
        mm = _load_mm()
        await service.begin_migration(mm["config_revision"], "op-d1", "owner-a")
        draft = _base_connection(CONN_B, "https://api.deepseek.com")
        ctx = await service.begin_synthetic_test(draft_connection=draft, task_id="news_annotation")
        assert ctx.draft_connection_id == CONN_B
        assert ctx.purpose == "synthetic_test"

    @pytest.mark.asyncio
    async def test_pending_litellm_with_expired_barrier_rejected(self, service, isolated_config):
        """pending + litellm_chat + 过期 draining 屏障 → 拒绝（须未过期才放行）。"""
        mm = _load_mm()
        await service.begin_migration(mm["config_revision"], "op-d2", "owner-a")
        _expire_lease_on_disk(isolated_config)
        draft = _base_connection(CONN_B, "https://api.deepseek.com")
        with pytest.raises(MigrationTransitionError, match="draining 迁移屏障"):
            await service.begin_synthetic_test(draft_connection=draft, task_id="news_annotation")

    @pytest.mark.asyncio
    async def test_pending_gguf_not_restricted(self, service):
        """pending + gguf（本地无云费用）→ 不受限。"""
        draft = _base_connection(CONN_B, "http://127.0.0.1:8000/v1", adapter_kind="gguf_chat")
        ctx = await service.begin_synthetic_test(draft_connection=draft, task_id="news_annotation")
        assert ctx.draft_connection_id == CONN_B

    @pytest.mark.asyncio
    async def test_active_litellm_not_restricted(self, service, isolated_config):
        """active（新链接管）+ litellm_chat → 不受限。"""
        _set_active_state_on_disk(isolated_config)
        draft = _base_connection(CONN_B, "https://api.deepseek.com")
        ctx = await service.begin_synthetic_test(draft_connection=draft, task_id="news_annotation")
        assert ctx.draft_connection_id == CONN_B
