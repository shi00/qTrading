"""Spike: 验证 ft.run_async + FLET_APP_HIDDEN 能否在当前环境捕获 page。

运行: python -m tests.integration._spike_flet_run_async
"""

import asyncio
import sys

import flet as ft


async def main():
    captured: list[ft.Page] = []
    ready = asyncio.Event()

    async def app_main(page: ft.Page) -> None:
        print(f"[app_main] page captured: {page}", flush=True)
        captured.append(page)
        ready.set()

    print(f"[spike] loop type: {type(asyncio.get_running_loop()).__name__}", flush=True)
    print("[spike] starting ft.run_async...", flush=True)
    task = asyncio.create_task(ft.run_async(app_main, view=ft.AppView.FLET_APP_HIDDEN, port=0))
    try:
        await asyncio.wait_for(ready.wait(), timeout=60.0)
        print(f"[spike] page captured! controls={len(captured[0].controls)}", flush=True)
        captured[0].add(ft.Text("probe"))
        print(f"[spike] after add: controls={len(captured[0].controls)}", flush=True)
    except TimeoutError:
        print("[spike] TIMEOUT: page not captured in 60s", flush=True)
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception) as e:
            print(f"[spike] task cancelled: {type(e).__name__}: {e}", flush=True)
    print("[spike] done", flush=True)


if __name__ == "__main__":
    # 强制用 selector loop（与 pytest-asyncio 配置一致）
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.WindowsSelectorEventLoop) as runner:
            runner.run(main())
    else:
        asyncio.run(main())
