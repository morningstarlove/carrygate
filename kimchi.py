# -*- coding: utf-8 -*-
"""
CARRYGATE — 김치프리미엄 신호 (④)

김치프리미엄 = 업비트 원화 가격 ÷ (해외 거래소 달러 가격 × 환율) − 1.
국내 투자 수요가 몰리면 양수로 벌어지고, 식으면 좁아진다. 질문은 두 가지다.
  (1) 프리미엄이 높을 때(과열) 이후 며칠 업비트 수익률이 낮은가 — 일 단위 200일 사후 검증
  (2) 하루 안에서 프리미엄이 벌어졌다 되돌아오는 성질이 있나 — 1분봉(bars_cache) 분석

환율: 두나무(업비트 운영사) 고시 환율 API → 실패 시 frankfurter(ECB) 로 대체. 과거 환율은 frankfurter.
주문 기능 없음. 읽기 전용 공개 API 만 쓴다.

사용법
  python kimchi.py            # 수집·계산·저장 (kimchi.json, kimchi_history.jsonl)
  python kimchi.py --dry-run
  python kimchi.py --report
"""
import json, sys, time, math, argparse
from datetime import datetime, timezone, timedelta

from scalp import http_json, f, KST, COINS

DAYS = 200            # 일 단위 사후 검증 구간
HI_PCT, LO_PCT = 90, 10   # 과열/냉각 백분위
FWD = (1, 3, 7)       # 며칠 뒤 수익률을 보나
INTRADAY_HORIZON = 15 # 분. 하루 안 되돌림을 볼 때


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


def r4(x):
    return None if x is None else round(x, 4)


# ----------------------------------------------------------------- 환율

def fx_now():
    try:
        d = http_json("https://quotation-api-cdn.dunamu.com/v1/forex/recent?codes=FRX.KRWUSD")
        v = f(d[0].get("basePrice"))
        if v:
            return v, "두나무 고시 환율"
    except Exception:
        pass
    d = http_json("https://api.frankfurter.app/latest?from=USD&to=KRW")
    return f(d["rates"]["KRW"]), "frankfurter(ECB)"


def fx_history(days):
    """{날짜 'YYYY-MM-DD': USDKRW}. 주말·휴일은 직전 값으로 채운다."""
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days + 10)
    d = http_json("https://api.frankfurter.app/%s..%s?from=USD&to=KRW" % (start.isoformat(), end.isoformat()))
    rates = {k: f(v.get("KRW")) for k, v in (d.get("rates") or {}).items()}
    out, last = {}, None
    day = start
    while day <= end:
        k = day.isoformat()
        if rates.get(k):
            last = rates[k]
        if last:
            out[k] = last
        day += timedelta(days=1)
    return out


# ----------------------------------------------------------------- 일봉

def upbit_daily(coin, count=DAYS):
    rows = http_json("https://api.upbit.com/v1/candles/days?market=KRW-%s&count=%d" % (coin, count))
    out = {}
    for r in rows:
        # candle_date_time_utc 는 그 봉의 시작(00:00 UTC = 09:00 KST). 종가는 다음날 00:00 UTC 가격.
        out[r["candle_date_time_utc"][:10]] = f(r["trade_price"])
    return out


def okx_daily(coin, count=DAYS):
    """OKX 현물 일봉. candles 엔드포인트는 한 번에 300개를 준다."""
    rows = http_json("https://www.okx.com/api/v5/market/candles?instId=%s-USDT&bar=1D&limit=300" % coin).get("data") or []
    out = {}
    for r in rows:
        t = int(r[0]) // 1000
        out[datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")] = f(r[4])
    return out


def premium_series(up, ok, fx):
    """날짜순 [(date, premium_pct, upbit_close, okx_close)]"""
    out = []
    for d in sorted(set(up) & set(ok)):
        rate = fx.get(d)
        if rate and up[d] and ok[d]:
            out.append((d, (up[d] / (ok[d] * rate) - 1.0) * 100.0, up[d], ok[d]))
    return out


def percentile_rank(hist, x):
    if not hist:
        return None
    return 100.0 * sum(1 for h in hist if h <= x) / len(hist)


def event_study(series):
    """프리미엄 백분위(과거 전체 기준) 상위/하위일 때 이후 N일 업비트 수익률과, 해외 대비 상대 수익률."""
    n = len(series)
    rows = {"high": [], "low": [], "mid": []}
    for i in range(30, n):
        hist = [p for _, p, _, _ in series[:i]]
        pr = percentile_rank(hist, series[i][1])
        bucket = "high" if pr >= HI_PCT else ("low" if pr <= LO_PCT else "mid")
        rec = {}
        for h in FWD:
            if i + h < n:
                rec["up%d" % h] = (series[i + h][2] / series[i][2] - 1.0) * 100.0
                rec["rel%d" % h] = rec["up%d" % h] - (series[i + h][3] / series[i][3] - 1.0) * 100.0
                rec["prem_chg%d" % h] = series[i + h][1] - series[i][1]
        if rec:
            rows[bucket].append(rec)
    out = {}
    for b, rs in rows.items():
        s = {"n": len(rs)}
        for h in FWD:
            ups = [r["up%d" % h] for r in rs if "up%d" % h in r]
            rels = [r["rel%d" % h] for r in rs if "rel%d" % h in r]
            chg = [r["prem_chg%d" % h] for r in rs if "prem_chg%d" % h in r]
            s["upbit_fwd%d_pct" % h] = r4(mean(ups))
            s["rel_vs_okx_fwd%d_pct" % h] = r4(mean(rels))
            s["premium_change_fwd%d_pp" % h] = r4(mean(chg))
            s["upbit_fwd%d_pos_pct" % h] = round(100.0 * sum(1 for x in ups if x > 0) / len(ups), 1) if ups else None
        out[b] = s
    return out


# ----------------------------------------------------------------- 하루 안 (1분봉)

def intraday(up_bars, ok_bars, fx, eval_t0):
    U = {b["t"]: b["c"] for b in up_bars if b["t"] >= eval_t0}
    O = {b["t"]: b["c"] for b in ok_bars if b["t"] >= eval_t0}
    ts = sorted(set(U) & set(O))
    prem = {t: (U[t] / (O[t] * fx) - 1.0) * 100.0 for t in ts}
    vals = [prem[t] for t in ts]
    if len(vals) < 60:
        return {"n": len(vals)}
    m, s = mean(vals), std(vals)
    dev, chg = [], []
    h = INTRADAY_HORIZON * 60
    for t in ts:
        if t + h in prem:
            dev.append(prem[t] - m)
            chg.append(prem[t + h] - prem[t])
    return {"n": len(vals), "mean_pct": r4(m), "std_pp": r4(s), "min_pct": r4(min(vals)), "max_pct": r4(max(vals)),
            "range_pp": r4(max(vals) - min(vals)),
            "reversion_corr_%dm" % INTRADAY_HORIZON: r4(corr(dev, chg))}   # 음수면 벌어진 뒤 되돌아온다


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


def report(path="kimchi_history.jsonl"):
    rows = []
    try:
        with open(path, encoding="utf-8") as fp:
            rows = [json.loads(l) for l in fp if l.strip()]
    except IOError:
        pass
    rows.sort(key=lambda r: r.get("date", ""))
    if not rows:
        print("기록 없음 — kimchi.py 가 하루 한 번 실행되면서 쌓인다.")
        return 0
    coins = []
    for r in rows:
        for c in r.get("coins", {}):
            if c not in coins:
                coins.append(c)
    print("=== 날짜별 김치프리미엄 (%%) 와 200일 백분위 ===")
    print("%-12s %8s %s" % ("날짜", "환율", "".join("%14s" % c for c in coins)))
    for r in rows:
        print("%-12s %8.1f %s" % (r["date"], r.get("fx") or 0,
                                 "".join("%14s" % ("%.2f (%.0f)" % (r["coins"][c]["premium_pct"], r["coins"][c]["percentile"])
                                                   if c in r["coins"] and r["coins"][c].get("percentile") is not None else "-") for c in coins)))
    last = rows[-1]
    print()
    print("=== 최근 날 사후 검증 (200일): 과열(상위 10%)일 때 이후 업비트 수익률 ===")
    for c in coins:
        es = last["coins"].get(c, {}).get("event_study", {})
        hi, lo = es.get("high", {}), es.get("low", {})
        print("%-5s 과열 n=%-3s 3일후 %s%% (해외대비 %s)   냉각 n=%-3s 3일후 %s%% (해외대비 %s)"
              % (c, hi.get("n", 0), hi.get("upbit_fwd3_pct"), hi.get("rel_vs_okx_fwd3_pct"),
                 lo.get("n", 0), lo.get("upbit_fwd3_pct"), lo.get("rel_vs_okx_fwd3_pct")))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--bars", default="bars_cache.json")
    ap.add_argument("--out", default="kimchi.json")
    ap.add_argument("--history", default="kimchi_history.jsonl")
    a = ap.parse_args(argv)
    if a.report:
        return report(a.history)

    errors = {}
    fx, fx_src = fx_now()
    try:
        fxh = fx_history(DAYS)
    except Exception as e:
        fxh, errors["fx_history"] = {}, str(e)[:120]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fxh[today] = fx

    cache = None
    try:
        with open(a.bars, encoding="utf-8") as fp:
            cache = json.load(fp)
    except (IOError, ValueError):
        pass

    coins = {}
    for c in COINS:
        try:
            up, ok = upbit_daily(c), okx_daily(c)
        except Exception as e:
            errors[c] = str(e)[:120]
            continue
        series = premium_series(up, ok, fxh)
        if len(series) < 40:
            errors[c] = "일봉 교집합 %d일뿐" % len(series)
            continue
        hist = [p for _, p, _, _ in series[:-1]]
        cur = series[-1]
        d = {"date_utc": cur[0], "premium_pct": r4(cur[1]), "upbit_close": cur[2], "okx_close": cur[3],
             "percentile": round(percentile_rank(hist, cur[1]), 1),
             "hist_days": len(series), "hist_mean_pct": r4(mean(hist)), "hist_min_pct": r4(min(hist)), "hist_max_pct": r4(max(hist)),
             "event_study": event_study(series),
             "series_tail": [(dd, round(p, 3)) for dd, p, _, _ in series[-30:]]}
        if cache and "upbit" in cache["bars"] and "okx" in cache["bars"] \
                and c in cache["bars"]["upbit"] and c in cache["bars"]["okx"]:
            d["intraday"] = intraday(cache["bars"]["upbit"][c], cache["bars"]["okx"][c], fx, cache["eval_t0"])
        coins[c] = d
        time.sleep(0.15)

    now_kst = datetime.now(KST)
    hot = [c for c, d in coins.items() if d["percentile"] >= HI_PCT]
    cold = [c for c, d in coins.items() if d["percentile"] <= LO_PCT]
    verdict = ("프리미엄 %s / 과열(상위10%%) %s / 냉각(하위10%%) %s"
               % (", ".join("%s %+.2f%%(%.0f)" % (c, d["premium_pct"], d["percentile"]) for c, d in coins.items()),
                  ", ".join(hot) or "없음", ", ".join(cold) or "없음")) if coins else "데이터 없음"
    out = {"schema": "carrygate-kimchi/1", "date": now_kst.strftime("%Y-%m-%d"),
           "generated_at_kst": now_kst.strftime("%Y-%m-%d %H:%M:%S"),
           "fx_usdkrw": fx, "fx_source": fx_src,
           "method": {"premium": "업비트 종가 ÷ (OKX 현물 종가 × USDKRW) − 1. 일봉은 둘 다 00:00 UTC 마감이라 같은 시각",
                      "event_study": "과거 전체 대비 백분위 %d 이상=과열, %d 이하=냉각. 이후 %s일 업비트 수익률과 OKX 대비 상대 수익률" % (HI_PCT, LO_PCT, FWD),
                      "intraday": "그날 1분봉으로 프리미엄 평균·범위와, 평균에서 벗어난 뒤 %d분 뒤 되돌아오는 상관(음수면 되돌림)" % INTRADAY_HORIZON},
           "coins": coins, "errors": errors, "summary": {"hot": hot, "cold": cold, "verdict": verdict}}

    print("=== 김치프리미엄 (%s, 환율 %.1f %s) ===" % (out["date"], fx, fx_src))
    print("%-5s %9s %7s %9s %9s  %-34s %s" % ("코인", "프리미엄%", "백분위", "200일평균", "200일범위", "과열 뒤 3일 업비트/해외대비", "하루 되돌림 상관"))
    for c, d in coins.items():
        hi = d["event_study"].get("high", {})
        it = d.get("intraday", {})
        print("%-5s %9.2f %7.0f %9.2f %4.1f~%4.1f  n=%-3d %6s%% / %6s%%           %s"
              % (c, d["premium_pct"], d["percentile"], d["hist_mean_pct"], d["hist_min_pct"], d["hist_max_pct"],
                 hi.get("n", 0), hi.get("upbit_fwd3_pct"), hi.get("rel_vs_okx_fwd3_pct"),
                 ("%.2f" % it["reversion_corr_15m"]) if it.get("reversion_corr_15m") is not None else "-"))
    if errors:
        print("오류:", errors)
    print("판정:", verdict)
    if a.dry_run:
        print("\n(dry-run: 저장하지 않음)")
        return 0
    with open(a.out, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    append_history({"date": out["date"], "fx": fx,
                    "coins": {c: {"premium_pct": d["premium_pct"], "percentile": d["percentile"],
                                  "intraday_range_pp": d.get("intraday", {}).get("range_pp"),
                                  "reversion_corr_15m": d.get("intraday", {}).get("reversion_corr_15m")}
                              for c, d in coins.items()},
                    "summary": out["summary"]}, a.history)
    return 0 if coins else 1


if __name__ == "__main__":
    sys.exit(main())
