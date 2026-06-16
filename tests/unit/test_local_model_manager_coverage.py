"""services/local_model_manager.py 补充测试 - _persistent_worker、run_inference取消场景"""

import queue
from unittest.mock import MagicMock, patch

import pytest

from services.local_model_manager import (
    LocalModelManager,
    _SENTINEL,
    _persistent_worker,
)


class TestPersistentWorkerImportFailure:
    def test_llama_cpp_import_failure(self):
        mock_req_queue = MagicMock()
        mock_res_queue = MagicMock()

        with patch("importlib.import_module", side_effect=ImportError("no module")):
            _persistent_worker("/path/to/model.gguf", {}, mock_req_queue, mock_res_queue)

        mock_res_queue.put.assert_called_once()
        call_args = mock_res_queue.put.call_args[0][0]
        assert call_args[0] == "error"
        assert "llama-cpp-python import failed" in call_args[1]

    def test_llama_cpp_attribute_failure(self):
        mock_req_queue = MagicMock()
        mock_res_queue = MagicMock()

        with patch("importlib.import_module", side_effect=AttributeError("no attr")):
            _persistent_worker("/path/to/model.gguf", {}, mock_req_queue, mock_res_queue)

        mock_res_queue.put.assert_called_once()
        call_args = mock_res_queue.put.call_args[0][0]
        assert call_args[0] == "error"


class TestPersistentWorkerModelLoadFailure:
    def test_model_load_failure(self):
        mock_req_queue = MagicMock()
        mock_res_queue = MagicMock()

        mock_llama_module = MagicMock()
        mock_llama_module.Llama.side_effect = Exception("model not found")

        with patch("importlib.import_module", return_value=mock_llama_module):
            _persistent_worker("/path/to/model.gguf", {}, mock_req_queue, mock_res_queue)

        mock_res_queue.put.assert_called_once()
        call_args = mock_res_queue.put.call_args[0][0]
        assert call_args[0] == "error"
        assert "Model load failed" in call_args[1]


class TestPersistentWorkerInvalidRequest:
    def test_invalid_request_format_not_tuple(self):
        mock_req_queue = MagicMock()
        mock_res_queue = MagicMock()

        mock_llama_module = MagicMock()
        mock_llama = MagicMock()
        mock_llama_module.Llama.return_value = mock_llama

        mock_req_queue.get.side_effect = ["invalid_request", _SENTINEL]

        with patch("importlib.import_module", return_value=mock_llama_module):
            _persistent_worker("/path/to/model.gguf", {}, mock_req_queue, mock_res_queue)

        error_calls = [c for c in mock_res_queue.put.call_args_list if c[0][0][0] == "error"]
        assert len(error_calls) >= 1
        assert "Invalid request format" in error_calls[0][0][0][1]

    def test_invalid_request_format_wrong_length(self):
        mock_req_queue = MagicMock()
        mock_res_queue = MagicMock()

        mock_llama_module = MagicMock()
        mock_llama = MagicMock()
        mock_llama_module.Llama.return_value = mock_llama

        mock_req_queue.get.side_effect = [("prompt", 100), _SENTINEL]

        with patch("importlib.import_module", return_value=mock_llama_module):
            _persistent_worker("/path/to/model.gguf", {}, mock_req_queue, mock_res_queue)

        error_calls = [c for c in mock_res_queue.put.call_args_list if c[0][0][0] == "error"]
        assert len(error_calls) >= 1


class TestPersistentWorkerInferenceError:
    def test_inference_error(self):
        mock_req_queue = MagicMock()
        mock_res_queue = MagicMock()

        mock_llama_module = MagicMock()
        mock_llama = MagicMock()
        mock_llama.create_chat_completion.side_effect = Exception("inference failed")
        mock_llama_module.Llama.return_value = mock_llama

        mock_req_queue.get.side_effect = [("prompt", 100, 0.7, "system"), _SENTINEL]

        with patch("importlib.import_module", return_value=mock_llama_module):
            _persistent_worker("/path/to/model.gguf", {}, mock_req_queue, mock_res_queue)

        error_calls = [c for c in mock_res_queue.put.call_args_list if c[0][0][0] == "error"]
        assert len(error_calls) >= 1
        assert "inference failed" in error_calls[-1][0][0][1]


class TestPersistentWorkerQueueGetError:
    def test_queue_get_error_continues(self):
        mock_req_queue = MagicMock()
        mock_res_queue = MagicMock()

        mock_llama_module = MagicMock()
        mock_llama = MagicMock()
        mock_llama_module.Llama.return_value = mock_llama

        mock_req_queue.get.side_effect = [Exception("queue error"), _SENTINEL]

        with patch("importlib.import_module", return_value=mock_llama_module):
            _persistent_worker("/path/to/model.gguf", {}, mock_req_queue, mock_res_queue)

        ready_calls = [c for c in mock_res_queue.put.call_args_list if c[0][0][0] == "ready"]
        assert len(ready_calls) == 1


class TestPersistentWorkerSentinelShutdown:
    def test_sentinel_shutdown(self):
        mock_req_queue = MagicMock()
        mock_res_queue = MagicMock()

        mock_llama_module = MagicMock()
        mock_llama = MagicMock()
        mock_llama_module.Llama.return_value = mock_llama

        mock_req_queue.get.side_effect = [_SENTINEL]

        with patch("importlib.import_module", return_value=mock_llama_module):
            _persistent_worker("/path/to/model.gguf", {}, mock_req_queue, mock_res_queue)

        shutdown_calls = [c for c in mock_res_queue.put.call_args_list if c[0][0][0] == "shutdown"]
        assert len(shutdown_calls) == 1


class TestLocalModelManagerShutdownWorkerEdgeCases:
    def test_shutdown_worker_queue_close_error(self):
        mgr = LocalModelManager()
        mock_req_queue = MagicMock()
        mock_res_queue = MagicMock()
        mock_proc = MagicMock()
        mock_proc.is_alive.return_value = True

        mock_req_queue.put.side_effect = Exception("queue closed")
        mock_req_queue.close.side_effect = Exception("close error")
        mock_res_queue.close.side_effect = Exception("close error")

        mgr._request_queue = mock_req_queue
        mgr._result_queue = mock_res_queue
        mgr._worker_proc = mock_proc
        mgr._worker_ready = True

        mgr._shutdown_worker()

        assert mgr._worker_proc is None
        assert mgr._worker_ready is False

    def test_shutdown_worker_join_thread_error(self):
        mgr = LocalModelManager()
        mock_req_queue = MagicMock()
        mock_res_queue = MagicMock()
        mock_proc = MagicMock()
        mock_proc.is_alive.return_value = False

        mock_req_queue.join_thread.side_effect = Exception("join error")
        mock_res_queue.join_thread.side_effect = Exception("join error")

        mgr._request_queue = mock_req_queue
        mgr._result_queue = mock_res_queue
        mgr._worker_proc = mock_proc
        mgr._worker_ready = True

        mgr._shutdown_worker()

        assert mgr._worker_proc is None


class TestLocalModelManagerEnsureWorkerEdgeCases:
    def test_ensure_worker_os_error_on_queue_get(self):
        mgr = LocalModelManager()

        with (
            patch.object(mgr, "_shutdown_worker"),
            patch("services.local_model_manager.multiprocessing.Process") as mock_proc_cls,
            patch("services.local_model_manager.multiprocessing.Queue") as mock_queue_cls,
        ):
            mock_res_queue = MagicMock()
            mock_req_queue = MagicMock()
            mock_queue_cls.side_effect = [mock_req_queue, mock_res_queue]

            mock_proc = MagicMock()
            mock_proc.is_alive.return_value = True
            mock_proc_cls.return_value = mock_proc

            result = mgr._ensure_worker("/path/to/model.gguf", {})
            assert result is True

    @pytest.mark.asyncio
    async def test_await_worker_ready_os_error(self):
        mgr = LocalModelManager()
        mgr._worker_ready = False
        mgr._result_queue = MagicMock()
        mgr._result_queue.get_nowait = MagicMock(side_effect=OSError("os error"))
        mgr._worker_proc = None

        with patch.object(mgr, "_shutdown_worker"):
            result = await mgr._await_worker_ready(timeout=0.1)
            assert result is False
            mgr._shutdown_worker.assert_called()

    def test_ensure_worker_timeout_error_on_queue_get(self):
        mgr = LocalModelManager()

        with (
            patch.object(mgr, "_shutdown_worker"),
            patch("services.local_model_manager.multiprocessing.Process") as mock_proc_cls,
            patch("services.local_model_manager.multiprocessing.Queue") as mock_queue_cls,
        ):
            mock_res_queue = MagicMock()
            mock_req_queue = MagicMock()
            mock_queue_cls.side_effect = [mock_req_queue, mock_res_queue]

            mock_proc = MagicMock()
            mock_proc.is_alive.return_value = True
            mock_proc_cls.return_value = mock_proc

            result = mgr._ensure_worker("/path/to/model.gguf", {})
            assert result is True

    @pytest.mark.asyncio
    async def test_await_worker_ready_timeout_error(self):
        import queue as queue_mod

        mgr = LocalModelManager()
        mgr._worker_ready = False
        mgr._result_queue = MagicMock()
        mgr._result_queue.get_nowait = MagicMock(side_effect=queue_mod.Empty)
        mgr._worker_proc = None

        with patch.object(mgr, "_shutdown_worker"):
            result = await mgr._await_worker_ready(timeout=0.1)
            assert result is False
            mgr._shutdown_worker.assert_called()


class TestLocalModelManagerLoadModelOSError:
    @pytest.mark.asyncio
    async def test_load_model_os_error_on_stat(self):
        from unittest.mock import AsyncMock

        with patch("services.local_model_manager._HAS_LLAMA_CPP", True):
            mgr = LocalModelManager()

            with (
                patch("os.path.exists", return_value=True),
                patch("os.stat", side_effect=OSError("stat error")),
                patch.object(LocalModelManager, "_get_load_lock"),
                patch.object(mgr, "_ensure_worker", return_value=True),
                patch.object(mgr, "_await_worker_ready", return_value=True),
                patch("services.local_model_manager.ThreadPoolManager") as mock_tpm,
            ):
                mock_tpm.return_value.run_async = AsyncMock(return_value="abc123")

                result = await mgr.load_model("/path/to/model.gguf", config={"n_threads": 4})
                assert result is True
                assert mgr._model_stat == (0, 0)


class TestLocalModelManagerRunInferenceWorkerDiesDuringPolling:
    @pytest.mark.asyncio
    async def test_worker_dies_after_first_poll(self):
        with patch("services.local_model_manager._HAS_LLAMA_CPP", True):
            mgr = LocalModelManager()
            mgr._model_path = "/path/to/model.gguf"

            with (
                patch("services.local_model_manager.ConfigHandler") as mock_ch,
                patch.object(mgr, "_ensure_worker", return_value=True),
                patch.object(mgr, "_await_worker_ready", return_value=True),
            ):
                mock_ch.get_local_ai_config.return_value = {
                    "local_model_path": "/path/to/model.gguf",
                    "local_model_timeout": 30,
                }

                mock_req_queue = MagicMock()
                mock_res_queue = MagicMock()

                poll_count = 0

                def mock_get_nowait():
                    nonlocal poll_count
                    poll_count += 1
                    if poll_count == 1:
                        raise queue.Empty()
                    return ("ok", "result")

                mock_res_queue.get_nowait.side_effect = mock_get_nowait

                mock_proc = MagicMock()
                mock_proc.is_alive.side_effect = [True, False]

                mgr._request_queue = mock_req_queue
                mgr._result_queue = mock_res_queue
                mgr._worker_ready = True
                mgr._worker_proc = mock_proc

                result = await mgr.run_inference("test prompt")
                assert result == "result"


class TestLocalModelManagerRunInferenceDeadlineReached:
    @pytest.mark.asyncio
    async def test_deadline_reached_with_result_at_end(self):
        with patch("services.local_model_manager._HAS_LLAMA_CPP", True):
            mgr = LocalModelManager()
            mgr._model_path = "/path/to/model.gguf"

            with (
                patch("services.local_model_manager.ConfigHandler") as mock_ch,
                patch.object(mgr, "_ensure_worker", return_value=True),
                patch.object(mgr, "_await_worker_ready", return_value=True),
            ):
                mock_ch.get_local_ai_config.return_value = {
                    "local_model_path": "/path/to/model.gguf",
                    "local_model_timeout": 2.0,
                }

                mock_req_queue = MagicMock()
                mock_res_queue = MagicMock()

                call_count = 0

                def mock_get_nowait():
                    nonlocal call_count
                    call_count += 1
                    if call_count <= 5:
                        raise queue.Empty()
                    return ("ok", "late result")

                mock_res_queue.get_nowait.side_effect = mock_get_nowait

                mock_proc = MagicMock()
                mock_proc.is_alive.return_value = True

                mgr._request_queue = mock_req_queue
                mgr._result_queue = mock_res_queue
                mgr._worker_ready = True
                mgr._worker_proc = mock_proc

                result = await mgr.run_inference("test prompt")
                assert result == "late result"


class TestLocalModelManagerInit:
    def test_init_returns_if_already_initialized(self):
        # Create a real instance first, then verify __init__ skips on second call
        mgr = LocalModelManager()
        saved_config = mgr._last_config  # Should be {} from first init

        # Call constructor again — __init__ should skip due to _initialized=True
        mgr2 = LocalModelManager()
        assert mgr2 is mgr  # Same instance (singleton)
        assert mgr2._last_config == saved_config  # Not re-initialized
        assert mgr2._initialized is True


class TestLocalModelManagerGetLoadLock:
    @pytest.mark.asyncio
    async def test_get_load_lock_creates_new_lock_per_loop(self):
        lock1 = LocalModelManager._get_load_lock()
        lock2 = LocalModelManager._get_load_lock()

        assert lock1 is not None
        assert lock2 is not None


class TestLocalModelManagerCancelEventSetBeforeInference:
    @pytest.mark.asyncio
    async def test_cancel_event_set_before_inference_starts(self):
        with patch("services.local_model_manager._HAS_LLAMA_CPP", True):
            mgr = LocalModelManager()
            mgr._worker_ready = True
            mgr._model_path = "/fake/model.gguf"
            mgr._worker_proc = MagicMock()
            mgr._worker_proc.is_alive.return_value = True
            mgr._request_queue = MagicMock()
            mgr._result_queue = MagicMock()
            mgr._result_queue.get_nowait.side_effect = queue.Empty
            mgr._cancel_event.set()

            with (
                patch.object(mgr, "_shutdown_worker"),
                patch(
                    "services.local_model_manager.ConfigHandler.get_local_ai_config",
                    return_value={"local_model_path": "/fake/model.gguf", "local_model_timeout": 90},
                ),
            ):
                with pytest.raises(RuntimeError, match="Inference cancelled"):
                    await mgr.run_inference("test prompt")

                mgr._shutdown_worker.assert_called()


class TestLocalModelManagerWorkerDiesWithoutResult:
    @pytest.mark.asyncio
    async def test_worker_dies_no_result_in_queue(self):
        with patch("services.local_model_manager._HAS_LLAMA_CPP", True):
            mgr = LocalModelManager()
            mgr._model_path = "/path/to/model.gguf"

            with (
                patch("services.local_model_manager.ConfigHandler") as mock_ch,
                patch.object(mgr, "_ensure_worker", return_value=True),
                patch.object(mgr, "_await_worker_ready", return_value=True),
            ):
                mock_ch.get_local_ai_config.return_value = {
                    "local_model_path": "/path/to/model.gguf",
                    "local_model_timeout": 30,
                }

                mock_req_queue = MagicMock()
                mock_res_queue = MagicMock()
                mock_res_queue.get_nowait.side_effect = queue.Empty
                mgr._request_queue = mock_req_queue
                mgr._result_queue = mock_res_queue
                mgr._worker_ready = True
                mgr._worker_proc = MagicMock()
                mgr._worker_proc.is_alive.return_value = False

                with pytest.raises(RuntimeError, match="exited without producing"):
                    await mgr.run_inference("test prompt")

                assert mgr._worker_ready is False
