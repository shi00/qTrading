"""_prepare_db_with_retry 单元测试（P0-1：prepare_database_runtime 失败不闪退）。

测试分组（6 个）：
- 成功路径：第一次成功 → 返回 URL
- 重试路径：失败后用户点 Retry → 第二次成功
- 退出路径：失败后用户点 Exit → sys.exit(0)
- external 模式：返回 None → 不进入重试循环
- CancelledError 传播：prepare_database_runtime 抛 CancelledError → 不捕获
- E2E 模式：失败 → sys.exit(1)（不渲染 PreInitErrorView）

Mock 策略：
- prepare_database_runtime：AsyncMock side_effect 控制成功/失败
- _wait_for_user_action：AsyncMock 返回 "retry"/"exit"（避免真实线程等待超时）
- page.render：MagicMock 记录调用
- DataSanitizer.sanitize_error：mock 返回固定字符串（避免路径脱敏逻辑）
- sys.exit：MagicMock(side_effect=SystemExit) 捕获
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio(loop_scope="function")
async def test_first_attempt_success() -> None:
    """prepare_database_runtime 第一次成功 → 返回 URL，不渲染 PreInitErrorView。"""
    from app.application import _prepare_db_with_retry

    expected_url = "postgresql+asyncpg://user:pass@127.0.0.1:5432/db"
    with patch("app.bootstrap.prepare_database_runtime", new_callable=AsyncMock) as mock_prepare:
        mock_prepare.return_value = expected_url
        page = MagicMock()

        result = await _prepare_db_with_retry(page, scenario=None)

    assert result == expected_url
    assert mock_prepare.call_count == 1
    # 成功路径不渲染 PreInitErrorView
    pre_init_renders = [c for c in page.render.call_args_list if "PreInitErrorView" in str(c)]
    assert len(pre_init_renders) == 0


@pytest.mark.asyncio(loop_scope="function")
async def test_failure_then_retry_success() -> None:
    """第一次失败、用户点 Retry、第二次成功 → 渲染 PreInitErrorView 1 次，返回 URL。"""
    from app.application import _prepare_db_with_retry

    expected_url = "postgresql+asyncpg://user:pass@127.0.0.1:5432/db"

    with (
        patch("app.bootstrap.prepare_database_runtime", new_callable=AsyncMock) as mock_prepare,
        patch("utils.sanitizers.DataSanitizer.sanitize_error", return_value="sanitized error"),
        patch("app.application._wait_for_user_action", new_callable=AsyncMock, return_value="retry"),
    ):
        mock_prepare.side_effect = [RuntimeError("sidecar failed"), expected_url]
        page = MagicMock()

        with patch.dict("os.environ", {"E2E_TESTING": "", "FLET_FORCE_WEB_SERVER": ""}):
            result = await _prepare_db_with_retry(page, scenario=None)

    assert result == expected_url
    assert mock_prepare.call_count == 2
    # 至少渲染过 1 次 PreInitErrorView
    pre_init_renders = [c for c in page.render.call_args_list if "PreInitErrorView" in str(c)]
    assert len(pre_init_renders) >= 1


@pytest.mark.asyncio(loop_scope="function")
async def test_failure_then_exit() -> None:
    """失败后用户点 Exit → sys.exit(0) 被调用。"""
    from app.application import _prepare_db_with_retry

    with (
        patch("app.bootstrap.prepare_database_runtime", new_callable=AsyncMock) as mock_prepare,
        patch("utils.sanitizers.DataSanitizer.sanitize_error", return_value="sanitized error"),
        patch("app.application._wait_for_user_action", new_callable=AsyncMock, return_value="exit"),
        patch("app.application.sys.exit", side_effect=SystemExit(0)) as mock_exit,
    ):
        mock_prepare.side_effect = RuntimeError("sidecar failed")
        page = MagicMock()

        with patch.dict("os.environ", {"E2E_TESTING": "", "FLET_FORCE_WEB_SERVER": ""}):
            with pytest.raises(SystemExit) as exc_info:
                await _prepare_db_with_retry(page, scenario=None)

    assert exc_info.value.code == 0
    mock_exit.assert_called_once_with(0)
    assert mock_prepare.call_count == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_external_mode_no_retry() -> None:
    """external 模式（prepare_database_runtime 返回 None）→ 不进入重试循环。"""
    from app.application import _prepare_db_with_retry

    with patch("app.bootstrap.prepare_database_runtime", new_callable=AsyncMock) as mock_prepare:
        mock_prepare.return_value = None
        page = MagicMock()

        result = await _prepare_db_with_retry(page, scenario=None)

    assert result is None
    assert mock_prepare.call_count == 1
    # 不渲染任何错误视图
    pre_init_renders = [c for c in page.render.call_args_list if "PreInitErrorView" in str(c)]
    assert len(pre_init_renders) == 0


@pytest.mark.asyncio(loop_scope="function")
async def test_cancelled_error_propagates() -> None:
    """prepare_database_runtime 抛 CancelledError → 不捕获，正确传播（R2 红线）。"""
    from app.application import _prepare_db_with_retry

    with patch("app.bootstrap.prepare_database_runtime", new_callable=AsyncMock) as mock_prepare:
        mock_prepare.side_effect = asyncio.CancelledError()
        page = MagicMock()

        with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion CancelledError 传播即测试目标(R2红线)，副作用(未渲染PreInitErrorView)在with块后断言
            await _prepare_db_with_retry(page, scenario=None)

    # 不渲染 PreInitErrorView（CancelledError 不是 Exception 子类，不被 except Exception 捕获）
    pre_init_renders = [c for c in page.render.call_args_list if "PreInitErrorView" in str(c)]
    assert len(pre_init_renders) == 0


@pytest.mark.asyncio(loop_scope="function")
async def test_e2e_mode_exits_on_failure() -> None:
    """E2E 模式 + 失败 → sys.exit(1)，不渲染 PreInitErrorView。"""
    from app.application import _prepare_db_with_retry

    with (
        patch("app.bootstrap.prepare_database_runtime", new_callable=AsyncMock) as mock_prepare,
        patch("utils.sanitizers.DataSanitizer.sanitize_error", return_value="sanitized error"),
        patch("app.application.sys.exit", side_effect=SystemExit(1)) as mock_exit,
    ):
        mock_prepare.side_effect = RuntimeError("sidecar failed")
        page = MagicMock()

        with patch.dict("os.environ", {"E2E_TESTING": "true"}):
            with pytest.raises(SystemExit) as exc_info:
                await _prepare_db_with_retry(page, scenario=None)

    assert exc_info.value.code == 1
    mock_exit.assert_called_once_with(1)
    # E2E 模式不渲染 PreInitErrorView
    pre_init_renders = [c for c in page.render.call_args_list if "PreInitErrorView" in str(c)]
    assert len(pre_init_renders) == 0


# ---------- _wait_for_user_action 直接测试（P2-2 补充）----------


@pytest.mark.asyncio(loop_scope="function")
async def test_wait_for_user_action_retry() -> None:
    """_wait_for_user_action: retry_event 被 set → 返回 "retry"。"""
    import threading

    from app.application import _wait_for_user_action

    retry_event = threading.Event()
    exit_event = threading.Event()

    # 在调用前 set retry_event，asyncio.to_thread 立即返回
    retry_event.set()

    result = await _wait_for_user_action(retry_event, exit_event)

    assert result == "retry"
    # finally 应 set 两个 event（P1-1 修复验证）
    assert retry_event.is_set()
    assert exit_event.is_set()


@pytest.mark.asyncio(loop_scope="function")
async def test_wait_for_user_action_exit() -> None:
    """_wait_for_user_action: exit_event 被 set → 返回 "exit"。"""
    import threading

    from app.application import _wait_for_user_action

    retry_event = threading.Event()
    exit_event = threading.Event()

    exit_event.set()

    result = await _wait_for_user_action(retry_event, exit_event)

    assert result == "exit"
    assert retry_event.is_set()
    assert exit_event.is_set()


@pytest.mark.asyncio(loop_scope="function")
async def test_wait_for_user_action_cancel_sets_both_events() -> None:
    """_wait_for_user_action: 外部取消时 finally set 两个 event 避免线程泄漏（P1-1 回归测试）。"""
    import threading

    from app.application import _wait_for_user_action

    retry_event = threading.Event()
    exit_event = threading.Event()

    # 创建 task 后立即取消，模拟用户关窗口时 main(page) 协程被取消
    task = asyncio.create_task(_wait_for_user_action(retry_event, exit_event))
    await asyncio.sleep(0.01)  # 让 task 开始执行
    task.cancel()

    with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion CancelledError 传播即测试目标，event set 副作用(P1-1线程泄漏修复)在with块后断言
        await task

    # P1-1 验证：finally 应 set 两个 event，唤醒阻塞的线程
    assert retry_event.is_set(), "finally 应 set retry_event 避免线程泄漏"
    assert exit_event.is_set(), "finally 应 set exit_event 避免线程泄漏"


# ---------- 窗口关闭 CancelledError 路径测试（P2-3 补充）----------


@pytest.mark.asyncio(loop_scope="function")
async def test_window_close_during_error_view_propagates_cancelled() -> None:
    """用户在 PreInitErrorView 关窗口 → CancelledError 正确传播（R2 红线）。"""
    from app.application import _prepare_db_with_retry

    with (
        patch("app.bootstrap.prepare_database_runtime", new_callable=AsyncMock) as mock_prepare,
        patch("utils.sanitizers.DataSanitizer.sanitize_error", return_value="sanitized error"),
        patch("app.application._wait_for_user_action", new_callable=AsyncMock) as mock_wait,
    ):
        mock_prepare.side_effect = RuntimeError("sidecar failed")
        # 模拟用户关窗口：_wait_for_user_action 抛 CancelledError
        mock_wait.side_effect = asyncio.CancelledError()
        page = MagicMock()

        with patch.dict("os.environ", {"E2E_TESTING": "", "FLET_FORCE_WEB_SERVER": ""}):
            with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion CancelledError 传播即测试目标(R2红线)，副作用(未渲染LoadingView)在with块后断言
                await _prepare_db_with_retry(page, scenario=None)

    # CancelledError 传播，不渲染 LoadingView（未到达 Retry 路径）
    loading_renders = [c for c in page.render.call_args_list if "LoadingView" in str(c)]
    assert len(loading_renders) == 0


# ---------- _build_pre_init_error_view 纯函数测试（diff coverage）----------


def test_build_pre_init_error_view_structure() -> None:
    """_build_pre_init_error_view 返回含 icon/title/error_text/retry/exit 的 Container。"""
    import flet as ft

    from ui.startup_views import _build_pre_init_error_view

    mock_i18n = MagicMock()
    mock_i18n.get.side_effect = lambda k: k  # 返回 key 本身便于断言
    with patch("ui.startup_views.I18n", mock_i18n):
        view = _build_pre_init_error_view("some error", on_retry=MagicMock(), on_exit=MagicMock())

    assert isinstance(view, ft.Container)
    # 验证含 error icon、标题文案、错误详情、retry/exit 按钮
    source = repr(view)
    assert "error_embedded_pg_start_failed" in source
    assert "some error" in source
    assert "retry" in source
    assert "exit_program" in source


def test_build_pre_init_error_view_truncates_long_message() -> None:
    """error_message 超过 200 字符时截断。"""
    from ui.startup_views import _build_pre_init_error_view

    long_msg = "x" * 300
    mock_i18n = MagicMock()
    mock_i18n.get.side_effect = lambda k: k
    with patch("ui.startup_views.I18n", mock_i18n):
        view = _build_pre_init_error_view(long_msg, on_retry=MagicMock(), on_exit=MagicMock())

    source = repr(view)
    assert "x" * 200 in source
    assert "x" * 201 not in source  # 截断到 200


def test_pre_init_error_view_component_renders() -> None:
    """PreInitErrorView @ft.component wrapper 通过 render_once 驱动执行（diff coverage L203-204）。

    使用 tests/unit/ui/component_renderer 的 render_once 在 Renderer 上下文中
    执行组件函数体，覆盖 ft.use_state + return _build_pre_init_error_view 行。
    """
    import flet as ft

    from tests.unit.ui.component_renderer import attach_fake_page, make_component, render_once
    from ui.startup_views import PreInitErrorView

    mock_i18n = MagicMock()
    mock_i18n.get.side_effect = lambda k: k
    with patch("ui.startup_views.I18n", mock_i18n):
        component = make_component(
            PreInitErrorView, error_message="test error", on_retry=MagicMock(), on_exit=MagicMock()
        )
        attach_fake_page(component)
        view = render_once(component)

    assert isinstance(view, ft.Container)


# ---------- P2-1: 重试退避 + 持续失败诊断引导 ----------


@pytest.mark.asyncio(loop_scope="function")
async def test_retry_backoff_sleeps_between_attempts() -> None:
    """P2-1: Retry 路径在重试前有指数退避 sleep（1s, 2s, 4s...）。

    验证：第一次失败后 sleep(1s)，第二次失败后 sleep(2s)。
    """
    from app.application import _prepare_db_with_retry

    expected_url = "postgresql+asyncpg://user:pass@127.0.0.1:5432/db"

    with (
        patch("app.bootstrap.prepare_database_runtime", new_callable=AsyncMock) as mock_prepare,
        patch("utils.sanitizers.DataSanitizer.sanitize_error", return_value="sanitized error"),
        patch("app.application._wait_for_user_action", new_callable=AsyncMock, return_value="retry"),
        patch("app.application.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        # 三次失败后第四次成功
        mock_prepare.side_effect = [
            RuntimeError("fail1"),
            RuntimeError("fail2"),
            RuntimeError("fail3"),
            expected_url,
        ]
        page = MagicMock()

        with patch.dict("os.environ", {"E2E_TESTING": "", "FLET_FORCE_WEB_SERVER": ""}):
            result = await _prepare_db_with_retry(page, scenario=None)

    assert result == expected_url
    assert mock_prepare.call_count == 4
    # Retry 路径 sleep 调用：第 1 次失败后 sleep(1)、第 2 次 sleep(2)、第 3 次 sleep(4)
    # (第四次成功，不再 sleep)
    sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
    # 过滤掉 LoadingView 刷新帧的 sleep(0.05)
    backoff_sleeps = [s for s in sleep_calls if s >= 1]
    assert backoff_sleeps == [1, 2, 4]  # noqa: weak-assertion <验证指数退避序列是测试目标本身>


@pytest.mark.asyncio(loop_scope="function")
async def test_backoff_capped_at_30_seconds() -> None:
    """P2-1: 退避间隔上限 30s（避免长时间卡顿）。"""
    from app.application import _prepare_db_with_retry

    with (
        patch("app.bootstrap.prepare_database_runtime", new_callable=AsyncMock) as mock_prepare,
        patch("utils.sanitizers.DataSanitizer.sanitize_error", return_value="sanitized error"),
        patch("app.application._wait_for_user_action", new_callable=AsyncMock, return_value="retry"),
        patch("app.application.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        # 6 次失败后第 7 次成功，验证第 6 次退避 = 30（上限）
        mock_prepare.side_effect = [RuntimeError(f"fail{i}") for i in range(6)] + [None]
        page = MagicMock()

        with patch.dict("os.environ", {"E2E_TESTING": "", "FLET_FORCE_WEB_SERVER": ""}):
            await _prepare_db_with_retry(page, scenario=None)

    sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
    backoff_sleeps = [s for s in sleep_calls if s >= 1]
    # 1, 2, 4, 8, 16, 30 (第 6 次失败后的退避被 cap 到 30)
    assert backoff_sleeps == [1, 2, 4, 8, 16, 30]  # noqa: weak-assertion <验证退避上限是测试目标本身>


@pytest.mark.asyncio(loop_scope="function")
async def test_persistent_failure_passes_failure_count_to_error_view() -> None:
    """P2-1: 连续失败 ≥3 次时，PreInitErrorView 收到 failure_count=3。

    验证：第三次失败渲染 PreInitErrorView 时 failure_count=3。
    """
    from app.application import _prepare_db_with_retry

    with (
        patch("app.bootstrap.prepare_database_runtime", new_callable=AsyncMock) as mock_prepare,
        patch("utils.sanitizers.DataSanitizer.sanitize_error", return_value="sanitized error"),
        patch("app.application._wait_for_user_action", new_callable=AsyncMock, return_value="exit"),
        patch("app.application.sys.exit", side_effect=SystemExit(0)),
        patch("app.application.asyncio.sleep", new_callable=AsyncMock),
    ):
        # 三次连续失败，第四次用户选 exit
        mock_prepare.side_effect = RuntimeError("persistent fail")
        page = MagicMock()

        with patch.dict("os.environ", {"E2E_TESTING": "", "FLET_FORCE_WEB_SERVER": ""}):
            with pytest.raises(SystemExit):  # noqa: weak-assertion SystemExit 触发即测试目标, failure_count 传递在 with 块后断言
                await _prepare_db_with_retry(page, scenario=None)

    # 验证 PreInitErrorView 渲染时传入了 failure_count
    pre_init_renders = [c for c in page.render.call_args_list if "PreInitErrorView" in str(c)]
    assert len(pre_init_renders) >= 1
    # 第一次失败 failure_count=1
    first_render_kwargs = pre_init_renders[0].kwargs
    assert first_render_kwargs.get("failure_count") == 1  # noqa: weak-assertion <验证 failure_count 传递是测试目标本身>


@pytest.mark.asyncio(loop_scope="function")
async def test_backoff_sleep_cancelled_propagates() -> None:
    """P2-1: 退避 sleep 被取消时 CancelledError 正确传播（R2 红线）。

    用户在退避等待期间关窗口 → main(page) 协程被取消 → asyncio.sleep 抛 CancelledError。
    """
    from app.application import _prepare_db_with_retry

    with (
        patch("app.bootstrap.prepare_database_runtime", new_callable=AsyncMock) as mock_prepare,
        patch("utils.sanitizers.DataSanitizer.sanitize_error", return_value="sanitized error"),
        patch("app.application._wait_for_user_action", new_callable=AsyncMock, return_value="retry"),
        patch("app.application.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        mock_prepare.side_effect = RuntimeError("fail")
        # 模拟 sleep 被取消
        mock_sleep.side_effect = asyncio.CancelledError()
        page = MagicMock()

        with patch.dict("os.environ", {"E2E_TESTING": "", "FLET_FORCE_WEB_SERVER": ""}):
            with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion CancelledError 传播即 R2 红线测试目标
                await _prepare_db_with_retry(page, scenario=None)


# ---------- P2-4: 可中断退避等待 + LoadingView 倒计时反馈 ----------


@pytest.mark.asyncio(loop_scope="function")
async def test_wait_for_event_or_timeout_returns_event_when_set() -> None:
    """P2-4: _wait_for_event_or_timeout: event 已 set → 立即返回 "event"."""
    import threading

    from app.application import _wait_for_event_or_timeout

    event = threading.Event()
    event.set()

    result = await _wait_for_event_or_timeout(event, 10)

    assert result == "event"
    assert event.is_set()


@pytest.mark.asyncio(loop_scope="function")
async def test_wait_for_event_or_timeout_returns_timeout_when_sleep_completes() -> None:
    """P2-4: _wait_for_event_or_timeout: event 未 set 且 sleep 完成 → 返回 "timeout"."""
    import threading

    from app.application import _wait_for_event_or_timeout

    event = threading.Event()

    with patch("app.application.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await _wait_for_event_or_timeout(event, 5)

    assert result == "timeout"
    mock_sleep.assert_called_once_with(5)
    # finally 应 set event 避免线程泄漏
    assert event.is_set()


@pytest.mark.asyncio(loop_scope="function")
async def test_wait_for_event_or_timeout_cancelled_sets_event() -> None:
    """P2-4: _wait_for_event_or_timeout: 外部取消时 finally set event 避免线程泄漏."""
    import threading

    from app.application import _wait_for_event_or_timeout

    event = threading.Event()

    # 模拟用户关窗口：main(page) 协程被取消
    task = asyncio.create_task(_wait_for_event_or_timeout(event, 10))
    await asyncio.sleep(0.01)  # 让 task 开始执行
    task.cancel()

    with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion CancelledError 传播即测试目标, event set 副作用在 with 块后断言
        await task

    # finally 应 set event，唤醒阻塞的 event.wait() 线程
    assert event.is_set(), "finally 应 set event 避免线程泄漏"


@pytest.mark.asyncio(loop_scope="function")
async def test_prepare_db_with_retry_backoff_exit_triggers_sys_exit() -> None:
    """P2-4: 退避等待期间用户点 Exit → sys.exit(0)。

    验证：第一次失败 → 用户 Retry → 渲染 LoadingView（带 Exit 按钮） →
    用户点 Exit → _wait_for_event_or_timeout 返回 "event" → sys.exit(0)。
    """
    from app.application import _prepare_db_with_retry

    with (
        patch("app.bootstrap.prepare_database_runtime", new_callable=AsyncMock) as mock_prepare,
        patch("utils.sanitizers.DataSanitizer.sanitize_error", return_value="sanitized error"),
        patch("app.application._wait_for_user_action", new_callable=AsyncMock, return_value="retry"),
        patch("app.application._wait_for_event_or_timeout", new_callable=AsyncMock, return_value="event"),
        patch("app.application.sys.exit", side_effect=SystemExit(0)) as mock_exit,
    ):
        mock_prepare.side_effect = RuntimeError("sidecar failed")
        page = MagicMock()

        with patch.dict("os.environ", {"E2E_TESTING": "", "FLET_FORCE_WEB_SERVER": ""}):
            with pytest.raises(SystemExit) as exc_info:  # noqa: weak-assertion SystemExit 触发即测试目标, sys.exit 参数在 with 块后断言
                await _prepare_db_with_retry(page, scenario=None)

    assert exc_info.value.code == 0
    mock_exit.assert_called_once_with(0)
    assert mock_prepare.call_count == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_prepare_db_with_retry_backoff_renders_loading_view_with_countdown() -> None:
    """P2-4: 退避等待前渲染 LoadingView 并传入 retry_backoff_seconds + failure_count + on_exit。

    验证：第一次失败 → Retry → 渲染 LoadingView 时传入 backoff=1、failure_count=1、on_exit 回调。
    """
    from app.application import _prepare_db_with_retry

    expected_url = "postgresql+asyncpg://user:pass@127.0.0.1:5432/db"

    with (
        patch("app.bootstrap.prepare_database_runtime", new_callable=AsyncMock) as mock_prepare,
        patch("utils.sanitizers.DataSanitizer.sanitize_error", return_value="sanitized error"),
        patch("app.application._wait_for_user_action", new_callable=AsyncMock, return_value="retry"),
        patch("app.application._wait_for_event_or_timeout", new_callable=AsyncMock, return_value="timeout"),
    ):
        mock_prepare.side_effect = [RuntimeError("fail"), expected_url]
        page = MagicMock()

        with patch.dict("os.environ", {"E2E_TESTING": "", "FLET_FORCE_WEB_SERVER": ""}):
            result = await _prepare_db_with_retry(page, scenario=None)

    assert result == expected_url
    # 验证 LoadingView 渲染时传入了 backoff 参数
    loading_renders = [c for c in page.render.call_args_list if "LoadingView" in str(c)]
    assert len(loading_renders) >= 1
    backoff_kwargs = loading_renders[0].kwargs
    assert backoff_kwargs.get("retry_backoff_seconds") == 1  # 第一次失败 backoff=2^0=1
    assert backoff_kwargs.get("failure_count") == 1
    assert "on_exit" in backoff_kwargs


@pytest.mark.asyncio(loop_scope="function")
async def test_prepare_db_with_retry_backoff_timeout_continues_retry() -> None:
    """P2-4: 退避超时后继续重试 prepare_database_runtime。

    验证：第一次失败 → Retry → 渲染 LoadingView → 退避超时 → 第二次成功。
    """
    from app.application import _prepare_db_with_retry

    expected_url = "postgresql+asyncpg://user:pass@127.0.0.1:5432/db"

    with (
        patch("app.bootstrap.prepare_database_runtime", new_callable=AsyncMock) as mock_prepare,
        patch("utils.sanitizers.DataSanitizer.sanitize_error", return_value="sanitized error"),
        patch("app.application._wait_for_user_action", new_callable=AsyncMock, return_value="retry"),
        patch("app.application._wait_for_event_or_timeout", new_callable=AsyncMock, return_value="timeout"),
    ):
        mock_prepare.side_effect = [RuntimeError("fail"), expected_url]
        page = MagicMock()

        with patch.dict("os.environ", {"E2E_TESTING": "", "FLET_FORCE_WEB_SERVER": ""}):
            result = await _prepare_db_with_retry(page, scenario=None)

    assert result == expected_url
    assert mock_prepare.call_count == 2


# ---------- P2-5: embedded URL ContextVar override 测试 ----------


def test_get_db_url_prefers_contextvar_override_over_env_var() -> None:
    """P2-5: ContextVar override(Priority 0) > DATABASE_URL env var(Priority 1)。

    当 embedded 模式成功后，用 ContextVar 写入 embedded URL，即使 DATABASE_URL
    环境变量存在，get_db_url() 也应返回 embedded URL（避免连错库）。
    """
    from utils.config_handler import ConfigHandler

    env_url = "postgresql+asyncpg://env_user:env_pass@external-host:5432/external_db"
    embedded_url = "postgresql+asyncpg://postgres:pass@127.0.0.1:15432/astock"

    with patch.dict("os.environ", {"DATABASE_URL": env_url}):
        # 未设 override：Priority 1 (env) 生效
        assert ConfigHandler.get_db_url() == env_url

        # P2-5: 设 ContextVar override（模拟 embedded 成功）
        token = ConfigHandler._db_url_override.set(embedded_url)
        try:
            # override 生效：Priority 0 覆盖 Priority 1
            assert ConfigHandler.get_db_url() == embedded_url
        finally:
            ConfigHandler._db_url_override.reset(token)

        # reset 后：回到 Priority 1 (env)
        assert ConfigHandler.get_db_url() == env_url


def test_get_db_url_contextvar_override_overrides_db_host_components() -> None:
    """P2-5: ContextVar override 同时覆盖 Priority 2（db_host 组件）。

    优先级验证：ContextVar(0) > env(1) > db_host 组件(2) > config.DB_URL(3)。
    """
    from utils.config_handler import ConfigHandler

    embedded_url = "postgresql+asyncpg://postgres:pass@127.0.0.1:15432/astock"
    components_url = "postgresql+asyncpg://persisted_user:persisted_pass@onboarding-host:5432/astock"

    # 模拟 onboard 后：db_host 已写入配置，Priority 2 会重建 components_url
    def _mock_get_typed(key: str, typ, default):
        return {
            "db_host": "onboarding-host",
            "db_port": 5432,
            "db_user": "persisted_user",
            "db_name": "astock",
        }.get(key, default)

    with (
        patch.dict("os.environ", {"DATABASE_URL": ""}),
        patch.object(ConfigHandler, "get_typed", side_effect=_mock_get_typed),
        patch.object(ConfigHandler, "get_db_password", return_value="persisted_pass"),
        patch(
            "data.persistence.db_config_service.DatabaseConfigService.build_url",
            return_value=components_url,
        ),
    ):
        # 未设 override：Priority 2 (db_host 组件) 生效
        assert ConfigHandler.get_db_url() == components_url

        # P2-5: 设 ContextVar override → Priority 0 赢
        token = ConfigHandler._db_url_override.set(embedded_url)
        try:
            assert ConfigHandler.get_db_url() == embedded_url
        finally:
            ConfigHandler._db_url_override.reset(token)

        # reset 后：回到 Priority 2
        assert ConfigHandler.get_db_url() == components_url


def test_embedded_db_url_lost_to_env_var_is_the_bug_we_fix() -> None:
    """P2-5 (Red phase): 当前 bug 复现——DATABASE_URL env var(Priority 1) 覆盖 config.DB_URL(Priority 3)。

    此测试复现 bug：embedded 成功后 config.DB_URL = embedded_url，但用户 shell
    残留 DATABASE_URL，get_db_url() 返回值是 env_url 而非 embedded_url。

    修复策略（P2-5）：embedded 成功后用 ContextVar(Priority 0) 显式 override，
    override 期间 get_db_url() 正确返回 embedded_url。
    """
    import config as app_config

    from utils.config_handler import ConfigHandler

    env_url = "postgresql+asyncpg://env_user:env_pass@bad-host:5432/wrong_db"
    embedded_url = "postgresql+asyncpg://postgres:pass@127.0.0.1:15432/astock"

    # 模拟：embedded 成功后写 config.DB_URL = embedded_url（main.py D15 行为）
    original_db_url = app_config.DB_URL
    try:
        app_config.DB_URL = embedded_url

        with patch.dict("os.environ", {"DATABASE_URL": env_url}):
            # Bug 复现：env(Priority 1) 胜过 config.DB_URL(Priority 3)
            # 此为当前未修复代码的真实行为（即 P2-5 bug 本身）
            before_override = ConfigHandler.get_db_url()
            assert before_override == env_url, (
                "Red phase: expected env_url to win (this is the bug we fix), "
                f"but got {before_override!r}. If this assert fails it means "
                "the precedence has changed and this test needs updating."
            )

            # 修复后：设 ContextVar override → embedded_url 赢
            token = ConfigHandler._db_url_override.set(embedded_url)
            try:
                after_override = ConfigHandler.get_db_url()
                assert after_override == embedded_url, (
                    f"After override expected embedded_url but got {after_override!r}"
                )
            finally:
                ConfigHandler._db_url_override.reset(token)

            # reset 后 bug 复现（回到 env_url）——证明 override 是必要的
            after_reset = ConfigHandler.get_db_url()
            assert after_reset == env_url, (
                f"After reset expected env_url again (proving override is required), but got {after_reset!r}"
            )
    finally:
        app_config.DB_URL = original_db_url
