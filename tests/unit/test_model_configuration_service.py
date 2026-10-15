"""ModelConfigurationService：CAS 转译 / 回执恢复 / 授权与撤销 / 单次测试 / 迁移预览。

S1「配置与凭据」服务层判定回归（reviews/模型统一.md §5.1、§4.3、§4.4、§12.1；
ADR-0014 §4 三态契约；MM-01 / MM-02(S1 侧) / MM-09(S1 侧) / MM-12(S1 侧)）。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from services.ai_service.model_management_types import (
    ConfigurationSaveOutcome,
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


def _base_connection(conn_id: str, api_base: str) -> dict:
    return {
        "connection_id": conn_id,
        "name": "官方 API",
        "adapter_kind": "litellm_chat",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "api_base": api_base,
        "billing": {"mode": "usage_only", "pricing_unit": "llm_tokens"},
    }


def _load_mm() -> dict:
    return dict(cfg_mod.ConfigHandler.read_model_management_fresh()["model_management"])


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
        """MM-09(S1)：单次测试上下文不写 grants、不改 config_revision。"""
        draft_conn = _base_connection(CONN_B, "http://127.0.0.1:8000/v1")
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
        draft_conn = _base_connection(CONN_B, "http://127.0.0.1:8000/v1")
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
