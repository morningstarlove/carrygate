# 2026-11-07 — 트랙 카드(한 장 그림) 의무화

## 사용자 지시
"새로운 트랙이 시작되면 어떤 로직으로 진행되는지 한장의 이미지로 — 캔들, 진입 시점, 논리, 목표, 기대값, 결과 예측 — 보여주도록 아예 박아놔."

## 한 일
- `track_card.py` 신규(표준 라이브러리만): 봉 CSV + 설명 JSON → SVG 한 장. `--preset scalp` 면 `scalp.py` 규칙 코드를 실제로 돌려 진입(▲롱/▼숏)·청산(○, 수수료 뺀 손익) 을 그린다. 보조선: VWAP(60봉), 20봉 고가.
- 첫 카드 `docs/cards/p30_rotate.svg` — OKX XRP 무기한선물 30분봉 300개(10/31~11/7 UTC, TradingView 수집, `docs/cards/data/okx_xrp_30m.csv`). 마지막 150봉 구간에서 두 규칙 실제 결과: VWAP 되돌림 지정가 8건 −7.22%, 20봉 돌파 7건 +6.09%(11/5 급락 추세 구간).
- 규칙으로 박음: `CLAUDE.md` 작업 방식에 "새 트랙·규칙을 시작하면 한 장 카드를 반드시 만든다 — 카드 없는 트랙 추가는 미완성", `HANDOVER.md` 4절 같은 문장. 목록·만드는 법 `docs/cards/README.md`.
- `tests/test_track_card.py` 4건. 전체 283건 통과, selfcheck 문제 없음, 잠금 일치.

## 바뀌지 않은 것
규칙 상수·`criteria.json`·`rules_lock.json` 없음. 주문·키 없음.

## 다음
- 새 규칙 PR 마다 카드 1장 + 봉 CSV 동봉(README 순서).
- P29 판정 11/12, P30 판정 11/20.
