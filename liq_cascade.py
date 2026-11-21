# -*- coding: utf-8 -*-
"""
CARRYGATE — 청산 추종 순방향 기록 (제안함 P40, 사용자 결정 2026-11-18 "B"). 주문 없음, 키 없음, 기록만.

SNS 글 "청산 알림 하나로 매매하는 법"의 주장 — "비트 롱 줄청산이 나면 한동안 계속 밀린다, 이더 숏이 더 좋다" — 을
**진짜 청산 흐름**으로 확인한다. 봉 자료로는 우위가 없었다(docs/LIQUIDATION_CASCADE_REVIEW.md) — 남은 가능성은 봉이
닫히기 전 몇 초뿐이라 초 단위로 센다.

자료: 사용자 가상서버의 vps_liq_collector.py 가 받아 둔 파일(Caddy /carrygate/liq/).
  liq_YYYYMMDD.jsonl  [받은 시각 초, 거래소 시각 ms, 종목, 방향, 금액 USDT, 평균가]
  px_YYYYMMDD.csv.gz  sec,btc_lo,btc_hi,btc_last,eth_lo,eth_hi,eth_last (1초 안 최저·최고·마지막 체결가)

규칙 (결과를 보기 전에 고정, RULE_FIXED):
  신호  : BTCUSDT **롱 청산**(방향 SELL) 금액의 최근 60초 합이 문턱 이상이 되는 순간. 시각은 **서버가 받은 시각**(실제로 알 수 있던 때).
          문턱 3개를 모두 기록한다 — 50만·**100만(주)**·300만 USDT. 신호 뒤 300초는 같은 문턱의 새 신호 없음.
  진입  : 신호 1초 뒤, ETHUSDT 숏. 체결가는 그 1초의 **최저 체결가**(불리한 쪽).
  청산  : 진입 5·30·**60(주)**·300·900초 뒤, 그 1초의 **최고 체결가**(불리한 쪽).
  비용  : 왕복 0.10% (바이낸스 선물 시장가 0.05% × 2). 호가폭·미끄러짐은 불리한 쪽 체결가로 대신한다.
  보조  : 같은 신호에서 BTC 숏 / 반대쪽(숏 청산 BUY → ETH 롱). 기록만.
  날    : UTC 하루. 가격이 하루의 90% 이상 받아진 날만 센다. 청산이 다음 날로 넘어가는 신호(23:44:58 이후)는 뺀다.
  판단  : 센 날 14일이 차면 주 칸(100만·60초) — 건수 ≥ 30 · 건당 순 > 0 · 순 승률 ≥ 50% 셋 다면 "후보"(carrygate-exec 설계 제안).
          건수 < 30 이면 판단 보류(계속 기록). 다른 칸이 좋아 보여도 결과를 보고 주 칸을 바꾸지 않는다.

  python liq_cascade.py            # 가상서버에서 최근 4일 중 아직 안 센 날을 받아 센다
  python liq_cascade.py --report   # 누적 표
  python liq_cascade.py --dir <폴더>   # 인터넷 없이 폴더의 파일로 (시험용)
"""
import os, sys, json, gzip, math, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

RULE_FIXED = "2026-11-18"
SIGNAL_SYM = "BTCUSDT"
WINDOW_SEC = 60
THRESHOLDS = [500_000, 1_000_000, 3_000_000]
MAIN_TH = 1_000_000
COOLDOWN_SEC = 300
ENTRY_DELAY_SEC = 1
HOLDS = [5, 30, 60, 300, 900]
MAIN_HOLD = 60
FEE_RT_PCT = 0.10
MIN_COVERAGE = 0.90
PX_GAP_SEC = 5            # 그 초의 가격이 없으면 이만큼 앞까지 찾아 쓴다. 그래도 없으면 그 건은 뺀다
JUDGE_DAYS = 14
JUDGE_MIN_N = 30
JUDGE_MIN_WIN = 50.0
LOOKBACK_DAYS = 4

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_JSON = os.path.join(HERE, "liq_cascade.json")
OUT_HIST = os.path.join(HERE, "liq_cascade_history.jsonl")
RELAY_URL = os.environ.get("CARRYGATE_LIQ_URL", "http://158.247.206.213/carrygate/liq/")
COL = {"BTCUSDT": 1, "ETHUSDT": 4}     # px 줄에서 lo 의 위치 (lo, hi, last 순)


def rules_dict():
    return {"signal_symbol": SIGNAL_SYM, "window_sec": WINDOW_SEC, "thresholds_usdt": THRESHOLDS, "main_threshold_usdt": MAIN_TH,
            "cooldown_sec": COOLDOWN_SEC, "entry_delay_sec": ENTRY_DELAY_SEC, "holds_sec": HOLDS, "main_hold_sec": MAIN_HOLD,
            "fee_round_trip_pct": FEE_RT_PCT, "fill": "short: entry at second low, exit at second high (adverse side)",
            "min_coverage": MIN_COVERAGE, "px_gap_sec": PX_GAP_SEC,
            "judge": {"days": JUDGE_DAYS, "min_n": JUDGE_MIN_N, "avg_net_gt": 0.0, "min_win_pct": JUDGE_MIN_WIN}}


# ----------------------------------------------------------------- 자료 읽기

def parse_liq_lines(text):
    out = []
    for line in text.splitlines():
        try:
            r = json.loads(line)
            out.append((float(r[0]), str(r[2]), str(r[3]), float(r[4])))
        except (ValueError, IndexError, TypeError):
            continue
    out.sort()
    return out


def parse_px(text):
    px = {}
    for line in text.splitlines():
        p = line.split(",")
        if len(p) != 7 or not p[0].isdigit():
            continue
        try:
            px[int(p[0])] = tuple(float(x) for x in p[1:])
        except ValueError:
            continue
    return px


def http_bytes(url):
    req = urllib.request.Request(url, headers={"User-Agent": "carrygate-liq/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def load_day(day, src_dir=None, base=None):
    """day='YYYYMMDD' → (청산 목록, 가격 dict). 파일이 없으면 예외."""
    names = ["liq_%s.jsonl" % day, "px_%s.csv.gz" % day]
    if src_dir:
        raw = []
        for n in names:
            p = os.path.join(src_dir, n)
            if not os.path.exists(p) and n.endswith(".gz"):
                p = p[:-3]
            raw.append(open(p, "rb").read() if os.path.exists(p) else (b"" if n.startswith("liq_") else None))
        if raw[1] is None:
            raise FileNotFoundError(names[1])
    else:
        b = (RELAY_URL if base is None else base).rstrip("/") + "/"
        try:
            liq_raw = http_bytes(b + names[0])
        except urllib.error.HTTPError as e:      # 청산이 한 건도 없던 날은 파일이 없다
            if e.code != 404:
                raise
            liq_raw = b""
        raw = [liq_raw, http_bytes(b + names[1])]
    px_bytes = gzip.decompress(raw[1]) if raw[1][:2] == b"\x1f\x8b" else raw[1]
    return parse_liq_lines(raw[0].decode("utf-8", "replace")), parse_px(px_bytes.decode("utf-8", "replace"))


# ----------------------------------------------------------------- 규칙

def signals(liqs, threshold, side, sym=SIGNAL_SYM):
    """최근 WINDOW_SEC 초 합이 threshold 이상이 되는 순간(받은 시각, 초 올림) 목록. 신호 뒤 COOLDOWN_SEC 는 쉰다."""
    out, win, total, last = [], [], 0.0, -1e18
    for t, s, sd, amt in liqs:
        if s != sym or sd != side:
            continue
        win.append((t, amt))
        total += amt
        while win and win[0][0] <= t - WINDOW_SEC:
            total -= win.pop(0)[1]
        if total >= threshold and t - last >= COOLDOWN_SEC:
            out.append({"t": int(math.ceil(t)), "sum_usdt": round(total, 0)})
            last = t
    return out


def px_at(px, sec, sym, which):
    """sec 초의 가격. which: 0=lo, 1=hi. 없으면 PX_GAP_SEC 초 앞까지."""
    for back in range(PX_GAP_SEC + 1):
        row = px.get(sec - back)
        if row:
            return row[COL[sym] - 1 + which]
    return None


def trade(px, t, hold, pos, sym):
    """신호 t → 진입 t+지연, 청산 +hold. pos −1 숏: 최저가에 팔고 최고가에 되산다. +1 롱: 최고가에 사고 최저가에 판다. 총수익 %."""
    te = t + ENTRY_DELAY_SEC
    e = px_at(px, te, sym, 0 if pos < 0 else 1)
    x = px_at(px, te + hold, sym, 1 if pos < 0 else 0)
    if not e or not x:
        return None
    return pos * (x / e - 1.0) * 100.0


def cell(rets):
    n = len(rets)
    nets = [r - FEE_RT_PCT for r in rets]
    return {"n": n, "gross_sum": round(sum(rets), 4), "net_sum": round(sum(nets), 4), "wins": sum(1 for x in nets if x > 0),
            "net_sq": round(sum(x * x for x in nets), 6)}


def evaluate_day(day, liqs, px):
    d0 = int(datetime.strptime(day, "%Y%m%d").replace(tzinfo=timezone.utc).timestamp())
    cov = sum(1 for s in px if d0 <= s < d0 + 86400) / 86400.0
    last_ok = d0 + 86400 - ENTRY_DELAY_SEC - max(HOLDS) - 1
    row = {"date": "%s-%s-%s" % (day[:4], day[4:6], day[6:]), "rule_fixed": RULE_FIXED, "coverage": round(cov, 4),
           "counted": cov >= MIN_COVERAGE, "liq_events": len(liqs),
           "btc_long_liq_usdt": round(sum(a for _, s, sd, a in liqs if s == SIGNAL_SYM and sd == "SELL"), 0),
           "btc_short_liq_usdt": round(sum(a for _, s, sd, a in liqs if s == SIGNAL_SYM and sd == "BUY"), 0),
           "cells": {}, "main_trades": []}
    legs = (("eth_short", "SELL", -1, "ETHUSDT"), ("btc_short", "SELL", -1, "BTCUSDT"), ("eth_long_mirror", "BUY", 1, "ETHUSDT"))
    for th in THRESHOLDS:
        for leg, side, pos, sym in legs:
            sig = [s for s in signals(liqs, th, side) if d0 <= s["t"] <= last_ok]
            for h in HOLDS:
                rets = []
                for s in sig:
                    r = trade(px, s["t"], h, pos, sym)
                    if r is None:
                        continue
                    rets.append(r)
                    if leg == "eth_short" and th == MAIN_TH and h == MAIN_HOLD:
                        row["main_trades"].append({"t": s["t"], "sum_usdt": s["sum_usdt"], "gross_pct": round(r, 4), "net_pct": round(r - FEE_RT_PCT, 4)})
                row["cells"]["%s|%d|%d" % (leg, th, h)] = cell(rets)
    return row


# ----------------------------------------------------------------- 누적·판단

def read_history(path=OUT_HIST):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def cumulative(rows):
    rows = [r for r in rows if r.get("counted") and r.get("rule_fixed") == RULE_FIXED]
    agg = {}
    for r in rows:
        for k, c in r["cells"].items():
            a = agg.setdefault(k, {"n": 0, "gross_sum": 0.0, "net_sum": 0.0, "wins": 0, "net_sq": 0.0})
            for f in a:
                a[f] += c[f]
    out = {}
    for k, a in agg.items():
        n = a["n"]
        avg = a["net_sum"] / n if n else None
        var = (a["net_sq"] - n * avg * avg) / (n - 1) if n > 1 else None
        t = avg / math.sqrt(var / n) if var and var > 0 else None
        out[k] = {"n": n, "avg_gross_pct": round(a["gross_sum"] / n, 4) if n else None, "avg_net_pct": round(avg, 4) if n else None,
                  "win_pct": round(100.0 * a["wins"] / n, 1) if n else None, "net_sum_pct": round(a["net_sum"], 3), "t": round(t, 2) if t is not None else None}
    return len(rows), out


def judge(days, cum):
    m = cum.get("eth_short|%d|%d" % (MAIN_TH, MAIN_HOLD)) or {"n": 0}
    if days < JUDGE_DAYS:
        return {"state": "기록 중", "note": "센 날 %d/%d일, 주 칸 %d건" % (days, JUDGE_DAYS, m["n"])}
    if m["n"] < JUDGE_MIN_N:
        return {"state": "판단 보류", "note": "주 칸 %d건 < %d건 — 계속 기록" % (m["n"], JUDGE_MIN_N)}
    ok = m["avg_net_pct"] > 0 and m["win_pct"] >= JUDGE_MIN_WIN
    return {"state": "후보" if ok else "탈락", "note": "주 칸 %d건, 건당 순 %+.4f%%, 순 승률 %.1f%%" % (m["n"], m["avg_net_pct"], m["win_pct"])}


def build(src_dir=None, now=None, hist_path=OUT_HIST, base=None):
    now = now or datetime.now(timezone.utc)
    rows = read_history(hist_path)
    have = {r["date"] for r in rows}
    errors, new = {}, []
    for back in range(LOOKBACK_DAYS, 0, -1):
        d = now - timedelta(days=back)
        if d.strftime("%Y-%m-%d") in have:
            continue
        day = d.strftime("%Y%m%d")
        try:
            liqs, px = load_day(day, src_dir, base)
            new.append(evaluate_day(day, liqs, px))
        except Exception as e:
            errors[day] = "%s: %s" % (type(e).__name__, e)
    rows += new
    days, cum = cumulative(rows)
    out = {"date": now.strftime("%Y-%m-%d"), "generated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "rules": dict(rules_dict(), rule_fixed=RULE_FIXED), "source": src_dir or (RELAY_URL if base is None else base),
           "days_counted": days, "days_recorded": len(rows), "judge": judge(days, cum), "cumulative": cum,
           "last_day": rows[-1] if rows else None, "errors": errors}
    return out, new


def report(out):
    print("=== 청산 추종 순방향 기록 (P40, 규칙 고정 %s) ===" % RULE_FIXED)
    print("센 날 %d일 (기록 %d일) | 판단: %s — %s" % (out["days_counted"], out["days_recorded"], out["judge"]["state"], out["judge"]["note"]))
    for leg, name in (("eth_short", "비트 롱 청산 → 이더 숏"), ("btc_short", "비트 롱 청산 → 비트 숏(보조)"), ("eth_long_mirror", "비트 숏 청산 → 이더 롱(보조)")):
        print("[%s]" % name)
        print("  %-10s %6s %5s %10s %10s %7s %6s" % ("문턱", "보유초", "건수", "건당총%", "건당순%", "순승률", "t"))
        for th in THRESHOLDS:
            for h in HOLDS:
                c = out["cumulative"].get("%s|%d|%d" % (leg, th, h))
                if not c or not c["n"]:
                    continue
                star = "★" if (leg == "eth_short" and th == MAIN_TH and h == MAIN_HOLD) else " "
                print(" %s%-9s %6d %5d %+10.4f %+10.4f %6.1f%% %6s" % (star, "%.1fM" % (th / 1e6), h, c["n"], c["avg_gross_pct"], c["avg_net_pct"], c["win_pct"], c["t"] if c["t"] is not None else "-"))
    ld = out.get("last_day")
    if ld:
        print("마지막 날 %s: 받은 비율 %.1f%%, 청산 %d건, 비트 롱 청산 합 %s USDT" % (ld["date"], ld["coverage"] * 100, ld["liq_events"], format(int(ld["btc_long_liq_usdt"]), ",")))
    for k, v in out["errors"].items():
        print("  못 받음 %s: %s" % (k, v))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--report" in argv:
        if not os.path.exists(OUT_JSON):
            print("청산 추종 기록 없음")
            return 0
        report(json.load(open(OUT_JSON, encoding="utf-8")))
        return 0
    src = argv[argv.index("--dir") + 1] if "--dir" in argv else None
    out, new = build(src)
    with open(OUT_JSON, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    if new:
        with open(OUT_HIST, "a", encoding="utf-8") as fp:
            for r in new:
                fp.write(json.dumps(r, ensure_ascii=False) + "\n")
    report(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
