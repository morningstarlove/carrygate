# 트랙 카드 (한 장 그림)

새 트랙·규칙을 시작할 때 **반드시** 한 장의 SVG 로 남긴다(사용자 지시 2026-11-07).
한 장에 들어가야 하는 것: 실데이터 캔들 · 진입/청산 지점 · 어떤 논리로 들어가는가 · 목표 · 기대값(수수료 뺀 뒤) · 결과 예측 · 틀리면 어떻게 되나 · 다음 판정일.
카드 없는 트랙 추가는 미완성이다.

## 목록

| 번호 | 카드 | 규칙 | 봉 데이터 | 만든 날 | 다음 판정 |
|---|---|---|---|---|---|
| P30 | [p30_rotate.svg](p30_rotate.svg) | 국면 교대 `scalp_rotate.py` | `data/okx_xrp_30m.csv` (OKX XRP 무기한선물 30분봉, 2026-10-31~11-07 UTC, TradingView 수집) | 2026-11-07 | 2026-11-20 |
| P31 | [p31_mm_wide.svg](p31_mm_wide.svg) | 호가 제공 넓은 호가폭 시장만 `orderbook.py WIDE_SPREAD_BP` | `data/upbit_trx_1m.csv` (업비트 TRX/KRW 1분봉, 2026-11-07 16:22~11-08 02:01 UTC, TradingView 수집) | 2026-11-08 | 2026-11-15 |
| P34 | [p34_rotate60.svg](p34_rotate60.svg) | 국면 교대 보조: 60분봉만 교대 vs 되돌림 고정 `scalp_rotate.py aux60` | `data/okx_xrp_60m.csv` (OKX XRP 무기한선물 60분봉, 2026-10-27~11-08 UTC, TradingView 수집) | 2026-11-08 | 2026-11-22 |
| P35 | [p35_rev_after_loss.svg](p35_rev_after_loss.svg) | 되돌림은 전날 진 날에만 `scalp_rotate.py aux_after_loss` | `data/okx_btc_60m.csv` (OKX BTC 무기한선물 60분봉, 2026-11-03~11-13 UTC, TradingView 수집) | 2026-11-13 | 2026-11-26 |
| P36 | [p36_macd_divergence.svg](p36_macd_divergence.svg) | MACD 하락 다이버전스 카드 검증(기록만, 트랙 아님) `scripts/macd_div_check.py` | `data/amex_spy_1d.csv` (SPY 일봉, 2025-05-01~11-28, TradingView 수집, `data/bb_reentry/AMEX_SPY.csv` 에서 자름) | 2026-11-13 | — (P36 종결, 기록만) |
| P33 | [p33_forecast.svg](p33_forecast.svg) | 예측 v2 `forecast.py` (크기 예측 + 방향 채점) | `data/okx_btc_2h.csv` (OKX BTC 무기한선물 2시간봉 — TradingView 1h 실데이터를 2h 로 합침, 2026-10-02~11-04 UTC) | 2026-11-08 | 2027-01-05 전후 (채점 60일) |

## 만드는 법

1. 봉 CSV 를 `docs/cards/data/<거래소_종목_봉>.csv` 로 저장 (열: `t,o,h,l,c,v`, `t` 는 UTC 초). 실데이터만 쓴다.
2. 설명 JSON 을 `docs/cards/<번호_이름>.json` 으로 쓴다. 키: `title, subtitle, venue, preset_tf, preset_strategies, logic(목록), entry, exit, target, expected, prediction, risk, next_check`. 
   `preset_strategies` 는 `scalp.py` 의 전략 키(`vwap_fade_lim`, `mr_z20_lim`, `brk_20` …). 규칙 코드가 실제로 낸 진입·청산이 그려진다.
3. 그린다:
   ```
   python track_card.py --bars docs/cards/data/<봉>.csv --spec docs/cards/<번호_이름>.json --out docs/cards/<번호_이름>.svg --preset scalp --last 150
   ```
   날짜 단위로 켜고 끄는 규칙은 JSON 에 `bands`(`[{from, to, color, label}]`, UTC 초)를 넣으면 배경 띠로 보인다(P35 예).
   scalp 가 아닌 트랙은 `--preset` 없이 JSON 의 `markers`(`[{t,px,side,kind,pnl,label,tag,color}]` — `label` 은 표시 옆 글자, `tag` 는 "롱/숏" 대신 쓸 글자, P36 부터) 와 `lines` 로 직접 표시한다.
4. 이 표에 줄을 추가하고, `proposals.md` 그 규칙 줄과 `HANDOVER.md` 에 링크한다. 규칙을 추가하는 그 PR 에 함께 넣는다.

표준 라이브러리만 쓴다. 외부 패키지 없음.
