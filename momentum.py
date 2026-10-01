# -*- coding: utf-8 -*-
"""
CARRYGATE — 장기 ETF 듀얼 모멘텀 백테스트

일봉 CSV(data/etf/<티커>.csv)를 월말 종가로 줄여서, **미리 고정해 둔 듀얼 모멘텀 규칙**들을
과거 전 구간에 그대로 돌려 보고, 교체 비용을 뺀 성적(CAGR·최대낙폭·변동성 등)을 낸다.
단순 보유(SPY)와 60/40 을 같은 기간에 나란히 놓고, 오늘 기준으로 각 규칙이 가리키는 자산도 보여 준다.

핵심 원칙
  - 규칙과 매개변수는 코드 맨 위 상수에 고정돼 있다. 결과를 보고 고치면 과최적화다.
  - 월말 종가로 판단하고 **다음 달**에 그 자산을 들고 간다. 판단에는 그 월말까지의 자료만 쓴다
    (순위용 수익률 = close[m] / close[m-L] - 1, L 은 개월).
  - 보유 자산이 바뀔 때마다 COST_PCT(%) 를 포트폴리오에서 뗀다. 첫 매수와 벤치마크에는 비용이 없다.
  - BIL(현금 대용)이 아직 없던 달은 현금 수익률을 0 으로 본다.
  - 주문 기능 없음. 파일만 읽는다. 외부 접속 없음.

사용법
  python momentum.py                   # 백테스트 + 현재 신호, momentum.json / momentum_history.jsonl 저장
  python momentum.py --dry-run         # 저장하지 않고 화면에만
  python momentum.py --data DIR        # 다른 폴더의 CSV 사용 (기본 data/etf)
  python momentum.py --report          # 쌓인 기록으로 신호가 어떻게 바뀌어 왔는지
"""
import json, sys, csv, os, math, argparse, statistics
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))

DATA_DIR = "data/etf"
COST_PCT = 0.25       # 보유 자산을 바꿀 때마다 포트폴리오에서 떼는 비용 (%)
CASH = "BIL"          # 현금 대용. 없는 달은 수익률 0
DEFENSIVE_BOND = "AGG"

# 전략 정의 (규칙 고정)
#   risky       : 공격 자산 후보 (점수가 가장 높은 1개만 본다)
#   lookbacks   : 점수 = 이 개월 수들의 수익률 평균
#   defensive   : "AGG" 고정이면 항상 그 자산, "TLT_OR_CASH" 면 TLT 12개월 수익률 > 0 일 때 TLT, 아니면 현금
STRATEGIES = {
    "gem_12":   {"name": "GEM 12개월",   "risky": ["SPY", "EFA"], "lookbacks": [12],
                 "defensive": "AGG"},
    "gem_6":    {"name": "GEM 6개월",    "risky": ["SPY", "EFA"], "lookbacks": [6],
                 "defensive": "AGG"},
    "gem_comp": {"name": "GEM 복합",     "risky": ["SPY", "EFA"], "lookbacks": [1, 3, 6, 12],
                 "defensive": "AGG"},
    "dm_4":     {"name": "DM 4자산",     "risky": ["SPY", "QQQ", "EFA", "GLD"], "lookbacks": [12],
                 "defensive": "TLT_OR_CASH"},
}
BENCHMARKS = {
    "spy_hold": {"name": "SPY 보유",     "weights": {"SPY": 1.0}},
    "6040":     {"name": "60/40 월 재조정", "weights": {"SPY": 0.6, "AGG": 0.4}},
}
STRATEGY_ORDER = ["gem_12", "gem_6", "gem_comp", "dm_4", "spy_hold", "6040"]
DECADES = [(2000, "2000년대"), (2010, "2010년대"), (2020, "2020년대")]

METHOD = ("월말(달의 마지막 거래일) 종가로 판단하고 다음 달에 보유. 순위용 수익률은 close[m]/close[m-L]-1 만 사용(미래 자료 없음). "
          "GEM: 공격 자산 중 점수 1등이 현금(BIL) 점수보다 높으면 그 자산, 아니면 AGG. "
          "DM 4자산: 공격 4개 중 12개월 1등이 현금보다 높으면 그 자산, 아니면 TLT(12개월>0) 또는 현금. "
          "복합 점수는 1·3·6·12개월 수익률 평균. BIL 이 없던 달은 현금 수익률 0. "
          "교체 때마다 COST_PCT 를 포트폴리오에서 차감(첫 매수·벤치마크 제외). "
          "모든 전략과 벤치마크를 같은 기간(모든 전략이 판단 가능한 첫 달 ~ 마지막 달)에서 비교. "
          "CAGR/변동성은 무위험 수익률 없이 계산.")


# --- 자료 읽기 ------------------------------------------------------------
def load_ticker(data_dir, ticker):
    """CSV -> [(date, close)] 날짜 오름차순. 파일이 없거나 망가졌으면 None."""
    path = os.path.join(data_dir, ticker + ".csv")
    rows = []
    try:
        with open(path, encoding="utf-8", newline="") as fp:
            for r in csv.DictReader(fp):
                try:
                    c = float(r["c"])
                    d = r["date"].strip()
                except (KeyError, ValueError, AttributeError):
                    continue
                if c > 0 and len(d) >= 10:
                    rows.append((d[:10], c))
    except IOError:
        return None
    if not rows:
        return None
    rows.sort(key=lambda x: x[0])
    return rows


def resample_month_end(rows):
    """[(date, close)] -> [(ym, date, close)] 달마다 마지막 거래일 하나."""
    last = {}
    for d, c in rows:
        last[d[:7]] = (d, c)
    return [(ym, last[ym][0], last[ym][1]) for ym in sorted(last)]


def all_tickers():
    t = set([CASH])
    for s in STRATEGIES.values():
        t.update(s["risky"])
        t.add(DEFENSIVE_BOND if s["defensive"] == "AGG" else "TLT")
    for b in BENCHMARKS.values():
        t.update(b["weights"])
    return sorted(t)


def load_all(data_dir, tickers=None):
    """-> (monthly, dates, ranges). monthly[t][ym] = 월말 종가, dates[t][ym] = 그 날짜."""
    monthly, dates, ranges = {}, {}, {}
    for t in (tickers or all_tickers()):
        rows = load_ticker(data_dir, t)
        if rows is None:
            continue
        ms = resample_month_end(rows)
        monthly[t] = dict((ym, c) for ym, d, c in ms)
        dates[t] = dict((ym, d) for ym, d, c in ms)
        ranges[t] = {"first_date": rows[0][0], "last_date": rows[-1][0], "bars": len(rows),
                     "first_month": ms[0][0], "last_month": ms[-1][0], "months": len(ms)}
    return monthly, dates, ranges


# --- 달 계산 ---------------------------------------------------------------
def ym_index(ym):
    y, m = ym.split("-")
    return int(y) * 12 + int(m) - 1


def ym_str(i):
    return "%04d-%02d" % (i // 12, i % 12 + 1)


def shift(ym, k):
    return ym_str(ym_index(ym) + k)


def lookback_return(mc, ym, L):
    """close[ym] / close[ym - L개월] - 1. 둘 중 하나라도 없으면 None. ym 이후 자료는 보지 않는다."""
    c1 = mc.get(ym)
    c0 = mc.get(shift(ym, -L))
    if c1 is None or c0 is None:
        return None
    return c1 / c0 - 1.0


def month_return(mc, ym_prev, ym):
    """ym_prev 월말 -> ym 월말 수익률. 없으면 None."""
    a, b = mc.get(ym_prev), mc.get(ym)
    if a is None or b is None:
        return None
    return b / a - 1.0


def score(monthly, ticker, ym, lookbacks):
    """lookbacks 수익률의 평균. 하나라도 없으면 None."""
    mc = monthly.get(ticker)
    if mc is None:
        return None
    rs = [lookback_return(mc, ym, L) for L in lookbacks]
    if any(r is None for r in rs):
        return None
    return sum(rs) / len(rs)


def cash_score(monthly, ym, lookbacks):
    """BIL 점수. BIL 자료가 없는 달은 0."""
    s = score(monthly, CASH, ym, lookbacks)
    return 0.0 if s is None else s


def required_tickers(cfg):
    return list(cfg["risky"]) + [DEFENSIVE_BOND if cfg["defensive"] == "AGG" else "TLT"]


def decide(cfg, monthly, ym):
    """ym 월말에 내릴 판단. (보유 자산, 상세) 또는 필요한 수익률이 없으면 (None, None)."""
    lb = cfg["lookbacks"]
    scores = {}
    for t in cfg["risky"]:
        s = score(monthly, t, ym, lb)
        if s is None:
            return None, None
        scores[t] = s
    cash = cash_score(monthly, ym, lb)
    best = max(cfg["risky"], key=lambda t: scores[t])
    detail = {"scores": scores, "cash_score": cash, "best": best}
    if scores[best] > cash:
        return best, detail
    if cfg["defensive"] == "AGG":
        if monthly.get(DEFENSIVE_BOND, {}).get(ym) is None:
            return None, None
        return DEFENSIVE_BOND, detail
    # TLT_OR_CASH
    tlt = score(monthly, "TLT", ym, [12])
    if tlt is None:
        return None, None
    detail["tlt_12m"] = tlt
    return ("TLT" if tlt > 0 else CASH), detail


def held_return(monthly, asset, ym_prev, ym):
    """보유 자산의 한 달 수익률. 현금(BIL) 자료가 없으면 0."""
    r = month_return(monthly.get(asset, {}), ym_prev, ym)
    if r is None:
        if asset == CASH:
            return 0.0
        raise ValueError("%s %s->%s 월말 종가 없음" % (asset, ym_prev, ym))
    return r


# --- 백테스트 --------------------------------------------------------------
def run_strategy(key, monthly, window, cost_pct=COST_PCT):
    """window = 연속된 달 목록 [m0..mn]. 각 m_i 월말에 판단 -> m_{i+1} 에 보유.
    교체 때마다 cost_pct 를 한 번씩 뗀다 (첫 매수 제외)."""
    cfg = STRATEGIES[key]
    eq, rets, holdings = [1.0], [], []
    for i in range(len(window) - 1):
        asset, _ = decide(cfg, monthly, window[i])
        if asset is None:
            raise ValueError("%s: %s 판단 불가" % (key, window[i]))
        r = held_return(monthly, asset, window[i], window[i + 1])
        v = eq[-1] * (1.0 + r)
        if holdings and asset != holdings[-1]:
            v *= 1.0 - cost_pct / 100.0
        holdings.append(asset)
        eq.append(v)
        rets.append(v / eq[-2] - 1.0)
    defensive = set([DEFENSIVE_BOND]) if cfg["defensive"] == "AGG" else set(["TLT", CASH])
    return {"key": key, "name": cfg["name"], "window": window, "eq": eq, "rets": rets,
            "holdings": holdings, "defensive": defensive}


def run_benchmark(key, monthly, window):
    w = BENCHMARKS[key]["weights"]
    eq, rets = [1.0], []
    for i in range(len(window) - 1):
        r = sum(wt * held_return(monthly, t, window[i], window[i + 1]) for t, wt in w.items())
        eq.append(eq[-1] * (1.0 + r))
        rets.append(r)
    return {"key": key, "name": BENCHMARKS[key]["name"], "window": window, "eq": eq, "rets": rets,
            "holdings": [], "defensive": set()}


# --- 지표 ------------------------------------------------------------------
def max_drawdown(eq):
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1.0)
    return mdd


def yearly_returns(window, eq):
    """달력 연도별 수익률 (소수). 첫 해는 시작 월말부터, 마지막 해는 마지막 월말까지."""
    last_idx = {}
    for i, ym in enumerate(window):
        last_idx[int(ym[:4])] = i
    out, prev = {}, 0
    for y in sorted(last_idx):
        i = last_idx[y]
        if i > prev:
            out[y] = eq[i] / eq[prev] - 1.0
        prev = i
    return out


def calc_metrics(window, eq, rets, holdings, defensive):
    """모든 값은 소수가 아니라 % 단위(교체횟수 제외)로 돌려준다."""
    n = len(rets)
    years = n / 12.0
    total = eq[-1] / eq[0] - 1.0
    cagr = (eq[-1] / eq[0]) ** (1.0 / years) - 1.0 if years > 0 and eq[-1] > 0 else -1.0
    vol = statistics.stdev(rets) * math.sqrt(12) if n >= 2 else 0.0
    worst12 = None
    if len(eq) > 12:
        worst12 = min(eq[i + 12] / eq[i] - 1.0 for i in range(len(eq) - 12))
    switches = sum(1 for i in range(1, len(holdings)) if holdings[i] != holdings[i - 1])
    def_pct = (100.0 * sum(1 for h in holdings if h in defensive) / len(holdings)) if holdings else 0.0
    yearly = yearly_returns(window, eq)
    return {
        "start": window[0], "end": window[-1], "months": n,
        "cagr_pct": cagr * 100.0,
        "total_return_pct": total * 100.0,
        "max_drawdown_pct": max_drawdown(eq) * 100.0,
        "volatility_pct": vol * 100.0,
        "cagr_over_vol": (cagr / vol) if vol > 0 else None,
        "worst_12m_pct": worst12 * 100.0 if worst12 is not None else None,
        "switches": switches,
        "defensive_pct": def_pct,
        "yearly_pct": dict((str(y), v * 100.0) for y, v in yearly.items()),
    }


def common_window(monthly, keys):
    """모든 전략이 판단할 수 있는 첫 달 ~ 필요한 티커가 모두 있는 마지막 달. 연속된 달 목록, 없으면 []."""
    need = set()
    for k in keys:
        if k in STRATEGIES:
            need.update(required_tickers(STRATEGIES[k]))
        else:
            need.update(BENCHMARKS[k]["weights"])
    if not need or any(t not in monthly for t in need):
        return []
    months = set.intersection(*[set(monthly[t]) for t in need])
    if not months:
        return []
    idx = sorted(ym_index(m) for m in months)
    run = [idx[-1]]                     # 가장 최근의 연속 구간만 쓴다
    for i in reversed(idx[:-1]):
        if i != run[-1] - 1:
            break
        run.append(i)
    run = [ym_str(i) for i in reversed(run)]
    for s, ym in enumerate(run):
        if all(decide(STRATEGIES[k], monthly, ym)[0] is not None for k in keys if k in STRATEGIES):
            return run[s:]
    return []


def current_signal(key, monthly, dates):
    """데이터의 마지막 월말 기준으로 지금 들고 있어야 할 자산과 후보별 수익률."""
    cfg = STRATEGIES[key]
    need = required_tickers(cfg)
    ym = min(max(monthly[t]) for t in need)
    asset, det = decide(cfg, monthly, ym)
    if asset is None:
        return None
    rets = {}
    for t in cfg["risky"] + [x for x in need if x not in cfg["risky"]] + [CASH]:
        if t in monthly:
            rets[t] = dict(("%dm" % L, lookback_return(monthly[t], ym, L))
                           for L in sorted(set(cfg["lookbacks"] + [12])))
    return {"as_of_month": ym, "as_of_date": max(dates[t].get(ym, "") for t in need),
            "asset": asset, "scores": det["scores"], "cash_score": det["cash_score"],
            "best_risky": det["best"], "tlt_12m": det.get("tlt_12m"),
            "returns": rets, "lookbacks": cfg["lookbacks"]}


def run_all(monthly, dates, ranges, cost_pct=COST_PCT, today=None):
    today = today or datetime.now(KST).strftime("%Y-%m-%d")
    runnable = [k for k in STRATEGIES if all(t in monthly for t in required_tickers(STRATEGIES[k]))]
    bench = [k for k in BENCHMARKS if all(t in monthly for t in BENCHMARKS[k]["weights"])]
    skipped = [k for k in list(STRATEGIES) + list(BENCHMARKS) if k not in runnable and k not in bench]
    keys = runnable + bench
    window = common_window(monthly, keys) if keys else []
    results = {}
    if len(window) >= 2:
        for k in keys:
            r = run_strategy(k, monthly, window, cost_pct) if k in STRATEGIES else run_benchmark(k, monthly, window)
            results[k] = {"name": r["name"], "kind": "strategy" if k in STRATEGIES else "benchmark",
                          "metrics": calc_metrics(window, r["eq"], r["rets"], r["holdings"], r["defensive"]),
                          "last_holdings": r["holdings"][-12:]}
    signals = {}
    for k in runnable:
        s = current_signal(k, monthly, dates)
        if s:
            signals[k] = s
    return {"date": today, "cost_pct": cost_pct, "method": METHOD,
            "window": {"start": window[0], "end": window[-1]} if len(window) >= 2 else None,
            "skipped": skipped, "data_ranges": ranges, "results": results, "signals": signals}


# --- 저장 ------------------------------------------------------------------
def round_floats(o, nd=4):
    if isinstance(o, float):
        return round(o, nd)
    if isinstance(o, dict):
        return dict((k, round_floats(v, nd)) for k, v in o.items())
    if isinstance(o, (list, tuple)):
        return [round_floats(v, nd) for v in o]
    return o


def write_history(out, path):
    rec = {"date": out["date"],
           "signals": dict((k, {"asset": s["asset"], "as_of": s["as_of_month"]})
                           for k, s in out["signals"].items()),
           "metrics": dict((k, {"cagr_pct": r["metrics"]["cagr_pct"], "mdd_pct": r["metrics"]["max_drawdown_pct"],
                                "switches": r["metrics"]["switches"]})
                           for k, r in out["results"].items())}
    line = json.dumps(round_floats(rec), ensure_ascii=False)
    try:
        with open(path, encoding="utf-8") as fp:
            rows = [l for l in fp.read().splitlines() if l.strip()]
    except IOError:
        rows = []
    keep = []
    for l in rows:
        try:
            if json.loads(l).get("date") == out["date"]:
                continue
        except ValueError:
            pass
        keep.append(l)
    keep.append(line)
    with open(path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(keep) + "\n")


def load_history(path):
    rows = []
    try:
        with open(path, encoding="utf-8") as fp:
            for l in fp:
                l = l.strip()
                if l:
                    try:
                        rows.append(json.loads(l))
                    except ValueError:
                        pass
    except IOError:
        pass
    rows.sort(key=lambda r: r.get("date", ""))
    return rows


# --- 출력 ------------------------------------------------------------------
def fmt(v, spec="%+.2f", suffix=""):
    return "-" if v is None else (spec % v) + suffix


def decade_returns(yearly):
    """연도별 % 수익률 -> 10년 단위 복리 수익률 %."""
    out = {}
    for start, label in DECADES:
        ys = [y for y in yearly if start <= int(y) < start + 10]
        if ys:
            g = 1.0
            for y in ys:
                g *= 1.0 + yearly[y] / 100.0
            out[label] = ((g - 1.0) * 100.0, min(ys), max(ys))
    return out


def print_summary(out):
    w = out["window"]
    print("=== 듀얼 모멘텀 백테스트 (%s) ===" % out["date"])
    if w is None:
        print("백테스트 가능한 전략 없음 — 필요한 ETF 자료가 부족하다.")
    else:
        print("비교 기간 %s ~ %s, 교체 비용 %.2f%%" % (w["start"], w["end"], out["cost_pct"]))
    if out["skipped"]:
        print("자료 부족으로 건너뜀:", ", ".join(out["skipped"]))
    print()
    print("%-12s %-17s %8s %8s %8s %10s %9s %6s %7s"
          % ("전략", "기간", "CAGR%", "MDD%", "변동성%", "CAGR/변동성", "최악12개월%", "교체횟수", "방어비중%"))
    for k in STRATEGY_ORDER:
        r = out["results"].get(k)
        if not r:
            continue
        m = r["metrics"]
        print("%-12s %-17s %8s %8s %8s %10s %9s %6d %7s"
              % (k, "%s~%s" % (m["start"], m["end"]), fmt(m["cagr_pct"]), fmt(m["max_drawdown_pct"]),
                 fmt(m["volatility_pct"], "%.2f"), fmt(m["cagr_over_vol"], "%.2f"),
                 fmt(m["worst_12m_pct"]), m["switches"], fmt(m["defensive_pct"], "%.1f")))
    print()
    print("총수익률 %: " + "  ".join("%s %s" % (k, fmt(out["results"][k]["metrics"]["total_return_pct"], "%+.1f"))
                                  for k in STRATEGY_ORDER if k in out["results"]))
    a, b = out["results"].get("gem_12"), out["results"].get("spy_hold")
    if a and b:
        print()
        print("--- gem_12 vs spy_hold 연도별 수익률 % ---")
        print("(첫 해는 시작 월말부터, 마지막 해는 자료의 마지막 월말까지의 부분 연도)")
        print("%-10s %10s %10s %10s" % ("연도", "gem_12", "spy_hold", "차이"))
        ya, yb = a["metrics"]["yearly_pct"], b["metrics"]["yearly_pct"]
        for y in sorted(set(ya) | set(yb)):
            va, vb = ya.get(y), yb.get(y)
            diff = va - vb if va is not None and vb is not None else None
            print("%-10s %10s %10s %10s" % (y, fmt(va), fmt(vb), fmt(diff)))
        print()
        print("--- 10년 단위 복리 수익률 % (해당 기간에 포함된 연도만) ---")
        da, db = decade_returns(ya), decade_returns(yb)
        for _, label in DECADES:
            if label in da and label in db:
                print("%-10s (%s~%s)  gem_12 %s  spy_hold %s"
                      % (label, da[label][1], da[label][2], fmt(da[label][0], "%+.1f"), fmt(db[label][0], "%+.1f")))
    print()
    print_signals(out)


def print_signals(out):
    print("--- 현재 신호 (자료의 마지막 월말 기준) ---")
    if not out["signals"]:
        print("신호를 낼 수 있는 전략 없음.")
        return
    for k in STRATEGY_ORDER:
        s = out["signals"].get(k)
        if not s:
            continue
        L = "%dm" % s["lookbacks"][-1]
        cand = []
        for t in s["scores"]:
            v = s["returns"].get(t, {}).get(L)
            cand.append("%s %s개월 %s" % (t, L[:-1], fmt(None if v is None else v * 100, "%+.1f", "%")))
        v = s["returns"].get(CASH, {}).get(L)
        cand.append("현금(%s) %s개월 %s" % (CASH, L[:-1], fmt(None if v is None else v * 100, "%+.1f", "%")))
        if s["tlt_12m"] is not None:
            cand.append("TLT 12개월 %+.1f%%" % (s["tlt_12m"] * 100))
        print("%-10s %s, 종가일 %s -> 보유: %s" % (k, s["as_of_month"], s["as_of_date"], s["asset"]))
        print("           " + " | ".join(cand))
        if len(s["lookbacks"]) > 1:
            print("           복합 점수 %s, 현금 %+.2f%%"
                  % (", ".join("%s %+.2f%%" % (t, v * 100) for t, v in s["scores"].items()), s["cash_score"] * 100))


def report(path="momentum_history.jsonl"):
    rows = load_history(path)
    if not rows:
        print("기록 없음 — momentum.py 가 실행되면서 쌓인다.")
        return 0
    keys = [k for k in STRATEGY_ORDER if any(k in r.get("signals", {}) for r in rows)]
    print("=== 모멘텀 신호 변화 (%d건: %s ~ %s) ===" % (len(rows), rows[0]["date"], rows[-1]["date"]))
    print("%-12s %-8s " % ("날짜", "기준월") + " ".join("%-9s" % k for k in keys))
    for r in rows:
        sg = r.get("signals", {})
        as_of = next((v["as_of"] for v in sg.values()), "-")
        print("%-12s %-8s " % (r["date"], as_of) + " ".join("%-9s" % sg.get(k, {}).get("asset", "-") for k in keys))
    print()
    for k in keys:
        last, prev = None, None
        for r in rows:
            a = r.get("signals", {}).get(k, {}).get("asset")
            if a is None:
                continue
            if prev is not None and a != prev:
                last = (r["date"], prev, a)
            prev = a
        if last:
            print("%-10s 마지막 교체: %s (%s -> %s), 현재 %s" % (k, last[0], last[1], last[2], prev))
        else:
            print("%-10s 기록 기간 중 교체 없음, 현재 %s" % (k, prev))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="저장하지 않는다")
    ap.add_argument("--report", action="store_true", help="신호 변화 기록만 출력")
    ap.add_argument("--data", default=DATA_DIR, help="ETF 일봉 CSV 폴더")
    ap.add_argument("--out", default="momentum.json")
    ap.add_argument("--history", default="momentum_history.jsonl")
    ap.add_argument("--cost", type=float, default=COST_PCT, help="교체 비용 %% (기본 %s)" % COST_PCT)
    a = ap.parse_args(argv)
    if a.report:
        return report(a.history)
    monthly, dates, ranges = load_all(a.data)
    if not monthly:
        print("ETF 자료 없음: %s" % a.data)
        return 1
    out = run_all(monthly, dates, ranges, a.cost)
    print_summary(out)
    if not out["results"]:
        return 1
    if not a.dry_run:
        with open(a.out, "w", encoding="utf-8") as fp:
            json.dump(round_floats(out), fp, ensure_ascii=False, indent=1)
        write_history(out, a.history)
        print()
        print("저장: %s, %s" % (a.out, a.history))
    return 0


if __name__ == "__main__":
    sys.exit(main())
