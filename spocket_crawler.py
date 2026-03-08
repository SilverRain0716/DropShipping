"""
Spocket 위닝 상품 크롤러 v1.0
- Playwright 기반 실제 브라우저 방식 (봇 감지 우회)
- Webshare Rotating US Proxy 연동 (10개 풀 로테이션)
- Free 플랜 로그인 → 상품 검색 → 필터링 → Google Sheets 저장

[비로그인 가능 여부 분석 결론]
- app.spocket.co 소싱가/정가/배송 데이터는 로그인 후에만 노출
- 비로그인 스크래핑으로는 핵심 수집 항목 획득 불가
→ Free 플랜 (카드 불필요) 계정으로 로그인 방식 채택

⚠️ 이 크롤러는 EC2(52.79.177.182) + Webshare US Proxy에서만 실행
⚠️ 로컬 PC 직접 실행 금지 — 키움 API(K-Trader) IP와 혼용 절대 금지
⚠️ K-Trader EC2(43.203.218.220)에서 실행 금지
⚠️ 판매 계정(Etsy/Amazon FBM/스마트스토어/쿠팡)과 동일 IP 사용 절대 금지
⚠️ service_account.json / .env / ktrader-key.pem GitHub 업로드 금지
"""

import asyncio
import random
import re
import logging
import os
from pathlib import Path
from datetime import datetime
from typing import Optional

import gspread
import requests
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from playwright.async_api import async_playwright, Page, BrowserContext

# .env 로드
load_dotenv()


# ──────────────────────────────────────────────
# 설정값 (CONFIG 딕셔너리 — eBay/Amazon 크롤러와 동일 구조)
# ──────────────────────────────────────────────
CONFIG = {
    # 검색 키워드
    "KEYWORDS": [
        "metal wall art",
        "gothic decor",
        "skull decor",
        "dark home decor",
        "wall sculpture",
        "halloween decor",
    ],

    # 수집 필터 조건
    "PRICE_MIN":         10.0,   # 소싱가 최소 ($)
    "PRICE_MAX":         40.0,   # 소싱가 최대 ($)
    "MARGIN_MIN_PCT":    35.0,   # 최소 마진율 (%)
    "SHIP_FROM":         "United States",
    "MAX_DELIVERY_DAYS": 14,     # 최대 배송일

    # Google Sheets
    "SHEET_ID":   os.environ.get("SHEET_ID", "11T9l3AvP6bApBTH67vVN5MsY6OTlA5LzwI8bdvJwqDc"),
    "SHEET_NAME": "Spocket_위닝후보",
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

    # 크롤러 동작
    "RETRY_COUNT": 3,
    "DELAY_MIN":   3,
    "DELAY_MAX":   10,

    # Spocket 로그인 URL
    "LOGIN_URL":  "https://app.spocket.co/login",
    "SEARCH_URL": "https://app.spocket.co/products",
}


# ──────────────────────────────────────────────
# Proxy 설정 (.env에서 로드 — eBay 크롤러와 동일)
# ──────────────────────────────────────────────
PROXY_HOST      = os.environ.get("PROXY_HOST",      "p.webshare.io")
PROXY_PORT      = os.environ.get("PROXY_PORT",      "80")
PROXY_USER_BASE = os.environ.get("PROXY_USER_BASE", "wthluxio-us")
PROXY_PASSWORD  = os.environ.get("PROXY_PASSWORD",  "")

# Spocket 계정
SPOCKET_EMAIL    = os.environ.get("SPOCKET_EMAIL",    "")
SPOCKET_PASSWORD = os.environ.get("SPOCKET_PASSWORD", "")

# Slack 웹훅 (선택)
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

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
        logging.FileHandler("spocket_crawler.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# User-Agent 풀 (로테이션)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]


# ──────────────────────────────────────────────
# Proxy 검증 (eBay 크롤러와 동일 함수)
# ──────────────────────────────────────────────
def verify_proxy_ip(proxy: dict) -> bool:
    """프록시 경유 후 미국 IP인지 확인"""
    try:
        proxies = {
            "http":  proxy["server"].replace("http://", f"http://{proxy['username']}:{proxy['password']}@"),
            "https": proxy["server"].replace("http://", f"http://{proxy['username']}:{proxy['password']}@"),
        }
        res = requests.get("https://api.ipify.org?format=json", proxies=proxies, timeout=10)
        ip = res.json().get("ip", "")
        logger.info(f"🌐 Proxy IP 확인: {ip}")
        return bool(ip)
    except Exception as e:
        logger.warning(f"⚠️ Proxy IP 확인 실패: {e}")
        return False


def get_valid_proxy() -> dict:
    """유효한 프록시 반환 (랜덤 선택 후 검증)"""
    pool = PROXY_LIST.copy()
    random.shuffle(pool)
    for proxy in pool:
        if verify_proxy_ip(proxy):
            return proxy
    return PROXY_LIST[0]


# ──────────────────────────────────────────────
# Stealth 브라우저 생성 (eBay 크롤러와 동일 함수)
# ──────────────────────────────────────────────
async def create_stealth_browser(playwright, proxy: dict):
    """
    봇 감지 우회 브라우저 생성
    ⚠️ 반드시 Webshare US Proxy와 함께 사용 — 크롤러 EC2 전용
    ⚠️ 판매 계정과 절대 같은 프록시 사용 금지
    """
    browser = await playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",  # 자동화 감지 비활성화
            "--disable-dev-shm-usage",                        # EC2 /dev/shm 부족 방지
            "--disable-extensions",
            "--single-process",                               # 메모리 절약 — t3.micro 크래시 방지
            "--disable-gpu",
            "--no-zygote",
            "--disable-setuid-sandbox",
        ],
    )

    context: BrowserContext = await browser.new_context(
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
# 파싱 유틸리티
# ──────────────────────────────────────────────
def parse_price(price_str: str) -> Optional[float]:
    """'$12.99', '12.99' 등 문자열에서 float 추출"""
    if not price_str:
        return None
    cleaned = re.sub(r"[^\d.]", "", price_str.strip())
    try:
        val = float(cleaned)
        return val if val > 0 else None
    except ValueError:
        return None


def calc_margin(sourcing: float, retail: float) -> float:
    """마진율 계산: (정가 - 소싱가) / 정가 * 100"""
    if retail <= 0:
        return 0.0
    return round((retail - sourcing) / retail * 100, 2)


def parse_delivery_days(text: str) -> int:
    """
    '7-14 business days', 'Ships in 3-5 days' 등에서 최대 일수 추출
    파싱 불가 시 99 반환 (필터 탈락 처리)
    """
    numbers = re.findall(r"\d+", text or "")
    return max(int(n) for n in numbers) if numbers else 99


# ──────────────────────────────────────────────
# Spocket 로그인
# ──────────────────────────────────────────────
async def spocket_login(page: Page) -> bool:
    """
    Spocket Free 플랜 계정 로그인.
    실패 시 스크린샷 저장 후 False 반환.

    [Free 플랜 재가입 방법]
    1. https://app.spocket.co/register 접속
    2. 이메일 + 비밀번호 입력 (카드 불필요)
    3. .env에 SPOCKET_EMAIL / SPOCKET_PASSWORD 등록
    """
    if not SPOCKET_EMAIL or not SPOCKET_PASSWORD:
        logger.error("❌ SPOCKET_EMAIL / SPOCKET_PASSWORD .env 미설정")
        return False

    logger.info("🔐 Spocket 로그인 시도 중...")
    try:
        await page.goto(CONFIG["LOGIN_URL"], wait_until="networkidle", timeout=30000)
        await asyncio.sleep(random.uniform(2, 4))

        # 이메일 입력
        email_sel = 'input[type="email"], input[name="email"], #email, [placeholder*="email"]'
        await page.wait_for_selector(email_sel, timeout=10000)
        await page.fill(email_sel, SPOCKET_EMAIL)
        await asyncio.sleep(random.uniform(0.5, 1.2))

        # 비밀번호 입력
        pw_sel = 'input[type="password"], input[name="password"], #password'
        await page.fill(pw_sel, SPOCKET_PASSWORD)
        await asyncio.sleep(random.uniform(0.5, 1.2))

        # 로그인 버튼 클릭
        btn_sel = 'button[type="submit"], button:has-text("Log in"), button:has-text("Sign in")'
        await page.click(btn_sel)

        # 대시보드 이동 확인
        await page.wait_for_url("**/dashboard**", timeout=20000)
        logger.info("✅ Spocket 로그인 성공")
        return True

    except Exception as e:
        logger.error(f"❌ 로그인 실패: {e}")
        try:
            await page.screenshot(path="spocket_login_error.png")
            logger.info("📸 스크린샷 저장: spocket_login_error.png")
        except Exception:
            pass
        return False


# ──────────────────────────────────────────────
# 무한 스크롤 (모든 상품 로드)
# ──────────────────────────────────────────────
async def scroll_to_bottom(page: Page, max_scrolls: int = 20) -> None:
    """무한 스크롤 끝까지 내려 모든 상품 카드 로드"""
    prev_height = 0
    no_change = 0

    for i in range(max_scrolls):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(random.uniform(1.5, 3.0))

        current_height = await page.evaluate("document.body.scrollHeight")
        if current_height == prev_height:
            no_change += 1
            if no_change >= 2:
                logger.debug(f"  스크롤 종료 ({i+1}회, 높이 변화 없음)")
                break
        else:
            no_change = 0
        prev_height = current_height


# ──────────────────────────────────────────────
# 단일 상품 카드 파싱
# ──────────────────────────────────────────────
async def parse_product_card(card, keyword: str) -> Optional[dict]:
    """
    상품 카드 요소 → 수집 항목 dict 변환.
    ⚠️ Spocket은 React SPA — 클래스명이 빌드마다 변경될 수 있음.
       첫 실행 후 spocket_login_error.png 또는 로그로 셀렉터 확인 필요.
    """
    try:
        # ── 상품명 ──
        name_el = await card.query_selector(
            '[class*="product-name"], [class*="ProductName"], [class*="title"], '
            'h3, h4, [data-testid="product-name"]'
        )
        name = (await name_el.inner_text()).strip() if name_el else ""
        if not name:
            return None

        # ── 소싱가 (Spocket 도매가 / Cost) ──
        cost_el = await card.query_selector(
            '[class*="cost"], [class*="wholesale"], [class*="sourcing"], '
            '[data-testid="cost-price"], [class*="Cost"]'
        )
        cost_str = (await cost_el.inner_text()).strip() if cost_el else ""
        sourcing_price = parse_price(cost_str)

        # ── 정가 (Retail Price / Suggested) ──
        retail_el = await card.query_selector(
            '[class*="retail"], [class*="suggested"], [class*="Retail"], '
            '[data-testid="retail-price"]'
        )
        retail_str = (await retail_el.inner_text()).strip() if retail_el else ""
        retail_price = parse_price(retail_str)

        # 가격 파싱 실패 시 건너뜀
        if sourcing_price is None or retail_price is None:
            return None

        # ── 배송비 ──
        ship_cost_el = await card.query_selector(
            '[class*="shipping"], [class*="Shipping"], [data-testid="shipping-cost"]'
        )
        ship_cost_str = (await ship_cost_el.inner_text()).strip() if ship_cost_el else ""
        is_free_ship = (
            "free" in ship_cost_str.lower()
            or ship_cost_str in ("$0", "$0.00", "0", "")
        )
        shipping_display = "무료 배송" if is_free_ship else ship_cost_str

        # ── Ships from ──
        from_el = await card.query_selector(
            '[class*="ships-from"], [class*="ShipsFrom"], [class*="location"], '
            '[data-testid="ships-from"]'
        )
        ships_from = (await from_el.inner_text()).strip() if from_el else ""

        # ── 배송 기간 ──
        delivery_el = await card.query_selector(
            '[class*="delivery"], [class*="dispatch"], [class*="days"], '
            '[data-testid="delivery-days"]'
        )
        delivery_str = (await delivery_el.inner_text()).strip() if delivery_el else ""
        delivery_days = parse_delivery_days(delivery_str)

        # ── 상품 URL ──
        link_el = await card.query_selector("a[href]")
        href = await link_el.get_attribute("href") if link_el else ""
        base = "https://app.spocket.co"
        product_url = href if href.startswith("http") else base + href

        # ── 마진율 계산 ──
        margin_pct = calc_margin(sourcing_price, retail_price)

        return {
            "상품명":        name,
            "소싱가($)":     sourcing_price,
            "정가($)":       retail_price,
            "마진율(%)":     margin_pct,
            "배송비":        shipping_display,
            "Ships from":    ships_from,
            "배송기간(일)":  delivery_days if delivery_days < 99 else "",
            "상품URL":       product_url,
            "검색키워드":    keyword,
            "수집시각(KST)": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }

    except Exception as e:
        logger.debug(f"카드 파싱 건너뜀: {e}")
        return None


# ──────────────────────────────────────────────
# 필터 조건 검증
# ──────────────────────────────────────────────
def passes_filter(product: dict) -> bool:
    """
    수집 조건 필터:
    - 소싱가 $10~$40
    - 마진율 35%+
    - Ships from United States
    - 배송 14일 이내
    """
    if not product.get("상품명"):
        return False
    sp = product.get("소싱가($)", 0) or 0
    rp = product.get("정가($)", 0) or 0
    if sp < CONFIG["PRICE_MIN"] or sp > CONFIG["PRICE_MAX"]:
        return False
    if rp <= sp:  # 정가 <= 소싱가 이상값 제거
        return False
    if product.get("마진율(%)", 0) < CONFIG["MARGIN_MIN_PCT"]:
        return False
    if CONFIG["SHIP_FROM"].lower() not in (product.get("Ships from") or "").lower():
        return False
    days = product.get("배송기간(일)", 99)
    if days and days > CONFIG["MAX_DELIVERY_DAYS"]:
        return False
    return True


# ──────────────────────────────────────────────
# 키워드 단위 검색 + 수집
# ──────────────────────────────────────────────
async def crawl_keyword(page: Page, keyword: str) -> list[dict]:
    """
    키워드 검색 → 무한스크롤 → 상품 카드 파싱 → 필터 적용.
    재시도 3회 포함.
    """
    results = []

    for attempt in range(1, CONFIG["RETRY_COUNT"] + 1):
        try:
            # URL 파라미터로 필터 전달 시도 (UI 조작 보완)
            import urllib.parse
            search_url = (
                f"{CONFIG['SEARCH_URL']}"
                f"?search={urllib.parse.quote(keyword)}"
                f"&shipsFrom=US"
                f"&minPrice={int(CONFIG['PRICE_MIN'])}"
                f"&maxPrice={int(CONFIG['PRICE_MAX'])}"
            )
            await page.goto(search_url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(random.uniform(2, 4))

            # 상품 로드 대기
            await page.wait_for_selector(
                '[class*="product-card"], [class*="ProductCard"], '
                '[data-testid="product-card"], [class*="product-item"]',
                timeout=15000,
            )

            # 무한 스크롤로 전체 로드
            await scroll_to_bottom(page)

            # 상품 카드 수집
            cards = await page.query_selector_all(
                '[class*="product-card"], [class*="ProductCard"], '
                '[data-testid="product-card"], [class*="product-item"]'
            )
            logger.info(f"  └── 카드 {len(cards)}개 발견")

            for card in cards:
                product = await parse_product_card(card, keyword)
                if product and passes_filter(product):
                    results.append(product)

            logger.info(f"  └── 필터 통과: {len(results)}개")
            break  # 성공

        except Exception as e:
            logger.error(f"  ❌ 시도 {attempt}/{CONFIG['RETRY_COUNT']} 실패: {e}")
            if attempt < CONFIG["RETRY_COUNT"]:
                await random_delay(5, 12)
            else:
                logger.error(f"  💀 '{keyword}' 최대 재시도 초과, 건너뜀")

    return results


# ──────────────────────────────────────────────
# 데이터 정합성 검증 (eBay 크롤러와 동일 구조)
# ──────────────────────────────────────────────
def validate_data(data: list[dict]) -> list[dict]:
    """
    - 상품명 누락 제거
    - 가격 0/None 제거
    - URL 기준 중복 제거
    - 마진율 재계산 일치 검증
    """
    before = len(data)
    seen_urls = set()
    valid = []

    for row in data:
        if not row.get("상품명"):
            continue
        sp = row.get("소싱가($)")
        rp = row.get("정가($)")
        if not sp or not rp or sp <= 0 or rp <= 0:
            continue
        url = row.get("상품URL", "")
        if url in seen_urls:
            continue
        seen_urls.add(url)

        # 마진율 재검증
        recalc = calc_margin(sp, rp)
        if abs(recalc - (row.get("마진율(%)") or 0)) > 0.5:
            logger.warning(f"마진율 재계산 적용: {row['상품명'][:30]} ({row['마진율(%)']} → {recalc})")
            row["마진율(%)"] = recalc

        valid.append(row)

    logger.info(f"🔍 정합성 검증: {before}개 → {len(valid)}개 (제거: {before - len(valid)}개)")
    return valid


# ──────────────────────────────────────────────
# Google Sheets 저장 (eBay 크롤러와 동일 구조)
# ──────────────────────────────────────────────
def save_to_sheets(data: list[dict]) -> bool:
    """
    Google Sheets "Spocket_위닝후보" 시트에 저장.
    기존 데이터 초기화 후 마진율 높은 순 덮어쓰기.
    재시도 3회 포함.
    """
    if not data:
        logger.warning("⚠️ 저장할 데이터 없음")
        return False

    sa_path = CONFIG["SERVICE_ACCOUNT_FILE"]
    if not Path(sa_path).exists():
        logger.error(f"❌ service_account.json 없음: {sa_path}")
        return False

    headers = [
        "상품명", "소싱가($)", "정가($)", "마진율(%)",
        "배송비", "Ships from", "배송기간(일)",
        "상품URL", "검색키워드", "수집시각(KST)",
    ]

    for attempt in range(1, CONFIG["RETRY_COUNT"] + 1):
        try:
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
                worksheet = spreadsheet.add_worksheet(
                    title=CONFIG["SHEET_NAME"], rows=5000, cols=len(headers)
                )
                logger.info(f"📋 새 시트 생성: '{CONFIG['SHEET_NAME']}'")

            # 초기화 후 전체 덮어쓰기 (마진율 높은 순 정렬 유지)
            worksheet.clear()
            rows = [headers] + [
                [str(row.get(h, "")) for h in headers] for row in data
            ]
            worksheet.update("A1", rows, value_input_option="USER_ENTERED")

            # 헤더 굵게
            worksheet.format("A1:J1", {"textFormat": {"bold": True}})

            logger.info(f"✅ Google Sheets 저장 완료: {len(data)}행 → '{CONFIG['SHEET_NAME']}'")
            return True

        except Exception as e:
            logger.error(f"❌ Sheets 저장 실패 (시도 {attempt}/{CONFIG['RETRY_COUNT']}): {e}")
            if attempt < CONFIG["RETRY_COUNT"]:
                import time; time.sleep(random.uniform(5, 10))

    return False


# ──────────────────────────────────────────────
# Slack 알림
# ──────────────────────────────────────────────
def send_slack(products: list[dict], success: bool) -> None:
    """Slack 웹훅으로 수집 결과 전송. SLACK_WEBHOOK_URL 미설정 시 건너뜀."""
    if not SLACK_WEBHOOK_URL:
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M KST")
    if success and products:
        top3 = products[:3]
        top_text = "\n".join(
            f"  {i+1}. {p['상품명'][:28]} | 마진 {p['마진율(%)']}% | 소싱 ${p['소싱가($)']}"
            for i, p in enumerate(top3)
        )
        msg = (
            f"✅ *Spocket 위닝 상품 수집 완료* ({now})\n"
            f"총 {len(products)}개 (마진 {CONFIG['MARGIN_MIN_PCT']}%↑ / US 배송)\n\n"
            f"*🏆 TOP 3:*\n{top_text}\n\n"
            f"📊 Google Sheets `{CONFIG['SHEET_NAME']}` 저장 완료"
        )
    else:
        msg = f"❌ *Spocket 크롤러 실패/결과 없음* ({now}) — 로그 확인 필요"

    try:
        res = requests.post(SLACK_WEBHOOK_URL, json={"text": msg}, timeout=10)
        if res.status_code == 200:
            logger.info("✅ Slack 알림 전송 완료")
        else:
            logger.warning(f"Slack 응답 이상: {res.status_code}")
    except Exception as e:
        logger.error(f"Slack 알림 실패: {e}")


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────
async def main():
    logger.info("=" * 60)
    logger.info("🕷️  Spocket 위닝 상품 크롤러 v1.0 시작")
    logger.info(f"📋 키워드: {CONFIG['KEYWORDS']}")
    logger.info(f"💰 소싱가 범위: ${CONFIG['PRICE_MIN']}~${CONFIG['PRICE_MAX']}")
    logger.info(f"📈 최소 마진율: {CONFIG['MARGIN_MIN_PCT']}%")
    logger.info("=" * 60)

    # ── Proxy 검증 ──
    logger.info("🔍 Proxy 검증 중...")
    proxy = get_valid_proxy()
    logger.info(f"🌐 사용 Proxy: {proxy['username']}@{PROXY_HOST}:{PROXY_PORT}")

    all_data = []

    async with async_playwright() as pw:
        browser, context = await create_stealth_browser(pw, proxy)
        page = await context.new_page()

        # ── 로그인 (재시도 3회) ──
        login_ok = False
        for attempt in range(1, CONFIG["RETRY_COUNT"] + 1):
            login_ok = await spocket_login(page)
            if login_ok:
                break
            logger.warning(f"로그인 재시도 {attempt}/{CONFIG['RETRY_COUNT']}")
            await random_delay(5, 10)

        if not login_ok:
            logger.error("❌ 로그인 3회 모두 실패 — 크롤러 중단")
            await browser.close()
            send_slack([], success=False)
            return

        # ── 키워드별 수집 ──
        for keyword in CONFIG["KEYWORDS"]:
            logger.info(f"\n{'─'*40}")
            logger.info(f"🔍 키워드: '{keyword}'")

            if all_data:
                await random_delay()

            try:
                results = await crawl_keyword(page, keyword)
                all_data.extend(results)
                logger.info(f"🏁 '{keyword}' 완료: {len(results)}개")
            except Exception as e:
                logger.error(f"💀 '{keyword}' 실패: {e}")

        await browser.close()

    logger.info(f"\n{'='*60}")
    logger.info(f"📊 전체 수집: {len(all_data)}개")

    # ── 정합성 검증 ──
    validated = validate_data(all_data)

    # ── 마진율 높은 순 정렬 ──
    validated.sort(key=lambda x: x.get("마진율(%)", 0), reverse=True)

    # ── Sheets 저장 ──
    save_ok = False
    if validated:
        save_ok = save_to_sheets(validated)
    else:
        logger.warning("⚠️ 저장할 유효 데이터 없음")

    # ── Slack 알림 ──
    send_slack(validated, success=save_ok)

    logger.info(f"🏁 크롤러 종료 | 최종 저장: {len(validated)}개")


if __name__ == "__main__":
    asyncio.run(main())
