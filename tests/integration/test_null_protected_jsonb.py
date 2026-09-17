# pyright: reportArgumentType=false, reportAttributeAccessIssue=false
# 复现/回归：BaseDao._save_upsert 的 null_protected 对 JSONB 列失效（根因修复回归）。

import datetime

import pytest

from tests.integration.test_infra_base import TestDatabaseBase

pytestmark = pytest.mark.integration

_TS = "000001.SZ"


class TestNullProtectedJsonb(TestDatabaseBase):
    @property
    def dao(self):
        return self.cache.market_dao

    async def test_upsert_null_protected_jsonb_keeps_first_value(self):
        """null_protected JSONB 列同主键二次写 None 必须保持首写值（回归：none_as_null）。

        根因：SQLAlchemy JSONB 默认把 Python None 编码为 JSON 'null'（非 SQL NULL），
        使 _save_upsert 的 null_protected ``coalesce(EXCLUDED, table)`` 保护失效
        （coalesce('null'::jsonb, old) 仍返回 'null'，覆盖旧值）。模型声明
        ``none_as_null=True`` 后，None 编为 SQL NULL，coalesce 正确保留首写值。
        """
        input_hash = "j" * 64
        now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
        win = (now - datetime.timedelta(days=3), now)
        first = {
            "ts_code": _TS,
            "input_hash": input_hash,
            "window_start": win[0],
            "window_end": win[1],
            "analysis_status": "analyzed_with_events",
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
        await self.dao.save_news_risk_brief(first)
        # 同主键二次写：可空结果列全为 None（失败不得覆盖既有成功结果）
        second = dict(first)
        for c in ("risk_level", "confidence", "summary", "events", "evidence_news_ids", "coverage", "model_id"):
            second[c] = None
        second["analysis_status"] = "failed"
        await self.dao.save_news_risk_brief(second)

        got = await self.dao.get_news_risk_brief(_TS, input_hash)
        assert got["analysis_status"] == "failed"  # 非 null_protected：EXCLUDED 更新
        assert got["summary"] == "succ"  # TEXT 保持
        assert got["evidence_news_ids"] == [1, 2]  # JSONB 保持
        assert got["events"] == [{"severity": "high"}]
        assert got["coverage"] == {"x": 1}
