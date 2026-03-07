"""
Amazon Best Seller 크롤러 v1.1
- Playwright 기반 실제 브라우저 방식 (봇 감지 우회)
- 베스트셀러 TOP100: 홈데코 / 반려동물 / 주방용품
- [v1.1 수정] 이슈 #3: 순위변동 Amazon 비노출 확정 — None 처리
- [v1.1 수정] 상품명 셀렉터 태그 무관 클래스 매칭으로 수정
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
# 수집 대상 카테고리 (Amazon Best Sellers URL)
# ──────────────────────────────────────────────
CATEGORIES = [
    {
        "name": "홈데코",
        "url":  "https://www.amazon.com/Best-Sellers-Home-Decor/zgbs/home-garden/1063498/",
    },
    {
        "name": "반려동물",
        "url":  "https://www.amazon.com/Best-Sellers-Pet-Supplies/zgbs/pet-supplies/",
    },
    {
        "name": "주방용품",
        "url":  "https://www.amazon.com/Best-Sellers-Kitchen-Dining/zgbs/kitchen/",
    },
]

# ──────────────────────────────────────────────
# 설정값
# ──────────────────────────────────────────────
CONFIG = {
    "SHEET_ID":   "11T9l3AvP6bApBTH67vVN5MsY6OTlA5LzwI8bdvJwqDc",
    "SHEET_NAME": "Amazon_Best",
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
    "MAX_PAGES":   2,       # 1페이지 = 50개, 2페이지 = TOP100
    "RETRY_COUNT": 3,
    "DELAY_MIN":   5,       # 아마존은 최소 5초 (eBay보다 차단 강함)
    "DELAY_MAX":   15,
}

# ──────────────────────────────────────────────
# Proxy 설정 (.env에서 로드)
# ──────────────────────────────────────────────
PROXY_HOST      = os.environ.get("PROXY_HOST",      "p.webshare.io")
PROXY_PORT      = os.environ.get("PROXY_PORT",      "80")
PROXY_USER_BASE = os.environ.get("PROXY_USER_BASE", "wthluxio-us")
PROXY_PASSWORD  = os.environ.get("PROXY_PASSWORD",  "")

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
        logging.FileHandler("amazon_crawler.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# User-Agent 풀 — 아마존은 최신 Chrome UA 필수
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]


# ──────────────────────────────────────────────
# Proxy 검증
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
    """US IP가 확인된 프록시 반환"""
    pool = PROXY_LIST.copy()
    random.shuffle(pool)
    for proxy in pool:
        if verify_proxy_ip(proxy):
            return proxy
    logger.warning("⚠️ 모든 Proxy 검증 실패 — 첫 번째 프록시로 강제 진행")
    return PROXY_LIST[0]


# ──────────────────────────────────────────────
# 봇 감지 우회 브라우저 생성
# ──────────────────────────────────────────────
async def create_stealth_browser(playwright, proxy: dict):
    """
    아마존 봇 감지 우회 브라우저
    ⚠️ 반드시 Webshare US Proxy와 함께 사용
    ⚠️ 판매 계정과 절대 같은 프록시 사용 금지
    """
    browser = await playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--disable-extensions",
            "--single-process",        # EC2 메모리 절약
            "--disable-gpu",
            "--no-zygote",
            "--disable-setuid-sandbox",
            "--window-size=1920,1080",
        ],
    )

    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
        timezone_id="America/New_York",
        extra_http_headers={
            "Accept-Language":  "en-US,en;q=0.9",
            "Accept-Encoding":  "gzip, deflate, br",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "sec-ch-ua": '"Chromium";v="123", "Not:A-Brand";v="8"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
        },
        proxy=proxy,  # ✅ Webshare US Proxy 연결
    )

    # 봇 감지 핵심 우회 스크립트
    await context.add_init_script("""
        // webdriver 플래그 제거
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

        // 플러그인 위장
        Object.defineProperty(navigator, 'plugins', {
            get: () => [
                { name: 'Chrome PDF Plugin' },
                { name: 'Chrome PDF Viewer' },
                { name: 'Native Client' }
            ]
        });

        // 언어 설정
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });

        // Chrome 객체 위장
        window.chrome = {
            runtime: {},
            loadTimes: function() {},
            csi: function() {},
            app: {}
        };

        // permissions 위장 (아마존 추가 감지 우회)
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : originalQuery(parameters)
        );
    """)

    return browser, context


async def random_delay(min_sec=None, max_sec=None):
    """랜덤 딜레이 — 아마존은 최소 5초 이상 필수"""
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
        val = float(cleaned)
        return val if val > 0 else None
    except ValueError:
        return None


def parse_review_count(review_str: str) -> Optional[int]:
    """'1,234' → 1234 변환"""
    if not review_str:
        return None
    cleaned = re.sub(r"[^\d]", "", review_str)
    try:
        return int(cleaned)
    except ValueError:
        return None


def parse_rank_change(badge_text: str) -> Optional[int]:
    """
    순위변동 파싱 — 이슈 #3 수정: 문자열 대신 숫자 반환 (NULL 방지)
    'up 5 in last month' → 5  (상승)
    'down 3 in last month' → -3 (하락)
    'NEW' → 0 (신규 진입)
    파싱 불가 → 0
    """
    if not badge_text:
        return None
    text = badge_text.strip().lower()
    num_match = re.search(r"\d+", text)
    num = int(num_match.group()) if num_match else 0
    if "up" in text or "↑" in text:
        return num
    elif "down" in text or "↓" in text:
        return -num
    elif "new" in text:
        return 0
    return None


# ──────────────────────────────────────────────
# CAPTCHA / 차단 감지
# ──────────────────────────────────────────────
async def check_blocked(page: Page) -> bool:
    """아마존 CAPTCHA 또는 차단 페이지 감지"""
    try:
        content = await page.content()
        title   = await page.title()

        # 차단 신호 키워드
        block_signals = [
            "robot check",
            "captcha",
            "enter the characters",
            "automated access",
            "unusual traffic",
            "temporarily unavailable",
        ]
        combined = (content + title).lower()
        for signal in block_signals:
            if signal in combined:
                logger.warning(f"🚫 차단 감지: '{signal}' — 프록시 교체 필요")
                return True
        return False
    except Exception:
        return False


# ──────────────────────────────────────────────
# 베스트셀러 페이지 파싱
# ──────────────────────────────────────────────
async def parse_best_sellers(page: Page, category_name: str, page_num: int) -> list[dict]:
    """
    아마존 베스트셀러 파싱
    ─────────────────────────────────────────────
    셀렉터 (2026-03 기준):
      상품 카드   : div.zg-grid-general-faceout  또는  li.zg-item-immersion
      순위        : span.zg-bdg-text
      상품명      : div._cDEzb_p13n-sc-css-line-clamp-3_g3dy1  또는  span.a-size-base-plus
      가격        : span.a-price > span.a-offscreen  또는  span._cDEzb_p13n-sc-price_3mJ9Z
      리뷰 점수   : span.a-icon-alt
      리뷰 수     : span.a-size-small > a  (두 번째 링크)
      ASIN        : div[data-asin]
      상품 URL    : a.a-link-normal (첫 번째)
      이미지      : img.a-dynamic-image  또는  img.s-image
      순위변동    : span.zg-badge-text  또는  div._cDEzb_p13n-sc-badge_3mJ9Z span
    ─────────────────────────────────────────────
    """
    results = []

    # 페이지 로드 대기 — 아마존은 동적 렌더링 대기 필요
    try:
        await page.wait_for_selector(
            "div.zg-grid-general-faceout, li.zg-item-immersion",
            timeout=20000,
        )
    except Exception:
        logger.warning(f"⚠️ 상품 카드 로드 타임아웃 — 페이지 구조 변경 가능성")
        # 디버그용 HTML 저장
        html = await page.content()
        debug_path = f"amazon_debug_{category_name}_p{page_num}.html"
        Path(debug_path).write_text(html, encoding="utf-8")
        logger.info(f"🔍 디버그 HTML 저장: {debug_path}")
        return []

    # 차단 감지
    if await check_blocked(page):
        return []

    # 상품 카드 수집 (두 가지 셀렉터 모두 시도)
    items = await page.query_selector_all("div.zg-grid-general-faceout")
    if not items:
        items = await page.query_selector_all("li.zg-item-immersion")

    logger.info(f"📦 상품 카드 발견: {len(items)}개")

    for idx, item in enumerate(items):
        try:
            # ── ASIN (상품 고유 ID)
            asin = ""
            asin_el = await item.query_selector("div[data-asin]")
            if asin_el:
                asin = await asin_el.get_attribute("data-asin") or ""
            # ASIN이 없으면 URL에서 추출 (fallback)

            # ── 순위
            rank = 0
            rank_el = await item.query_selector("span.zg-bdg-text")
            if rank_el:
                rank_text = await rank_el.inner_text()
                rank_match = re.search(r"\d+", rank_text)
                rank = int(rank_match.group()) if rank_match else 0
            # 순위가 파싱 안 되면 인덱스로 계산
            if rank == 0:
                rank = (page_num - 1) * 50 + idx + 1

            # ── 상품명 — 태그 무관 클래스명으로 매칭 (div/span 혼용 대응)
            title = ""
            for sel in [
                "._cDEzb_p13n-sc-css-line-clamp-3_g3dy1",   # 태그 무관 클래스 매칭
                "div._cDEzb_p13n-sc-css-line-clamp-3_g3dy1",
                "span.a-size-base-plus",
                "span.a-size-small.a-text-normal",
                "a.a-link-normal span",
            ]:
                title_el = await item.query_selector(sel)
                if title_el:
                    title = (await title_el.inner_text()).strip()
                    if title:
                        break

            # ── 가격
            price = None
            for sel in [
                "span._cDEzb_p13n-sc-price_3mJ9Z",
                "span.a-price span.a-offscreen",
                "span.p13n-sc-price",
            ]:
                price_el = await item.query_selector(sel)
                if price_el:
                    price_str = await price_el.inner_text()
                    price = parse_price(price_str)
                    if price:
                        break

            # ── 리뷰 점수 (별점)
            rating = ""
            rating_el = await item.query_selector("span.a-icon-alt")
            if rating_el:
                rating_text = await rating_el.inner_text()
                rating_match = re.search(r"[\d.]+", rating_text)
                rating = rating_match.group() if rating_match else ""

            # ── 리뷰 수
            review_count = None
            review_els = await item.query_selector_all("span.a-size-small a, a.a-link-normal span.a-size-small")
            for rel in review_els:
                rt = await rel.inner_text()
                if re.search(r"[\d,]+", rt) and len(rt) < 20:
                    review_count = parse_review_count(rt)
                    if review_count:
                        break

            # ── 상품 URL + ASIN fallback
            url = ""
            link_el = await item.query_selector("a.a-link-normal")
            if link_el:
                href = await link_el.get_attribute("href") or ""
                if href.startswith("/"):
                    url = "https://www.amazon.com" + href
                else:
                    url = href
                # URL에서 ASIN 추출 (fallback)
                if not asin:
                    asin_match = re.search(r"/dp/([A-Z0-9]{10})", url)
                    asin = asin_match.group(1) if asin_match else ""

            # ── 이미지 URL
            img_url = ""
            img_el = await item.query_selector("img.a-dynamic-image, img.s-image, img")
            if img_el:
                img_url = (
                    await img_el.get_attribute("src")
                    or await img_el.get_attribute("data-src")
                    or ""
                )

            # ── 순위변동 — Amazon 베스트셀러 페이지에서 현재 비노출 확인 (2026-03-07)
            # HTML 분석 결과 배지 요소 없음 → None 처리 (Sheets 빈칸)
            rank_change = None

            # 제목 없는 카드 (광고/더미) 스킵
            if not title:
                continue

            results.append({
                "수집일시":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "카테고리":   category_name,
                "순위":       rank,
                "순위변동":   rank_change,
                "ASIN":       asin,
                "상품명":     title,
                "가격":       price,
                "별점":       rating,
                "리뷰수":     review_count,
                "상품URL":    url,
                "이미지URL":  img_url,
            })

        except Exception as e:
            logger.warning(f"⚠️ [{idx}] 파싱 오류 (건너뜀): {e}")
            continue

    logger.info(f"✅ 파싱 완료: {len(results)}개")
    return results


# ──────────────────────────────────────────────
# 단일 페이지 크롤링 (브라우저 매번 새로 생성)
# ──────────────────────────────────────────────
async def crawl_page(url: str, category_name: str, page_num: int, proxy: dict) -> list[dict]:
    """
    페이지마다 브라우저 새로 생성 — 컨텍스트 종료 오류 방지
    (--single-process 모드 EC2 안정성 확보)
    """
    async with async_playwright() as playwright:
        browser, context = await create_stealth_browser(playwright, proxy)
        try:
            page = await context.new_page()

            # 아마존 접속 전 짧은 홈페이지 방문 (자연스러운 트래픽 패턴)
            await page.goto("https://www.amazon.com", wait_until="domcontentloaded", timeout=30000)
            await random_delay(2, 4)

            # 실제 베스트셀러 페이지 이동
            # 2페이지는 URL에 pg=2 파라미터 추가
            # 아마존 베스트셀러 2페이지 URL: ref_ + pg 파라미터 방식
            if page_num == 1:
                target_url = url
            else:
                base = url.rstrip("/")
                target_url = f"{base}/?ref_=zg_bs_pg_{page_num}&pg={page_num}"
            await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            await random_delay(3, 6)  # 렌더링 여유 대기

            return await parse_best_sellers(page, category_name, page_num)
        finally:
            await browser.close()


# ──────────────────────────────────────────────
# 카테고리 크롤링
# ──────────────────────────────────────────────
async def crawl_category(category: dict, proxy: dict) -> list[dict]:
    """카테고리 TOP100 크롤링 (2페이지 × 50개)"""
    all_results = []
    name = category["name"]
    base_url = category["url"]

    for page_num in range(1, CONFIG["MAX_PAGES"] + 1):
        logger.info(f"🌐 [{name}] 페이지 {page_num}/{CONFIG['MAX_PAGES']}")

        for attempt in range(1, CONFIG["RETRY_COUNT"] + 1):
            try:
                items = await crawl_page(base_url, name, page_num, proxy)

                if not items:
                    logger.warning(f"⚠️ [{name}] p{page_num}: 수집 0개 — CAPTCHA 또는 구조 변경 의심")
                    if attempt < CONFIG["RETRY_COUNT"]:
                        # 프록시 교체 후 재시도
                        proxy = get_valid_proxy()
                        logger.info(f"🔄 프록시 교체 후 재시도: {proxy['username']}")
                        await random_delay(10, 20)
                    continue

                all_results.extend(items)
                logger.info(f"📊 [{name}] 누적: {len(all_results)}개")
                break

            except Exception as e:
                logger.error(f"❌ [{name}] p{page_num} 시도 {attempt}/{CONFIG['RETRY_COUNT']} 실패: {e}")
                if attempt < CONFIG["RETRY_COUNT"]:
                    await random_delay(10, 20)
                else:
                    logger.error(f"💀 [{name}] p{page_num} 최종 실패, 건너뜀")

        # 페이지 간 딜레이 (아마존은 넉넉하게)
        if page_num < CONFIG["MAX_PAGES"]:
            await random_delay(8, 15)

    return all_results


# ──────────────────────────────────────────────
# 데이터 정합성 검증
# ──────────────────────────────────────────────
def validate_data(data: list[dict]) -> list[dict]:
    """상품명 누락·ASIN 중복 제거"""
    before = len(data)
    seen_asins = set()
    valid = []

    for row in data:
        if not row.get("상품명"):
            continue
        asin = row.get("ASIN")
        if asin:
            if asin in seen_asins:
                continue
            seen_asins.add(asin)
        valid.append(row)

    logger.info(f"🔍 정합성 검증: {before}개 → {len(valid)}개 (제거: {before - len(valid)}개)")
    return valid


# ──────────────────────────────────────────────
# Google Sheets 저장
# ──────────────────────────────────────────────
def save_to_sheets(data: list[dict]) -> bool:
    """수집 데이터를 Google Sheets에 저장 (카테고리별 시트)"""
    if not data:
        logger.warning("⚠️ 저장할 데이터 없음")
        return False

    try:
        sa_path = CONFIG["SERVICE_ACCOUNT_FILE"]
        if not Path(sa_path).exists():
            logger.error(f"❌ service_account.json 없음: {sa_path}")
            return False

        scopes = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(sa_path, scopes=scopes)
        gc = gspread.authorize(creds)
        spreadsheet = gc.open_by_key(CONFIG["SHEET_ID"])

        # 카테고리별로 별도 시트에 저장
        categories = list({row["카테고리"] for row in data})

        for cat in categories:
            cat_data = [row for row in data if row["카테고리"] == cat]
            sheet_name = f"Amazon_{cat}"

            try:
                worksheet = spreadsheet.worksheet(sheet_name)
            except gspread.WorksheetNotFound:
                worksheet = spreadsheet.add_worksheet(title=sheet_name, rows=5000, cols=20)
                logger.info(f"📋 새 시트 생성: {sheet_name}")

            headers = list(cat_data[0].keys())
            existing = worksheet.get_all_values()

            # 오늘 날짜 데이터는 덮어쓰기 (매일 최신 TOP100 유지)
            worksheet.clear()
            worksheet.append_row(headers)

            rows = [[str(row.get(col, "")) for col in headers] for row in cat_data]
            worksheet.append_rows(rows, value_input_option="USER_ENTERED")
            logger.info(f"✅ [{sheet_name}] 저장 완료: {len(rows)}행")

        return True

    except Exception as e:
        logger.error(f"❌ Google Sheets 저장 실패: {e}")
        return False


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────
async def main():
    logger.info("=" * 60)
    logger.info("🕷️  Amazon Best Seller 크롤러 v1.0 시작")
    logger.info(f"📋 카테고리: {[c['name'] for c in CATEGORIES]}")
    logger.info(f"📄 페이지당 최대: {CONFIG['MAX_PAGES']}p (TOP100)")
    logger.info("=" * 60)

    # ✅ 실행 전 Proxy US IP 검증
    logger.info("🔍 Proxy 검증 중...")
    proxy = get_valid_proxy()
    logger.info(f"🌐 사용 Proxy: {proxy['username']}@{PROXY_HOST}:{PROXY_PORT}")

    all_data = []

    for category in CATEGORIES:
        logger.info(f"\n{'─'*40}")
        logger.info(f"🛒 카테고리: [{category['name']}]")

        if all_data:
            await random_delay(10, 20)  # 카테고리 간 넉넉한 딜레이

        try:
            results = await crawl_category(category, proxy)
            all_data.extend(results)
            logger.info(f"🏁 [{category['name']}] 완료: {len(results)}개")
        except Exception as e:
            logger.error(f"💀 [{category['name']}] 실패: {e}")

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
