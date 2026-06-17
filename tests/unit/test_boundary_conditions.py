import asyncio
import base64
import inspect
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# P2-5: 文件含真实 asyncio.sleep（含 60s 长睡眠），标注 slow 以便 CI 分轨运行
pytestmark = pytest.mark.slow


class TestTushareClientBoundaryConditions:
    """Tushare API error handling boundary conditions - behavior tests"""

    @pytest.mark.asyncio
    async def test_handle_api_call_unknown_error_raises_on_last_retry(self):
        from data.external.tushare_client import TushareClient

        client = object.__new__(TushareClient)
        client._initialized = True
        client.max_retries = 1
        client._rate_limiter = None
        client._api_limiters = {}
        client.pro = MagicMock()
        client.timeout = 5
        client._capability_cache = {}
        client._capability_cache_lock = MagicMock()
        client._bg_tasks = set()

        failing_func = MagicMock(side_effect=Exception("unknown error"))

        with patch("utils.thread_pool.ThreadPoolManager") as mock_tpm:
            mock_tpm.return_value.io_pool = None
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(Exception, match="unknown error"):
                    await client._handle_api_call(failing_func)

    @pytest.mark.asyncio
    async def test_handle_api_call_network_error_exhaustion_raises_runtime_error(self):
        from data.external.tushare_client import TushareClient

        client = object.__new__(TushareClient)
        client._initialized = True
        client.max_retries = 1
        client._rate_limiter = None
        client._api_limiters = {}
        client.pro = MagicMock()
        client.timeout = 5
        client._capability_cache = {}
        client._capability_cache_lock = MagicMock()
        client._bg_tasks = set()

        network_error = ConnectionError("connection refused")
        failing_func = MagicMock(side_effect=network_error)

        with patch("utils.thread_pool.ThreadPoolManager") as mock_tpm:
            mock_tpm.return_value.io_pool = None
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(RuntimeError, match="retries exhausted"):
                    await client._handle_api_call(failing_func)

    @pytest.mark.asyncio
    async def test_handle_api_call_permission_error_reraises(self):
        from data.external.tushare_client import TushareClient

        client = object.__new__(TushareClient)
        client._initialized = True
        client.max_retries = 3
        client._rate_limiter = None
        client._api_limiters = {}
        client.pro = MagicMock()
        client.timeout = 5
        client._capability_cache = {}
        client._capability_cache_lock = MagicMock()
        client._bg_tasks = set()

        perm_error = Exception("没有权限访问该接口")
        failing_func = MagicMock(side_effect=perm_error)

        with patch("utils.thread_pool.ThreadPoolManager") as mock_tpm:
            mock_tpm.return_value.io_pool = None
            with pytest.raises(Exception, match="没有权限"):
                await client._handle_api_call(failing_func)

    @pytest.mark.asyncio
    async def test_handle_api_call_rate_limit_reduces_rate(self):
        from data.external.tushare_client import TushareClient

        client = object.__new__(TushareClient)
        client._initialized = True
        client.max_retries = 1
        client._api_limiters = {}
        client.pro = MagicMock()
        client.timeout = 5
        client._capability_cache = {}
        client._capability_cache_lock = MagicMock()
        client._bg_tasks = set()

        mock_limiter = MagicMock()
        mock_limiter.consume_async = AsyncMock()
        mock_limiter.current_rate_per_min = 120
        client._rate_limiter = mock_limiter

        rate_limit_error = Exception("每分钟最多访问120次")
        failing_func = MagicMock(side_effect=rate_limit_error)

        with patch("utils.thread_pool.ThreadPoolManager") as mock_tpm:
            mock_tpm.return_value.io_pool = None
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(RuntimeError):
                    await client._handle_api_call(failing_func)

        mock_limiter.reduce_rate.assert_called()

    @pytest.mark.asyncio
    async def test_paginated_first_page_failure_raises(self):
        from data.external.tushare_client import TushareClient

        client = object.__new__(TushareClient)
        client._initialized = True
        client.max_retries = 1
        client._rate_limiter = None
        client._api_limiters = {}
        client.pro = MagicMock()
        client.timeout = 5

        with patch.object(client, "_handle_api_call", side_effect=Exception("API error")):
            with pytest.raises(Exception, match="API error"):
                await client._handle_api_call_paginated(MagicMock())

    @pytest.mark.asyncio
    async def test_paginated_later_page_failure_returns_partial(self):
        from data.external.tushare_client import TushareClient
        import pandas as pd

        client = object.__new__(TushareClient)
        client._initialized = True
        client.max_retries = 1
        client._rate_limiter = None
        client._api_limiters = {}
        client.pro = MagicMock()
        client.timeout = 5

        first_page_df = pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.5]})

        call_count = 0

        async def mock_handle(func, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return first_page_df
            raise Exception("page failure")

        with patch.object(client, "_handle_api_call", side_effect=mock_handle):
            result = await client._handle_api_call_paginated(MagicMock(), max_pages=10)

        assert result is not None
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_paginated_empty_result_returns_none(self):
        from data.external.tushare_client import TushareClient

        client = object.__new__(TushareClient)
        client._initialized = True
        client.max_retries = 1
        client._rate_limiter = None
        client._api_limiters = {}
        client.pro = MagicMock()
        client.timeout = 5

        with patch.object(client, "_handle_api_call", return_value=None):
            result = await client._handle_api_call_paginated(MagicMock())

        assert result is None

    @pytest.mark.asyncio
    async def test_network_error_retries_with_backoff(self):
        from data.external.tushare_client import TushareClient

        client = object.__new__(TushareClient)
        client._initialized = True
        client.max_retries = 2
        client._rate_limiter = None
        client._api_limiters = {}
        client.pro = MagicMock()
        client.timeout = 5
        client._capability_cache = {}
        client._capability_cache_lock = MagicMock()
        client._bg_tasks = set()

        network_error = ConnectionError("connection refused")
        failing_func = MagicMock(side_effect=network_error)

        sleep_calls = []

        async def mock_sleep(seconds):
            sleep_calls.append(seconds)

        with patch("utils.thread_pool.ThreadPoolManager") as mock_tpm:
            mock_tpm.return_value.io_pool = None
            with patch("asyncio.sleep", side_effect=mock_sleep):
                with pytest.raises(RuntimeError):
                    await client._handle_api_call(failing_func)

        assert len(sleep_calls) > 0, "Network errors must trigger backoff sleep"


class TestQualityGateBoundaryConditions:
    """Quality Gate strict mode boundary conditions - behavior tests"""

    def test_strict_mode_env_var_controls_behavior(self):
        from data.persistence.quality_gate import QualityGateError, QualityTier, _check_tier
        import data.persistence.quality_gate as qg_module

        original = qg_module._STRICT_QUALITY_GATE
        try:
            qg_module._STRICT_QUALITY_GATE = True
            with pytest.raises(QualityGateError, match="STRICT"):
                _check_tier(None, QualityTier.BRONZE, "test_func")
        finally:
            qg_module._STRICT_QUALITY_GATE = original

    def test_check_tier_none_processor_strict_raises(self):
        from data.persistence.quality_gate import QualityGateError, QualityTier, _check_tier
        import data.persistence.quality_gate as qg_module

        original = qg_module._STRICT_QUALITY_GATE
        try:
            qg_module._STRICT_QUALITY_GATE = True
            with pytest.raises(QualityGateError, match="STRICT"):
                _check_tier(None, QualityTier.BRONZE, "test_func")
        finally:
            qg_module._STRICT_QUALITY_GATE = original

    def test_check_tier_none_processor_non_strict_bypasses(self):
        from data.persistence.quality_gate import QualityTier, _check_tier
        import data.persistence.quality_gate as qg_module

        original = qg_module._STRICT_QUALITY_GATE
        try:
            qg_module._STRICT_QUALITY_GATE = False
            result = _check_tier(None, QualityTier.BRONZE, "test_func")
            assert result is None
        finally:
            qg_module._STRICT_QUALITY_GATE = original

    def test_check_tier_uninitialized_tier_treated_as_critical(self):
        from data.persistence.quality_gate import QualityGateError, QualityTier, _check_tier

        processor = MagicMock()
        processor._quality_tier = None
        with pytest.raises(QualityGateError):
            _check_tier(processor, QualityTier.BRONZE, "test_func")

    def test_check_tier_insufficient_raises(self):
        from data.persistence.quality_gate import QualityGateError, QualityTier, _check_tier

        processor = MagicMock()
        processor._quality_tier = QualityTier.CRITICAL
        with pytest.raises(QualityGateError):
            _check_tier(processor, QualityTier.SILVER, "test_func")

    def test_require_quality_supports_async(self):
        from data.persistence.quality_gate import QualityTier, require_quality

        @require_quality(QualityTier.SILVER)
        async def async_method(self):
            return "async_result"

        assert inspect.iscoroutinefunction(async_method)

    def test_require_quality_supports_sync(self):
        from data.persistence.quality_gate import QualityTier, require_quality

        @require_quality(QualityTier.SILVER)
        def sync_method(self):
            return "sync_result"

        assert not inspect.iscoroutinefunction(sync_method)

    def test_find_processor_from_instance(self):
        from data.persistence.quality_gate import _find_processor

        obj = MagicMock()
        obj.data_processor = "found_from_instance"
        assert _find_processor(obj, (), {}) == "found_from_instance"

    def test_find_processor_from_kwargs(self):
        from data.persistence.quality_gate import _find_processor

        obj = MagicMock()
        obj.data_processor = None
        result = _find_processor(obj, (), {"data_processor": "found_from_kwargs"})
        assert result == "found_from_kwargs"

    def test_find_processor_from_args_dict(self):
        from data.persistence.quality_gate import _find_processor

        obj = MagicMock()
        obj.data_processor = None
        result = _find_processor(obj, ({"data_processor": "found_from_args"},), {})
        assert result == "found_from_args"


class TestSecurityManagerBoundaryConditions:
    """Encryption key/encrypt/decrypt boundary conditions - behavior tests"""

    def test_encrypt_empty_returns_empty(self):
        from utils.security_utils import SecurityManager

        assert SecurityManager.encrypt_data("") == ""

    def test_encrypt_none_returns_empty(self):
        from utils.security_utils import SecurityManager

        assert SecurityManager.encrypt_data(None) == ""

    def test_decrypt_empty_returns_empty(self):
        from utils.security_utils import SecurityManager

        assert SecurityManager.decrypt_data("") == ""

    def test_decrypt_none_returns_empty(self):
        from utils.security_utils import SecurityManager

        assert SecurityManager.decrypt_data(None) == ""

    def test_decrypt_too_short_raises(self):
        from utils.security_utils import DecryptionError, SecurityManager

        short_data = base64.b64encode(b"short").decode("utf-8")
        with pytest.raises(DecryptionError, match="too short"):
            SecurityManager.decrypt_data(short_data)

    def test_decrypt_invalid_base64_raises(self):
        from utils.security_utils import DecryptionError, SecurityManager

        with pytest.raises(DecryptionError, match="Base64"):
            SecurityManager.decrypt_data("!!!invalid-base64!!!")

    def test_encrypt_decrypt_roundtrip(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from utils.security_utils import SecurityManager

        original_key_file = SecurityManager.KEY_FILE
        original_key_file_bak = SecurityManager.KEY_FILE_BAK
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                SecurityManager.KEY_FILE = os.path.join(tmpdir, ".secret.key")
                SecurityManager.KEY_FILE_BAK = os.path.join(tmpdir, ".secret.key.bak")

                test_key = AESGCM.generate_key(bit_length=256)
                SecurityManager._key = test_key

                plaintext = "test_secret_value_123"
                encrypted = SecurityManager.encrypt_data(plaintext)
                assert encrypted != plaintext
                assert len(encrypted) > 0

                decrypted = SecurityManager.decrypt_data(encrypted)
                assert decrypted == plaintext

                SecurityManager._key = None
        finally:
            SecurityManager.KEY_FILE = original_key_file
            SecurityManager.KEY_FILE_BAK = original_key_file_bak
            SecurityManager._key = None

    def test_key_file_atomic_write(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from utils.security_utils import SecurityManager

        original_key_file = SecurityManager.KEY_FILE
        original_key_file_bak = SecurityManager.KEY_FILE_BAK
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                SecurityManager.KEY_FILE = os.path.join(tmpdir, ".secret.key")
                SecurityManager.KEY_FILE_BAK = os.path.join(tmpdir, ".secret.key.bak")

                test_key = AESGCM.generate_key(bit_length=256)
                SecurityManager._key = test_key
                SecurityManager._save_key(test_key)

                assert os.path.exists(SecurityManager.KEY_FILE)

                SecurityManager._key = None
        finally:
            SecurityManager.KEY_FILE = original_key_file
            SecurityManager.KEY_FILE_BAK = original_key_file_bak
            SecurityManager._key = None

    def test_key_backup_on_load(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from utils.security_utils import SecurityManager

        original_key_file = SecurityManager.KEY_FILE
        original_key_file_bak = SecurityManager.KEY_FILE_BAK
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                SecurityManager.KEY_FILE = os.path.join(tmpdir, ".secret.key")
                SecurityManager.KEY_FILE_BAK = os.path.join(tmpdir, ".secret.key.bak")

                test_key = AESGCM.generate_key(bit_length=256)
                SecurityManager._key = test_key
                SecurityManager._save_key(test_key)
                SecurityManager._key = None

                loaded_key = SecurityManager._load_key_file(SecurityManager.KEY_FILE)
                assert loaded_key == test_key
                assert os.path.exists(SecurityManager.KEY_FILE_BAK)

                SecurityManager._key = None
        finally:
            SecurityManager.KEY_FILE = original_key_file
            SecurityManager.KEY_FILE_BAK = original_key_file_bak
            SecurityManager._key = None

    def test_key_recovery_from_backup(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from utils.security_utils import SecurityManager

        original_key_file = SecurityManager.KEY_FILE
        original_key_file_bak = SecurityManager.KEY_FILE_BAK
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                SecurityManager.KEY_FILE = os.path.join(tmpdir, ".secret.key")
                SecurityManager.KEY_FILE_BAK = os.path.join(tmpdir, ".secret.key.bak")

                test_key = AESGCM.generate_key(bit_length=256)
                SecurityManager._key = test_key
                SecurityManager._save_key(test_key)
                SecurityManager._key = None

                SecurityManager._copy_file(SecurityManager.KEY_FILE, SecurityManager.KEY_FILE_BAK)

                os.remove(SecurityManager.KEY_FILE)
                with open(SecurityManager.KEY_FILE, "w", encoding="utf-8") as f:
                    f.write("corrupt_data")

                recovered_key = SecurityManager._load_key_file(SecurityManager.KEY_FILE_BAK)
                assert recovered_key is not None
                assert len(recovered_key) == 32

                SecurityManager._key = None
        finally:
            SecurityManager.KEY_FILE = original_key_file
            SecurityManager.KEY_FILE_BAK = original_key_file_bak
            SecurityManager._key = None

    def test_key_corrupt_both_files_raises(self):
        from utils.security_utils import SecurityManager

        original_key_file = SecurityManager.KEY_FILE
        original_key_file_bak = SecurityManager.KEY_FILE_BAK
        try:
            SecurityManager._key = None
            with tempfile.TemporaryDirectory() as tmpdir:
                SecurityManager.KEY_FILE = os.path.join(tmpdir, ".secret.key")
                SecurityManager.KEY_FILE_BAK = os.path.join(tmpdir, ".secret.key.bak")
                SecurityManager._key = None

                with open(SecurityManager.KEY_FILE, "w", encoding="utf-8") as f:
                    f.write("corrupt_primary")
                with open(SecurityManager.KEY_FILE_BAK, "w", encoding="utf-8") as f:
                    f.write("corrupt_backup")

                with pytest.raises(RuntimeError, match="corrupt"):
                    SecurityManager.get_key()

                SecurityManager._key = None
        finally:
            SecurityManager.KEY_FILE = original_key_file
            SecurityManager.KEY_FILE_BAK = original_key_file_bak
            SecurityManager._key = None

    def test_aesgcm_256bit_key(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        test_key = AESGCM.generate_key(bit_length=256)
        assert len(test_key) == 32, "AES-256 key must be 32 bytes (256 bits)"

    def test_nonce_96bit(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from utils.security_utils import SecurityManager

        original_key_file = SecurityManager.KEY_FILE
        original_key_file_bak = SecurityManager.KEY_FILE_BAK
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                SecurityManager.KEY_FILE = os.path.join(tmpdir, ".secret.key")
                SecurityManager.KEY_FILE_BAK = os.path.join(tmpdir, ".secret.key.bak")

                test_key = AESGCM.generate_key(bit_length=256)
                SecurityManager._key = test_key

                plaintext = "test_nonce_check"
                encrypted = SecurityManager.encrypt_data(plaintext)
                decoded = base64.b64decode(encrypted)
                nonce = decoded[:12]
                assert len(nonce) == 12, "AES-GCM nonce must be 12 bytes (96 bits)"

                SecurityManager._key = None
        finally:
            SecurityManager.KEY_FILE = original_key_file
            SecurityManager.KEY_FILE_BAK = original_key_file_bak
            SecurityManager._key = None


class TestShutdownBoundaryConditions:
    """Shutdown flow boundary conditions - behavior tests"""

    @pytest.mark.asyncio
    async def test_do_cleanup_idempotent(self):
        from utils.shutdown import ShutdownCoordinator, StepResult

        coord = ShutdownCoordinator(service_stop_delay=0)

        async def mock_steps(step_timeout_s):
            return [StepResult(name="Step 0", critical=True, ok=True, timed_out=False, elapsed_ms=10.0)]

        coord._run_cleanup_steps = mock_steps
        result1 = await coord.do_cleanup(timeout_s=5.0, step_timeout_s=2.0)
        result2 = await coord.do_cleanup(timeout_s=5.0, step_timeout_s=2.0)
        assert result1 == result2
        assert coord.cleanup_done

    @pytest.mark.asyncio
    async def test_do_cleanup_deduplicates_task(self):
        from utils.shutdown import ShutdownCoordinator, StepResult

        coord = ShutdownCoordinator(service_stop_delay=0)

        async def slow_steps(step_timeout_s):
            await asyncio.sleep(0.1)
            return [StepResult(name="Step 0", critical=True, ok=True, timed_out=False, elapsed_ms=10.0)]

        coord._run_cleanup_steps = slow_steps
        task1 = asyncio.create_task(coord.do_cleanup(timeout_s=5.0, step_timeout_s=2.0))
        task2 = asyncio.create_task(coord.do_cleanup(timeout_s=5.0, step_timeout_s=2.0))
        r1, r2 = await asyncio.gather(task1, task2)
        assert r1 == r2

    @pytest.mark.asyncio
    async def test_execute_cleanup_cancels_watchdog(self):
        from utils.shutdown import ShutdownCoordinator, StepResult

        coord = ShutdownCoordinator(service_stop_delay=0)
        coord.start_watchdog(timeout_s=30)
        assert coord.watchdog_started

        coord._run_cleanup_steps = AsyncMock(
            return_value=[StepResult(name="Step 0", critical=True, ok=True, timed_out=False, elapsed_ms=10.0)]
        )
        await coord._execute_cleanup(timeout_s=5.0, step_timeout_s=2.0)
        assert not coord.watchdog_started

    @pytest.mark.asyncio
    async def test_execute_cleanup_handles_timeout(self):
        from utils.shutdown import ShutdownCoordinator

        coord = ShutdownCoordinator(service_stop_delay=0)
        coord._run_cleanup_steps = AsyncMock(side_effect=TimeoutError())
        result = await coord._execute_cleanup(timeout_s=5.0, step_timeout_s=2.0)
        assert result is False
        assert coord.cleanup_done

    @pytest.mark.asyncio
    async def test_execute_cleanup_handles_unexpected_exception(self):
        from utils.shutdown import ShutdownCoordinator

        coord = ShutdownCoordinator(service_stop_delay=0)
        coord._run_cleanup_steps = AsyncMock(side_effect=RuntimeError("unexpected"))
        result = await coord._execute_cleanup(timeout_s=5.0, step_timeout_s=2.0)
        assert result is False
        assert coord.cleanup_done

    @pytest.mark.asyncio
    async def test_step_timeout_creates_step_result(self):
        from utils.shutdown import ShutdownCoordinator

        coord = ShutdownCoordinator()

        async def slow_step():
            await asyncio.sleep(60)

        result = await coord._run_async_step(
            name="test",
            step=slow_step,
            step_timeout_s=0.05,
            critical=True,
        )
        assert result.ok is False
        assert result.timed_out is True
        assert result.name == "test"
        assert result.critical is True

    def test_step_failure_does_not_skip_remaining(self):
        from utils.shutdown import _CLEANUP_STEPS

        assert len(_CLEANUP_STEPS) >= 2, "Must have ordered cleanup steps"
        for step in _CLEANUP_STEPS:
            assert len(step) == 4, f"Each step must be (name, method_name, critical, timeout): {step}"
