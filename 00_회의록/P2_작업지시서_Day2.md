# 📋 P2 크롤러 엔지니어 작업지시서 — Day 2
**발신: P1 PM | 수신: P2 크롤러 엔지니어 | 일자: 2026-03-02**

---

## ✅ Day 1 완료 현황 (참고)
- eBay Sold 크롤러 v2 (Playwright) 완성 — Akamai 봇감지 우회 완료
- Google Sheets 연동 완료 (515행 저장)
- AWS EC2 dropship-crawler 생성 완료
  - IP: 52.79.177.182 / Ubuntu 24.04 / Python 3.12.3 + Playwright 설치 완료
  - 경로: ~/dropship-crawler (venv 활성화 완료)
- 로컬 환경: Windows / C:\projects\dropship-crawler\

---

## 🔴 Day 2 우선순위 작업 (순서 엄수)

### [작업 1] 미국 Residential Proxy 선정 및 EC2 연동 ← 최우선
**배경**: F2 비자 리스크 관리 + Etsy 계정 신뢰도 확보를 위해
서울 EC2에서 미국 Residential Proxy 경유 방식으로 확정됨.

- 추천 서비스 (비용 순): Webshare → Smartproxy → Oxylabs
- 미국 주거용 IP(Residential) 필수 — 데이터센터 IP 사용 금지
- EC2 크롤러 코드에 프록시 설정 추가
- 프록시 적용 후 IP 확인 코드 작성:
```python
# 프록시 적용 후 반드시 IP 검증
import requests
res = requests.get('https://api.ipify.org?format=json', proxies=proxies)
print(res.json())  # 미국 IP 떠야 정상
```

### [작업 2] 로컬 크롤러 → EC2 업로드 (scp)
- pem 파일: C:\Users\Administrator\Downloads\ktrader-key.pem
- EC2 접속: ubuntu@52.79.177.182
- 업로드 명령어 (로컬 PowerShell):
```powershell
scp -i "C:\Users\Administrator\Downloads\ktrader-key.pem" -r C:\projects\dropship-crawler\* ubuntu@52.79.177.182:~/dropship-crawler/
```
- ⚠️ service_account.json 포함해서 업로드 필요
- ⚠️ .gitignore에 service_account.json 반드시 추가

### [작업 3] EC2 + Proxy 환경 end-to-end 테스트
- venv 활성화 → 크롤러 실행
- 미국 IP로 eBay 크롤링 정상 수행 확인
- Google Sheets 저장까지 전체 파이프라인 검증
- 정합성 95%+ 확인

### [작업 4] GitHub Actions cron 설정
- 1일 2회 자동 실행: KST 06:00 / 18:00 (UTC 21:00 / 09:00)
- EC2 SSH 접속 → 크롤러 실행 방식
- 실행 로그 Slack 또는 카카오 알림 연동

### [작업 5] Amazon 크롤러 작성 시작
- 수집 대상: 베스트셀러 TOP100 (홈데코 / 반려동물 / 주방용품)
- 수집 항목: 상품명, 순위, 가격, 리뷰수, ASIN, 순위변동
- ⚠️ IP 차단 HIGH — 딜레이 5~15초 랜덤, UA 로테이션 필수
- ⚠️ Residential Proxy 연동 필수 (데이터센터 IP 즉시 차단됨)

---

## 💰 마진 기준 (P3 분석가에게도 전달 필요)
| 카테고리 | 최소 마진 |
|---|---|
| 캔들·홈 디퓨저 | 35%+ |
| 반려동물 소품 | 35%+ |
| 주방 도구·가젯 | 25%+ |
| 미니멀 스테이셔너리 | 40%+ |
| 친환경 생활용품 | 30%+ |
| **공통 하한선** | **25% (이하 리스팅 금지)** |

---

## ⚠️ 보안 원칙 (코드 주석에 반드시 포함)
```python
# ⚠️ 경고: 이 크롤러는 EC2(52.79.177.182) + 미국 Residential Proxy에서만 실행
# ⚠️ 로컬 PC 실행 금지 — 키움 API(K-Trader) IP와 혼용 절대 금지
# ⚠️ K-Trader EC2(43.203.218.220)에서 실행 금지
# ⚠️ service_account.json GitHub 업로드 금지
```

---

## 📋 보고 형식 (작업 완료 시 P1에 보고)
```
📅 날짜
🕷️ [작업명] 현황 보고
✅ 완료 항목
🔴 잔여 항목 + 예상 완료 시점
📋 전체 로드맵 현황 (표 형식)
다음 보고 시점 명시
```
