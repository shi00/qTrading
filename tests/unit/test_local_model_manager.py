import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from services.local_model_manager import LocalModelManager, _HAS_LLAMA_CPP


@pytest.fixture(autouse=True)
def reset_singleton():
    LocalModelManager._instance = None
    LocalModelManager._initialized = False
    LocalModelManager._llm = None
    LocalModelManager._model_path = ""
    LocalModelManager._model_md5 = ""
    LocalModelManager._model_stat = (0, 0)
    LocalModelManager._last_config = {}
    LocalModelManager._is_loading = False
    LocalModelManager._cancel_event.clear()
    yield
    LocalModelManager._instance = None
    LocalModelManager._initialized = False
    LocalModelManager._llm = None
    LocalModelManager._model_path = ""
    LocalModelManager._model_md5 = ""
    LocalModelManager._model_stat = (0, 0)
    LocalModelManager._last_config = {}
    LocalModelManager._is_loading = False
    LocalModelManager._cancel_event.clear()


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
        assert mgr._llm is None

    def test_unload_with_model(self):
        mgr = LocalModelManager()
        mgr._llm = MagicMock()
        mgr._model_path = "/path/to/model.gguf"
        mgr.unload_model()
        assert mgr._llm is None


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
            mgr._llm = MagicMock()
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
        LocalModelManager._llm = None

    def teardown_method(self):
        LocalModelManager._instance = None
        LocalModelManager._initialized = False
        LocalModelManager._llm = None

    def test_get_loaded_model_path_empty(self):
        LocalModelManager._model_path = ""
        assert LocalModelManager._model_path == ""

    def test_get_loaded_model_md5_empty(self):
        LocalModelManager._model_md5 = ""
        assert LocalModelManager._model_md5 == ""


class TestLocalModelManagerReset:
    def test_reset_clears_instance(self):
        LocalModelManager._instance = MagicMock()
        LocalModelManager._instance._llm = None
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
            ):
                mock_tpm.return_value.run_async = AsyncMock(side_effect=["abc123", MagicMock()])
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
                assert mgr._llm is None
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
            ):
                mock_ch.get_local_ai_config.return_value = {"n_threads": 4}
                mock_tpm.return_value.run_async = AsyncMock(side_effect=["md5val", MagicMock()])
                result = await mgr.load_model("/path/to/model.gguf")
                assert result is True
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
            mgr._llm = MagicMock()
            mgr._model_path = "/path/to/model.gguf"
            with (
                patch("services.local_model_manager.ConfigHandler") as mock_ch,
                patch("services.local_model_manager.multiprocessing.Process") as mock_proc_cls,
                patch("services.local_model_manager.multiprocessing.Queue") as mock_queue_cls,
            ):
                mock_ch.get_local_ai_config.return_value = {
                    "local_model_path": "/path/to/model.gguf",
                    "local_model_timeout": 30,
                }
                mgr.load_model = AsyncMock(return_value=True)

                mock_queue = MagicMock()
                mock_queue.get_nowait.return_value = ("ok", "result text")
                mock_queue_cls.return_value = mock_queue

                mock_proc = MagicMock()
                mock_proc.is_alive.return_value = False
                mock_proc_cls.return_value = mock_proc

                result = await mgr.run_inference("test prompt")
                assert result == "result text"
        finally:
            mod._HAS_LLAMA_CPP = original

    @pytest.mark.asyncio
    async def test_run_inference_timeout(self):
        import services.local_model_manager as mod

        original = mod._HAS_LLAMA_CPP
        mod._HAS_LLAMA_CPP = True
        try:
            mgr = LocalModelManager()
            mgr._llm = MagicMock()
            mgr._model_path = "/path/to/model.gguf"
            with (
                patch("services.local_model_manager.ConfigHandler") as mock_ch,
                patch("services.local_model_manager.multiprocessing.Process") as mock_proc_cls,
                patch("services.local_model_manager.multiprocessing.Queue") as mock_queue_cls,
            ):
                mock_ch.get_local_ai_config.return_value = {
                    "local_model_path": "/path/to/model.gguf",
                    "local_model_timeout": 1,
                }
                mgr.load_model = AsyncMock(return_value=True)

                mock_queue = MagicMock()
                mock_queue.get_nowait.side_effect = Exception("empty")
                mock_queue_cls.return_value = mock_queue

                mock_proc = MagicMock()
                mock_proc.is_alive.return_value = True
                mock_proc_cls.return_value = mock_proc

                from services.local_model_manager import LocalInferenceTimeoutError

                with pytest.raises(LocalInferenceTimeoutError, match="timed out"):
                    await mgr.run_inference("test prompt")
        finally:
            mod._HAS_LLAMA_CPP = original

    @pytest.mark.asyncio
    async def test_run_inference_error(self):
        import services.local_model_manager as mod

        original = mod._HAS_LLAMA_CPP
        mod._HAS_LLAMA_CPP = True
        try:
            mgr = LocalModelManager()
            mgr._llm = MagicMock()
            mgr._model_path = "/path/to/model.gguf"
            with (
                patch("services.local_model_manager.ConfigHandler") as mock_ch,
                patch("services.local_model_manager.multiprocessing.Process") as mock_proc_cls,
                patch("services.local_model_manager.multiprocessing.Queue") as mock_queue_cls,
            ):
                mock_ch.get_local_ai_config.return_value = {
                    "local_model_path": "/path/to/model.gguf",
                    "local_model_timeout": 30,
                }
                mgr.load_model = AsyncMock(return_value=True)

                mock_queue = MagicMock()
                mock_queue.get_nowait.return_value = ("error", "inference error")
                mock_queue_cls.return_value = mock_queue

                mock_proc = MagicMock()
                mock_proc.is_alive.return_value = False
                mock_proc_cls.return_value = mock_proc

                with pytest.raises(RuntimeError, match="Inference execution failed"):
                    await mgr.run_inference("test prompt")
        finally:
            mod._HAS_LLAMA_CPP = original

    @pytest.mark.asyncio
    async def test_run_inference_load_fails(self):
        import services.local_model_manager as mod

        original = mod._HAS_LLAMA_CPP
        mod._HAS_LLAMA_CPP = True
        try:
            mgr = LocalModelManager()
            with patch("services.local_model_manager.ConfigHandler") as mock_ch:
                mock_ch.get_local_ai_config.return_value = {
                    "local_model_path": "/path/to/model.gguf",
                    "local_model_timeout": 30,
                }
                mgr.load_model = AsyncMock(return_value=False)
                with pytest.raises(RuntimeError, match="failed to load"):
                    await mgr.run_inference("test prompt")
        finally:
            mod._HAS_LLAMA_CPP = original


class TestLocalModelManagerGenerateSync:
    def test_generate_sync_no_model(self):
        mgr = LocalModelManager()
        mgr._llm = None
        with pytest.raises(ValueError, match="Model is None"):
            mgr._generate_sync("prompt", 100, 0.7, "system")

    def test_generate_sync_stream_success(self):
        """Verify streaming generation collects tokens correctly."""
        mgr = LocalModelManager()
        mock_llm = MagicMock()
        # Simulate streaming response: each chunk has a delta with content
        mock_llm.create_chat_completion.return_value = iter(
            [
                {"choices": [{"delta": {"role": "assistant"}}]},
                {"choices": [{"delta": {"content": "hel"}}]},
                {"choices": [{"delta": {"content": "lo"}}]},
                {"choices": [{"delta": {}}]},  # Empty delta (finish chunk)
            ]
        )
        mgr._llm = mock_llm
        mgr._cancel_event.clear()
        result = mgr._generate_sync("prompt", 100, 0.7, "system")
        assert result == "hello"
        mock_llm.create_chat_completion.assert_called_once()
        # Verify stream=True was passed
        call_kwargs = mock_llm.create_chat_completion.call_args
        assert call_kwargs.kwargs.get("stream") is True or call_kwargs[1].get("stream") is True

    def test_generate_sync_cancel_event_stops_generation(self):
        """Verify that setting _cancel_event causes early exit from streaming loop."""
        mgr = LocalModelManager()
        mock_llm = MagicMock()
        # Simulate a long stream; the cancel event should stop it early
        mock_llm.create_chat_completion.return_value = iter(
            [
                {"choices": [{"delta": {"content": "tok1"}}]},
                {"choices": [{"delta": {"content": "tok2"}}]},
                {"choices": [{"delta": {"content": "tok3"}}]},
                {"choices": [{"delta": {"content": "tok4"}}]},
            ]
        )
        mgr._llm = mock_llm

        # Pre-set the cancel event — simulates timeout handler firing
        mgr._cancel_event.set()

        result = mgr._generate_sync("prompt", 100, 0.7, "system")
        # Should return empty or partial since cancel is checked before processing
        assert result == ""

    def test_generate_sync_cancel_mid_stream(self):
        """Verify cancellation mid-stream returns partial output."""
        mgr = LocalModelManager()
        mock_llm = MagicMock()
        cancel_event = mgr._cancel_event
        cancel_event.clear()

        # Custom iterator that sets cancel after 2nd chunk
        def stream_with_cancel():
            yield {"choices": [{"delta": {"content": "first"}}]}
            yield {"choices": [{"delta": {"content": "second"}}]}
            cancel_event.set()  # Simulate timeout firing
            yield {"choices": [{"delta": {"content": "third"}}]}
            yield {"choices": [{"delta": {"content": "fourth"}}]}

        mock_llm.create_chat_completion.return_value = stream_with_cancel()
        mgr._llm = mock_llm

        result = mgr._generate_sync("prompt", 100, 0.7, "system")
        # Should have first two chunks, then break on the third iteration
        assert result == "firstsecond"


class TestLocalModelManagerUnloadSetsCancel:
    def test_unload_sets_cancel_event(self):
        """Verify unload_model sets _cancel_event to signal running inference."""
        mgr = LocalModelManager()
        mgr._llm = MagicMock()
        mgr._cancel_event.clear()
        mgr.unload_model()
        assert mgr._llm is None
        assert mgr._cancel_event.is_set()

    def test_unload_no_model_still_sets_cancel(self):
        """Even without a loaded model, unload should set cancel for safety."""
        mgr = LocalModelManager()
        mgr._cancel_event.clear()
        mgr.unload_model()
        assert mgr._cancel_event.is_set()


class TestLocalModelManagerGetLoadLock:
    @pytest.mark.asyncio
    async def test_get_load_lock_returns_lock(self):
        lock = LocalModelManager._get_load_lock()
        assert lock is not None


class TestLocalModelManagerSubprocessCleanup:
    @pytest.mark.asyncio
    async def test_timeout_terminates_subprocess(self):
        import services.local_model_manager as mod
        from services.local_model_manager import LocalInferenceTimeoutError

        original = mod._HAS_LLAMA_CPP
        mod._HAS_LLAMA_CPP = True
        try:
            mgr = LocalModelManager()
            mgr._llm = MagicMock()
            mgr._model_path = "/path/to/model.gguf"
            with (
                patch("services.local_model_manager.ConfigHandler") as mock_ch,
                patch("services.local_model_manager.multiprocessing.Process") as mock_proc_cls,
                patch("services.local_model_manager.multiprocessing.Queue") as mock_queue_cls,
            ):
                mock_ch.get_local_ai_config.return_value = {
                    "local_model_path": "/path/to/model.gguf",
                    "local_model_timeout": 1,
                }
                mgr.load_model = AsyncMock(return_value=True)

                mock_queue = MagicMock()
                mock_queue.get_nowait.side_effect = Exception("empty")
                mock_queue_cls.return_value = mock_queue

                mock_proc = MagicMock()
                mock_proc.is_alive.return_value = True
                mock_proc_cls.return_value = mock_proc

                with pytest.raises(LocalInferenceTimeoutError):
                    await mgr.run_inference("test prompt")

                mock_proc.terminate.assert_called()
                mock_proc.join.assert_called()
        finally:
            mod._HAS_LLAMA_CPP = original

    @pytest.mark.asyncio
    async def test_timeout_kills_if_terminate_fails(self):
        import services.local_model_manager as mod
        from services.local_model_manager import LocalInferenceTimeoutError

        original = mod._HAS_LLAMA_CPP
        mod._HAS_LLAMA_CPP = True
        try:
            mgr = LocalModelManager()
            mgr._llm = MagicMock()
            mgr._model_path = "/path/to/model.gguf"
            with (
                patch("services.local_model_manager.ConfigHandler") as mock_ch,
                patch("services.local_model_manager.multiprocessing.Process") as mock_proc_cls,
                patch("services.local_model_manager.multiprocessing.Queue") as mock_queue_cls,
            ):
                mock_ch.get_local_ai_config.return_value = {
                    "local_model_path": "/path/to/model.gguf",
                    "local_model_timeout": 1,
                }
                mgr.load_model = AsyncMock(return_value=True)

                mock_queue = MagicMock()
                mock_queue.get_nowait.side_effect = Exception("empty")
                mock_queue_cls.return_value = mock_queue

                mock_proc = MagicMock()
                mock_proc.is_alive.return_value = True
                mock_proc_cls.return_value = mock_proc

                with pytest.raises(LocalInferenceTimeoutError):
                    await mgr.run_inference("test prompt")

                mock_proc.terminate.assert_called()
                mock_proc.kill.assert_called()
        finally:
            mod._HAS_LLAMA_CPP = original

    @pytest.mark.asyncio
    async def test_success_closes_queue(self):
        import services.local_model_manager as mod

        original = mod._HAS_LLAMA_CPP
        mod._HAS_LLAMA_CPP = True
        try:
            mgr = LocalModelManager()
            mgr._llm = MagicMock()
            mgr._model_path = "/path/to/model.gguf"
            with (
                patch("services.local_model_manager.ConfigHandler") as mock_ch,
                patch("services.local_model_manager.multiprocessing.Process") as mock_proc_cls,
                patch("services.local_model_manager.multiprocessing.Queue") as mock_queue_cls,
            ):
                mock_ch.get_local_ai_config.return_value = {
                    "local_model_path": "/path/to/model.gguf",
                    "local_model_timeout": 30,
                }
                mgr.load_model = AsyncMock(return_value=True)

                mock_queue = MagicMock()
                mock_queue.get_nowait.return_value = ("ok", "result text")
                mock_queue_cls.return_value = mock_queue

                mock_proc = MagicMock()
                mock_proc.is_alive.return_value = False
                mock_proc_cls.return_value = mock_proc

                result = await mgr.run_inference("test prompt")
                assert result == "result text"
                mock_queue.close.assert_called()
                mock_queue.join_thread.assert_called()
        finally:
            mod._HAS_LLAMA_CPP = original

    @pytest.mark.asyncio
    async def test_subprocess_exits_without_result(self):
        import services.local_model_manager as mod

        original = mod._HAS_LLAMA_CPP
        mod._HAS_LLAMA_CPP = True
        try:
            mgr = LocalModelManager()
            mgr._llm = MagicMock()
            mgr._model_path = "/path/to/model.gguf"
            with (
                patch("services.local_model_manager.ConfigHandler") as mock_ch,
                patch("services.local_model_manager.multiprocessing.Process") as mock_proc_cls,
                patch("services.local_model_manager.multiprocessing.Queue") as mock_queue_cls,
            ):
                mock_ch.get_local_ai_config.return_value = {
                    "local_model_path": "/path/to/model.gguf",
                    "local_model_timeout": 30,
                }
                mgr.load_model = AsyncMock(return_value=True)

                mock_queue = MagicMock()
                mock_queue.get_nowait.side_effect = Exception("empty")
                mock_queue_cls.return_value = mock_queue

                mock_proc = MagicMock()
                mock_proc.is_alive.return_value = False
                mock_proc_cls.return_value = mock_proc

                with pytest.raises(RuntimeError, match="exited without producing"):
                    await mgr.run_inference("test prompt")
        finally:
            mod._HAS_LLAMA_CPP = original
