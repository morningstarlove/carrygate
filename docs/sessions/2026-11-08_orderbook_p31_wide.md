# 2026-11-08 — 호가 제공 시장별 기록 + 넓은 호가폭 시장 합산 (P31)

**세션:** https://claude.ai/code/session_01NW6d8yMKTBLhW7RdeHRgsq. 11/7 일일 보고 D 가설 → 사용자 "추가해".

## 가설과 근거
- "P29(닿으면 체결)는 호가폭이 왕복 수수료의 2배를 넘는 시장에서만 플러스." 업비트 호가폭 BTC 2.8 / ETH 5.2 / XRP 6.7 / DOGE 10.5 / LINK 12.5 / TRX 24.4bp, 거래소 합계 −4.9bp/건(11/7).
- TRX 가 넓은 이유: 가격 437원에 틱 1원 → 1틱 = 22.9bp. 호가폭이 시장 구조(틱)에서 온다.
- 시장별 결과가 저장되지 않아 전날 보고의 "TRX +11.7bp" 를 재확인할 수 없었다 → 기록부터 추가.

## 한 일
- `orderbook.py`: `WIDE_SPREAD_BP = 20.0`(수수료 10bp×2, 고정). `analyze()` 요약에 `mmt_by_market`(시장별 호가폭·왕복·건당 bp·wide 여부)과 `mmt_wide_markets/trades/net_bp_sum/net_bp_avg`. `print_result`·`--report` 에 시장별 표와 ★합계. P29 합계는 그대로(시장별 합 = 거래소 합, 테스트로 확인).
- 카드 `docs/cards/p31_mm_wide.svg`(업비트 TRX 1분봉 240개 실데이터, `docs/cards/data/upbit_trx_1m.csv`). 체결 마커는 없음 — 체결은 체결 기록으로 자동 집계되고 저장소에 표본 CSV 가 없다.
- `proposals.md` P31, `HANDOVER.md` 문단, `docs/cards/README.md` 줄, `tests/test_research.py::MarketMakingWide`.
- 일일 보고 Routine 프롬프트에 P31 줄 추가.

## 바뀌지 않은 것
P29 계산·`criteria.json`·`rules_lock.json`·워크플로. 주문·키 없음.

## 다음
- 11/8 저녁(22:00 KST) 수집부터 시장별 숫자가 `orderbook_history.jsonl` 에 쌓인다.
- 11/12 P29 첫 판단, 11/15 ★시장 7일 첫 판단, 11/20 P30.
