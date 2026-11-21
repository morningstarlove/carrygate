# 2026-11-18 청산 알림 매매법 검토 → 진짜 청산 흐름 순방향 기록 (제안함 P40, 사용자 결정 "b")

> 사용자 요청: SNS 글 "청산 알림 하나로 매매하는 법"(degenunnie, 3장) 공유, "청산전략 연구".
> 한 줄 결론: **봉 마감 뒤 따라가는 방식으로는 1·5·15분봉 어디에서도 수수료 뒤 우위 없음(t 전부 2 미만). SOXL→개별주는 글과 반대로 되돌림. 글의 우위가 있다면 봉이 닫히기 전 몇 초 안이고 이 자료로는 못 본다.** 자세한 것은 `docs/LIQUIDATION_CASCADE_REVIEW.md`.

## 한 일
- `docs/LIQUIDATION_CASCADE_REVIEW.md` — 글 → 숫자 규칙(결과 보기 전 고정), 주장별 실데이터 표, 한계, 선택지 A/B/C.
- `scripts/liq_cascade_check.py` — 재현 스크립트(표준 라이브러리, 인터넷 불필요).
- `data/liq_cascade/` — TradingView 봉 9개(바이낸스 BTC·ETH 선물 1·5·15분, SOXL·MU·SNDK 5분), 2.9MB.
- `proposals.md` P40 등록(대기). 규칙·`criteria.json`·잠금 변경 없음. (1차 검토 시점. 2차에서 트랙·잠금·카드 추가 — 아래)

## 숫자 (수수료 왕복 0.10% 뺀 뒤, 건당)
- 비트 급락(3σ·거래량 3배) → 이더 숏: 1분봉 1분 뒤 −0.183%(27건, t −2.0, 반등) / 5분봉 60분 뒤 +0.064%(33건, t +0.7) / 15분봉 15분 뒤 +0.017%(45건, t +1.5).
- 가장 좋은 칸: 5분봉 4σ 60분 뒤 +0.295%(22건, 승률 36%, t +1.3). 같은 기간 아무 때나 이더 숏 60분 총 +0.042%(하락장).
- 이더 동반 하락 배수 1.2~1.4배(같은 봉 안). BTC→ETH 1봉 뒤 상관 5분 +0.03, 1분 0.00.
- SOXL 급락(3σ) → 마이크론 숏 15분 −0.601%(24건, t −2.0), 샌디스크 −0.411%. 1봉 뒤 상관 +0.059·+0.042.
- 긴 아래꼬리 롱: 조건 충족 3~5건 — 판단 불가.

## 사용자 결정
P40: **"b"(2026-11-18)** — 사용자 서버에 실시간 청산 수신기를 띄워 순방향 기록.

## 2차: 순방향 기록 장치 (같은 날)
- `vps_liq_collector.py` — 서버 상시 수신기. 바이낸스 `!forceOrder@arr`(전 종목 청산) + BTC·ETH 체결 흐름. 웹소켓을 표준 라이브러리로 직접 구현. 2026-04 주소 분리(/market·/public) 때문에 후보 7개를 차례로 시도. `--probe` 로 주소 시험.
- `scripts/vps_liq_install.sh` — 서버 설치 한 줄(받기 → 주소 시험 → systemd `carrygate-liq` → 확인). 기존 Caddy `/carrygate/*` 경로를 그대로 쓴다.
- `liq_cascade.py` — 매일 집계. 규칙·판단 기준은 파일 맨 위. `RULE_FIXED` 2026-11-18, `rules_lock.json` 에 추가.
- `tests/test_liq_cascade.py` 17건 — 신호·쉼·불리한 쪽 체결가·하루 집계·판단 기준·수신기 해석·로컬 가짜 웹소켓 서버(승격·핑퐁·긴 프레임·닫힘·404)·파일 쓰기·압축.
- `.github/workflows/carrygate.yml` — "Liquidation cascade" 단계, 결과 2개 파일 저장, 시험 목록.
- 카드 `docs/cards/p40_liq_cascade.svg`(+ `.json`, `data/binance_eth_1m.csv`).
- **못 한 것:** 이 세션은 바이낸스 접속이 막혀 수신기를 실제 흐름에 붙여 보지 못했다. 공식 문서(developers.binance.com)도 못 열어 주소·형식은 검색 결과와 기존 지식에 의존. 설치 스크립트의 `--probe` 출력이 첫 실데이터 확인.
- 현황판 카드는 아직 안 만듦(자료가 쌓인 뒤).

## 사용자 할 일
1. PR 병합.
2. Vultr 서버에서: `curl -sS https://raw.githubusercontent.com/morningstarlove/carrygate/main/scripts/vps_liq_install.sh | bash` — 출력 끝이 "완료"가 아니면 출력 전체를 Claude 에게 붙인다.

## 환경 메모
- 이 세션은 거래소·야후 전부 403. 실데이터는 TradingView MCP(`get-ohlcv`, 분봉은 최근 6,700~8,000봉까지)로 받았다.
- 청산 내역 과거치는 구할 수 없어 "급락 + 거래량 급증 봉"을 대용 신호로 썼다.

## Routine
변경 없음.
