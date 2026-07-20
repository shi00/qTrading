# pyright: reportOptionalSubscript=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 Optional 下标访问。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import os
import time
import datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import services.ai_service as ai_mod
import utils.time_utils as time_utils

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_analyze_stock_does_not_dump_prompt_when_feature_disabled(monkeypatch, tmp_path):
    """默认应关闭 prompt 落盘，即使日志级别允许也不写文件。"""
    ai_mod.AIService._reset_singleton()
    service = ai_mod.AIService()

    monkeypatch.setattr(ai_mod.logger, "isEnabledFor", lambda level: True)
    monkeypatch.setattr(
        ai_mod.ConfigHandler,
        "get_setting",
        staticmethod(lambda key, default=None: False),
    )
    monkeypatch.setattr(ai_mod.ConfigHandler, "get_ai_system_prompt", staticmethod(lambda: "SYSTEM"))
    monkeypatch.setattr(service, "is_cloud_available", lambda: True)
    monkeypatch.setattr(
        service,
        "_chat_completion",
        AsyncMock(return_value={"score": 88, "reason": "ok"}),
    )
    monkeypatch.setattr(ai_mod, "validate_ai_analysis_response", lambda res: res)
    monkeypatch.setattr(ai_mod.config, "APP_ROOT", str(tmp_path), raising=False)

    result = await service.analyze_stock(
        stock_info={"ts_code": "000001.SZ", "name": "平安银行"},
        tech_info={"close": 10.0},
        news_list=[],
        strategy_key="oversold",
    )

    assert result["score"] == 88
    assert not (tmp_path / "logs" / "ai_prompts").exists()


@pytest.mark.asyncio
async def test_prompt_dump_cleanup_outside_hot_path(monkeypatch, tmp_path):
    """M-4: 清理由独立 helper 触发，analyze 仅负责落盘。"""
    ai_mod.AIService._reset_singleton()
    service = ai_mod.AIService()

    prompt_dir = tmp_path / "logs" / "ai_prompts"
    prompt_dir.mkdir(parents=True)
    stale_file = prompt_dir / "stale.md"
    fresh_file = prompt_dir / "fresh.md"
    stale_file.write_text("old", encoding="utf-8")
    fresh_file.write_text("fresh", encoding="utf-8")

    fake_now_ts = 1_700_000_000

    monkeypatch.setattr(ai_mod.logger, "isEnabledFor", lambda level: True)
    monkeypatch.setattr(
        ai_mod.ConfigHandler,
        "get_setting",
        staticmethod(lambda key, default=None: True if key == "ai_prompt_dump_enabled" else default),
    )
    monkeypatch.setattr(ai_mod.ConfigHandler, "get_ai_system_prompt", staticmethod(lambda: "SYSTEM"))
    monkeypatch.setattr(service, "is_cloud_available", lambda: True)
    monkeypatch.setattr(
        service,
        "_chat_completion",
        AsyncMock(return_value={"score": 90, "reason": "ok"}),
    )
    monkeypatch.setattr(ai_mod, "validate_ai_analysis_response", lambda res: res)
    monkeypatch.setattr(ai_mod.config, "APP_ROOT", str(tmp_path), raising=False)
    monkeypatch.setattr(time, "time", lambda: fake_now_ts)
    monkeypatch.setattr(
        os.path,
        "getmtime",
        lambda path: fake_now_ts - (25 * 60 * 60) if Path(path).name == "stale.md" else fake_now_ts,
    )
    monkeypatch.setattr(time_utils, "get_now", lambda: datetime.datetime(2026, 4, 28, 12, 0, 0))

    service._cleanup_prompt_dumps()

    result = await service.analyze_stock(
        stock_info={"ts_code": "000001.SZ", "name": "平安银行"},
        tech_info={"close": 10.0},
        news_list=[],
        strategy_key="oversold",
    )

    assert result["score"] == 90
    assert not stale_file.exists()
    assert fresh_file.exists()
    dumped = list(prompt_dir.glob("oversold_000001.SZ_*.md"))
    assert len(dumped) == 1


def _setup_dump_enabled(monkeypatch, tmp_path, service):
    """Common monkeypatch setup for SEC-008 dump-redaction tests."""

    monkeypatch.setattr(ai_mod.logger, "isEnabledFor", lambda level: True)
    monkeypatch.setattr(
        ai_mod.ConfigHandler,
        "get_setting",
        staticmethod(lambda key, default=None: True if key == "ai_prompt_dump_enabled" else default),
    )
    monkeypatch.setattr(ai_mod.ConfigHandler, "get_ai_system_prompt", staticmethod(lambda: "SYSTEM"))
    monkeypatch.setattr(service, "is_cloud_available", lambda: True)
    monkeypatch.setattr(
        service,
        "_chat_completion",
        AsyncMock(return_value={"score": 90, "reason": "ok"}),
    )
    monkeypatch.setattr(ai_mod, "validate_ai_analysis_response", lambda res: res)
    monkeypatch.setattr(ai_mod.config, "APP_ROOT", str(tmp_path), raising=False)
    monkeypatch.setattr(time_utils, "get_now", lambda: datetime.datetime(2026, 4, 28, 12, 0, 0))


@pytest.mark.asyncio
async def test_prompt_dump_redacts_user_custom_instructions(monkeypatch, tmp_path):
    """SEC-008: 含 user_custom_instructions 段的 prompt dump 后该段被脱敏为 [REDACTED]。"""
    ai_mod.AIService._reset_singleton()
    service = ai_mod.AIService()
    _setup_dump_enabled(monkeypatch, tmp_path, service)

    custom_instructions = "关注MACD金叉信号，注意成交量变化"
    result = await service.analyze_stock(
        stock_info={"ts_code": "000001.SZ", "name": "平安银行"},
        tech_info={"close": 10.0},
        news_list=[],
        strategy_key="oversold",
        ui_prompt_override=custom_instructions,
    )

    assert result["score"] == 90
    prompt_dir = tmp_path / "logs" / "ai_prompts"
    dumped = list(prompt_dir.glob("oversold_000001.SZ_*.md"))
    assert len(dumped) == 1
    content = dumped[0].read_text(encoding="utf-8")
    assert "<user_custom_instructions>[REDACTED]</user_custom_instructions>" in content
    # 原始自定义指令不应泄露到 dump 文件
    assert custom_instructions not in content
    # 系统指令与外部数据段应保留
    assert "Universal Rules (System)" in content
    assert "Strategy Prompt (System)" in content


@pytest.mark.asyncio
async def test_prompt_dump_without_user_custom_instructions_unchanged(monkeypatch, tmp_path):
    """SEC-008: 不含 user_custom_instructions 段时 dump 不受影响。"""
    ai_mod.AIService._reset_singleton()
    service = ai_mod.AIService()
    _setup_dump_enabled(monkeypatch, tmp_path, service)

    result = await service.analyze_stock(
        stock_info={"ts_code": "000001.SZ", "name": "平安银行"},
        tech_info={"close": 10.0},
        news_list=[],
        strategy_key="oversold",
    )

    assert result["score"] == 90
    prompt_dir = tmp_path / "logs" / "ai_prompts"
    dumped = list(prompt_dir.glob("oversold_000001.SZ_*.md"))
    assert len(dumped) == 1
    content = dumped[0].read_text(encoding="utf-8")
    # 无 user_custom_instructions 段时不应出现该标签或 [REDACTED]
    assert "<user_custom_instructions>" not in content
    assert "[REDACTED]" not in content
    # 市场数据段应正常保留
    assert "<market_data>" in content


@pytest.mark.asyncio
async def test_prompt_dump_redacts_multiline_user_custom_instructions(monkeypatch, tmp_path):
    """SEC-008: user_custom_instructions 段含多行内容时也能正确脱敏。"""
    ai_mod.AIService._reset_singleton()
    service = ai_mod.AIService()
    _setup_dump_enabled(monkeypatch, tmp_path, service)

    custom_instructions = "第一行：关注MACD金叉\n第二行：注意成交量放大\n第三行：观察主力资金流向"
    result = await service.analyze_stock(
        stock_info={"ts_code": "000001.SZ", "name": "平安银行"},
        tech_info={"close": 10.0},
        news_list=[],
        strategy_key="oversold",
        ui_prompt_override=custom_instructions,
    )

    assert result["score"] == 90
    prompt_dir = tmp_path / "logs" / "ai_prompts"
    dumped = list(prompt_dir.glob("oversold_000001.SZ_*.md"))
    assert len(dumped) == 1
    content = dumped[0].read_text(encoding="utf-8")
    assert "<user_custom_instructions>[REDACTED]</user_custom_instructions>" in content
    # 多行内容中的每一行都不应泄露到 dump 文件
    assert "第一行" not in content
    assert "第二行" not in content
    assert "第三行" not in content
