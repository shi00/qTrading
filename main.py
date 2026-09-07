"""AStockScreener 应用入口（review01-A7 收敛 + PRF-02 首帧最小 import）。

main.py 仅保留"日志初始化 + 全局异常钩子 + ft.run(app.application.run)"；
启动编排全部逻辑已迁移至 ``app/application.py::run(page)``（宪法 §4.1：app 层编排所有层）。

顶层只保留绘制/启动第一帧真正必需的轻量 import（``multiprocessing`` / ``os`` / ``flet``）。
重依赖（``app.application`` 及其拉起的 1300+ 模块、日志、异常钩子、E2E 判定）全部在
``main()`` 函数体内延迟导入，使 ``import main`` 不再提前触发整条解析链，
显著压缩窗口显示前的空白期（PRF-02）。
"""

import multiprocessing
import os

import flet as ft


def main() -> None:
    """应用入口：日志初始化 + 全局异常钩子 + ft.run(app.application.run)。

    重依赖在函数体内延迟导入（PRF-02 首帧最小 import）：``import main`` 时只加载
    上述轻量模块，``app.application`` 等推迟到本函数调用、进入 Flet 运行环境前才加载。
    """
    from app.application import run
    from utils.app_env import is_e2e_mode
    from utils.exception_hooks import install_global_exception_hooks
    from utils.logger import setup_logging

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
