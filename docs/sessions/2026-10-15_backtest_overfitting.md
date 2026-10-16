# 작업 기록 — 백테스트 과적합 논문 검토 (2026-10-15)

> 세션: https://claude.ai/code/session_01TMKJPdKuZ9yNShv1bxJD2j · 검토서 `docs/BACKTEST_OVERFITTING_REVIEW.md` · 제안함 P17·P18

## 한 일
- 댓글 권고("Bailey, Harvey, Prado, Hansen 논문을 보라")에 따라 논문 17편 서지·핵심 주장 정리. 수집은 하위 에이전트 2개(Sonnet), 해석·대입은 상위 모델.
- 논문 공식을 실데이터에 대입하는 스크립트 `scripts/overfit_check.py`(표준 라이브러리만) 작성·실행.
- 규칙·합격선·잠금 변경 없음. 제안함에 P17·P18 등록(대기).

## 숫자 (실데이터, 2026-10-15 기준)
- 초단타 후보 규칙은 실력 0 이어도 조합의 11~14% 를 후보로 뽑는다(378개 → 42~53개). 실제 후보는 6·11·3·5·1·0(7~17일). **10/7 PASS(후보 12개)는 잡음 바닥의 4분의 1이었다.**
- 터틀·추세전환 합격선(20건): 실력 0 인 규칙이 PASS 할 확률 19~40%. 95% 확신 건수: 터틀 업비트 55·OKX 299, 추세전환 업비트 16·OKX 268.
- 오늘 바뀌는 판정 없음(순방향 터틀 0·추세전환 1·돌파 6건).

## 사용자 결정
- 2026-10-15 "구현해" → P17·P18 둘 다 반영.

## 구현 (같은 날, 두 번째 커밋)
- `criteria.json`: `scalp.noise_floor {sims 500, seed 7, pass_only_if_candidates_above_median}`, sealed 2026-10-15(+sealed_history). 다른 트랙 합격선 숫자 변경 없음.
- `verdict.py`: `noise_floor()`(부호 뒤집기, scalp.py 의 같은 후보 규칙 적용), `judge_scalp` — 후보 ≤ 중앙값이면 NOT_YET(잡음 이하), FAIL 에도 잡음 바닥 기록. `interpret_pf_rule()`(이항분포 정확 계산 — 3절 모의 26/40/27/19% 와 일치)·`interpret_win_rate_rule()`, 각 거래소·돌파에 `interpretation`. 출력·이력 줄(`noise_median`)에 표시. 실행 시간 +1.3초.
- `rules_lock.json` 재생성(criteria 지문). `tests/test_verdict.py` 9건 추가(총 28건).
- 10/15 실행: 초단타 FAIL 그대로(후보 0, 잡음 바닥 중앙값 33·95% 43). 다른 트랙 NOT_YET 그대로.
- 현황판 카드에는 아직 안 올렸다(판정 파일·실행 출력에만). 올리려면 현황판 라이브 버전 위에서 `tracks.*.venues.*.interpretation`·`tracks.scalp.noise_floor` 를 읽게 고친다.

## 검증 한계
- 논문 PDF 가 이 세션 망에서 열리지 않아 서지는 확인, 본문 숫자는 저자 슬라이드·2차 자료 대조. 검토서에 "미확인" 표시.
