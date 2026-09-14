# pyright: reportArgumentType=false, reportOptionalMemberAccess=false
# 本文件含 mock/monkey-patch 模式，pyright 无法验证替身与生产类型兼容性，
# 统一在此文件局部禁用相关告警，测试行为由测试用例本身验证。

"""ui/viewmodels/egress_audit_view_model.py 单测 (SEC-03 数据出口面板).

覆盖：
1. load_aggregates 成功：今日/本月聚合 → 行列表（按次数降序）+ 会话计数 + loaded=True
2. 空聚合 → 空行列表 + loaded=True
3. 聚合读取异常 → 降级空态（loaded=False），不抛异常
4. R2：asyncio.CancelledError 必须传播
5. _to_rows 排序语义
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


class TestLoadAggregates:
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

    @pytest.mark.asyncio
    async def test_empty_aggregates_sets_loaded_true(self, monkeypatch) -> None:
        audit = EgressAudit()
        monkeypatch.setattr(audit, "get_aggregates", AsyncMock(return_value=({}, {})))
        monkeypatch.setattr(audit, "get_session_egress_count", lambda: 0)

        vm = _make_vm()
        await vm.load_aggregates()

        assert vm.state.loaded is True
        assert vm.state.today_rows == []
        assert vm.state.month_rows == []
        assert vm.state.session_count == 0

    @pytest.mark.asyncio
    async def test_exception_degrades_to_empty(self, monkeypatch) -> None:
        audit = EgressAudit()
        monkeypatch.setattr(
            audit,
            "get_aggregates",
            AsyncMock(side_effect=OSError("jsonl unreadable")),
        )

        vm = _make_vm()
        await vm.load_aggregates()  # 不抛异常

        assert vm.state.loaded is False
        assert vm.state.today_rows == []
        assert vm.state.month_rows == []
        assert vm.state.session_count == 0

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
        assert _to_rows({}) == []
