# 2026-11-24 — 늦게 시작해도 남나 (P41) + P30·P34 판정 기록

**세션:** https://claude.ai/code/session_01NW6d8yMKTBLhW7RdeHRgsq. 11/22·11/23 일일 보고 D 가설 → 사용자 "추가해".

## 배경
- 순방향에서 방향이 맞는 것은 P35(전날 되돌림이 진 날만 씀)·P39(같은 규칙 해외 선물 12시장)뿐. P35 11일 중 3일 써서 +286.4%(3/3), 큰 손실 날(11/14 −389, 11/20 −213)은 모두 쉼.
- P35 는 하루가 UTC 0시(한국 9시)에 시작한다고 가정. 자동 실행은 자주 늦고(11/17 7시간 반), 실거래도 판단 → 주문까지 시간이 걸린다. 실거래 설계 전에 "하루 첫 2시간을 놓쳐도 남나"를 잰다.

## 한 일
- `scalp.py`: `LATE_START_SEC`=7200(고정). `summarize(..., late_i)` 가 `late_net_pct`·`late_trades`(평가일 UTC 02시 이후 진입 매매만) 계산, `evaluate` 가 시간 단위별 `late_i` 를 넘김, `scalp_history.jsonl` 행에 `ntl` 추가. 기존 `nt`·판정·후보 계산은 그대로.
- `scalp_rotate.py`: `family_sum(..., field)`(값 없는 옛 행은 세지 않음), `rev_after_loss_late`, `build()` 출력 `aux_late`(P35 18시장·P39 12시장, 순방향 평가일 11/24~, `full_day_net_pct` = 같은 날 하루 전체로 셌다면), 스키마 `/5`, 보고 두 줄.
- 시험: `Arithmetic.test_late_start_net_only_counts_late_entries`, `RegimeRotate.test_late_start_uses_full_day_gate_and_late_result`. 합성 1분봉으로 `evaluate` 가 모든 행에 `late_net_pct` 를 채우는지 확인.
- 카드 `docs/cards/p41_late_start.svg`(OKX DOGE 60분봉, 하루 첫 2시간 빨간 띠).
- `proposals.md`: P41 등록, P40(체결 기록 보완) 철회 기록, **P30 11/20 탈락·P34 11/22 불성립** 판정 기록. `HANDOVER.md` 현황표·문단, `docs/cards/README.md`.

## 판정 기록
- P30 국면 교대(11/20): 순방향 14일 교대 −504.3% / 되돌림만 −175.3% / 돌파만 −92.1%, 플러스 5일 → 탈락, 사후 +101% 는 우연.
- P34 60분 보조(11/22): 60분 되돌림 고정 −107.8% > 60분만 교대 −414.0% 이나 고정이 마이너스 → 불성립.

## 바뀌지 않은 것
P35·P39 판단 규칙, `criteria.json`, `rules_lock.json`, 워크플로. 주문·키 없음.

## 다음
- 11/25 아침 첫 `ntl` 기록. 11/26 P35·12/2 P39 판단 때 늦게 시작 합을 함께 보고.
- 11/24 P38 첫 판단(오늘 17:10 보고).
