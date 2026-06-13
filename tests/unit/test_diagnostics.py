import os
import json
import zipfile
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from utils.diagnostics import SystemDiagnosticsCollector
from services.task_manager import TaskStatus, AppTask


@pytest.mark.asyncio
async def test_diagnostics_export(tmp_path):
    # 1. 创建假的日志目录和文件，放在临时文件夹中
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    app_log = log_dir / "app.log"
    error_log = log_dir / "error.log"

    # 写入带有敏感 token 和 数据库密码 的日志
    app_log.write_text("Some normal log\napi_key = test_token_123456\nAnother log", encoding="utf-8")
    error_log.write_text("Error occurred: postgresql://user:mock_secret_password@localhost:5432/db", encoding="utf-8")

    # Mock config.APP_ROOT 让它指向 tmp_path
    with patch("config.APP_ROOT", str(tmp_path)):
        import datetime

        # Mock DataProcessor
        mock_dp = MagicMock()
        mock_dp.check_data_health = AsyncMock(
            return_value={
                "status": "green",
                "msg": "OK",
                "date_field": datetime.date(2026, 6, 12),
                "datetime_field": datetime.datetime(2026, 6, 12, 12, 0, 0),
            }
        )

        # Mock TaskManager
        mock_task = MagicMock(spec=AppTask)
        mock_task.id = "t1"
        mock_task.name = "Test Task"
        mock_task.status = TaskStatus.RUNNING
        mock_task.progress = 0.5
        mock_task.description = "Running test"
        mock_task.error = ""

        mock_tm = MagicMock()
        mock_tm.get_all_tasks.return_value = [mock_task]

        # Mock ThreadPoolManager
        mock_tp = MagicMock()
        mock_io_pool = MagicMock()
        mock_io_pool._max_workers = 10
        mock_io_pool._threads = []
        mock_cpu_pool = MagicMock()
        mock_cpu_pool._max_workers = 4
        mock_cpu_pool._threads = []
        mock_tp.io_pool = mock_io_pool
        mock_tp.cpu_pool = mock_cpu_pool

        async def mock_run_async(task_type, func, *args, **kwargs):
            return func(*args, **kwargs)

        mock_tp.run_async = mock_run_async

        # Mock ConfigHandler
        # 写入含有敏感字段的配置
        mock_config = {
            "ts_token": "test_token_tushare_123456",
            "db_password": "mock_secret_db_password",
            "db_user": "postgres",
            "db_host": "localhost",
            "log_level": "INFO",
        }

        with (
            patch("data.data_processor.DataProcessor", return_value=mock_dp),
            patch("services.task_manager.TaskManager", return_value=mock_tm),
            patch("utils.thread_pool.ThreadPoolManager", return_value=mock_tp),
            patch("utils.config_handler.ConfigHandler.load_config", return_value=mock_config),
        ):
            # 执行导出
            zip_path = await SystemDiagnosticsCollector.export()

            assert os.path.exists(zip_path)
            assert zip_path.endswith(".zip")

            # 验证 zip 包的内容
            with zipfile.ZipFile(zip_path, "r") as zf:
                namelist = zf.namelist()
                assert "diagnostics_summary.json" in namelist
                assert "sanitized_app.log" in namelist
                assert "sanitized_error.log" in namelist

                # 读取并解析 json summary
                summary_data = json.loads(zf.read("diagnostics_summary.json").decode("utf-8"))

                # 检查环境底座信息
                assert "system" in summary_data
                assert "env_variables" in summary_data["system"]

                # 检查线程池和任务信息
                assert "thread_pool" in summary_data
                assert isinstance(summary_data["thread_pool"]["io_max_workers"], int)
                assert summary_data["thread_pool"]["io_max_workers"] > 0
                assert "tasks" in summary_data
                assert summary_data["tasks"]["total_tasks_in_memory"] == 1
                assert summary_data["tasks"]["tasks_list"][0]["id"] == "t1"

                # 检查健康度
                assert "health" in summary_data
                assert summary_data["health"]["status"] == "green"

                # 检查配置脱敏
                assert "config" in summary_data
                # 敏感字段应该被脱敏为 掩码
                assert summary_data["config"]["ts_token"] != "test_token_tushare_123456"
                assert "secret" not in summary_data["config"]["ts_token"]
                assert "secret" not in summary_data["config"]["db_password"]
                assert summary_data["config"]["db_password"] != "mock_secret_db_password"
                # 非敏感字段保留原样
                assert summary_data["config"]["db_user"] == "postgres"

                # 读取脱敏后的日志，确保里面不含有敏感词
                sanitized_app_content = zf.read("sanitized_app.log").decode("utf-8")
                assert "test_token_123456" not in sanitized_app_content
                assert "api_key=***" in sanitized_app_content.replace(" ", "")

                sanitized_error_content = zf.read("sanitized_error.log").decode("utf-8")
                assert "mock_secret_password" not in sanitized_error_content
                assert "postgresql://user:***@localhost:5432/db" in sanitized_error_content

            # 清理产生的 zip 文件
            if os.path.exists(zip_path):
                os.remove(zip_path)
