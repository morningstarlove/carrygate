# -*- coding: utf-8 -*-
"""
CARRYGATE — 펀딩 극단값 역추세 연구 (③)

펀딩비는 "오른다에 건 사람이 얼마나 많은가"를 보여주는 온도계다. 캐리(funding.py)는 그 이자를
받는 쪽이고, 이 파일은 **그 온도계를 방향 신호로** 쓸 수 있는지 본다.
가설: 펀딩이 과거 대비 아주 높은 날(과열)은 이후 1~7일 가격이 평균보다 약하고, 아주 낮은
날(공포)은 이후가 평균보다 강하다.

자료: 하이퍼리퀴드(1시간 정산)·OKX(8시간 정산) 펀딩 이력 약 6개월 + 일봉. 매일 다시 받아
사후 검증을 갱신하고, 오늘의 백분위(신호)를 기록한다.
주문 기능 없음. 읽기 전용 공개 API 만 쓴다.

사용법
  python funding_signal.py            # 수집·검증·저장 (funding_signal.json, funding_signal_history.jsonl)
  python funding_signal.py --dry-run
  python funding_signal.py --report
"""
import json, sys, time, math, argparse
from datetime import datetime, timezone

from scalp import http_json, f, KST, COINS

LOOKBACK_DAYS = 180
SIGNAL_AVG_DAYS = 3          # 신호 = 최근 3일 평균 일간 펀딩
HI_PCT, LO_PCT = 90, 10
FWD = (1, 3, 7)
HOLD_DAYS = 3                # 규칙 검증: 신호 다음날 시가 진입, 3일 뒤 시가 청산
TAKER_PCT = {"hyperliquid": 0.045, "okx": 0.05}


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def r4(x):
    return None if x is None else round(x, 4)


def day_of(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")


# ----------------------------------------------------------------- 수집

def hl_funding(coin, start, end):
    """[(sec, rate)] 1시간 정산. 500개씩 페이지."""
    out, t0 = [], start * 1000
    for _ in range(40):
        rows = http_json("https://api.hyperliquid.xyz/info",
                         data={"type": "fundingHistory", "coin": coin, "startTime": t0, "endTime": end * 1000})
        if not rows:
            break
        for r in rows:
            t, rate = f(r.get("time")), f(r.get("fundingRate"))
            if t is not None and rate is not None:
                out.append((int(t) // 1000, rate))
        last = max(int(f(r["time"])) for r in rows)
        if len(rows) < 500 or last <= t0:
            break
        t0 = last + 1
        time.sleep(0.1)
    return sorted(set(out))


def hl_daily(coin, start, end):
    rows = http_json("https://api.hyperliquid.xyz/info",
                     data={"type": "candleSnapshot", "req": {"coin": coin, "interval": "1d",
                                                             "startTime": start * 1000, "endTime": end * 1000}})
    return {day_of(int(r["t"]) // 1000): (f(r["o"]), f(r["c"])) for r in rows}


def okx_funding(coin, start, end):
    out, after = [], None
    for _ in range(12):
        url = "https://www.okx.com/api/v5/public/funding-rate-history?instId=%s-USDT-SWAP&limit=100" % coin
        if after:
            url += "&after=%d" % after
        rows = http_json(url).get("data") or []
        if not rows:
            break
        for r in rows:
            t, rate = f(r.get("fundingTime")), f(r.get("realizedRate") or r.get("fundingRate"))
            if t is not None and rate is not None:
                out.append((int(t) // 1000, rate))
        after = min(int(f(r["fundingTime"])) for r in rows)
        if after // 1000 < start:
            break
        time.sleep(0.12)
    return sorted(set((t, r) for t, r in out if start <= t < end))


def okx_daily(coin, start, end):
    """OKX 일봉. candles 엔드포인트는 한 번에 300개(약 10개월)를 준다.
    bar=1Dutc 로 받아야 00:00 UTC 시작이라 펀딩 일합(UTC 날짜)·하이퍼리퀴드 일봉(UTC)과 같은 날짜가 된다 —
    그냥 1D 는 홍콩시간(UTC+8) 기준이라 "다음날 시가" 가 실제로는 당일 16:00 UTC 가격이었다(2026-10-05 수정)."""
    rows = http_json("https://www.okx.com/api/v5/market/candles?instId=%s-USDT-SWAP&bar=1Dutc&limit=300" % coin).get("data") or []
    out = {}
    for r in rows:
        t = int(r[0]) // 1000
        if start <= t < end + 86400:
            out[day_of(t)] = (f(r[1]), f(r[4]))
    return out


SOURCES = {"hyperliquid": (hl_funding, hl_daily), "okx": (okx_funding, okx_daily)}


# ----------------------------------------------------------------- 계산

def daily_funding(pairs):
    """정산 건별 비율 -> UTC 날짜별 합(%). 하루에 받는/내는 펀딩 총량."""
    out = {}
    for t, rate in pairs:
        d = day_of(t)
        out[d] = out.get(d, 0.0) + rate * 100.0
    return out


def percentile_rank(hist, x):
    return 100.0 * sum(1 for h in hist if h <= x) / len(hist) if hist else None


def study(fund_by_day, px_by_day, taker_pct):
    """날짜순으로 신호(3일 평균 펀딩의 백분위)를 만들고 이후 수익률을 모은다.

    규칙 검증: 과열(≥90)이면 다음날 시가에 숏, 냉각(≤10)이면 롱, HOLD_DAYS 뒤 시가 청산.
    숏은 보유 중 펀딩을 받고(양수일 때), 롱은 낸다. 비용 = 시장가 2회."""
    days = sorted(d for d in px_by_day if d in fund_by_day)
    sig = []
    for i, d in enumerate(days):
        w = [fund_by_day[x] for x in days[max(0, i - SIGNAL_AVG_DAYS + 1):i + 1]]
        sig.append(mean(w))
    buckets = {"high": [], "low": [], "mid": []}
    trades = []
    for i in range(30, len(days)):
        pr = percentile_rank(sig[:i], sig[i])
        b = "high" if pr >= HI_PCT else ("low" if pr <= LO_PCT else "mid")
        rec = {}
        c0 = px_by_day[days[i]][1]
        for h in FWD:
            if i + h < len(days):
                rec["fwd%d" % h] = (px_by_day[days[i + h]][1] / c0 - 1.0) * 100.0
        if rec:
            buckets[b].append(rec)
        if b != "mid" and i + 1 + HOLD_DAYS < len(days):
            side = -1 if b == "high" else 1
            o_in, o_out = px_by_day[days[i + 1]][0], px_by_day[days[i + 1 + HOLD_DAYS]][0]
            gross = side * (o_out / o_in - 1.0) * 100.0
            fund = sum(fund_by_day.get(days[j], 0.0) for j in range(i + 1, i + 1 + HOLD_DAYS))
            carry = -side * fund          # 숏(-1)이면 +펀딩 수취, 롱이면 지불
            trades.append({"date": days[i], "side": side, "gross_pct": gross, "funding_pct": carry,
                           "net_pct": gross + carry - 2 * taker_pct})
    out = {}
    for b, rs in buckets.items():
        s = {"n": len(rs)}
        for h in FWD:
            xs = [r["fwd%d" % h] for r in rs if "fwd%d" % h in r]
            s["fwd%d_pct" % h] = r4(mean(xs))
            s["fwd%d_pos_pct" % h] = round(100.0 * sum(1 for x in xs if x > 0) / len(xs), 1) if xs else None
        out[b] = s
    rule = {"n": len(trades),
            "net_sum_pct": r4(sum(t["net_pct"] for t in trades)),
            "net_avg_pct": r4(mean([t["net_pct"] for t in trades])),
            "gross_avg_pct": r4(mean([t["gross_pct"] for t in trades])),
            "funding_avg_pct": r4(mean([t["funding_pct"] for t in trades])),
            "win_rate_pct": round(100.0 * sum(1 for t in trades if t["net_pct"] > 0) / len(trades), 1) if trades else None,
            "shorts": sum(1 for t in trades if t["side"] == -1), "longs": sum(1 for t in trades if t["side"] == 1)}
    today_sig = sig[-1] if sig else None
    today_pr = percentile_rank(sig[:-1], today_sig) if len(sig) > 30 else None
    return {"days": len(days), "buckets": out, "rule": rule,
            "signal_today": {"date": days[-1] if days else None, "funding_3d_avg_pct": r4(today_sig),
                             "percentile": round(today_pr, 1) if today_pr is not None else None,
                             "state": ("과열" if today_pr is not None and today_pr >= HI_PCT else
                                       "냉각" if today_pr is not None and today_pr <= LO_PCT else "중립")}}


# ----------------------------------------------------------------- 저장·보고

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


def pooled(results):
    """시장별 결과를 표본 수로 합쳐 전체 평균을 낸다."""
    agg = {}
    for b in ("high", "low", "mid"):
        n = sum(r["buckets"][b]["n"] for r in results)
        s = {"n": n}
        for h in FWD:
            pairs = [(r["buckets"][b]["fwd%d_pct" % h], r["buckets"][b]["n"]) for r in results
                     if r["buckets"][b].get("fwd%d_pct" % h) is not None]
            tot = sum(w for _, w in pairs)
            s["fwd%d_pct" % h] = r4(sum(v * w for v, w in pairs) / tot) if tot else None
        agg[b] = s
    rn = sum(r["rule"]["n"] for r in results)
    agg["rule"] = {"n": rn,
                   "net_avg_pct": r4(sum((r["rule"]["net_avg_pct"] or 0) * r["rule"]["n"] for r in results) / rn) if rn else None,
                   "net_sum_pct": r4(sum(r["rule"]["net_sum_pct"] or 0 for r in results))}
    return agg


def report(path="funding_signal_history.jsonl"):
    rows = []
    try:
        with open(path, encoding="utf-8") as fp:
            rows = [json.loads(l) for l in fp if l.strip()]
    except IOError:
        pass
    rows.sort(key=lambda r: r.get("date", ""))
    if not rows:
        print("기록 없음 — funding_signal.py 가 하루 한 번 실행되면서 쌓인다.")
        return 0
    print("=== 펀딩 신호 날짜별 상태 (코인: 백분위) ===")
    for r in rows:
        print("%-12s %s" % (r["date"], "  ".join("%s %s(%.0f)" % (k, v["state"], v["percentile"] or 0)
                                                  for k, v in sorted(r.get("signals", {}).items()) if v.get("percentile") is not None)))
    last = rows[-1]
    p = last.get("pooled", {})
    print()
    print("=== 최근 날 사후 검증 (전체 시장 합산, 약 %d일) ===" % last.get("lookback_days", 0))
    for b, name in (("high", "과열(상위10%)"), ("low", "냉각(하위10%)"), ("mid", "중립")):
        s = p.get(b, {})
        print("%-12s n=%-4d 1일후 %s%%  3일후 %s%%  7일후 %s%%" % (name, s.get("n", 0), s.get("fwd1_pct"), s.get("fwd3_pct"), s.get("fwd7_pct")))
    r = p.get("rule", {})
    print("규칙(과열 숏·냉각 롱, 3일 보유, 펀딩·수수료 반영): 매매 %s건, 건당 순 %s%%, 합계 %s%%" % (r.get("n"), r.get("net_avg_pct"), r.get("net_sum_pct")))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--out", default="funding_signal.json")
    ap.add_argument("--history", default="funding_signal_history.jsonl")
    ap.add_argument("--days", type=int, default=LOOKBACK_DAYS)
    a = ap.parse_args(argv)
    if a.report:
        return report(a.history)

    end = int(datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    start = end - a.days * 86400
    markets, errors = {}, {}
    for venue, (fn_f, fn_p) in SOURCES.items():
        for c in COINS:
            try:
                fund = daily_funding(fn_f(c, start, end))
                px = fn_p(c, start, end)
                if len(fund) < 40 or len(px) < 40:
                    raise RuntimeError("자료 부족 (펀딩 %d일, 일봉 %d일)" % (len(fund), len(px)))
                res = study(fund, px, TAKER_PCT[venue])
                res.update({"venue": venue, "coin": c})
                markets["%s:%s" % (venue, c)] = res
            except Exception as e:
                errors["%s/%s" % (venue, c)] = str(e)[:150]
            time.sleep(0.15)

    results = list(markets.values())
    pool = pooled(results) if results else {}
    signals = {k: m["signal_today"] for k, m in markets.items()}
    hot = [k for k, s in signals.items() if s["state"] == "과열"]
    cold = [k for k, s in signals.items() if s["state"] == "냉각"]
    if results:
        hi, lo, mid = pool["high"], pool["low"], pool["mid"]
        verdict = ("과열 뒤 3일 %s%% (n=%d) vs 냉각 뒤 %s%% (n=%d) vs 중립 %s%%. 규칙 건당 순 %s%% (n=%d). 오늘 과열 %s / 냉각 %s"
                   % (hi.get("fwd3_pct"), hi["n"], lo.get("fwd3_pct"), lo["n"], mid.get("fwd3_pct"),
                      pool["rule"].get("net_avg_pct"), pool["rule"]["n"], ", ".join(hot) or "없음", ", ".join(cold) or "없음"))
    else:
        verdict = "데이터 없음"

    now_kst = datetime.now(KST)
    out = {"schema": "carrygate-funding-signal/1", "date": now_kst.strftime("%Y-%m-%d"),
           "generated_at_kst": now_kst.strftime("%Y-%m-%d %H:%M:%S"), "lookback_days": a.days,
           "method": {"signal": "최근 %d일 평균 일간 펀딩(%%)의 과거 대비 백분위. ≥%d 과열, ≤%d 냉각" % (SIGNAL_AVG_DAYS, HI_PCT, LO_PCT),
                      "study": "신호일 종가 대비 %s일 뒤 종가 수익률을 과열/냉각/중립으로 나눠 평균" % (FWD,),
                      "rule": "과열이면 다음날 시가 숏, 냉각이면 롱, %d일 뒤 시가 청산. 보유 중 펀딩(숏 수취·롱 지불)과 시장가 수수료 2회 반영" % HOLD_DAYS},
           "markets": markets, "pooled": pool, "signals": signals, "errors": errors,
           "summary": {"hot": hot, "cold": cold, "verdict": verdict}}

    print("=== 펀딩 극단값 역추세 (%s, %d일) ===" % (out["date"], a.days))
    print("%-18s %5s %7s %8s | %10s %10s %10s | %8s %8s %6s" % ("시장", "일수", "오늘%", "백분위", "과열3일후", "냉각3일후", "중립3일후", "규칙n", "건당순%", "승률"))
    for k, m in markets.items():
        b, r, s = m["buckets"], m["rule"], m["signal_today"]
        print("%-18s %5d %7s %8s | %10s %10s %10s | %8d %8s %6s"
              % (k, m["days"], s["funding_3d_avg_pct"], s["percentile"],
                 "%s(n%d)" % (b["high"].get("fwd3_pct"), b["high"]["n"]), "%s(n%d)" % (b["low"].get("fwd3_pct"), b["low"]["n"]),
                 "%s" % b["mid"].get("fwd3_pct"), r["n"], r["net_avg_pct"], r["win_rate_pct"]))
    if errors:
        print("오류:", errors)
    print("판정:", verdict)
    if a.dry_run:
        print("\n(dry-run: 저장하지 않음)")
        return 0
    with open(a.out, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    append_history({"date": out["date"], "lookback_days": a.days, "signals": signals, "pooled": pool,
                    "summary": out["summary"]}, a.history)
    return 0 if markets else 1


if __name__ == "__main__":
    sys.exit(main())
