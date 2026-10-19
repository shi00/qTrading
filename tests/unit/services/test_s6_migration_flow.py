"""S6 切换交付批 B：D6 旧链停接全流程单测（s6_plan.md §1 D6 / §2 测试计划）。

假端点全流程以受控 transport 承载（s6_plan §0 事实 8：E2E 无 AI 假端点 fixture）：
- 配置域走真实 ``ConfigHandler`` + 独立临时配置文件（真实 CAS / revision / 屏障语义）；
- 数据域走内存版 ``app_state`` / ``ai_usage_monthly``（DAO 与 AIUsageTracker 共享同一
  存储，postgresql 方言编译取参），不触真实库；
- 新链分派经调度服务 provider 注入 mock executor，不触真实 LLM。

覆盖：状态机 pending→draining→sealed→active 全流程（旧链准入 / tracker 写入口 /
DAO 封口 / activate 失败保持与重试恢复）、admission 状态表、三入口 helper 不降级、
ai_mixin 批量循环安静停接、``legacy_tail`` 尾写差额入账与未知差额不入账。
"""

from __future__ import annotations

import datetime
import json
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
from sqlalchemy.dialects import postgresql

import utils.config_handler as cfg_mod
from core.model_management_types import LegacyDispatchBlockedError, LegacySealedError
from data.persistence.daos.ai_usage_dao import AIUsageDao
from services.ai_service.news_classifier import _resolve_news_annotation_dispatch
from services.ai_service.news_risk import _resolve_news_risk_dispatch
from services.ai_service.scenario_dispatch import evaluate_legacy_admission, resolve_executor
from services.ai_service.stock_analysis import _resolve_stock_analysis_dispatch
from services.ai_service.usage_tracker import AIUsageTracker
from services.model_configuration_service import ModelConfigurationService
from utils.config_models import ModelManagementSaveResult

pytestmark = pytest.mark.unit

_NOW = datetime.date(2026, 10, 14)
_MONTH_ISO = "2026-10"
_MONTH_COMPACT = "202610"

_DRAINING_TRANSITION: dict[str, Any] = {
    "transition_id": "t-1",
    "owner_session_id": "owner-1",
    "transition_epoch": 1,
    "phase": "draining",
    "lease_expires_at": "2099-01-01T00:00:00+00:00",
    "source_fingerprint": "fp",
    "legacy_seals": [],
}
_SEALED_TRANSITION: dict[str, Any] = {**_DRAINING_TRANSITION, "phase": "sealed"}


# ---------------------------------------------------------------------------
# 受控 transport：内存版 app_state / ai_usage_monthly（DAO 与 tracker 共享）
# ---------------------------------------------------------------------------


class _QueryResult:
    def __init__(self, row: tuple | None) -> None:
        self._row = row

    def fetchone(self) -> tuple | None:
        return self._row


class _MemoryConn:
    """按语句形态路由的内存连接：仅实现 S6 流程涉及的 SQL 形态。"""

    def __init__(self, db: _MemoryDb) -> None:
        self._db = db

    async def execute(self, stmt: Any, params: Any = None) -> _QueryResult:
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql = " ".join(str(compiled).split())
        p = dict(compiled.params)
        if params:
            p.update(params)  # sa.text 外部参数在 execute 第二参（compiled.params 为空）
        if "pg_advisory_xact_lock" in sql:
            return _QueryResult(None)
        if sql.startswith("SELECT"):
            return self._select(sql, p)
        if sql.startswith("INSERT"):
            return self._insert(sql, p)
        raise AssertionError(f"内存库未预期的 SQL 语句: {sql}")

    def _select(self, sql: str, p: dict[str, Any]) -> _QueryResult:
        db = self._db
        if "FROM app_state" in sql:
            key = p.get("key") or p.get("config_key") or p.get("config_key_1")
            assert key, f"app_state SELECT 缺 key 参数: {sql}"
            row = db.app_state.get(key)
            return _QueryResult((row,) if row is not None else None)
        if "FROM ai_usage_monthly" in sql:
            rows = [r for (m, s, _g), r in db.monthly.items() if m == p["month"] and s == p["scope"]]
            if not rows:
                return _QueryResult((None,))  # SUM 空集 → NULL 行
            total = sum((Decimal(r["cost_cny"]) for r in rows), Decimal("0"))
            return _QueryResult((str(total),))
        if "FROM ai_invocation" in sql:
            return _QueryResult((None,))  # 预留表空 → NULL 聚合
        raise AssertionError(f"内存库未预期的 SELECT: {sql}")

    def _insert(self, sql: str, p: dict[str, Any]) -> _QueryResult:
        db = self._db
        if "INTO app_state" in sql:
            key = p["config_key"] if "config_key" in p else p["key"]
            value = p["config_value"] if "config_value" in p else p["value"]
            existing = db.app_state.get(key)
            # upsert 累加差额为 SET 子句中的独立绑定参数（非 config_key/config_value）
            delta: int | None = None
            if "ON CONFLICT" in sql:
                delta = next(
                    (
                        int(v)
                        for k, v in p.items()
                        if k not in ("config_key", "config_value") and str(v).lstrip("-").isdigit()
                    ),
                    None,
                )
                assert delta is not None, f"upsert 语句未解析出累加差额: {sql} params={p}"
            if existing is None or delta is None:
                db.app_state[key] = value
            else:
                db.app_state[key] = str(int(existing) + delta)
            return _QueryResult(None)
        if "ai_usage_monthly" in sql:
            row = db.monthly.setdefault(
                (p["month"], p["scope"], p["grouping_key"]),
                {
                    "invocation_count": 0,
                    "attempt_count": 0,
                    "succeeded_count": 0,
                    "cost_cny": Decimal("0"),
                    "last_usage_revision": 0,
                },
            )
            row["invocation_count"] += int(p["invocation_count"])
            row["attempt_count"] += int(p["attempt_count"])
            row["succeeded_count"] += int(p["succeeded_count"])
            row["cost_cny"] += Decimal(p["cost_cny"])
            row["last_usage_revision"] = max(row["last_usage_revision"], int(p["usage_revision"]))
            return _QueryResult(None)
        raise AssertionError(f"内存库未预期的 INSERT: {sql}")


class _ConnCtx:
    def __init__(self, db: _MemoryDb) -> None:
        self._db = db

    async def __aenter__(self) -> _MemoryConn:
        return _MemoryConn(self._db)

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _MemoryEngine:
    def __init__(self, db: _MemoryDb) -> None:
        self._db = db

    def begin(self) -> _ConnCtx:
        return _ConnCtx(self._db)

    def connect(self) -> _ConnCtx:
        return _ConnCtx(self._db)


class _MemoryDb:
    """app_state / ai_usage_monthly 内存存储（DAO 与 tracker 共享同一份）。"""

    def __init__(self) -> None:
        self.app_state: dict[str, str] = {}
        self.monthly: dict[tuple[str, str, str], dict[str, Any]] = {}

    @property
    def engine(self) -> _MemoryEngine:
        return _MemoryEngine(self)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_config(tmp_path):
    """独立配置文件 + 干净缓存（与 test_model_configuration_service 同语义）。"""
    config_file = tmp_path / "user_settings.json"
    with (
        patch.object(cfg_mod, "CONFIG_FILE", str(config_file)),
        patch.object(cfg_mod.ConfigHandler, "_config_cache", None),
    ):
        yield config_file


@pytest.fixture()
def config_service(isolated_config):
    cfg_mod.ConfigHandler.ensure_defaults()
    return ModelConfigurationService()


@pytest.fixture()
def wired_dispatch(config_service, monkeypatch):
    """scenario_dispatch 配置服务注入锚 → 真实服务（新鲜读临时配置文件）。"""
    monkeypatch.setattr("services.ai_service.scenario_dispatch._new_config_service", lambda: config_service)
    return config_service


@pytest.fixture(autouse=True)
def _reset_dispatch_provider():
    yield
    from services.ai_service import scenario_dispatch

    scenario_dispatch.set_scheduling_services_provider(None)


@pytest.fixture()
def memory_db() -> _MemoryDb:
    return _MemoryDb()


@pytest.fixture()
def tracker(memory_db) -> AIUsageTracker:
    return AIUsageTracker(engine=memory_db.engine, clock=lambda: _NOW)


@pytest.fixture()
def dao(memory_db) -> AIUsageDao:
    d = AIUsageDao.__new__(AIUsageDao)
    d.engine = memory_db.engine
    return d


# ---------------------------------------------------------------------------
# 磁盘配置 helpers
# ---------------------------------------------------------------------------


def _load_mm() -> dict[str, Any]:
    return dict(cfg_mod.ConfigHandler.read_model_management_fresh()["model_management"])


def _write_mm(mm: dict[str, Any]) -> None:
    config_file = Path(str(cfg_mod.CONFIG_FILE))
    raw = json.loads(config_file.read_text(encoding="utf-8"))
    raw["model_management"] = mm
    config_file.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    cfg_mod.ConfigHandler._clear_cache()


def _enable_new_chain_route(conn_id: str = "conn-new-1") -> None:
    """磁盘启用 news_risk 新链路由 + 连接（activate 后新链接管用）。"""
    mm = _load_mm()
    mm["connections"] = [
        {
            "connection_id": conn_id,
            "name": "新链连接",
            "adapter_kind": "litellm_chat",
            "provider": "deepseek",
            "model": "deepseek-chat",
            "api_base": "https://api.example.com",
            "billing": {"mode": "usage_only", "pricing_unit": "llm_tokens"},
        }
    ]
    for route in mm["scenario_routes"]:
        if route["scenario_id"] == "news_risk":
            route["enabled"] = True
            route["candidates"] = [conn_id]
    _write_mm(mm)


# ---------------------------------------------------------------------------
# 全流程状态机（s6_plan §2 假端点行）
# ---------------------------------------------------------------------------


class TestS6CutoverFullStateMachine:
    @pytest.mark.asyncio
    async def test_pending_draining_sealed_active_full_flow(
        self,
        wired_dispatch,
        config_service,
        tracker,
        dao,
        memory_db,
    ):
        """pending→draining（旧链停接、tracker 仍记账）→seal（写入口拒绝、基线冻结）
        →activate（新链接管、旧链拒绝），全程真实服务 + 内存账本。"""
        # —— 阶段 0：pending + 无 transition：旧链 allowed、新链不分派 ——
        assert await evaluate_legacy_admission() == "allowed"
        assert await resolve_executor("news_risk") is None
        assert await _resolve_news_risk_dispatch() is None  # 三入口 helper 放行旧链
        await tracker.add_cost_cents(250)
        assert memory_db.app_state["ai_cost:202610"] == "250"

        # —— 阶段 1：begin_migration → draining：旧链停接、tracker 写入仍允许 ——
        outcome = await config_service.begin_migration(_load_mm()["config_revision"], "op-begin", "owner-1")
        assert outcome.status == "committed"
        assert await evaluate_legacy_admission() == "draining_stop"
        with pytest.raises(LegacyDispatchBlockedError) as draining_exc:
            await _resolve_news_risk_dispatch()
        assert draining_exc.value.admission == "draining_stop"
        await tracker.add_cost_cents(130)  # 在途请求记账不受停接影响
        assert memory_db.app_state["ai_cost:202610"] == "380"

        # —— 阶段 2：DAO 封口 + seal_legacy → sealed ——
        record = await dao.seal_legacy_month(month=_MONTH_ISO, owner_session_id="owner-1", owner_epoch=1)
        assert record is not None
        assert record["month"] == _MONTH_ISO
        assert record["cost_cny"] == "3.80"  # 380 分 → 元 Decimal 字符串
        assert json.loads(memory_db.app_state[f"ai_legacy_seal:{_MONTH_COMPACT}"]) == record
        outcome = await config_service.seal_legacy(
            _load_mm()["config_revision"], "op-seal", {_MONTH_ISO: record["seal_id"]}, "owner-1"
        )
        assert outcome.status == "committed"
        assert _load_mm()["migration_transition"]["phase"] == "sealed"

        # sealed：tracker 写入口拒绝且 app_state 值不变（R21 不静默丢账）
        snapshot_before = dict(memory_db.app_state)
        with pytest.raises(LegacySealedError) as sealed_cost:
            await tracker.add_cost_cents(999)
        assert sealed_cost.value.month == _MONTH_ISO
        with pytest.raises(LegacySealedError) as sealed_unpriced:
            await tracker.add_unpriced(2, 500)
        assert sealed_unpriced.value.month == _MONTH_ISO
        assert memory_db.app_state == snapshot_before
        assert await tracker.get_month_cost_cents() == 380  # 基线冻结
        assert await dao.is_month_sealed(month=_MONTH_COMPACT) is True
        assert await dao.is_month_sealed(month="202609") is False  # 跨月未封口
        assert await evaluate_legacy_admission() == "refused"
        with pytest.raises(LegacyDispatchBlockedError) as sealed_dispatch:
            await _resolve_news_risk_dispatch()
        assert sealed_dispatch.value.admission == "refused"

        # —— 阶段 3：activate → active：新链接管、旧链准入 refused ——
        _enable_new_chain_route()
        executor = MagicMock()
        from services.ai_service import scenario_dispatch

        scenario_dispatch.set_scheduling_services_provider(lambda: {"executor": executor})
        outcome = await config_service.activate(_load_mm()["config_revision"], "op-activate")
        assert outcome.status == "committed"
        fresh = _load_mm()
        assert fresh["migration_state"] == "active"
        assert fresh["migration_transition"]["phase"] == "sealed"  # transition 保留作审计
        assert await evaluate_legacy_admission() == "refused"  # active 也拒绝旧链
        dispatch = await resolve_executor("news_risk")
        assert dispatch is not None
        assert dispatch.executor is executor
        assert [c["connection_id"] for c in dispatch.snapshot.routes[0]["candidates"]] == ["conn-new-1"]


# ---------------------------------------------------------------------------
# activate 失败保持 sealed + 重试恢复（s6_plan §2 服务三方法行 / 假端点行）
# ---------------------------------------------------------------------------


class TestActivateFailureRecovery:
    @pytest.mark.asyncio
    async def test_not_committed_and_commit_unknown_keep_sealed_then_retry_recovers(
        self,
        wired_dispatch,
        config_service,
        tracker,
        dao,
        monkeypatch,
    ):
        """CAS not_committed / 回执 commit_unknown 均维持 sealed 停接；重试 activate 幂等恢复。"""
        await config_service.begin_migration(_load_mm()["config_revision"], "op-begin", "owner-1")
        record = await dao.seal_legacy_month(month=_MONTH_ISO, owner_session_id="owner-1", owner_epoch=1)
        await config_service.seal_legacy(
            _load_mm()["config_revision"], "op-seal", {_MONTH_ISO: record["seal_id"]}, "owner-1"
        )

        real_cas = cfg_mod.ConfigHandler.compare_and_save_model_management

        def _failing_cas(expected_revision: int, payload: dict, operation_id: str, **kwargs: Any):
            if operation_id == "op-activate-flaky":
                return ModelManagementSaveResult(status="not_committed", current_revision=expected_revision)
            return real_cas(expected_revision, payload, operation_id, **kwargs)

        monkeypatch.setattr(
            cfg_mod.ConfigHandler,
            "compare_and_save_model_management",
            staticmethod(_failing_cas),
        )
        outcome = await config_service.activate(_load_mm()["config_revision"], "op-activate-flaky")
        assert outcome.status == "not_committed"
        fresh = _load_mm()
        assert fresh["migration_state"] == "pending"
        assert fresh["migration_transition"]["phase"] == "sealed"  # 停接保持
        assert await evaluate_legacy_admission() == "refused"
        with pytest.raises(LegacyDispatchBlockedError) as blocked:
            await _resolve_news_risk_dispatch()
        assert blocked.value.admission == "refused"
        with pytest.raises(LegacySealedError) as sealed_retry:
            await tracker.add_cost_cents(1)
        assert sealed_retry.value.month == _MONTH_ISO

        # 回执恢复：磁盘回执 op 与失败 op 不匹配 → commit_unknown（不臆断已提交）
        resolved = await config_service.resolve_save_outcome("op-activate-flaky")
        assert resolved.status == "commit_unknown"

        # 重试 activate（幂等：凭 sealed 锚）→ committed 恢复
        monkeypatch.setattr(
            cfg_mod.ConfigHandler,
            "compare_and_save_model_management",
            staticmethod(real_cas),
        )
        outcome = await config_service.activate(_load_mm()["config_revision"], "op-activate-retry")
        assert outcome.status == "committed"
        assert _load_mm()["migration_state"] == "active"


# ---------------------------------------------------------------------------
# admission 状态表（s6_plan §2 admission 行）
# ---------------------------------------------------------------------------


class TestLegacyAdmissionStateTable:
    @staticmethod
    def _patch_snapshot(monkeypatch, migration_state: str, transition: dict[str, Any] | None) -> MagicMock:
        snap = MagicMock(migration_state=migration_state, migration_transition=transition)
        svc = MagicMock()
        svc.get_snapshot = AsyncMock(return_value=snap)
        monkeypatch.setattr("services.ai_service.scenario_dispatch._new_config_service", lambda: svc)
        return svc

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("migration_state", "transition", "expected"),
        [
            ("pending", None, "allowed"),
            ("pending", _DRAINING_TRANSITION, "draining_stop"),
            ("pending", _SEALED_TRANSITION, "refused"),
            ("active", _SEALED_TRANSITION, "refused"),
            ("pending", {**_DRAINING_TRANSITION, "phase": "unknown_phase"}, "allowed"),  # 未知 phase 防御性放行
        ],
    )
    async def test_admission_state_table(self, monkeypatch, migration_state, transition, expected):
        self._patch_snapshot(monkeypatch, migration_state, transition)
        assert await evaluate_legacy_admission() == expected

    @pytest.mark.asyncio
    async def test_admission_reads_fresh_each_call(self, monkeypatch):
        svc = MagicMock()
        svc.get_snapshot = AsyncMock(
            side_effect=[
                MagicMock(migration_state="pending", migration_transition=None),
                MagicMock(migration_state="pending", migration_transition=_DRAINING_TRANSITION),
            ]
        )
        monkeypatch.setattr("services.ai_service.scenario_dispatch._new_config_service", lambda: svc)
        assert await evaluate_legacy_admission() == "allowed"
        assert await evaluate_legacy_admission() == "draining_stop"
        assert svc.get_snapshot.await_count == 2  # 每次新鲜读，不缓存


# ---------------------------------------------------------------------------
# 三入口 helper（s6_plan §2 三入口行）
# ---------------------------------------------------------------------------


class TestThreeEntryHelpers:
    _HELPERS = (_resolve_news_risk_dispatch, _resolve_news_annotation_dispatch, _resolve_stock_analysis_dispatch)

    @pytest.mark.asyncio
    async def test_none_and_allowed_returns_none_walks_legacy(self, monkeypatch):
        snap = MagicMock(migration_state="pending", migration_transition=None)
        svc = MagicMock()
        svc.get_snapshot = AsyncMock(return_value=snap)
        monkeypatch.setattr("services.ai_service.scenario_dispatch._new_config_service", lambda: svc)
        for helper in self._HELPERS:
            assert await helper() is None  # allowed → 走旧链（既有行为）

    @pytest.mark.asyncio
    async def test_none_and_draining_raises_blocked_not_fallback(self, monkeypatch):
        snap = MagicMock(migration_state="pending", migration_transition=_DRAINING_TRANSITION)
        svc = MagicMock()
        svc.get_snapshot = AsyncMock(return_value=snap)
        monkeypatch.setattr("services.ai_service.scenario_dispatch._new_config_service", lambda: svc)
        for helper in self._HELPERS:
            with pytest.raises(LegacyDispatchBlockedError) as excinfo:
                await helper()
            assert excinfo.value.admission == "draining_stop"  # 不降级返回 None（R21）

    @pytest.mark.asyncio
    async def test_dispatch_hit_skips_admission_check(self, monkeypatch):
        """dispatch 命中 → 原样返回，admission 判定不被评估（get_snapshot 仅分派点消费一次）。"""
        snap = MagicMock(
            migration_state="active",
            migration_transition=_SEALED_TRANSITION,
            connections=({"connection_id": "conn-1"},),
            scenario_routes=(
                {
                    "scenario_id": "news_risk",
                    "enabled": True,
                    "candidates": ["conn-1"],
                    "route_revision": 1,
                    "total_timeout_seconds": 60,
                    "task_routes": {},
                },
            ),
        )
        svc = MagicMock()
        svc.get_snapshot = AsyncMock(return_value=snap)
        monkeypatch.setattr("services.ai_service.scenario_dispatch._new_config_service", lambda: svc)
        from services.ai_service import scenario_dispatch

        scenario_dispatch.set_scheduling_services_provider(lambda: {"executor": MagicMock()})
        dispatch = await _resolve_news_risk_dispatch()
        assert dispatch is not None
        assert svc.get_snapshot.await_count == 1  # admission 未评估（命中即返回）


# ---------------------------------------------------------------------------
# tracker 写入口（s6_plan §2 tracker 行）
# ---------------------------------------------------------------------------


class TestTrackerSealGate:
    @pytest.mark.asyncio
    async def test_sealed_rejects_writes_and_freezes_baseline(self, tracker, dao, memory_db):
        await tracker.add_cost_cents(250)
        record = await dao.seal_legacy_month(month=_MONTH_ISO, owner_session_id="owner-1", owner_epoch=1)
        assert record is not None
        snapshot_before = dict(memory_db.app_state)

        with pytest.raises(LegacySealedError) as cost_exc:
            await tracker.add_cost_cents(100)
        assert cost_exc.value.month == _MONTH_ISO
        with pytest.raises(LegacySealedError) as unpriced_exc:
            await tracker.add_unpriced(1, 10)
        assert unpriced_exc.value.month == _MONTH_ISO

        assert memory_db.app_state == snapshot_before  # 封口行与成本键均不变
        assert await tracker.get_month_cost_cents() == 250
        assert await tracker.get_month_unpriced() == (0, 0)

    @pytest.mark.asyncio
    async def test_engine_none_noop_before_seal_check(self):
        """engine=None no-op 路径在封口检查之前：不注入引擎时写入静默跳过。"""
        t = AIUsageTracker(engine=None)
        await t.add_cost_cents(5)
        await t.add_unpriced(1, 1)
        assert t._engine is None

    @pytest.mark.asyncio
    async def test_unsealed_accumulates(self, tracker, memory_db):
        await tracker.add_cost_cents(250)
        await tracker.add_unpriced(3, 500)
        assert memory_db.app_state["ai_cost:202610"] == "250"
        assert memory_db.app_state["ai_unpriced_calls:202610"] == "3"
        assert memory_db.app_state["ai_unpriced_tokens:202610"] == "500"


# ---------------------------------------------------------------------------
# 尾写（s6_plan §2 尾写行）：legacy_tail 差额入账 / 未知差额不入账
# ---------------------------------------------------------------------------


class TestLegacyTailWrite:
    @pytest.mark.asyncio
    async def test_tail_delta_enters_budget_baseline_only_after_apply(self, tracker, dao, memory_db):
        await tracker.add_cost_cents(250)
        record = await dao.seal_legacy_month(month=_MONTH_ISO, owner_session_id="owner-1", owner_epoch=1)
        assert record is not None

        # 未知差额：未应用 apply_monthly_delta → 基线仅 legacy（无 legacy_tail 行）
        summary = await dao.read_budget_summary(month=_MONTH_COMPACT)
        assert summary["known_cny"] == Decimal("2.50")
        assert memory_db.monthly == {}

        # 已验证的已知差额经 legacy_tail 尾写 → 基线含差额
        await dao.apply_monthly_delta(
            month=_MONTH_COMPACT,
            aggregation_scope="scenario",
            grouping_key="legacy_tail",
            scenario_id="news_risk",
            trigger="legacy_tail",
            invocation_count=1,
            attempt_count=1,
            cost_cny_delta=Decimal("0.37"),
        )
        summary = await dao.read_budget_summary(month=_MONTH_COMPACT)
        assert summary["known_cny"] == Decimal("2.87")  # 2.50 legacy + 0.37 尾写
        assert memory_db.monthly[(_MONTH_COMPACT, "scenario", "legacy_tail")]["cost_cny"] == Decimal("0.37")


# ---------------------------------------------------------------------------
# ai_mixin 批量循环收敛（s6_plan §2 ai_mixin 循环行）
# ---------------------------------------------------------------------------


def _make_mock_dp() -> MagicMock:
    dp = MagicMock()
    dp.is_cancelled = MagicMock(return_value=False)
    dp.cache = MagicMock()
    dp.cache.get_concepts = AsyncMock(return_value={})
    dp.cache.quote_dao.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
    dp.cache.quote_dao.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
    dp.cache.quote_dao.get_top_list = AsyncMock(return_value=pd.DataFrame())
    dp.cache.quote_dao.get_northbound = AsyncMock(return_value=pd.DataFrame())
    dp.cache.get_top_inst_batch = AsyncMock(return_value=pd.DataFrame())
    dp.cache.prefetch_auxiliary_data = AsyncMock(return_value={})
    dp.cache.get_macro_economy = AsyncMock(return_value=None)
    dp.cache.get_shibor_latest = AsyncMock(return_value=None)
    dp.get_latest_trade_date = AsyncMock(return_value="20240118")
    dp.get_stock_history = AsyncMock(return_value=pd.DataFrame())
    return dp


class TestAiMixinLoopQuietStop:
    @pytest.mark.asyncio
    async def test_analyze_one_quiet_stop_on_draining(
        self,
        wired_dispatch,
        config_service,
        caplog,
    ):
        """draining 停接：analyze_one 安静跳过——_mixin_analyze_single 不被调用、
        不触发 on_card_error、final_rows 为空退回原始候选集。"""
        import logging

        from strategies.ai_mixin import AIStrategyMixin

        class _FlowStrategy(AIStrategyMixin):
            key = "s6_flow_strategy"

            def get_ai_context(self, row):
                return "ctx"

        await config_service.begin_migration(_load_mm()["config_revision"], "op-begin-loop", "owner-1")

        s = _FlowStrategy()
        dp = _make_mock_dp()
        candidates = pd.DataFrame({"ts_code": ["000001.SZ", "600000.SH"], "close": [10.0, 11.0]})
        card_errors: list[tuple[str, str]] = []
        context = {"data_processor": dp, "on_card_error": lambda name, msg: card_errors.append((name, msg))}

        with (
            caplog.at_level(logging.INFO),
            patch("strategies.ai_mixin.ConfigHandler.get_ai_max_concurrent_analysis", return_value=2),
            patch("strategies.ai_mixin.ConfigHandler.is_ai_external_acknowledged", return_value=True),
            patch("strategies.ai_mixin.ConfigHandler.get_llm_provider", return_value="deepseek"),
            patch("strategies.ai_mixin.NewsFetcher.get_stock_news", new=AsyncMock(return_value=[])),
            patch("strategies.ai_mixin.NewsFetcher.get_us_major_moves", new=AsyncMock(return_value="")),
            patch("strategies.ai_mixin.AIService") as mock_ai,
            patch.object(AIStrategyMixin, "_mixin_analyze_single", new=AsyncMock()) as analyze_single,
        ):
            mock_ai_instance = MagicMock()
            mock_ai_instance.is_cloud_available.return_value = True
            mock_ai.return_value = mock_ai_instance

            result = await s.run_ai_analysis(candidates, context)

        analyze_single.assert_not_awaited()  # 停接期未发起任何旧链调用
        assert card_errors == []  # 安静停接，不触发 on_card_error
        assert len(result) == 2  # final_rows 为空 → 退回原始候选集
        assert "admission=draining_stop" in caplog.text  # 停接日志携带 admission

    @pytest.mark.asyncio
    async def test_admission_read_failure_fails_open(
        self,
        isolated_config,
        monkeypatch,
        caplog,
    ):
        """admission 读取异常 fail-open：与三入口「分派失败降级旧链」同语义，
        继续走旧链分析（不停接、不触发 on_card_error）；CancelledError 不被吞（R2）。"""
        import logging

        from strategies.ai_mixin import AIStrategyMixin

        class _FlowStrategy(AIStrategyMixin):
            key = "s6_flow_strategy"

            def get_ai_context(self, row):
                return "ctx"

        broken_service = MagicMock()
        broken_service.get_snapshot = AsyncMock(side_effect=RuntimeError("config read failed"))
        monkeypatch.setattr("services.ai_service.scenario_dispatch._new_config_service", lambda: broken_service)

        s = _FlowStrategy()
        dp = _make_mock_dp()
        candidates = pd.DataFrame({"ts_code": ["000001.SZ", "600000.SH"], "close": [10.0, 11.0]})
        card_errors: list[tuple[str, str]] = []
        context = {"data_processor": dp, "on_card_error": lambda name, msg: card_errors.append((name, msg))}

        with (
            caplog.at_level(logging.DEBUG),
            patch("strategies.ai_mixin.ConfigHandler.get_ai_max_concurrent_analysis", return_value=2),
            patch("strategies.ai_mixin.ConfigHandler.is_ai_external_acknowledged", return_value=True),
            patch("strategies.ai_mixin.ConfigHandler.get_llm_provider", return_value="deepseek"),
            patch("strategies.ai_mixin.NewsFetcher.get_stock_news", new=AsyncMock(return_value=[])),
            patch("strategies.ai_mixin.NewsFetcher.get_us_major_moves", new=AsyncMock(return_value="")),
            patch("strategies.ai_mixin.AIService") as mock_ai,
            patch.object(
                AIStrategyMixin,
                "_mixin_analyze_single",
                new=AsyncMock(return_value={"score": 75, "summary": "ok", "confidence": 80, "ai_status": "analyzed"}),
            ) as analyze_single,
        ):
            mock_ai_instance = MagicMock()
            mock_ai_instance.is_cloud_available.return_value = True
            mock_ai.return_value = mock_ai_instance

            result = await s.run_ai_analysis(candidates, context)

        assert analyze_single.await_count == 2  # fail-open：每股继续走旧链分析
        assert card_errors == []  # 读取失败不转化为卡片错误
        assert "ai_score" in result.columns  # 旧链分析结果正常聚合
        assert "legacy admission read failed" in caplog.text  # 降级日志可观测
