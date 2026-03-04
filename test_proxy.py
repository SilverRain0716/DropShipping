import os, requests
from dotenv import load_dotenv
load_dotenv()

PROXY_HOST      = os.environ.get("PROXY_HOST",     "p.webshare.io")
PROXY_PORT      = os.environ.get("PROXY_PORT",     "80")
PROXY_USER_BASE = os.environ.get("PROXY_USER_BASE","wthluxio-us")
PROXY_PASSWORD  = os.environ.get("PROXY_PASSWORD", "")

print("=" * 50)
success = 0
for i in range(1, 11):
    try:
        px = {
            "http":  f"http://{PROXY_USER_BASE}-{i}:{PROXY_PASSWORD}@{PROXY_HOST}:{PROXY_PORT}",
            "https": f"http://{PROXY_USER_BASE}-{i}:{PROXY_PASSWORD}@{PROXY_HOST}:{PROXY_PORT}",
        }
        ip = requests.get("https://api.ipify.org?format=json", proxies=px, timeout=10).json()["ip"]
        geo = requests.get(f"http://ip-api.com/json/{ip}", timeout=10).json()
        cc, city = geo.get("countryCode","??"), geo.get("city","?")
        mark = "✅" if cc == "US" else "⚠️ "
        print(f"{mark} [{i:02d}] → {ip} | {cc} / {city}")
        if cc == "US": success += 1
    except Exception as e:
        print(f"❌ [{i:02d}] 실패: {e}")

print("=" * 50)
print(f"결과: {success}/10 | {'✅ 크롤러 실행 가능' if success >= 5 else '🚫 Proxy 점검 필요'}")
