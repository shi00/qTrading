import os
import json
import zipfile
import contextlib
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np

from utils.diagnostics import SystemDiagnosticsCollector
from services.task_manager import TaskStatus, AppTask

pytestmark = pytest.mark.unit


# --- Helpers for augmented tests ---


def _make_log_files(tmp_path, *, app_log="ok", error_log="ok"):
    """创建 logs/ 目录及两个日志文件，返回 log_dir 路径。

    传入 app_log=None 或 error_log=None 表示不创建对应文件（用于测试缺失日志场景）。
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    if app_log is not None:
        (log_dir / "app.log").write_text(app_log, encoding="utf-8")
    if error_log is not None:
        (log_dir / "error.log").write_text(error_log, encoding="utf-8")
    return log_dir


@contextlib.contextmanager
def _patch_diagnostics_env(
    tmp_path,
    *,
    health_return=None,
    health_exc=None,
    config_dict=None,
):
    """统一 patch diagnostics.export 所依赖的 5 个外部入口。

    调用方需自行通过 _make_log_files 创建 logs/ 目录及日志文件（不同测试需要
    不同的文件状态：正常文件、目录、缺失等）。
    """
    mock_dp = MagicMock()
    if health_exc is not None:
        mock_dp.check_data_health = AsyncMock(side_effect=health_exc)
    else:
        mock_dp.check_data_health = AsyncMock(return_value=health_return or {"status": "green"})

    mock_tm = MagicMock()
    mock_tm.get_all_tasks.return_value = []

    mock_tp = MagicMock()
    mock_io_pool = MagicMock()
    mock_io_pool._max_workers = 10
    mock_io_pool._threads = []
    mock_cpu_pool = MagicMock()
    mock_cpu_pool._max_workers = 4
    mock_cpu_pool._threads = []
    mock_tp.io_pool = mock_io_pool
    mock_tp.cpu_pool = mock_cpu_pool

    async def _run_async(task_type, func, *args, **kwargs):
        return func(*args, **kwargs)

    mock_tp.run_async = _run_async

    with (
        patch("config.APP_ROOT", str(tmp_path)),
        patch("data.data_processor.DataProcessor", return_value=mock_dp),
        patch("services.task_manager.TaskManager", return_value=mock_tm),
        patch("utils.thread_pool.ThreadPoolManager", return_value=mock_tp),
        patch(
            "utils.config_handler.ConfigHandler.load_config",
            return_value=config_dict or {"log_level": "INFO"},
        ),
    ):
        yield mock_dp


@pytest.mark.asyncio
async def test_diagnostics_export(tmp_path):
    # 1. 创建假的日志目录和文件，放在临时文件夹中
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    app_log = log_dir / "app.log"
    error_log = log_dir / "error.log"

    # 写入带有敏感 token 和 数据库密码 的日志
    app_log.write_text("Some normal log\napi_key = test_token_123456\nAnother log", encoding="utf-8")
    error_log.write_text(
        "Error occurred: postgresql://user:mock_secret_password@localhost:5432/db",
        encoding="utf-8",
    )

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
            patch(
                "utils.config_handler.ConfigHandler.load_config",
                return_value=mock_config,
            ),
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


@pytest.mark.asyncio
async def test_diagnostics_export_with_numpy_types(tmp_path):
    """health_info 含 numpy 标量（来自 pandas SQL 聚合查询）时，json_serial 应正确序列化。
    修复历史 bug：json_serial 仅处理 datetime，导致 numpy.int64 触发 TypeError。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text("ok", encoding="utf-8")
    (log_dir / "error.log").write_text("ok", encoding="utf-8")

    with patch("config.APP_ROOT", str(tmp_path)):
        mock_dp = MagicMock()
        # 模拟 stock_dao.count_trade_days() 等返回 numpy 标量的场景
        mock_dp.check_data_health = AsyncMock(
            return_value={
                "status": "green",
                "msg": "OK",
                "trade_days": np.int64(252),
                "concept_count": np.int64(85),
                "expected_rows": np.int64(4500),
                "avg_amount": np.float64(1.23e8),
                "is_healthy": np.bool_(True),
            }
        )

        mock_tm = MagicMock()
        mock_tm.get_all_tasks.return_value = []

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

        with (
            patch("data.data_processor.DataProcessor", return_value=mock_dp),
            patch("services.task_manager.TaskManager", return_value=mock_tm),
            patch("utils.thread_pool.ThreadPoolManager", return_value=mock_tp),
            patch(
                "utils.config_handler.ConfigHandler.load_config",
                return_value={"log_level": "INFO"},
            ),
        ):
            zip_path = await SystemDiagnosticsCollector.export()
            assert os.path.exists(zip_path)

            try:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    summary_data = json.loads(zf.read("diagnostics_summary.json").decode("utf-8"))
                    health = summary_data["health"]
                    # numpy 标量应被正确序列化为 Python 原生类型
                    assert health["trade_days"] == 252
                    assert isinstance(health["trade_days"], int)
                    assert health["concept_count"] == 85
                    assert health["expected_rows"] == 4500
                    assert health["avg_amount"] == 1.23e8
                    assert isinstance(health["avg_amount"], float)
                    assert health["is_healthy"] is True
            finally:
                if os.path.exists(zip_path):
                    os.remove(zip_path)


# --- 以下测试覆盖 diagnostics.py 未达 80% 阈值的缺失分支 ---


@pytest.mark.asyncio
async def test_diagnostics_export_health_check_exception(tmp_path):
    """覆盖 diagnostics.py:110-112：DataProcessor.check_data_health 抛异常时，
    记录 error 日志并降级为 {"status": "red", "error": ...}，export 仍成功完成。

    场景：数据库连接断开等导致健康检查失败，诊断导出不应中断，而是把失败原因
    脱敏后写入 health 字段，便于运维定位。
    """
    _make_log_files(tmp_path)
    with _patch_diagnostics_env(tmp_path, health_exc=RuntimeError("db connection lost")):
        zip_path = await SystemDiagnosticsCollector.export()
        try:
            assert os.path.exists(zip_path)
            with zipfile.ZipFile(zip_path, "r") as zf:
                summary = json.loads(zf.read("diagnostics_summary.json").decode("utf-8"))
                # 降级路径：status=red，error 字段含脱敏后的异常信息
                assert summary["health"]["status"] == "red"
                assert "error" in summary["health"]
                # 异常信息经 DataSanitizer.sanitize_error 处理后仍可读
                assert summary["health"]["error"] is not None
        finally:
            if os.path.exists(zip_path):
                os.remove(zip_path)


@pytest.mark.asyncio
async def test_diagnostics_export_json_serial_numpy_ndarray(tmp_path):
    """覆盖 diagnostics.py:143-144, 146-147：health_info 含 numpy 多维数组时，
    obj.item() 抛 ValueError（非 0 维数组），回退到 obj.tolist() 序列化为嵌套 list。

    场景：pandas DataFrame.values 或 SQL 聚合返回 ndarray，标准 json 无法序列化。
    """
    _make_log_files(tmp_path)
    with _patch_diagnostics_env(
        tmp_path,
        health_return={
            "status": "green",
            "matrix": np.array([[1, 2], [3, 4]]),
        },
    ):
        zip_path = await SystemDiagnosticsCollector.export()
        try:
            assert os.path.exists(zip_path)
            with zipfile.ZipFile(zip_path, "r") as zf:
                summary = json.loads(zf.read("diagnostics_summary.json").decode("utf-8"))
                # ndarray 经 tolist() 转为嵌套 list
                assert summary["health"]["matrix"] == [[1, 2], [3, 4]]
                assert isinstance(summary["health"]["matrix"], list)
        finally:
            if os.path.exists(zip_path):
                os.remove(zip_path)


@pytest.mark.asyncio
async def test_diagnostics_export_json_serial_decimal(tmp_path):
    """覆盖 diagnostics.py:152-153：health_info 含 Decimal（SQLAlchemy Numeric 返回值）时，
    通过 is_finite + as_tuple 鸭子类型识别并转为 float。

    场景：StockDao 聚合查询 SUM/AVG 返回 Decimal，标准 json 无法序列化。
    """
    _make_log_files(tmp_path)
    with _patch_diagnostics_env(
        tmp_path,
        health_return={
            "status": "green",
            "avg_price": Decimal("3.14"),
        },
    ):
        zip_path = await SystemDiagnosticsCollector.export()
        try:
            assert os.path.exists(zip_path)
            with zipfile.ZipFile(zip_path, "r") as zf:
                summary = json.loads(zf.read("diagnostics_summary.json").decode("utf-8"))
                # Decimal 经 float() 转为浮点数
                assert summary["health"]["avg_price"] == 3.14
                assert isinstance(summary["health"]["avg_price"], float)
        finally:
            if os.path.exists(zip_path):
                os.remove(zip_path)


@pytest.mark.asyncio
async def test_diagnostics_export_json_serial_isoformat_object(tmp_path):
    """覆盖 diagnostics.py:149-150：health_info 含仅有 isoformat 方法的对象时
    （非 datetime/date 子类），走 isoformat 分支。

    场景：第三方库返回的自定义时间对象（如 pandas.Period）具有 isoformat 方法
    但不继承 datetime，需通过鸭子类型识别。
    """
    _make_log_files(tmp_path)
    # 构造一个有 isoformat 但不是 datetime/date 子类的对象
    # spec=["isoformat"] 确保 hasattr(obj, "item")/hasattr(obj, "tolist") 返回 False
    custom_obj = MagicMock(spec=["isoformat"])
    custom_obj.isoformat.return_value = "2024-01-01T00:00:00"

    with _patch_diagnostics_env(
        tmp_path,
        health_return={
            "status": "green",
            "custom_ts": custom_obj,
        },
    ):
        zip_path = await SystemDiagnosticsCollector.export()
        try:
            assert os.path.exists(zip_path)
            with zipfile.ZipFile(zip_path, "r") as zf:
                summary = json.loads(zf.read("diagnostics_summary.json").decode("utf-8"))
                assert summary["health"]["custom_ts"] == "2024-01-01T00:00:00"
        finally:
            if os.path.exists(zip_path):
                os.remove(zip_path)


@pytest.mark.asyncio
async def test_diagnostics_export_json_serial_unsupported_type_raises(tmp_path):
    """覆盖 diagnostics.py:154：json_serial 遇到无法识别的类型时 raise TypeError，
    导致 json.dump 失败，export() 传播异常（fail-fast，不静默丢数据）。

    场景：health_info 意外包含 set 等不可序列化类型，应明确报错而非产出残缺 ZIP。
    """
    _make_log_files(tmp_path)
    # set 类型没有 item/tolist/isoformat/is_finite/as_tuple 任何方法
    with _patch_diagnostics_env(
        tmp_path,
        health_return={
            "status": "green",
            "unsupported": {1, 2, 3},
        },
    ):
        with pytest.raises(TypeError, match="not serializable"):
            await SystemDiagnosticsCollector.export()


@pytest.mark.asyncio
async def test_diagnostics_export_log_read_exception(tmp_path):
    """覆盖 diagnostics.py:201-202：日志文件读取异常时，记录 error 但不影响其他文件打包。

    场景：app.log 被占用或变为目录（权限/损坏），open() 抛异常被 except 捕获，
    error.log 仍正常读取，最终 ZIP 包含 summary 和 error.log 但不含 app.log。
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # app.log 创建为目录 → open() 抛 IsADirectoryError(Linux)/PermissionError(Windows)
    (log_dir / "app.log").mkdir()
    # error.log 正常文件
    (log_dir / "error.log").write_text("error log content", encoding="utf-8")

    with _patch_diagnostics_env(tmp_path):
        zip_path = await SystemDiagnosticsCollector.export()
        try:
            assert os.path.exists(zip_path)
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = zf.namelist()
                # summary 始终存在
                assert "diagnostics_summary.json" in names
                # error.log 正常读取，应被打包
                assert "sanitized_error.log" in names
                # app.log 读取失败，不应出现在 ZIP 中
                assert "sanitized_app.log" not in names
        finally:
            if os.path.exists(zip_path):
                os.remove(zip_path)
