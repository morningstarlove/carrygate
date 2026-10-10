# CARRYGATE — 세션 시작 규칙 (모든 Claude Code 세션이 자동으로 읽는 파일)

> 이 파일은 "입구"다. 원본은 `HANDOVER.md`. 여기에는 세션이 매번 지켜야 할 것만 짧게 둔다. (2026-10-10 작성, `docs/AI_FRAMEWORKS_REVIEW.md` ④)

## 이 저장소는 무엇인가
매일 자동으로 시장을 읽어 **판단 근거만 기록**하는 장치. 주문 없음, API 키 없음, 읽기 전용. 외부 파이썬 패키지 0개(표준 라이브러리만).
실거래·주문·키는 **별도 저장소**(`carrygate-exec`, `docs/SCALP_LIVE_TEST_PLAN.md` 4절)로 분리한다.

## 읽는 순서 (긴 문서를 앞만 읽고 멈추지 않기 위해)
1. `HANDOVER.md` **0절 "지금 상태 한눈에"** — 무엇이 돌고, 무엇을 기다리고, 누가 결정할 것인지.
2. `HANDOVER.md` **5절 "원칙"** — 아래 절대 규칙의 원본.
3. `proposals.md` — 규칙 변경 제안함. 마지막 번호와 "대기" 항목을 본다.
4. 해당 트랙의 설계서 `docs/*_PLAN.md`, 최근 작업 기록 `docs/sessions/`.
`HANDOVER.md` 1~4절은 필요한 트랙 부분만 찾아 읽는다(120KB).

## 절대 규칙 (원칙 1~5 요약, 어기면 CI 가 막는다)
- **주문을 넣지 않는다. 키를 두지 않는다.** 지갑 주소(공개 정보)만 허용.
- **규칙·합격선은 결과를 보고 고치지 않는다.** 바꾸려면 `proposals.md` 등록 → 그 트랙 `RULE_FIXED` 새 날짜 → `python verdict.py --lock`. 이 셋이 한 PR. 변형은 `VARIANTS` 에 **추가만**, 새 변형은 더 추가하지 않는다(10/9 발견).
- **매매 판단에 AI 를 쓰지 않는다.** 판단은 고정 규칙 코드가 한다. AI 의 자리는 코드 작성·검산·이상 보고·문서·사람 결정 보조다(`docs/AI_FRAMEWORKS_REVIEW.md` 2절).
- **실데이터로 확인한 것만 사실로 적는다.** 수익률은 수수료를 뺀 뒤 말한다. 7일 평균 없이 진입 판정을 내리지 않는다.
- 백테스트 합격/불합격은 약한 근거다(유니버스 날짜로 뒤집힘). **순방향 기록만 믿는다.**

## 작업 방식
- 브랜치는 `main` 에서 따고 PR 로 합친다. 워크플로는 `main` 에서만 돈다.
- **역할 분담:** 설계·기획·판정 해석·규칙 제안은 세션의 최상위 모델이 직접. 자료 수집·형식 변환·반복 검산·긴 파일 훑기는 하위 에이전트(Agent 도구, 더 작은 모델)에 목표·금지 사항·읽을 파일을 명시해 맡긴다. 하위 에이전트는 규칙 상수·`criteria.json`·`rules_lock.json` 을 고치지 않는다.
- **마일스톤마다 인수인계서를 갱신한다.** `HANDOVER.md` 0절(상태)·해당 절, `docs/sessions/YYYY-MM-DD_*.md`(한 일·숫자·사용자 결정·Routine). 사용자는 코딩 지식이 없다 — 문서는 그 전제로 쓴다.
- 답은 핵심 문장으로 짧게. 질문은 최소화하고, 할 때는 선택지를 준다. 자체 시험을 거친 뒤 최종 보고 형태로.
- 결과 파일을 현황판 카드로 추가하면 현황판 `FILES` 와 갱신 Routine 프롬프트의 파일 목록 둘 다 고친다. 현황판은 **라이브 버전을 읽고 그 위에** 고친다.

## AI 도구 정책
- 쓰는 것: Claude Code 세션·Routine·Agent 도구, MCP(TradingView·GitHub), Graft·Agency Agents·Codebase Memory(선택, `bash scripts/install_ai_tools.sh`).
- **안 쓰는 것:** LangGraph·CrewAI·PydanticAI·Agno·Mastra 등 "AI 가 매 단계 판단하는 앱" 프레임워크. 이유와 재검토 조건은 `docs/AI_FRAMEWORKS_REVIEW.md`. 새 프레임워크·패키지를 들이려면 그 문서 4절 조건에 해당하는지 먼저 적는다.
- 자동(Auto) 모드에서는 외부 설치 스크립트가 막힌다. 재설치는 "Accept edits" 모드에서.

## 시험 (인터넷 불필요, 수정 뒤 반드시)
```
python -m unittest tests.test_scalp tests.test_research tests.test_midterm tests.test_momentum tests.test_live tests.test_turtle tests.test_reversal tests.test_relay tests.test_verdict tests.test_grid tests.test_selfcheck
python -m unittest tests.test_kr_universe tests.test_patterns tests.test_kr_breakout
python selfcheck.py
```
실데이터 확인은 GitHub Actions `CARRYGATE daily` → Run workflow → `dry_run`. 전체 목록은 `HANDOVER.md` 4절 "테스트".
