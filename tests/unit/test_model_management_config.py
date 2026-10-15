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
from unittest import mock

import pytest

import utils.config_handler as cfg_mod
from utils.config_models import AppConfig

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

        result = cfg_mod.ConfigHandler.compare_and_save_model_management(payload["config_revision"], payload, "op-bad")

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
