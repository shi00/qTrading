# pyright: reportAttributeAccessIssue=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 动态属性访问（mock/stub/monkey-patch）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

"""P3-Tushare-Token-Invalid-Race: _token_invalid 竞态修复测试。

验证：
1. 旧协程在 set_token 后不应将 _token_invalid 覆盖为 True（并发竞态修复）
2. 事件循环未运行时 set_token 抛 RuntimeError（避免静默失败）
3. _token_invalid 读写经 get_loop_local(asyncio.Lock) 保护（R11 强制）
"""

import asyncio
import threading
from unittest.mock import MagicMock, patch

import pytest

from data.external.tushare_client import TushareAPIPermissionError, TushareClient

pytestmark = pytest.mark.unit


@pytest.fixture
def tushare_client_mocks():
    with (
        patch("data.external.tushare_client.ts") as mock_ts,
        patch("data.external.tushare_client.ConfigHandler") as mock_ch,
    ):
        mock_ts.pro_api.return_value = MagicMock()
        mock_ch.get_token.return_value = "test_token"
        mock_ch.get_tushare_timeout.return_value = 30
        mock_ch.get_request_max_retries.return_value = 3
        mock_ch.get_tushare_point_tier.return_value = "points_5000"
        client = TushareClient(token="test_token")
        yield client, mock_ts, mock_ch


class TestTokenInvalidRaceCondition:
    """P3-Tushare-Token-Invalid-Race: _token_invalid 并发竞态修复。"""

    @pytest.mark.asyncio
    async def test_stale_coroutine_does_not_override_token_invalid_after_set_token(self, tushare_client_mocks):
        """旧协程在 set_token 后不应将 _token_invalid 覆盖为 True。

        场景：
        1. 协程 A 调用 _handle_api_call，遇到 token 错误（is_token_invalid=True）
        2. 在协程 A 设置 _token_invalid=True 之前，set_token_async 被调用：
           - 重置 _token_invalid=False
           - 更新 self.token 为新 token
        3. 协程 A 继续，设置 _token_invalid=True 时检测到 token 已改变，跳过设置
        4. 验证：_token_invalid 仍为 False（不被旧协程覆盖）
        """
        client, _, _ = tushare_client_mocks
        client.max_retries = 1
        client._bg_tasks = set()

        # 模拟 _handle_api_call 流程：
        # 1. wait_for 抛出 token 错误异常
        # 2. except 块判定 is_token_invalid=True
        # 3. 在 mark_api_unavailable 之后、_token_invalid=True 之前，触发 set_token_async
        # 4. set_token_async 重置 _token_invalid=False 并更新 self.token
        # 5. _handle_api_call 持 lock 设置 _token_invalid=True 时，检测到 token 改变，跳过
        async def mock_wait_for(coro, timeout=None):
            # 模拟 token 错误
            raise Exception("您的token不对，请确认。")

        # 通过 mock mark_api_unavailable 在 _token_invalid=True 之前插入 set_token_async
        # mark_api_unavailable 是同步方法，但我们可以在测试主协程中先 await set_token_async
        # 然后再让 _handle_api_call 继续
        # 简化方案：在 mock_wait_for 抛异常前，先更新 token（模拟 set_token 已被调用）
        async def mock_wait_for_with_token_change(coro, timeout=None):
            # 模拟：在 API 调用期间（wait_for 执行中），set_token 被调用更新了 token
            # set_token_async 已重置 _token_invalid=False 并更新 self.token
            await client.set_token_async("new_token_after_invalid")
            # 然后 wait_for 抛出 token 错误（基于旧 token 的判断）
            raise Exception("您的token不对，请确认。")

        mock_func = MagicMock()
        mock_func.__name__ = "moneyflow_hsgt"

        with patch("data.external.tushare_client.asyncio.wait_for", side_effect=mock_wait_for_with_token_change):
            # _handle_api_call 应抛 TushareAPIPermissionError（token 错误，api_name=moneyflow_hsgt）
            with pytest.raises(TushareAPIPermissionError, match="moneyflow_hsgt"):
                await client._handle_api_call(mock_func, trade_date="20240101")

        # 验证：_token_invalid 不被旧协程覆盖为 True（应为 False）
        # 因为 set_token_async 已重置，且 _handle_api_call 检测到 token 改变不再设置 True
        assert client.is_token_invalid is False, "旧协程不应在 set_token 后将 _token_invalid 覆盖为 True"
        # 验证 token 已更新
        assert client.token == "new_token_after_invalid"

        # 清理 bg_tasks
        for t in list(client._bg_tasks):
            t.cancel()
        client._bg_tasks.clear()

    @pytest.mark.asyncio
    async def test_token_invalid_read_write_protected_by_loop_local_lock(self, tushare_client_mocks):
        """_token_invalid 读写经 get_loop_local(asyncio.Lock) 保护（R11 强制）。

        验证：_get_token_invalid_lock 返回同一事件循环内的同一 Lock 实例。
        """
        client, _, _ = tushare_client_mocks

        lock1 = client._get_token_invalid_lock()
        lock2 = client._get_token_invalid_lock()
        # 同一事件循环内应返回同一 Lock 实例
        assert lock1 is lock2, "同一事件循环内 _get_token_invalid_lock 应返回同一 Lock 实例"
        # 应为 asyncio.Lock 类型
        assert isinstance(lock1, asyncio.Lock), "_get_token_invalid_lock 应返回 asyncio.Lock"

    @pytest.mark.asyncio
    async def test_set_token_async_resets_token_invalid(self, tushare_client_mocks):
        """set_token_async 应重置 _token_invalid=False（经 loop-local lock 保护）。"""
        client, _, _ = tushare_client_mocks
        client._token_invalid = True
        assert client.is_token_invalid is True

        await client.set_token_async("new_token_after_invalid")

        assert client.is_token_invalid is False
        assert client.token == "new_token_after_invalid"

    @pytest.mark.asyncio
    async def test_set_token_async_same_token_resets_breaker(self, tushare_client_mocks):
        """set_token_async 传入相同 token 时也应重置熔断标志。"""
        client, _, _ = tushare_client_mocks
        client._token_invalid = True

        await client.set_token_async(client.token)

        assert client.is_token_invalid is False


class TestSetTokenWithoutEventLoop:
    """事件循环未运行时 set_token 抛异常（DoD 4）。"""

    def test_set_token_raises_when_no_event_loop(self, tushare_client_mocks):
        """事件循环未运行时 set_token 抛 RuntimeError（避免静默失败）。

        set_token 通过 run_coroutine_threadsafe 调度到事件循环执行 _set_token_async。
        若无运行中的事件循环，应抛 RuntimeError 而非静默失败。
        """
        client, _, _ = tushare_client_mocks
        # 确保没有保存的事件循环引用
        client._loop = None

        with pytest.raises(RuntimeError, match="requires a running"):
            client.set_token("new_token")

    def test_set_token_raises_when_in_event_loop(self, tushare_client_mocks):
        """在事件循环线程中调用 set_token 抛 RuntimeError（避免 run_coroutine_threadsafe 死锁）。

        set_token 是同步方法，通过 run_coroutine_threadsafe 调度到事件循环。
        若在事件循环线程中调用，future.result() 会死锁。
        应抛 RuntimeError，提示用 set_token_async。
        """

        client, _, _ = tushare_client_mocks

        async def call_set_token_in_loop():
            # 在事件循环中调用同步 set_token 应抛 RuntimeError
            with pytest.raises(RuntimeError, match="cannot be called from within"):
                client.set_token("new_token")

        asyncio.run(call_set_token_in_loop())


class TestSetTokenFromWorkerThread:
    """set_token 同步包装器在 worker 线程中的成功路径（DoD ⑧）。"""

    def test_set_token_from_worker_thread_succeeds(self, tushare_client_mocks):
        """worker 线程中调用 set_token（事件循环已运行）应成功。

        验证：
        - 返回值 True（cache 被清空，应触发 probe）
        - token 确实被更新为新值
        - capability_cache 被清空
        - _token_invalid 被重置为 False
        """
        client, _, _ = tushare_client_mocks
        # 预设：_token_invalid=True，capability_cache 有内容
        client._token_invalid = True
        client._capability_cache["some_api"] = True
        assert client.token == "test_token"

        result_holder: dict[str, object] = {}

        def worker():
            try:
                result_holder["result"] = client.set_token("new_token_from_worker")
            except Exception as e:  # noqa: BLE001
                result_holder["error"] = e

        async def main():
            # 在 async 上下文中捕获事件循环引用（worker 线程依赖此引用调度协程）
            client._capture_loop()
            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            # 让事件循环处理 run_coroutine_threadsafe 提交的协程
            while thread.is_alive():
                await asyncio.sleep(0.005)
            thread.join()

        asyncio.run(main())

        # 断言无异常
        assert "error" not in result_holder, f"worker thread raised: {result_holder.get('error')}"
        # 返回值 True
        assert result_holder.get("result") is True
        # token 已更新
        assert client.token == "new_token_from_worker"
        # capability_cache 被清空
        assert client._capability_cache == {}
        # _token_invalid 被重置
        assert client.is_token_invalid is False


class TestInitWithDifferentToken:
    """__init__ 二次实例化 token 变更路径（DoD ⑦：__init__ 调用路径无回归）。"""

    def test_init_with_different_token_in_async_context(self, tushare_client_mocks):
        """async 上下文中 TushareClient(token="new") 二次调用不应抛 RuntimeError。

        回归场景：原 __init__ 内 self.set_token(token) 在事件循环线程中调用
        会抛 RuntimeError（set_token 明确拒绝在事件循环线程中调用以避免死锁）。
        修复后走 _set_token_core 路径，不依赖事件循环调度。
        """
        client, _, _ = tushare_client_mocks
        assert client.token == "test_token"

        async def call_init_in_loop():
            # 二次实例化（单例）传入不同 token，不应抛 RuntimeError
            new_client = TushareClient(token="new_token_in_async")
            return new_client

        new_client = asyncio.run(call_init_in_loop())

        # 单例：返回同一实例
        assert new_client is client
        # token 已更新
        assert client.token == "new_token_in_async"

    def test_init_with_different_token_in_sync_context(self, tushare_client_mocks):
        """sync 上下文中 TushareClient(token="new") 二次调用不应抛 RuntimeError。

        回归场景：原 __init__ 内 self.set_token(token) 在无运行 loop 的同步上下文中
        也会抛 RuntimeError（set_token 要求有运行 loop 才能调度协程）。
        修复后走 _set_token_core 路径，不依赖事件循环调度。
        """
        client, _, _ = tushare_client_mocks
        assert client.token == "test_token"
        # 确保 _loop 为 None（sync 上下文）
        client._loop = None

        # 二次实例化（单例）传入不同 token，不应抛 RuntimeError
        new_client = TushareClient(token="new_token_in_sync")

        # 单例：返回同一实例
        assert new_client is client
        # token 已更新
        assert client.token == "new_token_in_sync"
