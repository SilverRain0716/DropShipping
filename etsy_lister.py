#!/usr/bin/env python3
"""
Etsy 자동 리스팅 스크립트 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Google Sheets → 리스팅 템플릿 생성 → Etsy API 업로드

파이프라인:
  1. Google Sheets에서 CJ 위닝후보 읽기
  2. Etsy 최적화 제목/태그/설명 자동 생성
  3. CJ 상품 이미지 다운로드
  4. Etsy API로 리스팅 생성 (Draft → Active)

필요 환경변수:
  ETSY_API_KEY        - Etsy API Keystring
  ETSY_SHARED_SECRET  - Etsy Shared Secret
  ETSY_ACCESS_TOKEN   - OAuth2 Access Token (etsy_auth.py로 발급)
  ETSY_SHOP_ID        - Etsy Shop ID
  SHEET_ID            - Google Sheets ID
  GOOGLE_SA_KEY_PATH  - GCP 서비스 계정 키 경로
  DISCORD_WEBHOOK_URL - Discord 알림 웹훅

⚠️ 판매 계정(Etsy)과 동일 IP 사용 절대 금지
⚠️ Etsy API Rate Limit: 5 QPS / 5K QPD
"""

import os
import sys
import json
import time
import random
import logging
import hashlib
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 설정
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
KST = timezone(timedelta(hours=9))

CONFIG = {
    "MAX_LISTINGS_PER_RUN":  10,      # 1회 실행당 최대 리스팅 수
    "LISTING_PRICE_MARKUP":  2.5,     # 소싱가 × 마크업 = 판매가
    "MIN_PRICE_USD":         29.99,   # 최소 판매가
    "MAX_PRICE_USD":         99.99,   # 최대 판매가
    "DEFAULT_QUANTITY":      999,     # 드롭쉬핑이므로 대량 설정
    "SHIPPING_PROFILE_ID":   None,    # 첫 실행 시 자동 생성
    "WHO_MADE":              "someone_else",
    "WHEN_MADE":             "2020_2026",
    "TAXONOMY_ID_HOME_DECOR": 891,    # Home & Living > Home Decor
    "ETSY_API_BASE":         "https://api.etsy.com/v3/application",
}

# 환경변수
ETSY_API_KEY       = os.environ.get("ETSY_API_KEY", "")
ETSY_SHARED_SECRET = os.environ.get("ETSY_SHARED_SECRET", "")
ETSY_ACCESS_TOKEN  = os.environ.get("ETSY_ACCESS_TOKEN", "")
ETSY_SHOP_ID       = os.environ.get("ETSY_SHOP_ID", "")
SHEET_ID           = os.environ.get("SHEET_ID", "")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 로깅
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
logger = logging.getLogger("etsy_lister")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("etsy_lister.log", encoding="utf-8"),
    ],
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Etsy API 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def etsy_headers() -> dict:
    return {
        "x-api-key": f"{ETSY_API_KEY}:{ETSY_SHARED_SECRET}",
        "Authorization": f"Bearer {ETSY_ACCESS_TOKEN}",
        "Content-Type": "application/x-www-form-urlencoded",
    }


def etsy_api_call(method: str, endpoint: str, data: dict = None, files=None) -> Optional[dict]:
    url = f"{CONFIG['ETSY_API_BASE']}{endpoint}"
    headers = etsy_headers()
    if files:
        headers.pop("Content-Type", None)

    try:
        time.sleep(0.25)  # Rate limit: 5 QPS
        if method == "GET":
            resp = requests.get(url, headers=headers, params=data, timeout=15)
        elif method == "POST":
            if files:
                resp = requests.post(url, headers=headers, data=data, files=files, timeout=30)
            else:
                resp = requests.post(url, headers=headers, data=data, timeout=15)
        elif method == "PUT":
            resp = requests.put(url, headers=headers, data=data, timeout=15)
        else:
            return None

        if resp.status_code in (200, 201):
            return resp.json()
        else:
            logger.error(f"Etsy API {resp.status_code}: {resp.text[:300]}")
            return None
    except Exception as e:
        logger.error(f"Etsy API 오류: {e}")
        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 리스팅 템플릿 생성 (제목 / 태그 / 설명)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Etsy SEO 키워드 맵 (카테고리별)
ETSY_KEYWORDS = {
    "home decor": [
        "home decor", "living room decor", "housewarming gift",
        "modern home", "minimalist decor", "apartment decor",
        "room decoration", "aesthetic home", "cozy home",
        "interior design", "new home gift", "home accessories",
    ],
    "wall art": [
        "wall art", "wall decor", "wall hanging",
        "living room art", "modern wall art", "home wall decor",
        "bedroom decor", "office decor", "abstract art",
        "decorative art", "art piece", "contemporary decor",
    ],
    "gothic": [
        "gothic decor", "dark aesthetic", "gothic home",
        "spooky decor", "witch decor", "occult decor",
        "dark academia", "horror decor", "macabre art",
        "gothic gift", "alternative decor", "dark home",
    ],
    "halloween": [
        "halloween decor", "halloween decoration", "spooky season",
        "halloween party", "trick or treat", "haunted house",
        "halloween gift", "fall decor", "autumn decoration",
        "halloween prop", "scary decor", "october decor",
    ],
}


def generate_etsy_title(product_name: str, category: str) -> str:
    """
    Etsy SEO 최적화 제목 생성
    규칙: 140자 이내, 핵심 키워드 앞에 배치
    형식: [상품명] | [카테고리 키워드] | [용도 키워드]
    """
    name = product_name.strip()
    # 너무 긴 이름 자르기
    if len(name) > 80:
        name = name[:77] + "..."

    # 카테고리 키워드 추가
    kw = ETSY_KEYWORDS.get(category, ETSY_KEYWORDS["home decor"])
    suffix_options = [
        f"{kw[0].title()} | Housewarming Gift",
        f"{kw[0].title()} | Unique Gift Idea",
        f"{kw[0].title()} | Modern Home Accent",
    ]
    suffix = random.choice(suffix_options)

    title = f"{name} | {suffix}"
    # Etsy 제목 140자 제한
    if len(title) > 140:
        title = title[:137] + "..."

    return title


def generate_etsy_tags(product_name: str, category: str) -> list[str]:
    """
    Etsy 태그 생성 (최대 13개, 각 20자 이내)
    """
    base_tags = ETSY_KEYWORDS.get(category, ETSY_KEYWORDS["home decor"])[:8]

    # 상품명에서 추가 키워드 추출
    name_words = product_name.lower().split()
    extra_tags = []
    useful_words = {"led", "vintage", "modern", "rustic", "wooden", "ceramic",
                    "glass", "metal", "boho", "minimalist", "handmade", "lamp",
                    "vase", "clock", "mirror", "shelf", "candle", "frame"}
    for word in name_words:
        clean = word.strip(".,!?()[]\"'")
        if clean in useful_words and clean not in " ".join(base_tags):
            extra_tags.append(clean)

    all_tags = base_tags + extra_tags
    # 각 태그 20자 제한, 최대 13개
    final_tags = [t[:20] for t in all_tags][:13]
    return final_tags


def generate_etsy_description(product_name: str, category: str,
                               price: float, ship_cost: float) -> str:
    """
    Etsy 상품 설명 자동 생성
    """
    cat_display = category.replace("_", " ").title()

    description = f"""{product_name}

━━━━━━━━━━━━━━━━━━━━━━━━━━
PRODUCT DETAILS
━━━━━━━━━━━━━━━━━━━━━━━━━━

Category: {cat_display}

This beautiful {cat_display.lower()} piece is perfect for adding a unique touch to your living space. Whether you're decorating your living room, bedroom, or office, this item brings style and personality to any room.

━━━━━━━━━━━━━━━━━━━━━━━━━━
SHIPPING INFORMATION
━━━━━━━━━━━━━━━━━━━━━━━━━━

• Processing time: 1-3 business days
• Estimated delivery: 7-15 business days
• Ships from our fulfillment center
• Tracking number provided

━━━━━━━━━━━━━━━━━━━━━━━━━━
WHY CHOOSE US?
━━━━━━━━━━━━━━━━━━━━━━━━━━

✓ Carefully selected products
✓ Quality checked before shipping
✓ Fast and reliable shipping with tracking
✓ Responsive customer service
✓ 30-day satisfaction guarantee

━━━━━━━━━━━━━━━━━━━━━━━━━━
CARE INSTRUCTIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━

Please handle with care. Wipe clean with a soft, dry cloth. Keep away from direct sunlight and moisture for best longevity.

━━━━━━━━━━━━━━━━━━━━━━━━━━

If you have any questions about this product, please don't hesitate to message us. We're happy to help!

Thank you for visiting TrendPickFinds! ♥
"""
    return description


def calculate_etsy_price(source_price: float) -> float:
    """
    Etsy 판매가 계산
    소싱가 × 마크업 → 최소/최대 범위 내 조정 → .99 엔딩
    """
    raw_price = source_price * CONFIG["LISTING_PRICE_MARKUP"]
    raw_price = max(raw_price, CONFIG["MIN_PRICE_USD"])
    raw_price = min(raw_price, CONFIG["MAX_PRICE_USD"])
    # .99 엔딩
    price = int(raw_price) + 0.99
    if price > CONFIG["MAX_PRICE_USD"]:
        price = CONFIG["MAX_PRICE_USD"]
    return round(price, 2)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CJ 이미지 다운로드
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def download_cj_image(product_url: str) -> Optional[str]:
    """
    CJ 상품 페이지에서 메인 이미지 URL 추출 및 다운로드
    → 로컬 파일 경로 반환
    """
    # CJ 상품 URL에서 pid 추출
    pid = product_url.split("pid=")[-1] if "pid=" in product_url else None
    if not pid:
        return None

    # CJ API로 상품 상세 조회 → 이미지 URL 획득
    cj_token = os.environ.get("CJ_ACCESS_TOKEN", "")
    if not cj_token:
        logger.warning("CJ_ACCESS_TOKEN 미설정 — 이미지 다운로드 불가")
        return None

    try:
        resp = requests.get(
            f"https://developers.cjdropshipping.com/api2.0/v1/product/query",
            headers={"CJ-Access-Token": cj_token},
            params={"pid": pid},
            timeout=15,
        )
        data = resp.json()
        if data.get("result"):
            img_url = data["data"].get("productImage", "")
            if img_url:
                img_resp = requests.get(img_url, timeout=15)
                if img_resp.status_code == 200:
                    filename = f"/tmp/cj_img_{pid}.jpg"
                    with open(filename, "wb") as f:
                        f.write(img_resp.content)
                    return filename
    except Exception as e:
        logger.error(f"CJ 이미지 다운로드 실패 (pid={pid}): {e}")

    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Etsy 리스팅 생성
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def upload_listing_image(listing_id: int, image_path: str) -> bool:
    """Etsy 리스팅에 이미지 업로드"""
    endpoint = f"/shops/{ETSY_SHOP_ID}/listings/{listing_id}/images"
    with open(image_path, "rb") as f:
        files = {"image": ("product.jpg", f, "image/jpeg")}
        result = etsy_api_call("POST", endpoint, data={}, files=files)
    return result is not None


def create_etsy_listing(product: dict) -> Optional[int]:
    """
    Etsy에 Draft 리스팅 생성 → listing_id 반환
    """
    name = product["상품명"]
    category = product["카테고리"]
    source_price = float(product["소싱가($)"])
    ship_cost = float(product["배송비($)"])

    title = generate_etsy_title(name, category)
    tags = generate_etsy_tags(name, category)
    description = generate_etsy_description(name, category, source_price, ship_cost)
    price = calculate_etsy_price(source_price)

    logger.info(f"리스팅 생성: {title[:60]}... | ${price}")

    data = {
        "quantity": str(CONFIG["DEFAULT_QUANTITY"]),
        "title": title,
        "description": description,
        "price": str(price),
        "who_made": CONFIG["WHO_MADE"],
        "when_made": CONFIG["WHEN_MADE"],
        "taxonomy_id": str(CONFIG["TAXONOMY_ID_HOME_DECOR"]),
        "tags": ",".join(tags),
        "type": "physical",
    }

    # Shipping Profile이 있으면 추가
    if CONFIG.get("SHIPPING_PROFILE_ID"):
        data["shipping_profile_id"] = str(CONFIG["SHIPPING_PROFILE_ID"])

    endpoint = f"/shops/{ETSY_SHOP_ID}/listings"
    result = etsy_api_call("POST", endpoint, data=data)

    if result and "listing_id" in result:
        listing_id = result["listing_id"]
        logger.info(f"✅ Draft 리스팅 생성 성공: listing_id={listing_id}")
        return listing_id
    else:
        logger.error(f"❌ 리스팅 생성 실패: {name[:50]}")
        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Google Sheets 연동
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def read_winning_products() -> list[dict]:
    """
    Google Sheets에서 CJ_위닝후보 시트 읽기
    → 마진율 높은 순으로 정렬 → 상위 N개 반환
    """
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        creds = Credentials.from_service_account_file(
            os.environ.get("GOOGLE_SA_KEY_PATH", "service_account.json"),
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(SHEET_ID)
        ws = sh.worksheet("CJ_위닝후보")
        records = ws.get_all_records()

        # 마진율 기준 정렬
        records.sort(key=lambda x: float(x.get("마진율(%)", 0)), reverse=True)
        return records[:CONFIG["MAX_LISTINGS_PER_RUN"]]
    except Exception as e:
        logger.error(f"Google Sheets 읽기 실패: {e}")
        return []


def mark_as_listed(product_url: str, listing_id: int):
    """
    Google Sheets에 리스팅 완료 표시
    (추후 중복 리스팅 방지용)
    """
    # TODO: Sheets에 'Etsy_listing_id' 컬럼 추가 후 구현
    pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Discord 알림
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def send_discord_notification(success_count: int, fail_count: int, listings: list):
    if not DISCORD_WEBHOOK_URL:
        return

    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")

    if success_count > 0:
        top_items = "\n".join(
            f"  {i+1}. {l['title'][:50]} | ${l['price']}"
            for i, l in enumerate(listings[:5])
        )
        msg = (
            f"✅ **Etsy 자동 리스팅 완료** ({now})\n"
            f"성공: {success_count}개 | 실패: {fail_count}개\n\n"
            f"📋 리스팅된 상품:\n{top_items}"
        )
    else:
        msg = (
            f"❌ **Etsy 리스팅 실패/결과 없음** ({now})\n"
            f"성공: 0개 | 실패: {fail_count}개 — 로그 확인 필요"
        )

    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": msg}, timeout=10)
    except Exception:
        pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 메인 파이프라인
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def main():
    logger.info("=" * 60)
    logger.info("🏪 Etsy 자동 리스팅 v1.0 시작")
    logger.info(f"🕐 실행 시각: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')}")
    logger.info(f"🎯 최대 리스팅 수: {CONFIG['MAX_LISTINGS_PER_RUN']}개")
    logger.info("=" * 60)

    # 환경변수 검증
    missing = []
    if not ETSY_API_KEY:
        missing.append("ETSY_API_KEY")
    if not ETSY_SHARED_SECRET:
        missing.append("ETSY_SHARED_SECRET")
    if not ETSY_ACCESS_TOKEN:
        missing.append("ETSY_ACCESS_TOKEN")
    if not ETSY_SHOP_ID:
        missing.append("ETSY_SHOP_ID")
    if not SHEET_ID:
        missing.append("SHEET_ID")

    if missing:
        logger.error(f"❌ 필수 환경변수 누락: {', '.join(missing)}")
        logger.error("   → GitHub Secrets에 등록 필요")
        send_discord_notification(0, 0, [])
        return

    # 1. Google Sheets에서 위닝 후보 읽기
    logger.info("📋 Google Sheets에서 위닝 후보 읽는 중...")
    products = read_winning_products()
    if not products:
        logger.warning("⚠️ 리스팅할 상품이 없음")
        send_discord_notification(0, 0, [])
        return

    logger.info(f"📦 {len(products)}개 상품 로드 완료")

    # 2. 각 상품에 대해 리스팅 생성
    success_count = 0
    fail_count = 0
    listed_items = []

    for i, product in enumerate(products, 1):
        logger.info(f"\n{'─' * 40}")
        logger.info(f"[{i}/{len(products)}] {product.get('상품명', 'N/A')[:50]}")

        # 리스팅 생성
        listing_id = create_etsy_listing(product)
        if not listing_id:
            fail_count += 1
            continue

        # 이미지 업로드
        product_url = product.get("상품URL", "")
        img_path = download_cj_image(product_url)
        if img_path:
            img_ok = upload_listing_image(listing_id, img_path)
            if img_ok:
                logger.info(f"🖼️ 이미지 업로드 성공")
            else:
                logger.warning(f"⚠️ 이미지 업로드 실패 — Draft 상태 유지")
            # 임시 파일 정리
            try:
                os.remove(img_path)
            except OSError:
                pass
        else:
            logger.warning(f"⚠️ 이미지 다운로드 실패 — Draft 상태 유지")

        # 성공 기록
        price = calculate_etsy_price(float(product.get("소싱가($)", 0)))
        listed_items.append({
            "title": generate_etsy_title(product.get("상품명", ""), product.get("카테고리", "")),
            "price": price,
            "listing_id": listing_id,
        })
        success_count += 1
        mark_as_listed(product_url, listing_id)

        # Rate limit 준수 (상품 간 딜레이)
        time.sleep(random.uniform(1, 3))

    # 3. 결과 리포트
    logger.info(f"\n{'=' * 60}")
    logger.info(f"📊 리스팅 결과: 성공 {success_count}개 / 실패 {fail_count}개")
    logger.info(f"{'=' * 60}")

    # 4. Discord 알림
    send_discord_notification(success_count, fail_count, listed_items)


if __name__ == "__main__":
    main()
