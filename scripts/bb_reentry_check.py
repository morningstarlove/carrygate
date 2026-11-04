"""볼린저밴드 하단 재진입 카드 실데이터 측정 (2026-11-04, 제안함 P27). 트랙 아님, 주문 없음, 표준 라이브러리만.
자료: data/bb_reentry/*.csv (TradingView 일봉, 2026-11-04 받음, 분할 반영). 재현: python scripts/bb_reentry_check.py
결과를 보기 전에 고정한 규칙:
  밴드  : 종가 20일 단순평균 ± 2 × 모표준편차, 일봉
  신호  : 전날 종가 < 전날 하단선, 오늘 종가 >= 오늘 하단선 (이탈 뒤 종가 재진입)
  진입  : 다음 날 시가. 시가가 이미 손절가 이하면 건너뜀
  손절  : 이탈 구간(연속 이탈 종가일 ~ 재진입일) 최저가. 장중 저가가 닿으면 손절가(갭이면 시가)
  청산  : 종가 >= 중간선 이면 그 종가. 20거래일 지나면 종가. 같은 날은 손절 먼저(보수적)
  비용  : 왕복 — 암호화폐 0.20%, 한국 개별주 0.25%(세금 0.20 포함), 나머지 0.05%
  한 시장에 한 포지션. 오늘(미완성) 봉 제외.
변형(비교용, 함께 고정):
  touch   : 카드가 "하지 말라"는 것 — 첫 이탈 종가 다음 날 시가 매수, 손절 = 이탈일 저가
  trend200: 기본 + 신호일 종가 > 200일 평균
  nostop  : 기본에서 손절 제거(중간선·20일만)
기준선: 같은 시장 매수보유의 하루 평균 수익 × 보유일수 (같은 기간 그냥 들고 있었으면)
"""
import csv, os, sys, math, glob
HERE = os.path.dirname(os.path.abspath(__file__))
D = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "data", "bb_reentry")
def cost(sym):
    if sym.startswith("BINANCE"): return 0.20
    if sym in ("KRX_005930", "KRX_000660"): return 0.25
    return 0.05
def load(f):
    return [(r["date"], float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"])) for r in csv.DictReader(open(f))]
def bands(c, n=20, k=2.0):
    mid = [None]*len(c); lo = [None]*len(c)
    for i in range(n-1, len(c)):
        w = c[i-n+1:i+1]; m = sum(w)/n; sd = math.sqrt(sum((x-m)**2 for x in w)/n)
        mid[i] = m; lo[i] = m - k*sd
    return mid, lo
def sma(c, n):
    out = [None]*len(c); s = 0
    for i, x in enumerate(c):
        s += x
        if i >= n: s -= c[i-n]
        if i >= n-1: out[i] = s/n
    return out
def run(rows, mode, cst):
    d = [r[0] for r in rows]; o = [r[1] for r in rows]; h = [r[2] for r in rows]; l = [r[3] for r in rows]; c = [r[4] for r in rows]
    mid, lo = bands(c); s200 = sma(c, 200)
    trades = []; i = 21; skipped = 0
    while i < len(rows)-1:
        sig = False
        if mode == "touch":
            if c[i] < lo[i] and c[i-1] >= lo[i-1]:
                sig = True; stop = l[i]
        else:
            if c[i-1] < lo[i-1] and c[i] >= lo[i]:
                j = i-1
                while j-1 >= 0 and lo[j-1] is not None and c[j-1] < lo[j-1]: j -= 1
                stop = min(l[j:i+1]); sig = True
                if mode == "trend200" and (s200[i] is None or c[i] <= s200[i]): sig = False
        if not sig: i += 1; continue
        e = i+1; entry = o[e]
        if mode != "nostop" and entry <= stop: skipped += 1; i += 1; continue
        k = e; exitp = None; why = None
        while k < len(rows):
            if mode != "nostop" and l[k] <= stop:
                exitp = min(o[k], stop) if k > e else stop; why = "stop"; break
            if c[k] >= mid[k]: exitp = c[k]; why = "mid"; break
            if k - e + 1 >= 20: exitp = c[k]; why = "time"; break
            k += 1
        if exitp is None: break  # 열린 포지션은 집계 제외
        net = (exitp/entry - 1)*100 - cst
        trades.append({"in": d[e], "out": d[k], "days": k-e+1, "net": net, "why": why})
        i = k+1
    return trades, skipped
def summ(tr, bh_day):
    if not tr: return None
    n = len(tr); nets = [t["net"] for t in tr]; m = sum(nets)/n
    sd = math.sqrt(sum((x-m)**2 for x in nets)/(n-1)) if n > 1 else 0
    win = sum(1 for x in nets if x > 0)/n*100
    gp = sum(x for x in nets if x > 0); gl = -sum(x for x in nets if x < 0)
    eq = 1.0
    for x in nets: eq *= 1 + x/100
    days = sum(t["days"] for t in tr)
    base = bh_day*days  # 같은 일수 매수보유 기대 합(%)
    return {"n": n, "mean": m, "t": m/(sd/math.sqrt(n)) if sd else 0, "win": win,
            "pf": gp/gl if gl else 99, "total": (eq-1)*100, "sum": sum(nets), "base_sum": base,
            "edge_per_trade": (sum(nets)-base)/n, "stops": sum(1 for t in tr if t["why"] == "stop")/n*100,
            "avgdays": days/n}
res = {}
for f in sorted(glob.glob(os.path.join(D, "*.csv"))):
    sym = os.path.basename(f)[:-4]; rows = load(f)
    c = [r[4] for r in rows]
    bh_day = (sum(c[i]/c[i-1]-1 for i in range(1, len(c)))/(len(c)-1))*100
    res[sym] = {"bh_day": bh_day, "from": rows[0][0], "to": rows[-1][0]}
    for mode in ("base", "touch", "trend200", "nostop"):
        tr, sk = run(rows, mode, cost(sym))
        res[sym][mode] = summ(tr, bh_day); 
        if mode == "base":
            res[sym]["skipped"] = sk
            half = "2018-01-01"
            res[sym]["early"] = summ([t for t in tr if t["in"] < half], bh_day)
            res[sym]["late"] = summ([t for t in tr if t["in"] >= half], bh_day)
def fmt(s): return "-" if s is None else "%3d건 평균%+6.2f%% t%+5.2f 승%3.0f%% PF%4.2f 누적%+7.1f%% 초과/건%+5.2f%% 손절%3.0f%% %4.1f일" % (s["n"], s["mean"], s["t"], s["win"], s["pf"], s["total"], s["edge_per_trade"], s["stops"], s["avgdays"])
for sym, r in res.items():
    print("\n##", sym, r["from"], "~", r["to"], "건너뜀", r["skipped"])
    for mode in ("base", "touch", "trend200", "nostop", "early", "late"):
        print("  %-8s %s" % (mode, fmt(r[mode])))

GROUPS = {"미국": ["AMEX_SPY", "NASDAQ_QQQ", "AMEX_IWM"],
          "한국": ["KRX_KOSPI", "KRX_KOSDAQ", "KRX_069500", "KRX_005930", "KRX_000660"],
          "암호화폐": ["BINANCE_BTCUSDT", "BINANCE_ETHUSDT", "BINANCE_XRPUSDT", "BINANCE_SOLUSDT", "BINANCE_DOGEUSDT"],
          "금·환율": ["TVC_GOLD", "FX_EURUSD"]}
print("\n## 시장 묶음 (건당 평균 / 같은 일수 매수보유 대비 초과)")
for g, ss in GROUPS.items():
    for mode in ("base", "touch", "nostop", "trend200"):
        n = sum(res[s][mode]["n"] for s in ss); tot = sum(res[s][mode]["sum"] for s in ss); base = sum(res[s][mode]["base_sum"] for s in ss)
        print("  %-5s %-8s %4d건 평균%+.2f%% 초과/건%+.2f%%" % (g, mode, n, tot/n, (tot-base)/n))
