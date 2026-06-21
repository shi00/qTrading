"""
Tests for ConfigHandler thread safety.

验证配置处理器的线程安全性。
"""

import threading

from utils.config_handler import ConfigHandler
import pytest


pytestmark = pytest.mark.unit


class TestConfigThreadSafety:
    """测试配置处理器的线程安全性"""

    def test_config_thread_safety(self):
        """多线程并发读写配置文件"""
        ConfigHandler._config_cache = None
        ConfigHandler.save_config({"test_counter": 0})

        NUM_WRITERS = 20
        NUM_READERS = 40

        errors = []

        def writer_task(idx):
            try:
                ConfigHandler.save_config({f"w_{idx}": idx})
            except Exception as e:
                errors.append(e)

        def reader_task():
            try:
                cfg = ConfigHandler.load_config()
                _ = cfg.get("test_counter")
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(NUM_WRITERS):
            t = threading.Thread(target=writer_task, args=(i,))
            threads.append(t)

        for _i in range(NUM_READERS):
            t = threading.Thread(target=reader_task)
            threads.append(t)

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors occurred during concurrent access: {errors}"

        final_config = ConfigHandler.load_config()
        assert final_config is not None

        success_count = 0
        for i in range(NUM_WRITERS):
            if final_config.get(f"w_{i}") == i:
                success_count += 1

        assert success_count == NUM_WRITERS, (
            f"Writer success rate: {success_count}/{NUM_WRITERS}. Some writes were lost or corrupted."
        )

    def test_config_concurrent_read(self):
        """多线程并发读取配置"""
        ConfigHandler._config_cache = None
        ConfigHandler.save_config({"test_value": "test_data"})

        results = []
        errors = []

        def reader_task():
            try:
                cfg = ConfigHandler.load_config()
                results.append(cfg.get("test_value"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader_task) for _ in range(50)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(results) == 50
        assert all(r == "test_data" for r in results)

    def test_config_write_merge(self):
        """并发写入时合并配置"""
        ConfigHandler._config_cache = None
        ConfigHandler.save_config({})

        def write_task(idx):
            ConfigHandler.save_config({f"key_{idx}": f"value_{idx}"})

        threads = [threading.Thread(target=write_task, args=(i,)) for i in range(10)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final_config = ConfigHandler.load_config()
        assert len(final_config) >= 1
