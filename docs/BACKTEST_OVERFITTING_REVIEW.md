# 백테스트 과적합 논문 검토 — Bailey · López de Prado · Harvey · Hansen (2026-10-15)

> 계기: 댓글 "Bailey, Harvey, Prado, Hansen 등이 백테스팅 오버피팅에 대해 쓴 논문들을 찾아보라". 논문을 찾아 읽고, 그 공식을 **우리 실데이터**(`scalp_history.jsonl`, `verdict.json`, `criteria.json`)에 대입했다. 계산 스크립트 `scripts/overfit_check.py`(표준 라이브러리만, 인터넷 불필요).
> 검증 범위: 서지(저자·연도·학술지·DOI)는 확인했다. 논문 본문 PDF 는 이 세션 망에서 열리지 않아 공식·숫자는 저자 슬라이드·2차 자료로 대조했다. "미확인" 표시는 그 뜻이다.

## 0. 한 줄 결론

**논문들의 결론은 이미 우리 원칙(사전 등록·순방향만 신뢰·변형 추가만)과 같은 방향이다. 다만 두 군데가 비어 있다.** ① 초단타는 조합 378→900개를 동시에 시험하는데 "운만으로 몇 개가 후보로 뜨는가"를 재지 않았다. 재 보니 10/7 의 PASS(후보 12개)는 **운만으로 기대되는 후보 수(약 47개)보다 적었다** — 처음부터 실력의 증거가 아니었다. ② 터틀·추세전환 합격선(20건에 손익비 1.2·1.5)은 **실력 0 인 규칙도 19~40% 확률로 PASS** 한다. 20건은 "망했는지"를 볼 수는 있어도 "되는지"를 확정할 수 없다. 둘 다 규칙을 고치자는 게 아니라 **PASS 를 읽는 법**의 문제다(제안함 P17·P18, 사용자 결정).

## 1. 논문 목록

| # | 논문 | 핵심 주장 | 우리에게 뜻하는 것 |
|---|---|---|---|
| 1 | Bailey·Borwein·López de Prado·Zhu (2014) *Pseudo-Mathematics and Financial Charlatanism*, Notices of the AMS 61(5) 458-471. [PDF](https://www.ams.org/notices/201405/rnoti-p458.pdf) | 실력 0 인 전략 N 개를 시험하면 그중 최고의 백테스트 샤프는 N 이 늘수록 커진다. 기대 최댓값 ≈ (1−γ)·Z⁻¹(1−1/N) + γ·Z⁻¹(1−1/(N·e)). 2년 자료면 **7번**만 시도해도 샤프 1 짜리 "전략"이 나온다. 5년이면 45번. 시도 횟수를 안 밝힌 백테스트는 읽을 수 없다 | 초단타 378개 조합의 기대 최댓값은 표준편차의 **2.97배**, 900개면 3.23배(2절) |
| 2 | Bailey·López de Prado (2014) *The Deflated Sharpe Ratio*, J. Portfolio Mgmt 40(5) 94-107, DOI 10.3905/jpm.2014.40.5.094. [PDF](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf) | 샤프에서 "시도 횟수 N 만큼 운으로 나올 값"을 뺀 뒤 그래도 0 보다 큰 확률(DSR). 입력: N, 시도들의 샤프 분산, 기록 길이 T, 왜도·첨도 | 판정에 **N 을 넣어야** 한다. 우리는 N 을 기록은 하지만(378개) 합격선에 쓰지 않는다 |
| 3 | Bailey·López de Prado (2012) *The Sharpe Ratio Efficient Frontier*, J. Risk 15(2). [PDF](https://www.davidhbailey.com/dhbpapers/sharpe-frontier.pdf) | 확률적 샤프(PSR)와 **최소 기록 길이(MinTRL)**: 샤프가 0 보다 크다고 95% 확신하려면 기록이 몇 개 필요한가 ≈ (1.645 / 거래당 샤프)² | 터틀 업비트는 **55건**, OKX 는 299건, 추세전환 업비트 16건, OKX 268건(3절). 합격선 20건은 업비트 추세전환 하나만 충분하다 |
| 4 | Bailey·Borwein·López de Prado·Zhu (2017) *The Probability of Backtest Overfitting*, J. Computational Finance 20(4), DOI 10.21314/jcf.2016.322. [PDF](https://escholarship.org/uc/item/4w1110bb) | CSCV: 기간을 S 조각으로 나눠 절반 조합 전부(S=16 이면 12,870가지)에서 "표본 안 1등이 표본 밖에서 중앙값 아래로 떨어질 확률"(PBO) | 우리는 CSCV 대신 **시간 순방향**(RULE_FIXED 이후만)을 쓴다. 더 느리지만 더 정직하다. 초단타 "앞·뒤 반나절 모두 플러스"(견고 조건)는 PBO 의 축소판 |
| 5 | Harvey·Liu·Zhu (2016) *… and the Cross-Section of Expected Returns*, Rev. Financial Studies 29(1) 5-68, DOI 10.1093/rfs/hhv059 | 논문 313편의 요인 316개를 다중검정으로 다시 보면 대부분 거짓. 새 발견은 **t > 3.0**(샤프 × √연수 > 3) 이어야 한다 | t>3 을 거래 수로 바꾸면 터틀 182~993건, 추세전환 53~891건(3절). 20건으로는 어떤 트랙도 이 문턱에 못 간다 |
| 6 | Harvey·Liu (2015) *Backtesting*, J. Portfolio Mgmt 41(1) 13-28, SSRN 2345489. [코드](https://people.duke.edu/~charvey/backtesting/) | "샤프를 반으로 깎아 읽어라"는 관행은 틀렸다. 깎는 폭은 시도 횟수에 따라 비선형 — 아슬아슬한 전략은 **거의 0 까지**, 아주 좋은 전략은 조금 | 초단타처럼 플러스가 겨우 나오는 조합은 깎고 나면 0 이다 |
| 7 | Harvey·Liu (2014) *Evaluating Trading Strategies*, J. Portfolio Mgmt 40(5) 108-118 | 예: 샤프 0.92(t=2.91) 전략이 단일 검정에선 유의하지만 다중검정 보정 뒤 무의미 | 결과를 보고 "좋아 보이는 것"을 고르는 순간 보정이 필요하다 |
| 8 | Harvey·Liu (2020) *False (and Missed) Discoveries in Financial Economics*, J. Finance 75(5) 2503-2553, DOI 10.1111/jofi.12951. [arXiv 2006.04269](https://arxiv.org/abs/2006.04269) | 가짜 발견(1종 오류)만 막으면 **진짜를 놓친다**(2종 오류). 둘을 같이 재서 문턱을 정해야 한다. 지금 방법들은 잘하는 운용자를 가려낼 힘이 없다 | 3절: 터틀 OKX 는 백테스트만큼 실력이 있어도 20건에서 42% 확률로 PASS 를 못 받는다. 문턱만 올리면 이 숫자가 더 나빠진다 |
| 9 | Harvey (2017) *The Scientific Outlook in Financial Economics*, J. Finance 72(4) 1399-1440 (학회장 연설) | p-해킹, 안 알리는 시험, 발표 편향. 효과가 드문 영역에선 t>3 도 가짜가 많다. p 값 대신 최소 베이즈 팩터 | "되는 전략은 드물다"는 사전 확률을 깔고 읽는다 |
| 10 | White (2000) *A Reality Check for Data Snooping*, Econometrica 68(5) 1097-1126 | 여러 모델을 찾아본 뒤 "최고"가 기준선을 이기는지 **탐색 전체**를 부트스트랩으로 검정 | 2절의 부호 뒤집기 시험이 이 방식의 가장 단순한 형태 |
| 11 | Hansen (2005) *A Test for Superior Predictive Ability*, J. Business & Economic Statistics 23(4) 365-380 | White 검정은 형편없는 후보가 많이 섞이면 힘이 떨어진다. 후보별로 표준화하고 명백히 나쁜 것은 빼서 고침(SPA) | 900개 중 대부분이 비용에 죽는 조합이라 White 식 그대로는 너무 관대하다 — 2절에서 "관대한 귀무가설"이라고 적은 이유 |
| 12 | Sullivan·Timmermann·White (1999) *Data-Snooping, Technical Trading Rule Performance, and the Bootstrap*, J. Finance 54(5) 1647-1691 | 기술적 매매 규칙 수천 개(7,846개로 알려짐, 미확인)를 다우 100년에 적용. 1986년까지는 최고 규칙이 보정 뒤에도 유의, **1987~1996 에선 사라짐** | 과거 데이터로 찾은 규칙은 시대가 바뀌면 죽는다. 순방향 기록이 필요한 이유 |
| 13 | Romano·Wolf (2005) *Stepwise Multiple Testing as Formalized Data Snooping*, Econometrica 73(4) 1237-1282 | White 보다 힘이 센 단계식 다중검정(집단별 오류율 통제) | 참고 |
| 14 | Hansen·Lunde·Nason (2011) *The Model Confidence Set*, Econometrica 79(2) 453-497 | "최고 하나"를 고르지 말고 **최고를 포함하는 집합**을 신뢰구간처럼 만든다. 자료가 약하면 집합이 커진다 | 초단타 후보 12→6→2→0 은 자료가 약해 집합이 사실상 전부였던 것 |
| 15 | López de Prado (2018) *Advances in Financial Machine Learning* 11·12·14장; *The 10 Reasons Most Machine Learning Funds Fail*, J. Portfolio Mgmt 44(6) | "백테스트는 연구 도구가 아니다"(11.4). 흠 없는 백테스트도 아마 틀렸다(11.3). 정제 교차검증(CPCV) | 우리 원칙 5 와 같다 |
| 16 | Bailey·López de Prado (2021) *Finance Is Not Excused* (SSRN); 축약판 Significance 18(6) 22-25 | 금융 연구가 통계 기본을 어기는 분야라는 재확인 | — |
| 17 | López de Prado·Lewis (2019) *Detection of False Investment Strategies Using Unsupervised Learning*, Quantitative Finance 19(9) | 서로 닮은 시도들을 묶어 **실질 시도 횟수**를 센다 | 378개 조합은 18시장 × 7규칙 × 3봉이라 서로 닮았다. 실질 N 은 378 보다 작지만 45 보다는 훨씬 크다 |

공개 도구: Bailey 의 과적합 시연 도구 [carmamaths.org/resources/backtest](https://carmamaths.org/resources/backtest), R `pbo` 패키지(CSCV, 제3자), Harvey 의 haircut 코드(위 링크). 우리는 외부 패키지를 안 쓰므로 필요한 계산은 `scripts/overfit_check.py` 에 표준 라이브러리로 뒀다.

## 2. 초단타에 대입 — 후보 수가 "운만으로 나올 수"보다 적었다

초단타 후보 규칙(`scalp.py cumulative_candidates`): 7일 이상 기록 + 누적 순수익 > 0 + 플러스 일수 60% 이상 + 같은 시장·봉의 기준선(무작위 5봉 보유)보다 나음.

**잡음 바닥 시험(White 2000 방식의 가장 단순한 형태):** 실제 기록 17일치에서 조합마다 날짜별 순수익의 **부호를 무작위로 뒤집어**(실력 0·비용 0 인 세상) 같은 후보 규칙을 적용, 2,000번 반복해 후보가 몇 개 뜨는지 셌다.

| 누적 일수 | 조합 수 | 실제 후보 | 운만으로 기대(중앙값 / 95%) | 실제 ÷ 운 |
|---|---|---|---|---|
| 7 | 378 | 6 | 42 / 54 | 0.14 |
| 9 | 378 | 11 | 47 / 59 | 0.23 |
| 11 | 378 | 3 | 49 / 62 | 0.06 |
| 13 | 378 | 5 | 52 / 64 | 0.10 |
| 15 | 378 | 1 | 53 / 66 | 0.02 |
| 17 | 378 | 0 | 32 / 42 | 0.00 |

읽는 법:
- 실력이 없어도 **후보 규칙은 조합의 11~14% 를 후보로 뽑는다.** 378개면 40~50개.
- 10/7 PASS 때 후보 12개(여기선 11개)는 그 **4분의 1**. 실제 조합들은 "실력 0" 보다 못했다 — 비용이 먹어서다. **PASS 는 처음부터 실력의 증거가 아니었다**(P10 에 "일부는 우연" 이라고 적었지만, 전부가 우연 이하였다).
- 이 귀무가설은 관대하다(Hansen 2005 의 지적). 비용까지 넣으면 "운만으로 기대"는 더 작아지지만, 결론 방향은 같다: 후보 수가 잡음 바닥을 넘은 적이 없다.
- 900개로 늘린 뒤(P16)에도 같다. 조합을 늘릴수록 운으로 뜨는 후보가 늘어난다(Bailey 2014, N=900 → 기대 최댓값 3.23σ).

**따라서:** 초단타 PASS 는 "후보 수 > 운만으로 기대되는 후보 수(중앙값)" 일 때만 PASS 로 읽어야 한다. 이것이 **제안 P17**.

## 3. 순방향 합격선에 대입 — 20건은 "망함"은 보여도 "됨"은 못 보여 준다

`criteria.json` 합격선을 백테스트 승률·손익비 모양 그대로 두고, 승률만 "기대손익 0" 이 되게 낮춘 귀무가설에서 2만 번 모의(스크립트 3절).

| 트랙·거래소 | 백테스트 승률 / 손익비 | 합격선 | 실력 0 인데 PASS(1종 오류) | 백테스트만큼 실력 있는데 PASS 못 함(2종 오류) | 거래당 샤프 | 95% 확신에 필요한 건수(MinTRL) | t>3 건수 |
|---|---|---|---|---|---|---|---|
| 터틀 업비트 | 37.3% / 1.65 | PF≥1.2 @20건, z≥−2 | **26%** | 34% | 0.22 | 55 | 182 |
| 터틀 OKX | 34.9% / 1.23 | 같음 | **40%** | 42% | 0.10 | 299 | 993 |
| 추세전환 업비트 | 37.7% / 2.82 | PF≥1.5 @20건 | **27%** | 8% | 0.41 | 16 | 53 |
| 추세전환 OKX | 33.5% / 1.25 | 같음 | **19%** | 66% | 0.10 | 268 | 891 |
| 한국 주식 돌파 | 29.4% | 승률≥20% @15건 | 승률이 **반토막**(14.7%)이어도 38% 확률로 PASS | 백테스트대로면 86% PASS | — | — | — |

읽는 법:
- 가짜 PASS 가 20~40% 다. 통계 관행(5%)의 4~8배. **PASS 하나로 "되는 규칙"이라고 말할 수 없다.**
- 그렇다고 문턱을 올리면 2종 오류가 커진다(Harvey·Liu 2020). 터틀 OKX 는 백테스트가 맞아도 42% 확률로 떨어진다. 백테스트 손익비 1.2~1.25 짜리는 **수백 건 전에는 어느 쪽으로도 판정이 안 나는 트랙**이다.
- 유일하게 20건이 충분한 것은 추세전환 업비트(백테스트 PF 2.8). 단, 그 2.8 자체가 유니버스·기간에 따라 뒤집힐 수 있다(10/9 발견).
- 한국 주식 돌파의 PASS 조건(승률 ≥ 20%)은 "수익" 검정이 아니라 "백테스트 모양이 유지되는가" 검정이다. 설계 의도와 맞지만, PASS 를 "돈이 된다"로 읽으면 안 된다.
- 지금 순방향은 터틀 0건·추세전환 1건·돌파 6건이라 **오늘 바뀌는 판정은 없다.**

**따라서:** 합격선 숫자는 그대로 두되, 판정 파일에 가짜 PASS 확률과 MinTRL 을 같이 적고, PASS 의 뜻을 "소액 시험 후보"로 고정한다(이미 `criteria.json status_meaning` 이 그렇게 적혀 있다). 실전 투입 결정은 MinTRL(55건·16건 등) 이후로 미룬다. 이것이 **제안 P18**.

## 4. 이미 논문대로 하고 있는 것

| 논문의 처방 | 우리 장치 |
|---|---|
| 합격선을 결과 전에 봉인(사전 등록) | `criteria.json` sealed 2026-10-06, `rules_lock.json`, CI |
| 시도 횟수를 기록하고 공개 | `scalp.json rows_total`(378→900), `VARIANTS` 목록이 잠금에 포함 |
| 결과 보고 규칙 조정 금지 | `proposals.md` 절차, 변형은 추가만, 새 변형 추가 중단(10/9) |
| 표본 밖(순방향) 성적만 신뢰 | `RULE_FIXED` 이후 매매만 집계, 백테스트 합격선은 약한 근거로 격하(10/9) |
| 기준선(무작위)과 비교 | 초단타 `baseline_hold5`, 돌파 대조군 `no_sector` |
| 운 보정 | `verdict.py luck_z` (백테스트 승률 대비 실제 승수) |
| 비용 뺀 수익만 말함 | 원칙 4, 초단타 `hurdle`(본전 승률) |

## 5. 비어 있는 것과 제안 (규칙 변경 아님 — 읽는 법·표시 추가)

| 제안 | 내용 | 바뀌는 것 |
|---|---|---|
| **P17** 초단타 잡음 바닥 | `verdict.py` 초단타 판정에 "부호 뒤집기 후보 수 중앙값"을 같이 계산해 적고, 실제 후보 수가 그 중앙값 이하면 PASS 대신 **NOT_YET(잡음 이하)** | `criteria.json scalp` 에 항목 추가 → 사전 등록 절차(잠금 갱신). 10/7 같은 PASS 가 다시 뜨지 않는다 |
| **P18** 합격선 해석 | 판정 파일 각 트랙에 `false_pass_pct`·`min_trades_95` 를 적는다. 합격선 숫자는 그대로. PASS = "소액 시험 후보", 실전 규모 결정은 MinTRL 이후 | 표시 추가만. 규칙·합격선·잠금 변경 없음 |
| 하지 않을 것 | 합격선 문턱 올리기, 조합 더 늘리기, CSCV/PBO 도입(외부 패키지·복잡도 대비 얻는 게 적다 — 순방향이 이미 그 역할) | — |

**2026-10-15 사용자 결정 "구현해" → 둘 다 반영.** P17: `criteria.json scalp.noise_floor`(부호 뒤집기 500회, seed 7), `verdict.py noise_floor`·`judge_scalp`. P18: `verdict.py interpret_pf_rule`(이항분포 정확 계산, 3절 모의와 일치)·`interpret_win_rate_rule`, 판정 파일 `interpretation`. criteria sealed 2026-10-15, 잠금 재생성, 시험 `tests/test_verdict.py` 9건 추가. 10/21 재확인은 잡음 바닥 기준으로 나온다.

## 6. 다시 계산하는 법

```
python scripts/overfit_check.py .
```
인터넷 불필요, 약 1분. 1절(기대 최댓값)·2절(잡음 바닥)·3절(가짜 PASS·MinTRL) 표를 그대로 찍는다. 기록이 쌓이면 숫자가 바뀐다.
