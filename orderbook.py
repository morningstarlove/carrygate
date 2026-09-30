# -*- coding: utf-8 -*-
"""
CARRYGATE — 호가창 표본 수집·분석 (C)

봉(캔들)에는 없는 정보 — 매수·매도 호가 잔량의 불균형과 실제 호가 폭 — 을 몇 시간 동안
일정 간격으로 찍어 두고, 그 불균형이 다음 1분·5분 가격 방향을 맞히는지, 호가 폭이
변동폭에 비해 얼마나 넓은지(지정가로 양쪽에 걸어 먹을 여지가 있는지)를 계산한다.

주문 기능 없음. 읽기 전용 공개 API 만 쓴다. API 키 불필요.

사용법
  python orderbook.py --minutes 330            # 5시간 30분 수집 후 분석·저장
  python orderbook.py --minutes 2 --dry-run    # 짧게 시험
  python orderbook.py --analyze orderbook_samples.csv   # 저장된 표본만 다시 분석
  python orderbook.py --report                 # 누적 요약
"""
import json, sys, time, csv, math, argparse, urllib.request
from datetime import datetime, timezone, timedelta

COINS = ["BTC", "ETH", "XRP", "TRX", "LINK", "DOGE"]
KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (compatible; carrygate/1.0)", "Accept": "application/json"}
DEPTH_LEVELS = 5          # 불균형 계산에 쓰는 호가 단수 (최우선 5호가)
IMB_THRESHOLD = 0.3       # "치우쳤다"고 보는 불균형 기준 ((매수−매도)/(매수+매도))
HORIZONS = (60, 300)      # 초. 불균형 뒤 1분·5분 가격 변화를 본다
MIN_DAYS = 5
FEES = {"upbit": {"taker_pct": 0.05, "maker_pct": 0.05},
        "hyperliquid": {"taker_pct": 0.045, "maker_pct": 0.015},
        "okx": {"taker_pct": 0.05, "maker_pct": 0.02}}


def http_json(url, data=None, timeout=15):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    hdr = dict(UA)
    if body is not None:
        hdr["Content-Type"] = "application/json"
    req = urllib.request.Request(url, headers=hdr, data=body)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------- 수집

def snap(bids, asks):
    """[(가격, 수량)] 최우선부터 -> 한 줄 표본. 잔량은 금액(가격×수량) 기준."""
    if not bids or not asks:
        return None
    bid, ask = bids[0][0], asks[0][0]
    if not bid or not ask or ask <= bid:
        return None
    bq = sum(p * q for p, q in bids[:DEPTH_LEVELS])
    aq = sum(p * q for p, q in asks[:DEPTH_LEVELS])
    return {"bid": bid, "ask": ask, "bid_notional": bq, "ask_notional": aq}


def fetch_upbit():
    d = http_json("https://api.upbit.com/v1/orderbook?markets=" + ",".join("KRW-%s" % c for c in COINS))
    out = {}
    for r in d:
        coin = r["market"].split("-")[1]
        units = r.get("orderbook_units") or []
        bids = [(f(u["bid_price"]), f(u["bid_size"])) for u in units]
        asks = [(f(u["ask_price"]), f(u["ask_size"])) for u in units]
        s = snap(bids, asks)
        if s:
            out[coin] = s
    return out


def fetch_hyperliquid():
    out = {}
    for c in COINS:
        d = http_json("https://api.hyperliquid.xyz/info", data={"type": "l2Book", "coin": c})
        lv = d.get("levels") or [[], []]
        bids = [(f(x["px"]), f(x["sz"])) for x in lv[0]]
        asks = [(f(x["px"]), f(x["sz"])) for x in lv[1]]
        s = snap(bids, asks)
        if s:
            out[c] = s
        time.sleep(0.05)
    return out


def fetch_okx():
    out = {}
    for c in COINS:
        d = http_json("https://www.okx.com/api/v5/market/books?instId=%s-USDT-SWAP&sz=%d" % (c, DEPTH_LEVELS))
        row = (d.get("data") or [None])[0]
        if not row:
            continue
        bids = [(f(x[0]), f(x[1])) for x in row.get("bids") or []]
        asks = [(f(x[0]), f(x[1])) for x in row.get("asks") or []]
        s = snap(bids, asks)
        if s:
            out[c] = s
        time.sleep(0.05)
    return out


SOURCES = [("upbit", fetch_upbit), ("hyperliquid", fetch_hyperliquid), ("okx", fetch_okx)]
CSV_FIELDS = ["ts", "venue", "coin", "bid", "ask", "bid_notional", "ask_notional"]


def collect(minutes, interval, path):
    """interval 초마다 세 거래소 호가를 찍어 CSV 에 바로 쓴다. 실패는 세고 계속한다."""
    end = time.time() + minutes * 60
    n, errors = 0, {}
    with open(path, "w", newline="", encoding="utf-8") as fp:
        w = csv.DictWriter(fp, fieldnames=CSV_FIELDS)
        w.writeheader()
        while time.time() < end:
            t0 = time.time()
            ts = int(t0)
            for venue, fn in SOURCES:
                try:
                    for coin, s in fn().items():
                        row = {"ts": ts, "venue": venue, "coin": coin}
                        row.update(s)
                        w.writerow(row)
                        n += 1
                except Exception as e:
                    errors[venue] = errors.get(venue, 0) + 1
                    errors[venue + "_last"] = str(e)[:100]
            fp.flush()
            time.sleep(max(0.0, interval - (time.time() - t0)))
    return n, errors


# ----------------------------------------------------------------- 분석

def load_samples(path):
    by = {}
    with open(path, encoding="utf-8") as fp:
        for r in csv.DictReader(fp):
            key = "%s:%s" % (r["venue"], r["coin"])
            by.setdefault(key, []).append({"ts": int(r["ts"]), "bid": float(r["bid"]), "ask": float(r["ask"]),
                                          "bq": float(r["bid_notional"]), "aq": float(r["ask_notional"])})
    for k in by:
        by[k].sort(key=lambda x: x["ts"])
    return by


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def std(xs):
    if len(xs) < 2:
        return None
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def corr(xs, ys):
    if len(xs) < 3:
        return None
    mx, my, sx, sy = mean(xs), mean(ys), std(xs), std(ys)
    if not sx or not sy:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (len(xs) * sx * sy)


def analyze_market(rows):
    """한 시장의 표본 -> 호가 폭, 변동폭, 불균형 예측력."""
    n = len(rows)
    if n < 30:
        return {"n": n}
    mids = [(r["bid"] + r["ask"]) / 2.0 for r in rows]
    spreads_bp = [(r["ask"] - r["bid"]) / m * 1e4 for r, m in zip(rows, mids)]
    imbs = [(r["bq"] - r["aq"]) / (r["bq"] + r["aq"]) if (r["bq"] + r["aq"]) > 0 else 0.0 for r in rows]
    ts = [r["ts"] for r in rows]

    # 시각 -> 그 시각 이후 첫 표본의 중간가 (앞으로 h초 뒤 가격)
    def fwd(i, h):
        target = ts[i] + h
        j = i
        while j < n and ts[j] < target:
            j += 1
        return mids[j] if j < n and ts[j] - target <= 30 else None

    out = {"n": n, "span_min": round((ts[-1] - ts[0]) / 60.0, 1),
           "spread_bp_mean": round(mean(spreads_bp), 3),
           "spread_bp_median": round(sorted(spreads_bp)[n // 2], 3),
           "imbalance_abs_mean": round(mean([abs(x) for x in imbs]), 3)}
    # 1분 변동폭: 60초 간격 중간가 변화의 표준편차 (bp)
    r60 = [(fwd(i, 60) / mids[i] - 1.0) * 1e4 for i in range(n) if fwd(i, 60)]
    out["mid_move_60s_std_bp"] = round(std(r60), 3) if len(r60) > 2 else None
    out["spread_over_move60"] = round(out["spread_bp_mean"] / out["mid_move_60s_std_bp"], 3) if out.get("mid_move_60s_std_bp") else None
    for h in HORIZONS:
        pairs = []
        for i in range(n):
            m2 = fwd(i, h)
            if m2:
                pairs.append((imbs[i], (m2 / mids[i] - 1.0) * 1e4))
        if len(pairs) < 30:
            continue
        xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
        up = [y for x, y in pairs if x >= IMB_THRESHOLD]
        dn = [y for x, y in pairs if x <= -IMB_THRESHOLD]
        signed = [y if x >= IMB_THRESHOLD else -y for x, y in pairs if abs(x) >= IMB_THRESHOLD]
        out["h%d" % h] = {
            "n": len(pairs), "corr": round(corr(xs, ys), 4) if corr(xs, ys) is not None else None,
            "n_bid_heavy": len(up), "ret_after_bid_heavy_bp": round(mean(up), 3) if up else None,
            "n_ask_heavy": len(dn), "ret_after_ask_heavy_bp": round(mean(dn), 3) if dn else None,
            "signed_ret_bp": round(mean(signed), 3) if signed else None,
            "hit_rate_pct": round(100.0 * sum(1 for y in signed if y > 0) / len(signed), 1) if signed else None}
    return out


def analyze(by, fees=FEES):
    markets = {k: analyze_market(v) for k, v in by.items()}
    by_venue = {}
    for k, m in markets.items():
        venue = k.split(":")[0]
        if m.get("n", 0) < 30:
            continue
        b = by_venue.setdefault(venue, {"markets": 0, "spread_bp": [], "signed60": [], "signed300": [], "hit60": [], "corr60": []})
        b["markets"] += 1
        b["spread_bp"].append(m["spread_bp_mean"])
        for h, key in ((60, "signed60"), (300, "signed300")):
            v = m.get("h%d" % h, {}).get("signed_ret_bp")
            if v is not None:
                b[key].append(v)
        v = m.get("h60", {}).get("hit_rate_pct")
        if v is not None:
            b["hit60"].append(v)
        v = m.get("h60", {}).get("corr")
        if v is not None:
            b["corr60"].append(v)
    summary = {}
    for venue, b in by_venue.items():
        fee = fees.get(venue, {})
        summary[venue] = {"markets": b["markets"],
                          "spread_bp_mean": round(mean(b["spread_bp"]), 3),
                          "maker_fee_bp": round(fee.get("maker_pct", 0) * 100, 2),
                          "taker_fee_bp": round(fee.get("taker_pct", 0) * 100, 2),
                          "imb_corr60_mean": round(mean(b["corr60"]), 4) if b["corr60"] else None,
                          "imb_signed60_bp": round(mean(b["signed60"]), 3) if b["signed60"] else None,
                          "imb_signed300_bp": round(mean(b["signed300"]), 3) if b["signed300"] else None,
                          "imb_hit60_pct": round(mean(b["hit60"]), 1) if b["hit60"] else None}
        # 지정가 양쪽 걸기 여지: 호가 폭의 절반이 지정가 수수료보다 커야 한 번 왕복에 남는다
        summary[venue]["half_spread_minus_maker_fee_bp"] = round(summary[venue]["spread_bp_mean"] / 2.0 - summary[venue]["maker_fee_bp"], 3)
    return markets, summary


def print_result(out):
    print("=== 호가창 연구 (%s, 표본 %d개, %s ~ %s UTC) ===" % (out["date"], out["samples"], out["window_utc"][0], out["window_utc"][1]))
    print("%-12s %5s %9s %9s %9s %10s %10s %8s %12s" % ("거래소", "시장", "호가폭bp", "지정가수수료", "반폭-수수료", "불균형→1분bp", "불균형→5분bp", "적중%", "불균형상관"))
    for v, s in out["summary"].items():
        print("%-12s %5d %9.2f %9.2f %9.2f %10s %10s %8s %12s"
              % (v, s["markets"], s["spread_bp_mean"], s["maker_fee_bp"], s["half_spread_minus_maker_fee_bp"],
                 "%+.2f" % s["imb_signed60_bp"] if s["imb_signed60_bp"] is not None else "-",
                 "%+.2f" % s["imb_signed300_bp"] if s["imb_signed300_bp"] is not None else "-",
                 "%.0f" % s["imb_hit60_pct"] if s["imb_hit60_pct"] is not None else "-",
                 "%.3f" % s["imb_corr60_mean"] if s["imb_corr60_mean"] is not None else "-"))
    print()
    print("--- 시장별 ---")
    print("%-18s %6s %8s %9s %10s %10s %8s" % ("시장", "표본", "호가폭bp", "1분변동bp", "폭/변동", "불균형→1분", "적중%"))
    for k, m in sorted(out["markets"].items()):
        if m.get("n", 0) < 30:
            print("%-18s %6d  (표본 부족)" % (k, m.get("n", 0)))
            continue
        h = m.get("h60", {})
        print("%-18s %6d %8.2f %9s %10s %10s %8s"
              % (k, m["n"], m["spread_bp_mean"],
                 "%.2f" % m["mid_move_60s_std_bp"] if m.get("mid_move_60s_std_bp") is not None else "-",
                 "%.2f" % m["spread_over_move60"] if m.get("spread_over_move60") is not None else "-",
                 "%+.2f" % h["signed_ret_bp"] if h.get("signed_ret_bp") is not None else "-",
                 "%.0f" % h["hit_rate_pct"] if h.get("hit_rate_pct") is not None else "-"))
    if out.get("errors"):
        print("수집 오류:", json.dumps(out["errors"], ensure_ascii=False))
    print()
    print("판정:", out["verdict"])


def verdict_of(summary):
    if not summary:
        return "표본 없음"
    parts = []
    for v, s in summary.items():
        mm = "지정가 양쪽 걸기 여지 %s(반폭−수수료 %+.2fbp)" % ("있음" if s["half_spread_minus_maker_fee_bp"] > 0 else "없음", s["half_spread_minus_maker_fee_bp"])
        im = ("불균형→1분 %+.2fbp 적중 %s%%" % (s["imb_signed60_bp"], s["imb_hit60_pct"])) if s["imb_signed60_bp"] is not None else "불균형 표본 부족"
        parts.append("%s: %s, %s" % (v, mm, im))
    return " / ".join(parts) + " — 하루치, 누적으로 판단"


def append_history(rec, path):
    try:
        with open(path, encoding="utf-8") as fp:
            rows = [l for l in fp.read().splitlines() if l.strip()]
    except IOError:
        rows = []
    rows = [l for l in rows if json.loads(l).get("date") != rec["date"]]
    rows.append(json.dumps(rec, ensure_ascii=False))
    with open(path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(rows) + "\n")


def report(path="orderbook_history.jsonl"):
    rows = []
    try:
        with open(path, encoding="utf-8") as fp:
            rows = [json.loads(l) for l in fp if l.strip()]
    except IOError:
        pass
    rows.sort(key=lambda r: r.get("date", ""))
    if not rows:
        print("기록 없음 — orderbook.py 가 하루 한 번 실행되면서 쌓인다.")
        return 0
    print("=== 호가창 연구 누적 (%d일치: %s ~ %s) ===" % (len(rows), rows[0]["date"], rows[-1]["date"]))
    venues = sorted(set(v for r in rows for v in r.get("summary", {})))
    print("%-12s %4s %9s %11s %12s %12s %8s %8s" % ("거래소", "일수", "호가폭bp", "반폭-수수료", "불균형→1분bp", "불균형→5분bp", "적중%", "플러스일"))
    for v in venues:
        ss = [r["summary"][v] for r in rows if v in r.get("summary", {})]
        sp = mean([s["spread_bp_mean"] for s in ss])
        hs = mean([s["half_spread_minus_maker_fee_bp"] for s in ss])
        s60 = [s["imb_signed60_bp"] for s in ss if s.get("imb_signed60_bp") is not None]
        s300 = [s["imb_signed300_bp"] for s in ss if s.get("imb_signed300_bp") is not None]
        hit = [s["imb_hit60_pct"] for s in ss if s.get("imb_hit60_pct") is not None]
        print("%-12s %4d %9.2f %11.2f %12s %12s %8s %5d/%d"
              % (v, len(ss), sp, hs, "%+.2f" % mean(s60) if s60 else "-", "%+.2f" % mean(s300) if s300 else "-",
                 "%.0f" % mean(hit) if hit else "-", sum(1 for x in s60 if x > 0), len(s60)))
    if len(rows) < MIN_DAYS:
        print("아직 %d일치. %d일 이상 모여야 방향을 말할 수 있다." % (len(rows), MIN_DAYS))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=330)
    ap.add_argument("--interval", type=int, default=10)
    ap.add_argument("--samples", default="orderbook_samples.csv")
    ap.add_argument("--analyze", help="수집 없이 이 CSV 만 분석")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--out", default="orderbook.json")
    ap.add_argument("--history", default="orderbook_history.jsonl")
    a = ap.parse_args(argv)
    if a.report:
        return report(a.history)

    errors, n = {}, 0
    if a.analyze:
        path = a.analyze
    else:
        path = a.samples
        n, errors = collect(a.minutes, a.interval, path)
    by = load_samples(path)
    markets, summary = analyze(by)
    ts_all = [r["ts"] for rows in by.values() for r in rows]
    fmt = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%m-%d %H:%M")
    now = datetime.now(KST)
    out = {"schema": "carrygate-orderbook/1", "date": now.strftime("%Y-%m-%d"),
           "generated_at_kst": now.strftime("%Y-%m-%d %H:%M:%S"),
           "samples": sum(len(v) for v in by.values()), "interval_sec": a.interval,
           "window_utc": [fmt(min(ts_all)), fmt(max(ts_all))] if ts_all else ["-", "-"],
           "method": {"depth": "최우선 %d호가 금액 합" % DEPTH_LEVELS,
                      "imbalance": "(매수잔량−매도잔량)/(합). |불균형| ≥ %.1f 을 '치우침'으로 보고, 치우친 방향의 %d·%d초 뒤 중간가 변화(bp)를 잰다" % (IMB_THRESHOLD, HORIZONS[0], HORIZONS[1]),
                      "spread": "호가 폭 = (매도1−매수1)/중간가. 반폭 − 지정가 수수료 > 0 이면 양쪽 지정가로 한 번 왕복에 남는 여지가 있다는 뜻 (체결·역선택은 별도)"},
           "markets": markets, "summary": summary, "errors": errors,
           "verdict": verdict_of(summary)}
    print_result(out)
    if a.dry_run:
        print("\n(dry-run: 저장하지 않음)")
        return 0
    with open(a.out, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    append_history({"date": out["date"], "samples": out["samples"], "window_utc": out["window_utc"],
                    "summary": summary, "verdict": out["verdict"]}, a.history)
    return 0 if out["samples"] else 1


if __name__ == "__main__":
    sys.exit(main())
