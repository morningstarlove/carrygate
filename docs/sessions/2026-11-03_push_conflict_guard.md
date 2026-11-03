# 2026-11-03 — 결과 저장 충돌 방지 (`push_results.py`)

**사용자 지시:** "충돌 방지 실행". 같은 날 현황판 갱신 루틴이 daily 실패를 발견해 보고한 데 대한 답.

## 사고
- 실행 #82 (CARRYGATE daily, 예약 00:18 UTC → 실제 06:52 UTC 시작, 6.5시간 지연) 저장 단계에서
  `CONFLICT (content): verdict.json`, `verdict_history.jsonl` → `git push` 실패(exit 128). 결과 43개 파일 미저장.
- 원인: 한국 주식 워크플로(kr_breakout.yml)가 07:14 UTC 에 같은 두 파일을 먼저 올렸다(23dc068).
  두 워크플로가 모두 `verdict.py` 를 돌려 판정 파일을 쓰고, 저장은 `git pull --rebase || true; git push` 라 충돌이면 그대로 실패했다.
- 계산 오류는 아니었다(같은 실행의 검산 "문제 없음"). 17:59 KST 수동 재실행(a7ec3cf)으로 그날 결과 복구.

## 한 일
- `push_results.py` 신설(표준 라이브러리만): 있는 경로만 커밋 → 원격을 당겨 그 위로 옮김 → 충돌 나면 기계적으로 해결 → 올림.
  - `*.jsonl` 기록: 양쪽 줄 합침. 같은 날짜(`reversal_bars` 는 같은 `bar`)는 지금 올리는 쪽, 날짜순 정렬. 어느 쪽 기록도 버리지 않음.
  - 그 밖의 결과 파일: 방금 계산한 값.
  - `--verdict`: 합친 뒤 `verdict.py` 로 판정을 최신 입력 전체로 다시 계산해 같은 커밋에 넣음.
  - 푸시가 밀리면 다시 당겨서 최대 5번. 끝내 못 올리면 종료코드 1(빨간불 유지 — 조용히 넘어가지 않음).
- 워크플로 4개 저장 단계 교체: `carrygate.yml`(--verdict), `kr_breakout.yml`(--verdict), `orderbook.yml`, `reversal_4h.yml`.
  `kr_breakout.yml` 의 백테스트·fixture 커밋, 수동 전용 `probe.yml` 은 그대로.
- `tests/test_push_results.py` 7건: jsonl 합치기 3건 + 임시 원격 저장소로 ① 11/3 과 같은 판정 파일 충돌 ② 당겨온 직후 다른 쪽이 또 올려 푸시가 밀리는 경우 ③ 변경 없음 ④ 평상시.
  daily 단위시험 목록과 `CLAUDE.md` 시험 목록에 추가.

## 확인
- 11/3 실데이터 재현: 원격 = 23dc068(한국 주식이 먼저 올린 상태), 작업본 = 41d05df(daily 시작 시점) + daily 결과.
  - 예전 방식: `CONFLICT verdict.json`, `verdict_history.jsonl` 그대로 재현.
  - 새 방식: 두 파일 자동 해결 → 판정 재계산 → 1번째 시도에 올라감(3.6초). 결과가 실제 재실행 커밋 a7ec3cf 와 `generated_at_utc` 한 줄만 다르고 같다.
    한국 주식 파일은 23dc068 그대로 보존. 그 결과로 `selfcheck.py` "문제 없음".
- 전체 단위시험·`selfcheck.py`·`verdict.py --check-lock` 통과.

## 바뀌지 않은 것
- 규칙 상수·`RULE_FIXED`·`criteria.json`·`rules_lock.json` 그대로. 저장 방법만 바뀌었다.
- 예약 시각도 그대로. GitHub 예약 지연(이번엔 6.5시간)은 우리가 막을 수 없어서, 겹쳐도 안전하게 만드는 쪽을 택했다.

## 남은 확인
- 실제 워크플로에서의 첫 작동은 다음 예약 실행 로그(호가창 22:00 KST, 추세 전환 21:07 KST, daily 다음 날)의 "올림: …" 줄로 본다.
