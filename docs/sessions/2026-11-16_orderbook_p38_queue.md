# 2026-11-16 — 호가 제공 줄 서기 반영 (P38)

**세션:** https://claude.ai/code/session_01NW6d8yMKTBLhW7RdeHRgsq. 11/15·11/16 일일 보고 선택지 A → 사용자 "추가해".

## 배경
- P31 11/15 판정 **후보**: 업비트 TRX 7일 836왕복, 건당 +10.29bp(수수료 뺀 뒤), 플러스일 7/7. 좁은 5시장(BTC·ETH·XRP·DOGE·LINK)은 7일 내내 마이너스. 11/16 까지 8/8, +10.12bp.
- 그러나 P29·P31 은 "내 가격에 체결이 한 번 찍히면 내 체결" — 같은 가격에 먼저 선 주문(줄)을 무시한 낙관값. TRX 는 호가 한 칸 1원(≈24bp)이라 줄이 길다. 실거래 전에 줄을 넣어 다시 잰다.

## 한 일
- `orderbook.py`
  - 수집: `snap` 이 최우선 잔량 `bid1_qty`·`ask1_qty`(코인 수)를, `fetch_upbit_trades` 가 10초 구간 가격별 매도·매수 주도 체결량 `sell_px_vol`·`buy_px_vol`('437:1200.5;436:30')을 CSV 에 추가. 200건 한도에 걸리면 `errors.upbit_trades_capped` 로 센다.
  - `mm_queue(rows, fee)`: 걸 때 최우선 잔량을 앞 줄로 적고, 내 가격의 상대 주도 체결량 누적 > 줄 × `QUEUE_MULT`(1.0, 고정)이거나 내 가격 너머 체결·상대 호가가 넘어오면 체결. 최우선이 바뀌면 새 줄 맨 뒤로. 나머지(재고 ±1, 300초 뒤 시장가)는 P29 와 같다.
  - 요약 `summary.upbit.mmq_by_market`, 하루 출력·`--report` 누적 표.
- 시험: `tests/test_research.py::MarketMakingQueue` 6건(줄 다 먹혀야 체결, 줄 미달 무체결, 내 가격 너머 체결, 가격 이동 시 줄 재설정, 칸 없으면 계산 안 함, 가격별 양 문자열 왕복). 가짜 업비트 자료로 수집 → CSV → 분석 끝까지 확인(P29 47왕복 vs P38 15왕복).
- 카드 `docs/cards/p38_mm_queue.svg`, `proposals.md` P38(+P31 판정 기록), `HANDOVER.md` 현황표·문단, `docs/cards/README.md`.

## 바뀌지 않은 것
P26·P29·P31 계산과 합계, `criteria.json`, `rules_lock.json`, 워크플로 파일. 주문·키 없음.

## 다음
- 첫 실데이터는 11/16 22:00 KST 수집(11/17 기록). 11/24 보고에서 7일 첫 판단.
- 통과하면 소액 실거래 시험 설계를 사용자에게 선택지로(별도 저장소 `carrygate-exec`, 사용자 서버의 업비트 키 — 이 저장소엔 키 없음).
- 일일 보고 Routine 에 P38 줄 추가.
