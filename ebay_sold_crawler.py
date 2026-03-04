"""
eBay Sold Listings 크롤러 v2.2
- Playwright 기반 실제 브라우저 방식 (Akamai 봇 감지 우회)
- Webshare Rotating US Proxy 연동 (10개 풀 로테이션)
- HTML 구조 분석 완료 (2026-03-02) 기준 셀렉터 적용
- Google Sheets 자동 저장 연동

⚠️ 이 크롤러는 EC2(52.79.177.182) + Webshare US Proxy에서만 실행
⚠️ 로컬 PC 직접 실행 금지 — 키움 API(K-Trader) IP와 혼용 절대 금지
⚠️ K-Trader EC2(43.203.218.220)에서 실행 금지
⚠️ 판매 계정(Etsy/스마트스토어/쿠팡)과 동일 IP 사용 절대 금지
⚠️ service_account.json / .env / ktrader-key.pem GitHub 업로드 금지
"""

import asyncio
import random
import re
import logging
import os
import requests
from pathlib import Path
from datetime import datetime
from typing import Optional

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from playwright.async_api import async_playwright, Page

# .env 로드
load_dotenv()

# ──────────────────────────────────────────────
# 설정값
# ──────────────────────────────────────────────
CONFIG = {
    "KEYWORDS": [
        "home decor",
        "wall art",
        "ceramic vase",
    ],
    "SHEET_ID":   "11T9l3AvP6bApBTH67vVN5MsY6OTlA5LzwI8bdvJwqDc",
    "SHEET_NAME": "eBay_Sold",
    "SERVICE_ACCOUNT_FILE": (
        os.environ.get("GOOGLE_SA_KEY_PATH")
        or str(next(
            (p for p in [
                Path(__file__).parent / "service_account.json",
                Path.home() / "dropship-crawler" / "service_account.json",
            ] if p.exists()),
            Path(__file__).parent / "service_account.json"
        ))
    ),
    "MAX_PAGES":   3,
    "RETRY_COUNT": 3,
    "DELAY_MIN":   3,
    "DELAY_MAX":   10,
    "BASE_URL": (
        "https://www.ebay.com/sch/i.html"
        "?_nkw={keyword}"
        "&LH_Sold=1"
        "&LH_Complete=1"
        "&_sop=13"
        "&_pgn={page}"
    ),
}

# ──────────────────────────────────────────────
# Proxy 설정 (.env에서 로드)
# ──────────────────────────────────────────────
PROXY_HOST      = os.environ.get("PROXY_HOST",      "p.webshare.io")
PROXY_PORT      = os.environ.get("PROXY_PORT",      "80")
PROXY_USER_BASE = os.environ.get("PROXY_USER_BASE", "wthluxio-us")
PROXY_PASSWORD  = os.environ.get("PROXY_PASSWORD",  "")

# 10개 프록시 풀 생성
PROXY_LIST = [
    {
        "server":   f"http://{PROXY_HOST}:{PROXY_PORT}",
        "username": f"{PROXY_USER_BASE}-{i}",
        "password": PROXY_PASSWORD,
    }
    for i in range(1, 11)
]

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("ebay_crawler.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# User-Agent 풀 (로테이션)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
]


# ──────────────────────────────────────────────
# Proxy IP 검증 (US 여부 확인)
# ──────────────────────────────────────────────
def verify_proxy_ip(proxy: dict) -> bool:
    """프록시가 US IP인지 확인 — 비미국 IP면 크롤러 차단"""
    try:
        px = {
            "http":  f"http://{proxy['username']}:{proxy['password']}@{PROXY_HOST}:{PROXY_PORT}",
            "https": f"http://{proxy['username']}:{proxy['password']}@{PROXY_HOST}:{PROXY_PORT}",
        }
        ip  = requests.get("https://api.ipify.org?format=json", proxies=px, timeout=10).json()["ip"]
        geo = requests.get(f"http://ip-api.com/json/{ip}", timeout=10).json()
        cc  = geo.get("countryCode", "??")
        if cc != "US":
            logger.warning(f"⚠️ 비미국 IP 감지: {ip} ({cc}) — 해당 프록시 건너뜀")
            return False
        logger.info(f"✅ Proxy 검증 완료: {ip} | {cc} / {geo.get('city','?')}")
        return True
    except Exception as e:
        logger.warning(f"⚠️ Proxy 검증 실패: {e}")
        return False


def get_valid_proxy() -> dict:
    """US IP가 확인된 프록시 반환 (랜덤 선택 후 검증)"""
    # 프록시 풀을 섞어서 순서 무작위화
    pool = PROXY_LIST.copy()
    random.shuffle(pool)
    for proxy in pool:
        if verify_proxy_ip(proxy):
            return proxy
    # 검증 실패해도 첫 번째 반환 (네트워크 문제일 수 있음)
    logger.warning("⚠️ 모든 Proxy 검증 실패 — 첫 번째 프록시로 강제 진행")
    return PROXY_LIST[0]


# ──────────────────────────────────────────────
# 봇 감지 우회 브라우저 생성
# ──────────────────────────────────────────────
async def create_stealth_browser(playwright, proxy: dict):
    """
    Akamai/PerimeterX 봇 감지 우회 브라우저 생성
    ⚠️ 반드시 Webshare US Proxy와 함께 사용 — 크롤러 EC2 전용
    ⚠️ 판매 계정과 절대 같은 프록시 사용 금지
    """
    browser = await playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",  # 자동화 감지 비활성화
            "--disable-dev-shm-usage",                        # EC2 /dev/shm 부족 방지 (크래시 핵심 해결)
            "--disable-extensions",
            "--single-process",                               # 메모리 절약 — EC2 t2/t3.micro 크래시 방지
            "--disable-gpu",
            "--no-zygote",                                    # 프로세스 포크 최소화
            "--disable-setuid-sandbox",
        ],
    )

    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={
            "width":  random.choice([1366, 1440, 1920]),
            "height": random.choice([768, 900, 1080]),
        },
        locale="en-US",
        timezone_id="America/New_York",
        extra_http_headers={
            "Accept-Language":  "en-US,en;q=0.9",
            "Accept-Encoding":  "gzip, deflate, br",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        },
        proxy=proxy,  # ✅ Webshare US Proxy 연결
    )

    # navigator.webdriver 플래그 제거 (봇 감지 핵심 우회)
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', {
            get: () => [
                { name: 'Chrome PDF Plugin' },
                { name: 'Chrome PDF Viewer' },
                { name: 'Native Client' }
            ]
        });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        window.chrome = { runtime: {} };
    """)

    return browser, context


async def random_delay(min_sec=None, max_sec=None):
    """랜덤 딜레이 — 인간 행동 패턴 모방 / IP 차단 방지"""
    delay = random.uniform(
        min_sec or CONFIG["DELAY_MIN"],
        max_sec or CONFIG["DELAY_MAX"],
    )
    logger.info(f"⏱️ 대기 중... {delay:.1f}초")
    await asyncio.sleep(delay)


# ──────────────────────────────────────────────
# 유틸리티
# ──────────────────────────────────────────────
def parse_price(price_str: str) -> Optional[float]:
    """'$28.49' → 28.49 변환"""
    if not price_str:
        return None
    cleaned = re.sub(r"[^\d.]", "", price_str)
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_feedback_count(feedback_str: str) -> Optional[int]:
    """'99.6% positive (3.7K)' → 3700"""
    if not feedback_str:
        return None
    match = re.search(r"\(([0-9.]+)([KMk]?)\)", feedback_str)
    if not match:
        return None
    num, unit = float(match.group(1)), match.group(2).upper()
    if unit == "K":
        num *= 1000
    elif unit == "M":
        num *= 1000000
    return int(num)


# ──────────────────────────────────────────────
# 페이지 파싱 (검증된 셀렉터 적용)
# ──────────────────────────────────────────────
async def parse_sold_items(page: Page, keyword: str) -> list[dict]:
    """
    이베이 판매완료 검색결과 파싱
    ─────────────────────────────────────────────
    검증된 셀렉터 (2026-03-02 HTML 분석 기준):
      상품 카드     : li.s-card
      판매 완료 확인: div.s-card__caption span.su-styled-text  (→ "Sold" 포함)
      제목          : div.s-card__title span.su-styled-text.primary
      판매가        : span.s-card__price
      원가(할인전)  : span.su-styled-text.secondary.strikethrough
      상품URL       : a.s-card__link:not(.image-treatment)
      이미지        : img.s-card__image
      상품상태      : div.s-card__subtitle span.su-styled-text
      배송비        : div.s-card__attribute-row (delivery/shipping 포함 행)
      판매자ID      : div.su-card-container__attributes__secondary span.su-styled-text (1번째)
      판매자피드백  : div.su-card-container__attributes__secondary span.su-styled-text (2번째)
    ─────────────────────────────────────────────
    """
    results = []

    # 검색 결과 로드 대기
    try:
        await page.wait_for_selector("ul.srp-results", timeout=15000)
    except Exception:
        # 봇 차단 페이지 여부 확인
        content = await page.content()
        if "captcha" in content.lower() or "robot" in content.lower():
            logger.warning("🤖 봇 감지 페이지 감지 — 프록시 교체 필요")
        raise

    item_elements = await page.query_selector_all("li.s-card")
    logger.info(f"📦 li.s-card 발견: {len(item_elements)}개")

    for idx, item in enumerate(item_elements):
        try:
            # 실제 판매 완료 상품 필터링 (Sold 텍스트 없으면 더미 카드)
            sold_date_el = await item.query_selector("div.s-card__caption span.su-styled-text")
            if not sold_date_el:
                continue
            sold_date_text = await sold_date_el.inner_text()
            if "Sold" not in sold_date_text:
                continue

            # ── 제목
            title_el = await item.query_selector("div.s-card__title span.su-styled-text.primary")
            title = (await title_el.inner_text()).strip() if title_el else ""

            # ── 판매가 (실제 낙찰/거래가)
            price_el = await item.query_selector("span.s-card__price")
            price_str = await price_el.inner_text() if price_el else ""
            price = parse_price(price_str)

            # ── 원가 (할인 전 가격 — strikethrough)
            orig_price_el = await item.query_selector("span.su-styled-text.secondary.strikethrough")
            orig_price_str = await orig_price_el.inner_text() if orig_price_el else ""
            orig_price = parse_price(orig_price_str)

            # ── 할인율
            discount_rate = None
            if orig_price and price and orig_price > 0:
                discount_rate = round((orig_price - price) / orig_price * 100, 1)

            # ── 상품 URL
            link_el = await item.query_selector("a.s-card__link:not(.image-treatment)")
            url = await link_el.get_attribute("href") if link_el else ""
            item_id_match = re.search(r"/itm/(\d+)", url or "")
            item_id = item_id_match.group(1) if item_id_match else ""

            # ── 이미지 URL
            img_el = await item.query_selector("img.s-card__image")
            img_url = ""
            if img_el:
                img_url = (
                    await img_el.get_attribute("src")
                    or await img_el.get_attribute("data-src")
                    or ""
                )

            # ── 상품 상태 (Pre-Owned, Brand New 등)
            condition_el = await item.query_selector("div.s-card__subtitle span.su-styled-text")
            condition = (await condition_el.inner_text()).strip() if condition_el else ""

            # ── 배송비
            shipping = ""
            attr_rows = await item.query_selector_all("div.s-card__attribute-row")
            for row in attr_rows:
                row_text = await row.inner_text()
                if "delivery" in row_text.lower() or "shipping" in row_text.lower() or "free" in row_text.lower():
                    shipping = row_text.strip()
                    break

            # ── 판매자 정보
            seller_spans = await item.query_selector_all(
                "div.su-card-container__attributes__secondary span.su-styled-text"
            )
            seller_id    = (await seller_spans[0].inner_text()).strip() if len(seller_spans) >= 1 else ""
            feedback_str = (await seller_spans[1].inner_text()).strip() if len(seller_spans) >= 2 else ""
            feedback_count = parse_feedback_count(feedback_str)

            # ── 스폰서 여부
            footer_el = await item.query_selector("div.s-card__footer")
            is_sponsored = False
            if footer_el:
                is_sponsored = "Sponsored" in (await footer_el.inner_text())

            results.append({
                "수집일시":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "키워드":        keyword,
                "아이템ID":      item_id,
                "제목":          title,
                "판매가":        price,
                "원가":          orig_price,
                "할인율(%)":     discount_rate,
                "판매일":        sold_date_text.replace("Sold", "").strip(),
                "상품상태":      condition,
                "배송비":        shipping,
                "판매자ID":      seller_id,
                "판매자피드백":  feedback_str,
                "판매자피드백수": feedback_count,
                "스폰서여부":    "Y" if is_sponsored else "N",
                "상품URL":       url,
                "이미지URL":     img_url,
            })

        except Exception as e:
            logger.warning(f"⚠️ [{idx}] 파싱 오류 (건너뜀): {e}")
            continue

    logger.info(f"✅ 파싱 완료: {len(results)}개 유효 상품")
    return results


# ──────────────────────────────────────────────
# 키워드 크롤링 (페이지 순회 + 재시도)
# ──────────────────────────────────────────────
async def crawl_page(url: str, keyword: str, proxy: dict) -> list[dict]:
    """
    단일 페이지 크롤링 — 페이지마다 브라우저를 새로 열어 컨텍스트 종료 오류 방지
    (--single-process 모드에서 장시간 실행 시 브라우저 종료 문제 해결)
    """
    async with async_playwright() as playwright:
        browser, context = await create_stealth_browser(playwright, proxy)
        try:
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await random_delay(2, 5)

            # 검색 결과 없는 페이지 감지
            no_result = await page.query_selector(".srp-save-null-search, .srp-no-results")
            if no_result:
                return []

            return await parse_sold_items(page, keyword)
        finally:
            await browser.close()


async def crawl_keyword(keyword: str, proxy: dict) -> list[dict]:
    """키워드 하나에 대해 MAX_PAGES 페이지 크롤링, 실패 시 RETRY_COUNT회 재시도"""
    all_results = []

    for page_num in range(1, CONFIG["MAX_PAGES"] + 1):
        url = CONFIG["BASE_URL"].format(
            keyword=keyword.replace(" ", "+"),
            page=page_num,
        )
        logger.info(f"🌐 '{keyword}' - 페이지 {page_num}/{CONFIG['MAX_PAGES']}")

        # 재시도 로직 (최대 3회) — 매 시도마다 새 브라우저 인스턴스
        for attempt in range(1, CONFIG["RETRY_COUNT"] + 1):
            try:
                items = await crawl_page(url, keyword, proxy)

                # 빈 결과 = 마지막 페이지 도달
                if not items and page_num > 1:
                    logger.info(f"🔚 '{keyword}' 페이지 {page_num}: 결과 없음, 중단")
                    return all_results

                all_results.extend(items)
                logger.info(f"📊 누적: {len(all_results)}개")
                break  # 성공 → 재시도 루프 탈출

            except Exception as e:
                logger.error(f"❌ 시도 {attempt}/{CONFIG['RETRY_COUNT']} 실패: {e}")
                if attempt < CONFIG["RETRY_COUNT"]:
                    await random_delay(5, 15)
                else:
                    logger.error(f"💀 '{keyword}' p{page_num} 최종 실패, 건너뜀")

        # 페이지 간 딜레이
        if page_num < CONFIG["MAX_PAGES"]:
            await random_delay()

    return all_results


# ──────────────────────────────────────────────
# 데이터 정합성 검증
# ──────────────────────────────────────────────
def validate_data(data: list[dict]) -> list[dict]:
    """제목 누락·가격 이상·아이템ID 중복 제거"""
    before = len(data)
    seen_ids = set()
    valid = []

    for row in data:
        if not row.get("제목"):
            continue
        price = row.get("판매가")
        if price is None or price <= 0:
            continue
        item_id = row.get("아이템ID")
        if item_id:
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)
        valid.append(row)

    logger.info(f"🔍 정합성 검증: {before}개 → {len(valid)}개 (제거: {before - len(valid)}개)")
    return valid


# ──────────────────────────────────────────────
# Google Sheets 저장
# ──────────────────────────────────────────────
def save_to_sheets(data: list[dict]) -> bool:
    """수집 데이터를 Google Sheets에 추가 저장 (헤더 자동 생성)"""
    if not data:
        logger.warning("⚠️ 저장할 데이터 없음")
        return False

    try:
        sa_path = CONFIG["SERVICE_ACCOUNT_FILE"]
        if not Path(sa_path).exists():
            logger.error(f"❌ service_account.json 없음: {sa_path}")
            return False

        logger.info(f"🔑 서비스 계정 키 경로: {sa_path}")
        scopes = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(sa_path, scopes=scopes)
        gc = gspread.authorize(creds)

        spreadsheet = gc.open_by_key(CONFIG["SHEET_ID"])
        try:
            worksheet = spreadsheet.worksheet(CONFIG["SHEET_NAME"])
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=CONFIG["SHEET_NAME"], rows=5000, cols=20)
            logger.info(f"📋 새 시트 생성: {CONFIG['SHEET_NAME']}")

        headers = list(data[0].keys())
        existing = worksheet.get_all_values()

        if not existing or existing[0] != headers:
            worksheet.clear()
            worksheet.append_row(headers)
            logger.info("📝 헤더 행 추가")

        rows = [[str(row.get(col, "")) for col in headers] for row in data]
        worksheet.append_rows(rows, value_input_option="USER_ENTERED")

        logger.info(f"✅ Google Sheets 저장 완료: {len(rows)}행 추가")
        return True

    except Exception as e:
        logger.error(f"❌ Google Sheets 저장 실패: {e}")
        return False


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────
async def main():
    logger.info("=" * 60)
    logger.info("🕷️  eBay Sold Listings 크롤러 v2.2 시작")
    logger.info(f"📋 키워드: {CONFIG['KEYWORDS']}")
    logger.info(f"📄 페이지당 최대: {CONFIG['MAX_PAGES']}p")
    logger.info("=" * 60)

    # ✅ 실행 전 Proxy US IP 검증
    logger.info("🔍 Proxy 검증 중...")
    proxy = get_valid_proxy()
    logger.info(f"🌐 사용 Proxy: {proxy['username']}@{PROXY_HOST}:{PROXY_PORT}")

    all_data = []

    for keyword in CONFIG["KEYWORDS"]:
        logger.info(f"\n{'─'*40}")
        logger.info(f"🔍 키워드: '{keyword}'")

        if all_data:
            await random_delay()

        try:
            results = await crawl_keyword(keyword, proxy)
            all_data.extend(results)
            logger.info(f"🏁 '{keyword}' 완료: {len(results)}개")
        except Exception as e:
            logger.error(f"💀 '{keyword}' 실패: {e}")

    logger.info(f"\n{'='*60}")
    logger.info(f"📊 전체 수집: {len(all_data)}개")

    validated = validate_data(all_data)

    if validated:
        save_to_sheets(validated)
    else:
        logger.warning("⚠️ 저장할 유효 데이터 없음")

    logger.info("🏁 크롤러 종료")


if __name__ == "__main__":
    asyncio.run(main())
