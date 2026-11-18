# 2026-11-18 — P35 를 해외 선물 12시장만으로 (P39)

**세션:** https://claude.ai/code/session_01NW6d8yMKTBLhW7RdeHRgsq. 11/18 일일 보고 D 가설 → 사용자 "추가해".

## 가설과 근거
- P35(전날 30·60분 되돌림 합 < 0 인 날만 되돌림)의 이익은 해외 선물에서 나오고 업비트는 빠진다.
- `scalp_history.jsonl` 10/13~11/17, P35 쓰는 날 12일: 18시장 중 16곳이 쓰는 날 합 플러스. 상위 HL DOGE +28.8(9/12), OKX DOGE +27.8(10/12), HL ETH +26.4, OKX XRP +25.9. 하위 업비트 TRX −3.9, 업비트 XRP −2.0, 업비트 BTC +1.1. 쉬는 날은 거의 전 시장 마이너스.
- 실거래를 한다면 공매도 되는 해외 선물이 현실적 → 같은 규칙을 OKX·하이퍼리퀴드 12시장 합만으로(전날 판단도 12시장) 순방향 기록.

## 한 일
- `scalp_rotate.py`: `family_sum` 에 거래소 필터(`venues`), `FAMILIES["rev_off"]`, `build()` 출력 `aux_after_loss_offshore`(사후/순방향, `next_active`), 스키마 `/4`, 보고 세 줄. `AUX3_FIXED`=`AUX3_FORWARD_FROM`=2026-11-18. P30·P34·P35 그대로(시험 확인).
- 사후 35일(평가일 ~11/17): 14일 써서 +93.7%, 11/14 플러스, 쉰 날 12시장 되돌림 −156.8%, 12시장 되돌림 고정 −63.1%.
- 카드 `docs/cards/p39_p35_offshore.svg` — OKX DOGE 60분봉 168개 실데이터(11/11~11/18), 날짜별 씀·쉼 띠(12시장 기준)와 두 되돌림 규칙의 실제 진입·청산.
- `proposals.md` P39, `HANDOVER.md` 현황표·문단(P38 첫 이틀 숫자 포함), `docs/cards/README.md`, `tests/test_scalp.py::RegimeRotate::test_offshore_after_loss_ignores_upbit`.
- 일일 보고 Routine 에 P39 줄 추가.

## 바뀌지 않은 것
P30·P34·P35 계산, `criteria.json`, `rules_lock.json`, 워크플로. 주문·키 없음.

## 다음
- 11/20 P30, 11/22 P34, 11/24 P38, 11/26 P35, **12/2 P39** 첫 판단.
