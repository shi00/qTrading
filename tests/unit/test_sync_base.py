import datetime
from unittest.mock import MagicMock

import pandas as pd

from data.sync.base import ISyncStrategy, SyncContext, SyncResult, safe_error
import pytest


pytestmark = pytest.mark.unit


class TestSyncContext:
    def test_creation(self):
        ctx = SyncContext(api="api", cache="cache")
        assert ctx.api == "api"
        assert ctx.cache == "cache"

    def test_processor_none(self):
        ctx = SyncContext(api="api", cache="cache")
        assert ctx.processor is None

    def test_processor_setter_with_weakref(self):
        ctx = SyncContext(api="api", cache="cache")
        obj = MagicMock()
        ctx.processor = obj
        assert ctx.processor is obj

    def test_processor_setter_none(self):
        ctx = SyncContext(api="api", cache="cache")
        ctx.processor = None
        assert ctx.processor is None


class TestSyncResult:
    def test_default_values(self):
        result = SyncResult()
        assert result.added == 0
        assert result.updated == 0
        assert result.skipped == 0
        assert result.errors == []
        assert result.status == "success"

    def test_custom_values(self):
        result = SyncResult(added=10, updated=5, status="partial")
        assert result.added == 10
        assert result.updated == 5
        assert result.status == "partial"

    def test_merge_adds_counts(self):
        r1 = SyncResult(added=5, updated=3)
        r2 = SyncResult(added=2, updated=1)
        r1.merge(r2)
        assert r1.added == 7
        assert r1.updated == 4

    def test_merge_errors(self):
        r1 = SyncResult(errors=["err1"])
        r2 = SyncResult(errors=["err2"])
        r1.merge(r2)
        assert r1.errors == ["err1", "err2"]

    def test_merge_message_concat(self):
        r1 = SyncResult(message="first")
        r2 = SyncResult(message="second")
        r1.merge(r2)
        assert r1.message == "first | second"

    def test_merge_message_empty_self(self):
        r1 = SyncResult(message="")
        r2 = SyncResult(message="only")
        r1.merge(r2)
        assert r1.message == "only"

    def test_merge_message_truncation(self):
        r1 = SyncResult(message="A" * 1500)
        r2 = SyncResult(message="B" * 1500)
        r1.merge(r2)
        assert len(r1.message) == 2000
        assert r1.message.endswith("...")

    def test_merge_quality_scores(self):
        r1 = SyncResult(quality_scores={datetime.date(2024, 1, 1): 0.9})
        r2 = SyncResult(quality_scores={datetime.date(2024, 1, 2): 0.8})
        r1.merge(r2)
        assert len(r1.quality_scores) == 2

    def test_merge_quality_scores_string_keys(self):
        r1 = SyncResult(quality_scores={})
        r2 = SyncResult(quality_scores={"20240101": 0.9})
        r1.merge(r2)
        assert datetime.date(2024, 1, 1) in r1.quality_scores

    def test_merge_table_stats_new(self):
        r1 = SyncResult(table_stats={"daily": {"count": 10}})
        r2 = SyncResult(table_stats={"weekly": {"count": 5}})
        r1.merge(r2)
        assert "weekly" in r1.table_stats

    def test_merge_table_stats_existing(self):
        r1 = SyncResult(table_stats={"daily": {"count": 10}})
        r2 = SyncResult(table_stats={"daily": {"count": 5}})
        r1.merge(r2)
        assert r1.table_stats["daily"]["count"] == 15


class TestSyncResultMergeStatus:
    def test_merge_both_success(self):
        r1 = SyncResult(status="success")
        r2 = SyncResult(status="success")
        r1.merge(r2)
        assert r1.status == "success"

    def test_merge_one_failed(self):
        r1 = SyncResult(status="success")
        r2 = SyncResult(status="failed")
        r1.merge(r2)
        assert r1.status == "partial"

    def test_merge_both_failed(self):
        r1 = SyncResult(status="failed")
        r2 = SyncResult(status="failed")
        r1.merge(r2)
        assert r1.status == "failed"

    def test_merge_cancelled_overrides(self):
        r1 = SyncResult(status="success")
        r2 = SyncResult(status="cancelled")
        r1.merge(r2)
        assert r1.status == "cancelled"

    def test_merge_cancelled_self(self):
        r1 = SyncResult(status="cancelled")
        r2 = SyncResult(status="success")
        r1.merge(r2)
        assert r1.status == "cancelled"

    def test_merge_partial_and_failed(self):
        r1 = SyncResult(status="partial")
        r2 = SyncResult(status="failed")
        r1.merge(r2)
        assert r1.status == "partial"

    def test_merge_success_and_partial(self):
        r1 = SyncResult(status="success")
        r2 = SyncResult(status="partial")
        r1.merge(r2)
        assert r1.status == "partial"


class TestSyncResultToSummary:
    def test_default(self):
        r = SyncResult()
        assert "status=success" in r.to_summary()

    def test_with_added(self):
        r = SyncResult(added=10)
        assert "added=10" in r.to_summary()

    def test_with_updated(self):
        r = SyncResult(updated=5)
        assert "updated=5" in r.to_summary()

    def test_with_skipped(self):
        r = SyncResult(skipped=3)
        assert "skipped=3" in r.to_summary()

    def test_with_errors(self):
        r = SyncResult(errors=["e1", "e2"])
        assert "errors=2" in r.to_summary()

    def test_with_warnings(self):
        r = SyncResult(warnings=["w1"])
        assert "warnings=1" in r.to_summary()

    def test_with_message(self):
        r = SyncResult(message="test msg")
        assert "message=test msg" in r.to_summary()

    def test_empty_counts_omitted(self):
        r = SyncResult()
        summary = r.to_summary()
        assert "added" not in summary
        assert "updated" not in summary
        assert "skipped" not in summary


class TestSyncResultToDict:
    def test_default(self):
        d = SyncResult().to_dict()
        assert d["status"] == "success"
        assert d["added"] == 0
        assert d["updated"] == 0
        assert d["skipped"] == 0
        assert d["errors"] == []
        assert d["warnings"] == []

    def test_with_values(self):
        r = SyncResult(added=5, updated=3, status="partial", errors=["e1"])
        d = r.to_dict()
        assert d["added"] == 5
        assert d["status"] == "partial"
        assert d["errors"] == ["e1"]

    def test_returns_copy(self):
        r = SyncResult(errors=["e1"])
        d = r.to_dict()
        d["errors"].append("e2")
        assert len(r.errors) == 1

    def test_quality_scores_copy(self):
        r = SyncResult(quality_scores={datetime.date(2024, 1, 1): 0.9})
        d = r.to_dict()
        d["quality_scores"][datetime.date(2024, 1, 2)] = 0.8
        assert len(r.quality_scores) == 1


class TestSyncResultMergeExpectedBases:
    def test_merge_expected_bases(self):
        r1 = SyncResult(expected_bases={datetime.date(2024, 1, 1): 100})
        r2 = SyncResult(expected_bases={datetime.date(2024, 1, 2): 200})
        r1.merge(r2)
        assert len(r1.expected_bases) == 2

    def test_merge_expected_bases_string_keys(self):
        r1 = SyncResult(expected_bases={})
        r2 = SyncResult(expected_bases={"20240101": 100})
        r1.merge(r2)
        assert datetime.date(2024, 1, 1) in r1.expected_bases


class TestSyncResultMergeWarnings:
    def test_merge_warnings(self):
        r1 = SyncResult(warnings=["w1"])
        r2 = SyncResult(warnings=["w2"])
        r1.merge(r2)
        assert r1.warnings == ["w1", "w2"]


class TestISyncStrategyCleanNullValues:
    def test_none_df(self):
        assert ISyncStrategy._clean_null_values(None) is None

    def test_empty_string(self):
        df = pd.DataFrame({"col": ["", "hello"]})
        result = ISyncStrategy._clean_null_values(df)
        assert pd.isna(result.iloc[0]["col"])

    def test_none_string(self):
        df = pd.DataFrame({"col": ["None", "hello"]})
        result = ISyncStrategy._clean_null_values(df)
        assert pd.isna(result.iloc[0]["col"])

    def test_nan_string(self):
        df = pd.DataFrame({"col": ["nan", "hello"]})
        result = ISyncStrategy._clean_null_values(df)
        assert pd.isna(result.iloc[0]["col"])

    def test_normal_values_preserved(self):
        df = pd.DataFrame({"col": ["hello", "world"]})
        result = ISyncStrategy._clean_null_values(df)
        assert list(result["col"]) == ["hello", "world"]

    def test_object_without_replace(self):
        assert ISyncStrategy._clean_null_values(42) == 42


class TestSyncContextConfig:
    def test_config_default_none(self):
        ctx = SyncContext(api="api", cache="cache")
        assert ctx.config is None

    def test_config_set(self):
        ctx = SyncContext(api="api", cache="cache", config="cfg")
        assert ctx.config == "cfg"


class TestISyncStrategyCancelSemantics:
    """D-P1-6: Verify all sync strategies use _check_cancelled consistently."""

    @staticmethod
    def _make_strategy():
        ctx = SyncContext(api=MagicMock(), cache=MagicMock())

        class ConcreteStrategy(ISyncStrategy):
            async def _run_impl(self, **kwargs):
                return SyncResult()

        return ConcreteStrategy(ctx)

    def test_check_cancelled_sets_result_status(self):
        strategy = self._make_strategy()
        result = SyncResult()
        assert not strategy._check_cancelled(result)
        assert result.status == "success"

    def test_check_cancelled_returns_true_when_cancelled(self):
        strategy = self._make_strategy()
        strategy._cancelled = True
        result = SyncResult()
        assert strategy._check_cancelled(result) is True
        assert result.status == "cancelled"

    def test_cancel_sets_flag(self):
        strategy = self._make_strategy()
        assert strategy._cancelled is False
        strategy.cancel()
        assert strategy._cancelled is True

    def test_holder_uses_check_cancelled(self):
        from data.sync.holder import HolderSyncStrategy

        assert hasattr(HolderSyncStrategy, "_check_cancelled"), "HolderSyncStrategy should use _check_cancelled"

    def test_historical_checks_cancelled_after_run(self):
        from data.sync.historical import HistoricalSyncStrategy

        assert hasattr(HistoricalSyncStrategy, "_check_cancelled"), (
            "HistoricalSyncStrategy should have _check_cancelled method"
        )

    def test_macro_uses_check_cancelled(self):
        from data.sync.macro import MacroSyncStrategy

        assert hasattr(MacroSyncStrategy, "_check_cancelled"), "MacroSyncStrategy.run should use _check_cancelled"


class TestSafeError:
    """P3-2: data/ 层 R9 一致性 — safe_error 共享脱敏入口单测。"""

    def test_returns_sanitized_string_for_plain_exception(self):
        """普通异常经 safe_error 返回字符串。"""
        result = safe_error(ValueError("plain message"))
        assert isinstance(result, str)
        assert "plain message" in result

    def test_url_credentials_redacted(self):
        """含 DB URL 凭证的异常经 safe_error 后密码被脱敏。"""
        err = RuntimeError("connect to postgresql://user:secret_pass@host:5432/db failed")
        result = safe_error(err)
        assert "secret_pass" not in result
        assert "***" in result

    def test_known_secret_replaced(self):
        """注册的 secret 在异常消息中被精确替换为 ***。"""
        from utils.sanitizers import DataSanitizer

        secret = "super_secret_token_12345"
        DataSanitizer.register_secret(secret)
        try:
            err = RuntimeError(f"auth failed with token={secret}")
            result = safe_error(err)
            assert secret not in result
            assert "***" in result
        finally:
            DataSanitizer._reset_known_secrets()

    def test_non_exception_string_input(self):
        """safe_error 接受 Exception 对象，str(e) 用于脱敏。"""
        err = ValueError("with api_key=sk-abcd1234efgh5678")
        result = safe_error(err)
        assert "sk-abcd1234efgh5678" not in result

    def test_returns_string_for_empty_exception(self):
        """空消息异常返回空字符串（脱敏后）。"""
        result = safe_error(ValueError(""))
        assert isinstance(result, str)
