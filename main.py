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
import sys

import flet as ft


def _write_smoke_output_file(content: str) -> None:
    """若设置了 QTRADING_SMOKE_OUTPUT_FILE，将自检输出安全写入该文件（兼容 console=False GUI 子系统）。"""
    smoke_out_file = os.environ.get("QTRADING_SMOKE_OUTPUT_FILE")
    if not smoke_out_file:
        return
    try:
        with open(smoke_out_file, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as exc:
        if sys.stderr is not None:
            sys.stderr.write(f"AStockScreener smoke check WARNING: failed to write {smoke_out_file}: {exc}\n")
            sys.stderr.flush()


def _run_smoke_test() -> int:
    r"""B04: 构建产物与源码自检模式（--smoke-test）。

    校验核心模块可导入且 RESOURCE_ROOT 下静态资源目录与配置文件齐全，输出特征字符串并以退出码 0 结束。
    所有重依赖均在本函数内延迟导入，不影响 ``import main`` 首帧最小导入集（PRF-02）。
    注：Windows 打包产物为 GUI 子系统（console=False），手动命令行自检可通过管道
    ``.\AStockScreener.exe --smoke-test | Out-Host`` 或设置 ``QTRADING_SMOKE_OUTPUT_FILE`` 查看输出。
    """
    import importlib

    import config

    for mod_name in (
        "app.application",
        "flet",
        "flet_charts",
        "flet_code_editor",
        "polars",
    ):
        importlib.import_module(mod_name)

    required_dirs = (
        "locales",
        "assets",
        "alembic",
        os.path.join("data", "tiktoken_cache"),
    )
    missing = [rel for rel in required_dirs if not os.path.isdir(os.path.join(config.RESOURCE_ROOT, rel))]
    if not os.path.isfile(os.path.join(config.RESOURCE_ROOT, "alembic.ini")):
        missing.append("alembic.ini")
    if missing:
        msg = f"AStockScreener smoke check FAILED: missing resources under {config.RESOURCE_ROOT}: {missing}\n"
        if sys.stderr is not None:
            sys.stderr.write(msg)
            sys.stderr.flush()
        _write_smoke_output_file(msg)
        return 1

    ok_msg = "AStockScreener smoke check OK\n"
    if sys.stdout is not None:
        sys.stdout.write(ok_msg)
        sys.stdout.flush()
    _write_smoke_output_file(ok_msg)
    return 0


def main() -> None:
    """应用入口：日志初始化 + 全局异常钩子 + ft.run(app.application.run)。

    重依赖在函数体内延迟导入（PRF-02 首帧最小 import）：``import main`` 时只加载
    上述轻量模块，``app.application`` 等推迟到本函数调用、进入 Flet 运行环境前才加载。
    """
    if "--smoke-test" in sys.argv:
        raise SystemExit(_run_smoke_test())

    import config
    from app.application import run
    from utils.app_env import is_e2e_mode
    from utils.exception_hooks import install_global_exception_hooks
    from utils.logger import setup_logging

    setup_logging()
    install_global_exception_hooks()
    assets = os.path.join(config.RESOURCE_ROOT, "assets")
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
