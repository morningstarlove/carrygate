# 2026-11-13 — 되돌림은 전날 진 날에만 (P35)

**세션:** https://claude.ai/code/session_01NW6d8yMKTBLhW7RdeHRgsq. 11/10·11/12 일일 보고 D 가설 → 사용자 "추가해"(11/13).

## 가설과 근거
- 되돌림 묶음(30·60분 VWAP 지정가 + 평균회귀 지정가, 18시장 합)은 추세 날 한 번에 크게 잃고 다음날 회복한다.
- `scalp_history.jsonl` 사후 평가일 10/13~11/11(29일): 전날 합 < 0 인 날만 쓰면 10일 +184.0%(9/10 플러스), 쉰 19일의 되돌림 −310.7%, 되돌림 고정 −126.7%.
- 대조: P30(전날 이긴 쪽)은 손실 다음날 돌파로 갈아타 순방향 6일 −223.8%. 단순 역교대(전날 진 쪽)는 사후 −308% — 반등 효과는 되돌림 쪽에만 있다.
- 11/10 제안 뒤 11/10(쉼, 실제 −53)·11/11(씀, 실제 +47)도 같은 방향이었지만 규칙 고정 전이라 사후로 둔다.

## 한 일
- `scalp_rotate.py`: `rev_after_loss()`·`summarize_after_loss()`, `build()` 출력 `aux_after_loss`(사후/순방향 요약, `next_active`), 스키마 `/3`, 보고 세 줄. `AUX2_FIXED`=2026-11-13, `AUX2_FORWARD_FROM`=2026-11-12. P30·P34 그대로(테스트 확인).
- `track_card.py`: spec `bands`(날짜 배경 띠) 기능, 큰 가격 축 숫자 쉼표 표기.
- 카드 `docs/cards/p35_rev_after_loss.svg` — OKX BTC 60분봉 240개 실데이터(11/3~11/13), 날짜별 씀(초록)·쉼(회색) 띠와 그날 18시장 되돌림 합, BTC 60분 두 되돌림 규칙의 실제 진입·청산.
- `proposals.md` P35, `HANDOVER.md`, `docs/cards/README.md`, 테스트 2건(`test_after_loss_*`, `test_bands_drawn_behind`).
- 일일 보고 Routine 에 P35 줄("내일 씀/쉼") 추가.

## 바뀌지 않은 것
P30·P34 규칙, `criteria.json`, `rules_lock.json`, 워크플로. 주문·키 없음.

## 다음
- 11/20 P30, 11/22 P34, **11/26 P35** 14일 첫 판단. 11/15 P31(TRX).
