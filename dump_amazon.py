import asyncio, os
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from pathlib import Path

load_dotenv()
proxy = {
    "server": f"http://{os.environ.get('PROXY_HOST','p.webshare.io')}:{os.environ.get('PROXY_PORT','80')}",
    "username": f"{os.environ.get('PROXY_USER_BASE','wthluxio-us')}-1",
    "password": os.environ.get("PROXY_PASSWORD",""),
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

        # 첫 번째 상품 카드 HTML만 추출
        items = await page.query_selector_all("div.zg-grid-general-faceout")
        if items:
            html = await items[0].inner_html()
            Path("card_sample.html").write_text(html, encoding="utf-8")
            print(f"카드 수: {len(items)}")
            print("card_sample.html 저장 완료")
        else:
            # 전체 페이지 저장
            Path("card_sample.html").write_text(await page.content(), encoding="utf-8")
            print("카드 없음 — 전체 페이지 저장")

        await browser.close()

asyncio.run(main())
