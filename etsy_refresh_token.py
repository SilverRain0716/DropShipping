#!/usr/bin/env python3
"""
Etsy Access Token 자동 갱신
GitHub Actions에서 실행 → Refresh Token으로 새 Access Token 발급
"""
import os
import sys
import requests

ETSY_API_KEY = os.environ.get("ETSY_API_KEY", "")
REFRESH_TOKEN = os.environ.get("ETSY_REFRESH_TOKEN", "")

if not ETSY_API_KEY or not REFRESH_TOKEN:
    print("❌ ETSY_API_KEY 또는 ETSY_REFRESH_TOKEN 미설정")
    sys.exit(1)

resp = requests.post(
    "https://api.etsy.com/v3/public/oauth/token",
    data={
        "grant_type": "refresh_token",
        "client_id": ETSY_API_KEY,
        "refresh_token": REFRESH_TOKEN,
    },
    timeout=15,
)
data = resp.json()

if "access_token" in data:
    token = data["access_token"]
    # GitHub Actions output으로 전달
    with open(os.environ.get("GITHUB_OUTPUT", "/dev/null"), "a") as f:
        f.write(f"access_token={token}\n")
    print(f"✅ Access Token 갱신 성공 (만료: {data.get('expires_in', 3600)}초)")
else:
    print(f"❌ Token 갱신 실패: {data}")
    sys.exit(1)
