# 2026-11-06 예측 v2 구현 (제안함 P33 반영, 사용자 결정 "A")

## 사용자 결정
11/5 설계서 `docs/FORECAST_PLAN.md` 선택지 중 **A 구현 시작**.

## 만든 것
| 파일 | 내용 |
|---|---|
| `forecast.py` | 표준 라이브러리만. OKX 무기한 2시간봉 12종목(BTC ETH SOL XRP BNB DOGE LINK DOT ARB AAVE PEPE BONK) 358봉. ① 크기: rv12(24h 변동성) 3분위 → 과거 300시점 같은 분위의 12h \|수익\| 중앙값 ② 방향 D1(r12 분위 상승 비율)·D2(rv12·r12·BTC r12 분위가 같은 과거, 10개 미만이면 D3 대체)·D3(기준선) ③ 채점: 목표 봉이 닫히면 실제 수익·적중(p≠0.5)·Brier. `--dry-run`·`--fixture`·`--save-fixture`·`--report` |
| `forecast.json` / `forecast_history.jsonl` | 오늘 예측 + 누적 요약 / 하루 한 줄(채점이 과거 줄을 고치므로 전체를 다시 쓴다). `push_results.py` 가 날짜별로 합친다 |
| `verdict.py judge_forecast` | 여섯 번째 트랙. 잡음 바닥 = 예측을 (날짜,코인) 사이에서 500회 섞은 적중률 분포(seed 7). 크기 판정이 전체 판정, D1·D2 는 따로 PASS/FAIL |
| `criteria.json forecast` | sealed 2026-11-06. 60일, 결손 ≤6일, 크기 비율 PASS ≥1.20(앞뒤 각 ≥1.10)·FAIL <1.05, 방향 Brier 기준선 대비 0.005 |
| `rules_lock.json` | forecast 규칙 상수 포함해 재생성 |
| `tests/test_forecast.py` | 23건: 백분위·분위·누수 검사(뒤 봉을 지워도 예측 동일)·변동성 체제 분위·D2 대체·채점·요약·절반 분할·결손·판정 PASS/FAIL/NOT_YET·잡음 바닥 결정성·fixture 이틀 흉내 |
| `tests/fixtures/forecast/` | 실데이터 2시간봉 12종목 × 400봉(트레이딩뷰 1h → 2h, 10/2~11/4). 인터넷 없이 dry-run |
| `.github/workflows/carrygate.yml` | "Forecast v2" 단계(retest 뒤, verdict 앞), `--report`, 테스트 목록, 저장 목록 |
| `selfcheck.py` | `forecast.json` 모양·`forecast_history.jsonl` 날짜 검사 |
| 현황판 | 타일 "예측 v2 (12h 폭)", 카드(오늘 예측 표 + 누적 채점 표), 판정 카드에 줄. **라이브 버전을 읽고 그 위에** 고쳐 publish, `dashboard/index.html` 백업도 라이브와 맞춤(라이브에만 있던 수정 3곳 포함) |

## 한 장 카드 (11/8 추가, CLAUDE.md 11/7 규칙)
`docs/cards/p33_forecast.svg` — OKX BTC 2시간봉 실데이터(`docs/cards/data/okx_btc_2h.csv`)에 `forecast.predict_one` 을 세 시점에 돌려 예상 폭(점선)과 12h 뒤 실제 수익을 그렸다. `python track_card.py --bars docs/cards/data/okx_btc_2h.csv --spec docs/cards/p33_forecast.json --out docs/cards/p33_forecast.svg --last 84`. 번호는 main 과 두 번 겹쳐 P28→P31→P33 으로 옮겼다(닮은 과거 기각은 P32).

## 확인한 것
- fixture dry-run: 12/12 종목 예측. BTC 10/2~11/4 12h \|수익\| 중앙값 0.58%(조용한 달), 상위 분위 예상폭 0.578%.
- 이틀 흉내(봉 6개 빼고 예측 → 전체 봉으로 채점): 12건 채점, 적중·Brier 계산 확인.
- 누수 검사 통과, 단위시험 전체 통과, selfcheck 문제 없음.
- OKX 실데이터는 이 세션에서 접속 불가(000) — **병합 뒤 첫 daily 실행 로그 "Forecast v2" 단계에서 확인해야 한다.** 실패해도 continue-on-error 라 다른 단계는 돈다.

## 병합·실데이터 확인 (11/8)
- 사용자 결정 "니가 병합하고 진행해" → PR #95 를 `main` 에 병합(`9abe34f`). main 이 세 번 앞서가 제안 번호가 P28→P31→P33 으로 옮겨졌다(닮은 과거 기각 P32).
- `CARRYGATE daily` dry_run 실행 #89(main): **Forecast v2 단계 성공** — OKX 2시간봉 12/12 종목 예측(봉 2026-11-08 10:00 UTC, 전부 변동성 하위 분위, 예상 폭 BTC 0.53% ~ PEPE 2.08%, D2 ARB 는 닮은 상황 8개라 D3 로 대체). Verdict `[forecast] NOT_YET 채점 0일`, Unit tests·Selfcheck 성공. dry_run 이라 저장 안 함 — 첫 저장 11/9 09:18.

## 남은 것
1. ~~갱신 Routine 파일 목록에 `forecast.json` 추가~~ — 그 세션이 11/6 반영 완료.
2. 첫 daily 뒤 `forecast.json` 이 생겼는지, 다음 날 `scored_now` 가 12인지 점검 루틴이 보고.
3. 2027-01-05 전후 첫 판정. PASS 면 설계서 4절(포지션 크기 조절·문지기) 제안함 등록.

## Routine
변경 요청 1건(위 1). 새 Routine 없음.
