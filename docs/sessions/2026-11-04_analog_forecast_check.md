# 2026-11-04 "닮은 과거 찾기" 예측 검증 — 결론: 수익 근거 없음

## 사용자 요청
현황판(가상서버 BBv2·돈치안 현황판) 화면의 "지금 12봉과 닮은 과거 20구간 → 12시간 뒤 중앙값" 예측 시스템을 만들어 수익률을 올리고 싶다.

## 한 일
화면 방식을 그대로 재현해 실데이터로 순방향(걸어가며) 시험했다. 결과를 보기 전에 규칙·비용·변형 16개를 고정.
- 자료: 트레이딩뷰 MCP, 바이낸스 무기한 선물 1시간봉 → 2시간봉, 12종목(BTC ETH SOL XRP BNB DOGE LINK DOT ARB AAVE 1000PEPE 1000BONK), 2026-01-01 ~ 11-04 (307일, 3,689봉). 이 클라우드 세션은 거래소 직접 접속이 막혀 있다(전부 000).
- 방식: 시점마다 그 종목 최근 L봉 모양과 가장 닮은 K구간을 전 종목 과거에서 찾음(결과가 확정된 구간만 — 미래 정보 없음). 6봉(12h) 뒤 수익률 중앙값 부호로 방향 예측.
- 변형 16개: 길이 L 12·24 × 검색 범위 최근 300봉·전체 × K 20·50 × 닮음 기준(가격 경로·수익률).
- 평가: 12시간 간격(겹침 없음) 6,576회. 비용 왕복 0.10%(시장가 0.05%×2).
- 대조군: ① 무작위 20구간(닮음 무시) 50회 = 잡음 바닥 ② 모양 무시하고 최근 시장 흐름만 ③ 모멘텀·반전.

## 숫자 (전부 수수료 뺀 값)
| 항목 | 결과 |
|---|---|
| 화면 설정(L12·300봉·K20·경로) 방향 적중 | **51.0%** — 잡음 바닥 중앙값 49.95%, 95% 선 50.8% |
| 16개 변형 적중 범위 | 50.0 ~ 52.1% (모양 무시 "시장 흐름만" 50.2~51.9% 와 같은 수준) |
| 매번 예측 방향으로 매매 | **16개 전부 마이너스** (−1.4 ~ −15.8bp/회) |
| 확신 매매(상승비율 ≥65% 롱 / ≤35% 숏) 최고 변형 L24·300봉·K50·수익률 | 전체 +15.8bp. **상반기 +32.3bp → 하반기 −11.4bp.** 같은 시점 코인끼리 묶으면 t 0.16 (우연) |
| 화면 설정 확신 매매 | 상반기 +22.3bp → 하반기 −17.5bp, 시점 묶음 t −0.42 |
| 전 변형 공통 | 1~6월 적중 52~55% → 6~11월 47~51%. **효과가 뒤집혔다** |
| 반전(최근 12봉 반대) | 적중 53.0% 지만 수수료 뒤 −5.7bp |

화면 자체 숫자도 같은 결론: "과거 441시점 적중 54%" 인데 그 기간 "12h 뒤 오른 비율 45.9%" → 항상 "내림"이라고만 해도 54.1%. **닮은 과거가 보태는 정보 0.**

## 결론
- 가격 모양만으로 찾는 "닮은 과거" 예측은 이 12종목·2시간봉에서 **수수료를 넘는 근거 없음**. 트랙 신설·매매 사용 안 함(제안함 P27).
- 상반기에만 맞고 하반기에 뒤집힌 것 = 백테스트에서 고르면 실전에서 깨지는 전형(`docs/BACKTEST_OVERFITTING_REVIEW.md`).
- 현황판 그림은 "참고 그림"으로는 둬도 되지만, 청산·진입 근거로 쓰면 안 된다(화면 문구 그대로).

## 수익화 쪽으로 남은 길 (가격 모양 말고 다른 정보)
1. 가격 외 정보를 쓰는 트랙(이미 있음): 캐리·펀딩(`funding_signal.py`), 베이시스, 호가 제공(P26), 김치 프리미엄 — 가격 모양보다 근거가 구조적이다.
2. 닮은 과거를 다시 보려면: 모양 대신 "상황" 기준(변동성 수준·BTC 방향·펀딩)으로 찾고, 6개월 이상 순방향 기록 뒤 판단. 지금은 하지 않음 — 변형만 늘어난다.

## 다시 돌리는 법
스크립트는 numpy 가 필요해 저장소(표준 라이브러리만)에 넣지 않고 아래에 둔다. 자료는 트레이딩뷰 MCP `get-ohlcv`(BINANCE:<코인>USDT.P, 1h, count 8000, columns) 12번 → `{심볼: bars}` 를 `bars_1h.json` 으로 저장 → `python analog_test.py <폴더>`.

<details><summary>analog_test.py</summary>

```python
# -*- coding: utf-8 -*-
"""닮은 과거(analog) 예측 검증 — 화면의 방식 재현 + 대조군 + 잡음 바닥.
규칙(결과 보기 전 고정): 2시간봉, 패턴 길이 L, 과거 lib 봉 안에서 전 종목 후보, 상위 K개, H=6봉(12h) 뒤 수익률 중앙값으로 방향 예측.
비용: 왕복 0.10%(바이낸스 선물 시장가 0.05%×2). 평가 시점은 H봉 간격(겹치지 않게).
"""
import json, sys, math
import numpy as np

S = sys.argv[1]
raw = json.load(open(S + "/bars_1h.json"))
H = 6
COST = 0.0010
rng = np.random.default_rng(7)

# 1h -> 2h 종가 (짝수 UTC 시각에 끝나는 봉)
grid = sorted({t - 3600 for b in raw.values() for t in b["t"] if (t // 3600) % 2 == 1})
gi = {t: k for k, t in enumerate(grid)}
names = [s.split(":")[1].replace("USDT.P", "") for s in raw]
C = np.full((len(raw), len(grid)), np.nan)
for ci, b in enumerate(raw.values()):
    for t, c in zip(b["t"], b["c"]):
        if (t // 3600) % 2 == 1:
            C[ci, gi[t - 3600]] = c
# 마지막 봉은 미완성일 수 있어 버림
C = C[:, :-1]
T = C.shape[1]
R = np.diff(np.log(C), axis=1)            # R[:, k] = 봉 k -> k+1 로그수익
R = np.concatenate([np.full((len(names), 1), np.nan), R], axis=1)  # R[:, k] = 봉 k 의 수익
FWD = np.full_like(C, np.nan)
FWD[:, :T - H] = np.log(C[:, H:] / C[:, :T - H])   # 봉 k 종가에서 H봉 뒤까지
print("coins", names, "bars(2h)", T, "days", T / 12)


def shapes(L, kind):
    """끝 봉 k 의 L봉 모양을 단위벡터로. kind=path: 누적 가격 경로 상관, ret: 수익률 상관."""
    n = len(names)
    X = np.full((n, T, L), np.nan)
    for k in range(L, T):
        w = R[:, k - L + 1:k + 1]
        X[:, k, :] = np.cumsum(w, axis=1) if kind == "path" else w
    X = X - np.nanmean(X, axis=2, keepdims=True)
    nr = np.sqrt(np.nansum(X * X, axis=2, keepdims=True))
    return X / np.where(nr == 0, np.nan, nr)


def run(L, lib, K, kind, random_pick=False, reps=1):
    X = shapes(L, kind)
    n = len(names)
    flatX = X.reshape(n * T, L)
    end = np.tile(np.arange(T), n)
    fwd = FWD.reshape(n * T)
    ok_all = ~np.isnan(flatX).any(axis=1) & ~np.isnan(fwd)
    warm = max(L + 300 + H, 400)
    rows = []  # (k, coin, pred_med, up_frac, real, mom, base_drift)
    for k in range(warm, T - H, H):
        m = ok_all & (end <= k - H) & (end >= (k - lib if lib else 0))
        idx = np.nonzero(m)[0]
        q = X[:, k, :]
        qok = ~np.isnan(q).any(axis=1) & ~np.isnan(FWD[:, k])
        if not qok.any() or len(idx) < K:
            continue
        cf = fwd[idx]
        drift = float(np.median(cf))
        if random_pick:
            for ci in np.nonzero(qok)[0]:
                for _ in range(reps):
                    pick = cf[rng.choice(len(idx), K, replace=False)]
                    rows.append((k, ci, float(np.median(pick)), float((pick > 0).mean()), float(FWD[ci, k]), 0.0, drift))
            continue
        sim = q[qok] @ flatX[idx].T
        top = np.argpartition(-sim, K - 1, axis=1)[:, :K]
        for row, ci in enumerate(np.nonzero(qok)[0]):
            pick = cf[top[row]]
            mom = float(np.nansum(R[ci, k - L + 1:k + 1]))
            rows.append((k, ci, float(np.median(pick)), float((pick > 0).mean()), float(FWD[ci, k]), mom, drift))
    return np.array(rows)


def stats(a, label, conf=0.65):
    pred, upf, real, mom, drift, kk = a[:, 2], a[:, 3], a[:, 4], a[:, 5], a[:, 6], a[:, 0]
    nz = (pred != 0) & (real != 0)
    hit = float((np.sign(pred[nz]) == np.sign(real[nz])).mean())
    base_up = float((real > 0).mean())
    hit_drift = float((np.sign(drift[nz]) == np.sign(real[nz])).mean())   # 모양 무시, 최근 시장 흐름만
    ic = float(np.corrcoef(pred, real)[0, 1])
    # 매매: 매번 예측 방향
    pnl_all = np.sign(pred) * real - COST
    # 확신 매매: 상승비율 ≥ conf 롱, ≤ 1-conf 숏
    sel = (upf >= conf) | (upf <= 1 - conf)
    side = np.where(upf >= conf, 1, -1)
    pnl_c = (side * real - COST)[sel]
    half = kk < np.median(kk)
    def tstat(x):
        return float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x)))) if len(x) > 2 else float("nan")
    out = dict(cfg=label, n=int(len(a)), hit=round(hit, 4), base_up=round(base_up, 4),
               hit_drift_only=round(hit_drift, 4), ic=round(ic, 4),
               all_mean_bp=round(pnl_all.mean() * 1e4, 1), all_t=round(tstat(pnl_all), 2),
               conf_n=int(sel.sum()), conf_mean_bp=round(pnl_c.mean() * 1e4, 1) if sel.any() else None,
               conf_t=round(tstat(pnl_c), 2),
               hit_h1=round(float((np.sign(pred[half & nz]) == np.sign(real[half & nz])).mean()), 4),
               hit_h2=round(float((np.sign(pred[~half & nz]) == np.sign(real[~half & nz])).mean()), 4),
               ic_h1=round(float(np.corrcoef(pred[half], real[half])[0, 1]), 4),
               ic_h2=round(float(np.corrcoef(pred[~half], real[~half])[0, 1]), 4))
    return out


results = []
for L in (12, 24):
    for lib in (300, 0):
        for K in (20, 50):
            for kind in ("path", "ret"):
                a = run(L, lib, K, kind)
                st = stats(a, "L%d lib%s K%d %s" % (L, lib or "all", K, kind))
                results.append(st)
                print(json.dumps(st, ensure_ascii=False))
                if (L, lib, K, kind) == (12, 300, 20, "path"):
                    base_rows = a

# 대조군: 모멘텀/반전 (같은 시점)
mom = base_rows[:, 5]; real = base_rows[:, 4]
for nm, sgn in (("momentum L12", 1), ("reversal L12", -1)):
    p = sgn * np.sign(mom) * real - COST
    print(nm, "hit", round(float((np.sign(sgn * mom) == np.sign(real)).mean()), 4),
          "mean_bp", round(p.mean() * 1e4, 1), "t", round(p.mean() / (p.std(ddof=1) / math.sqrt(len(p))), 2))
# 매수 보유 (평균)
print("buy&hold per 12h mean_bp", round(float(real.mean() * 1e4), 1))

# 잡음 바닥: 닮음 무시하고 같은 후보 풀에서 무작위 20개 (화면 설정)
rr = run(12, 300, 20, "path", random_pick=True, reps=50)
hits = []
ics = []
for r_ in range(50):
    sub = rr[r_::50]
    nz = (sub[:, 2] != 0) & (sub[:, 4] != 0)
    hits.append(float((np.sign(sub[nz, 2]) == np.sign(sub[nz, 4])).mean()))
    ics.append(float(np.corrcoef(sub[:, 2], sub[:, 4])[0, 1]))
hits.sort(); ics.sort()
print("noise floor (random 20) hit median %.4f 95%% %.4f | ic median %.4f 95%% %.4f" % (hits[25], hits[47], ics[25], ics[47]))
json.dump(dict(results=results, noise_hit=[hits[25], hits[47]], noise_ic=[ics[25], ics[47]]), open(S + "/analog_results.json", "w"), ensure_ascii=False, indent=1)
```
</details>

## Routine
변경 없음.
