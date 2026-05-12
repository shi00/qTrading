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
        import services.local_model_manager as mod

        original = mod._HAS_LLAMA_CPP
        mod._HAS_LLAMA_CPP = False
        try:
            mgr = LocalModelManager()
            result = await mgr.load_model("/path/to/model.gguf")
            assert result is False
        finally:
            mod._HAS_LLAMA_CPP = original


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
            patch.object(mgr, "_shutdown_worker"),
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
            mgr._shutdown_worker.assert_called_once()

    def test_ensure_worker_returns_false_on_load_failure(self):
        mgr = LocalModelManager()
        mgr._worker_proc = None
        mgr._worker_ready = False

        with (
            patch.object(mgr, "_shutdown_worker"),
            patch("services.local_model_manager.multiprocessing.Process") as mock_proc_cls,
            patch("services.local_model_manager.multiprocessing.Queue") as mock_queue_cls,
        ):
            mock_res_queue = MagicMock()
            mock_res_queue.get.return_value = ("error", "Model load failed: no file")
            mock_req_queue = MagicMock()
            mock_queue_cls.side_effect = [mock_req_queue, mock_res_queue]

            mock_proc = MagicMock()
            mock_proc.is_alive.return_value = True
            mock_proc_cls.return_value = mock_proc

            result = mgr._ensure_worker("/bad/model.gguf", {})
            assert result is False
            mgr._shutdown_worker.assert_called()

    def test_ensure_worker_returns_false_on_timeout(self):
        mgr = LocalModelManager()
        mgr._worker_proc = None
        mgr._worker_ready = False

        with (
            patch.object(mgr, "_shutdown_worker"),
            patch("services.local_model_manager.multiprocessing.Process") as mock_proc_cls,
            patch("services.local_model_manager.multiprocessing.Queue") as mock_queue_cls,
        ):
            mock_res_queue = MagicMock()
            mock_res_queue.get.side_effect = queue.Empty()
            mock_req_queue = MagicMock()
            mock_queue_cls.side_effect = [mock_req_queue, mock_res_queue]

            mock_proc = MagicMock()
            mock_proc.is_alive.return_value = True
            mock_proc_cls.return_value = mock_proc

            result = mgr._ensure_worker("/path/to/model.gguf", {})
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
        import services.local_model_manager as mod

        original = mod._HAS_LLAMA_CPP
        mod._HAS_LLAMA_CPP = True
        try:
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
        finally:
            mod._HAS_LLAMA_CPP = original

    @pytest.mark.asyncio
    async def test_file_not_found(self):
        import services.local_model_manager as mod

        original = mod._HAS_LLAMA_CPP
        mod._HAS_LLAMA_CPP = True
        try:
            mgr = LocalModelManager()
            result = await mgr.load_model("/nonexistent/model.gguf")
            assert result is False
        finally:
            mod._HAS_LLAMA_CPP = original

    @pytest.mark.asyncio
    async def test_same_model_skip_reload(self):
        import services.local_model_manager as mod

        original = mod._HAS_LLAMA_CPP
        mod._HAS_LLAMA_CPP = True
        try:
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
        finally:
            mod._HAS_LLAMA_CPP = original


class TestLocalModelManagerRunInference:
    @pytest.mark.asyncio
    async def test_no_llama_cpp(self):
        import services.local_model_manager as mod

        original = mod._HAS_LLAMA_CPP
        mod._HAS_LLAMA_CPP = False
        try:
            mgr = LocalModelManager()
            with pytest.raises(ImportError):
                await mgr.run_inference("test prompt")
        finally:
            mod._HAS_LLAMA_CPP = original

    @pytest.mark.asyncio
    async def test_no_model_configured(self):
        import services.local_model_manager as mod

        original = mod._HAS_LLAMA_CPP
        mod._HAS_LLAMA_CPP = True
        try:
            mgr = LocalModelManager()
            with patch("utils.config_handler.ConfigHandler.get_local_ai_config", return_value={"local_model_path": ""}):
                with pytest.raises(RuntimeError, match="not configured"):
                    await mgr.run_inference("test prompt")
        finally:
            mod._HAS_LLAMA_CPP = original


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
        import services.local_model_manager as mod

        original = mod._HAS_LLAMA_CPP
        mod._HAS_LLAMA_CPP = True
        try:
            mgr = LocalModelManager()
            with (
                patch("os.path.exists", return_value=True),
                patch("os.stat", return_value=MagicMock(st_mtime=100, st_size=999)),
                patch.object(LocalModelManager, "_get_load_lock"),
                patch("services.local_model_manager.ThreadPoolManager") as mock_tpm,
                patch.object(mgr, "_ensure_worker", return_value=True),
            ):
                mock_tpm.return_value.run_async = AsyncMock(return_value="abc123")
                result = await mgr.load_model("/path/to/model.gguf", config={"n_threads": 2})
                assert result is True
                assert mgr._model_path == "/path/to/model.gguf"
                assert mgr._model_md5 == "abc123"
        finally:
            mod._HAS_LLAMA_CPP = original

    @pytest.mark.asyncio
    async def test_load_model_failure(self):
        import services.local_model_manager as mod

        original = mod._HAS_LLAMA_CPP
        mod._HAS_LLAMA_CPP = True
        try:
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
        finally:
            mod._HAS_LLAMA_CPP = original

    @pytest.mark.asyncio
    async def test_load_model_no_config(self):
        import services.local_model_manager as mod

        original = mod._HAS_LLAMA_CPP
        mod._HAS_LLAMA_CPP = True
        try:
            mgr = LocalModelManager()
            with (
                patch("os.path.exists", return_value=True),
                patch("os.stat", return_value=MagicMock(st_mtime=100, st_size=999)),
                patch.object(LocalModelManager, "_get_load_lock"),
                patch("services.local_model_manager.ThreadPoolManager") as mock_tpm,
                patch("services.local_model_manager.ConfigHandler") as mock_ch,
                patch.object(mgr, "_ensure_worker", return_value=True),
            ):
                mock_ch.get_local_ai_config.return_value = {"n_threads": 4}
                mock_tpm.return_value.run_async = AsyncMock(return_value="md5val")
                result = await mgr.load_model("/path/to/model.gguf")
                assert result is True
        finally:
            mod._HAS_LLAMA_CPP = original

    @pytest.mark.asyncio
    async def test_load_model_ensure_worker_fails(self):
        import services.local_model_manager as mod

        original = mod._HAS_LLAMA_CPP
        mod._HAS_LLAMA_CPP = True
        try:
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
        finally:
            mod._HAS_LLAMA_CPP = original


class TestLocalModelManagerRunInferenceWithModel:
    @pytest.mark.asyncio
    async def test_run_inference_success(self):
        import services.local_model_manager as mod

        original = mod._HAS_LLAMA_CPP
        mod._HAS_LLAMA_CPP = True
        try:
            mgr = LocalModelManager()
            mgr._model_path = "/path/to/model.gguf"
            with (
                patch("services.local_model_manager.ConfigHandler") as mock_ch,
                patch.object(mgr, "_ensure_worker", return_value=True),
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
                mgr._worker_proc = MagicMock()
                mgr._worker_proc.is_alive.return_value = True

                result = await mgr.run_inference("test prompt")
                assert result == "result text"
                mock_req_queue.put.assert_called_once_with(
                    ("test prompt", 150, 0.7, "You are a helpful assistant."),
                    timeout=5,
                )
        finally:
            mod._HAS_LLAMA_CPP = original

    @pytest.mark.asyncio
    async def test_run_inference_timeout(self):
        import services.local_model_manager as mod
        from services.local_model_manager import LocalInferenceTimeoutError

        original = mod._HAS_LLAMA_CPP
        mod._HAS_LLAMA_CPP = True
        try:
            mgr = LocalModelManager()
            mgr._model_path = "/path/to/model.gguf"
            with (
                patch("services.local_model_manager.ConfigHandler") as mock_ch,
                patch.object(mgr, "_ensure_worker", return_value=True),
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
                mgr._worker_proc = MagicMock()
                mgr._worker_proc.is_alive.return_value = True

                with pytest.raises(LocalInferenceTimeoutError):
                    await mgr.run_inference("test prompt")

                mgr._shutdown_worker.assert_called()
        finally:
            mod._HAS_LLAMA_CPP = original

    @pytest.mark.asyncio
    async def test_run_inference_error_response(self):
        import services.local_model_manager as mod

        original = mod._HAS_LLAMA_CPP
        mod._HAS_LLAMA_CPP = True
        try:
            mgr = LocalModelManager()
            mgr._model_path = "/path/to/model.gguf"
            with (
                patch("services.local_model_manager.ConfigHandler") as mock_ch,
                patch.object(mgr, "_ensure_worker", return_value=True),
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
                mgr._worker_proc = MagicMock()
                mgr._worker_proc.is_alive.return_value = True

                with pytest.raises(RuntimeError, match="Inference execution failed"):
                    await mgr.run_inference("test prompt")
        finally:
            mod._HAS_LLAMA_CPP = original

    @pytest.mark.asyncio
    async def test_run_inference_worker_shutdown(self):
        import services.local_model_manager as mod

        original = mod._HAS_LLAMA_CPP
        mod._HAS_LLAMA_CPP = True
        try:
            mgr = LocalModelManager()
            mgr._model_path = "/path/to/model.gguf"
            with (
                patch("services.local_model_manager.ConfigHandler") as mock_ch,
                patch.object(mgr, "_ensure_worker", return_value=True),
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
                mgr._worker_proc = MagicMock()
                mgr._worker_proc.is_alive.return_value = True

                with pytest.raises(RuntimeError, match="Worker shut down unexpectedly"):
                    await mgr.run_inference("test prompt")
                assert mgr._worker_ready is False
        finally:
            mod._HAS_LLAMA_CPP = original

    @pytest.mark.asyncio
    async def test_run_inference_request_queue_full(self):
        import services.local_model_manager as mod

        original = mod._HAS_LLAMA_CPP
        mod._HAS_LLAMA_CPP = True
        try:
            mgr = LocalModelManager()
            mgr._model_path = "/path/to/model.gguf"
            with (
                patch("services.local_model_manager.ConfigHandler") as mock_ch,
                patch.object(mgr, "_ensure_worker", return_value=True),
            ):
                mock_ch.get_local_ai_config.return_value = {
                    "local_model_path": "/path/to/model.gguf",
                    "local_model_timeout": 30,
                }

                mock_req_queue = MagicMock()
                mock_req_queue.put.side_effect = Exception("queue full")
                mgr._request_queue = mock_req_queue
                mgr._result_queue = MagicMock()
                mgr._worker_proc = MagicMock()

                with pytest.raises(RuntimeError, match="Failed to send request to worker"):
                    await mgr.run_inference("test prompt")
                assert mgr._worker_ready is False
        finally:
            mod._HAS_LLAMA_CPP = original


class TestLocalModelManagerUnloadSetsCancel:
    def test_unload_clears_cancel_event_after_shutdown(self):
        mgr = LocalModelManager()
        mgr._cancel_event.set()
        with patch.object(mgr, "_shutdown_worker"):
            mgr.unload_model()
        assert not mgr._cancel_event.is_set()

    def test_unload_no_model_still_clears_cancel(self):
        mgr = LocalModelManager()
        mgr._cancel_event.set()
        with patch.object(mgr, "_shutdown_worker"):
            mgr.unload_model()
        assert not mgr._cancel_event.is_set()


class TestLocalModelManagerGetLoadLock:
    @pytest.mark.asyncio
    async def test_get_load_lock_returns_lock(self):
        lock = LocalModelManager._get_load_lock()
        assert lock is not None


class TestLocalModelManagerSubprocessCleanup:
    @pytest.mark.asyncio
    async def test_timeout_shuts_down_worker(self):
        import services.local_model_manager as mod
        from services.local_model_manager import LocalInferenceTimeoutError

        original = mod._HAS_LLAMA_CPP
        mod._HAS_LLAMA_CPP = True
        try:
            mgr = LocalModelManager()
            mgr._model_path = "/path/to/model.gguf"
            with (
                patch("services.local_model_manager.ConfigHandler") as mock_ch,
                patch.object(mgr, "_ensure_worker", return_value=True),
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
                mgr._worker_proc = MagicMock()
                mgr._worker_proc.is_alive.return_value = True

                with pytest.raises(LocalInferenceTimeoutError):
                    await mgr.run_inference("test prompt")

                mgr._shutdown_worker.assert_called()
        finally:
            mod._HAS_LLAMA_CPP = original

    @pytest.mark.asyncio
    async def test_worker_exits_without_result(self):
        import services.local_model_manager as mod

        original = mod._HAS_LLAMA_CPP
        mod._HAS_LLAMA_CPP = True
        try:
            mgr = LocalModelManager()
            mgr._model_path = "/path/to/model.gguf"
            with (
                patch("services.local_model_manager.ConfigHandler") as mock_ch,
                patch.object(mgr, "_ensure_worker", return_value=True),
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
                mgr._worker_proc = MagicMock()
                mgr._worker_proc.is_alive.return_value = False

                with pytest.raises(RuntimeError, match="exited without producing"):
                    await mgr.run_inference("test prompt")
                assert mgr._worker_ready is False
        finally:
            mod._HAS_LLAMA_CPP = original


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
        import services.local_model_manager as mod

        mod._HAS_LLAMA_CPP = True
        try:
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
        finally:
            mod._HAS_LLAMA_CPP = False
