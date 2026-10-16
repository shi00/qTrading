# pyright: reportArgumentType=false, reportAttributeAccessIssue=false
# 复现/回归：BaseDao._save_upsert 的 null_protected 对 JSONB 列失效（根因修复回归）。
# S4 MM-06：save_news_risk_brief 改 Core 条件 upsert 后本文件同步迁移——
# 成功→失败写入被 ``analysis_status NOT IN (成功态)`` 条件整行挡下；coalesce
# 保护改由失败→失败序列验证（既有行非成功态 → WHERE 放行 → EXCLUDED NULL
# 经 coalesce 不覆盖首写非空值）。

import datetime

import pytest

from tests.integration.test_infra_base import TestDatabaseBase

pytestmark = pytest.mark.integration

_TS = "000001.SZ"


def _brief(input_hash: str, status: str, *, empty_results: bool = False) -> dict:
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    win = (now - datetime.timedelta(days=3), now)
    brief = {
        "ts_code": _TS,
        "input_hash": input_hash,
        "window_start": win[0],
        "window_end": win[1],
        "analysis_status": status,
        "risk_level": "high",
        "confidence": 80,
        "summary": "succ",
        "events": [{"severity": "high"}],
        "evidence_news_ids": [1, 2],
        "coverage": {"x": 1},
        "model_id": "m",
        "analysis_profile": "p",
        "prompt_version": "v1",
    }
    if empty_results:
        for c in ("risk_level", "confidence", "summary", "events", "evidence_news_ids", "coverage", "model_id"):
            brief[c] = None
    return brief


class TestNullProtectedJsonb(TestDatabaseBase):
    @property
    def dao(self):
        return self.cache.market_dao

    async def test_upsert_null_protected_jsonb_keeps_first_value(self):
        """null_protected JSONB 列同主键二次写 None 必须保持首写值（回归：none_as_null）。

        根因：SQLAlchemy JSONB 默认把 Python None 编码为 JSON 'null'（非 SQL NULL），
        使 null_protected ``coalesce(EXCLUDED, table)`` 保护失效
        （coalesce('null'::jsonb, old) 仍返回 'null'，覆盖旧值）。模型声明
        ``none_as_null=True`` 后，None 编为 SQL NULL，coalesce 正确保留首写值。

        S4 MM-06 后失败→失败序列仍命中 coalesce 路径（既有行非成功态，
        条件 WHERE 放行，EXCLUDED None 不覆盖首写非空结果）。
        """
        input_hash = "j" * 64
        first = _brief(input_hash, "failed")
        await self.dao.save_news_risk_brief(first)
        # 同主键二次写：可空结果列全为 None（失败不得覆盖既有失败结果列）
        second = _brief(input_hash, "failed", empty_results=True)
        outcome = await self.dao.save_news_risk_brief(second)

        assert outcome == {"written": True, "kept_success": False}
        got = await self.dao.get_news_risk_brief(_TS, input_hash)
        assert got["analysis_status"] == "failed"  # 非 null_protected：EXCLUDED 更新
        assert got["summary"] == "succ"  # TEXT 保持（coalesce）
        assert got["evidence_news_ids"] == [1, 2]  # JSONB 保持（coalesce + none_as_null）
        assert got["events"] == [{"severity": "high"}]
        assert got["coverage"] == {"x": 1}

    async def test_failed_write_blocked_by_existing_success(self):
        """S4 MM-06：既有成功快照存在时 failed 写入整行无效（kept_success=True）。

        失败不得覆盖成功（§7.2）由 DAO 条件 upsert 兜底（服务层不再先查再写）。
        """
        input_hash = "k" * 64
        success = _brief(input_hash, "analyzed_with_events")
        await self.dao.save_news_risk_brief(success)

        outcome = await self.dao.save_news_risk_brief(_brief(input_hash, "failed", empty_results=True))
        assert outcome == {"written": False, "kept_success": True}

        got = await self.dao.get_news_risk_brief(_TS, input_hash)
        assert got["analysis_status"] == "analyzed_with_events"
        assert got["summary"] == "succ"
        assert got["evidence_news_ids"] == [1, 2]

    async def test_success_write_refreshes_snapshot(self):
        """成功态写入无条件刷新（允许成功快照更新，rowcount 恒 > 0）。"""
        input_hash = "l" * 64
        await self.dao.save_news_risk_brief(_brief(input_hash, "analyzed_no_event"))
        refreshed = _brief(input_hash, "analyzed_no_event")
        refreshed["summary"] = "refreshed"
        outcome = await self.dao.save_news_risk_brief(refreshed)
        assert outcome == {"written": True, "kept_success": False}
        got = await self.dao.get_news_risk_brief(_TS, input_hash)
        assert got["summary"] == "refreshed"
