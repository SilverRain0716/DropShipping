# 🧠 P1 PM 상태보고서
> 새 채팅 시작 시 이 파일을 P1에 업로드하면 컨텍스트 즉시 복원됩니다.

업데이트: 2026-03-02 (Day 1 완료)

---

## 📍 사업 현황
- **Phase**: 1 (M1~4)
- **진행일차**: Day 1 완료 / Day 2 시작 전
- **마스터플랜 저장소**: https://github.com/SilverRain0716/DropShipping

---

## 🖥️ 인프라 현황
| 항목 | 상태 | 비고 |
|---|---|---|
| 로컬 환경 | ✅ 완료 | Windows / Python 3.11.9 (64bit) / pyenv / venv |
| 로컬 크롤러 경로 | ✅ | C:\projects\dropship-crawler\ |
| AWS EC2 K-Trader | ✅ 실행 중 | 키움 API 전용 / IP: 43.203.218.220 / t3.small |
| AWS EC2 dropship-crawler | ✅ 실행 중 | 크롤러 전용 / IP: 52.79.177.182 / t3.micro |
| EC2 OS | ✅ | Ubuntu 24.04 LTS |
| EC2 Python | ✅ | Python 3.12.3 + Playwright + Chromium 설치 완료 |
| EC2 경로 | ✅ | ~/dropship-crawler (venv 구성 완료) |
| pem 파일 위치 | ⚠️ 로컬만 보관 | C:\Users\Administrator\Downloads\ktrader-key.pem |
| SSH 접속 명령어 | | `ssh -i "C:\Users\Administrator\Downloads\ktrader-key.pem" ubuntu@52.79.177.182` |

---

## 🕷️ 크롤러 현황
| 크롤러 | 상태 | 비고 |
|---|---|---|
| eBay Sold 크롤러 v2 | ✅ 완료 | Playwright 방식 / Akamai 봇감지 우회 완료 |
| 수집 데이터 | ✅ | 540개 수집 → 515개 정합성 검증 완료 |
| Google Sheets 연동 | ✅ 완료 | service_account.json 로컬 보관 |
| EC2 배포 (scp) | 🔴 Day 2 예정 | 로컬 → EC2 업로드 필요 |
| GitHub Actions cron | ⬜ 대기 | Day 2 예정 (1일 2회, KST 06:00 / 18:00) |
| Amazon 크롤러 | ⬜ 대기 | Day 2 시작 예정 / IP 차단 HIGH 주의 |
| TikTok 크롤러 | ⬜ 대기 | |
| 브랜드공홈 크롤러 | ⬜ 대기 | |
| Slack/카카오 알림 | ⬜ 대기 | |

---

## 📊 KPI 현황
| 지표 | 목표 (M4) | 현재 | 판정 |
|---|---|---|---|
| 크롤링 소스 가동 | 4개 | 1개 (eBay) | 🟡 진행중 |
| 데이터 정합성 | 95%+ | 96.7% | ✅ 통과 |
| Etsy 리스팅 | 10개+ | 0개 | ⬜ 미시작 |
| 월 주문 | 30건 | 0건 | ⬜ 미시작 |
| 위닝 상품 확정 | 3개 | 0개 | ⬜ 미시작 |
| 사업자 등록 | 완료 | 미완료 | ⬜ 미시작 |

---

## 🛒 채널 현황
| 채널 | 상태 | 진입 조건 |
|---|---|---|
| Etsy US | ⬜ 미개설 | 지금 개설 가능 |
| 스마트스토어 | ⬜ Phase 2 | Etsy 월 주문 30건 + 위닝 3개 달성 후 |
| 쿠팡 | ⬜ Phase 3 | 스마트스토어 월 주문 50건 달성 후 |

---

## 📅 Day 2 우선순위 (다음 세션)
1. 🔴 로컬 크롤러 파일 → EC2 scp 업로드
2. 🔴 EC2에서 크롤러 실행 + Google Sheets end-to-end 테스트
3. ⬜ GitHub Actions cron 설정 (1일 2회 자동화)
4. ⬜ Amazon 크롤러 작성 시작

---

## ⚠️ 보안 원칙 (절대 준수)
- 크롤러 실행 IP(52.79.177.182) ≠ 판매계정 IP 절대 분리
- `service_account.json` → GitHub 업로드 금지
- `ktrader-key.pem` → GitHub 업로드 금지
- 로컬 PC에서 크롤러 직접 실행 금지 (키움 API IP 혼용 위험)

---

## 🤖 Claude 워크스페이스 구조
| ID | 역할 | 열기 타이밍 |
|---|---|---|
| P1 | 🧠 PM·마스터플랜 총괄 | 매주 월요일 + 큰 결정 시 |
| P2 | 🕷️ 크롤러 엔지니어 | 크롤러 작성·디버깅 시 |
| P3 | 📊 데이터 분석가 | 매일 아침 (크롤링 완료 후) |
| P4 | 🛒 리스팅 전문가 | 위닝 상품 확정 즉시 |
| P5 | 💬 CS·운영 | 주문 발생 시 |
| P6 | ⚖️ 리스크 감시자 | 문제 발생 시만 |

---
*이 파일은 매 세션 종료 시 업데이트 후 GitHub 커밋할 것*
