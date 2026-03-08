import asyncio, random, os
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()

PROXY_HOST = os.environ.get("PROXY_HOST", "p.webshare.io")
PROXY_PORT = os.environ.get("PROXY_PORT", "80")
PROXY_USER_BASE = os.environ.get("PROXY_USER_BASE", "wthluxio-us")
PROXY_PASSWORD = os.environ.get("PROXY_PASSWORD", "")

proxy = {
    "server": f"http://{PROXY_HOST}:{PROXY_PORT}",
    "username": f"{PROXY_USER_BASE}-1",
    "password": PROXY_PASSWORD,
}

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox","--disable-dev-shm-usage","--single-process","--disable-gpu","--no-zygote"],
        )
        ctx = await browser.new_context(proxy=proxy, locale="en-US")
        page = await ctx.new_page()
        await page.goto("https://www.amazon.com", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)
        await page.goto(
            "https://www.amazon.com/Best-Sellers-Home-Decor/zgbs/home-garden/",
            wait_until="domcontentloaded", timeout=30000
        )
        await asyncio.sleep(4)

        items = await page.query_selector_all("div.zg-grid-general-faceout")
        print(f"카드 수: {len(items)}\n")

        for i, item in enumerate(items[:10]):
            rank_change = None
            for sel in [
                "span.zg-badge-text",
                "div._cDEzb_p13n-sc-badge_3mJ9Z span",
                "span.a-badge-text",
                "[class*='zg-badge']",
                "[class*='badge'] span",
                "[class*='rank'] span",
            ]:
                el = await item.query_selector(sel)
                if el:
                    text = (await el.inner_text()).strip()
                    if text:
                        rank_change = text
                        break

            title_el = await item.query_selector("span.a-size-base-plus")
            title = (await title_el.inner_text()).strip()[:30] if title_el else "?"
            print(f"[{i+1:02d}] 순위변동: {rank_change!r:20} | {title}")

        await browser.close()

asyncio.run(main())
