# 2026-11-07 — 초단타 국면 교대 규칙 추가 (P30)

**세션:** https://claude.ai/code/session_01NW6d8yMKTBLhW7RdeHRgsq. 11/6 일일 보고 D 가설 → 사용자 "추가해".

## 가설과 근거
- "전날 이긴 쪽을 오늘 쓴다." 11/5 추세 날 30·60분 되돌림 −205%·돌파 +107%, 10/31 되돌림 −100%·돌파 +26% 처럼 국면이 갈린다.
- `scalp_history.jsonl` 평가일 10/13~11/5(23일): 교대 +101.1%(플러스 13일), 되돌림만 −64.9%, 돌파만 −142.3%. 되돌림 연속 상관 −0.21.
- 두 선택지(전날 승자/패자) 중 하나를 고른 사후 결과 → 우연 가능성. 그래서 순방향만 센다.

## 한 일
- `scalp_rotate.py`: 묶음 합계(`family_sum`), 교대(`rotate`), 사후/순방향 분리(`build`, `FORWARD_FROM`="2026-11-06"), `scalp_rotate.json`, `--report`. 새 문턱 없음.
- 워크플로 `carrygate.yml`: 표본 밖 확인 다음 단계에 `python scalp_rotate.py`, 결과 커밋 목록에 `scalp_rotate.json`.
- 시험 3건(`tests.test_scalp.RegimeRotate`), 전체 OK, selfcheck·잠금 일치.

## 다음
- 순방향 14일 = 평가일 11/6~11/19 → 11/20 일일 보고에서 첫 판단. 일일 보고 C 표에 교대 줄 추가.
