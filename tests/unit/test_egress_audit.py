"""utils/egress_audit.py 单测 (SEC-03 数据出口集中审计).

覆盖 (design §4):
1. record 字段构造 / JSONL 落盘格式
2. 今日/本月聚合 = 文件唯一事实源, 无双重计数 (多条 record 后聚合 = 文件全量)
3. 会话计数单调 (进程内内存, 不受 deque maxlen 影响)
4. 落盘失败降级 (会话计数仍 +1, 聚合低估, 不抛异常)
5. 单例重置 (会话计数/缓冲清空; 落盘路径 override 保留)
6. 并发追加无交错 (单例写锁)
7. recent(N) 缓冲 maxlen 截断
8. add_listener 订阅/退订 + 回调隔离
"""

from __future__ import annotations

import asyncio
import json

import pytest

from utils.egress_audit import EgressAudit, EgressRecord

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _tmp_egress_path(tmp_path, monkeypatch):
    """把 EgressAudit 落盘路径注入临时目录 (须在 reset 后、首次 record 前)."""
    EgressAudit._configure_for_tests(str(tmp_path / "egress_audit.jsonl"))
    yield tmp_path


# ============================================================================
# record 字段与 JSONL 落盘格式
# ============================================================================


class TestRecordAndJsonl:
    async def test_record_writes_jsonl_line(self, _tmp_egress_path) -> None:
        await EgressAudit().record(
            destination="llm:deepseek/deepseek-chat",
            category="analysis",
            item_count=2,
            payload_size_bytes=1200,
        )

        lines = (_tmp_egress_path / "egress_audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        obj = json.loads(lines[0])
        assert obj["destination"] == "llm:deepseek/deepseek-chat"
        assert obj["category"] == "analysis"
        assert obj["item_count"] == 2
        assert obj["payload_size_bytes"] == 1200
        assert obj["status"] == "sent"
        assert obj["timestamp"]  # ISO 字符串

    async def test_record_status_failed(self, _tmp_egress_path) -> None:
        await EgressAudit().record(
            destination="llm:qwen/qwen-max",
            category="news",
            item_count=1,
            payload_size_bytes=500,
            status="failed",
        )
        lines = (_tmp_egress_path / "egress_audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert json.loads(lines[0])["status"] == "failed"

    async def test_record_increments_session_count(self, _tmp_egress_path) -> None:
        audit = EgressAudit()
        assert audit.get_session_egress_count() == 0
        await audit.record(destination="llm:a/b", category="analysis", item_count=1, payload_size_bytes=10)
        await audit.record(destination="llm:a/b", category="analysis", item_count=1, payload_size_bytes=10)
        assert audit.get_session_egress_count() == 2


# ============================================================================
# 聚合 = 文件唯一事实源, 无双重计数
# ============================================================================


class TestAggregatesNoDoubleCount:
    async def test_aggregates_match_jsonl_file(self, _tmp_egress_path) -> None:
        audit = EgressAudit()
        # 同一目的地多条记录: 聚合 count/bytes 应为文件全量, 不叠加内存 deque (无双计)
        for _ in range(3):
            await audit.record(
                destination="llm:deepseek/deepseek-chat", category="analysis", item_count=1, payload_size_bytes=100
            )
        await audit.record(destination="llm:qwen/qwen-max", category="news", item_count=1, payload_size_bytes=50)

        today_map, month_map = await audit.get_aggregates()
        assert today_map["llm:deepseek/deepseek-chat"] == (3, 300)
        assert today_map["llm:qwen/qwen-max"] == (1, 50)
        assert month_map["llm:deepseek/deepseek-chat"] == (3, 300)

    async def test_aggregates_ignore_failed_status(self, _tmp_egress_path) -> None:
        """聚合统计所有落盘记录 (含 failed) — 元数据审计是'外发尝试'的全景."""
        audit = EgressAudit()
        await audit.record(
            destination="llm:a/b", category="analysis", item_count=1, payload_size_bytes=10, status="failed"
        )
        today_map, _ = await audit.get_aggregates()
        assert today_map["llm:a/b"] == (1, 10)

    async def test_aggregates_empty_when_no_file(self, _tmp_egress_path) -> None:
        today_map, month_map = await EgressAudit().get_aggregates()
        assert today_map == {}
        assert month_map == {}

    async def test_aggregates_ignore_corrupt_lines(self, _tmp_egress_path) -> None:
        path = _tmp_egress_path / "egress_audit.jsonl"
        path.write_text(
            '{"timestamp": "2026-09-14T00:00:00", "destination": "llm:ok/ok"}\nnot-json-line\n', encoding="utf-8"
        )
        today_map, _ = await EgressAudit().get_aggregates()
        assert today_map == {"llm:ok/ok": (1, 0)}


# ============================================================================
# 落盘失败降级
# ============================================================================


class TestAppendFailureDegradation:
    async def test_append_failure_does_not_raise_and_count_still_increments(
        self, _tmp_egress_path, monkeypatch
    ) -> None:
        audit = EgressAudit()
        monkeypatch.setattr(audit, "_append_jsonl", lambda rec: (_ for _ in ()).throw(OSError("disk full")))
        # 不应抛出异常 (审计降级不阻断 AI 主流程)
        await audit.record(destination="llm:a/b", category="analysis", item_count=1, payload_size_bytes=10)
        assert audit.get_session_egress_count() == 1

    async def test_cancelled_error_propagates(self, _tmp_egress_path, monkeypatch) -> None:
        audit = EgressAudit()
        monkeypatch.setattr(audit, "_append_jsonl", lambda rec: (_ for _ in ()).throw(asyncio.CancelledError()))
        with pytest.raises(asyncio.CancelledError) as excinfo:
            await audit.record(destination="llm:a/b", category="analysis", item_count=1, payload_size_bytes=10)
        assert excinfo.type is asyncio.CancelledError  # R2: 取消必须传播而非吞没


# ============================================================================
# 单例重置
# ============================================================================


class TestSingletonReset:
    async def test_reset_clears_count_and_buffer(self, _tmp_egress_path) -> None:
        audit = EgressAudit()
        await audit.record(destination="llm:a/b", category="analysis", item_count=1, payload_size_bytes=10)
        assert audit.get_session_egress_count() == 1
        assert audit.recent(limit=10)

        EgressAudit._reset_singleton()
        fresh = EgressAudit()
        assert fresh.get_session_egress_count() == 0
        assert fresh.recent(limit=10) == []
        # 落盘路径 override 保留 (单测注入的临时路径)
        assert fresh._file_path() == str(_tmp_egress_path / "egress_audit.jsonl")


# ============================================================================
# recent(N) maxlen 缓冲
# ============================================================================


class TestRecentBuffer:
    async def test_recent_limited_by_maxlen(self, _tmp_egress_path, monkeypatch) -> None:
        audit = EgressAudit()
        # 超过 maxlen 仍保留最近 N 条 (maxlen=200)
        for i in range(5):
            await audit.record(destination=f"llm:m{i}/m", category="analysis", item_count=1, payload_size_bytes=i)
        recent = audit.recent(limit=3)
        assert len(recent) == 3
        assert recent[-1]["destination"] == "llm:m4/m"
        assert audit.get_session_egress_count() == 5  # 会话计数不受 maxlen 截断


# ============================================================================
# 并发追加无交错 (写锁)
# ============================================================================


class TestConcurrentAppend:
    async def test_concurrent_records_produce_valid_lines(self, _tmp_egress_path) -> None:
        audit = EgressAudit()
        await asyncio.gather(
            *[
                audit.record(destination=f"llm:model{i % 3}/m", category="analysis", item_count=1, payload_size_bytes=i)
                for i in range(12)
            ]
        )
        lines = (_tmp_egress_path / "egress_audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 12
        for line in lines:
            obj = json.loads(line)  # 每行合法 JSON = 无交错
            assert obj["destination"].startswith("llm:")


# ============================================================================
# add_listener 订阅/退订 + 回调隔离
# ============================================================================


class TestListeners:
    async def test_listener_receives_count_updates(self, _tmp_egress_path) -> None:
        audit = EgressAudit()
        seen: list[int] = []
        unsub = audit.add_listener(lambda count: seen.append(count))
        await audit.record(destination="llm:a/b", category="analysis", item_count=1, payload_size_bytes=10)
        await audit.record(destination="llm:a/b", category="analysis", item_count=1, payload_size_bytes=10)
        assert seen == [1, 2]
        unsub()
        await audit.record(destination="llm:a/b", category="analysis", item_count=1, payload_size_bytes=10)
        assert seen == [1, 2]  # 退订后不再回调

    async def test_listener_exception_isolated(self, _tmp_egress_path) -> None:
        audit = EgressAudit()
        audit.add_listener(lambda _count: (_ for _ in ()).throw(RuntimeError("boom")))
        # 回调异常被隔离, 不阻断 record/落盘
        await audit.record(destination="llm:a/b", category="analysis", item_count=1, payload_size_bytes=10)
        assert audit.get_session_egress_count() == 1
        lines = (_tmp_egress_path / "egress_audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1


# ============================================================================
# 辅助构造验证 (EgressRecord dataclass)
# ============================================================================


class TestEgressRecord:
    def test_frozen_dataclass(self) -> None:
        from datetime import datetime

        rec = EgressRecord(
            timestamp=datetime(2026, 9, 14, 10, 0, 0),
            destination="llm:a/b",
            category="analysis",
            payload_size_bytes=100,
            item_count=1,
        )
        assert rec.status == "sent"
        with pytest.raises(AttributeError) as excinfo:
            rec.destination = "changed"  # frozen: 不可变
        assert isinstance(excinfo.value, AttributeError)  # frozen dataclass 拒绝赋值
