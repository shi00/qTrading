"""
系统一键诊断/日志导出工具 - System Diagnostics Collector

搜集系统环境、脱敏后的配置信息、线程池/任务调度指标、数据健康状况,
并读取最后 500 行日志全量脱敏后打包导出为 ZIP。
"""

import datetime
import json
import logging
import os
import platform
import sys
import zipfile
import tempfile
import shutil

import config
from utils.config_handler import ConfigHandler
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)


class SystemDiagnosticsCollector:
    """系统级诊断数据收集器"""

    @staticmethod
    async def export() -> str:
        """
        搜集诊断指标、配置以及最新日志，脱敏后打包存储为 ZIP 压缩文件。

        Returns:
            str: 导出的 ZIP 压缩文件绝对路径。
        """
        # 1. 创建临时工作目录
        temp_dir = tempfile.mkdtemp()
        try:
            # 2. 搜集底座运行环境信息
            env_vars = dict(os.environ)
            sanitized_env = DataSanitizer.sanitize_dict(env_vars)

            system_info = {
                "timestamp": datetime.datetime.now().isoformat(),
                "os": platform.system(),
                "os_release": platform.release(),
                "os_version": platform.version(),
                "machine": platform.machine(),
                "processor": platform.processor(),
                "python_version": sys.version,
                "python_implementation": platform.python_implementation(),
                "env_variables": sanitized_env,
            }

            # 3. 搜集线程池和任务管理器指标
            # 线程池指标
            from utils.thread_pool import ThreadPoolManager

            tp_manager = ThreadPoolManager()
            thread_pool_info = {
                "io_max_workers": tp_manager.io_pool._max_workers if tp_manager.io_pool else None,
                "io_current_threads": len(tp_manager.io_pool._threads) if tp_manager.io_pool else 0,
                "cpu_max_workers": tp_manager.cpu_pool._max_workers if tp_manager.cpu_pool else None,
                "cpu_current_threads": len(tp_manager.cpu_pool._threads) if tp_manager.cpu_pool else 0,
            }

            # 任务管理器指标
            from services.task_manager import TaskManager, TaskStatus

            tm_manager = TaskManager()
            all_tasks = tm_manager.get_all_tasks()
            running_tasks = [t for t in all_tasks if getattr(t, "status", None) == TaskStatus.RUNNING]
            queued_tasks = [t for t in all_tasks if getattr(t, "status", None) == TaskStatus.QUEUED]
            completed_tasks = [t for t in all_tasks if getattr(t, "status", None) == TaskStatus.COMPLETED]
            failed_tasks = [t for t in all_tasks if getattr(t, "status", None) == TaskStatus.FAILED]

            task_info = {
                "total_tasks_in_memory": len(all_tasks),
                "running_tasks_count": len(running_tasks),
                "queued_tasks_count": len(queued_tasks),
                "completed_tasks_count": len(completed_tasks),
                "failed_tasks_count": len(failed_tasks),
                "tasks_list": [
                    {
                        "id": getattr(t, "id", None),
                        "name": getattr(t, "name", None),
                        "status": str(getattr(t, "status", None)),
                        "progress": getattr(t, "progress", None),
                        "description": getattr(t, "description", None),
                        "error": DataSanitizer.sanitize_error(str(getattr(t, "error", "")))
                        if getattr(t, "error", "")
                        else None,
                    }
                    for t in all_tasks
                ],
            }

            # 4. 获取配置并脱敏
            raw_config = ConfigHandler.load_config()
            sanitized_config = DataSanitizer.sanitize_dict(raw_config)

            # 5. 调用数据质量检查 (check_data_health)
            health_info = {}
            try:
                from data.data_processor import DataProcessor

                dp = DataProcessor()
                if hasattr(dp, "check_data_health"):
                    health_info = await dp.check_data_health()
            except Exception as e:
                logger.error(f"[Diagnostics] Failed to gather health check data: {e}", exc_info=True)
                health_info = {"status": "red", "error": DataSanitizer.sanitize_error(e)}

            # 整合所有状态到一个 json 中
            diagnostics_summary = {
                "system": system_info,
                "thread_pool": thread_pool_info,
                "tasks": task_info,
                "config": sanitized_config,
                "health": health_info,
            }

            # 6. 计算 ZIP 归档输出路径
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            log_dir = os.path.join(config.APP_ROOT, "logs")
            os.makedirs(log_dir, exist_ok=True)
            zip_filename = f"diagnostics_{timestamp}.zip"
            zip_path = os.path.abspath(os.path.join(log_dir, zip_filename))

            # 定义 JSON 序列化辅助函数
            def json_serial(obj):
                if isinstance(obj, (datetime.datetime, datetime.date)):
                    return obj.isoformat()
                raise TypeError(f"Type {type(obj)} not serializable")

            # 7. 在 IO 线程池中执行物理文件读写与 ZIP 归档，防范主线程 CPU/IO 阻塞 (符合 CLAUDE.md R16)
            from utils.thread_pool import TaskType

            await ThreadPoolManager().run_async(
                TaskType.IO,
                SystemDiagnosticsCollector._write_diagnostics_archive,
                temp_dir,
                diagnostics_summary,
                log_dir,
                zip_path,
                json_serial,
            )

            logger.info(f"[Diagnostics] Diagnostics package successfully exported to: {zip_path}")
            return zip_path

        finally:
            # 清理临时文件夹
            shutil.rmtree(temp_dir, ignore_errors=True)

    @staticmethod
    def _write_diagnostics_archive(temp_dir, diagnostics_summary, log_dir, zip_path, json_serial):
        """在 IO 线程池中执行的物理文件写入与压缩归档"""
        # 1. 写入 json summary
        summary_file_path = os.path.join(temp_dir, "diagnostics_summary.json")
        with open(summary_file_path, "w", encoding="utf-8") as f:
            json.dump(diagnostics_summary, f, indent=4, ensure_ascii=False, default=json_serial)

        # 2. 读取 logs/app.log 和 logs/error.log 的最后 500 行，利用 DataSanitizer.sanitize_error 全量脱敏
        log_files_to_collect = []
        for log_name in ["app.log", "error.log"]:
            log_path = os.path.join(log_dir, log_name)
            if os.path.exists(log_path):
                try:
                    with open(log_path, encoding="utf-8", errors="ignore") as lf:
                        lines = lf.readlines()
                    last_lines = lines[-500:] if len(lines) > 500 else lines

                    # 逐行脱敏
                    sanitized_lines = [DataSanitizer.sanitize_error(line) for line in last_lines]

                    sanitized_log_path = os.path.join(temp_dir, f"sanitized_{log_name}")
                    with open(sanitized_log_path, "w", encoding="utf-8") as out_lf:
                        out_lf.writelines(sanitized_lines)
                    log_files_to_collect.append((sanitized_log_path, f"sanitized_{log_name}"))
                except Exception as e:
                    logger.error(f"[Diagnostics] Failed to read log file {log_name}: {e}")

        # 3. 打包 ZIP
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zip_f:
            # 写入 json summary
            zip_f.write(summary_file_path, "diagnostics_summary.json")
            # 写入脱敏日志
            for src_path, arc_name in log_files_to_collect:
                zip_f.write(src_path, arc_name)
