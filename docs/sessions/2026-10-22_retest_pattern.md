# 2026-10-22 급상승·급하락 패턴 트랙 신설 (마일스톤 16, 제안함 P22)

> 사용자 요청: SNS 카드 4장("부자들은 여기서 진입" — 1차 상승 → 횡보 → 2차 상승 → 조정 → 지지 구간 상승 캔들 → 매수, 블룸에너지·팔란티어 예시)을 보고 "이런 패턴을 찾고 분석 후 진입할 수 있게 설계".
> 한 줄 결론: **규칙 코드 `retest.py` + 설계서 + 판정 연결까지 끝. 실데이터 백테스트는 아직 없다** — 이 클라우드 세션은 거래소 API 가 403 으로 막혀 가짜 봉으로만 돌렸다. 첫 실데이터는 `CARRYGATE daily` 다음 실행(또는 Run workflow → `dry_run`).

## 1. 만든 것

| 파일 | 내용 |
|---|---|
| `docs/RETEST_PLAN.md` | 설계서. 그림 → 숫자 규칙 표, 체결 가정, 합격선, 합격 뒤 "진입" 경로(별도 저장소·가상서버·1시간 주기) |
| `retest.py` | 1시간봉(업비트 현물 6 롱 / OKX 선물 6 롱+숏) 1년 백테스트 + 오늘 단계 + 순방향 집계. 숏은 가격을 뒤집어 롱 규칙 그대로. 변형 3개(`no_vol`, `zone_limit`, `target_2r`). `--fixture`, `--save-fixture`, `--report`, `--dry-run` |
| `tests/test_retest.py` | 자동 시험 19건: 1차 상승 판정(거래량·높이), 확인 봉 규칙 5가지, 거울 대칭(롱=숏 손익), 확인 봉 없으면 기한 만료, 지정가 변형, 거래량 조건 차단·변형 통과, 조정 거래량 증가 차단, 지지 깨짐 무효, 되돌림 50% 초과 탈락, 익절 변형, 손절, 한 계좌 캡, 신호 단계 3가지, 평가·통계, 규칙 사전, 기록 덮어쓰기 |
| `criteria.json` | `retest` 합격선(추세전환과 같은 틀: 20건, 손익비 ≥ 1.5 PASS, < 1.0 FAIL). sealed **2026-10-22**. 다른 네 트랙 숫자 변경 없음 |
| `verdict.py` | 다섯 번째 트랙으로 채점·이력·출력·잠금(`rules_dict`) |
| `rules_lock.json` | `python verdict.py --lock` 재생성. 대조 일치 |
| `selfcheck.py` | `retest.json` 모양 + `check_retest`(실현 순수익 재계산, 손절선 방향, 손절 체결가, 진입가 위치) + 기록 날짜 |
| `.github/workflows/carrygate.yml` | 추세 전환 다음 `python retest.py`, 추이 출력, 시험 목록, 커밋 목록 |
| `proposals.md` | P22 등록(트랙 추가, 기존 규칙 변경 없음) |
| `HANDOVER.md`, `CLAUDE.md`, `README.md` | 0절 표·자동 실행 표·마일스톤 16·시험 목록 |

## 2. 규칙 숫자 (결과 보기 전에 고정, `RULE_FIXED = 2026-10-22`)

1차 상승 ATR×3 이상·36봉 안·거래량 1.5배 → 횡보 4~60봉·되돌림 ≤ 50%·폭 ≤ H/2·거래량 ≤ 0.8배 → 종가 돌파 → 2차 고점 지지 상단 + ATR×1.5 (36봉 안) → 48봉 안 조정(거래량 ≤ 0.8배) → 지지 구간에 닿은 양봉(윗절반 마감) 종가 진입 → 손절 지지 하단, 래칫. 자세한 표는 설계서 1절.

## 3. 돌린 것

```
python -m unittest tests.test_retest                      # 19건 OK
python -m unittest (CLAUDE.md 목록 전체)                  # 190건 + 59건 OK
python retest.py --fixture <가짜 1년 봉 12시장> --dry-run  # 15초. 기본 0~1건, no_vol 164~347건
python verdict.py --lock && python verdict.py --dry-run   # 잠금 일치, retest 는 "retest.json 없음"(첫 실행 전)
python selfcheck.py                                       # 문제 없음
```
가짜 봉(무작위 걸음) 결과는 규칙의 강도만 알려 준다: 거래량 조건 3개가 거의 다 거른다. 실데이터에서 연 20건 미만이면 판정이 오래 "판단 전"이다 — 그래도 규칙은 안 고친다(설계서 3절).

## 4. 사용자 결정 필요 (선택지)

없음. 다만 알아 둘 것:
- **현황판 카드는 아직 안 올렸다.** 현황판은 라이브 버전 위에 고쳐야 하고(`FILES` + 갱신 Routine 프롬프트 둘 다), 첫 `retest.json` 이 생긴 뒤 올리는 게 맞다. 판정 카드(`verdict.json`)에는 자동으로 `retest` 줄이 들어간다 — 단, 현황판 `rVerdict` 가 `["turtle","reversal"]` 만 돌므로 **카드에 보이려면 그 목록에 `retest` 를 더해야 한다**(다음 세션, 라이브 버전에서).
- 실전 진입은 이 저장소 밖(`carrygate-exec`, 가상서버 1시간 주기). 순방향 20건 PASS 전에는 시작하지 않는다(설계서 5절).

## 5. Routine

변경 없음. `CARRYGATE daily`(매일 09:18 KST) 안에 단계가 들어갔다. 첫 실행 뒤 할 일: 실행 로그 "Retest pattern rules" 단계에서 수집 실패 0 확인 → `retest.json` 백테스트 표를 `HANDOVER.md` 마일스톤 16 에 옮김 → `python retest.py --save-fixture tests/fixtures/retest` 를 Actions 에서 한 번 돌려 오프라인 고정 봉을 저장(추세 전환과 같은 방식).
