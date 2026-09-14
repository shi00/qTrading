# pyright: reportArgumentType=false, reportOptionalMemberAccess=false, reportOptionalSubscript=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）、
# Optional 成员访问（mock 返回 None）、Optional 下标访问。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

"""SEC-03 数据出口审计——AI 服务层集成测试。

验证 ``LiteLLMClient._chat_completion`` 云分支的审计点与 AIService 编排的联动
（design_sec03_egress_audit.md §4 集成层面）：
1. cloud 调用触发 record，destination = 配置 provider/model（effective model 单点来源）
2. model_override（failover cross-provider）时 destination = override 的真实 model
3. failover 多供应商尝试：每次云端尝试均记录（primary + fallback 各一条，不遗漏目的地）
4. local 调用不记录（数据不出本机）
5. 「仅本地模式」开启时 is_cloud_available() 返回 False，cloud 分支拒绝且不产生审计
6. 审计落盘失败不阻断 AI 主流程（_chat_completion 仍返回结果）
7. category 随 purpose 传递（analysis / news）

R2：单测不吞 CancelledError；审计点自身含 CancelledError 传播测试（见
tests/unit/test_egress_audit.py）。
"""

from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.ai_service import AIService
from services.ai_service.litellm_client import _resolve_effective_model
from utils.egress_audit import EgressAudit

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _tmp_egress_path(tmp_path):
    """注入 EgressAudit 落盘路径到临时目录（reset 后、首次 record 前）。"""
    EgressAudit._reset_singleton()
    EgressAudit._configure_for_tests(str(tmp_path / "egress_audit.jsonl"))
    yield tmp_path


def _read_records(path) -> list[dict]:
    """读取 JSONL 审计文件（唯一事实源）为记录列表；无文件返回空列表。"""
    file = path / "egress_audit.jsonl"
    if not file.exists():
        return []
    lines = file.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _make_cloud_service(monkeypatch):
    """构造已配置 cloud 的 AIService（隔离 singleton reset 由 conftest autouse 保证）。"""
    with patch("services.ai_service.ConfigHandler") as mock_ch:
        mock_ch.get_llm_config.return_value = {
            "api_key": "test-key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
            "model": "deepseek-v4-flash",
        }
        mock_ch.get_setting.return_value = False
        mock_ch.get_ai_max_concurrent_analysis.return_value = 5
        mock_ch.get_failover_config.return_value = {
            "primary": "deepseek/deepseek-v4-flash",
            "fallbacks": [],
        }
        svc = AIService()
    # 显式给定云配置，脱离 mock ConfigHandler（is_cloud_available 走真实 ConfigHandler，
    # 仅「仅本地模式」开关经 monkeypatch 显式控制）。
    svc._is_cloud_configured = True
    svc._litellm_config = {
        "api_key": "test-key",
        "provider": "deepseek",
        "base_url": "http://api.test.com",
        "model": "deepseek-v4-flash",
    }
    monkeypatch.setattr(
        "utils.config_handler.ConfigHandler.is_ai_local_only_mode",
        staticmethod(lambda: False),
    )
    return svc


# ============================================================================
# cloud 调用触发审计（destination = effective model）
# ============================================================================


class TestCloudCallRecordsEgress:
    @pytest.mark.asyncio
    async def test_cloud_call_records_destination_from_config(self, _tmp_egress_path, monkeypatch) -> None:
        """cloud 成功调用后 JSONL 一条记录，destination = 配置 provider/model。"""
        svc = _make_cloud_service(monkeypatch)
        svc._chat_completion_litellm = AsyncMock(return_value={"content": '{"score": 88, "reason": "ok"}'})

        result = await svc._chat_completion(
            messages=[{"role": "user", "content": "分析这支股票"}],
            provider="cloud",
            json_mode=True,
        )

        assert result["score"] == 88
        records = _read_records(_tmp_egress_path)
        assert len(records) == 1
        rec = records[0]
        assert rec["destination"] == "llm:deepseek/deepseek-v4-flash"
        assert rec["category"] == "analysis"
        assert rec["item_count"] == 1
        assert rec["payload_size_bytes"] > 0
        assert rec["status"] == "sent"
        # 元数据审计不记录 prompt 内容本身（防二次泄露）
        assert "分析这支股票" not in json.dumps(rec, ensure_ascii=False)

    @pytest.mark.asyncio
    async def test_category_follows_purpose_news(self, _tmp_egress_path, monkeypatch) -> None:
        """purpose=news 时 category 记录为 news。"""
        svc = _make_cloud_service(monkeypatch)
        svc._chat_completion_litellm = AsyncMock(return_value={"content": '{"category": "tech"}'})

        await svc._chat_completion(
            messages=[{"role": "user", "content": "分类"}],
            provider="cloud",
            purpose="news",
            json_mode=True,
        )

        records = _read_records(_tmp_egress_path)
        assert records[0]["category"] == "news"

    @pytest.mark.asyncio
    async def test_model_override_uses_override_destination(self, _tmp_egress_path, monkeypatch) -> None:
        """failover/cross-provider 传入 model_override 时 destination = override 的真实 model。"""
        svc = _make_cloud_service(monkeypatch)
        svc._chat_completion_litellm = AsyncMock(return_value={"content": '{"score": 88}'})

        await svc._chat_completion(
            messages=[{"role": "user", "content": "x"}],
            provider="cloud",
            model="qwen/qwen-max",  # 模拟跨供应商 override
            json_mode=True,
        )

        records = _read_records(_tmp_egress_path)
        assert len(records) == 1
        assert records[0]["destination"] == "llm:qwen/qwen-max"


# ============================================================================
# failover 每次云端尝试均记录
# ============================================================================


class TestFailoverRecordsEachAttempt:
    @pytest.mark.asyncio
    async def test_failover_primary_fallback_each_recorded(self, _tmp_egress_path, monkeypatch) -> None:
        """主供应商超时切 fallback：primary + fallback 各一条审计，目的地不混淆。"""
        svc = _make_cloud_service(monkeypatch)
        monkeypatch.setattr(
            "utils.config_handler.ConfigHandler.get_failover_config",
            staticmethod(lambda: {"primary": "deepseek/deepseek-v4-flash", "fallbacks": ["qwen/qwen-max"]}),
        )
        # 第一次（primary）超时 → 触发 fallback；第二次（qwen）成功
        svc._chat_completion_litellm = AsyncMock(
            side_effect=[TimeoutError("primary timeout"), {"content": '{"score": 88}'}],
        )

        result = await svc._chat_completion_with_failover(
            messages=[{"role": "user", "content": "x"}],
            json_mode=True,
        )

        assert result["score"] == 88
        records = _read_records(_tmp_egress_path)
        dests = sorted(r["destination"] for r in records)
        assert dests == ["llm:deepseek/deepseek-v4-flash", "llm:qwen/qwen-max"]
        assert all(r["category"] == "analysis" for r in records)

    @pytest.mark.asyncio
    async def test_failover_primary_success_single_record(self, _tmp_egress_path, monkeypatch) -> None:
        """主供应商直接成功：仅一条审计，不因 failover 循环产生多余记录。"""
        svc = _make_cloud_service(monkeypatch)
        monkeypatch.setattr(
            "utils.config_handler.ConfigHandler.get_failover_config",
            staticmethod(lambda: {"primary": "deepseek/deepseek-v4-flash", "fallbacks": ["qwen/qwen-max"]}),
        )
        svc._chat_completion_litellm = AsyncMock(return_value={"content": '{"score": 88}'})

        await svc._chat_completion_with_failover(
            messages=[{"role": "user", "content": "x"}],
            json_mode=True,
        )

        records = _read_records(_tmp_egress_path)
        assert len(records) == 1
        assert records[0]["destination"] == "llm:deepseek/deepseek-v4-flash"


# ============================================================================
# local 不记录 / 仅本地模式拦截
# ============================================================================


class TestLocalAndLocalOnly:
    @pytest.mark.asyncio
    async def test_local_call_does_not_record(self, _tmp_egress_path, monkeypatch) -> None:
        """local 推理不产生审计记录（数据不出本机）。"""
        svc = _make_cloud_service(monkeypatch)
        mock_manager = MagicMock()
        mock_manager.get_loaded_model_path.return_value = "/path/to/model"
        mock_manager.run_inference = AsyncMock(return_value='{"category": "tech"}')
        with (
            patch(
                "services.local_model_manager.LocalModelManager.get_instance",
                AsyncMock(return_value=mock_manager),
            ),
            patch.object(svc, "_setup_local_model", AsyncMock()),
        ):
            result = await svc._chat_completion(
                messages=[
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "hello"},
                ],
                provider="local",
                json_mode=True,
            )

        assert result["category"] == "tech"
        assert _read_records(_tmp_egress_path) == []

    @pytest.mark.asyncio
    async def test_local_only_mode_blocks_cloud_without_audit(self, _tmp_egress_path, monkeypatch) -> None:
        """仅本地模式开启：is_cloud_available False，cloud 分支拒绝且不产生审计。"""
        svc = _make_cloud_service(monkeypatch)
        monkeypatch.setattr(
            "utils.config_handler.ConfigHandler.is_ai_local_only_mode",
            staticmethod(lambda: True),
        )

        assert svc.is_cloud_available() is False
        with pytest.raises(ValueError, match="Cloud LLM not configured"):
            await svc._chat_completion(
                messages=[{"role": "user", "content": "x"}],
                provider="cloud",
            )
        assert _read_records(_tmp_egress_path) == []

    @pytest.mark.asyncio
    async def test_local_only_off_cloud_available(self, _tmp_egress_path, monkeypatch) -> None:
        """仅本地模式关闭时云端可用（回归确认开关语义）。"""
        svc = _make_cloud_service(monkeypatch)
        assert svc.is_cloud_available() is True


# ============================================================================
# 审计失败降级不阻断 AI 主流程
# ============================================================================


class TestAuditFailureDegradation:
    @pytest.mark.asyncio
    async def test_audit_append_failure_does_not_block_cloud_call(self, _tmp_egress_path, monkeypatch) -> None:
        """落盘失败仅降级：_chat_completion 仍正常返回结果。"""
        svc = _make_cloud_service(monkeypatch)
        svc._chat_completion_litellm = AsyncMock(return_value={"content": '{"score": 88}'})
        monkeypatch.setattr(
            EgressAudit,
            "_append_jsonl",
            lambda rec: (_ for _ in ()).throw(OSError("disk full")),
        )

        result = await svc._chat_completion(
            messages=[{"role": "user", "content": "x"}],
            provider="cloud",
            json_mode=True,
        )

        assert result["score"] == 88


# ============================================================================
# _resolve_effective_model 单点解析（destination 数据来源）
# ============================================================================


class TestResolveEffectiveModel:
    def test_override_priority(self) -> None:
        assert _resolve_effective_model({"provider": "deepseek", "model": "m"}, "qwen/qwen-max") == "qwen/qwen-max"

    def test_config_provider_model(self) -> None:
        assert (
            _resolve_effective_model({"provider": "deepseek", "model": "deepseek-v4-flash"}, None)
            == "deepseek/deepseek-v4-flash"
        )

    def test_model_only_without_provider(self) -> None:
        assert _resolve_effective_model({"provider": "", "model": "m"}, None) == "m"
