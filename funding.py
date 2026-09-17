# -*- coding: utf-8 -*-
"""
CARRYGATE — 캐리(펀딩비) 브리지

무기한선물 펀딩비를 여러 거래소 공개 API에서 읽어
연환산 수익률(APR)로 바꾸고 funding.json 으로 저장한다.

캐리 = 현물 매수 + 무기한선물 동일 수량 숏.
       가격이 오르든 내리든 손익이 상쇄되고, 펀딩비만 수취한다.

주문 기능 없음. 읽기 전용 공개 API만 사용한다. API 키 불필요.
"""
import json, time, sys, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

COINS = ["BTC", "ETH", "XRP", "TRX", "LINK", "DOGE"]
KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (compatible; carrygate/1.0)",
      "Accept": "application/json"}

# 진입/청산 문턱 (연 %). 히스테리시스 — 문턱을 다르게 둬서 잦은 진출입을 막는다.
ENTER_APR = 8.0
EXIT_APR = 2.0


def http_json(url, tries=3, data=None):
    last = None
    for i in range(tries):
        try:
            body = json.dumps(data).encode("utf-8") if data is not None else None
            hdr = dict(UA)
            if body is not None:
                hdr["Content-Type"] = "application/json"
            req = urllib.request.Request(url, headers=hdr, data=body)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(1.5 + i * 2)
    raise RuntimeError("fetch failed %s : %s" % (url, last))


def f(x):
    """문자열/None 안전 float 변환"""
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def apr(rate, interval_hours):
    """1회 펀딩비율 -> 연환산 % (단리)"""
    if rate is None or not interval_hours:
        return None
    return rate * (24.0 / interval_hours) * 365.0 * 100.0


AVG_DAYS = 7   # 판정에 쓰는 평균 구간


def avg_recent(pairs, days=AVG_DAYS):
    """[(정산시각_ms, 비율)] -> 최근 N일 평균 비율. 값이 없으면 None.

    거래소마다 정산 주기가 달라도 시각으로 잘라내면 동일 기간이 된다."""
    if not pairs:
        return None
    cutoff = (time.time() - days * 86400) * 1000.0
    vals = [r for t, r in pairs if t is not None and r is not None and t >= cutoff]
    return sum(vals) / len(vals) if vals else None


# ----------------------------------------------------------------- 거래소별

def src_binance():
    prem = http_json("https://fapi.binance.com/fapi/v1/premiumIndex")
    by_sym = {r["symbol"]: r for r in prem}

    iv = {}
    try:
        for r in http_json("https://fapi.binance.com/fapi/v1/fundingInfo"):
            h = f(r.get("fundingIntervalHours"))
            if h:
                iv[r["symbol"]] = h
    except Exception:
        pass

    out = {}
    for c in COINS:
        sym = c + "USDT"
        r = by_sym.get(sym)
        if not r:
            continue
        hours = iv.get(sym, 8.0)
        mark, index = f(r.get("markPrice")), f(r.get("indexPrice"))

        avg7 = None
        try:
            n = int(round(7 * 24 / hours))
            hist = http_json("https://fapi.binance.com/fapi/v1/fundingRate"
                             "?symbol=%s&limit=%d" % (sym, n))
            vals = [f(h.get("fundingRate")) for h in hist]
            vals = [v for v in vals if v is not None]
            if vals:
                avg7 = sum(vals) / len(vals)
        except Exception:
            pass
        time.sleep(0.15)

        out[c] = {
            "venue": "binance",
            "interval_hours": hours,
            "funding_rate_now": f(r.get("lastFundingRate")),
            "funding_rate_avg7d": avg7,
            "mark_price": mark,
            "index_price": index,
            "premium_pct": round((mark / index - 1.0) * 100, 4) if (mark and index) else None,
        }
    return out


def src_bybit():
    t = http_json("https://api.bybit.com/v5/market/tickers?category=linear")
    rows = t.get("result", {}).get("list", [])
    by_sym = {r["symbol"]: r for r in rows}

    iv = {}
    try:
        info = http_json("https://api.bybit.com/v5/market/instruments-info"
                         "?category=linear&limit=1000")
        for r in info.get("result", {}).get("list", []):
            m = f(r.get("fundingInterval"))
            if m:
                iv[r["symbol"]] = m / 60.0
    except Exception:
        pass

    out = {}
    for c in COINS:
        sym = c + "USDT"
        r = by_sym.get(sym)
        if not r:
            continue
        mark, index = f(r.get("markPrice")), f(r.get("indexPrice"))
        out[c] = {
            "venue": "bybit",
            "interval_hours": iv.get(sym, 8.0),
            "funding_rate_now": f(r.get("fundingRate")),
            "funding_rate_avg7d": None,
            "mark_price": mark,
            "index_price": index,
            "premium_pct": round((mark / index - 1.0) * 100, 4) if (mark and index) else None,
        }
    return out


def src_okx():
    out = {}
    for c in COINS:
        inst = "%s-USDT-SWAP" % c
        try:
            d = http_json("https://www.okx.com/api/v5/public/funding-rate?instId=%s" % inst)
            r = (d.get("data") or [None])[0]
            if not r:
                continue
            t0, t1 = f(r.get("fundingTime")), f(r.get("nextFundingTime"))
            hours = round((t1 - t0) / 3600000.0) if (t0 and t1 and t1 > t0) else 8.0

            avg = None
            try:
                h = http_json("https://www.okx.com/api/v5/public/funding-rate-history"
                              "?instId=%s&limit=100" % inst)
                avg = avg_recent([(f(x.get("fundingTime")), f(x.get("realizedRate") or x.get("fundingRate")))
                                  for x in (h.get("data") or [])])
            except Exception:
                pass
            time.sleep(0.15)

            out[c] = {
                "venue": "okx",
                "interval_hours": hours or 8.0,
                "funding_rate_now": f(r.get("fundingRate")),
                "funding_rate_avg7d": avg,
                "mark_price": None,
                "index_price": None,
                "premium_pct": None,
            }
        except Exception:
            pass
        time.sleep(0.15)
    return out


def src_gate():
    rows = http_json("https://api.gateio.ws/api/v4/futures/usdt/contracts")
    by_name = {r["name"]: r for r in rows}
    out = {}
    for c in COINS:
        r = by_name.get("%s_USDT" % c)
        if not r:
            continue
        sec = f(r.get("funding_interval")) or 28800.0
        mark, index = f(r.get("mark_price")), f(r.get("index_price"))

        avg = None
        try:
            h = http_json("https://api.gateio.ws/api/v4/futures/usdt/funding_rate"
                          "?contract=%s_USDT&limit=100" % c)
            # t 는 초 단위라 ms 로 맞춘다
            avg = avg_recent([(f(x.get("t")) * 1000.0 if f(x.get("t")) else None, f(x.get("r")))
                              for x in h])
        except Exception:
            pass
        time.sleep(0.15)

        out[c] = {
            "venue": "gate",
            "interval_hours": sec / 3600.0,
            "funding_rate_now": f(r.get("funding_rate")),
            "funding_rate_avg7d": avg,
            "mark_price": mark,
            "index_price": index,
            "premium_pct": round((mark / index - 1.0) * 100, 4) if (mark and index) else None,
        }
    return out


def src_hyperliquid():
    d = http_json("https://api.hyperliquid.xyz/info", data={"type": "metaAndAssetCtxs"})
    meta, ctxs = d[0], d[1]
    names = [u["name"] for u in meta["universe"]]
    out = {}
    for c in COINS:
        if c not in names:
            continue
        i = names.index(c)
        ctx = ctxs[i]
        mark, oracle = f(ctx.get("markPx")), f(ctx.get("oraclePx"))

        avg = None
        try:
            start = int((time.time() - AVG_DAYS * 86400) * 1000)
            h = http_json("https://api.hyperliquid.xyz/info",
                          data={"type": "fundingHistory", "coin": c, "startTime": start})
            avg = avg_recent([(f(x.get("time")), f(x.get("fundingRate"))) for x in h])
        except Exception:
            pass
        time.sleep(0.15)

        out[c] = {
            "venue": "hyperliquid",
            "interval_hours": 1.0,          # 하이퍼리퀴드는 1시간마다 정산
            "funding_rate_now": f(ctx.get("funding")),
            "funding_rate_avg7d": avg,
            "mark_price": mark,
            "index_price": oracle,
            "premium_pct": round((mark / oracle - 1.0) * 100, 4) if (mark and oracle) else None,
        }
    return out


SOURCES = [
    ("binance", src_binance),
    ("bybit", src_bybit),
    ("okx", src_okx),
    ("gate", src_gate),
    ("hyperliquid", src_hyperliquid),
]


# ----------------------------------------------------------------- 집계

def decorate(e):
    """APR 계산 후 판단용 대표값(decision_apr_pct)을 붙인다."""
    h = e["interval_hours"]
    e["apr_now_pct"] = round(apr(e["funding_rate_now"], h), 4) if e["funding_rate_now"] is not None else None
    e["apr_avg7d_pct"] = round(apr(e["funding_rate_avg7d"], h), 4) if e["funding_rate_avg7d"] is not None else None
    # 7일 평균이 있으면 그걸 쓴다. 한 번의 값은 튀기 때문이다.
    if e["apr_avg7d_pct"] is not None:
        e["decision_apr_pct"], e["basis"] = e["apr_avg7d_pct"], "7일평균"
    else:
        e["decision_apr_pct"], e["basis"] = e["apr_now_pct"], "단발값"
    return e


def rank(e):
    """대표값 고르는 기준: 7일 평균이 있는 쪽 우선, 그다음 연환산이 높은 쪽."""
    return (1 if e.get("basis") == "7일평균" else 0, e["decision_apr_pct"])


def median(vals):
    v = sorted(vals)
    n = len(v)
    if not n:
        return None
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


def read_gate():
    """status.json 에서 게이트/급락 상태를 읽어온다. 없으면 미상."""
    try:
        with open("status.json", encoding="utf-8") as fp:
            s = json.load(fp)
        btc = s.get("coins", {}).get("KRW-BTC", {})
        return {
            "date": s.get("date"),
            "btc_gate": btc.get("gate", "미상"),
            "btc_crash_flag": bool(btc.get("crash_flag")),
        }
    except Exception:
        return {"date": None, "btc_gate": "미상", "btc_crash_flag": False}


def write_history(out):
    """하루 한 줄. 같은 날짜가 이미 있으면 덮어쓴다 (재실행해도 중복되지 않게).

    코인별 수치까지 남긴다. 며칠 지켜볼 때 이자가 안정적인지 보려면
    요약만으로는 부족하고 코인별 추이가 있어야 한다."""
    path = "funding_history.jsonl"
    coins = {}
    for c, d in out["coins"].items():
        coins[c] = {
            "apr": d["best"]["decision_apr_pct"],
            "venue": d["best"]["venue"],
            "basis": d["best"]["basis"],
            "median": d["median_apr_pct"],
        }
    rec = {"date": out["date"], "summary": out["summary"], "coins": coins,
           "gate": out["gate_ref"].get("btc_gate")}
    line = json.dumps(rec, ensure_ascii=False)
    try:
        with open(path, encoding="utf-8") as fp:
            rows = [l for l in fp.read().splitlines() if l.strip()]
    except IOError:
        rows = []
    rows = [l for l in rows if json.loads(l).get("date") != out["date"]]
    rows.append(line)
    with open(path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(rows) + "\n")


def main():
    now = datetime.now(KST)
    per_coin = {c: [] for c in COINS}
    ok, failed = [], {}

    for name, fn in SOURCES:
        try:
            res = fn()
            if not res:
                raise RuntimeError("no rows")
            for c, e in res.items():
                per_coin[c].append(decorate(e))
            ok.append(name)
        except Exception as e:
            failed[name] = str(e)[:200]

    coins = {}
    for c in COINS:
        vs = [v for v in per_coin[c] if v.get("decision_apr_pct") is not None]
        if not vs:
            continue
        # 7일 평균이 있는 쪽을 먼저. 단발값은 크게 튀어서 대표값이 되면 안 된다.
        vs.sort(key=rank, reverse=True)
        coins[c] = {
            "venues": vs,
            "best": vs[0],
            "median_apr_pct": round(median([v["decision_apr_pct"] for v in vs]), 4),
            "venue_count": len(vs),
        }

    gate = read_gate()

    best_coin = best = None
    for c, d in coins.items():
        if best is None or rank(d["best"]) > rank(best):
            best_coin, best = c, d["best"]

    if not coins:
        verdict, state = "데이터 없음 — 판정 불가", "미상"
    elif gate["btc_crash_flag"]:
        verdict, state = "급락 감지 — 캐리 중단 (거래소·시장 스트레스 구간)", "중단"
    elif best["decision_apr_pct"] >= ENTER_APR and best["basis"] != "7일평균":
        # 단발값은 크게 튄다. 평균 없이 진입 판정을 내리지 않는다.
        state = "대기"
        verdict = "%s %s 연 %.1f%% — 문턱은 넘었으나 7일 평균 없음(단발값). 진입 보류" % (
            best_coin, best["venue"], best["decision_apr_pct"])
    elif best["decision_apr_pct"] >= ENTER_APR:
        state = "진입 가능"
        verdict = "%s %s 연 %.1f%% (7일평균) — 진입 문턱(연 %.0f%%) 충족" % (
            best_coin, best["venue"], best["decision_apr_pct"], ENTER_APR)
    elif best["decision_apr_pct"] < EXIT_APR:
        state = "청산"
        verdict = "최고 %s %s 연 %.1f%% — 청산 문턱(연 %.0f%%) 미만" % (
            best_coin, best["venue"], best["decision_apr_pct"], EXIT_APR)
    else:
        state = "대기"
        verdict = "최고 %s %s 연 %.1f%% — 진입 문턱(연 %.0f%%) 미달, 보유 중이면 유지" % (
            best_coin, best["venue"], best["decision_apr_pct"], ENTER_APR)

    out = {
        "schema": "carrygate-funding/1",
        "date": now.strftime("%Y-%m-%d"),
        "generated_at_kst": now.strftime("%Y-%m-%d %H:%M:%S"),
        "strategy": "현물 매수 + 무기한선물 동일수량 숏 (델타 중립) / 펀딩비 수취",
        "thresholds": {"enter_apr_pct": ENTER_APR, "exit_apr_pct": EXIT_APR},
        "sources_ok": ok,
        "sources_failed": failed,
        "gate_ref": gate,
        "coins": coins,
        "summary": {
            "state": state,
            "best_coin": best_coin,
            "best_venue": best["venue"] if best else None,
            "best_apr_pct": best["decision_apr_pct"] if best else None,
            "best_basis": best["basis"] if best else None,
            "avg_coverage": "%d/%d" % (
                sum(1 for d in coins.values() if d["best"]["basis"] == "7일평균"), len(coins)),
            "coins_covered": len(coins),
            "venues_ok": len(ok),
            "verdict": verdict,
            "ok": bool(ok),
        },
    }

    with open("funding.json", "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=2)

    write_history(out)

    print(json.dumps(out["summary"], ensure_ascii=False, indent=2))
    if failed:
        print("SOURCE ERRORS:", json.dumps(failed, ensure_ascii=False), file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
