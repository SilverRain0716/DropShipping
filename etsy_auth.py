#!/usr/bin/env python3
"""
Etsy OAuth2 인증 스크립트
━━━━━━━━━━━━━━━━━━━━━━━━
Etsy Open API v3 OAuth 2.0 PKCE 플로우로 Access Token 발급

사용법:
  1. python etsy_auth.py
  2. 브라우저에서 인증 URL 클릭 → Etsy 로그인 → 권한 승인
  3. 리다이렉트된 URL의 ?code= 값 입력
  4. Access Token 출력 → GitHub Secret에 등록

필요 환경변수:
  ETSY_API_KEY       - Etsy API Keystring
  ETSY_SHARED_SECRET - Etsy Shared Secret

발급되는 토큰:
  - Access Token (유효: 1시간)
  - Refresh Token (유효: 90일)
"""

import os
import sys
import json
import hashlib
import base64
import secrets
import urllib.parse
import requests

ETSY_API_KEY = os.environ.get("ETSY_API_KEY", "")
ETSY_SHARED_SECRET = os.environ.get("ETSY_SHARED_SECRET", "")
REDIRECT_URI = "https://localhost:3003/callback"

# OAuth2 scopes (리스팅 읽기/쓰기 + 샵 읽기 + 주문 읽기)
SCOPES = [
    "listings_r", "listings_w", "listings_d",
    "shops_r", "shops_w",
    "transactions_r",
    "email_r",
]

def generate_pkce():
    code_verifier = secrets.token_urlsafe(64)[:128]
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return code_verifier, code_challenge


def get_auth_url(code_challenge: str) -> str:
    params = {
        "response_type": "code",
        "client_id": ETSY_API_KEY,
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(SCOPES),
        "state": secrets.token_hex(16),
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"https://www.etsy.com/oauth/connect?{urllib.parse.urlencode(params)}"


def exchange_code(auth_code: str, code_verifier: str) -> dict:
    resp = requests.post(
        "https://api.etsy.com/v3/public/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": ETSY_API_KEY,
            "redirect_uri": REDIRECT_URI,
            "code": auth_code,
            "code_verifier": code_verifier,
        },
        timeout=15,
    )
    return resp.json()


def refresh_access_token(refresh_token: str) -> dict:
    resp = requests.post(
        "https://api.etsy.com/v3/public/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": ETSY_API_KEY,
            "refresh_token": refresh_token,
        },
        timeout=15,
    )
    return resp.json()


def main():
    if not ETSY_API_KEY:
        print("❌ ETSY_API_KEY 환경변수 미설정")
        print("   set ETSY_API_KEY=your_keystring")
        sys.exit(1)

    # Refresh Token이 있으면 갱신 모드
    if len(sys.argv) > 1 and sys.argv[1] == "--refresh":
        refresh_token = input("Refresh Token 입력: ").strip()
        result = refresh_access_token(refresh_token)
        if "access_token" in result:
            print(f"\n✅ Access Token 갱신 성공!")
            print(f"   Access Token:  {result['access_token']}")
            print(f"   Refresh Token: {result['refresh_token']}")
            print(f"   Expires in:    {result.get('expires_in', 3600)}초")
        else:
            print(f"\n❌ 갱신 실패: {result}")
        return

    # 새 인증 플로우
    code_verifier, code_challenge = generate_pkce()

    auth_url = get_auth_url(code_challenge)
    print("=" * 60)
    print("🔑 Etsy OAuth2 인증")
    print("=" * 60)
    print(f"\n1. 아래 URL을 브라우저에 붙여넣으세요:\n")
    print(f"   {auth_url}\n")
    print(f"2. Etsy 로그인 → 권한 승인")
    print(f"3. 리다이렉트된 URL에서 ?code= 값 복사\n")
    print(f"   예: https://localhost:3003/callback?code=XXXXXX&state=...")
    print(f"        → XXXXXX 부분만 복사\n")

    auth_code = input("Authorization Code 입력: ").strip()
    if not auth_code:
        print("❌ 코드가 비어있습니다")
        sys.exit(1)

    result = exchange_code(auth_code, code_verifier)

    if "access_token" in result:
        print(f"\n{'=' * 60}")
        print(f"✅ 인증 성공!")
        print(f"{'=' * 60}")
        print(f"\nAccess Token:  {result['access_token']}")
        print(f"Refresh Token: {result['refresh_token']}")
        print(f"Expires in:    {result.get('expires_in', 3600)}초")
        print(f"\n📋 GitHub Secrets에 등록하세요:")
        print(f"   ETSY_ACCESS_TOKEN  = {result['access_token']}")
        print(f"   ETSY_REFRESH_TOKEN = {result['refresh_token']}")

        # 토큰을 파일에도 저장 (로컬 백업)
        with open("etsy_tokens.json", "w") as f:
            json.dump(result, f, indent=2)
        print(f"\n💾 etsy_tokens.json에 저장됨 (보안 주의!)")
    else:
        print(f"\n❌ 인증 실패: {result}")


if __name__ == "__main__":
    main()
