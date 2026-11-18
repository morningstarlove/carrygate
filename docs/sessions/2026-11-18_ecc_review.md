# 2026-11-18 ECC 최소 적용 + 백테스트·평가 코드 검토

> 사용자 지시: ECC 플러그인을 최소 구성으로 적용 → 설치 점검, 시크릿 스캔, `/code-review`·`mle-reviewer` 검토(미래참조·데이터 누수·체결가), 테스트 먼저 쓰고 수정, 저장·인수인계.

## 1. 설치 상태
- 클라우드 세션에는 `/plugin` 명령이 없다 → 마켓플레이스 등록 불가. ECC 는 **설치돼 있지 않았고 중복도 없다**(`~/.claude/plugins` 에 ECC 없음, 저장소에 흔적 없음).
- 최소 구성: ECC v2.2.3(커밋 3f14a10, MIT) 에서 **`agents/mle-reviewer.md` 한 파일만** `.claude/agents/` 에 둠. 에이전트 72·스킬 327·훅은 들이지 않음(`docs/AI_FRAMEWORKS_REVIEW.md` 5절 표에 기록).
- `tdd-workflow`(JS 위주 23KB)·`save-session`(저장 위치가 세션과 함께 사라지는 `~/.claude/session-data`)은 **절차만 따름**: 실패하는 시험 먼저 → 수정 → 전체 시험, 저장은 이 파일과 `HANDOVER.md`.

## 2. 시크릿 스캔 (AgentShield 1회 실행, 설치 없음: `npx -y ecc-agentshield scan --path . --supply-chain`)
- **시크릿 100점 — API 키·토큰 노출 0건.** 자체 패턴 검색(AWS·OpenAI·GitHub·Slack·텔레그램·개인키·64자리 hex, 키=값 대입)도 0건. 워크플로에 `secrets.` 참조 없음.
- 한계: 이 세션의 저장소 사본은 최근 51개 커밋만 있다(얕은 복제) — 그보다 오래된 이력은 못 봤다.
- 종합 C(73점)의 나머지는 설정 지적: ① `CLAUDE.md` 에 "프롬프트 방어 문구" 없음(문구 유무만 보는 검사 — 안 고침) ② `.claude/settings.json` 허용 규칙 `Bash(node dist/cli.js:*)`(이 저장소에 없는 파일, Graft 설치 잔재) → **삭제함** ③ 거부 목록·PreToolUse 훅 없음(안 고침, 주문·키가 없는 저장소라 위험 낮음).

## 3. 코드 검토 결과 (`/code-review` high, 10건)
**고친 것 3건 (P39)** — 시험 먼저 써서 실패 확인 뒤 수정:
| # | 파일 | 문제 | 시험 |
|---|---|---|---|
| 1 | `patterns.py` `scan` | i 번째 날 판정에 i+1, i+2 봉을 읽음(삼각수렴 피벗). 백테스트만 영향, fixture 18일 중 5일 달랐음 | `test_patterns::test_scan_never_reads_bars_after_i` |
| 2 | `scalp_research.py` `funding_event_study` | 정산 전후 수익률 1분 어긋남 | `test_research::test_first_minute_after_settlement_is_post_not_pre` |
| 3 | `scalp_research.py` `follow_rule` | 문턱 σ 를 평가일 전체(미래 포함)로 계산 → 평가일 이전 준비 구간으로만 | `test_research::test_threshold_uses_only_bars_before_eval_window`, `test_no_signal_without_prep_window` |

**안 고치고 사용자 결정으로 올린 것 7건 (P40, 대기)** — 순방향 트랙의 체결 논리·기준선이라 고치면 기록을 다시 세야 한다: scalp 청산 지정가 4봉, reversal·retest 소급 진입, reversal·retest 확정 손실 누락, turtle 진입일 손절 미확인, verdict 기준 승률에 순방향 섞임, momentum 월중 신호, 한국 주식 거래세 0.15%(2026년 세율 확인 필요). 이 7건은 검토 도구 보고 수치이고 메인 세션이 직접 재확인한 것은 위 3건뿐이다.

`mle-reviewer`(나머지 14개 파일 대상): 이 파일 작성 시점에 실행 중 — 결과는 아래 5절에 덧붙인다.

## 4. 시험
- 수정 전: 새 시험 5개 실패(재현). 수정 후: `CLAUDE.md` 시험 목록 268 + 65 = **333개 통과**, `python selfcheck.py` 문제 없음, `python verdict.py --check-lock` 일치.

## 5. 다음에 할 일
- **사용자 결정: P40** (A 고치고 다시 셈 / B 보조 기록 / C 주의 문구).
- 한국 주식 백테스트 재실행(Actions `kr_breakout` → `backtest` 체크) — 수정된 판정기로 `kr_backtest.json` 갱신.
- `research_history.jsonl` 의 A(펀딩)·C(따라가기)는 11/18 실행분부터 새 계산 — 이전 값과 직접 비교 금지.
- Routine 변경 없음.
