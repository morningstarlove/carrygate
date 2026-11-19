# 2026-11-19 — 미국 주식 개장 범위 돌파 종이매매 (P40)

**세션:** https://claude.ai/code/session_011HUW4VU8sERjv8Vu9snS4X. 사용자가 SNS 로 본 저장소 `Jakeschincariol/day-trader-skill` 을 "확인하고 분석" → "진입 청산 판단해서 페이퍼 돌려보자".

## 외부 저장소 검토
- Claude 스킬 11개(스캔·뉴스·진입·청산·타임머신·리스크·수량·브로커·알림·실적·의회 공시). MIT, 파이썬 표준 라이브러리, 키 없음(야후·나스닥·SEC·하원 공시). 커밋 1개.
- 자체 예시(TSLA 59세션 33건: 목표 13·손절 15·미결 5)를 README 가 "동전 던지기 수준"이라고 적음. 수익률 주장 없음.
- 원칙과 충돌: AI 가 단계마다 판단, `trader-broker` 는 Alpaca 주문 → **설치 안 함.** 규칙만 차용.
- 저장소 연결(add_repo)이 권한에서 막혀 README 와 `trader-scanner`·`trader-entry`·`trader-exit` 의 SKILL.md 만 읽었다. 파이썬 코드는 못 읽음.

## 한 일
- `us_orb.py` — 스킬 문서의 기본값을 고정 코드로. 규칙 전문은 파일 머리말·`proposals.md` P40.
  - 스킬에서 안 가져온 것: 거래량 배수 확인(선택 기능), 5,943종목 전체 스캔(20종목 고정으로 대체), AI 판단, 주문.
  - 우리가 정한 것(스킬에 없음): 한 봉에 손절·목표 둘 다 닿으면 손절 먼저, 갭으로 손절을 지나면 시가 체결, 비용 체결당 0.05%, 종목 20개.
- 연결: daily 워크플로 단계·저장 목록, `verdict.py` 잠금(`us_orb`)·`rules_lock.json` 재생성, `selfcheck.py`(`check_us_orb`·형식·날짜), `tests/test_us_orb.py` 18건, `CLAUDE.md` 시험 목록.
- 카드 `docs/cards/p40_us_orb.svg` — NVDA 5분봉 11/11·11/12 실데이터, 실제 규칙 결과 두 건(−1.00%, +1.66%, 둘 다 15:55 정리).

## 숫자 (사후 계산 — 약한 근거, 규칙 고정 전 자료)
TradingView 정규장 5분봉 20종목 × 4,700봉(8/26~11/18, 60세션), `python us_orb.py --bars-dir <폴더> --backtest`:

| 항목 | 값 |
|---|---|
| 거래 | 639건 (세션당 약 10.7건) |
| 건당 순수익 | **−0.14%** (비용 전 −0.04%, 평균 −0.07R) |
| 합 | −89.8% |
| 승률 | 38.3% |
| t | −2.88 |
| 플러스 세션 | 20 / 60 |
| 전반 30세션 / 후반 30세션 | −0.11% (t −1.64) / −0.18% (t −2.39) |
| 청산 사유 | 목표 87건 +157.7% · 15:55 정리 265건 +56.7% · 손절 287건 −304.2% |
| 종목 | 플러스 2(ORCL +6.8, MSTR +5.0), 나머지 18개 마이너스(최저 AMZN −11.0) |

봉 원본 20개(각 약 200KB)는 저장소에 넣지 않았다(카드용 NVDA 이틀치만).

## 시험
- 단위시험 284건 + 64건 통과, `python selfcheck.py` 문제 없음.
- 검산 함수는 사후 639건을 임시 기록으로 넣어 문제 0건 확인(파일은 지움).
- **못 한 것:** 이 컨테이너에서 야후가 403 으로 막혀 `yahoo_5m` 실호출은 확인 못 함. 병합 뒤 Actions `CARRYGATE daily` → Run workflow → `dry_run` 의 "US opening-range breakout" 단계 로그로 확인할 것.

## 사용자 반응과 지금 멈춘 자리 (같은 날, 인수인계 시점)
- 사용자: "이거(스킬 11개) 가져와서 하는 것 맞아?" → 아니라고 답함(규칙 숫자만 빌려 새 코드로 씀) → **"이걸 안 가져올 거면 뭐 하러 해"**. 즉 사용자가 원한 것은 **원본 스킬을 실제로 가져와 돌리는 것**이었다. P40 은 그 요구를 채우지 못했다.
- 그 뒤 저장소 받기를 다시 시도해 **성공**: 공개 저장소라 `git clone --depth 1 https://github.com/Jakeschincariol/day-trader-skill` 로 읽기 가능(커밋 `3d0f4ef` 1개). 처음 막혔던 것은 "세션에 연결" 요청이었고, 익명 읽기는 된다. 받은 곳은 컨테이너 임시 경로라 **세션이 끝나면 사라진다 — 다음 세션은 다시 받아야 한다.**
- 받은 내용(파일 목록·줄 수만 확인, **코드 본문은 아직 안 읽음**): 스킬 폴더 11개(`trader-scanner/scan.py` 1,083줄, `trader-entry/entry.py` 960, `trader-timemachine/timemachine.py` 956, `trader-budget/size.py` 881, `trader-risk/check.py` 819, `trader-alerts/alerts.py` 787, `trader-exit/exit.py` 735, `trader-broker/ticket.py` 720, `trader-pelosi/ptr.py` 566, `trader-analyst/facts.py` 548, `trader-news/news.py` 546), 각 `SKILL.md`, `.claude-plugin/`, 오프라인 시험 `tests/`(고정 자료 포함).
- 여기서 사용자가 작업을 멈추고 인수인계를 요청. **원본 스킬 설치·실행은 시작하지 않았다.**

## 다음 세션이 할 일 (원본 스킬을 가져와 돌리기)
1. 저장소를 다시 받고, 11개 파이썬 코드를 읽어 검증한다(외부로 보내는 곳, 파일 쓰는 곳, 주문 경로). 저장소의 자체 시험 `python -m unittest discover tests` 를 오프라인으로 돌린다.
2. **어디에 둘지 사용자에게 한 번 묻는다(선택지):**
   - A. 별도 저장소(`carrygate-exec` 또는 새 저장소)에 11개 전부 설치 + Alpaca 모의계좌. 키는 사용자가 환경 설정에 넣는다. CARRYGATE 원칙(AI 판단 금지·주문 금지)과 충돌 없음. **추천.**
   - B. 이 저장소 `.claude/skills/` 에 `trader-broker` 만 빼고 10개 설치, 주문 없이 스킬 자체 기록 파일(`trader/*.json`)로 종이 기록. 원칙 3(매매 판단에 AI 안 씀)을 사용자가 명시적으로 풀어야 하고 `CLAUDE.md`·`docs/AI_FRAMEWORKS_REVIEW.md` 를 고쳐야 한다.
3. 돌리는 방법: 미국 장 시간(KST 23:30~06:00)에 Routine 이 세션을 깨워 `scan.py orb` → `entry.py` → `exit.py` → `check.py` 순으로 실행하고 결과를 기록. 이 클라우드 컨테이너는 **야후가 403 으로 막혀 있어** 환경 네트워크 허용 목록에 `query1.finance.yahoo.com` 등을 추가해야 스킬이 자료를 받는다(SEC·nasdaqtrader 도 확인 필요).
4. P40(`us_orb.py`)의 처리도 그때 함께 결정: 비교용으로 병합해 순방향 기록 / 버림.

## 사용자 결정
- 2026-11-19 "진입 청산 판단해서 페이퍼 돌려보자" → 종이 기록 트랙 신설(P40).
- 같은 날 "이걸 안 가져올 거면 뭐 하러 해" → 원본 스킬을 가져와 돌리는 쪽이 사용자 의도.
- **대기:** ① 원본 스킬 설치 위치 A/B(위) ② P40 브랜치를 병합할지 버릴지. P40 은 **아직 `main` 에 없다**(PR 없음, 브랜치 `claude/zen-ritchie-a35eox` 에만).

## 다음 (P40 을 살릴 경우에만)
- 병합 → dry_run 으로 야후 경로 확인 → 11/20 09:18 daily 가 11/19 세션을 첫 기록.
- **12/18 보고에서 20세션 첫 판단.** 후보면 `carrygate-exec` 에서 모의계좌 체결 시험 설계(사용자 결정).
- 선택: 현황판 카드 추가(`FILES` + 갱신 Routine 목록), 매일 점검 Routine 에 `us_orb.json` 한 줄.
