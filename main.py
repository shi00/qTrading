"""AStockScreener 应用入口（review01-A7 收敛）。

main.py 仅保留"日志初始化 + 全局异常钩子 + ft.run(app.application.run)"；
启动编排全部逻辑已迁移至 ``app/application.py::run(page)``（宪法 §4.1：app 层编排所有层）。
"""

import multiprocessing
import os

import flet as ft

from app.application import run
from utils.app_env import is_e2e_mode
from utils.exception_hooks import install_global_exception_hooks
from utils.logger import setup_logging


def main() -> None:
    """应用入口：日志初始化 + 全局异常钩子 + ft.run(app.application.run)。"""
    setup_logging()
    install_global_exception_hooks()
    assets = os.path.join(os.path.dirname(__file__), "assets")
    run_kwargs = {"main": run, "assets_dir": assets}
    if is_e2e_mode():
        # E2E 强制 CanvasKit：Flet 0.86.x 默认 skwasm 在 headless Windows CI 上
        # 渲染管线卡死（字体测量 GPU stall 后无 frame 产出），main 分支一直用
        # CanvasKit 且 E2E 稳定通过。被 3cff3ab1 调试改动误删，现恢复。
        run_kwargs["web_renderer"] = ft.WebRenderer.CANVAS_KIT
    ft.run(**run_kwargs)


if __name__ == "__main__":  # pragma: no cover
    multiprocessing.freeze_support()
    main()
