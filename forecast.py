# -*- coding: utf-8 -*-
"""
CARRYGATE — 예측 v2 (설계서 docs/FORECAST_PLAN.md, 제안함 P31). "맞힐 수 있는 것만 예측하고, 매일 채점한다."

무엇을 하나
  매일 1회, OKX 무기한선물 12종목의 2시간봉에서 (가) 앞 12시간의 **움직임 크기** 와 (나) **방향 확률** 을 고정 산술로 계산해 기록하고,
  다음 날 어제 예측을 실제 봉으로 **채점**한다. 주문 없음, 키 없음, AI 판단 없음. 예측은 매매에 쓰지 않는다 —
  60일 뒤 판정(verdict.py 여섯 번째 트랙)이 PASS 일 때만 "기존 트랙 포지션 크기 조절·문지기" 를 제안함에 올린다.

규칙 (고정값, 사후 조정 금지)
  봉           2시간봉(UTC 정렬). 예측 시점 = 마지막 완성 봉 k. 목표 = H=6봉(12시간) 뒤 종가. 수익 = ln(c[k+H]/c[k]).
  변수         rv12 = 최근 12봉 로그수익 표준편차,  r12 = 최근 12봉 로그수익 합,  btc_r12 = BTC 의 r12.
  분위         종목별 과거 LOOKBACK=300 시점(k−H−300 … k−H−1, 모두 결과가 확정된 시점)의 3분위(33.3 / 66.7 백분위). 미래 정보 없음.
  ① 크기       rv12 분위(하/중/상) → 같은 분위 과거 시점의 |수익| 중앙값 = 예상 폭.
  ② 방향 D1    r12 분위 → 같은 분위 과거 시점의 상승 비율 (24h 반전 가설).
  ② 방향 D2    (rv12 분위, r12 분위, btc_r12 분위) 세 개가 같은 과거 시점(= 상황이 닮은 과거)의 상승 비율·분포.
               같은 상황이 MIN_ANALOG=10 개 미만이면 D3 값으로 대신하고 fallback 표시.
  ② 방향 D3    과거 300 시점 전체의 상승 비율 (= 정보 없음, 기준선).
  ③ 채점       목표 봉이 닫힌 뒤: 실제 수익, 상승 여부, 예측기별 적중(p≠0.5 일 때만)·Brier = (p − 실제상승)².
               크기는 예측 분위별 실제 |수익| 을 모아 상위/하위 비율.
  합격선       criteria.json forecast (60일, 크기 비율 ≥ 1.20·앞뒤 30일 각 ≥ 1.10, 방향은 잡음 바닥·Brier 둘 다).

사용법
  python forecast.py                                   # 수집·예측·채점·저장 (forecast.json, forecast_history.jsonl)
  python forecast.py --dry-run                         # 저장 안 함
  python forecast.py --fixture tests/fixtures/forecast --dry-run   # 저장된 2시간봉 CSV 로 (인터넷 불필요)
  python forecast.py --save-fixture tests/fixtures/forecast        # 거래소에서 받은 봉을 CSV 로 저장 (Actions 에서)
  python forecast.py --report                          # 누적 채점 보고
"""
import os, sys, json, math, csv, time, argparse
from datetime import datetime, timezone

from common import http_json, f, KST, r4, load_history

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(HERE, "forecast.json")
HISTORY_PATH = os.path.join(HERE, "forecast_history.jsonl")

# ----------------------------------------------------------------- 고정 규칙 (사후 조정 금지)
BAR_MIN = 120               # 2시간봉
H = 6                       # 예측 지평 = 6봉 = 12시간
LOOKBACK = 300              # 분위·분포를 재는 과거 시점 수
RV_N = 12                   # 변동성 구간(봉) = 24시간
R_N = 12                    # 수익 구간(봉) = 24시간
TERCILE = (33.3, 66.7)      # 3분위 경계 백분위
MIN_ANALOG = 10             # D2 상황 닮은 과거 최소 개수 (미만이면 D3 로 대체)
RULE_FIXED = "2026-11-06"   # 이 날 이후 예측만 순방향 채점 대상
COINS = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "LINK", "DOT", "ARB", "AAVE", "PEPE", "BONK"]
BARS = LOOKBACK + H + RV_N + 40     # 받아올 봉 수 (358) — 채점용 과거 봉도 여기 들어 있다(약 30일)
MIN_BARS = LOOKBACK + H + RV_N + 2
PREDICTORS = ("D1", "D2", "D3")
BUCKETS = ("low", "mid", "high")


def rules_dict():
    return {"bar_minutes": BAR_MIN, "horizon_bars": H, "lookback_points": LOOKBACK, "rv_bars": RV_N, "ret_bars": R_N,
            "tercile_pct": list(TERCILE), "min_analog": MIN_ANALOG, "coins": list(COINS), "predictors": list(PREDICTORS),
            "size_feature": "rv12", "d1_feature": "r12", "d2_features": ["rv12", "r12", "btc_r12"]}


# ----------------------------------------------------------------- 2시간봉 수집

def _bar_open_cutoff(now=None):
    now = int(now if now is not None else datetime.now(timezone.utc).timestamp())
    return now - now % (BAR_MIN * 60)


def _mk(t, o, h, l, c, v):
    return {"t": int(t), "date": datetime.fromtimestamp(int(t), timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "o": o, "h": h, "l": l, "c": c, "v": v}


def okx_2h(coin, bars=BARS):
    """OKX 무기한선물 2H 봉(UTC 정렬). 최신 300 + history-candles 100개씩. confirm=0(미완성) 제외. reversal.okx_4h 와 같은 방식."""
    inst = "%s-USDT-SWAP" % coin
    out = {}

    def take(rows):
        for r in rows:
            if len(r) > 8 and str(r[8]) == "0":
                continue
            t = int(r[0]) // 1000
            out[t] = _mk(t, f(r[1]), f(r[2]), f(r[3]), f(r[4]), f(r[6]) if len(r) > 6 else f(r[5]))

    rows = http_json("https://www.okx.com/api/v5/market/candles?instId=%s&bar=2H&limit=300" % inst).get("data") or []
    take(rows)
    while rows and len(out) < bars + 2:
        oldest = min(int(r[0]) for r in rows)
        time.sleep(0.25)
        rows = http_json("https://www.okx.com/api/v5/market/history-candles?instId=%s&bar=2H&limit=100&after=%d"
                         % (inst, oldest)).get("data") or []
        take(rows)
    cut = _bar_open_cutoff()
    res = [out[t] for t in sorted(out) if t < cut]
    return res[-bars:]


def fixture_path(path, coin):
    return os.path.join(path, "okx_%s_2h.csv" % coin)


def load_fixture(path, coin):
    with open(fixture_path(path, coin), encoding="utf-8") as fh:
        return [_mk(r["t"], float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r["volume"]))
                for r in csv.DictReader(fh)]


def save_fixture(path, coin, bars):
    os.makedirs(path, exist_ok=True)
    with open(fixture_path(path, coin), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["t", "date", "open", "high", "low", "close", "volume"])
        for b in bars:
            w.writerow([b["t"], b["date"], b["o"], b["h"], b["l"], b["c"], b["v"]])


# ----------------------------------------------------------------- 순수 계산

def log_returns(closes):
    """r[i] = ln(c[i]/c[i−1]); r[0] = None."""
    out = [None]
    for i in range(1, len(closes)):
        out.append(math.log(closes[i] / closes[i - 1]) if closes[i] > 0 and closes[i - 1] > 0 else None)
    return out


def roll_sum(r, i, n):
    w = r[i - n + 1:i + 1]
    if i - n + 1 < 0 or any(x is None for x in w):
        return None
    return sum(w)


def roll_std(r, i, n):
    w = r[i - n + 1:i + 1]
    if i - n + 1 < 0 or any(x is None for x in w):
        return None
    m = sum(w) / n
    return math.sqrt(sum((x - m) ** 2 for x in w) / n)


def percentile(xs, p):
    """선형 보간 백분위 (numpy 기본과 같음)."""
    s = sorted(xs)
    if not s:
        return None
    k = (len(s) - 1) * p / 100.0
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def median(xs):
    return percentile(xs, 50)


def bucket(x, lo, hi):
    if x is None or lo is None or hi is None:
        return None
    return "low" if x <= lo else ("high" if x >= hi else "mid")


def features(closes, btc_closes):
    """각 봉 i 의 (rv12, r12, btc_r12). 길이 = closes. 값이 안 되는 자리는 None."""
    r = log_returns(closes)
    rb = log_returns(btc_closes)
    out = []
    for i in range(len(closes)):
        out.append({"rv12": roll_std(r, i, RV_N), "r12": roll_sum(r, i, R_N), "btc_r12": roll_sum(rb, i, R_N)})
    return out


def predict_one(closes, btc_closes, k=None):
    """봉 k(기본: 마지막) 에서의 예측. closes 와 btc_closes 는 같은 시각 정렬. 미래 봉(k 뒤)은 쓰지 않는다."""
    n = len(closes)
    k = n - 1 if k is None else k
    if k + 1 < MIN_BARS:
        return {"ok": False, "reason": "봉 %d개 — 최소 %d개" % (k + 1, MIN_BARS)}
    fe = features(closes[:k + 1], btc_closes[:k + 1])
    hist = []
    for j in range(k - H - LOOKBACK, k - H):
        x = fe[j]
        if any(x[q] is None for q in ("rv12", "r12", "btc_r12")):
            continue
        fwd = math.log(closes[j + H] / closes[j])
        hist.append((x["rv12"], x["r12"], x["btc_r12"], fwd))
    if len(hist) < LOOKBACK * 0.9:
        return {"ok": False, "reason": "과거 시점 %d개 — 최소 %d개" % (len(hist), int(LOOKBACK * 0.9))}
    cur = fe[k]
    if any(cur[q] is None for q in ("rv12", "r12", "btc_r12")):
        return {"ok": False, "reason": "현재 변수 없음"}
    bounds = {}
    for qi, q in enumerate(("rv12", "r12", "btc_r12")):
        col = [h[qi] for h in hist]
        bounds[q] = (percentile(col, TERCILE[0]), percentile(col, TERCILE[1]))
    b_rv = bucket(cur["rv12"], *bounds["rv12"])
    b_r = bucket(cur["r12"], *bounds["r12"])
    b_btc = bucket(cur["btc_r12"], *bounds["btc_r12"])
    hb = [(bucket(h[0], *bounds["rv12"]), bucket(h[1], *bounds["r12"]), bucket(h[2], *bounds["btc_r12"]), h[3]) for h in hist]
    # ① 크기
    same_rv = [abs(h[3]) for h in hb if h[0] == b_rv]
    size_pct = median(same_rv) * 100 if same_rv else None
    size_all_pct = median([abs(h[3]) for h in hb]) * 100
    # ② 방향
    fwd_all = [h[3] for h in hb]
    p3 = sum(1 for x in fwd_all if x > 0) / len(fwd_all)
    same_r = [h[3] for h in hb if h[1] == b_r]
    p1 = sum(1 for x in same_r if x > 0) / len(same_r) if same_r else p3
    analog = [h[3] for h in hb if h[0] == b_rv and h[1] == b_r and h[2] == b_btc]
    fallback = len(analog) < MIN_ANALOG
    p2 = p3 if fallback else sum(1 for x in analog if x > 0) / len(analog)
    dist = analog if not fallback else fwd_all
    return {"ok": True,
            "features": {"rv12": r4(cur["rv12"]), "r12": r4(cur["r12"]), "btc_r12": r4(cur["btc_r12"])},
            "buckets": {"rv12": b_rv, "r12": b_r, "btc_r12": b_btc},
            "size": {"bucket": b_rv, "expected_abs_pct": round(size_pct, 3) if size_pct is not None else None,
                     "all_abs_pct": round(size_all_pct, 3), "n": len(same_rv)},
            "direction": {"D1": {"p_up": round(p1, 4), "n": len(same_r)},
                          "D2": {"p_up": round(p2, 4), "n": len(analog), "fallback": fallback,
                                 "q25_pct": round(percentile(dist, 25) * 100, 3), "median_pct": round(median(dist) * 100, 3),
                                 "q75_pct": round(percentile(dist, 75) * 100, 3)},
                          "D3": {"p_up": round(p3, 4), "n": len(fwd_all)}},
            "hist_points": len(hist)}


# ----------------------------------------------------------------- 채점

def score_entry(entry, closes_by_t):
    """entry = history 한 줄의 coins[coin]. closes_by_t = {봉 시작 unix: 종가}. 목표 봉이 있으면 채점 dict, 없으면 None."""
    c1 = closes_by_t.get(entry["t_target"])
    if c1 is None or not entry.get("c0"):
        return None
    real = math.log(c1 / entry["c0"])
    up = real > 0
    out = {"c1": c1, "realized_pct": round(real * 100, 4), "abs_pct": round(abs(real) * 100, 4), "up": up, "predictors": {}}
    for name in PREDICTORS:
        p = entry["direction"][name]["p_up"]
        hit = None if p == 0.5 else ((p > 0.5) == up)
        out["predictors"][name] = {"p_up": p, "hit": hit, "brier": round((p - (1.0 if up else 0.0)) ** 2, 4)}
    return out


def score_history(rows, closes_by_coin_t):
    """채점 안 된 줄을 채점한다(제자리 수정). 돌려주는 값: 새로 채점한 (날짜, 코인) 수."""
    n = 0
    for row in rows:
        for coin, e in (row.get("coins") or {}).items():
            if not e.get("ok") or e.get("scored"):
                continue
            s = score_entry(e, closes_by_coin_t.get(coin) or {})
            if s:
                e["scored"] = s
                n += 1
    return n


def _half_split(dates):
    ds = sorted(set(dates))
    cut = ds[len(ds) // 2] if ds else None
    return cut


def score_summary(rows, rule_fixed=RULE_FIXED):
    """누적 채점 요약 — 순수 계산. verdict.judge_forecast 가 같은 함수를 쓴다.
    크기: 예측 분위별 실제 |수익| 평균, 상위/하위 비율(전체·앞 절반·뒤 절반).
    방향: 예측기별 n·적중·적중률·Brier 평균."""
    pts = []   # (date, coin, size_bucket, abs_pct, up, {name: (p, hit, brier)})
    for row in rows:
        if row.get("date", "") < rule_fixed:
            continue
        for coin, e in (row.get("coins") or {}).items():
            s = e.get("scored")
            if not s:
                continue
            pts.append((row["date"], coin, (e.get("size") or {}).get("bucket"), s["abs_pct"], s["up"],
                        {nm: (v["p_up"], v["hit"], v["brier"]) for nm, v in s["predictors"].items()}))
    dates = sorted(set(p[0] for p in pts))
    cut = _half_split(dates)

    def size_stats(sub):
        by = {b: [p[3] for p in sub if p[2] == b] for b in BUCKETS}
        mean = {b: (round(sum(v) / len(v), 4) if v else None) for b, v in by.items()}
        ratio = None
        if by["high"] and by["low"] and mean["low"]:
            ratio = round(mean["high"] / mean["low"], 4)
        return {"n": {b: len(v) for b, v in by.items()}, "mean_abs_pct": mean, "ratio_high_low": ratio}

    def dir_stats(sub):
        out = {}
        for nm in PREDICTORS:
            v = [p[5][nm] for p in sub if nm in p[5]]
            dec = [x for x in v if x[1] is not None]
            hits = sum(1 for x in dec if x[1])
            out[nm] = {"n": len(v), "decisive": len(dec), "hits": hits,
                       "hit_rate": round(hits / len(dec), 4) if dec else None,
                       "brier": round(sum(x[2] for x in v) / len(v), 4) if v else None}
        return out

    first = [p for p in pts if cut is None or p[0] < cut]
    second = [p for p in pts if cut is not None and p[0] >= cut]
    base_up = round(sum(1 for p in pts if p[4]) / len(pts), 4) if pts else None
    missing = 0
    if dates:
        d0 = datetime.strptime(dates[0], "%Y-%m-%d"); d1 = datetime.strptime(dates[-1], "%Y-%m-%d")
        missing = (d1 - d0).days + 1 - len(dates)
    return {"rule_fixed": rule_fixed, "days": len(dates), "points": len(pts), "first_date": dates[0] if dates else None,
            "last_date": dates[-1] if dates else None, "missing_days": missing, "base_up_rate": base_up,
            "size": {"all": size_stats(pts), "first_half": size_stats(first), "second_half": size_stats(second)},
            "direction": {"all": dir_stats(pts), "first_half": dir_stats(first), "second_half": dir_stats(second)},
            "_points": pts}


# ----------------------------------------------------------------- 조립

def read_history(path=HISTORY_PATH):
    return load_history(path)


def write_history(rows, path=HISTORY_PATH):
    """전체를 다시 쓴다(채점이 과거 줄을 고치므로). 같은 날짜는 하나만, 날짜순."""
    by = {}
    for r in rows:
        by[r["date"]] = r
    with open(path, "w", encoding="utf-8") as fh:
        for d in sorted(by):
            fh.write(json.dumps(by[d], ensure_ascii=False) + "\n")


def build(fixture=None, save_to=None, history_rows=None, now_utc=None):
    now = datetime.now(KST)
    out = {"schema": "carrygate-forecast/1", "date": now.strftime("%Y-%m-%d"),
           "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "source": "fixture %s" % fixture if fixture else "okx /v5/market/candles bar=2H (USDT 무기한)",
           "rules": dict(rules_dict(), rule_fixed=RULE_FIXED), "coins": {}, "errors": {}}
    bars = {}
    for coin in COINS:
        try:
            bars[coin] = load_fixture(fixture, coin) if fixture else okx_2h(coin)
            if save_to and not fixture:
                save_fixture(save_to, coin, bars[coin])
            if not fixture:
                time.sleep(0.2)
        except Exception as e:
            out["errors"][coin] = "%s: %s" % (type(e).__name__, e)
    if "BTC" not in bars:
        out["errors"]["BTC"] = out["errors"].get("BTC", "BTC 봉 없음 — btc_r12 계산 불가")
        out["summary"] = {"ok": False, "verdict": "BTC 봉 없음"}
        return out, history_rows or []
    btc_by_t = {b["t"]: b["c"] for b in bars["BTC"]}
    closes_by_coin_t = {}
    for coin, bs in bars.items():
        ts = [b["t"] for b in bs if b["t"] in btc_by_t]
        closes = [next(b["c"] for b in bs if b["t"] == t) for t in ts]
        btc = [btc_by_t[t] for t in ts]
        closes_by_coin_t[coin] = {b["t"]: b["c"] for b in bs}
        pr = predict_one(closes, btc)
        k_t = ts[-1] if ts else None
        pr.update({"t_bar": k_t, "bar_utc": datetime.fromtimestamp(k_t, timezone.utc).strftime("%Y-%m-%d %H:%M") if k_t else None,
                   "t_target": (k_t + H * BAR_MIN * 60) if k_t else None, "c0": closes[-1] if closes else None, "bars": len(bs)})
        out["coins"][coin] = pr
    # 채점: 과거 줄에 목표 봉이 닫힌 것
    rows = list(history_rows if history_rows is not None else read_history())
    newly = score_history(rows, closes_by_coin_t)
    # 오늘 줄
    today = {"date": out["date"], "t_bar": out["coins"]["BTC"].get("t_bar"), "rule_fixed": RULE_FIXED,
             "coins": {c: {k: v for k, v in p.items() if k not in ("features",)} for c, p in out["coins"].items()}}
    rows = [r for r in rows if r["date"] != out["date"]] + [today]
    summ = score_summary(rows)
    summ.pop("_points", None)
    out["cumulative"] = summ
    ok = [c for c, p in out["coins"].items() if p.get("ok")]
    high = [c for c in ok if out["coins"][c]["size"]["bucket"] == "high"]
    low = [c for c in ok if out["coins"][c]["size"]["bucket"] == "low"]
    out["summary"] = {"ok": bool(ok), "coins_ok": len(ok), "coins_total": len(COINS), "scored_now": newly,
                      "size_high": high, "size_low": low, "history_days": summ["days"], "points": summ["points"],
                      "size_ratio": summ["size"]["all"]["ratio_high_low"],
                      "verdict": "%d/%d 종목 예측 — 앞 12h 폭 상위 %d·하위 %d종목, 누적 채점 %d일 %d건, 크기 비율 %s" % (
                          len(ok), len(COINS), len(high), len(low), summ["days"], summ["points"],
                          summ["size"]["all"]["ratio_high_low"] if summ["size"]["all"]["ratio_high_low"] is not None else "-")}
    return out, rows


def print_summary(out):
    s = out["summary"]
    print("=== 예측 v2 %s (%s) ===" % (out["date"], out["source"]))
    print(s["verdict"])
    print("%-6s %-16s %-5s %8s  %6s %6s %6s  %s" % ("코인", "봉(UTC)", "폭분위", "예상폭%", "D1", "D2", "D3", "D2 n"))
    for coin, p in out["coins"].items():
        if not p.get("ok"):
            print("%-6s %s" % (coin, p.get("reason")))
            continue
        d = p["direction"]
        print("%-6s %-16s %-5s %8s  %6.3f %6.3f %6.3f  %d%s" % (coin, p["bar_utc"], p["size"]["bucket"], p["size"]["expected_abs_pct"],
                                                             d["D1"]["p_up"], d["D2"]["p_up"], d["D3"]["p_up"], d["D2"]["n"],
                                                             " (대체)" if d["D2"]["fallback"] else ""))
    c = out.get("cumulative") or {}
    if c.get("points"):
        sz = c["size"]["all"]; dr = c["direction"]["all"]
        print("누적 채점 %d일 %d건 (%s~%s, 결손 %d일): 크기 상위 %s%% / 하위 %s%% 비율 %s | 방향 적중 D1 %s D2 %s D3 %s | Brier D1 %s D2 %s D3 %s" % (
            c["days"], c["points"], c["first_date"], c["last_date"], c["missing_days"],
            sz["mean_abs_pct"]["high"], sz["mean_abs_pct"]["low"], sz["ratio_high_low"],
            dr["D1"]["hit_rate"], dr["D2"]["hit_rate"], dr["D3"]["hit_rate"], dr["D1"]["brier"], dr["D2"]["brier"], dr["D3"]["brier"]))
    for k, e in out["errors"].items():
        print("[오류] %s: %s" % (k, e))


def print_report(path=HISTORY_PATH):
    rows = read_history(path)
    if not rows:
        print("예측 기록 없음")
        return
    c = score_summary(rows)
    print("예측 기록 %d일 (%s~%s), 채점 %d일 %d건, 결손 %d일" % (len(rows), rows[0]["date"], rows[-1]["date"], c["days"], c["points"], c["missing_days"]))
    for part in ("all", "first_half", "second_half"):
        sz = c["size"][part]; dr = c["direction"][part]
        print("  %-12s 크기 n=%s 평균|r| %s 비율 %s | 방향 적중 D1 %s D2 %s D3 %s | Brier D1 %s D2 %s D3 %s" % (
            part, sz["n"], sz["mean_abs_pct"], sz["ratio_high_low"], dr["D1"]["hit_rate"], dr["D2"]["hit_rate"], dr["D3"]["hit_rate"],
            dr["D1"]["brier"], dr["D2"]["brier"], dr["D3"]["brier"]))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fixture", default=None)
    ap.add_argument("--save-fixture", default=None, help="거래소에서 받은 2시간봉을 이 폴더에 CSV 로 저장")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)
    if a.report:
        print_report()
        return
    out, rows = build(a.fixture, a.save_fixture)
    print_summary(out)
    if not a.dry_run:
        with open(OUT_PATH, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2)
        write_history(rows)
        print("저장: forecast.json, forecast_history.jsonl")


if __name__ == "__main__":
    main()
