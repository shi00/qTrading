# pyright: reportArgumentType=false, reportOptionalMemberAccess=false
# 本文件含 mock/monkey-patch 模式，pyright 无法验证替身与生产类型兼容性，
# 统一在此文件局部禁用相关告警，测试行为由测试用例本身验证。

"""ui/viewmodels/egress_audit_view_model.py 单测 (SEC-03 数据出口面板).

覆盖：
1. load_aggregates 成功：今日/本月聚合 → 行元组（按次数降序）+ 会话计数 + loaded=True
2. 空聚合 → 空行元组 + loaded=True
3. 聚合读取异常 → 降级空态（loaded=False），不抛异常
4. R2：asyncio.CancelledError 必须传播
5. _to_rows 排序语义
6. state 行容器不可变：初始/成功/异常均为 tuple，且不暴露 append/clear 原地修改通道
7. 重新加载生成新快照，不改变已持有的旧快照
"""

from __future__ import annotations

import asyncio

import pytest
from unittest.mock import AsyncMock

from ui.viewmodels.egress_audit_view_model import EgressAuditViewModel, _to_rows
from utils.egress_audit import EgressAudit

pytestmark = pytest.mark.unit


def _make_vm() -> EgressAuditViewModel:
    vm = EgressAuditViewModel()
    return vm


def _assert_immutable_rows(rows: object) -> None:
    """断言行容器为 tuple，且不存在 append/clear 原地修改通道。"""
    assert isinstance(rows, tuple)
    for method in ("append", "clear"):
        assert not hasattr(rows, method), f"行容器不应暴露原地修改方法 {method}"
        with pytest.raises(AttributeError) as exc_info:
            getattr(rows, method)
        # 异常消息需指名缺失的方法，并表明行容器仍是不可变的 tuple
        assert method in str(exc_info.value)
        assert "tuple" in str(exc_info.value)


class TestLoadAggregates:
    def test_initial_state_rows_are_immutable_tuples(self) -> None:
        vm = _make_vm()

        assert vm.state.loaded is False
        assert vm.state.today_rows == ()
        assert vm.state.month_rows == ()
        _assert_immutable_rows(vm.state.today_rows)
        _assert_immutable_rows(vm.state.month_rows)

    @pytest.mark.asyncio
    async def test_success_populates_rows(self, monkeypatch) -> None:
        audit = EgressAudit()
        monkeypatch.setattr(
            audit,
            "get_aggregates",
            AsyncMock(
                return_value=(
                    {"llm:deepseek/deepseek-v4-flash": (3, 300), "llm:qwen/qwen-max": (1, 50)},
                    {"llm:deepseek/deepseek-v4-flash": (5, 900)},
                )
            ),
        )
        monkeypatch.setattr(audit, "get_session_egress_count", lambda: 7)

        vm = _make_vm()
        await vm.load_aggregates()

        assert vm.state.loaded is True
        assert vm.state.session_count == 7
        # 今日按次数降序：deepseek(3) 在前，qwen(1) 在后
        assert [r.destination for r in vm.state.today_rows] == [
            "llm:deepseek/deepseek-v4-flash",
            "llm:qwen/qwen-max",
        ]
        assert vm.state.today_rows[0].count == 3
        assert vm.state.today_rows[0].payload_size_bytes == 300
        assert [r.destination for r in vm.state.month_rows] == ["llm:deepseek/deepseek-v4-flash"]
        assert vm.state.month_rows[0].payload_size_bytes == 900
        _assert_immutable_rows(vm.state.today_rows)
        _assert_immutable_rows(vm.state.month_rows)

    @pytest.mark.asyncio
    async def test_reload_keeps_previous_snapshot_unchanged(self, monkeypatch) -> None:
        audit = EgressAudit()
        get_aggregates = AsyncMock(
            return_value=(
                {"llm:a/a": (2, 20), "llm:b/b": (5, 50)},
                {"llm:a/a": (3, 30)},
            )
        )
        monkeypatch.setattr(audit, "get_aggregates", get_aggregates)
        monkeypatch.setattr(audit, "get_session_egress_count", lambda: 2)

        vm = _make_vm()
        await vm.load_aggregates()
        previous_state = vm.state
        previous_today = previous_state.today_rows
        previous_month = previous_state.month_rows

        assert [r.destination for r in previous_today] == ["llm:b/b", "llm:a/a"]
        assert [r.destination for r in previous_month] == ["llm:a/a"]

        get_aggregates.return_value = (
            {"llm:c/c": (9, 900)},
            {"llm:c/c": (9, 900)},
        )
        await vm.load_aggregates()

        assert vm.state is not previous_state
        assert [r.destination for r in vm.state.today_rows] == ["llm:c/c"]
        assert [r.destination for r in vm.state.month_rows] == ["llm:c/c"]
        # 旧快照保留原内容，且不与新快照共享行容器
        assert previous_state.today_rows == previous_today
        assert previous_state.month_rows == previous_month
        assert previous_state.today_rows is not vm.state.today_rows
        assert previous_state.month_rows is not vm.state.month_rows

    @pytest.mark.asyncio
    async def test_empty_aggregates_sets_loaded_true(self, monkeypatch) -> None:
        audit = EgressAudit()
        monkeypatch.setattr(audit, "get_aggregates", AsyncMock(return_value=({}, {})))
        monkeypatch.setattr(audit, "get_session_egress_count", lambda: 0)

        vm = _make_vm()
        await vm.load_aggregates()

        assert vm.state.loaded is True
        assert vm.state.today_rows == ()
        assert vm.state.month_rows == ()
        assert vm.state.session_count == 0
        _assert_immutable_rows(vm.state.today_rows)
        _assert_immutable_rows(vm.state.month_rows)

    @pytest.mark.asyncio
    async def test_exception_degrades_to_empty(self, monkeypatch, caplog):
        audit = EgressAudit()
        monkeypatch.setattr(
            audit,
            "get_aggregates",
            AsyncMock(side_effect=OSError("jsonl unreadable: C:\\Users\\secret\\egress.jsonl")),
        )

        vm = _make_vm()
        import logging

        with caplog.at_level(logging.DEBUG, logger="ui.viewmodels.egress_audit_view_model"):
            await vm.load_aggregates()  # 不抛异常

        assert vm.state.loaded is False
        assert vm.state.today_rows == ()
        assert vm.state.month_rows == ()
        assert vm.state.session_count == 0
        _assert_immutable_rows(vm.state.today_rows)
        _assert_immutable_rows(vm.state.month_rows)
        # R9: debug 日志中的异常消息经 DataSanitizer 脱敏, 原始路径不泄露
        assert "C:\\Users\\secret" not in caplog.text
        assert "<PATH>" in caplog.text

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self, monkeypatch) -> None:
        audit = EgressAudit()
        monkeypatch.setattr(
            audit,
            "get_aggregates",
            AsyncMock(side_effect=asyncio.CancelledError()),
        )

        vm = _make_vm()
        with pytest.raises(asyncio.CancelledError) as excinfo:
            await vm.load_aggregates()  # R2: 必须传播
        assert excinfo.type is asyncio.CancelledError


class TestToRows:
    def test_sort_by_count_desc(self) -> None:
        rows = _to_rows({"llm:a/a": (1, 10), "llm:b/b": (5, 50)})
        assert [r.destination for r in rows] == ["llm:b/b", "llm:a/a"]

    def test_empty_map(self) -> None:
        assert _to_rows({}) == ()
        _assert_immutable_rows(_to_rows({}))

    def test_returns_tuple(self) -> None:
        rows = _to_rows({"llm:a/a": (1, 10), "llm:b/b": (5, 50)})
        assert isinstance(rows, tuple)
        _assert_immutable_rows(rows)
