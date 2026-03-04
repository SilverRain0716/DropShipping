"""
이베이 셀렉터 진단 스크립트
- 실제 브라우저 창(headless=False)으로 실행
- 어떤 셀렉터가 작동하는지 확인
- 실행: python debug_ebay.py
"""
from playwright.sync_api import sync_playwright
import time

def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,  # ← 실제 창으로 열림 (디버깅용)
            slow_mo=500,
        )
        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="America/New_York",
        )
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
        """)

        page = context.new_page()

        print("🌐 이베이 메인 접속...")
        page.goto("https://www.ebay.com", wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)

        url = (
            "https://www.ebay.com/sch/i.html"
            "?_nkw=home+decor&_sacat=10033"
            "&LH_Sold=1&LH_Complete=1&_sop=13&_ipg=48&_pgn=1"
        )
        print("🔍 검색 페이지 이동...")
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(5)  # JS 렌더링 충분히 대기

        print(f"📄 타이틀: {page.title()}")
        print(f"📏 HTML 길이: {len(page.content())}")

        # 다양한 셀렉터 시도
        selectors = [
            ".s-item",
            "li.s-item",
            ".srp-results .s-item",
            "ul.srp-results > li",
            "[class*='s-item__title']",
            ".s-item__title",
        ]
        print("\n=== 셀렉터 탐색 ===")
        for sel in selectors:
            els = page.query_selector_all(sel)
            print(f"  {sel}: {len(els)}개")

        # 작동하는 셀렉터 찾으면 첫 항목 출력
        items = page.query_selector_all(".s-item")
        if not items:
            items = page.query_selector_all("li.s-item")

        if items:
            print(f"\n✅ 항목 {len(items)}개 발견!")
            item = items[1] if len(items) > 1 else items[0]

            # 각 데이터 셀렉터 확인
            for sel in [".s-item__title", ".s-item__price", ".s-item__ended-date", "span.POSITIVE"]:
                el = item.query_selector(sel)
                if el:
                    print(f"  {sel}: {el.inner_text().strip()[:80]}")
                else:
                    print(f"  {sel}: ❌ 없음")
        else:
            print("\n❌ 항목 없음 — HTML 저장 중...")
            with open("ebay_debug.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            print("   ebay_debug.html 저장 완료 → VS Code에서 열어서 구조 확인")

        input("\n[Enter] 누르면 브라우저 닫힘...")
        browser.close()

if __name__ == "__main__":
    main()
