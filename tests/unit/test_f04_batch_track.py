# pyright: reportAttributeAccessIssue=false, reportArgumentType=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 动态属性访问 与 参数类型不兼容
# （mock/stub/monkey-patch）。pyright 无法验证替身类与生产类型的兼容性，统一在
# 此文件局部禁用相关告警，测试行为由测试用例本身验证。

"""F04 回归：策略批量入口在 active 后跳过全部待分析股票。

批次入口先定执行轨道并冻结 run 快照（同请求单轨，模型统一.md §14.2-6）：
- 新轨（resolve_analysis_track 返回 dispatch）：跳过旧云端可用性与旧链准入 guard，
  冻结快照整批透传（批次中保存配置不混版）；
- 旧轨（None）：保留既有 is_cloud_available / ack / 逐股准入 guard；
- 明确阻断（LegacyDispatchBlockedError：draining_stop/refused/read_failed）：
  逐行显式标记 ai_status="legacy_stopped"，不得伪装成筛选结果（R21）。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from core.model_management_types import LegacyDispatchBlockedError
from strategies.ai_mixin import AIStrategyMixin

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _mock_ai_external_acknowledged_default_true():
    """默认所有测试视为已确认 AI 外发政策（与 test_ai_mixin 同构）——
    旧轨 ack guard 非本文件被测对象。"""
    with (
        patch(
            "strategies.ai_mixin.ConfigHandler.is_ai_external_acknowledged",
            return_value=True,
        ),
        patch(
            "strategies.ai_mixin.ConfigHandler.get_llm_provider",
            return_value="deepseek",
        ),
    ):
        yield


@pytest.fixture(autouse=True)
def _legacy_admission_allowed(monkeypatch):
    """旧链批量测试前提显式化（F05 fail-closed）：准入读取必须成功且为 allowed
    （pending 未开启迁移）才走旧链。专门测试阻断/新轨的用例在用例内覆盖本打桩。"""
    from services.ai_service import scenario_dispatch

    config_service = MagicMock()
    config_service.get_snapshot = AsyncMock(
        return_value=MagicMock(migration_state="pending", migration_transition=None, scenario_routes=())
    )
    monkeypatch.setattr(scenario_dispatch, "_new_config_service", lambda: config_service)


def _make_mock_dp(*, is_cancelled: bool = False, trade_date: str | None = None) -> MagicMock:
    """创建完整的 mock DataProcessor（与 test_ai_mixin 同构）。"""
    dp = MagicMock()
    dp.is_cancelled = MagicMock(return_value=is_cancelled)
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
    dp.get_latest_trade_date = AsyncMock(return_value=trade_date or "20240118")
    dp.get_stock_history = AsyncMock(return_value=pd.DataFrame())
    return dp


class _ConcreteStrategy(AIStrategyMixin):
    key = "test_strategy"

    def get_ai_context(self, row):
        return f"Test context for {row.get('ts_code', '?')}"


def _make_candidates(n: int = 2) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": [f"00000{i}.SZ" for i in range(1, n + 1)],
            "name": [f"stock{i}" for i in range(1, n + 1)],
            "close": [10.0] * n,
        }
    )


class TestF04BatchTrackFreeze:
    """F04：批次入口轨道判定与冻结快照透传。"""

    @pytest.mark.asyncio
    async def test_active_new_track_analyzes_without_legacy_guards(self):
        """active 新轨：仅统一连接（旧云端不可用）也能分析——批次入口分派命中后
        跳过 is_cloud_available / 逐股旧链准入，冻结快照 identity 透传到
        analyze_stock，且分派点唯一（批次中途不重新判定，不混版）。"""
        s = _ConcreteStrategy()
        dp = _make_mock_dp()
        candidates = _make_candidates(2)
        on_progress = MagicMock()
        context = {"data_processor": dp, "on_progress": on_progress}

        frozen_dispatch = object()  # 哨兵：identity 断言批次冻结快照
        resolve_calls: list[int] = []

        async def fake_resolve_analysis_track():
            resolve_calls.append(1)
            return frozen_dispatch

        captured_kwargs: dict = {}

        async def mock_analyze(stock_info, *args, **kwargs):
            captured_kwargs.update(kwargs)
            return {"score": 50, "summary": "ok", "ai_status": "analyzed"}

        with (
            patch("strategies.ai_mixin.ConfigHandler.is_ai_local_only_mode", return_value=False),
            patch("strategies.ai_mixin.AIService") as mock_ai,
            patch("strategies.ai_mixin.NewsFetcher.get_stock_news", new=AsyncMock(return_value=[])),
            patch("strategies.ai_mixin.NewsFetcher.get_us_major_moves", new=AsyncMock(return_value="")),
        ):
            mock_ai_instance = MagicMock()
            # 仅配置统一连接的用户：旧链云端不可用（缺陷场景下会整批跳过）
            mock_ai_instance.is_cloud_available.return_value = False
            mock_ai_instance.analyze_stock = AsyncMock(side_effect=mock_analyze)
            mock_ai.return_value = mock_ai_instance

            with patch(
                "services.ai_service.scenario_dispatch.resolve_analysis_track",
                new=fake_resolve_analysis_track,
            ):
                result = await s.run_ai_analysis(candidates, context)

        # 候选不再被安静丢弃：2 股均被分析
        assert mock_ai_instance.analyze_stock.await_count == 2
        # 分派点唯一：批次入口一次判定，逐股不重判（冻结快照，批次中保存配置不混版）
        assert len(resolve_calls) == 1
        # 冻结快照 identity 透传（analyze_stock 不再自行新鲜分派）
        assert captured_kwargs.get("scenario_dispatch") is frozen_dispatch
        # 结果行带 analyzed 状态（而非 math-only 裸返回）
        assert set(result["ai_status"]) == {"analyzed"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("admission", ["draining_stop", "refused", "read_failed"])
    async def test_blocked_at_entry_marks_legacy_stopped(self, admission):
        """明确阻断（draining/refused/read_failed）：逐行显式标记 legacy_stopped，
        不发起任何分析调用，不伪装成筛选结果（R21）；on_progress 通知停接。"""
        s = _ConcreteStrategy()
        dp = _make_mock_dp()
        candidates = _make_candidates(3)
        on_progress = MagicMock()
        context = {"data_processor": dp, "on_progress": on_progress}

        async def fake_resolve_analysis_track():
            raise LegacyDispatchBlockedError(admission)

        with (
            patch("strategies.ai_mixin.AIService") as mock_ai,
            patch("strategies.ai_mixin.NewsFetcher.get_stock_news", new=AsyncMock(return_value=[])),
            patch("strategies.ai_mixin.NewsFetcher.get_us_major_moves", new=AsyncMock(return_value="")),
        ):
            mock_ai_instance = MagicMock()
            mock_ai_instance.is_cloud_available.return_value = True
            mock_ai_instance.analyze_stock = AsyncMock()
            mock_ai.return_value = mock_ai_instance

            with patch(
                "services.ai_service.scenario_dispatch.resolve_analysis_track",
                new=fake_resolve_analysis_track,
            ):
                result = await s.run_ai_analysis(candidates, context)

        # 不发起任何分析调用
        mock_ai_instance.analyze_stock.assert_not_awaited()
        # 全量行保留且显式标记（不是静默丢弃/裸 math-only）
        assert len(result) == 3
        assert (result["ai_status"] == "legacy_stopped").all()
        assert result["ai_score"].isna().all()
        # on_progress 通知停接原因（ai_legacy_dispatch_stopped）
        progress_messages = [call.args[2] for call in on_progress.call_args_list]
        assert any(getattr(m, "key", None) == "ai_legacy_dispatch_stopped" for m in progress_messages)

    @pytest.mark.asyncio
    async def test_legacy_track_cloud_unavailable_unchanged(self):
        """旧轨回归：pending（分派未命中）+ 旧云端不可用 → 维持 math-only 返回，
        轨道判定不破坏既有「未配置」语义。"""
        s = _ConcreteStrategy()
        dp = _make_mock_dp()
        candidates = _make_candidates(1)
        context = {"data_processor": dp}

        async def fake_resolve_analysis_track():
            return None

        with (
            patch("strategies.ai_mixin.AIService") as mock_ai,
            patch("strategies.ai_mixin.ConfigHandler.is_ai_local_only_mode", return_value=False),
        ):
            mock_ai_instance = MagicMock()
            mock_ai_instance.is_cloud_available.return_value = False
            mock_ai_instance.analyze_stock = AsyncMock()
            mock_ai.return_value = mock_ai_instance

            with patch(
                "services.ai_service.scenario_dispatch.resolve_analysis_track",
                new=fake_resolve_analysis_track,
            ):
                result = await s.run_ai_analysis(candidates, context)

        # math-only：无 AI 分析调用、无 ai_status 标记列
        mock_ai_instance.analyze_stock.assert_not_awaited()
        assert len(result) == 1
        assert "ai_status" not in result.columns

    @pytest.mark.asyncio
    async def test_legacy_track_draining_stops_per_stock(self):
        """旧轨 draining 停接语义保留：批次已进入旧轨后逐股新鲜读准入，
        draining_stop 安静停接（不触发 on_card_error），无行丢失伪装。"""
        s = _ConcreteStrategy()
        dp = _make_mock_dp()
        candidates = _make_candidates(2)
        context = {"data_processor": dp}

        async def fake_resolve_analysis_track():
            return None

        with (
            patch("strategies.ai_mixin.AIService") as mock_ai,
            patch("strategies.ai_mixin.NewsFetcher.get_stock_news", new=AsyncMock(return_value=[])),
            patch("strategies.ai_mixin.NewsFetcher.get_us_major_moves", new=AsyncMock(return_value="")),
        ):
            mock_ai_instance = MagicMock()
            mock_ai_instance.is_cloud_available.return_value = True
            mock_ai_instance.analyze_stock = AsyncMock()
            mock_ai.return_value = mock_ai_instance

            # 排水期快照：migration_transition.phase=draining → evaluate_legacy_admission
            # 返回 draining_stop（经真实判定逻辑，非直接 patch）
            from services.ai_service import scenario_dispatch

            config_service = MagicMock()
            config_service.get_snapshot = AsyncMock(
                return_value=MagicMock(
                    migration_state="pending",
                    migration_transition={"phase": "draining"},
                    scenario_routes=(),
                )
            )

            with (
                patch(
                    "services.ai_service.scenario_dispatch.resolve_analysis_track",
                    new=fake_resolve_analysis_track,
                ),
                patch.object(scenario_dispatch, "_new_config_service", lambda: config_service),
            ):
                result = await s.run_ai_analysis(candidates, context)

        # 逐股停接：无分析调用；停接行被安静丢弃后 final_rows 为空 → 退回候选集
        mock_ai_instance.analyze_stock.assert_not_awaited()
        assert len(result) == 2
        assert "ai_status" not in result.columns


class TestF04AnalyzeStockDispatchPassthrough:
    """F04：StockAnalysisService.analyze_stock 冻结快照透传契约。"""

    @staticmethod
    def _make_service() -> object:
        from services.ai_service import AIService
        from services.ai_service.stock_analysis import StockAnalysisService

        svc = AIService.__new__(AIService)
        svc._is_cloud_configured = True
        svc._litellm_config = {"api_key": "test-key"}
        svc._local_model_loaded = False
        svc._supports_reasoning = False
        svc._initialized = True
        svc._stock_analysis = StockAnalysisService(svc)
        return svc

    @pytest.mark.asyncio
    async def test_frozen_dispatch_used_without_re_resolve(self):
        """传入批次冻结快照：直接复用，不逐股新鲜分派；新轨不要求旧云端可用。"""
        svc = self._make_service()

        frozen_dispatch = SimpleNamespace(
            executor=MagicMock(),
            snapshot=SimpleNamespace(config_revision=1),
            route_revision=1,
            total_timeout_seconds=1.0,
            task_routes={},
        )
        # 新链成功出口（F03 协议）：ScenarioOutput(output, metadata)
        from services.ai_service.scenario_executor import ScenarioOutput

        exec_result = SimpleNamespace(
            status="succeeded",
            outputs=[ScenarioOutput(output={"score": 60, "confidence": 70}, metadata={"actual_model": "m"})],
            candidate_decisions=[SimpleNamespace(decision="succeeded", connection_id="conn-1")],
            actual_usage=None,
        )
        frozen_dispatch.executor.execute = AsyncMock(return_value=exec_result)

        with patch(
            "services.ai_service.stock_analysis._resolve_stock_analysis_dispatch",
            new=AsyncMock(side_effect=AssertionError("批次冻结快照下不得重新分派")),
        ) as mock_resolve:
            result = await svc._stock_analysis.analyze_stock(
                {"ts_code": "000001.SZ", "name": "平安银行"},
                {},
                [],
                include_learning_context=False,  # 避免单测触发真实学习上下文读取
                scenario_dispatch=frozen_dispatch,  # type: ignore[arg-type]  # SimpleNamespace 替代 frozen dataclass 测试替身
            )

        mock_resolve.assert_not_awaited()
        # 命中新链执行分支：程序覆写 execution_metadata 权威（F03），分数来自 ScenarioOutput
        assert result["score"] == 60
        assert result["execution_metadata"]["ai_status"] == "succeeded"
        assert result["execution_metadata"]["config_revision"] == 1

    @pytest.mark.asyncio
    async def test_none_dispatch_self_resolves(self):
        """未传快照（单股直连 / retry 等独立请求）：保持自行新鲜分派语义。"""
        svc = self._make_service()

        resolve_calls: list[int] = []

        async def fake_resolve():
            resolve_calls.append(1)
            return None

        svc.is_cloud_available = MagicMock(return_value=False)

        with (
            patch(
                "services.ai_service.stock_analysis._resolve_stock_analysis_dispatch",
                new=fake_resolve,
            ),
        ):
            result = await svc._stock_analysis.analyze_stock(
                {"ts_code": "000001.SZ", "name": "平安银行"},
                {},
                [],
            )

        assert len(resolve_calls) == 1
        assert result["ai_status"] == "unsupported"  # dispatch None + 云端不可用 → 显式能力边界

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates_from_track_resolution(self):
        """R2：轨道判定中的 CancelledError 不被吞没，裸传播（配合优雅停机）。"""
        progress_messages: list[object] = []
        resolve_calls: list[int] = []

        async def fake_resolve():
            resolve_calls.append(1)
            raise asyncio.CancelledError

        s = _ConcreteStrategy()
        dp = _make_mock_dp()
        candidates = _make_candidates(1)
        context = {
            "data_processor": dp,
            "on_progress": lambda cur, total, msg: progress_messages.append(msg),
        }

        with (
            patch("strategies.ai_mixin.AIService"),
            patch(
                "services.ai_service.scenario_dispatch.resolve_analysis_track",
                new=fake_resolve,
            ),
        ):
            with pytest.raises(asyncio.CancelledError) as exc_info:
                await s.run_ai_analysis(candidates, context)

        # 精确类型断言：传播的是 CancelledError 本身（未被替换、未包装为其他异常）
        assert type(exc_info.value) is asyncio.CancelledError
        # 取消确实发生在轨道判定点（而非上游短路漏测）
        assert len(resolve_calls) == 1
        # 取消未落入任何降级分支：无 legacy_stopped / policy_not_acknowledged /
        # math-only 进度通知，批次被取消而非「跑完」
        assert progress_messages == []
