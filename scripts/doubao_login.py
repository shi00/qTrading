import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

# 动态获取项目根目录 (假设 script 位于项目下的 scripts/ 目录)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
AUTH_FILE = PROJECT_ROOT / ".doubao_auth_state.json"


async def main():
    print("🚀 启动 Playwright 登录向导...")
    try:
        async with async_playwright() as p:
            # 启动非无头模式，供用户可见操作
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context()
            page = await context.new_page()

            print("🌐 正在导航至豆包官网...")
            await page.goto("https://www.doubao.com/chat/")

            print("\n========================================================")
            print("🔔 1. 请在弹出的浏览器窗口中完成【手机号验证】或【扫码登录】。")
            print(
                "🔔 2. 登录成功后，请在豆包界面随便发一句问候（例如'你好'），确保界面和会话成功加载。",
            )
            print(f"🔔 3. 登录状态将被保存至: {AUTH_FILE}")
            print("========================================================\n")

            # 循环防止误回车
            while True:
                user_input = await asyncio.to_thread(
                    input,
                    ">> 若已完成登录并收到了豆包的回复，请按【Enter】继续 (输入 'q' 取消)... ",
                )
                if user_input.strip().lower() == "q":
                    print("🛑 用户取消操作。")
                    return
                break

            # 确保持久化路径的父目录存在 (虽然当前就在根目录，属于防卫性编程)
            AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)

            # 保存包括 cookies 和 localstorage 在内的存储状态
            await context.storage_state(path=str(AUTH_FILE))
            print("✅ 登录状态已成功持久化保存！")

            await browser.close()
            print("🛑 浏览器已关闭。您现在可以使用已授权的状态运行数据抓取脚本了。")

    except Exception as e:
        print(f"❌ 运行过程中发生未捕获异常: {e}")


if __name__ == "__main__":
    asyncio.run(main())
