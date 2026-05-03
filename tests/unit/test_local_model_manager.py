import pytest
from unittest.mock import patch, MagicMock

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
    yield
    LocalModelManager._instance = None
    LocalModelManager._initialized = False
    LocalModelManager._llm = None
    LocalModelManager._model_path = ""
    LocalModelManager._model_md5 = ""
    LocalModelManager._model_stat = (0, 0)
    LocalModelManager._last_config = {}
    LocalModelManager._is_loading = False


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
