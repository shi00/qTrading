import asyncio
import json
import os
import random
import re
import sys
from collections import defaultdict
from typing import List, Tuple

from playwright.async_api import async_playwright, Page

# Dynamically add the project root to sys.path so it can run from anywhere
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_ROOT not in sys.path:
    sys.path.append(APP_ROOT)

from data.cache_manager import CacheManager  # noqa: E402

AUTH_FILE = os.path.join(APP_ROOT, ".doubao_auth_state.json")
MAX_EXCLUDE_RETRIES = 2  # 对于有毒批次的最高容忍次数

# ==========================================
# 豆包专用提示词模板 (Doubao Prompt Template)
# ==========================================

PROMPT_TEMPLATE = """你现在的角色是一名资深的A股量化数据架构师，具备实时联网查询的深度研报阅读能力。我这里有一批缺乏行业和题材标签的A股上市公司的代码和名称。请你作为数据补全模块，为它们进行高精度的概念打标。

**【核心打标规则】**
1. **宁缺毋滥与极致硬核**：根据每家公司的最新主营业务描述、董秘互动、核心产品及上下游客户，提取 2 - 5 个最具爆发力的“细分炒作概念”。如果该股票毫无亮点（如夕阳产业），宁可返回空数组 []，也绝不可为凑数而胡乱生造概念。
2. **屏蔽宽泛与大类词**：彻底封杀诸如“机械制造”、“化工”、“国企改革”、“央企改革”、“长三角一体化”这种毫无量化博弈价值的宽泛词。必须聚焦于具体科技路线、具体知名品牌供应链（如果链、华为链）、具象化新质生产力（如低空经济、算力租赁、脑机接口）。
3. **概念名称高度收敛**：请使用A股各大行情软件最普遍认同的短语。不要使用“华为相关的汽车产业链”，请统一输出为标准的“华为汽车”。同理，统一使用“消费电子”、“固态电池”等标准词缀。

**【输出格式绝对指令】**
- 不要提供任何额外的解释、开场白、思考过程或免责声明。
- 必须将所有的结果包裹在一个标准的 Markdown ```json ... ``` 代码块中。
- 所有 {count} 只股票必须全部输出在这个唯一的 JSON 数组中，绝对不允许拆分、省略或中途截断！

**【期望生成的 JSON 格式模板】**
[
  {{"ts_code": "002896.SZ", "name": "中大力德", "concepts": ["减速器", "机器人", "专精特新"]}},
  {{"ts_code": "603813.SH", "name": "*ST原尚", "concepts": []}}
]

**【以下是等待处理的 {count} 只股票名单】：**
{stock_list}"""


class DoubaoTagger:
    def __init__(self, dry_run: bool = False):
        self.cm = None
        self.dao = None
        self.cancel_event = None
        self.exclude_counter = defaultdict(int)
        self.dry_run = dry_run

    async def initialize(self):
        if self.dao is None:
            self.cm = CacheManager()
            await self.cm.init_db()
            self.dao = self.cm.stock_dao

    async def process_batch(self, page: Page, stocks: List[Tuple[str, str]]) -> bool:
        """核心交互逻辑：发送数据并等待大模型结果"""
        stock_text = "\n".join([f"{r[0]} ({r[1]})" for r in stocks])
        prompt = PROMPT_TEMPLATE.format(count=len(stocks), stock_list=stock_text)

        print(f"⏳ 开始处理新批次，共 {len(stocks)} 只股票...")
        await page.goto("https://www.doubao.com/chat/")

        # 更稳健的 DOM 等待：使用 get_by_xxx 语义化选择器
        try:
            new_chat_btn = page.get_by_text("新对话", exact=True).first
            if await new_chat_btn.is_visible(timeout=3000):
                await new_chat_btn.click()
        except Exception:
            pass  # 可能已经在全新会话中了

        # 等待输入框准备就绪
        textarea = page.locator('textarea[data-testid="chat_input_input"]')
        await textarea.wait_for(state="visible", timeout=10000)
        await textarea.fill(prompt)
        await asyncio.sleep(0.5)

        print("✈️ 发送 Prompt 给大模型...")
        await page.keyboard.press("Enter")

        # 智能等待机制，摒弃单纯的 Sleep
        print("⏳ 等待大模型生成代码结构返回，这可能需要几十秒...")
        response_text = ""
        last_stock_code = stocks[-1][0]

        try:
            # 轮询检查输出结果，替代生硬的 150 次 sleep
            for _ in range(30):
                await asyncio.sleep(2)

                # 获取最后一个气泡内的全文本或代码块
                # 使用 text_content() 获取原汁原味的文本，避免被 CSS 隐藏
                bubbles = await page.locator(
                    '.content-wrapper, div[data-testid="chat-message-bubble"]'
                ).all()
                potential_json = ""

                # 优先寻找正规的代码块
                code_blocks = await page.locator("pre code").all()
                if code_blocks:
                    potential_json = await code_blocks[-1].text_content()
                elif bubbles:
                    all_text = await bubbles[-1].text_content()
                    # 用增强正则硬抓数组 [...]，跳过前面可能的胡言乱语
                    match = re.search(r"\[\s*\{.*?\}\s*\]", all_text, re.DOTALL)
                    potential_json = match.group(0) if match else ""

                # 清洗不可见字符
                if potential_json:
                    potential_json = potential_json.strip("` \n\r\t")
                    if potential_json.startswith("json\n"):
                        potential_json = potential_json[5:]

                if potential_json and "ts_code" in potential_json:
                    try:
                        data = json.loads(potential_json)
                        if isinstance(data, list) and len(data) > 0:
                            # 判定是否包含最后一只股票，或者数量达标 (生成结束的标志)
                            last_found = any(
                                isinstance(d, dict)
                                and d.get("ts_code") == last_stock_code
                                for d in data
                            )
                            if last_found or len(data) == len(stocks):
                                response_text = potential_json
                                break
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            print(f"⚠️ 提取过程超时或异常: {e}", flush=True)

        # 解析并入库
        if response_text:
            try:
                data = json.loads(response_text)
                print(
                    f"✅ 成功解析 JSON，共提取了 {len(data)} 个股票对象。", flush=True
                )

                if self.dry_run:
                    print(
                        f"🏜️ [Dry-Run] 模式已开启，跳过入库过程。提取结果如下：\n{json.dumps(data, ensure_ascii=False, indent=2)}",
                        flush=True,
                    )
                    return True

                count = await self.dao.upsert_ai_concepts(data)
                print(f"🎉 成果入库完成！写入 {count} 条专属概念。", flush=True)
                return True
            except json.JSONDecodeError as e:
                print(f"❌ JSON 解析失败: {e}")
                return False
        else:
            print("❌ 未在返回结果中找到合规的 JSON (可能遭遇 WAF 拦截、截断或超时)。")
            # 出错留痕
            try:
                html_content = await page.content()
                with open(
                    os.path.join(APP_ROOT, "doubao_dom_debug.html"),
                    "w",
                    encoding="utf-8",
                ) as f:
                    f.write(html_content)
                await page.screenshot(
                    path=os.path.join(APP_ROOT, "debug_doubao_fail.png")
                )
            except Exception:
                pass
            return False

    async def run(self, limit: int = 0):
        if not os.path.exists(AUTH_FILE):
            print(
                f"❌ 找不到持久化文件 {AUTH_FILE}！请先运行 doubao_login.py 登录。",
                flush=True,
            )
            return

        await self.initialize()
        print("🚀 启动 Playwright 全自动打标流水线 (Refactored)...", flush=True)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context(storage_state=AUTH_FILE)
            page = await context.new_page()

            batch_count = 0
            exclude_codes = []
            consecutive_failures = 0
            processed_count = 0

            while True:
                if self.cancel_event and self.cancel_event.is_set():
                    print("🛑 任务已被用户取消，退出打标流水线...", flush=True)
                    break

                # 浏览器内存清理 (保留原有优秀机制)
                if batch_count > 0 and batch_count % 15 == 0:
                    print("🧹 清理浏览器缓存与僵尸内存，重建上下文...")
                    await page.close()
                    await context.close()
                    context = await browser.new_context(storage_state=AUTH_FILE)
                    page = await context.new_page()

                batch_count += 1
                if limit > 0:
                    batch_limit = min(limit - processed_count, 50)
                    if batch_limit <= 0:
                        print(
                            f"✅ 已达到指定的测试上限 {limit} 只股票，任务结束。",
                            flush=True,
                        )
                        break
                else:
                    batch_limit = random.randint(30, 50)

                stocks = await self.dao.get_stocks_without_ai_concepts(
                    batch_limit, exclude_codes
                )

                if not stocks and not exclude_codes:
                    print(
                        "✅ 数据库中所有正常股票已完成打标！全自动任务圆满结束。",
                        flush=True,
                    )
                    break
                elif not stocks and exclude_codes:
                    print("⚠️ 正常股票已遍历完毕，开始攻坚错题本排查队列...", flush=True)
                    # 清洗错题本：移除重试超过阈值的死忠坏账
                    exclude_codes = [
                        c
                        for c in exclude_codes
                        if self.exclude_counter[c] < MAX_EXCLUDE_RETRIES
                    ]
                    if not exclude_codes:
                        print("🚫 错题本重试全线溃败或已清空，彻底结束。", flush=True)
                        break
                    stocks = await self.dao.get_stocks_without_ai_concepts(
                        20, []
                    )  # 降低批次大小攻坚
                    # 过滤只查错题本中的股票
                    stocks = [s for s in stocks if s[0] in exclude_codes]
                    if not stocks:
                        break

                print("=" * 50, flush=True)
                success = await self.process_batch(page, stocks)

                if success:
                    consecutive_failures = 0
                    # 如果有修复的错题，要从 exclude 中移除
                    processed_codes = [s[0] for s in stocks]
                    processed_count += len(processed_codes)
                    exclude_codes = [
                        c for c in exclude_codes if c not in processed_codes
                    ]
                else:
                    consecutive_failures += 1
                    for s in stocks:
                        exclude_codes.append(s[0])
                        self.exclude_counter[s[0]] += 1
                    print("⚠️ 本批次已加入隔离排查队列。")

                # WAF 熔断深度休眠退避策略
                if consecutive_failures > 0:
                    cooldown = min(consecutive_failures * 120, 900)
                    print(f"🚨 触发异常防卫机制，休眠 {cooldown} 秒...", flush=True)
                    if self.cancel_event and self.cancel_event.is_set():
                        break
                    await asyncio.sleep(cooldown)
                    delay = random.uniform(5, 15)
                else:
                    delay = random.uniform(30, 60)

                print(f"😴 批次结束，拟人休眠 {delay:.1f} 秒...", flush=True)
                if self.cancel_event and self.cancel_event.is_set():
                    break
                await asyncio.sleep(delay)

            await browser.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Max stocks to process")
    parser.add_argument(
        "--dry-run", action="store_true", help="Do not save to databases"
    )
    args = parser.parse_args()

    tagger = DoubaoTagger(dry_run=args.dry_run)
    asyncio.run(tagger.run(limit=args.limit))
