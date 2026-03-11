"""
cj_crawler.py — CJ Dropshipping 위닝 상품 크롤러 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Spocket(hCaptcha 차단) 대체 소싱처로 CJ 공식 API 채택
- Playwright/브라우저 불필요 — 순수 requests API 방식
- 구조: spocket_crawler.py와 동일 (CONFIG/validate/sheets/discord)

⚠️ 이 크롤러는 EC2(52.79.177.182) + Webshare Rotating Proxy에서만 실행
⚠️ 로컬 PC 직접 실행 금지 — 키움 API(K-Trader) IP와 혼용 절대 금지
⚠️ K-Trader EC2(43.203.218.220)에서 실행 금지
⚠️ 판매 계정(Etsy/Amazon FBM/스마트스토어/쿠팡)과 동일 IP 사용 절대 금지
⚠️ service_account.json / .env / ktrader-key.pem GitHub 업로드 금지
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import random
import time
import logging
import os
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── 환경 변수 로드
load_dotenv()


# ──────────────────────────────────────────────
# 설정값 (CONFIG 딕셔너리 — spocket_crawler.py와 동일 구조)
# ──────────────────────────────────────────────
CONFIG = {
    # 수집 카테고리 키워드
    "CATEGORIES": [
        "home decor",
        "wall art",
        "gothic",
        "halloween",
    ],

    # 수집 필터 조건
    "PRICE_MIN":         10.0,   # 소싱가 최소 ($)
    "PRICE_MAX":         40.0,   # 소싱가 최대 ($)
    "MARGIN_MIN_PCT":    35.0,   # 최소 마진율 (%)
    "MAX_DELIVERY_DAYS": 14,     # 최대 배송일
    "PAGE_SIZE":         50,     # 페이지당 상품 수
    "MAX_PAGES":         5,      # 카테고리당 최대 페이지 수

    # Google Sheets (spocket_crawler.py와 동일 Sheets 문서 사용)
    "SHEET_ID":   os.environ.get("SHEET_ID", "11T9l3AvP6bApBTH67vVN5MsY6OTlA5LzwI8bdvJwqDc"),
    "SHEET_NAME": "CJ_위닝후보",
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

    # 크롤러 동작 (spocket_crawler.py와 동일)
    "RETRY_COUNT": 3,
    "DELAY_MIN":   3,
    "DELAY_MAX":   10,

    # CJ API 엔드포인트
    "CJ_TOKEN_URL":   "https://developers.cjdropshipping.com/api2.0/v1/authentication/getAccessToken",
    "CJ_PRODUCT_URL": "https://developers.cjdropshipping.com/api2.0/v1/product/list",
    "CJ_SHIP_URL":    "https://developers.cjdropshipping.com/api2.0/v1/logistic/freightCalculate",
}


# ──────────────────────────────────────────────
# Proxy 설정 (spocket_crawler.py와 동일 변수명)
# ──────────────────────────────────────────────
PROXY_HOST      = os.environ.get("PROXY_HOST",      "p.webshare.io")
PROXY_PORT      = os.environ.get("PROXY_PORT",      "80")
PROXY_USER_BASE = os.environ.get("PROXY_USER_BASE", "wthluxio-us")
PROXY_PASSWORD  = os.environ.get("PROXY_PASSWORD",  "")

# CJ 계정 (토큰 발급용)
CJ_EMAIL    = os.environ.get("CJ_EMAIL",    "")
CJ_PASSWORD = os.environ.get("CJ_PASSWORD", "")   # .env에서 로드 — API Key를 password로 전달

# Discord 웹훅 (spocket_crawler.py와 동일 변수명)
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# KST 타임존
KST = timezone(timedelta(hours=9))

# 10개 프록시 풀 (spocket_crawler.py와 동일 구조)
# ⚠️ 판매 계정과 절대 동일 프록시 풀 사용 금지
PROXY_LIST = [
    {
        "server":   f"http://{PROXY_HOST}:{PROXY_PORT}",
        "username": f"{PROXY_USER_BASE}-{i}",
        "password": PROXY_PASSWORD,
    }
    for i in range(1, 11)
]

# User-Agent 풀 (spocket_crawler.py와 동일)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

# ── 로깅 설정 (spocket_crawler.py와 동일 포맷)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("cj_crawler.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Proxy 검증 (spocket_crawler.py와 동일 함수)
# ──────────────────────────────────────────────
def verify_proxy_ip(proxy: dict) -> bool:
    """프록시 경유 후 IP 확인 (미국 IP 여부 체크)"""
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
    logger.warning("⚠️ 모든 Proxy 검증 실패 — 첫 번째 Proxy 사용")
    return PROXY_LIST[0]


# ──────────────────────────────────────────────
# requests 세션 생성 (재시도 3회 + Proxy 적용)
# ──────────────────────────────────────────────
def build_session(proxy: dict) -> requests.Session:
    """
    재시도 로직 + Webshare Rotating Proxy 적용 세션 생성
    ⚠️ 판매 계정과 절대 동일 프록시 사용 금지
    """
    session = requests.Session()

    # 재시도 전략: 500/502/503/504 → 최대 3회, 지수 백오프
    retry = Retry(
        total=CONFIG["RETRY_COUNT"],
        backoff_factor=2,                          # 2초, 4초, 8초 대기
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    # Proxy 비활성화 (Webshare 대역폭 소진, CJ 공식 API는 프록시 불필요)
    # proxy_url = (
    #     f"http://{proxy['username']}:{proxy['password']}@"
    #     f"{PROXY_HOST}:{PROXY_PORT}"
    # )
    # session.proxies = {"http": proxy_url, "https": proxy_url}
    
    # User-Agent 로테이션 (IP 차단 우회)
    session.headers.update({
        "User-Agent":   random.choice(USER_AGENTS),
        "Accept":       "application/json",
        "Content-Type": "application/json",
    })

    return session


def random_delay(min_sec=None, max_sec=None) -> None:
    """랜덤 딜레이 — 인간 행동 패턴 모방 / IP 차단 방지"""
    delay = random.uniform(
        min_sec or CONFIG["DELAY_MIN"],
        max_sec or CONFIG["DELAY_MAX"],
    )
    logger.info(f"⏱️ 대기 중... {delay:.1f}초")
    time.sleep(delay)


# ──────────────────────────────────────────────
# STEP 5: CJ API 토큰 발급 (테스트 진입점)
# ──────────────────────────────────────────────
def get_access_token(session: requests.Session) -> Optional[str]:
    """
    CJ API 액세스 토큰 발급
    POST /authentication/getAccessToken
    Body: {"email": CJ_EMAIL, "password": CJ_PASSWORD}
    → accessToken 반환 (유효시간 12시간)
    실패 시 None 반환

    ⚠️ 인증 방식 주의:
    - 올바름: {"email": ..., "password": ...}   ← CJ_PASSWORD Secret 사용
    - 잘못됨: {"email": ..., "apiKey": ...}     ← code:1600005 오류 발생
    CJ_API_KEY Secret이 아닌 CJ_PASSWORD Secret을 반드시 사용할 것
    """
    if not CJ_EMAIL or not CJ_PASSWORD:
        logger.error("❌ CJ_EMAIL / CJ_PASSWORD .env 미설정")
        logger.error("   → GitHub Secrets: CJ_EMAIL, CJ_PASSWORD 등록 필요")
        logger.error("   → CJ_API_KEY Secret은 이 크롤러에서 사용하지 않음")
        return None

    # ✅ 올바른 인증 payload — password 필드 사용 (apiKey 사용 시 code:1600005 오류)
    # apiKey 방식 (프록시 우회 시 정상 동작 확인됨 2026-03-10)
    payload = {"apiKey": os.environ.get("CJ_API_KEY", "")}

    for attempt in range(1, CONFIG["RETRY_COUNT"] + 1):
        try:
            logger.info(f"🔑 CJ API 토큰 발급 시도 ({attempt}/{CONFIG['RETRY_COUNT']})...")
            resp = requests.post(
                CONFIG["CJ_TOKEN_URL"],
                json=payload,
                timeout=15,
                proxies={"http": None, "https": None},  # 프록시 우회
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("result") is True:
                token = data["data"]["accessToken"]
                logger.info("✅ 토큰 발급 성공")
                return token
            else:
                # API 레벨 오류 (HTTP 200이지만 result: false)
                logger.error(f"❌ 토큰 발급 API 오류: {data.get('message')} (code: {data.get('code')})")
                return None  # 재시도 불필요한 인증 오류

        except requests.RequestException as e:
            logger.error(f"❌ 토큰 발급 네트워크 오류 (시도 {attempt}): {e}")
            if attempt < CONFIG["RETRY_COUNT"]:
                random_delay(5, 10)

    return None


# ──────────────────────────────────────────────
# 상품 목록 단일 페이지 조회
# ──────────────────────────────────────────────
def fetch_product_page(
    session: requests.Session,
    token: str,
    category: str,
    page: int,
) -> list[dict]:
    """
    CJ 상품 목록 API 단일 페이지 조회
    GET /product/list?pageNum={page}&pageSize=50&categoryKeyword={category}
    실패 시 빈 리스트 반환
    """
    headers = {"CJ-Access-Token": token}
    params  = {
        "pageNum":         page,
        "pageSize":        CONFIG["PAGE_SIZE"],
        "productNameEn": category,
    }

    # 랜덤 딜레이 (서버 부하 방지 + IP 차단 우회)
    random_delay()

    try:
        resp = session.get(
            CONFIG["CJ_PRODUCT_URL"],
            headers=headers,
            params=params,
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("result") is True:
            products = data.get("data", {}).get("list", [])
            logger.info(f"   └─ 페이지 {page}: {len(products)}개 수신")
            return products
        else:
            logger.warning(f"⚠️ 상품 조회 API 오류: {data.get('message')}")
            return []

    except requests.RequestException as e:
        logger.error(f"❌ 상품 조회 네트워크 오류 (카테고리: {category}, 페이지: {page}): {e}")
        return []


# ──────────────────────────────────────────────
# 배송비 조회 (US → US)
# ──────────────────────────────────────────────
def fetch_shipping_cost(
    session: requests.Session,
    token: str,
    product_sku: str,
) -> float:
    """
    미국 내 배송비 조회 (US 창고 → US 고객)
    조회 실패 시 기본값 $4.99 반환 (안전 마진 계산 보장)
    """
    if not product_sku:
        return 4.99

    headers = {"CJ-Access-Token": token}
    payload = {
        "startCountryCode": "US",
        "endCountryCode":   "US",
        "products": [{"skuId": product_sku, "quantity": 1}],
    }

    # 배송비 API 딜레이 (짧게)
    time.sleep(random.uniform(1, 3))

    try:
        resp = session.post(CONFIG["CJ_SHIP_URL"], headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if data.get("result") is True:
            logistics = data.get("data", [])
            if logistics:
                # 가장 저렴한 배송 옵션 선택
                cheapest = min(logistics, key=lambda x: float(x.get("logisticPrice", 999)))
                return float(cheapest.get("logisticPrice", 4.99))

    except Exception:
        pass  # 실패 시 기본값 사용

    return 4.99


# ──────────────────────────────────────────────
# 마진율 계산 (spocket_crawler.py calc_margin과 동일 구조)
# ──────────────────────────────────────────────
def calc_margin(sourcing: float, retail: float) -> float:
    """마진율 계산: (정가 - 소싱가) / 정가 * 100"""
    if retail <= 0:
        return 0.0
    return round((retail - sourcing) / retail * 100, 2)


# ──────────────────────────────────────────────
# 단일 상품 파싱 + 필터 적용
# ──────────────────────────────────────────────
def process_product(
    raw: dict,
    session: requests.Session,
    token: str,
    category: str,
) -> Optional[dict]:
    """
    CJ API 응답 단일 상품 dict → 수집 항목 변환 + 필터 적용
    조건 미달 시 None 반환
    """
    try:
       # 범위형 가격 처리 ("28.63 -- 31.81" → 28.63)
        raw_price = str(raw.get("sellPrice") or "0")
        if "--" in raw_price:
            raw_price = raw_price.split("--")[0].strip()
        sell_price = float(raw_price)
        suggest_price = float(raw.get("suggestSellingPrice") or 0)
        inventory     = int(raw.get("inventory") or 0)
        product_id    = raw.get("pid", "")
        product_sku   = raw.get("productSku", "")

        # ── 소싱가 범위 필터
        if not (CONFIG["PRICE_MIN"] <= sell_price <= CONFIG["PRICE_MAX"]):
            return None

        # # ── 재고 필터
        # if inventory <= 0:
        #     return None

        # ── 정가: suggestSellingPrice 없으면 sellPrice * 2.5
        if suggest_price <= 0:
            suggest_price = round(sell_price * 2.5, 2)

        # ── US 창고 여부 확인
        ship_from_us = any(
            "US" in str(w.get("countryCode", "")).upper()
            for w in raw.get("sourceWarehouse", [])
        )

        # ── 배송비 조회
        ship_cost = fetch_shipping_cost(session, token, product_sku)

        # ── 마진율 계산 (배송비 포함)
        # 실제 마진 = 정가 - 소싱가 - 배송비
        effective_cost = sell_price + ship_cost
        margin_pct = calc_margin(effective_cost, suggest_price)

        # ── 마진율 필터
        if margin_pct < CONFIG["MARGIN_MIN_PCT"]:
            return None

        # ── 상품 URL
        product_url = f"https://app.cjdropshipping.com/product-detail.html?pid={product_id}"

        return {
            "상품명":           raw.get("productNameEn", "N/A"),
            "카테고리":         category,
            "소싱가($)":        sell_price,
            "정가($)":          suggest_price,
            "배송비($)":        ship_cost,
            "마진율(%)":        margin_pct,
            "재고":             inventory,
            "US창고":           "✅" if ship_from_us else "❌",
            "상품URL":          product_url,
            "수집시각(KST)":    datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        }

    except Exception as e:
        logger.debug(f"상품 파싱 건너뜀 (pid={raw.get('pid')}): {e}")
        return None


# ──────────────────────────────────────────────
# 카테고리 단위 크롤링
# ──────────────────────────────────────────────
def crawl_category(
    session: requests.Session,
    token: str,
    category: str,
) -> list[dict]:
    """
    단일 카테고리 전체 페이지 수집
    재시도 3회 포함 (spocket_crawler.py crawl_keyword와 동일 구조)
    """
    results = []
    logger.info(f"\n{'─'*40}")
    logger.info(f"📦 카테고리 수집 시작: '{category}'")

    for page in range(1, CONFIG["MAX_PAGES"] + 1):
        raw_products = fetch_product_page(session, token, category, page)

        # 빈 페이지 → 마지막 페이지 도달
        if not raw_products:
            logger.info(f"   마지막 페이지 도달 (페이지 {page})")
            break

        for raw in raw_products:
            product = process_product(raw, session, token, category)
            if product:
                results.append(product)
                logger.info(
                    f"   ✅ {product['상품명'][:38]} | "
                    f"소싱가: ${product['소싱가($)']:.2f} | 마진율: {product['마진율(%)']}%"
                )

        # 페이지 간 추가 대기
        if page < CONFIG["MAX_PAGES"] and raw_products:
            random_delay(2, 5)

    logger.info(f"🏁 '{category}' 완료: {len(results)}개 위닝 후보")
    return results


# ──────────────────────────────────────────────
# 데이터 정합성 검증 (spocket_crawler.py와 동일 구조)
# ──────────────────────────────────────────────
def validate_data(data: list[dict]) -> list[dict]:
    """
    - 상품명 누락 제거
    - 가격 0/None 제거
    - URL 기준 중복 제거
    - 마진율 재계산 일치 검증
    """
    before    = len(data)
    seen_urls = set()
    valid     = []

    for row in data:
        if not row.get("상품명"):
            continue
        sp = row.get("소싱가($)") or 0
        rp = row.get("정가($)") or 0
        sc = row.get("배송비($)") or 0
        if sp <= 0 or rp <= 0:
            continue
        url = row.get("상품URL", "")
        if url in seen_urls:
            continue
        seen_urls.add(url)

        # 마진율 재검증 (배송비 포함 재계산)
        recalc = calc_margin(sp + sc, rp)
        if abs(recalc - (row.get("마진율(%)") or 0)) > 0.5:
            logger.warning(
                f"마진율 재계산 적용: {row['상품명'][:30]} "
                f"({row['마진율(%)']} → {recalc})"
            )
            row["마진율(%)"] = recalc

        valid.append(row)

    logger.info(f"🔍 정합성 검증: {before}개 → {len(valid)}개 (제거: {before - len(valid)}개)")
    return valid


# ──────────────────────────────────────────────
# Google Sheets 저장 (spocket_crawler.py와 동일 구조)
# ──────────────────────────────────────────────
def save_to_sheets(data: list[dict]) -> bool:
    """
    Google Sheets "CJ_위닝후보" 시트에 저장
    기존 데이터 초기화 후 마진율 높은 순 덮어쓰기
    재시도 3회 포함
    """
    if not data:
        logger.warning("⚠️ 저장할 데이터 없음")
        return False

    sa_path = CONFIG["SERVICE_ACCOUNT_FILE"]
    if not Path(sa_path).exists():
        logger.error(f"❌ service_account.json 없음: {sa_path}")
        return False

    headers = [
        "상품명", "카테고리", "소싱가($)", "정가($)", "배송비($)",
        "마진율(%)", "재고", "US창고", "상품URL", "수집시각(KST)",
    ]

    for attempt in range(1, CONFIG["RETRY_COUNT"] + 1):
        try:
            scopes = [
                "https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive",
            ]
            creds = Credentials.from_service_account_file(sa_path, scopes=scopes)
            gc    = gspread.authorize(creds)

            spreadsheet = gc.open_by_key(CONFIG["SHEET_ID"])
            try:
                worksheet = spreadsheet.worksheet(CONFIG["SHEET_NAME"])
            except gspread.WorksheetNotFound:
                worksheet = spreadsheet.add_worksheet(
                    title=CONFIG["SHEET_NAME"], rows=5000, cols=len(headers)
                )
                logger.info(f"📋 새 시트 생성: '{CONFIG['SHEET_NAME']}'")

            # 초기화 후 전체 덮어쓰기
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
                time.sleep(random.uniform(5, 10))

    return False


# ──────────────────────────────────────────────
# Discord 알림 (spocket_crawler.py send_discord와 동일 구조)
# ──────────────────────────────────────────────
def send_discord(products: list[dict], success: bool) -> None:
    """Discord 웹훅으로 수집 결과 전송. DISCORD_WEBHOOK_URL 미설정 시 건너뜀."""
    if not DISCORD_WEBHOOK_URL:
        logger.warning("⚠️ DISCORD_WEBHOOK_URL 미설정 — 알림 건너뜀")
        return

    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")

    if success and products:
        top5 = products[:5]
        top_text = "\n".join(
            f"  {i+1}. {p['상품명'][:30]} | 마진 {p['마진율(%)']}% | 소싱 ${p['소싱가($)']:.2f} | US창고: {p['US창고']}"
            for i, p in enumerate(top5)
        )
        msg = (
            f"✅ **CJ Dropshipping 위닝 후보 수집 완료** ({now})\n"
            f"총 {len(products)}개 (마진 {CONFIG['MARGIN_MIN_PCT']}%↑ / $"
            f"{CONFIG['PRICE_MIN']}~${CONFIG['PRICE_MAX']})\n\n"
            f"**🏆 TOP 5:**\n{top_text}\n\n"
            f"📊 Google Sheets `{CONFIG['SHEET_NAME']}` 저장 완료\n"
            f"⚠️ 크롤러 전용 IP 사용 중 | 판매 계정 IP와 분리됨"
        )
    else:
        msg = f"❌ **CJ 크롤러 실패/결과 없음** ({now}) — 로그 확인 필요"

    try:
        res = requests.post(DISCORD_WEBHOOK_URL, json={"content": msg}, timeout=10)
        if res.status_code in (200, 204):
            logger.info("✅ Discord 알림 전송 완료")
        else:
            logger.warning(f"Discord 응답 이상: {res.status_code}")
    except Exception as e:
        logger.error(f"Discord 알림 실패: {e}")


# ──────────────────────────────────────────────
# 메인 (spocket_crawler.py main과 동일 흐름)
# ──────────────────────────────────────────────
def main():
    logger.info("=" * 60)
    logger.info("🕷️  CJ Dropshipping 크롤러 v1.0 시작")
    logger.info(f"📋 카테고리: {CONFIG['CATEGORIES']}")
    logger.info(f"💰 소싱가 범위: ${CONFIG['PRICE_MIN']}~${CONFIG['PRICE_MAX']}")
    logger.info(f"📈 최소 마진율: {CONFIG['MARGIN_MIN_PCT']}%")
    logger.info(f"🕐 실행 시각: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')}")
    logger.info("=" * 60)

    # ── Proxy 검증 (spocket_crawler.py와 동일)
    logger.info("🔍 Proxy 검증 중...")
    proxy = get_valid_proxy()
    logger.info(f"🌐 사용 Proxy: {proxy['username']}@{PROXY_HOST}:{PROXY_PORT}")

    # ── requests 세션 생성
    session = build_session(proxy)

    # ── STEP 5: CJ API 토큰 발급 (재시도 3회 포함)
    token = None
    for attempt in range(1, CONFIG["RETRY_COUNT"] + 1):
        token = get_access_token(session)
        if token:
            break
        logger.warning(f"토큰 재시도 {attempt}/{CONFIG['RETRY_COUNT']}")
        random_delay(5, 10)

    if not token:
        logger.error("❌ CJ API 토큰 발급 3회 모두 실패 — 크롤러 중단")
        send_discord([], success=False)
        return

    # ── 카테고리별 수집
    all_data = []
    for category in CONFIG["CATEGORIES"]:
        try:
            results = crawl_category(session, token, category)
            all_data.extend(results)
        except Exception as e:
            logger.error(f"💀 '{category}' 카테고리 실패: {e}")

        # 카테고리 간 대기 (5~15초)
        if category != CONFIG["CATEGORIES"][-1]:
            random_delay(5, 15)

    logger.info(f"\n{'='*60}")
    logger.info(f"📊 전체 수집: {len(all_data)}개")

    # ── 정합성 검증 (spocket_crawler.py와 동일)
    validated = validate_data(all_data)

    # ── 마진율 높은 순 정렬
    validated.sort(key=lambda x: x.get("마진율(%)", 0), reverse=True)

    # ── Google Sheets 저장
    save_ok = False
    if validated:
        save_ok = save_to_sheets(validated)
    else:
        logger.warning("⚠️ 저장할 유효 데이터 없음")

    # ── Discord 알림
    send_discord(validated, success=save_ok)

    # ── 최종 요약
    logger.info(f"\n{'='*60}")
    logger.info(f"🏁 크롤러 종료 | 최종 저장: {len(validated)}개")
    if validated:
        logger.info("\n📋 TOP 10 위닝 후보 (마진율 순):")
        for i, p in enumerate(validated[:10], 1):
            logger.info(
                f"  {i:02d}. {p['상품명'][:42]:<42} "
                f"소싱가: ${p['소싱가($)']:>6.2f} | 마진율: {p['마진율(%)']:>6.1f}% | US창고: {p['US창고']}"
            )
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
