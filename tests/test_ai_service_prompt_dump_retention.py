import os
import sys
import time
import datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import services.ai_service as ai_mod
import utils.time_utils as time_utils


@pytest.mark.asyncio
async def test_analyze_stock_does_not_dump_prompt_when_feature_disabled(monkeypatch, tmp_path):
    """默认应关闭 prompt 落盘，即使日志级别允许也不写文件。"""
    ai_mod.AIService._reset_singleton()
    service = ai_mod.AIService()

    monkeypatch.setattr(ai_mod.logger, "isEnabledFor", lambda level: True)
    monkeypatch.setattr(ai_mod.ConfigHandler, "get_setting", staticmethod(lambda key, default=None: False))
    monkeypatch.setattr(ai_mod.ConfigHandler, "get_ai_system_prompt", staticmethod(lambda: "SYSTEM"))
    monkeypatch.setattr(service, "is_cloud_available", lambda: True)
    monkeypatch.setattr(service, "_chat_completion", AsyncMock(return_value={"score": 88, "reason": "ok"}))
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
    monkeypatch.setattr(service, "_chat_completion", AsyncMock(return_value={"score": 90, "reason": "ok"}))
    monkeypatch.setattr(ai_mod, "validate_ai_analysis_response", lambda res: res)
    monkeypatch.setattr(ai_mod.config, "APP_ROOT", str(tmp_path), raising=False)
    monkeypatch.setattr(time, "time", lambda: fake_now_ts)
    monkeypatch.setattr(
        os.path, "getmtime", lambda path: fake_now_ts - (25 * 60 * 60) if Path(path).name == "stale.md" else fake_now_ts
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
