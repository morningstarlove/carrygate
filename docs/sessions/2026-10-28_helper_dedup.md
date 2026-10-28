# 2026-10-28 — 헬퍼 중복 정리 (`common.py` 신설)

**세션:** https://claude.ai/code/session_01TdCF5a1JbBXeb8zDM7YJWX
**규칙 변경:** 없음 (`rules_lock.json` 그대로, `criteria.json` 손 안 댐)
**사용자 지시:** "헬퍼 중복정리" (10/5 코드 분석에서 보고한 문제 ②)

## 무엇이 문제였나

- 같은 헬퍼가 파일마다 복사돼 있었다: `http_json` 7벌(변형 6종), `load_json` 5벌, `append_history` 7벌, `mean` 6벌, `r4` 6벌, `f` 6벌, `KST` 11벌, `UA` 9벌 등.
- 한 곳을 고치면 나머지는 안 고쳐지는 구조. 10/5 OKX 시간대 수정이 바로 그 예(같은 `okx_daily` 가 3벌이라 1벌만 고쳐져 있었다).
- `scalp.py`(초단타)가 사실상 공용 모듈이었다: 8개 모듈이 `from scalp import http_json, f, KST` 로 초단타 파일에 의존.

## 한 것

1. `common.py` 신설 — `KST`, `UA`, `http_json`, `f`, `r2`, `r4`, `mean`, `std`, `corr`, `load_json`, `append_history`, `load_history`. 표준 라이브러리만. 규칙 상수 없음.
2. 19개 모듈에서 **본문이 동일한** 복사본만 지우고 `from common import …` 로 바꿈. AST 로 본문 해시를 비교해 같은 것만 골랐다.
   - 지운 줄 359, 더한 줄 27 (+ `common.py` 약 110줄, `tests/test_common.py` 8건).
   - `scalp.py` 에서 가져오는 것은 이제 `COINS` 뿐 (basis·funding_signal·kimchi·retest·reversal·turtle).
3. **일부러 안 합친 것** (`common.py` 머리말에 같은 내용):
   - `orderbook.py http_json` — 재시도 없이 바로 실패. 초 단위 표본 간격을 지키기 위해.
   - `kr_universe.py http_json·f·append_history` — KRX 25초 타임아웃, NaN→None, `(path, rec)` 인자 순서(시험이 그 순서로 부른다).
   - `verdict.py load_json` — 저장소 기준 경로, 깨진 JSON 은 숨기지 않고 오류.
   - `kr_relay.py`·`vps_relay.py` — 한국 PC·Vultr 서버에서 저장소 없이 단독 실행. 복사본 유지가 맞다.
   - `kr_ohlcv.py UA` — 네이버·야후용 브라우저 UA. `etf_update.py UA` — Accept 헤더 없는 쪽, 그대로.
   - `fetch.py http_json` 만 재시도 대기 시간이 2·5·8초에서 공용의 1.5·3.5·5.5초로 바뀜. 결과값 무관.
4. 시험 목록에 `tests.test_common` 추가: `.github/workflows/carrygate.yml`, `CLAUDE.md`, `HANDOVER.md` 4절.

## 확인한 것

| 검사 | 결과 |
|---|---|
| `python -m unittest` 전체 (CLAUDE.md 두 줄 + test_retest + test_common) | 258건 전부 통과 |
| `python selfcheck.py` | 문제 없음 |
| 오프라인 dry-run 8종 (scalp·scalp_research·turtle·momentum·kr_universe·kr_breakout 백테스트·verdict·report) | 전부 종료 코드 0, 오류 없음 |
| 미정의 이름 정적 검사 (지운 헬퍼를 아직 쓰는 곳) | 0건 |
| 남은 "2파일 이상 본문 동일" 함수 | 8개 — 모두 트랙 모듈 안의 짧은 보조 함수(`rp`·`px`·`hl_1m`·`_mk`·`is_pivot_high`·`is_pivot_low`·`save_fixture`·`_bar_open_cutoff`). 트랙별로 따로 두는 게 맞아 안 건드림 |

실데이터 확인은 머지 뒤 Actions `CARRYGATE daily` `dry_run`.

## 다음에 손댈 수 있는 것 (이번엔 안 함)

- `okx_daily` 3벌(funding_signal·kimchi·turtle)은 인스트루먼트(현물/무기한)·반환 형식이 달라 그대로. 합치려면 인자로 구분해야 하고 터틀 규칙 파일을 건드리게 되므로 별도 PR.
- `report`·`print_summary`·`write_history` 는 모듈마다 출력 형식이 달라 중복이 아니라 각자 것.
