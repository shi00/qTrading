"""ModelManagement 配置域：CAS / 跨进程文件锁 / 新鲜读取 / 安全子域保护。

S1「配置与凭据」ConfigHandler 层判定回归（reviews/模型统一.md §5.1、§13.3、MM-03 / MM-12）：
- DEFAULT_CONFIG / ensure_defaults 不得把 model_management 当未知键删除；
- compare_and_save_model_management：revision CAS + operation_id 回执 + 安全子域保护；
- read_model_management_fresh：锁内重读磁盘，损坏显式报错，禁止静默落默认；
- 跨进程：A 缓存 N → B 保存 N+1 → A 新鲜读取 N+1；A 保存无关设置保留 B 的新域。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal
from unittest import mock

import pytest

import utils.config_handler as cfg_mod
from utils.config_models import (
    MODEL_MANAGEMENT_SCHEMA_VERSION,
    AppConfig,
    ModelManagementConfig,
    SecurityStateCorruptionError,
    validate_security_subdomain_integrity,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def isolated_config(tmp_path):
    """为单个测试提供独立配置文件与干净缓存。"""
    config_file = tmp_path / "user_settings.json"
    with (
        mock.patch.object(cfg_mod, "CONFIG_FILE", str(config_file)),
        mock.patch.object(cfg_mod.ConfigHandler, "_config_cache", None),
    ):
        yield config_file


def _write_raw_config(config_file: Path, data: dict) -> None:
    config_file.write_text(json.dumps(data), encoding="utf-8")


def _read_raw_config(config_file: Path) -> dict:
    return json.loads(config_file.read_text(encoding="utf-8"))


def _run_worker(config_file: Path, code: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["ASTOCK_CONFIG_FILE"] = str(config_file)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


class TestDefaultDomainRegistration:
    def test_app_config_contains_model_management_domain(self):
        cfg = AppConfig()
        mm = cfg.model_management
        assert mm.schema_version == 1
        assert mm.config_revision >= 1
        assert mm.migration_state == "pending"
        assert mm.connections == []
        assert mm.security_state.security_revision == 0
        assert mm.last_config_commit is None

    def test_default_config_dump_includes_domain(self):
        dumped = AppConfig().model_dump()
        assert "model_management" in dumped
        assert dumped["model_management"]["migration_state"] == "pending"

    def test_legacy_config_backfills_domain(self, isolated_config):
        _write_raw_config(
            isolated_config,
            {"llm_provider": "deepseek", "llm_model": "deepseek-v4-flash"},
        )
        loaded = cfg_mod.ConfigHandler.load_config()
        assert "model_management" in loaded
        assert loaded["model_management"]["migration_state"] == "pending"

    def test_ensure_defaults_preserves_domain_and_prunes_unknown(self, isolated_config):
        _write_raw_config(
            isolated_config,
            {
                "llm_provider": "deepseek",
                "model_management": {
                    "schema_version": 1,
                    "config_revision": 5,
                    "migration_state": "pending",
                    "connections": [],
                    "scenario_routes": [],
                    "usage_policy": {},
                    "security_state": {"security_revision": 3, "grants": [], "revoked_credential_versions": []},
                    "last_config_commit": None,
                    "migration_transition": None,
                },
                "unknown_legacy_key": 1,
            },
        )
        cfg_mod.ConfigHandler.ensure_defaults()
        raw = _read_raw_config(isolated_config)
        assert raw["model_management"]["config_revision"] == 5
        assert raw["model_management"]["security_state"]["security_revision"] == 3
        assert "unknown_legacy_key" not in raw

    def test_schema_allows_same_name_different_endpoints(self):
        """同名不同端点是两个独立连接（身份为 connection_id，非名称）。"""
        base = AppConfig().model_management.model_dump()
        base["connections"] = [
            {
                "connection_id": "11111111-1111-4111-8111-111111111111",
                "name": "官方 API",
                "enabled": True,
                "adapter_kind": "litellm_chat",
                "provider": "deepseek",
                "model": "deepseek-chat",
                "api_base": "https://api.deepseek.com",
                "credential_ref": None,
                "credential_version": None,
                "adapter_options": {},
                "capabilities": {},
                "context_window": None,
                "concurrency_limit": 1,
                "billing": {"mode": "usage_only", "pricing_unit": "llm_tokens", "price_source": None},
                "connection_revision": 1,
                "last_validation": None,
            },
            {
                "connection_id": "22222222-2222-4222-8222-222222222222",
                "name": "官方 API",
                "enabled": True,
                "adapter_kind": "litellm_chat",
                "provider": "deepseek",
                "model": "deepseek-chat",
                "api_base": "https://api.deepseek.com/v2",
                "credential_ref": None,
                "credential_version": None,
                "adapter_options": {},
                "capabilities": {},
                "context_window": None,
                "concurrency_limit": 1,
                "billing": {"mode": "usage_only", "pricing_unit": "llm_tokens", "price_source": None},
                "connection_revision": 1,
                "last_validation": None,
            },
        ]
        AppConfig.model_validate({**AppConfig().model_dump(), "model_management": base})
        assert len(base["connections"]) == 2


class TestCompareAndSave:
    def _payload(self, config_file: Path) -> dict:
        loaded = cfg_mod.ConfigHandler.load_config()
        return dict(loaded["model_management"])

    def test_commit_increments_revision_and_writes_receipt(self, isolated_config):
        cfg_mod.ConfigHandler.ensure_defaults()
        payload = self._payload(isolated_config)
        expected = payload["config_revision"]

        result = cfg_mod.ConfigHandler.compare_and_save_model_management(expected, payload, "op-1")

        assert result.status == "committed"
        assert result.committed_revision == expected + 1
        assert result.last_config_commit is not None
        assert result.last_config_commit["operation_id"] == "op-1"

        raw = _read_raw_config(isolated_config)
        assert raw["model_management"]["config_revision"] == expected + 1
        assert raw["model_management"]["last_config_commit"]["operation_id"] == "op-1"
        # 回执随同一次 os.replace 落盘且不含秘密载体
        assert "payload_fingerprint" in raw["model_management"]["last_config_commit"]

    def test_revision_conflict_keeps_disk_and_cache(self, isolated_config):
        cfg_mod.ConfigHandler.ensure_defaults()
        payload = self._payload(isolated_config)
        before = _read_raw_config(isolated_config)

        result = cfg_mod.ConfigHandler.compare_and_save_model_management(999, payload, "op-x")

        assert result.status == "revision_conflict"
        assert result.current_revision == before["model_management"]["config_revision"]
        assert _read_raw_config(isolated_config) == before

    def test_save_draft_path_cannot_modify_security_subdomain(self, isolated_config):
        """save_draft（update_security_state=False）payload 携带的安全子域被磁盘现值替换。"""
        cfg_mod.ConfigHandler.ensure_defaults()
        payload = self._payload(isolated_config)
        payload["security_state"] = {
            "security_revision": 42,
            "grants": [
                {
                    "grant_id": "fake",
                    "connection_id": "c",
                    "target_fingerprint": "t",
                    "purpose": "stock_analysis",
                    "scope_version": 1,
                    "confirmed_at": "2026-01-01T00:00:00+00:00",
                    "revoked_at": None,
                }
            ],
            "revoked_credential_versions": [],
        }

        result = cfg_mod.ConfigHandler.compare_and_save_model_management(
            payload["config_revision"], payload, "op-2", update_security_state=False
        )

        assert result.status == "committed"
        raw = _read_raw_config(isolated_config)
        assert raw["model_management"]["security_state"]["security_revision"] == 0
        assert raw["model_management"]["security_state"]["grants"] == []

    def test_security_command_path_updates_security_state(self, isolated_config):
        cfg_mod.ConfigHandler.ensure_defaults()
        payload = self._payload(isolated_config)
        payload["connections"] = [
            {
                "connection_id": "11111111-1111-4111-8111-111111111111",
                "name": "官方 API",
                "adapter_kind": "litellm_chat",
                "provider": "deepseek",
                "model": "deepseek-chat",
                "billing": {"mode": "usage_only", "pricing_unit": "llm_tokens"},
            }
        ]
        payload["security_state"] = dict(payload["security_state"])
        payload["security_state"]["security_revision"] = 1
        payload["security_state"]["grants"] = [
            {
                "grant_id": "g-1",
                "connection_id": "11111111-1111-4111-8111-111111111111",
                "target_fingerprint": "https://api.deepseek.com:443",
                "purpose": "stock_analysis",
                "scope_version": 1,
                "confirmed_at": "2026-01-01T00:00:00+00:00",
                "revoked_at": None,
            }
        ]

        result = cfg_mod.ConfigHandler.compare_and_save_model_management(
            payload["config_revision"], payload, "op-sec", update_security_state=True
        )

        assert result.status == "committed"
        raw = _read_raw_config(isolated_config)
        assert raw["model_management"]["security_state"]["security_revision"] == 1
        assert len(raw["model_management"]["security_state"]["grants"]) == 1

    def test_invalid_payload_rejected_not_committed(self, isolated_config):
        cfg_mod.ConfigHandler.ensure_defaults()
        payload = self._payload(isolated_config)
        payload["migration_state"] = "bogus_state"

        # 草稿路径：注入的非法迁移状态被 F07 剥离（回填磁盘现值），连接保存不受牵连
        result = cfg_mod.ConfigHandler.compare_and_save_model_management(payload["config_revision"], payload, "op-bad")
        assert result.status == "committed"
        raw = _read_raw_config(isolated_config)
        assert raw["model_management"]["migration_state"] == "pending"

        # 迁移命令路径（update_migration_state=True）：payload 迁移子域原样校验，非法值被 schema 拒绝
        current = _read_raw_config(isolated_config)["model_management"]["config_revision"]
        result = cfg_mod.ConfigHandler.compare_and_save_model_management(
            current, payload, "op-bad-cmd", update_migration_state=True
        )
        assert result.status == "not_committed"
        assert result.committed_revision is None


class TestFreshRead:
    def test_fresh_read_returns_disk_state_and_refreshes_cache(self, isolated_config):
        cfg_mod.ConfigHandler.ensure_defaults()
        stale = cfg_mod.ConfigHandler.load_config()
        assert stale["model_management"]["config_revision"] == 1

        # 绕过缓存直接改磁盘（模拟另一进程写入）
        raw = _read_raw_config(isolated_config)
        raw["model_management"]["config_revision"] = 7
        _write_raw_config(isolated_config, raw)

        fresh = cfg_mod.ConfigHandler.read_model_management_fresh()
        assert fresh["model_management"]["config_revision"] == 7
        # 缓存已刷新：普通 load_config 也读到新值
        reloaded = cfg_mod.ConfigHandler.load_config()
        assert reloaded["model_management"]["config_revision"] == 7

    def test_fresh_read_corrupt_disk_raises_explicit_error(self, isolated_config):
        cfg_mod.ConfigHandler.ensure_defaults()
        isolated_config.write_text("{not-valid-json", encoding="utf-8")

        from utils.config.storage import ConfigFreshReadError

        with pytest.raises(ConfigFreshReadError, match="config file unreadable"):
            cfg_mod.ConfigHandler.read_model_management_fresh()


class TestCrossProcess:
    def test_a_cached_n_b_saves_n_plus_1_a_fresh_reads_new(self, isolated_config):
        """跨进程验收：A 缓存 N，B 保存 N+1，A 创建新运行读 N+1（MM-03）。"""
        cfg_mod.ConfigHandler.ensure_defaults()
        cached = cfg_mod.ConfigHandler.load_config()
        assert cached["model_management"]["config_revision"] == 1

        worker = (
            "import json\n"
            "from utils.config_handler import ConfigHandler\n"
            "ConfigHandler.ensure_defaults()\n"
            "cur = ConfigHandler.read_model_management_fresh()\n"
            "mm = dict(cur['model_management'])\n"
            "expected = mm['config_revision']\n"
            "mm['connections'] = [{\n"
            "    'connection_id': 'conn-b', 'name': 'B', 'adapter_kind': 'litellm_chat',\n"
            "    'provider': 'deepseek', 'model': 'deepseek-chat',\n"
            "    'billing': {'mode': 'usage_only', 'pricing_unit': 'llm_tokens'},\n"
            "}]\n"
            "mm['scenario_routes'] = [\n"
            "    {**r, 'candidates': ['conn-b']} if r['scenario_id'] == 'news_annotation' else r\n"
            "    for r in mm['scenario_routes']\n"
            "]\n"
            "result = ConfigHandler.compare_and_save_model_management(expected, mm, 'op-B')\n"
            "print('STATUS=' + result.status)\n"
            "print('REV=' + str(result.committed_revision))\n"
        )
        proc = _run_worker(isolated_config, worker)
        assert proc.returncode == 0, proc.stderr
        assert "STATUS=committed" in proc.stdout

        fresh = cfg_mod.ConfigHandler.read_model_management_fresh()
        assert fresh["model_management"]["config_revision"] == 2
        assert fresh["model_management"]["scenario_routes"][0]["candidates"] == ["conn-b"]

    def test_save_unrelated_key_keeps_other_process_model_management(self, isolated_config):
        """A 保存无关设置不得用旧缓存覆盖 B 写入的新域。"""
        cfg_mod.ConfigHandler.ensure_defaults()

        worker = (
            "from utils.config_handler import ConfigHandler\n"
            "ConfigHandler.ensure_defaults()\n"
            "cur = ConfigHandler.read_model_management_fresh()\n"
            "mm = dict(cur['model_management'])\n"
            "expected = mm['config_revision']\n"
            "mm['connections'] = [{\n"
            "    'connection_id': 'conn-b', 'name': 'B', 'adapter_kind': 'litellm_chat',\n"
            "    'provider': 'deepseek', 'model': 'deepseek-chat',\n"
            "    'billing': {'mode': 'usage_only', 'pricing_unit': 'llm_tokens'},\n"
            "}]\n"
            "mm['scenario_routes'] = [\n"
            "    {**r, 'candidates': ['conn-b']} if r['scenario_id'] == 'news_annotation' else r\n"
            "    for r in mm['scenario_routes']\n"
            "]\n"
            "result = ConfigHandler.compare_and_save_model_management(expected, mm, 'op-B')\n"
            "print('STATUS=' + result.status)\n"
        )
        proc = _run_worker(isolated_config, worker)
        assert proc.returncode == 0, proc.stderr

        cfg_mod.ConfigHandler.save_config({"log_level": "DEBUG"})
        raw = _read_raw_config(isolated_config)
        assert raw["log_level"] == "DEBUG"
        assert raw["model_management"]["config_revision"] == 2
        assert raw["model_management"]["scenario_routes"][0]["candidates"] == ["conn-b"]

    def test_file_lock_blocks_concurrent_writer(self, tmp_path):
        """子进程持锁期间，另一写者超时失败——跨进程文件锁互斥前提。"""
        from filelock import FileLock, Timeout

        lock_path = str(tmp_path / "cfg.lock")
        sentinel = tmp_path / "held.marker"
        holder_code = (
            "import time\n"
            "from pathlib import Path\n"
            "from filelock import FileLock\n"
            f"lock = FileLock({lock_path!r}, timeout=5)\n"
            "lock.acquire()\n"
            f"Path({str(sentinel)!r}).write_text('1')\n"
            "time.sleep(3)\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", holder_code],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            # 轮询哨兵文件，确定性等待子进程完成持锁（子进程持锁窗口 3s 足够父进程尝试）
            deadline = time.monotonic() + 10
            while not sentinel.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert sentinel.exists(), "holder failed to acquire lock in time"
            with pytest.raises(Timeout, match="could not be acquired"):
                FileLock(lock_path, timeout=1).acquire()
        finally:
            proc.wait(timeout=15)


class TestSaveConfigCorruptionSemantics:
    def test_save_config_aborts_on_corrupt_disk_instead_of_overwrite(self, isolated_config):
        """磁盘损坏时普通保存中止（不再以空基底整份覆盖丢失他键）。"""
        cfg_mod.ConfigHandler.ensure_defaults()
        cfg_mod.ConfigHandler.load_config()
        isolated_config.write_text("{corrupt", encoding="utf-8")

        assert cfg_mod.ConfigHandler.save_config({"log_level": "DEBUG"}) is False
        assert isolated_config.read_text(encoding="utf-8") == "{corrupt"


class TestSecuritySubdomainIntegrity:
    """F08：区分「合法初始化 / 旧版本迁移」与「已初始化安全子域的结构损坏」。

    Pydantic 对缺失的 security_state 子字段补默认值（零修订 / 空 grants / 空撤销），
    会把「合法 JSON 的部分安全字段丢失」洗白为合法空安全态；入口必须在 schema
    校验前按磁盘原始形态判定，损坏即抛 :class:`SecurityStateCorruptionError`。
    """

    @pytest.mark.parametrize(
        "raw_mm",
        [
            pytest.param(None, id="domain-missing-initialization"),
            pytest.param({"schema_version": 0}, id="legacy-schema-migration"),
            pytest.param({"schema_version": 0, "security_state": {}}, id="legacy-schema-with-partial-state"),
        ],
    )
    def test_passes_for_initialization_and_legacy_migration(self, raw_mm):
        validate_security_subdomain_integrity(raw_mm)

    def test_passes_for_complete_security_state(self):
        raw_mm = {
            "schema_version": 1,
            "security_state": {"security_revision": 3, "grants": [], "revoked_credential_versions": []},
        }
        validate_security_subdomain_integrity(raw_mm)

    def test_schema_version_literal_matches_constant(self):
        """Literal[1] 与 MODEL_MANAGEMENT_SCHEMA_VERSION 常量一致性守护（无法直接引用，见模型注释）。"""
        assert MODEL_MANAGEMENT_SCHEMA_VERSION == 1
        assert ModelManagementConfig.model_fields["schema_version"].annotation == Literal[1]

    @pytest.mark.parametrize(
        "raw_mm",
        [
            pytest.param("corrupted", id="domain-not-dict"),
            pytest.param({"config_revision": 1}, id="schema-version-missing"),
            pytest.param({"schema_version": "1"}, id="schema-version-string"),
            pytest.param({"schema_version": True}, id="schema-version-bool"),
            pytest.param({"schema_version": 1}, id="security-state-missing"),
            pytest.param({"schema_version": 1, "security_state": None}, id="security-state-null"),
            pytest.param(
                {"schema_version": 1, "security_state": {"grants": []}},
                id="grants-only-missing-revision-and-revocations",
            ),
            pytest.param(
                {"schema_version": 1, "security_state": {"security_revision": 1, "grants": []}},
                id="missing-revoked-credential-versions",
            ),
            pytest.param(
                {"schema_version": 1, "security_state": {"security_revision": 1, "revoked_credential_versions": []}},
                id="missing-grants",
            ),
            pytest.param(
                {"schema_version": 1, "security_state": {"grants": [], "revoked_credential_versions": []}},
                id="missing-security-revision",
            ),
        ],
    )
    def test_raises_for_structural_corruption(self, raw_mm):
        with pytest.raises(SecurityStateCorruptionError) as excinfo:
            validate_security_subdomain_integrity(raw_mm)
        # 强断言：错误消息须携带结构损坏判定语义（区别于任意 ValueError）
        assert "结构损坏" in str(excinfo.value)


class TestFreshReadSecurityCorruption:
    """F08 回归：撤销后逐字段删除安全键，fresh read 必须失败关闭。"""

    @pytest.mark.parametrize(
        "drop_key",
        ["revoked_credential_versions", "grants", "security_revision", "security_state", "schema_version"],
    )
    def test_fresh_read_fails_closed_on_dropped_security_key(self, isolated_config, drop_key):
        cfg_mod.ConfigHandler.ensure_defaults()
        # 注入撤销事实（MM-02 语义的持久形态）并确认 fresh read 基线可见
        raw = _read_raw_config(isolated_config)
        raw["model_management"]["security_state"]["revoked_credential_versions"] = [
            {"credential_version": "v-old", "revoked_at": "2026-01-01T00:00:00+00:00"}
        ]
        _write_raw_config(isolated_config, raw)
        fresh = cfg_mod.ConfigHandler.read_model_management_fresh()
        assert fresh["model_management"]["security_state"]["revoked_credential_versions"][0]["credential_version"] == (
            "v-old"
        )

        # 逐字段删除安全键（模拟合法 JSON 的部分字段丢失，schema 层面仍可合法）
        raw = _read_raw_config(isolated_config)
        if drop_key == "security_state":
            del raw["model_management"]["security_state"]
        elif drop_key == "schema_version":
            del raw["model_management"]["schema_version"]
        else:
            del raw["model_management"]["security_state"][drop_key]
        _write_raw_config(isolated_config, raw)
        corrupted_disk = isolated_config.read_text(encoding="utf-8")

        from utils.config.storage import ConfigFreshReadError

        with pytest.raises(ConfigFreshReadError, match="security subdomain corruption"):
            cfg_mod.ConfigHandler.read_model_management_fresh()
        # 失败关闭：只读路径，磁盘损坏形态未被改写
        assert isolated_config.read_text(encoding="utf-8") == corrupted_disk

    def test_fresh_read_fails_closed_on_future_schema_version(self, isolated_config):
        """schema_version 高于当前版本：入口放行、Literal schema 拒绝，同样失败关闭。"""
        cfg_mod.ConfigHandler.ensure_defaults()
        raw = _read_raw_config(isolated_config)
        raw["model_management"]["schema_version"] = 2
        _write_raw_config(isolated_config, raw)

        from utils.config.storage import ConfigFreshReadError

        with pytest.raises(ConfigFreshReadError, match="config validation failed"):
            cfg_mod.ConfigHandler.read_model_management_fresh()

    def test_fresh_read_backfills_domain_when_missing(self, isolated_config):
        """合法初始化回归：磁盘无 model_management 域时 fresh read 照常补默认。"""
        _write_raw_config(isolated_config, {"llm_provider": "deepseek"})

        fresh = cfg_mod.ConfigHandler.read_model_management_fresh()

        security = fresh["model_management"]["security_state"]
        assert fresh["model_management"]["schema_version"] == 1
        assert security["security_revision"] == 0
        assert security["grants"] == []
        assert security["revoked_credential_versions"] == []


class TestCompareAndSaveSecurityCorruption:
    """F08：CAS 持久化边界——磁盘安全子域损坏时确定不提交，禁止洗白落盘。"""

    def test_cas_aborts_when_disk_security_subdomain_corrupted(self, isolated_config):
        cfg_mod.ConfigHandler.ensure_defaults()
        payload = dict(cfg_mod.ConfigHandler.load_config()["model_management"])
        expected = payload["config_revision"]
        raw = _read_raw_config(isolated_config)
        del raw["model_management"]["security_state"]["revoked_credential_versions"]
        _write_raw_config(isolated_config, raw)

        result = cfg_mod.ConfigHandler.compare_and_save_model_management(expected, payload, "op-f08")

        assert result.status == "not_committed"
        # 磁盘保持损坏原样：撤销键未被补出的空列表洗白
        raw_after = _read_raw_config(isolated_config)
        assert "revoked_credential_versions" not in raw_after["model_management"]["security_state"]

    def test_cas_initializes_domain_when_disk_missing(self, isolated_config):
        """首次初始化不阻断：磁盘无 model_management 域时 CAS 可正常创建完整域。"""
        _write_raw_config(isolated_config, {"llm_provider": "deepseek"})
        payload = AppConfig().model_dump()["model_management"]

        result = cfg_mod.ConfigHandler.compare_and_save_model_management(1, payload, "op-init")

        assert result.status == "committed"
        raw = _read_raw_config(isolated_config)
        assert raw["model_management"]["schema_version"] == 1
        assert raw["model_management"]["security_state"]["security_revision"] == 0


class TestDraftSecurityStrip:
    """F09：草稿路径无条件剥离输入安全子域——首次初始化只落明确空安全态。

    修复前保护条件要求磁盘已有 ``security_state``，无文件 / 旧配置首次提交时
    payload 自带 grants 直通 schema 落盘，普通草稿入口即可写入授权事实
    （schema 引用校验只约束引用有效性，不证明授权来自安全命令）。
    """

    _CONNECTION_ID = "11111111-1111-4111-8111-111111111111"

    def _payload_with_grant(self) -> dict:
        """合法连接 + 引用该连接的自带 grant（攻击形态：草稿自带授权）。"""
        payload = AppConfig().model_dump()["model_management"]
        payload["connections"] = [
            {
                "connection_id": self._CONNECTION_ID,
                "name": "官方 API",
                "adapter_kind": "litellm_chat",
                "provider": "deepseek",
                "model": "deepseek-chat",
                "billing": {"mode": "usage_only", "pricing_unit": "llm_tokens"},
            }
        ]
        payload["security_state"] = {
            "security_revision": 9,
            "grants": [
                {
                    "grant_id": "g-smuggled",
                    "connection_id": self._CONNECTION_ID,
                    "target_fingerprint": "https://api.deepseek.com:443",
                    "purpose": "stock_analysis",
                    "scope_version": 1,
                    "confirmed_at": "2026-01-01T00:00:00+00:00",
                    "revoked_at": None,
                }
            ],
            "revoked_credential_versions": [],
        }
        return payload

    def test_draft_on_missing_file_strips_payload_grants(self, isolated_config):
        """无文件首次提交：合法连接正常写入，自带 grant 不得写入。"""
        assert not isolated_config.exists()

        result = cfg_mod.ConfigHandler.compare_and_save_model_management(
            1, self._payload_with_grant(), "op-f09-init", update_security_state=False
        )

        assert result.status == "committed"
        raw = _read_raw_config(isolated_config)
        security = raw["model_management"]["security_state"]
        # 首次合法初始化落出明确空安全状态（三键齐备，R21 语义：空 ≠ 伪装合法值）
        assert security["security_revision"] == 0
        assert security["grants"] == []
        assert security["revoked_credential_versions"] == []
        # 连接本身不受剥离影响
        assert raw["model_management"]["connections"][0]["connection_id"] == self._CONNECTION_ID

    def test_draft_on_legacy_config_without_domain_strips_payload_grants(self, isolated_config):
        """旧配置无 model_management 域：草稿自带 grant 同样不得写入。"""
        _write_raw_config(isolated_config, {"llm_provider": "deepseek", "llm_model": "deepseek-v4-flash"})

        result = cfg_mod.ConfigHandler.compare_and_save_model_management(
            1, self._payload_with_grant(), "op-f09-legacy", update_security_state=False
        )

        assert result.status == "committed"
        raw = _read_raw_config(isolated_config)
        assert raw["model_management"]["security_state"]["security_revision"] == 0
        assert raw["model_management"]["security_state"]["grants"] == []
        # 旧配置无关键保留
        assert raw["llm_provider"] == "deepseek"

    def test_draft_on_legacy_domain_without_security_keys_rejected(self, isolated_config):
        """旧 mm 域缺安全键：F08 判结构损坏 → not_committed，磁盘保持原样。"""
        _write_raw_config(isolated_config, {"model_management": {"schema_version": 1, "config_revision": 1}})

        result = cfg_mod.ConfigHandler.compare_and_save_model_management(
            1, self._payload_with_grant(), "op-f09-corrupt", update_security_state=False
        )

        assert result.status == "not_committed"
        raw = _read_raw_config(isolated_config)
        # 磁盘保持损坏原样：grant 未被洗白落盘，revision 未推进
        assert "security_state" not in raw["model_management"]
        assert raw["model_management"]["config_revision"] == 1


_DRAINING_TRANSITION: dict = {
    "transition_id": "t-f07",
    "owner_session_id": "owner-1",
    "transition_epoch": 2,
    "phase": "draining",
    "lease_expires_at": "2099-01-01T00:00:00+00:00",
    "source_fingerprint": "fp-f07",
    "legacy_seals": [{"month": "2026-10", "legacy_seal_id": "seal-1", "sealed_at": "2026-10-19T00:00:00+00:00"}],
}


class TestDraftMigrationStrip:
    """F07：迁移子域由迁移专用命令独占——普通草稿保存不删屏障、不改状态。

    修复前 VM 草稿 payload 不含 ``migration_transition``，CAS 以 payload 整域
    替换 model_management，draining/sealed 屏障被一次普通保存清掉（pending 的
    旧链准入可重新变 allowed）；payload 自带 ``migration_state="active"`` 还可
    绕过专用 seal/activate 流程。
    """

    def _mm_with_transition(self, config_file: Path, *, phase: str = "draining") -> dict:
        cfg_mod.ConfigHandler.ensure_defaults()
        raw = _read_raw_config(config_file)
        raw["model_management"]["migration_transition"] = {**_DRAINING_TRANSITION, "phase": phase}
        _write_raw_config(config_file, raw)
        return dict(cfg_mod.ConfigHandler.load_config()["model_management"])

    def test_draft_save_preserves_disk_transition(self, isolated_config):
        """begin/seal 后普通草稿保存：payload 不带迁移键，磁盘屏障原样保留。"""
        disk_mm = self._mm_with_transition(isolated_config, phase="sealed")
        payload = dict(disk_mm)
        payload.pop("migration_transition", None)  # 模拟真实 VM 草稿 payload（不含迁移子域）
        payload.pop("last_config_commit", None)
        expected = payload["config_revision"]

        result = cfg_mod.ConfigHandler.compare_and_save_model_management(expected, payload, "op-f07-draft")

        assert result.status == "committed"
        raw = _read_raw_config(isolated_config)
        transition = raw["model_management"]["migration_transition"]
        assert transition["phase"] == "sealed"
        assert transition["transition_id"] == _DRAINING_TRANSITION["transition_id"]
        assert transition["transition_epoch"] == _DRAINING_TRANSITION["transition_epoch"]
        assert transition["legacy_seals"] == _DRAINING_TRANSITION["legacy_seals"]
        assert raw["model_management"]["migration_state"] == "pending"

    def test_draft_payload_cannot_overwrite_migration_subdomain(self, isolated_config):
        """草稿 payload 自带 active / 伪屏障：被剥离，落盘为磁盘现值。"""
        disk_mm = self._mm_with_transition(isolated_config)  # draining
        payload = dict(disk_mm)
        payload["migration_state"] = "active"  # 攻击形态：草稿直写 active 绕过 activate
        payload["migration_transition"] = {**_DRAINING_TRANSITION, "phase": "sealed", "transition_epoch": 99}
        payload.pop("last_config_commit", None)

        result = cfg_mod.ConfigHandler.compare_and_save_model_management(
            payload["config_revision"], payload, "op-f07-smuggle"
        )

        assert result.status == "committed"
        raw = _read_raw_config(isolated_config)
        assert raw["model_management"]["migration_state"] == "pending"
        transition = raw["model_management"]["migration_transition"]
        assert transition["phase"] == "draining"
        assert transition["transition_epoch"] == _DRAINING_TRANSITION["transition_epoch"]

    def test_draft_save_on_missing_migration_subdomain_falls_back_to_defaults(self, isolated_config):
        """磁盘无迁移子域（首次初始化）：草稿保存落出明确初始态 pending / 无屏障。"""
        payload = AppConfig().model_dump()["model_management"]
        payload.pop("migration_state", None)
        payload.pop("migration_transition", None)

        result = cfg_mod.ConfigHandler.compare_and_save_model_management(1, payload, "op-f07-init")

        assert result.status == "committed"
        raw = _read_raw_config(isolated_config)
        assert raw["model_management"]["migration_state"] == "pending"
        assert raw["model_management"]["migration_transition"] is None

    def test_migration_command_path_submits_migration_subdomain(self, isolated_config):
        """update_migration_state=True（迁移命令专用）：payload 迁移子域原样提交。"""
        disk_mm = self._mm_with_transition(isolated_config)
        payload = dict(disk_mm)
        payload["migration_state"] = "pending"
        payload["migration_transition"] = {**_DRAINING_TRANSITION, "phase": "sealed"}
        payload.pop("last_config_commit", None)

        result = cfg_mod.ConfigHandler.compare_and_save_model_management(
            payload["config_revision"], payload, "op-f07-cmd", update_security_state=False, update_migration_state=True
        )

        assert result.status == "committed"
        raw = _read_raw_config(isolated_config)
        assert raw["model_management"]["migration_transition"]["phase"] == "sealed"
        assert (
            raw["model_management"]["migration_transition"]["transition_epoch"]
            == (_DRAINING_TRANSITION["transition_epoch"])
        )
