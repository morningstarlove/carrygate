"""추세선 채널 하단 매수(제안함 P42) 실데이터 측정. 트랙 아님, 주문 없음, 표준 라이브러리만, 인터넷 불필요.
자료: data/bb_reentry/*.csv (TradingView 일봉 15종목, 2026-11-04 받음 — P27 과 같은 자료).
재현: python scripts/trendline_check.py            (표)
      python scripts/trendline_check.py --json out.json
규칙은 trendline.py 머리말에 결과를 보기 전에 고정(RULE_FIXED 2026-11-17). 이 스크립트는 규칙을 바꾸지 않는다.
비용(왕복, P27 과 동일): 암호화폐 0.20%, 한국 개별주 0.25%, 나머지 0.05%.
기준선: 같은 종목 매수보유 하루 평균 수익 × 보유일수. 열린 포지션은 집계 제외. 앞·뒤 절반은 2018-01-01 로 나눈다.
"""
import csv, os, sys, math, glob, json
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import trendline as T

D = os.path.join(HERE, "..", "data", "bb_reentry")
GROUPS = {"미국": ["AMEX_SPY", "NASDAQ_QQQ", "AMEX_IWM"],
          "한국": ["KRX_KOSPI", "KRX_KOSDAQ", "KRX_069500", "KRX_005930", "KRX_000660"],
          "암호화폐": ["BINANCE_BTCUSDT", "BINANCE_ETHUSDT", "BINANCE_XRPUSDT", "BINANCE_SOLUSDT", "BINANCE_DOGEUSDT"],
          "금·환율": ["TVC_GOLD", "FX_EURUSD"]}
HALF = "2018-01-01"


def cost(sym):
    if sym.startswith("BINANCE"): return 0.20
    if sym in ("KRX_005930", "KRX_000660"): return 0.25
    return 0.05


def load(f):
    return [{"date": r["date"], "o": float(r["o"]), "h": float(r["h"]), "l": float(r["l"]), "c": float(r["c"])}
            for r in csv.DictReader(open(f, encoding="utf-8"))]


def summ(tr, bh_day):
    tr = [t for t in tr if t["why"] != "open"]
    if not tr: return None
    n = len(tr); nets = [t["net_pct"] for t in tr]; m = sum(nets) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in nets) / (n - 1)) if n > 1 else 0
    gp = sum(x for x in nets if x > 0); gl = -sum(x for x in nets if x < 0)
    days = sum(t["days"] for t in tr)
    why = lambda w: round(sum(1 for t in tr if t["why"] == w) / n * 100)
    return {"n": n, "mean": m, "t": m / (sd / math.sqrt(n)) if sd else 0.0, "win": sum(1 for x in nets if x > 0) / n * 100,
            "pf": gp / gl if gl else 99.0, "sum": sum(nets), "base_sum": bh_day * days, "edge": (sum(nets) - bh_day * days) / n,
            "target": why("target"), "stop": why("stop"), "expire": why("expire"), "days": days / n, "nets": nets}


def pool(items):
    items = [s for s in items if s]
    if not items: return None
    nets = [x for s in items for x in s["nets"]]; n = len(nets); m = sum(nets) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in nets) / (n - 1)) if n > 1 else 0
    gp = sum(x for x in nets if x > 0); gl = -sum(x for x in nets if x < 0)
    return {"n": n, "mean": m, "t": m / (sd / math.sqrt(n)) if sd else 0.0, "win": sum(1 for x in nets if x > 0) / n * 100,
            "pf": gp / gl if gl else 99.0, "edge": (sum(s["sum"] for s in items) - sum(s["base_sum"] for s in items)) / n,
            "days": sum(s["days"] * s["n"] for s in items) / n}


def fmt(s):
    return "      -" if s is None else "%4d건 평균%+6.2f%% t%+5.2f 승%3.0f%% PF%5.2f 초과/건%+6.2f%% %5.1f일" % (
        s["n"], s["mean"], s["t"], s["win"], s["pf"], s["edge"], s["days"])


def main():
    res = {}
    for f in sorted(glob.glob(os.path.join(D, "*.csv"))):
        sym = os.path.basename(f)[:-4]; bars = load(f)
        c = [b["c"] for b in bars]
        bh = sum(c[i] / c[i - 1] - 1 for i in range(1, len(c))) / (len(c) - 1) * 100
        res[sym] = {"from": bars[0]["date"], "to": bars[-1]["date"], "bh_day": bh}
        for win in T.WINDOWS:
            for mode in T.MODES:
                tr = T.backtest(bars, win, mode, cost(sym))
                res[sym][(win, mode)] = summ(tr, bh)
                res[sym][(win, mode, "early")] = summ([t for t in tr if t["date_in"] < HALF], bh)
                res[sym][(win, mode, "late")] = summ([t for t in tr if t["date_in"] >= HALF], bh)
    print("## 전체 15종목 합산 (수수료 뺀 뒤 건당 평균 / 같은 일수 매수보유 대비 초과)")
    out = {"rule_fixed": T.RULE_FIXED, "all": {}, "groups": {}, "symbols": {}}
    for win in T.WINDOWS:
        for mode in T.MODES:
            p = pool([res[s][(win, mode)] for s in res])
            mark = " ★주 규칙" if (win, mode) == T.PRIMARY else ""
            print("  %s(%3d봉) %-8s %s%s" % (win, T.WINDOWS[win], mode, fmt(p), mark))
            out["all"]["%s|%s" % (win, mode)] = {k: round(v, 3) for k, v in p.items()} if p else None
    w, m = T.PRIMARY
    print("\n## 주 규칙(%s·%s) 앞·뒤 절반 (%s 기준)" % (w, m, HALF))
    for part in ("early", "late"):
        print("  %-5s %s" % (part, fmt(pool([res[s][(w, m, part)] for s in res]))))
    print("\n## 주 규칙 시장 묶음")
    for g, ss in GROUPS.items():
        p = pool([res[s][(w, m)] for s in ss])
        print("  %-5s %s" % (g, fmt(p)))
        out["groups"][g] = {k: round(v, 3) for k, v in p.items()} if p else None
    print("\n## 주 규칙 종목별 (익절/손절/만료 %)")
    for s in res:
        r = res[s][(w, m)]
        print("  %-17s %s %s" % (s, fmt(r), "" if r is None else "익%d 손%d 만%d" % (r["target"], r["stop"], r["expire"])))
        out["symbols"][s] = None if r is None else {k: round(v, 3) for k, v in r.items() if k != "nets"}
    if "--json" in sys.argv:
        json.dump(out, open(sys.argv[sys.argv.index("--json") + 1], "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
