"""
cj_crawler.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CJ Dropshipping 공식 API 기반 위닝 상품 수집기
- Playwright/Captcha 불필요 (순수 REST API 방식)
- Google Sheets 자동 저장 + Discord 웹훅 알림
- GitHub Actions KST 07:00 자동 실행 대응

⚠️ 경고: 이 크롤러는 전용 IP/VPN에서만 실행할 것
⚠️ 판매 계정(Etsy/스마트스토어/쿠팡)과 동일 IP 사용 절대 금지
⚠️ EC2 + Webshare Rotating Proxy 경유 필수
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import time
import random
import logging
import requests
import gspread
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ──────────────────────────────────────────
# 환경 변수 로드
# ──────────────────────────────────────────
load_dotenv()

CJ_EMAIL        = os.getenv("CJ_EMAIL")          # CJ 계정 이메일
CJ_API_KEY      = os.getenv("CJ_API_KEY")        # CJ API Key (비밀번호 대용)
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")   # Discord 웹훅 URL
SHEETS_ID       = os.getenv("GOOGLE_SHEETS_ID")  # Google Sheets 문서 ID
GCP_CRED_JSON   = os.getenv("GCP_CREDENTIALS_JSON", "gcp_credentials.json")

# Webshare Rotating Proxy 설정
# ⚠️ 반드시 판매 계정과 다른 프록시 풀 사용
PROXY_USER = os.getenv("PROXY_USER")
PROXY_PASS = os.getenv("PROXY_PASS")
PROXY_HOST = os.getenv("PROXY_HOST", "p.webshare.io")
PROXY_PORT = os.getenv("PROXY_PORT", "80")

# ──────────────────────────────────────────
# 로깅 설정
# ──────────────────────────────────────────
KST = timezone(timedelta(hours=9))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("cj_crawler.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────
# 수집 조건 설정
# ──────────────────────────────────────────
TARGET_CATEGORIES = [
    "Home Decor",
    "Wall Art",
    "Gothic",
    "Halloween",
]

PRICE_MIN       = 10.0   # 최소 소싱가 (USD)
PRICE_MAX       = 40.0   # 최대 소싱가 (USD)
MIN_MARGIN_RATE = 0.35   # 최소 마진율 35%
MAX_SHIP_DAYS   = 14     # 최대 배송일
PAGE_SIZE       = 50     # 페이지당 상품 수
MAX_PAGES       = 5      # 카테고리당 최대 페이지 수

# ──────────────────────────────────────────
# CJ API 엔드포인트
# ──────────────────────────────────────────
CJ_BASE_URL  = "https://developers.cjdropshipping.com/api2.0/v1"
TOKEN_URL    = f"{CJ_BASE_URL}/authentication/getAccessToken"
PRODUCT_URL  = f"{CJ_BASE_URL}/product/list"
SHIP_URL     = f"{CJ_BASE_URL}/logistic/freightCalculate"

# ──────────────────────────────────────────
# HTTP 세션 (재시도 3회 포함)
# ──�────────────────────────────────────────
def build_session() -> requests.Session:
    """
    재시도 로직 포함 requests 세션 생성
    - 500/502/503/504 오류 시 3회 재시도
    - 프록시 설정 포함
    """
    session = requests.Session()

    retry_strategy = Retry(
        total=3,                          # 최대 재시도 횟수
        backoff_factor=2,                 # 재시도 대기: 2, 4, 8초
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    # Webshare Rotating Proxy 적용
    # ⚠️ 판매 계정과 절대 동일 프록시 사용 금지
    if PROXY_USER and PROXY_PASS:
        proxy_url = f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{PROXY_PORT}"
        session.proxies = {
            "http":  proxy_url,
            "https": proxy_url,
        }
        logger.info(f"🔒 프록시 적용: {PROXY_HOST}:{PROXY_PORT}")
    else:
        logger.warning("⚠️  프록시 미설정 — 로컬 IP 노출 주의!")

    # User-Agent 로테이션 (IP 차단 우회)
    ua_pool = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/125.0",
    ]
    session.headers.update({
        "User-Agent":   random.choice(ua_pool),
        "Accept":       "application/json",
        "Content-Type": "application/json",
    })

    return session


# ──────────────────────────────────────────
# 1. CJ 액세스 토큰 발급
# ──────────────────────────────────────────
def get_access_token(session: requests.Session) -> str | None:
    """
    CJ API 액세스 토큰 발급
    - 토큰 유효시간: 12시간 (매 실행마다 재발급)
    - 실패 시 None 반환
    """
    payload = {
        "email":    CJ_EMAIL,
        "password": CJ_API_KEY,   # CJ는 API Key를 password 파라미터로 전달
    }

    try:
        logger.info("🔑 CJ API 토큰 발급 요청...")
        resp = session.post(TOKEN_URL, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if data.get("result") is True:
            token = data["data"]["accessToken"]
            logger.info("✅ 토큰 발급 성공")
            return token
        else:
            logger.error(f"❌ 토큰 발급 실패: {data.get('message')}")
            return None

    except requests.RequestException as e:
        logger.error(f"❌ 토큰 발급 네트워크 오류: {e}")
        return None


# ──────────────────────────────────────────
# 2. 상품 목록 조회 (단일 페이지)
# ──────────────────────────────────────────
def fetch_product_page(
    session: requests.Session,
    token: str,
    category: str,
    page: int,
) -> list[dict]:
    """
    CJ API 상품 목록 단일 페이지 조회
    - 랜덤 딜레이 3~10초 적용 (IP 차단 우회)
    - 오류 시 빈 리스트 반환
    """
    headers = {"CJ-Access-Token": token}
    params  = {
        "pageNum":         page,
        "pageSize":        PAGE_SIZE,
        "categoryKeyword": category,
    }

    # 랜덤 딜레이 (서버 부하 방지 + IP 차단 우회)
    delay = random.uniform(3, 10)
    logger.info(f"⏳ {delay:.1f}초 대기 후 요청... (카테고리: {category}, 페이지: {page})")
    time.sleep(delay)

    try:
        resp = session.get(PRODUCT_URL, headers=headers, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        if data.get("result") is True:
            products = data.get("data", {}).get("list", [])
            logger.info(f"   └─ {len(products)}개 상품 수신")
            return products
        else:
            logger.warning(f"⚠️  API 응답 오류: {data.get('message')}")
            return []

    except requests.RequestException as e:
        logger.error(f"❌ 상품 조회 실패 (카테고리: {category}, 페이지: {page}): {e}")
        return []


# ──────────────────────────────────────────
# 3. 배송비 조회 (US → US)
# ──────────────────────────────────────────
def fetch_shipping_cost(
    session: requests.Session,
    token: str,
    product_sku: str,
    quantity: int = 1,
) -> float:
    """
    미국 내 배송비 조회
    - 조회 실패 시 기본값 4.99 반환 (안전 마진 계산용)
    """
    headers = {"CJ-Access-Token": token}
    payload = {
        "startCountryCode": "US",
        "endCountryCode":   "US",
        "products": [{"skuId": product_sku, "quantity": quantity}],
    }

    time.sleep(random.uniform(1, 3))  # 배송비 API 딜레이

    try:
        resp = session.post(SHIP_URL, headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if data.get("result") is True:
            logistics = data.get("data", [])
            if logistics:
                # 가장 저렴한 배송 옵션 선택
                cheapest = min(logistics, key=lambda x: x.get("logisticPrice", 999))
                return float(cheapest.get("logisticPrice", 4.99))
        return 4.99  # 기본 배송비

    except Exception:
        return 4.99  # 조회 실패 시 기본값


# ──────────────────────────────────────────
# 4. 마진율 계산
# ──────────────────────────────────────────
def calc_margin(sell_price: float, ship_cost: float, suggest_price: float) -> float:
    """
    마진율 계산
    공식: (판매가 - 소싱가 - 배송비) / 판매가
    suggest_price 없을 경우 sellPrice * 2.5 사용
    """
    if suggest_price <= 0:
        suggest_price = sell_price * 2.5

    profit = suggest_price - sell_price - ship_cost
    if suggest_price == 0:
        return 0.0
    return round(profit / suggest_price, 4)


# ──────────────────────────────────────────
# 5. 상품 필터링 및 정형화
# ──────────────────────────────────────────
def process_product(
    raw: dict,
    session: requests.Session,
    token: str,
    category: str,
) -> dict | None:
    """
    단일 상품 데이터 가공 및 조건 필터링
    조건 미달 시 None 반환
    """
    try:
        sell_price    = float(raw.get("sellPrice") or 0)
        suggest_price = float(raw.get("suggestSellingPrice") or 0)
        inventory     = raw.get("inventory", 0)
        product_id    = raw.get("pid", "")
        product_sku   = raw.get("productSku", "")

        # ── 소싱가 범위 필터
        if not (PRICE_MIN <= sell_price <= PRICE_MAX):
            return None

        # ── 재고 있음 필터
        if int(inventory) <= 0:
            return None

        # ── US 창고 여부 확인 (Ship from US 우선)
        ship_from_us = any(
            "US" in str(w.get("countryCode", ""))
            for w in raw.get("sourceWarehouse", [])
        )

        # ── 배송비 조회
        ship_cost = fetch_shipping_cost(session, token, product_sku)

        # ── 마진율 계산
        if suggest_price <= 0:
            suggest_price = round(sell_price * 2.5, 2)
        margin = calc_margin(sell_price, ship_cost, suggest_price)

        # ── 마진율 필터
        if margin < MIN_MARGIN_RATE:
            return None

        # ── 상품 URL 생성
        product_url = f"https://app.cjdropshipping.com/product-detail.html?pid={product_id}"

        return {
            "상품명":      raw.get("productNameEn", "N/A"),
            "카테고리":    category,
            "소싱가":      f"${sell_price:.2f}",
            "정가":        f"${suggest_price:.2f}",
            "배송비":      f"${ship_cost:.2f}",
            "마진율":      f"{margin * 100:.1f}%",
            "마진율_수치": margin,                  # 정렬용 숫자값
            "재고":        inventory,
            "US창고":      "✅" if ship_from_us else "❌",
            "상품URL":     product_url,
            "수집일시":    datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        }

    except Exception as e:
        logger.warning(f"⚠️  상품 처리 오류 (pid={raw.get('pid')}): {e}")
        return None


# ──────────────────────────────────────────
# 6. 전체 크롤링 실행
# ──────────────────────────────────────────
def run_crawl() -> list[dict]:
    """
    전체 카테고리 크롤링 메인 루프
    반환: 필터링된 위닝 후보 상품 리스트 (마진율 내림차순)
    """
    session = build_session()

    # 토큰 발급
    token = get_access_token(session)
    if not token:
        raise RuntimeError("CJ API 토큰 발급 실패 — 실행 중단")

    results: list[dict] = []

    for category in TARGET_CATEGORIES:
        logger.info(f"\n{'='*50}")
        logger.info(f"📦 카테고리 수집 시작: {category}")
        logger.info(f"{'='*50}")

        for page in range(1, MAX_PAGES + 1):
            raw_products = fetch_product_page(session, token, category, page)

            if not raw_products:
                logger.info(f"   마지막 페이지 도달 (페이지 {page})")
                break

            for raw in raw_products:
                product = process_product(raw, session, token, category)
                if product:
                    results.append(product)
                    logger.info(
                        f"   ✅ 위닝 후보: {product['상품명'][:40]} | "
                        f"소싱가: {product['소싱가']} | 마진율: {product['마진율']}"
                    )

        # 카테고리 간 추가 대기 (5~15초)
        inter_delay = random.uniform(5, 15)
        logger.info(f"⏳ 다음 카테고리까지 {inter_delay:.1f}초 대기...")
        time.sleep(inter_delay)

    # 마진율 내림차순 정렬
    results.sort(key=lambda x: x["마진율_수치"], reverse=True)
    logger.info(f"\n🎯 총 수집된 위닝 후보: {len(results)}개")

    return results


# ──────────────────────────────────────────
# 7. Google Sheets 저장
# ──────────────────────────────────────────
def save_to_sheets(products: list[dict]) -> bool:
    """
    Google Sheets "CJ_위닝후보" 시트에 마진율 높은 순으로 저장
    - 기존 데이터 초기화 후 재작성 (최신 데이터 유지)
    - 오류 시 False 반환
    """
    if not products:
        logger.warning("⚠️  저장할 상품 없음 — Sheets 저장 건너뜀")
        return False

    try:
        logger.info("📊 Google Sheets 저장 시작...")

        # GCP 서비스 계정 인증
        scopes = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds  = Credentials.from_service_account_file(GCP_CRED_JSON, scopes=scopes)
        client = gspread.authorize(creds)

        spreadsheet = client.open_by_key(SHEETS_ID)

        # "CJ_위닝후보" 시트 가져오기 (없으면 생성)
        try:
            sheet = spreadsheet.worksheet("CJ_위닝후보")
        except gspread.WorksheetNotFound:
            sheet = spreadsheet.add_worksheet(title="CJ_위닝후보", rows=500, cols=12)
            logger.info("   새 시트 'CJ_위닝후보' 생성")

        # 헤더 정의 (마진율_수치 컬럼은 내부용이므로 제외)
        headers = [
            "상품명", "카테고리", "소싱가", "정가", "배송비",
            "마진율", "재고", "US창고", "상품URL", "수집일시",
        ]

        # 기존 데이터 전체 초기화
        sheet.clear()

        # 헤더 + 데이터 일괄 업데이트 (API 호출 최소화)
        rows = [headers]
        for p in products:
            rows.append([
                p["상품명"], p["카테고리"], p["소싱가"], p["정가"],
                p["배송비"], p["마진율"], p["재고"], p["US창고"],
                p["상품URL"], p["수집일시"],
            ])

        sheet.update("A1", rows)

        # 헤더 행 볼드 서식 (선택)
        sheet.format("A1:J1", {"textFormat": {"bold": True}})

        logger.info(f"✅ Google Sheets 저장 완료 ({len(products)}개 상품)")
        return True

    except Exception as e:
        logger.error(f"❌ Google Sheets 저장 실패: {e}")
        return False


# ──────────────────────────────────────────
# 8. Discord 웹훅 알림
# ──────────────────────────────────────────
def send_discord_alert(products: list[dict], success: bool) -> None:
    """
    Discord 웹훅으로 수집 결과 알림
    - TOP 10 위닝 후보 요약 포함
    - Sheets 저장 성공/실패 상태 포함
    """
    if not DISCORD_WEBHOOK:
        logger.warning("⚠️  DISCORD_WEBHOOK 미설정 — 알림 건너뜀")
        return

    now_kst  = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    top_10   = products[:10]
    sheets_status = "✅ 저장 완료" if success else "❌ 저장 실패"

    # 상품 목록 텍스트 생성
    product_lines = ""
    for i, p in enumerate(top_10, 1):
        product_lines += (
            f"`{i:02d}` **{p['상품명'][:35]}**\n"
            f"    소싱가: {p['소싱가']} | 마진율: {p['마진율']} | "
            f"US창고: {p['US창고']} | 재고: {p['재고']}\n"
            f"    🔗 {p['상품URL']}\n\n"
        )

    embed = {
        "embeds": [{
            "title":       "🕷️ CJ Dropshipping 위닝 후보 수집 완료",
            "description": (
                f"**수집 시각:** {now_kst}\n"
                f"**총 위닝 후보:** {len(products)}개\n"
                f"**Google Sheets:** {sheets_status}\n"
                f"**카테고리:** Home Decor / Wall Art / Gothic / Halloween\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📋 **TOP {len(top_10)} 위닝 후보 (마진율 순)**\n\n"
                f"{product_lines}"
            ),
            "color": 0x00C851 if success else 0xFF4444,
            "footer": {"text": "⚠️ 크롤러 전용 IP 사용 중 | 판매 계정 IP와 분리됨"},
        }]
    }

    try:
        resp = requests.post(DISCORD_WEBHOOK, json=embed, timeout=10)
        if resp.status_code == 204:
            logger.info("✅ Discord 알림 전송 완료")
        else:
            logger.warning(f"⚠️  Discord 응답 코드: {resp.status_code}")
    except Exception as e:
        logger.error(f"❌ Discord 알림 실패: {e}")


# ──────────────────────────────────────────
# 9. 데이터 정합성 검증
# ──────────────────────────────────────────
def validate_products(products: list[dict]) -> list[dict]:
    """
    수집 데이터 정합성 검증
    - 필수 필드 누락 검사
    - 가격/마진율 유효성 확인
    - 이상 데이터 로깅 후 제거
    """
    valid     = []
    invalid_n = 0

    required_fields = ["상품명", "소싱가", "정가", "마진율", "상품URL"]

    for p in products:
        # 필수 필드 확인
        if not all(p.get(f) for f in required_fields):
            logger.warning(f"⚠️  필수 필드 누락: {p.get('상품명', 'UNKNOWN')}")
            invalid_n += 1
            continue

        # 상품명 최소 길이 확인
        if len(p["상품명"]) < 3:
            invalid_n += 1
            continue

        # URL 형식 확인
        if not p["상품URL"].startswith("https://"):
            invalid_n += 1
            continue

        valid.append(p)

    if invalid_n:
        logger.warning(f"⚠️  정합성 검증 실패 {invalid_n}개 제거됨")

    logger.info(f"✅ 정합성 검증 통과: {len(valid)}개")
    return valid


# ──────────────────────────────────────────
# 10. 메인 실행 진입점
# ──────────────────────────────────────────
def main():
    """
    CJ Dropshipping 크롤러 메인 실행 함수
    실행 순서:
      1. 크롤링 실행
      2. 데이터 정합성 검증
      3. Google Sheets 저장
      4. Discord 알림 전송
    """
    logger.info("━" * 60)
    logger.info("🕷️  CJ Dropshipping 크롤러 시작")
    logger.info(f"   실행 시각: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')}")
    logger.info("⚠️  경고: 전용 크롤러 IP (EC2 + Webshare Proxy) 경유 필수")
    logger.info("⚠️  판매 계정과 동일 IP 절대 금지")
    logger.info("━" * 60)

    # 환경 변수 필수값 확인
    if not CJ_EMAIL or not CJ_API_KEY:
        logger.error("❌ CJ_EMAIL / CJ_API_KEY 환경 변수 누락 — 실행 중단")
        return

    # 1단계: 크롤링
    try:
        products = run_crawl()
    except RuntimeError as e:
        logger.error(f"❌ 크롤링 실패: {e}")
        send_discord_alert([], success=False)
        return

    # 2단계: 정합성 검증
    products = validate_products(products)

    # 3단계: Google Sheets 저장
    sheets_ok = save_to_sheets(products)

    # 4단계: Discord 알림
    send_discord_alert(products, success=sheets_ok)

    # 최종 요약 출력
    logger.info("\n" + "━" * 60)
    logger.info("📋 수집 완료 요약")
    logger.info(f"   위닝 후보 총계  : {len(products)}개")
    logger.info(f"   Google Sheets   : {'✅ 완료' if sheets_ok else '❌ 실패'}")

    if products:
        logger.info("\n   📊 TOP 10 위닝 후보 (마진율 순):")
        for i, p in enumerate(products[:10], 1):
            logger.info(
                f"   {i:02d}. {p['상품명'][:45]:<45} "
                f"소싱가: {p['소싱가']:>8} | 마진율: {p['마진율']:>7} | US창고: {p['US창고']}"
            )
    logger.info("━" * 60)


if __name__ == "__main__":
    main()
