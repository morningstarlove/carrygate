# 2026-10-13 — 초단타 15일 판정 FAIL, 대안 규칙 추가 (P16)

**세션:** https://claude.ai/code/session_01NW6d8yMKTBLhW7RdeHRgsq (초단타 연구·7일 보고 담당)

## 한 일
- 진행 상황 확인: `verdict.py` **scalp FAIL**(누적 15일, 후보 0). 15분봉 VWAP 되돌림 18시장 합 7일 +19% → 15일 −91%. OKX XRP 15m VWAP 지정가 10/13 −4.5%, 누적 −2.2%.
- 사용자 결정 **"추천대로"** → (가) 마일스톤 5 "탈락 시 대안" 실행. 규칙 문턱값은 손대지 않고 **추가만**:
  - `scalp.py`: `TIMEFRAMES` 1·5·15 → 1·5·15·30·60. `WARMUP_MIN` 1440 → 3600(60분봉 VWAP 60봉). 밤 필터 규칙 `vwap_fade_lim_n`·`mr_z20_lim_n`(`NIGHT_KST` 21~02시, 진입만 제한). 하이퍼리퀴드 1분봉 5000봉 한도 → 이어받기.
  - `scalp_research.py`: C. 따라가기 지정가 종이 규칙 `follow_rule`(선행 |r|>2σ → 후행 같은 방향 지정가, `FOLLOW_TTL`=3, `FOLLOW_HOLD`=5, 현물은 롱만). `follow_by_pair` 를 research.json·이력·`--report` 에 기록.
  - 시험 5건 추가(밤 필터 2, 따라가기 3), 기존 시험 1건을 시간 단위 수에 안 묶이게 고침. 전체 151건 OK. `verdict.py --lock` 재생성(strategies 목록만 바뀜).
- 문서: `proposals.md` P16, `HANDOVER.md` 0절·마일스톤 5·3절, README.

## 숫자 (10/13 기준)
| | 7일(10/5) | 10일(10/9) | 15일(10/13) |
|---|---|---|---|
| 후보 수 | 5 | 6 | **0** |
| 15m VWAP 지정가 합(18시장) | +19.3% | ≈+19% | **−90.7%** |
| OKX XRP 15m VWAP 지정가 누적 | +5.4% | +4.1% | **−2.2%** |

## 사용자 결정
- 2026-10-13 "추천대로" = (가) 추가 규칙으로 계속, 소액 체결 시험(P13)은 시작하지 않음.

## 바뀌지 않은 것
- 기존 8규칙·1.5σ·z±2·3봉·60봉·합격선(`criteria.json`)·기존 1·5·15분 결과(준비 구간이 늘어도 지표 창이 이미 차 있음).

## 다음
- 10/14 기록부터 새 규칙 데이터. **10/21** `python scalp.py --report` / `python scalp_research.py --report` 로 첫 판단(7일). Routine trig_01WWuHJTvmZxK7GWpuvdSZca(10/21 16:30)가 그날 재확인한다.
- 선행 미해결(다른 세션 트랙): `selfcheck.py` 가 한국주식 `no_flag 278470` 손절 손실 −14.1% 를 경고(main 에서도 동일 — 갭 하락 추정, 이 PR 과 무관).
