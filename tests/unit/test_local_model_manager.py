import queue

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from services.local_model_manager import LocalModelManager, _HAS_LLAMA_CPP


@pytest.fixture(autouse=True)
def reset_singleton():
    LocalModelManager._instance = None
    LocalModelManager._initialized = False
    LocalModelManager._model_path = ""
    LocalModelManager._model_md5 = ""
    LocalModelManager._model_stat = (0, 0)
    LocalModelManager._last_config = {}
    LocalModelManager._is_loading = False
    LocalModelManager._cancel_event.clear()
    LocalModelManager._worker_proc = None
    LocalModelManager._request_queue = None
    LocalModelManager._result_queue = None
    LocalModelManager._worker_ready = False
    yield
    LocalModelManager._instance = None
    LocalModelManager._initialized = False
    LocalModelManager._model_path = ""
    LocalModelManager._model_md5 = ""
    LocalModelManager._model_stat = (0, 0)
    LocalModelManager._last_config = {}
    LocalModelManager._is_loading = False
    LocalModelManager._cancel_event.clear()
    LocalModelManager._worker_proc = None
    LocalModelManager._request_queue = None
    LocalModelManager._result_queue = None
    LocalModelManager._worker_ready = False


class TestLocalModelManagerGetLoadedModelPath:
    def test_no_model_loaded(self):
        mgr = LocalModelManager()
        assert mgr.get_loaded_model_path() == ""

    def test_with_model_path(self):
        mgr = LocalModelManager()
        mgr._model_path = "/path/to/model.gguf"
        assert mgr.get_loaded_model_path() == "/path/to/model.gguf"


class TestLocalModelManagerGetLoadedModelMd5:
    def test_no_model_loaded(self):
        mgr = LocalModelManager()
        assert mgr.get_loaded_model_md5() == ""

    def test_with_md5(self):
        mgr = LocalModelManager()
        mgr._model_md5 = "abc123"
        assert mgr.get_loaded_model_md5() == "abc123"


class TestLocalModelManagerCalculateFileMd5:
    def test_nonexistent_file(self):
        result = LocalModelManager.calculate_file_md5("/nonexistent/file.gguf")
        assert result == ""


class TestLocalModelManagerGetInstance:
    @pytest.mark.asyncio
    async def test_creates_instance(self):
        instance = await LocalModelManager.get_instance()
        assert instance is not None


class TestLocalModelManagerUnloadModel:
    def test_unload_no_model(self):
        mgr = LocalModelManager()
        mgr.unload_model()
        assert mgr._worker_ready is False
        assert mgr._model_path == ""

    def test_unload_with_model(self):
        mgr = LocalModelManager()
        mgr._worker_ready = True
        mgr._model_path = "/path/to/model.gguf"
        with patch.object(mgr, "_shutdown_worker"):
            mgr.unload_model()
        assert mgr._model_path == ""
        assert mgr._model_md5 == ""
        assert mgr._model_stat == (0, 0)
        assert mgr._last_config == {}


class TestLocalModelManagerLoadModel:
    @pytest.mark.asyncio
    async def test_no_llama_cpp(self):
        with patch("services.local_model_manager._HAS_LLAMA_CPP", False):
            mgr = LocalModelManager()
            result = await mgr.load_model("/path/to/model.gguf")
            assert result is False


class TestPersistentWorkerEnsureWorker:
    def test_ensure_worker_returns_true_if_already_ready(self):
        mgr = LocalModelManager()
        mgr._worker_proc = MagicMock()
        mgr._worker_proc.is_alive.return_value = True
        mgr._worker_ready = True
        mgr._model_path = "/path/to/model.gguf"

        result = mgr._ensure_worker("/path/to/model.gguf", {})
        assert result is True

    def test_ensure_worker_restarts_if_model_path_changed(self):
        mgr = LocalModelManager()
        mgr._worker_proc = MagicMock()
        mgr._worker_proc.is_alive.return_value = True
        mgr._worker_ready = True
        mgr._model_path = "/old/model.gguf"

        with (
            patch.object(mgr, "_shutdown_worker_locked"),
            patch("services.local_model_manager.multiprocessing.Process") as mock_proc_cls,
            patch("services.local_model_manager.multiprocessing.Queue") as mock_queue_cls,
        ):
            mock_res_queue = MagicMock()
            mock_res_queue.get.return_value = ("ready", "/new/model.gguf")
            mock_req_queue = MagicMock()
            mock_queue_cls.side_effect = [mock_req_queue, mock_res_queue]

            mock_proc = MagicMock()
            mock_proc.is_alive.return_value = True
            mock_proc_cls.return_value = mock_proc

            result = mgr._ensure_worker("/new/model.gguf", {"n_threads": 4})
            assert result is True
            mgr._shutdown_worker_locked.assert_called_once()

    def test_ensure_worker_returns_true_on_process_start(self):
        mgr = LocalModelManager()
        mgr._worker_proc = None
        mgr._worker_ready = False

        with (
            patch.object(mgr, "_shutdown_worker_locked"),
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
    async def test_await_worker_ready_returns_false_on_load_failure(self):
        mgr = LocalModelManager()
        mgr._worker_ready = False
        mgr._result_queue = MagicMock()
        mgr._result_queue.get_nowait.return_value = ("error", "Model load failed: no file")
        mgr._worker_proc = MagicMock()
        mgr._worker_proc.is_alive.return_value = True

        with patch.object(mgr, "_shutdown_worker"):
            result = await mgr._await_worker_ready()
            assert result is False
            assert mgr._worker_ready is False
            mgr._shutdown_worker.assert_called()

    @pytest.mark.asyncio
    async def test_await_worker_ready_returns_true_on_ready(self):
        mgr = LocalModelManager()
        mgr._worker_ready = False
        mgr._result_queue = MagicMock()
        mgr._result_queue.get_nowait.return_value = ("ready", "/path/to/model.gguf")
        mgr._worker_proc = MagicMock()
        mgr._worker_proc.is_alive.return_value = True

        result = await mgr._await_worker_ready()
        assert result is True
        assert mgr._worker_ready is True

    @pytest.mark.asyncio
    async def test_await_worker_ready_detects_worker_crash(self):
        mgr = LocalModelManager()
        mgr._worker_ready = False
        mgr._result_queue = MagicMock()
        mgr._result_queue.get_nowait.side_effect = queue.Empty
        mgr._worker_proc = MagicMock()
        mgr._worker_proc.is_alive.return_value = False
        mgr._worker_proc.exitcode = -11

        with patch.object(mgr, "_shutdown_worker"):
            result = await mgr._await_worker_ready(timeout=1)
            assert result is False
            assert mgr._worker_ready is False
            mgr._shutdown_worker.assert_called()

    @pytest.mark.asyncio
    async def test_await_worker_ready_respects_cancel_event(self):
        mgr = LocalModelManager()
        mgr._worker_ready = False
        mgr._result_queue = MagicMock()
        mgr._result_queue.get_nowait.side_effect = queue.Empty
        mgr._worker_proc = MagicMock()
        mgr._worker_proc.is_alive.return_value = True
        mgr._cancel_event.set()

        with patch.object(mgr, "_shutdown_worker"):
            result = await mgr._await_worker_ready(timeout=1)
            assert result is False
            mgr._shutdown_worker.assert_called()

    @pytest.mark.asyncio
    async def test_await_worker_ready_no_result_queue(self):
        mgr = LocalModelManager()
        mgr._result_queue = None

        result = await mgr._await_worker_ready()
        assert result is False


class TestPersistentWorkerShutdownWorker:
    def test_shutdown_worker_sends_sentinel(self):
        from services.local_model_manager import _SENTINEL

        mgr = LocalModelManager()
        mock_req_queue = MagicMock()
        mock_proc = MagicMock()
        mock_proc.is_alive.return_value = True
        mock_proc.join.return_value = None
        mgr._request_queue = mock_req_queue
        mgr._worker_proc = mock_proc
        mgr._result_queue = MagicMock()
        mgr._worker_ready = True

        mgr._shutdown_worker()

        mock_req_queue.put.assert_called_once_with(_SENTINEL, timeout=2.0)
        assert mgr._worker_proc is None
        assert mgr._worker_ready is False

    def test_shutdown_worker_terminates_if_join_times_out(self):
        mgr = LocalModelManager()
        mock_req_queue = MagicMock()
        mock_proc = MagicMock()
        mock_proc.is_alive.side_effect = [True, True, False]
        mock_proc.join.return_value = None
        mgr._request_queue = mock_req_queue
        mgr._worker_proc = mock_proc
        mgr._result_queue = MagicMock()
        mgr._worker_ready = True

        mgr._shutdown_worker()

        mock_proc.terminate.assert_called()

    def test_shutdown_worker_kills_if_terminate_fails(self):
        mgr = LocalModelManager()
        mock_req_queue = MagicMock()
        mock_proc = MagicMock()
        mock_proc.is_alive.side_effect = [True, True, True, False]
        mock_proc.join.return_value = None
        mgr._request_queue = mock_req_queue
        mgr._worker_proc = mock_proc
        mgr._result_queue = MagicMock()
        mgr._worker_ready = True

        mgr._shutdown_worker()

        mock_proc.kill.assert_called()

    def test_shutdown_worker_noop_if_no_proc(self):
        mgr = LocalModelManager()
        mgr._worker_proc = None
        mgr._request_queue = None
        mgr._result_queue = None
        mgr._shutdown_worker()
        assert mgr._worker_proc is None
        assert mgr._worker_ready is False

    def test_unload_model_calls_shutdown_worker(self):
        mgr = LocalModelManager()
        with patch.object(mgr, "_shutdown_worker") as mock_sw:
            mgr.unload_model()
            mock_sw.assert_called_once()


class TestPersistentWorkerModelReuse:
    @pytest.mark.asyncio
    async def test_second_inference_reuses_worker(self):
        with patch("services.local_model_manager._HAS_LLAMA_CPP", True):
            mgr = LocalModelManager()
            mgr._model_path = "/path/to/model.gguf"

            ensure_call_count = 0

            def mock_ensure(model_path, core_config):
                nonlocal ensure_call_count
                ensure_call_count += 1
                mgr._worker_ready = True
                mgr._worker_proc = MagicMock()
                mgr._worker_proc.is_alive.return_value = True
                if mgr._request_queue is None:
                    mgr._request_queue = MagicMock()
                if mgr._result_queue is None:
                    mgr._result_queue = MagicMock()
                return True

            with (
                patch("services.local_model_manager.ConfigHandler") as mock_ch,
                patch.object(mgr, "_ensure_worker", side_effect=mock_ensure),
            ):
                mock_ch.get_local_ai_config.return_value = {
                    "local_model_path": "/path/to/model.gguf",
                    "local_model_timeout": 30,
                }

                mgr._result_queue = MagicMock()
                mgr._result_queue.get_nowait.return_value = ("ok", "first result")
                result1 = await mgr.run_inference("first prompt")
                assert result1 == "first result"

                mgr._result_queue.get_nowait.return_value = ("ok", "second result")
                result2 = await mgr.run_inference("second prompt")
                assert result2 == "second result"

                assert ensure_call_count == 2

    @pytest.mark.asyncio
    async def test_file_not_found(self):
        with patch("services.local_model_manager._HAS_LLAMA_CPP", True):
            mgr = LocalModelManager()
            result = await mgr.load_model("/nonexistent/model.gguf")
            assert result is False

    @pytest.mark.asyncio
    async def test_same_model_skip_reload(self):
        with patch("services.local_model_manager._HAS_LLAMA_CPP", True):
            mgr = LocalModelManager()
            mgr._worker_ready = True
            mgr._model_path = "/path/to/model.gguf"
            mgr._model_stat = (0, 0)
            mgr._last_config = {"n_threads": 4, "n_batch": 1024, "n_ctx": 4096, "n_gpu_layers": 0, "flash_attn": True}
            with (
                patch("os.path.exists", return_value=True),
                patch("os.stat", return_value=MagicMock(st_mtime=0, st_size=0)),
            ):
                result = await mgr.load_model(
                    "/path/to/model.gguf",
                    config={"n_threads": 4, "n_batch": 1024, "n_ctx": 4096, "n_gpu_layers": 0, "flash_attn": True},
                )
                assert result is True


class TestLocalModelManagerRunInference:
    @pytest.mark.asyncio
    async def test_no_llama_cpp(self):
        with patch("services.local_model_manager._HAS_LLAMA_CPP", False):
            mgr = LocalModelManager()
            with pytest.raises(ImportError):
                await mgr.run_inference("test prompt")

    @pytest.mark.asyncio
    async def test_no_model_configured(self):
        with patch("services.local_model_manager._HAS_LLAMA_CPP", True):
            mgr = LocalModelManager()
            with patch("utils.config_handler.ConfigHandler.get_local_ai_config", return_value={"local_model_path": ""}):
                with pytest.raises(RuntimeError, match="not configured"):
                    await mgr.run_inference("test prompt")


class TestLocalModelManagerCalculateMd5:
    def test_nonexistent_file(self):
        result = LocalModelManager.calculate_file_md5("/nonexistent/file.gguf")
        assert result == ""


class TestLocalModelManagerConstants:
    def test_has_llama_cpp_is_bool(self):
        assert isinstance(_HAS_LLAMA_CPP, bool)


class TestLocalModelManagerGetModelInfo:
    def setup_method(self):
        LocalModelManager._instance = None
        LocalModelManager._initialized = False

    def teardown_method(self):
        LocalModelManager._instance = None
        LocalModelManager._initialized = False

    def test_get_loaded_model_path_empty(self):
        mgr = LocalModelManager.__new__(LocalModelManager)
        mgr._model_path = ""
        assert mgr.get_loaded_model_path() == ""

    def test_get_loaded_model_md5_empty(self):
        mgr = LocalModelManager.__new__(LocalModelManager)
        mgr._model_md5 = ""
        assert mgr.get_loaded_model_md5() == ""

    def test_get_loaded_model_path_set(self):
        mgr = LocalModelManager.__new__(LocalModelManager)
        mgr._model_path = "/path/to/model.gguf"
        assert mgr.get_loaded_model_path() == "/path/to/model.gguf"


class TestLocalModelManagerGetters:
    def setup_method(self):
        LocalModelManager._instance = None
        LocalModelManager._initialized = False

    def teardown_method(self):
        LocalModelManager._instance = None
        LocalModelManager._initialized = False

    def test_get_loaded_model_path_empty(self):
        LocalModelManager._model_path = ""
        assert LocalModelManager._model_path == ""

    def test_get_loaded_model_md5_empty(self):
        LocalModelManager._model_md5 = ""
        assert LocalModelManager._model_md5 == ""


class TestLocalModelManagerReset:
    def test_reset_clears_instance(self):
        LocalModelManager._instance = MagicMock()
        LocalModelManager._reset_singleton()
        assert LocalModelManager._instance is None


class TestLocalModelManagerCalculateFileMd5Success:
    def test_with_real_file(self, tmp_path):
        p = tmp_path / "model.gguf"
        p.write_bytes(b"hello world")
        result = LocalModelManager.calculate_file_md5(str(p))
        assert result != ""


class TestLocalModelManagerLoadModelWithLlama:
    @pytest.mark.asyncio
    async def test_load_model_success(self):
        with patch("services.local_model_manager._HAS_LLAMA_CPP", True):
            mgr = LocalModelManager()
            with (
                patch("os.path.exists", return_value=True),
                patch("os.stat", return_value=MagicMock(st_mtime=100, st_size=999)),
                patch.object(LocalModelManager, "_get_load_lock"),
                patch("services.local_model_manager.ThreadPoolManager") as mock_tpm,
                patch.object(mgr, "_ensure_worker", return_value=True),
                patch.object(mgr, "_await_worker_ready", return_value=True),
            ):
                mock_tpm.return_value.run_async = AsyncMock(return_value="abc123")
                result = await mgr.load_model("/path/to/model.gguf", config={"n_threads": 2})
                assert result is True
                assert mgr._model_path == "/path/to/model.gguf"
                assert mgr._model_md5 == "abc123"

    @pytest.mark.asyncio
    async def test_load_model_failure(self):
        with patch("services.local_model_manager._HAS_LLAMA_CPP", True):
            mgr = LocalModelManager()
            with (
                patch("os.path.exists", return_value=True),
                patch("os.stat", return_value=MagicMock(st_mtime=100, st_size=999)),
                patch.object(LocalModelManager, "_get_load_lock"),
                patch("services.local_model_manager.ThreadPoolManager") as mock_tpm,
            ):
                mock_tpm.return_value.run_async = AsyncMock(side_effect=Exception("load failed"))
                result = await mgr.load_model("/path/to/model.gguf")
                assert result is False
                assert mgr._model_path == ""

    @pytest.mark.asyncio
    async def test_load_model_no_config(self):
        with patch("services.local_model_manager._HAS_LLAMA_CPP", True):
            mgr = LocalModelManager()
            with (
                patch("os.path.exists", return_value=True),
                patch("os.stat", return_value=MagicMock(st_mtime=100, st_size=999)),
                patch.object(LocalModelManager, "_get_load_lock"),
                patch("services.local_model_manager.ThreadPoolManager") as mock_tpm,
                patch("services.local_model_manager.ConfigHandler") as mock_ch,
                patch.object(mgr, "_ensure_worker", return_value=True),
                patch.object(mgr, "_await_worker_ready", return_value=True),
            ):
                mock_ch.get_local_ai_config.return_value = {"n_threads": 4}
                mock_tpm.return_value.run_async = AsyncMock(return_value="md5val")
                result = await mgr.load_model("/path/to/model.gguf")
                assert result is True

    @pytest.mark.asyncio
    async def test_load_model_ensure_worker_fails(self):
        with patch("services.local_model_manager._HAS_LLAMA_CPP", True):
            mgr = LocalModelManager()
            with (
                patch("os.path.exists", return_value=True),
                patch("os.stat", return_value=MagicMock(st_mtime=100, st_size=999)),
                patch.object(LocalModelManager, "_get_load_lock"),
                patch("services.local_model_manager.ThreadPoolManager") as mock_tpm,
                patch.object(mgr, "_ensure_worker", return_value=False),
            ):
                mock_tpm.return_value.run_async = AsyncMock(return_value="abc123")
                result = await mgr.load_model("/path/to/model.gguf", config={"n_threads": 2})
                assert result is False

    @pytest.mark.asyncio
    async def test_load_model_await_worker_fails(self):
        with patch("services.local_model_manager._HAS_LLAMA_CPP", True):
            mgr = LocalModelManager()
            with (
                patch("os.path.exists", return_value=True),
                patch("os.stat", return_value=MagicMock(st_mtime=100, st_size=999)),
                patch.object(LocalModelManager, "_get_load_lock"),
                patch("services.local_model_manager.ThreadPoolManager") as mock_tpm,
                patch.object(mgr, "_ensure_worker", return_value=True),
                patch.object(mgr, "_await_worker_ready", return_value=False),
            ):
                mock_tpm.return_value.run_async = AsyncMock(return_value="abc123")
                result = await mgr.load_model("/path/to/model.gguf", config={"n_threads": 2})
                assert result is False


class TestLocalModelManagerRunInferenceWithModel:
    @pytest.mark.asyncio
    async def test_run_inference_success(self):
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
                mock_res_queue.get_nowait.return_value = ("ok", "result text")
                mgr._request_queue = mock_req_queue
                mgr._result_queue = mock_res_queue
                mgr._worker_ready = True
                mgr._worker_proc = MagicMock()
                mgr._worker_proc.is_alive.return_value = True

                result = await mgr.run_inference("test prompt")
                assert result == "result text"

    @pytest.mark.asyncio
    async def test_run_inference_timeout(self):
        from services.local_model_manager import LocalInferenceTimeoutError

        with patch("services.local_model_manager._HAS_LLAMA_CPP", True):
            mgr = LocalModelManager()
            mgr._model_path = "/path/to/model.gguf"
            with (
                patch("services.local_model_manager.ConfigHandler") as mock_ch,
                patch.object(mgr, "_ensure_worker", return_value=True),
                patch.object(mgr, "_await_worker_ready", return_value=True),
                patch.object(mgr, "_shutdown_worker"),
            ):
                mock_ch.get_local_ai_config.return_value = {
                    "local_model_path": "/path/to/model.gguf",
                    "local_model_timeout": 1,
                }

                mock_req_queue = MagicMock()
                mock_res_queue = MagicMock()
                mock_res_queue.get_nowait.side_effect = queue.Empty
                mgr._request_queue = mock_req_queue
                mgr._result_queue = mock_res_queue
                mgr._worker_ready = True
                mgr._worker_proc = MagicMock()
                mgr._worker_proc.is_alive.return_value = True

                with pytest.raises(LocalInferenceTimeoutError):
                    await mgr.run_inference("test prompt")

                mgr._shutdown_worker.assert_called()

    @pytest.mark.asyncio
    async def test_run_inference_error_response(self):
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
                mock_res_queue.get_nowait.return_value = ("error", "something went wrong")
                mgr._request_queue = mock_req_queue
                mgr._result_queue = mock_res_queue
                mgr._worker_ready = True
                mgr._worker_proc = MagicMock()
                mgr._worker_proc.is_alive.return_value = True

                with pytest.raises(RuntimeError, match="Inference execution failed"):
                    await mgr.run_inference("test prompt")

    @pytest.mark.asyncio
    async def test_run_inference_worker_shutdown(self):
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
                mock_res_queue.get_nowait.return_value = ("shutdown", "ok")
                mgr._request_queue = mock_req_queue
                mgr._result_queue = mock_res_queue
                mgr._worker_ready = True
                mgr._worker_proc = MagicMock()
                mgr._worker_proc.is_alive.return_value = True

                with pytest.raises(RuntimeError, match="Worker shut down unexpectedly"):
                    await mgr.run_inference("test prompt")
                assert mgr._worker_ready is False

    @pytest.mark.asyncio
    async def test_run_inference_request_queue_full(self):
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
                mock_req_queue.put.side_effect = Exception("queue full")
                mgr._request_queue = mock_req_queue
                mgr._result_queue = MagicMock()
                mgr._worker_ready = True
                mgr._worker_proc = MagicMock()

                with pytest.raises(RuntimeError, match="Failed to send request to worker"):
                    await mgr.run_inference("test prompt")
                assert mgr._worker_ready is False


class TestLocalModelManagerUnloadSetsCancel:
    def test_unload_leaves_cancel_event_set(self):
        mgr = LocalModelManager()
        mgr._cancel_event.clear()
        with patch.object(mgr, "_shutdown_worker"):
            mgr.unload_model()
        assert mgr._cancel_event.is_set()

    def test_unload_no_model_still_sets_cancel(self):
        mgr = LocalModelManager()
        mgr._cancel_event.clear()
        with patch.object(mgr, "_shutdown_worker"):
            mgr.unload_model()
        assert mgr._cancel_event.is_set()


class TestLocalModelManagerLoadModelTimeout:
    """Verify load_model() passes the resolved timeout to _await_worker_ready()."""

    @pytest.mark.asyncio
    async def test_timeout_from_panel_config(self):
        with patch("services.local_model_manager._HAS_LLAMA_CPP", True):
            mgr = LocalModelManager()
            with (
                patch("os.path.exists", return_value=True),
                patch("os.stat", return_value=MagicMock(st_mtime=100, st_size=999)),
                patch.object(LocalModelManager, "_get_load_lock"),
                patch("services.local_model_manager.ThreadPoolManager") as mock_tpm,
                patch.object(mgr, "_ensure_worker", return_value=True),
                patch.object(mgr, "_await_worker_ready", return_value=True) as mock_await,
            ):
                mock_tpm.return_value.run_async = AsyncMock(return_value="md5val")
                result = await mgr.load_model("/path/to/model.gguf", config={"timeout": 60, "n_threads": 4})
                assert result is True
                mock_await.assert_called_once_with(timeout=60.0)

    @pytest.mark.asyncio
    async def test_timeout_from_persisted_config(self):
        with patch("services.local_model_manager._HAS_LLAMA_CPP", True):
            mgr = LocalModelManager()
            with (
                patch("os.path.exists", return_value=True),
                patch("os.stat", return_value=MagicMock(st_mtime=100, st_size=999)),
                patch.object(LocalModelManager, "_get_load_lock"),
                patch("services.local_model_manager.ThreadPoolManager") as mock_tpm,
                patch.object(mgr, "_ensure_worker", return_value=True),
                patch.object(mgr, "_await_worker_ready", return_value=True) as mock_await,
            ):
                mock_tpm.return_value.run_async = AsyncMock(return_value="md5val")
                result = await mgr.load_model(
                    "/path/to/model.gguf",
                    config={"local_model_timeout": 120, "n_threads": 4},
                )
                assert result is True
                mock_await.assert_called_once_with(timeout=120.0)

    @pytest.mark.asyncio
    async def test_timeout_default_when_missing(self):
        with patch("services.local_model_manager._HAS_LLAMA_CPP", True):
            mgr = LocalModelManager()
            with (
                patch("os.path.exists", return_value=True),
                patch("os.stat", return_value=MagicMock(st_mtime=100, st_size=999)),
                patch.object(LocalModelManager, "_get_load_lock"),
                patch("services.local_model_manager.ThreadPoolManager") as mock_tpm,
                patch.object(mgr, "_ensure_worker", return_value=True),
                patch.object(mgr, "_await_worker_ready", return_value=True) as mock_await,
            ):
                mock_tpm.return_value.run_async = AsyncMock(return_value="md5val")
                result = await mgr.load_model("/path/to/model.gguf", config={"n_threads": 4})
                assert result is True
                mock_await.assert_called_once_with(timeout=180.0)


class TestLocalModelManagerLoadModelClearsCancel:
    """Verify load_model() clears _cancel_event before starting worker."""

    @pytest.mark.asyncio
    async def test_cancel_event_cleared_before_worker_start(self):
        with patch("services.local_model_manager._HAS_LLAMA_CPP", True):
            mgr = LocalModelManager()
            mgr._cancel_event.set()
            assert mgr._cancel_event.is_set()

            async def _assert_cancel_cleared_then_return(*args, **kwargs):
                assert not mgr._cancel_event.is_set(), (
                    "cancel_event should be cleared before _await_worker_ready is called"
                )
                return True

            with (
                patch("os.path.exists", return_value=True),
                patch("os.stat", return_value=MagicMock(st_mtime=100, st_size=999)),
                patch.object(LocalModelManager, "_get_load_lock"),
                patch("services.local_model_manager.ThreadPoolManager") as mock_tpm,
                patch.object(mgr, "_ensure_worker", return_value=True),
                patch.object(mgr, "_await_worker_ready", side_effect=_assert_cancel_cleared_then_return),
            ):
                mock_tpm.return_value.run_async = AsyncMock(return_value="md5val")
                result = await mgr.load_model("/path/to/model.gguf", config={"n_threads": 4})
                assert result is True


class TestLocalModelManagerGetLoadLock:
    @pytest.mark.asyncio
    async def test_get_load_lock_returns_lock(self):
        lock = LocalModelManager._get_load_lock()
        assert lock is not None


class TestLocalModelManagerSubprocessCleanup:
    @pytest.mark.asyncio
    async def test_timeout_shuts_down_worker(self):
        from services.local_model_manager import LocalInferenceTimeoutError

        with patch("services.local_model_manager._HAS_LLAMA_CPP", True):
            mgr = LocalModelManager()
            mgr._model_path = "/path/to/model.gguf"
            with (
                patch("services.local_model_manager.ConfigHandler") as mock_ch,
                patch.object(mgr, "_ensure_worker", return_value=True),
                patch.object(mgr, "_await_worker_ready", return_value=True),
                patch.object(mgr, "_shutdown_worker"),
            ):
                mock_ch.get_local_ai_config.return_value = {
                    "local_model_path": "/path/to/model.gguf",
                    "local_model_timeout": 1,
                }

                mock_req_queue = MagicMock()
                mock_res_queue = MagicMock()
                mock_res_queue.get_nowait.side_effect = queue.Empty
                mgr._request_queue = mock_req_queue
                mgr._result_queue = mock_res_queue
                mgr._worker_ready = True
                mgr._worker_proc = MagicMock()
                mgr._worker_proc.is_alive.return_value = True

                with pytest.raises(LocalInferenceTimeoutError):
                    await mgr.run_inference("test prompt")

                mgr._shutdown_worker.assert_called()

    @pytest.mark.asyncio
    async def test_worker_exits_without_result(self):
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


class TestLocalInferenceTimeoutErrorType:
    """E-P1-4: Verify LocalInferenceTimeoutError is a distinct RuntimeError subclass."""

    def test_is_runtime_error_subclass(self):
        from services.local_model_manager import LocalInferenceTimeoutError

        assert issubclass(LocalInferenceTimeoutError, RuntimeError), (
            "E-P1-4: LocalInferenceTimeoutError should be a RuntimeError subclass"
        )

    def test_not_timeout_error_subclass(self):
        from services.local_model_manager import LocalInferenceTimeoutError

        assert not issubclass(LocalInferenceTimeoutError, TimeoutError), (
            "E-P1-4: LocalInferenceTimeoutError should NOT be a TimeoutError subclass "
            "to avoid being caught by generic asyncio.TimeoutError handlers"
        )

    def test_can_be_raised_and_caught(self):
        from services.local_model_manager import LocalInferenceTimeoutError

        with pytest.raises(LocalInferenceTimeoutError):
            raise LocalInferenceTimeoutError("test timeout")

    def test_caught_by_runtime_error(self):
        from services.local_model_manager import LocalInferenceTimeoutError

        with pytest.raises(RuntimeError):
            raise LocalInferenceTimeoutError("test timeout")


class TestSentinelEqualityComparison:
    """BUG-1: _SENTINEL must use == not is for cross-process comparison."""

    def test_sentinel_eq_works_for_equal_string(self):
        from services.local_model_manager import _SENTINEL

        reconstructed = "__SHUTDOWN__"
        assert reconstructed == _SENTINEL

    def test_normal_request_not_equal_to_sentinel(self):
        from services.local_model_manager import _SENTINEL

        normal_request = ("prompt", 100, 0.7, "system")
        assert normal_request != _SENTINEL


class TestCancelEventInterruptsInference:
    """BUG-2: _cancel_event should be checked in run_inference polling loop."""

    @pytest.mark.asyncio
    async def test_cancel_event_raises_runtime_error(self):
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

            with patch(
                "services.local_model_manager.ConfigHandler.get_local_ai_config",
                return_value={"local_model_path": "/fake/model.gguf", "local_model_timeout": 90},
            ):
                with pytest.raises(RuntimeError, match="Inference cancelled"):
                    await mgr.run_inference("test prompt")


class TestLoadModelClearsCancelEvent:
    """load_model should clear _cancel_event on success so a fresh inference is not blocked."""

    @pytest.mark.asyncio
    async def test_cancel_event_cleared_on_load_success(self):
        with patch("services.local_model_manager._HAS_LLAMA_CPP", True):
            mgr = LocalModelManager()
            mgr._cancel_event.set()

            with patch(
                "services.local_model_manager.ConfigHandler.get_local_ai_config",
                return_value={"local_model_path": "/fake/model.gguf", "local_model_timeout": 90},
            ):
                with patch.object(mgr, "_ensure_worker", return_value=True):
                    with patch.object(mgr, "_await_worker_ready", return_value=True):
                        with patch.object(mgr, "calculate_file_md5", return_value="abc123"):
                            with patch("os.path.exists", return_value=True):
                                with patch("os.stat", return_value=MagicMock(st_mtime=1, st_size=2)):
                                    result = await mgr.load_model("/fake/model.gguf")
                                    assert result is True
                                    assert not mgr._cancel_event.is_set()


class TestShutdownWorkerLockedNoDeadlock:
    """Verify _ensure_worker calls _shutdown_worker_locked (not _shutdown_worker) to avoid reentrant deadlock."""

    def test_ensure_worker_calls_locked_version(self):
        mgr = LocalModelManager()
        mgr._worker_proc = None
        mgr._worker_ready = False

        with (
            patch.object(mgr, "_shutdown_worker_locked") as mock_locked,
            patch.object(mgr, "_shutdown_worker") as mock_public,
            patch("services.local_model_manager.multiprocessing.Process") as mock_proc_cls,
            patch("services.local_model_manager.multiprocessing.Queue") as mock_queue_cls,
        ):
            mock_req_queue = MagicMock()
            mock_res_queue = MagicMock()
            mock_queue_cls.side_effect = [mock_req_queue, mock_res_queue]

            mock_proc = MagicMock()
            mock_proc.is_alive.return_value = True
            mock_proc_cls.return_value = mock_proc

            mgr._ensure_worker("/path/to/model.gguf", {})

            mock_locked.assert_called_once()
            mock_public.assert_not_called()

    def test_shutdown_worker_delegates_to_locked(self):
        mgr = LocalModelManager()
        mgr._worker_proc = None
        mgr._request_queue = None
        mgr._result_queue = None

        with patch.object(mgr, "_shutdown_worker_locked") as mock_locked:
            mgr._shutdown_worker()
            mock_locked.assert_called_once()


class TestAwaitWorkerReadyPolling:
    """Comprehensive tests for the polling-based _await_worker_ready implementation."""

    @pytest.mark.asyncio
    async def test_timeout_with_alive_worker(self):
        """Worker is alive but never produces a result → timeout after specified duration."""
        mgr = LocalModelManager()
        mgr._result_queue = MagicMock()
        mgr._result_queue.get_nowait.side_effect = queue.Empty
        mgr._worker_proc = MagicMock()
        mgr._worker_proc.is_alive.return_value = True

        with patch.object(mgr, "_shutdown_worker"):
            result = await mgr._await_worker_ready(timeout=0.5)
            assert result is False
            assert mgr._worker_ready is False
            mgr._shutdown_worker.assert_called()

    @pytest.mark.asyncio
    async def test_worker_crash_with_exitcode_logged(self):
        """Worker crashes → detect is_alive()=False, log exitcode, return False quickly."""
        mgr = LocalModelManager()
        mgr._result_queue = MagicMock()
        mgr._result_queue.get_nowait.side_effect = queue.Empty
        mgr._worker_proc = MagicMock()
        mgr._worker_proc.is_alive.return_value = False
        mgr._worker_proc.exitcode = -6

        with patch.object(mgr, "_shutdown_worker"):
            with patch("services.local_model_manager.logger") as mock_logger:
                result = await mgr._await_worker_ready(timeout=5)
                assert result is False
                # Verify exitcode was logged
                error_calls = [str(c) for c in mock_logger.error.call_args_list]
                assert any("exitcode=-6" in c for c in error_calls)

    @pytest.mark.asyncio
    async def test_error_status_from_worker(self):
        """Worker sends ("error", msg) via queue → return False with _worker_ready=False."""
        mgr = LocalModelManager()
        mgr._result_queue = MagicMock()
        mgr._result_queue.get_nowait.return_value = ("error", "CUDA out of memory")
        mgr._worker_proc = MagicMock()
        mgr._worker_proc.is_alive.return_value = True

        with patch.object(mgr, "_shutdown_worker"):
            result = await mgr._await_worker_ready()
            assert result is False
            assert mgr._worker_ready is False
            mgr._shutdown_worker.assert_called()

    @pytest.mark.asyncio
    async def test_ready_status_sets_worker_ready(self):
        """Worker sends ("ready", path) → return True, _worker_ready=True."""
        mgr = LocalModelManager()
        mgr._result_queue = MagicMock()
        mgr._result_queue.get_nowait.return_value = ("ready", "/path/to/model.gguf")
        mgr._worker_proc = MagicMock()
        mgr._worker_proc.is_alive.return_value = True

        result = await mgr._await_worker_ready()
        assert result is True
        assert mgr._worker_ready is True

    @pytest.mark.asyncio
    async def test_cancel_event_aborts_waiting(self):
        """Cancel event is set → immediately return False without waiting for timeout."""
        mgr = LocalModelManager()
        mgr._result_queue = MagicMock()
        mgr._result_queue.get_nowait.side_effect = queue.Empty
        mgr._worker_proc = MagicMock()
        mgr._worker_proc.is_alive.return_value = True
        mgr._cancel_event.set()

        with patch.object(mgr, "_shutdown_worker"):
            result = await mgr._await_worker_ready(timeout=10)
            assert result is False
            mgr._shutdown_worker.assert_called()

    @pytest.mark.asyncio
    async def test_worker_crash_with_residual_result(self):
        """Worker crashes but left an error message in the queue → should read and return it."""
        mgr = LocalModelManager()
        mgr._result_queue = MagicMock()
        # First get_nowait raises Empty (in polling loop), second returns error (after crash detected)
        mgr._result_queue.get_nowait.side_effect = [queue.Empty, ("error", "import failed")]
        mgr._worker_proc = MagicMock()
        mgr._worker_proc.is_alive.return_value = False

        with patch.object(mgr, "_shutdown_worker"):
            result = await mgr._await_worker_ready()
            assert result is False


class TestShutdownWorkerLockedExceptionPaths:
    """Cover exception handling branches in _shutdown_worker_locked."""

    def test_close_request_queue_raises(self):
        mgr = LocalModelManager()
        mock_req_queue = MagicMock()
        mock_req_queue.close.side_effect = OSError("broken pipe")
        mock_req_queue.join_thread.side_effect = OSError("broken pipe")
        mock_res_queue = MagicMock()
        mock_proc = MagicMock()
        mock_proc.is_alive.return_value = False
        mgr._request_queue = mock_req_queue
        mgr._result_queue = mock_res_queue
        mgr._worker_proc = mock_proc
        mgr._worker_ready = True

        mgr._shutdown_worker()

        assert mgr._worker_proc is None
        assert mgr._worker_ready is False

    def test_close_result_queue_raises(self):
        mgr = LocalModelManager()
        mock_req_queue = MagicMock()
        mock_res_queue = MagicMock()
        mock_res_queue.close.side_effect = OSError("broken pipe")
        mock_res_queue.join_thread.side_effect = OSError("broken pipe")
        mock_proc = MagicMock()
        mock_proc.is_alive.return_value = False
        mgr._request_queue = mock_req_queue
        mgr._result_queue = mock_res_queue
        mgr._worker_proc = mock_proc
        mgr._worker_ready = True

        mgr._shutdown_worker()

        assert mgr._worker_proc is None
        assert mgr._worker_ready is False

    def test_sentinel_put_raises(self):
        mgr = LocalModelManager()
        mock_req_queue = MagicMock()
        mock_req_queue.put.side_effect = Exception("queue closed")
        mock_res_queue = MagicMock()
        mock_proc = MagicMock()
        mock_proc.is_alive.side_effect = [True, False]
        mock_proc.join.return_value = None
        mgr._request_queue = mock_req_queue
        mgr._result_queue = mock_res_queue
        mgr._worker_proc = mock_proc
        mgr._worker_ready = True

        mgr._shutdown_worker()

        assert mgr._worker_proc is None


class TestAwaitWorkerReadyEdgeCases:
    """Cover final drain and result_queue None branches."""

    @pytest.mark.asyncio
    async def test_result_queue_becomes_none_mid_poll(self):
        """result_queue is not None initially but becomes None before get_nowait."""
        mgr = LocalModelManager()
        mgr._result_queue = MagicMock()
        mgr._result_queue.get_nowait.side_effect = queue.Empty
        mgr._worker_proc = MagicMock()
        mgr._worker_proc.is_alive.return_value = True

        # Simulate result_queue becoming None after first poll
        call_count = 0

        def get_nowait_with_none():
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                mgr._result_queue = None
                raise queue.Empty
            raise queue.Empty

        mgr._result_queue.get_nowait = get_nowait_with_none

        with patch.object(mgr, "_shutdown_worker"):
            result = await mgr._await_worker_ready(timeout=0.5)
            assert result is False

    @pytest.mark.asyncio
    async def test_final_drain_succeeds(self):
        """Polling loop finds nothing, but final drain gets a result."""
        mgr = LocalModelManager()
        mgr._result_queue = MagicMock()
        mgr._worker_proc = MagicMock()
        mgr._worker_proc.is_alive.return_value = True

        # First get_nowait in loop raises Empty, second in final drain returns ready
        call_count = 0

        def get_nowait_sequence():
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise queue.Empty
            return ("ready", "/path/to/model.gguf")

        mgr._result_queue.get_nowait = get_nowait_sequence

        result = await mgr._await_worker_ready()
        assert result is True
        assert mgr._worker_ready is True


class TestRunInferenceWorkerNotReady:
    """Cover run_inference when _worker_ready is False."""

    @pytest.mark.asyncio
    async def test_worker_not_ready_calls_await(self):
        with patch("services.local_model_manager._HAS_LLAMA_CPP", True):
            mgr = LocalModelManager()
            mgr._model_path = "/path/to/model.gguf"
            mgr._worker_ready = False

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
                mock_res_queue.get_nowait.return_value = ("ok", "result")
                mgr._request_queue = mock_req_queue
                mgr._result_queue = mock_res_queue
                mgr._worker_proc = MagicMock()
                mgr._worker_proc.is_alive.return_value = True

                result = await mgr.run_inference("test prompt")
                assert result == "result"
                mgr._await_worker_ready.assert_called_once()

    @pytest.mark.asyncio
    async def test_worker_not_ready_await_fails(self):
        with patch("services.local_model_manager._HAS_LLAMA_CPP", True):
            mgr = LocalModelManager()
            mgr._model_path = "/path/to/model.gguf"
            mgr._worker_ready = False

            with (
                patch("services.local_model_manager.ConfigHandler") as mock_ch,
                patch.object(mgr, "_ensure_worker", return_value=True),
                patch.object(mgr, "_await_worker_ready", return_value=False),
            ):
                mock_ch.get_local_ai_config.return_value = {
                    "local_model_path": "/path/to/model.gguf",
                    "local_model_timeout": 30,
                }

                with pytest.raises(RuntimeError, match="failed to become ready"):
                    await mgr.run_inference("test prompt")


class TestLoadModelOSError:
    """Cover load_model when os.stat raises OSError."""

    @pytest.mark.asyncio
    async def test_os_stat_fails(self):
        with patch("services.local_model_manager._HAS_LLAMA_CPP", True):
            mgr = LocalModelManager()
            with (
                patch("os.path.exists", return_value=True),
                patch("os.stat", side_effect=OSError("permission denied")),
                patch.object(LocalModelManager, "_get_load_lock"),
                patch("services.local_model_manager.ThreadPoolManager") as mock_tpm,
                patch.object(mgr, "_ensure_worker", return_value=True),
                patch.object(mgr, "_await_worker_ready", return_value=True),
            ):
                mock_tpm.return_value.run_async = AsyncMock(return_value="abc123")
                result = await mgr.load_model("/path/to/model.gguf", config={"n_threads": 2})
                assert result is True
                assert mgr._model_stat == (0, 0)


class TestResetSingleton:
    """Cover _reset_singleton with an active instance."""

    def test_reset_calls_shutdown_worker(self):
        mock_instance = MagicMock()
        LocalModelManager._instance = mock_instance
        LocalModelManager._reset_singleton()
        mock_instance._shutdown_worker.assert_called_once()
        assert LocalModelManager._instance is None


class TestGetInstance:
    """Cover get_instance creation path."""

    @pytest.mark.asyncio
    async def test_creates_new_instance(self):
        LocalModelManager._instance = None
        LocalModelManager._initialized = False
        instance = await LocalModelManager.get_instance()
        assert instance is not None
        assert LocalModelManager._instance is instance


class TestInitAlreadyInitialized:
    """Cover __init__ when _initialized is True."""

    def test_init_skips_when_initialized(self):
        LocalModelManager._initialized = True
        mgr = LocalModelManager()
        # Should not reset any attributes
        assert mgr._last_config == {}
        LocalModelManager._initialized = False
