"""AgentDao observe 监听连接生命周期测试（检视 D24）。

``observe_session_events`` 的独立 LISTEN 连接自 ``asyncpg.connect`` 返回那
一刻起即受保护：``add_listener`` 失败、确认语句失败、受理校验失败、事件
迭代中取消、调用方 ``break`` 后 ``aclose``（含生成器收尾）都必须关闭连接
**恰好一次**；关闭自身失败不得替换原始异常；``CancelledError`` 必须继续
传播（R2）。

真实 LISTEN/PG 行为由 ``tests/integration/test_agent_dao_observe.py`` 覆盖；
本文件只测清理路径（mock 连接与 listener，不连库）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from core.agent_types import EventPage
from data.persistence.daos.agent_dao import AgentDao

pytestmark = pytest.mark.unit


class _FakeListenConn:
    """记录 add/remove/close 调用次数并可按需注入失败。"""

    def __init__(self) -> None:
        self.add_listener_calls = 0
        self.remove_listener_calls = 0
        self.close_calls = 0
        self.add_listener_error: BaseException | None = None
        self.execute_error: BaseException | None = None
        self.remove_listener_error: BaseException | None = None
        self.close_error: BaseException | None = None

    async def add_listener(self, _channel: str, _callback: Any) -> None:
        self.add_listener_calls += 1
        if self.add_listener_error is not None:
            raise self.add_listener_error

    async def execute(self, _sql: str) -> None:
        if self.execute_error is not None:
            raise self.execute_error

    async def remove_listener(self, _channel: str, _callback: Any) -> None:
        self.remove_listener_calls += 1
        if self.remove_listener_error is not None:
            raise self.remove_listener_error

    async def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


def _dao(fake_conn: _FakeListenConn, monkeypatch: pytest.MonkeyPatch) -> AgentDao:
    engine = MagicMock(spec=AsyncEngine)
    engine.url = sa.engine.make_url("postgresql+asyncpg://user:pass@localhost:5432/testdb")
    connect_cm = MagicMock()
    connect_cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    connect_cm.__aexit__ = AsyncMock(return_value=False)
    engine.connect = MagicMock(return_value=connect_cm)

    async def _connect(**_kwargs: Any) -> _FakeListenConn:
        return fake_conn

    monkeypatch.setattr(asyncpg, "connect", _connect)

    async def _session_exists(_self: AgentDao, _conn: Any, _user_scope: str, _session_id: str) -> None:
        return None

    monkeypatch.setattr(AgentDao, "_history_session_exists", _session_exists)
    return AgentDao(engine)


def _single_event_reader(monkeypatch: pytest.MonkeyPatch, *, session_seq: int = 1) -> None:
    """read_events 只返回一条事件（之后空页），用于正常退出路径。"""

    async def _read_events(_self: AgentDao, query: Any) -> EventPage:
        events = (cast(Any, SimpleNamespace(session_seq=session_seq)),) if query.after_seq < session_seq else ()
        return EventPage(events=events, last_seq=session_seq, has_more=False)

    monkeypatch.setattr(AgentDao, "read_events", _read_events)


def _empty_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _read_events(_self: AgentDao, query: Any) -> EventPage:
        return EventPage(events=(), last_seq=query.after_seq, has_more=False)

    monkeypatch.setattr(AgentDao, "read_events", _read_events)


class TestObserveListenConnLifecycle:
    """D24：取得连接即覆盖注册/确认/迭代；close 恰好一次。"""

    async def test_add_listener_failure_closes_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeListenConn()
        fake.add_listener_error = RuntimeError("registration failed")
        dao = _dao(fake, monkeypatch)
        _empty_reader(monkeypatch)

        observer = dao.observe_session_events("s-obs", user_scope="u1")
        with pytest.raises(RuntimeError, match="registration failed"):
            await observer.__anext__()

        assert fake.add_listener_calls == 1
        assert fake.remove_listener_calls == 0  # 未注册成功不退订
        assert fake.close_calls == 1

    async def test_confirm_query_failure_closes_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeListenConn()
        fake.execute_error = RuntimeError("confirm failed")
        dao = _dao(fake, monkeypatch)
        _empty_reader(monkeypatch)

        observer = dao.observe_session_events("s-obs", user_scope="u1")
        with pytest.raises(RuntimeError, match="confirm failed"):
            await observer.__anext__()

        assert fake.remove_listener_calls == 1
        assert fake.close_calls == 1

    async def test_close_failure_does_not_mask_original_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeListenConn()
        fake.add_listener_error = RuntimeError("original failure")
        fake.close_error = OSError("close failed")
        dao = _dao(fake, monkeypatch)
        _empty_reader(monkeypatch)

        observer = dao.observe_session_events("s-obs", user_scope="u1")
        with pytest.raises(RuntimeError, match="original failure"):
            await observer.__anext__()
        assert fake.close_calls == 1

    async def test_cancellation_closes_once_and_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeListenConn()
        dao = _dao(fake, monkeypatch)
        _empty_reader(monkeypatch)

        observer = dao.observe_session_events("s-obs", user_scope="u1", poll_interval_s=0.5)
        task = asyncio.ensure_future(observer.__anext__())
        await asyncio.sleep(0.05)  # 进入 wait_for(notify_queue.get()) 等待
        task.cancel()
        with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion 取消类异常无参数可断言
            await task

        assert fake.remove_listener_calls == 1
        assert fake.close_calls == 1
        # 取消失败后的收尾不双关（幂等）
        await observer.aclose()  # type: ignore[attr-defined]  # 异步生成器 aclose 为标准库 API
        assert fake.close_calls == 1

    async def test_break_then_aclose_closes_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeListenConn()
        dao = _dao(fake, monkeypatch)
        _single_event_reader(monkeypatch, session_seq=7)

        observer = dao.observe_session_events("s-obs", user_scope="u1")
        event = await observer.__anext__()
        assert event.session_seq == 7
        assert fake.close_calls == 0  # 迭代中连接仍存活

        await observer.aclose()  # type: ignore[attr-defined]  # 异步生成器 aclose 为标准库 API
        assert fake.remove_listener_calls == 1
        assert fake.close_calls == 1

        # 二次 aclose 幂等（不双关）
        await observer.aclose()  # type: ignore[attr-defined]  # 异步生成器 aclose 为标准库 API
        assert fake.close_calls == 1

    async def test_unsubscribe_cancel_still_closes_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeListenConn()
        fake.remove_listener_error = asyncio.CancelledError()
        dao = _dao(fake, monkeypatch)
        _single_event_reader(monkeypatch)

        observer = dao.observe_session_events("s-obs", user_scope="u1")
        await observer.__anext__()
        with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion 取消类异常无参数可断言
            await observer.aclose()  # type: ignore[attr-defined]  # 异步生成器 aclose 为标准库 API

        assert fake.remove_listener_calls == 1
        assert fake.close_calls == 1
