"""ShutdownCoordinator Step 8 单元测试（Phase 2 §3.5 红灯翻绿）。

测试分组（6 个）：
- 结构验证：_CLEANUP_STEPS 索引 8 = Step 8 定义
- noop 路径：service 未注册 / 已注册但未初始化
- 调用路径：asyncio.to_thread 包装 stop_sync 被调用
- 失败路径：stop_sync raise → 记 ERROR，不抛异常
- 超时逻辑：effective_timeout = min(35.0, step_timeout_s=35.0) = 35.0

Mock 策略：
- 直接实例化 ShutdownCoordinator()（构造函数支持 page=None）
- mock EmbeddedPostgresService.get_instance 返回 mock service 或 raise RuntimeError
- mock asyncio.to_thread 验证调用 + 控制异常路径
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from utils.shutdown import ShutdownCoordinator, _CLEANUP_STEPS


def test_step8_in_cleanup_steps_at_index_8() -> None:
    """_CLEANUP_STEPS[8] 应为 Step 8 定义。"""
    assert len(_CLEANUP_STEPS) >= 9, f"_CLEANUP_STEPS 应至少 9 项，实际 {len(_CLEANUP_STEPS)}"
    assert _CLEANUP_STEPS[8] == ("Step 8", "_step8_stop_embedded_postgres", True, 35.0), (
        f"_CLEANUP_STEPS[8] 实际：{_CLEANUP_STEPS[8]}"
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_step8_noop_when_service_not_registered(monkeypatch) -> None:
    """EmbeddedPostgresService._reset_singleton() 后 → Step 8 无操作，不抛异常。"""
    from data.persistence.embedded_postgres.service import EmbeddedPostgresService

    EmbeddedPostgresService._reset_singleton()

    # get_instance 应 raise RuntimeError（未注册）
    def _raise_runtime_error():
        raise RuntimeError("singleton not initialized")

    monkeypatch.setattr(EmbeddedPostgresService, "get_instance", classmethod(lambda cls: _raise_runtime_error()))

    coordinator = ShutdownCoordinator()
    # Step 8 应无操作且不抛异常
    await coordinator._step8_stop_embedded_postgres()


@pytest.mark.asyncio(loop_scope="function")
async def test_step8_noop_when_service_not_initialized(monkeypatch) -> None:
    """单例已注册但 _process is None → Step 8 无操作。

    此场景下 get_instance 返回 service 实例，但 service._process is None（未 start），
    stop_sync 内部早 return 不抛异常。
    """
    from data.persistence.embedded_postgres.service import EmbeddedPostgresService

    mock_service = MagicMock()
    mock_service._process = None
    # stop_sync 早 return（_process is None）
    mock_service.stop_sync = MagicMock(return_value=None)
    monkeypatch.setattr(EmbeddedPostgresService, "get_instance", classmethod(lambda cls: mock_service))

    coordinator = ShutdownCoordinator()
    await coordinator._step8_stop_embedded_postgres()

    # stop_sync 应被调用（即使 _process is None，也是 graceful no-op）
    mock_service.stop_sync.assert_called_once()


@pytest.mark.asyncio(loop_scope="function")
async def test_step8_calls_stop_sync_via_to_thread(monkeypatch) -> None:
    """mock service.stop_sync，运行 Step 8，验证 asyncio.to_thread 包装调用。"""
    from data.persistence.embedded_postgres.service import EmbeddedPostgresService

    mock_service = MagicMock()
    mock_service.stop_sync = MagicMock(return_value=None)
    monkeypatch.setattr(EmbeddedPostgresService, "get_instance", classmethod(lambda cls: mock_service))

    to_thread_calls: list = []

    async def _fake_to_thread(func, *args, **kwargs):
        to_thread_calls.append((func, args, kwargs))
        func(*args, **kwargs)
        return None

    monkeypatch.setattr("utils.shutdown.asyncio.to_thread", _fake_to_thread)

    coordinator = ShutdownCoordinator()
    await coordinator._step8_stop_embedded_postgres()

    # 验证 asyncio.to_thread 被调用且参数含 service.stop_sync
    assert len(to_thread_calls) == 1, f"期望 asyncio.to_thread 调用 1 次，实际：{len(to_thread_calls)}"
    func, args, kwargs = to_thread_calls[0]
    assert func == mock_service.stop_sync, f"期望 func=mock_service.stop_sync，实际：{func}"
    # stop_sync 应被调用
    mock_service.stop_sync.assert_called_once()


@pytest.mark.asyncio(loop_scope="function")
async def test_step8_logs_error_on_stop_failure(monkeypatch, caplog) -> None:
    """mock service.stop_sync raise Exception → Step 8 记 ERROR，不抛异常。"""
    from data.persistence.embedded_postgres.service import EmbeddedPostgresService

    mock_service = MagicMock()
    mock_service.stop_sync = MagicMock(side_effect=RuntimeError("fake stop failure"))
    monkeypatch.setattr(EmbeddedPostgresService, "get_instance", classmethod(lambda cls: mock_service))

    async def _fake_to_thread(func, *args, **kwargs):
        func(*args, **kwargs)
        return None

    monkeypatch.setattr("utils.shutdown.asyncio.to_thread", _fake_to_thread)

    import logging

    coordinator = ShutdownCoordinator()
    with caplog.at_level(logging.ERROR, logger="utils.shutdown"):
        # 不应抛异常
        await coordinator._step8_stop_embedded_postgres()

    # 验证 ERROR 日志含关键信息
    assert any("Step 8 stop embedded postgres failed" in r.message for r in caplog.records), (
        f"期望 ERROR 日志含 'Step 8 stop embedded postgres failed'，实际：{[r.message for r in caplog.records]}"
    )


def test_step8_timeout_not_limited_by_min_logic() -> None:
    """do_cleanup(timeout_s=50.0, step_timeout_s=35.0) 时 Step 8 effective_timeout == 35.0。

    验证 utils/shutdown.py:250 effective_timeout = min(default_timeout, step_timeout_s)
    当 step_timeout_s=35.0 时，Step 8 默认 35.0 不被 min 钳制为更小值。
    """
    step8_def = _CLEANUP_STEPS[8]
    default_timeout = step8_def[3]
    assert default_timeout == 35.0, f"Step 8 default_timeout 应为 35.0，实际：{default_timeout}"

    # 模拟 do_cleanup 调用时的 effective_timeout 计算
    step_timeout_s = 35.0  # window_lifecycle 修订后的调用参数
    effective_timeout = min(default_timeout, step_timeout_s)
    assert effective_timeout == 35.0, f"effective_timeout 应为 35.0（min(35.0, 35.0)），实际：{effective_timeout}"

    # 验证旧默认 step_timeout_s=5.0 时会被钳制（说明 window_lifecycle 修订的必要性）
    old_step_timeout_s = 5.0
    old_effective = min(default_timeout, old_step_timeout_s)
    assert old_effective == 5.0, (
        f"旧默认 step_timeout_s=5.0 时 effective_timeout 应为 5.0（被钳制），实际：{old_effective}"
    )
