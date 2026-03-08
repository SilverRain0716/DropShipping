import asyncio
import random
from playwright.async_api import async_playwright
import os
from dotenv import load_dotenv

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
        await page.goto(
            "https://www.ebay.com/sch/i.html?_nkw=home+decor&LH_Sold=1&LH_Complete=1&_sop=13",
            wait_until="domcontentloaded", timeout=30000
        )
        await asyncio.sleep(3)

        items = await page.query_selector_all("li.s-card")
        print(f"총 카드: {len(items)}개\n")

        for i, item in enumerate(items[:5]):  # 첫 5개만 확인
            footer = await item.query_selector("div.s-card__footer")
            footer_text = (await footer.inner_text()).strip() if footer else "NO FOOTER"

            # 전체 카드 텍스트에서 스폰서 관련 텍스트 찾기
            full_text = await item.inner_text()
            has_sponsored = "Sponsored" in full_text

            print(f"[{i}] footer: '{footer_text[:80]}'")
            print(f"     Sponsored in full_text: {has_sponsored}")
            print()

        await browser.close()

asyncio.run(main())
