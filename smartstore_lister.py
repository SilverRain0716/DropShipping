#!/usr/bin/env python3
"""
smartstore_lister.py — 네이버 스마트스토어 자동 리스팅 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
파이프라인:
  1. Google Sheets CJ_위닝후보 시트에서 상품 읽기
  2. CJ API /product/query 로 실제 이미지 URL 조회
  3. 네이버 커머스 API bcrypt 인증 토큰 발급
  4. /v2/products 로 상품 등록
  5. 결과 Discord 알림 + Sheets 상태 업데이트

필요 환경변수:
  NAVER_CLIENT_ID       - 네이버 커머스 API 앱 ID
  NAVER_CLIENT_SECRET   - 네이버 커머스 API 시크릿
  CJ_ACCESS_TOKEN       - CJ API 액세스 토큰 (cj_crawler.py와 공유)
  CJ_EMAIL              - CJ 계정 이메일 (토큰 재발급용)
  CJ_PASSWORD           - CJ 계정 비밀번호
  CJ_API_KEY            - CJ API Key
  SHEET_ID              - Google Sheets ID
  GOOGLE_SA_KEY_PATH    - GCP 서비스 계정 키 경로
  DISCORD_WEBHOOK_URL   - Discord 웹훅

⚠️ 인증 방식: bcrypt 해싱 (HMAC이 아님!)
⚠️ timestamp: 현재시각 - 3초 (서버 시각 오차 보정)
"""

import os
import sys
import time
import random
import logging
import json
import requests
import bcrypt
import pybase64
import gspread
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 설정
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
KST = timezone(timedelta(hours=9))

CONFIG = {
    # 네이버 커머스 API
    "NAVER_API_BASE":       "https://api.commerce.naver.com",
    "NAVER_TOKEN_URL":      "https://api.commerce.naver.com/external/v1/oauth2/token",

    # CJ API
    "CJ_TOKEN_URL":         "https://developers.cjdropshipping.com/api2.0/v1/authentication/getAccessToken",
    "CJ_PRODUCT_QUERY_URL": "https://developers.cjdropshipping.com/api2.0/v1/product/query",

    # Google Sheets
    "SHEET_ID":   os.environ.get("SHEET_ID", ""),
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

    # 리스팅 설정
    "MAX_LISTINGS_PER_RUN": 10,       # 1회 최대 등록 수
    "PRICE_MARKUP_KRW":     1400,     # USD → KRW 환율 (보수적 적용)
    "MARKUP_RATIO":         2.8,      # 소싱가 × 마크업 = 판매가
    "MIN_PRICE_KRW":        15000,    # 최소 판매가 (원)
    "DEFAULT_STOCK":        999,      # 재고 수량 (드롭쉬핑)

    # 네이버 카테고리 ID (홈데코 → 홈/리빙 > 인테리어 소품)
    "DEFAULT_CATEGORY_ID":  "50000803",  # 인테리어 소품

    # 딜레이
    "DELAY_MIN": 3,
    "DELAY_MAX": 8,
    "RETRY_COUNT": 3,
}

# 환경변수
NAVER_CLIENT_ID     = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")
CJ_EMAIL            = os.environ.get("CJ_EMAIL", "")
CJ_PASSWORD         = os.environ.get("CJ_PASSWORD", "")
CJ_API_KEY          = os.environ.get("CJ_API_KEY", "")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 로깅
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("smartstore_lister.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [1] 네이버 커머스 API 인증 — bcrypt 방식 (HMAC 아님!)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def get_naver_token() -> Optional[str]:
    """
    네이버 커머스 API 액세스 토큰 발급
    인증 방식: bcrypt(password=timestamp, salt=client_secret) → base64 인코딩
    timestamp: 현재 밀리초 - 3000 (서버 시각 오차 보정)
    """
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        logger.error("❌ NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 미설정")
        return None

    for attempt in range(1, CONFIG["RETRY_COUNT"] + 1):
        try:
            # timestamp: 3초 전 값 사용 (서버 시각 차이 보정)
            timestamp = str(int((time.time() - 3) * 1000))

            # bcrypt 해싱: password=timestamp, salt=client_secret
            password  = timestamp.encode("utf-8")
            salt      = NAVER_CLIENT_SECRET.encode("utf-8")
            hashed    = bcrypt.hashpw(password, salt)
            client_secret_sign = pybase64.standard_b64encode(hashed).decode("utf-8")

            # 디버그: 전송 값 로깅
            logger.info(f"   client_id: {NAVER_CLIENT_ID}")
            logger.info(f"   timestamp: {timestamp}")
            logger.info(f"   sign(앞30): {client_secret_sign[:30]}...")
            logger.info(f"   secret 앞5: {NAVER_CLIENT_SECRET[:5]}")

            # 토큰 요청
            resp = requests.post(
                CONFIG["NAVER_TOKEN_URL"],
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "client_id":          NAVER_CLIENT_ID,
                    "timestamp":          timestamp,
                    "client_secret_sign": client_secret_sign,
                    "grant_type":         "client_credentials",
                    "type":               "SELF",
                },
                timeout=15,
                proxies={"http": None, "https": None},
            )

            # ✅ 에러 응답 body를 먼저 로깅 (raise_for_status 전)
            if not resp.ok:
                logger.error(
                    f"❌ 네이버 API 오류 HTTP {resp.status_code}: {resp.text[:400]}"
                )
                if resp.status_code == 400:
                    return None  # 400은 재시도해도 동일 → 즉시 중단
                if attempt < CONFIG["RETRY_COUNT"]:
                    time.sleep(random.uniform(3, 7))
                continue

            data = resp.json()
            token = data.get("access_token")
            if token:
                expires_in = data.get("expires_in", 3600)
                logger.info(f"✅ 네이버 토큰 발급 성공 (유효: {expires_in}초)")
                return token
            else:
                logger.error(f"❌ 네이버 토큰 발급 실패: {data}")
                return None

        except requests.RequestException as e:
            logger.error(f"❌ 네이버 토큰 네트워크 오류 (시도 {attempt}): {e}")
            if attempt < CONFIG["RETRY_COUNT"]:
                time.sleep(random.uniform(3, 7))

    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [2] CJ API 토큰 발급
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def get_cj_token() -> Optional[str]:
    """CJ API 액세스 토큰 발급 (apiKey 방식)"""
    api_key = CJ_API_KEY
    if not api_key:
        logger.error("❌ CJ_API_KEY 미설정")
        return None

    payload = {"apiKey": api_key}

    for attempt in range(1, CONFIG["RETRY_COUNT"] + 1):
        try:
            resp = requests.post(
                CONFIG["CJ_TOKEN_URL"],
                json=payload,
                timeout=15,
                proxies={"http": None, "https": None},
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("result") is True:
                token = data["data"]["accessToken"]
                logger.info("✅ CJ 토큰 발급 성공")
                return token
            else:
                logger.error(f"❌ CJ 토큰 API 오류: {data.get('message')} (code: {data.get('code')})")
                return None

        except requests.RequestException as e:
            logger.error(f"❌ CJ 토큰 네트워크 오류 (시도 {attempt}): {e}")
            if attempt < CONFIG["RETRY_COUNT"]:
                time.sleep(random.uniform(3, 7))

    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [3] CJ 상품 이미지 URL 실조회 — 핵심 버그픽스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def get_cj_product_image(pid: str, cj_token: str) -> str:
    """
    CJ API /product/query?pid= 로 실제 productImage URL 조회
    
    ⚠️ 버그 원인: pid로 이미지 URL 추정 생성 → 실제 URL 형식과 불일치
    ✅ 해결: 이 함수로 실제 URL 직접 조회
    
    반환 우선순위:
    1. productImage (대표 이미지)
    2. productImageSet[0].imageUrl (이미지 세트 첫 번째)
    3. "" (조회 실패 시)
    """
    if not pid or not cj_token:
        return ""

    try:
        resp = requests.get(
            CONFIG["CJ_PRODUCT_QUERY_URL"],
            headers={"CJ-Access-Token": cj_token},
            params={"pid": pid},
            timeout=15,
            proxies={"http": None, "https": None},
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("result") is True and data.get("data"):
            product = data["data"]

            # 1순위: productImage
            image_url = product.get("productImage", "")
            if image_url and image_url.startswith("http"):
                logger.debug(f"   이미지 URL 조회 성공 (productImage): {image_url[:60]}")
                return image_url

            # 2순위: productImageSet 첫 번째
            image_set = product.get("productImageSet", [])
            if image_set and isinstance(image_set, list):
                first = image_set[0]
                url = first.get("imageUrl", "") if isinstance(first, dict) else ""
                if url and url.startswith("http"):
                    logger.debug(f"   이미지 URL 조회 성공 (imageSet[0]): {url[:60]}")
                    return url

            logger.warning(f"   ⚠️ pid={pid} 이미지 필드 없음. 응답: {list(product.keys())}")
            return ""

        else:
            logger.warning(f"   ⚠️ CJ product/query 실패 pid={pid}: {data.get('message')}")
            return ""

    except Exception as e:
        logger.warning(f"   ⚠️ CJ 이미지 조회 예외 pid={pid}: {e}")
        return ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [4] Google Sheets에서 위닝후보 읽기
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def load_candidates_from_sheets() -> list[dict]:
    """
    Google Sheets CJ_위닝후보 시트에서 상품 목록 읽기
    리스팅 상태가 '등록완료'인 항목은 건너뜀
    """
    sa_path = CONFIG["SERVICE_ACCOUNT_FILE"]
    if not Path(sa_path).exists():
        logger.error(f"❌ service_account.json 없음: {sa_path}")
        return []

    try:
        scopes = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds       = Credentials.from_service_account_file(sa_path, scopes=scopes)
        gc          = gspread.authorize(creds)
        spreadsheet = gc.open_by_key(CONFIG["SHEET_ID"])
        worksheet   = spreadsheet.worksheet(CONFIG["SHEET_NAME"])

        all_rows = worksheet.get_all_records()
        logger.info(f"📊 Sheets에서 {len(all_rows)}개 로드")

        # 아직 등록 안 된 것만 필터 (리스팅상태 컬럼이 없거나 비어있는 것)
        candidates = [
            row for row in all_rows
            if row.get("리스팅상태", "") not in ("등록완료", "등록중", "스킵")
        ]
        logger.info(f"   → 등록 대상: {len(candidates)}개")
        return candidates

    except gspread.WorksheetNotFound:
        logger.error(f"❌ 시트 '{CONFIG['SHEET_NAME']}' 없음")
        return []
    except Exception as e:
        logger.error(f"❌ Sheets 읽기 실패: {e}")
        return []


def update_listing_status(row_index: int, status: str, product_id: str = "") -> None:
    """Sheets에 리스팅 상태 업데이트"""
    sa_path = CONFIG["SERVICE_ACCOUNT_FILE"]
    if not Path(sa_path).exists():
        return

    try:
        scopes      = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds       = Credentials.from_service_account_file(sa_path, scopes=scopes)
        gc          = gspread.authorize(creds)
        spreadsheet = gc.open_by_key(CONFIG["SHEET_ID"])
        worksheet   = spreadsheet.worksheet(CONFIG["SHEET_NAME"])

        # 헤더 확인 후 리스팅상태 컬럼 위치 찾기
        headers = worksheet.row_values(1)
        if "리스팅상태" not in headers:
            worksheet.update_cell(1, len(headers) + 1, "리스팅상태")
            worksheet.update_cell(1, len(headers) + 2, "스마트스토어ID")
            headers = worksheet.row_values(1)

        status_col = headers.index("리스팅상태") + 1
        id_col     = headers.index("스마트스토어ID") + 1 if "스마트스토어ID" in headers else status_col + 1

        # row_index는 데이터 행 기준 (헤더=1, 첫 데이터=2)
        actual_row = row_index + 2
        worksheet.update_cell(actual_row, status_col, status)
        if product_id:
            worksheet.update_cell(actual_row, id_col, product_id)

    except Exception as e:
        logger.warning(f"⚠️ Sheets 상태 업데이트 실패 (행 {row_index}): {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [5] 가격 계산 및 상품명 생성
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def calc_sale_price_krw(sourcing_usd: float) -> int:
    """
    소싱가(USD) → 판매가(KRW)
    공식: 소싱가 × 환율 × 마크업, 최소 MIN_PRICE_KRW 보장
    100원 단위 올림
    """
    raw = sourcing_usd * CONFIG["PRICE_MARKUP_KRW"] * CONFIG["MARKUP_RATIO"]
    price = max(raw, CONFIG["MIN_PRICE_KRW"])
    # 100원 단위 올림
    return int((price + 99) // 100 * 100)


def make_product_name(en_name: str, category: str) -> str:
    """
    영문 상품명 → 스마트스토어용 한국어 혼합 상품명
    최대 100자 제한
    """
    # 카테고리별 접두어 맵핑
    prefix_map = {
        "home decor":  "홈데코 인테리어",
        "wall art":    "월아트 벽장식",
        "gothic":      "고딕 인테리어",
        "halloween":   "할로윈 장식",
    }
    prefix = prefix_map.get(category.lower(), "인테리어 소품")

    # 상품명 정리 (특수문자 제거)
    clean_name = en_name.replace('"', '').replace("'", "").strip()
    full_name  = f"{prefix} {clean_name}"

    # 100자 초과 시 자르기
    return full_name[:100]


def make_detail_content(product_name: str, sourcing_usd: float, category: str) -> str:
    """상품 상세 HTML 설명 생성"""
    return f"""
<div style="text-align:center; font-family: Arial, sans-serif; padding: 20px;">
  <h2 style="color:#333;">{product_name}</h2>
  <p style="color:#666; font-size:14px;">
    고품질 {category} 제품입니다.<br>
    주문 후 7~14일 내 배송됩니다.<br>
    상품 관련 문의는 고객센터를 이용해 주세요.
  </p>
  <hr/>
  <p style="color:#999; font-size:12px;">
    ※ 상품 이미지는 실제와 다소 차이가 있을 수 있습니다.<br>
    ※ 해외 소싱 상품으로 배송 기간이 다소 소요될 수 있습니다.
  </p>
</div>
""".strip()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [6] pid 추출 (상품URL에서)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def extract_pid_from_url(product_url: str) -> str:
    """
    상품URL: https://app.cjdropshipping.com/product-detail.html?pid=XXXX
    → XXXX 추출
    """
    if not product_url:
        return ""
    if "pid=" in product_url:
        return product_url.split("pid=")[-1].strip()
    return ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [7] 스마트스토어 상품 등록 — 핵심 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def register_product(
    naver_token: str,
    product_name: str,
    sale_price: int,
    image_url: str,
    detail_html: str,
    category_id: str = None,
) -> Optional[str]:
    """
    네이버 커머스 API POST /v2/products 로 상품 등록
    성공 시 smartstoreProductId 반환, 실패 시 None

    ✅ 필수 필드 전부 포함:
    - smartstoreChannelProduct (네이버쇼핑 등록)
    - afterServiceInfo (A/S 정보)
    - minorPurchasable (미성년자 구매 가능)
    - originAreaInfo (원산지)
    """
    cat_id = category_id or CONFIG["DEFAULT_CATEGORY_ID"]

    payload = {
        "originProduct": {
            "statusType":      "SALE",
            "leafCategoryId":  cat_id,
            "name":            product_name,
            "detailContent":   detail_html,
            "images": {
                "representativeImage": {"url": image_url}
            },
            "salePrice":       sale_price,
            "stockQuantity":   CONFIG["DEFAULT_STOCK"],
            "deliveryInfo": {
                "deliveryType":              "DELIVERY",
                "deliveryAttributeType":     "NORMAL",
                "deliveryFee": {
                    "deliveryFeeType":       "FREE",
                },
                "claimDeliveryInfo": {
                    "returnDeliveryFee":     5000,
                    "exchangeDeliveryFee":   5000,
                    "shippingAddressId":     0,
                    "returnAddressId":       0,
                },
            },
            "detailAttribute": {
                "afterServiceInfo": {
                    "afterServiceTelephoneNumber": "1588-1234",
                    "afterServiceGuideContent":    "고객센터로 문의해 주세요.",
                },
                "originAreaInfo": {
                    "originAreaCode": "03",   # 03 = 중국
                    "content":        "중국",
                },
                "minorPurchasable": True,
                "naverShoppingSearchInfo": {
                    "modelInfo": {
                        "modelName": product_name[:40],
                    }
                },
            },
        },
        "smartstoreChannelProduct": {
            "naverShoppingRegistration":          True,
            "channelProductDisplayStatusType":    "ON",
        },
    }

    url     = f"{CONFIG['NAVER_API_BASE']}/external/v2/products"
    headers = {
        "Authorization": f"Bearer {naver_token}",
        "Content-Type":  "application/json",
    }

    for attempt in range(1, CONFIG["RETRY_COUNT"] + 1):
        try:
            resp = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=30,
                proxies={"http": None, "https": None},
            )

            # 성공: 200 또는 201
            if resp.status_code in (200, 201):
                data = resp.json()
                # 응답에서 상품 ID 추출
                product_id = (
                    str(data.get("smartstoreProductNo", ""))
                    or str(data.get("originProductNo", ""))
                    or str(data.get("id", ""))
                )
                logger.info(f"   ✅ 등록 성공! 스마트스토어 상품ID: {product_id}")
                return product_id or "success"

            # 실패
            try:
                err = resp.json()
            except Exception:
                err = resp.text

            logger.error(
                f"   ❌ 등록 실패 (시도 {attempt}, HTTP {resp.status_code}): "
                f"{json.dumps(err, ensure_ascii=False)[:300]}"
            )

            # 재시도 불필요한 오류 (400 Bad Request)
            if resp.status_code == 400:
                logger.error("   → 400 오류: payload 필드 문제, 재시도 없음")
                return None

        except requests.RequestException as e:
            logger.error(f"   ❌ 네트워크 오류 (시도 {attempt}): {e}")

        if attempt < CONFIG["RETRY_COUNT"]:
            time.sleep(random.uniform(5, 10))

    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [8] Discord 알림
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def send_discord(results: list[dict], success_count: int, fail_count: int) -> None:
    """리스팅 결과 Discord 알림"""
    if not DISCORD_WEBHOOK_URL:
        return

    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    status_emoji = "✅" if success_count > 0 else "❌"

    lines = [
        f"{status_emoji} **스마트스토어 자동 리스팅 완료** ({now})",
        f"성공: {success_count}개 | 실패/스킵: {fail_count}개",
        "",
    ]

    if results:
        lines.append("**등록된 상품:**")
        for r in results[:5]:
            lines.append(f"  • {r['name'][:35]} | {r['price']:,}원 | ID: {r.get('id','?')}")

    msg = "\n".join(lines)

    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json={"content": msg}, timeout=10)
        if resp.status_code in (200, 204):
            logger.info("✅ Discord 알림 전송 완료")
    except Exception as e:
        logger.warning(f"⚠️ Discord 알림 실패: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [9] 메인 파이프라인
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def main():
    logger.info("=" * 60)
    logger.info("🛒 스마트스토어 자동 리스팅 v1.0 시작")
    logger.info(f"🕐 실행 시각: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')}")
    logger.info(f"🎯 최대 등록 수: {CONFIG['MAX_LISTINGS_PER_RUN']}개")
    logger.info("=" * 60)

    # ── STEP 1: 네이버 토큰 발급
    logger.info("\n[STEP 1] 네이버 커머스 API 토큰 발급...")
    naver_token = get_naver_token()
    if not naver_token:
        logger.error("❌ 네이버 토큰 발급 실패 — 중단")
        send_discord([], 0, 0)
        sys.exit(1)

    # ── STEP 2: CJ 토큰 발급
    logger.info("\n[STEP 2] CJ API 토큰 발급...")
    cj_token = get_cj_token()
    if not cj_token:
        logger.error("❌ CJ 토큰 발급 실패 — 이미지 조회 불가, 중단")
        sys.exit(1)

    # ── STEP 3: Sheets에서 위닝후보 로드
    logger.info("\n[STEP 3] Google Sheets에서 위닝후보 로드...")
    candidates = load_candidates_from_sheets()
    if not candidates:
        logger.warning("⚠️ 등록할 상품 없음 — 종료")
        send_discord([], 0, 0)
        return

    # 최대 등록 수 제한
    targets = candidates[:CONFIG["MAX_LISTINGS_PER_RUN"]]
    logger.info(f"📦 이번 실행 등록 대상: {len(targets)}개")

    # ── STEP 4: 상품별 등록
    success_list = []
    fail_count   = 0

    for i, row in enumerate(targets):
        product_url = row.get("상품URL", "")
        product_name_en = row.get("상품명", "")
        category    = row.get("카테고리", "home decor")
        sourcing_usd = float(row.get("소싱가($)") or 0)

        logger.info(f"\n{'─'*40}")
        logger.info(f"[{i+1}/{len(targets)}] {product_name_en[:50]}")

        if not product_name_en or sourcing_usd <= 0:
            logger.warning("   ⚠️ 상품명/가격 누락 → 스킵")
            fail_count += 1
            update_listing_status(i, "스킵")
            continue

        # pid 추출
        pid = extract_pid_from_url(product_url)
        if not pid:
            logger.warning(f"   ⚠️ pid 추출 실패 (URL: {product_url}) → 스킵")
            fail_count += 1
            update_listing_status(i, "스킵")
            continue

        # ── [핵심] CJ 이미지 URL 실조회
        logger.info(f"   🖼️  CJ 이미지 URL 조회 중... (pid={pid})")
        image_url = get_cj_product_image(pid, cj_token)

        if not image_url:
            logger.warning(f"   ⚠️ 이미지 URL 없음 pid={pid} → 스킵")
            fail_count += 1
            update_listing_status(i, "이미지없음")
            continue

        logger.info(f"   ✅ 이미지 URL: {image_url[:70]}...")

        # 판매가 계산
        sale_price = calc_sale_price_krw(sourcing_usd)
        logger.info(f"   💰 판매가: {sale_price:,}원 (소싱가 ${sourcing_usd:.2f})")

        # 상품명 생성
        smart_name   = make_product_name(product_name_en, category)
        detail_html  = make_detail_content(smart_name, sourcing_usd, category)
        logger.info(f"   📝 등록명: {smart_name}")

        # 등록 실행
        logger.info("   📤 스마트스토어 등록 중...")
        update_listing_status(i, "등록중")

        product_id = register_product(
            naver_token=naver_token,
            product_name=smart_name,
            sale_price=sale_price,
            image_url=image_url,
            detail_html=detail_html,
        )

        if product_id:
            success_list.append({
                "name":  smart_name,
                "price": sale_price,
                "id":    product_id,
            })
            update_listing_status(i, "등록완료", product_id)
            logger.info(f"   ✅ 등록 완료 → {product_id}")
        else:
            fail_count += 1
            update_listing_status(i, "등록실패")
            logger.error("   ❌ 등록 실패")

        # 딜레이 (네이버 API Rate Limit 방지)
        if i < len(targets) - 1:
            delay = random.uniform(CONFIG["DELAY_MIN"], CONFIG["DELAY_MAX"])
            logger.info(f"   ⏱️  다음 상품까지 {delay:.1f}초 대기...")
            time.sleep(delay)

    # ── STEP 5: 결과 요약 + Discord 알림
    logger.info(f"\n{'='*60}")
    logger.info(f"🏁 리스팅 완료 | 성공: {len(success_list)}개 | 실패/스킵: {fail_count}개")
    for r in success_list:
        logger.info(f"   ✅ {r['name'][:40]} | {r['price']:,}원 | ID: {r['id']}")
    logger.info("=" * 60)

    send_discord(success_list, len(success_list), fail_count)


if __name__ == "__main__":
    main()
