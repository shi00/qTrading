"""ScreenerViewModel 导出 mixin（C3-7 拆分）。

从 ``screener_view_model.py`` 按职责拆出：结果导出 CSV/Excel/bytes（``get_export_data``、
``export_results``/``export_results_excel``/``export_results_bytes``）、导出按钮判据
``has_export_data``、导出进行中守卫（``try_begin_export``/``end_export``，状态
``state.is_exporting`` 供 View 禁用按钮并防连点重入）。

本 mixin 无独立内部缓冲，仅依赖宿主 VM 的 ``_full_results``；全部分支经
``ThreadPoolManager.run_async(TaskType.CPU, ...)`` offload CPU 密集序列化 (R16)。
"""

from __future__ import annotations

import io
import logging
from collections.abc import Callable

import pandas as pd

from ui.viewmodels.screener_types import ScreenerState
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)


class ExportMixin:
    """导出职责（C3-7）。组合进 ``ScreenerViewModel``。"""

    _full_results: pd.DataFrame | None
    _state: ScreenerState
    _set_state: Callable[..., None]

    @property
    def has_export_data(self) -> bool:
        """UX-04: 全量结果非空判据 (导出按钮禁用用, 与过滤后 total_items 解耦)."""
        return self._full_results is not None and not self._full_results.empty

    def try_begin_export(self) -> bool:
        """进入导出临界区 (P-SCREENER-1: 连点守卫)。

        Returns:
            bool: True 表示成功置位可执行导出; False 表示已有导出进行中, 调用方直接返回。
        成功置位后调用方 MUST 经 ``end_export()`` 复位 (含异常/取消路径)。
        """
        if self._state.is_exporting:
            return False
        self._set_state(is_exporting=True)
        return True

    def end_export(self) -> None:
        """退出导出临界区, 复位 ``is_exporting`` (与 ``try_begin_export`` 配对)."""
        self._set_state(is_exporting=False)

    def get_export_data(self):
        """Get the current results DataFrame for export"""
        if self._full_results is None or self._full_results.empty:
            return None
        return self._full_results

    async def export_results(self, filepath):
        """Export current results to CSV at the specified path"""
        if self._full_results is None or self._full_results.empty:
            return None, "No data to export"

        try:
            await ThreadPoolManager().run_async(
                TaskType.CPU,
                self._full_results.to_csv,
                filepath,
                index=False,
                encoding="utf-8-sig",
            )
            return filepath, None
        except Exception as e:
            logger.error("Export failed: %s", DataSanitizer.sanitize_error(e))
            logger.debug("Export failed traceback", exc_info=True)
            return None, DataSanitizer.sanitize_error(e)

    async def export_results_excel(self, filepath: str) -> tuple[str | None, str | None]:
        """Export current results to Excel (.xlsx) at the specified path.

        与 ``export_results`` 结构对齐: 通过 ``ThreadPoolManager.run_async(TaskType.CPU, ...)``
        offload CPU 密集的 ``df.to_excel`` 调用 (R16). ``asyncio.CancelledError`` 为
        BaseException, 不被 ``except Exception`` 捕获, 自动传播 (R2 与 ``export_results`` 一致).
        """
        if self._full_results is None or self._full_results.empty:
            return None, "No data to export"

        try:
            await ThreadPoolManager().run_async(
                TaskType.CPU,
                self._full_results.to_excel,
                filepath,
                index=False,
                engine="openpyxl",
            )
            return filepath, None
        except Exception as e:
            logger.error("Export Excel failed: %s", DataSanitizer.sanitize_error(e))
            logger.debug("Export Excel failed traceback", exc_info=True)
            return None, DataSanitizer.sanitize_error(e)

    async def export_results_bytes(self, format_: str) -> tuple[bytes | None, str | None]:
        """Export current results to bytes (Web mode: browser download via ``src_bytes``).

        与 ``export_results``/``export_results_excel`` 结构对齐: 通过
        ``ThreadPoolManager.run_async(TaskType.CPU, ...)`` offload CPU 密集的序列化 (R16).
        View (Web 模式) 调用此方法获取 bytes, 传给 ``file_picker.save_file(src_bytes=...)``.

        Args:
            format_: "csv" 或 "xlsx"

        Returns:
            (bytes, None) 成功; (None, error_msg) 失败.
        """
        if self._full_results is None or self._full_results.empty:
            return None, "No data to export"

        try:
            if format_ == "csv":
                csv_str = await ThreadPoolManager().run_async(
                    TaskType.CPU,
                    self._full_results.to_csv,
                    index=False,
                    encoding="utf-8-sig",
                )
                assert csv_str is not None
                return csv_str.encode("utf-8-sig"), None
            else:
                buf = io.BytesIO()
                await ThreadPoolManager().run_async(
                    TaskType.CPU,
                    self._full_results.to_excel,
                    buf,
                    index=False,
                    engine="openpyxl",
                )
                return buf.getvalue(), None
        except Exception as e:
            logger.error("Export bytes failed: %s", DataSanitizer.sanitize_error(e))
            logger.debug("Export bytes failed traceback", exc_info=True)
            return None, DataSanitizer.sanitize_error(e)
