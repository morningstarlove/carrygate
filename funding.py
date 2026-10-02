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
# 문턱은 수수료를 뺀 순수익(net) 기준으로 비교한다.
ENTER_APR = 8.0
EXIT_APR = 2.0

# --- 수수료 가정 -------------------------------------------------------------
# 펀딩비만 보면 실제 손에 쥐는 돈을 과대평가한다. 캐리는 진입에 2번(현물 매수 +
# 선물 숏), 청산에 2번(현물 매도 + 선물 숏 청산) 체결되므로 왕복 4회 수수료가 든다.
# 이 수수료는 1회성이라, 오래 들고 있을수록 연환산 부담이 줄어든다.
#
# 하이퍼리퀴드는 매 실행마다 API(userFees)에서 실제 수수료표를 받아 쓴다.
# 나머지 거래소(okx/gate/binance/bybit)는 아래 보수적 기본 가정을 쓴다.
TAKER_FEE_SPOT_PCT = 0.10     # 기본 가정: 현물 1회 체결 수수료 (%)
TAKER_FEE_PERP_PCT = 0.05     # 기본 가정: 선물 1회 체결 수수료 (%)
HOLD_DAYS = 30.0              # 가정 보유기간 (일)

# 하이퍼리퀴드 API 가 실패했을 때만 쓰는 값 (공식 문서의 기본 등급 테이커 수수료).
HL_DOC_PERP_TAKER_PCT = 0.045
HL_DOC_SPOT_TAKER_PCT = 0.070
HL_ZERO_ADDR = "0x" + "0" * 40   # 거래 이력 없는 주소 = 기본 등급(Tier 0) 수수료
HL_STABLES = ("USDC", "USDH", "USDT0", "USDE")

# --- 한국 중계 ---------------------------------------------------------------
# 바이낸스(451)·바이비트(403)는 미국 서버에서 막힌다. 한국 PC 에서 kr_relay.py 가 하루 한 번
# data/kr_funding.json 을 올리면, 두 거래소가 직접 수집에 실패했을 때 그 파일(36시간 안의 것)을 대신 쓴다.
RELAY_PATH = "data/kr_funding.json"
RELAY_MAX_AGE_H = 36.0
RELAY_VENUES = ("binance", "bybit")
# 두 거래소의 공식 수수료표(일반 등급 테이커). API 키 없이 실측할 수 없어 문서 값을 쓴다.
VENUE_DOC_FEES = {
    "binance": (0.10, 0.05,  "바이낸스 공식 수수료표 일반 등급 (현물 0.10%, 선물 0.05%)"),
    "bybit":   (0.10, 0.055, "바이비트 공식 수수료표 일반 등급 (현물 0.10%, 선물 0.055%)"),
}


def fee_drag(spot_pct, perp_pct):
    """1회 체결 수수료(%) -> 왕복 4회 수수료를 보유기간으로 나눈 연 % 부담."""
    return 2.0 * (spot_pct + perp_pct) * 365.0 / HOLD_DAYS


ROUND_TRIP_FEE_PCT = 2.0 * (TAKER_FEE_SPOT_PCT + TAKER_FEE_PERP_PCT)
FEE_DRAG_APR_PCT = fee_drag(TAKER_FEE_SPOT_PCT, TAKER_FEE_PERP_PCT)


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

def hl_info():
    """하이퍼리퀴드 실제 수수료(기본 등급)와 현물 상장 여부를 API 에서 읽는다.

    캐리는 현물과 선물을 같이 들어야 한다. 하이퍼리퀴드에 그 코인의 현물이 없으면
    현물은 다른 거래소에서 사야 하므로, 현물 수수료는 기본 가정으로 계산한다."""
    out = {"perp_taker_pct": HL_DOC_PERP_TAKER_PCT,
           "spot_taker_pct": HL_DOC_SPOT_TAKER_PCT,
           "fee_source": "공식 문서 기준값 (API 조회 실패)",
           "spot_pairs": {}, "spot_checked": False}
    url = "https://api.hyperliquid.xyz/info"
    try:
        d = http_json(url, data={"type": "userFees", "user": HL_ZERO_ADDR})
        fs = d.get("feeSchedule") or {}
        perp = f(fs.get("cross"))
        spot = f(fs.get("spotCross"))
        if perp is None:
            perp = f(d.get("userCrossRate"))
        if spot is None:
            spot = f(d.get("userSpotCrossRate"))
        if perp is not None and spot is not None:
            out["perp_taker_pct"] = perp * 100.0
            out["spot_taker_pct"] = spot * 100.0
            out["fee_source"] = "하이퍼리퀴드 API 실측 (userFees, 기본 등급 테이커)"
    except Exception as e:
        out["fee_error"] = str(e)[:150]

    try:
        m = http_json(url, data={"type": "spotMeta"})
        names = {t.get("index"): t.get("name") for t in m.get("tokens", [])}
        pairs = {}
        for u in m.get("universe", []):
            toks = u.get("tokens") or []
            if len(toks) != 2:
                continue
            base, quote = names.get(toks[0]), names.get(toks[1])
            if quote not in HL_STABLES:
                continue
            for c in COINS:
                # 하이퍼리퀴드 현물은 BTC 를 UBTC 처럼 U 를 붙여 상장한 경우가 있다
                if base in (c, "U" + c) and c not in pairs:
                    pairs[c] = "%s/%s" % (base, quote)
        out["spot_pairs"] = pairs
        out["spot_checked"] = True
    except Exception as e:
        out["spot_error"] = str(e)[:150]
    return out


def read_relay(path=RELAY_PATH, now=None):
    """한국 중계 파일을 읽는다. 36시간보다 오래된 것은 쓰지 않는다(PC 가 꺼져 있었던 날)."""
    info = {"present": False, "fresh": False, "age_hours": None, "generated_at_kst": None,
            "max_age_hours": RELAY_MAX_AGE_H, "venues": {}}
    try:
        with open(path, encoding="utf-8") as fp:
            d = json.load(fp)
    except Exception:
        return info
    info["present"] = True
    info["generated_at_kst"] = d.get("generated_at_kst")
    try:
        gen = datetime.strptime(d["generated_at_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        return info
    now = now or datetime.now(timezone.utc)
    age = (now - gen).total_seconds() / 3600.0
    info["age_hours"] = round(age, 1)
    info["fresh"] = age <= RELAY_MAX_AGE_H
    if info["fresh"]:
        info["venues"] = {v: rows for v, rows in (d.get("venues") or {}).items() if v in RELAY_VENUES and rows}
    return info


def venue_fees(venue, coin, hl):
    """(현물 1회 %, 선물 1회 %, 설명) — 거래소·코인별 실제 적용 수수료."""
    if venue in VENUE_DOC_FEES:
        return VENUE_DOC_FEES[venue]
    if venue == "hyperliquid" and hl:
        pair = hl["spot_pairs"].get(coin)
        if pair:
            return hl["spot_taker_pct"], hl["perp_taker_pct"], "하이퍼리퀴드 현물(%s)+선물" % pair
        why = "하이퍼리퀴드 현물 없음" if hl["spot_checked"] else "하이퍼리퀴드 현물 확인 불가"
        return TAKER_FEE_SPOT_PCT, hl["perp_taker_pct"], "%s — 현물은 타 거래소 기본 가정" % why
    return TAKER_FEE_SPOT_PCT, TAKER_FEE_PERP_PCT, "기본 가정"


def decorate(e, fees=None):
    """APR 을 계산하고 수수료를 뺀 순수익(decision_apr_pct)까지 붙인다.

    gross_apr_pct  : 펀딩비만 본 연환산 (수수료 전)
    decision_apr_pct: 수수료를 뺀 연환산. 모든 판정은 이 값으로 한다.
    """
    h = e["interval_hours"]
    e["apr_now_pct"] = round(apr(e["funding_rate_now"], h), 4) if e["funding_rate_now"] is not None else None
    e["apr_avg7d_pct"] = round(apr(e["funding_rate_avg7d"], h), 4) if e["funding_rate_avg7d"] is not None else None
    # 7일 평균이 있으면 그걸 쓴다. 한 번의 값은 튀기 때문이다.
    if e["apr_avg7d_pct"] is not None:
        e["gross_apr_pct"], e["basis"] = e["apr_avg7d_pct"], "7일평균"
    else:
        e["gross_apr_pct"], e["basis"] = e["apr_now_pct"], "단발값"

    spot_pct, perp_pct, note = fees or (TAKER_FEE_SPOT_PCT, TAKER_FEE_PERP_PCT, "기본 가정")
    drag = fee_drag(spot_pct, perp_pct)
    e["fee_spot_taker_pct"] = round(spot_pct, 4)
    e["fee_perp_taker_pct"] = round(perp_pct, 4)
    e["fee_note"] = note
    e["fee_drag_apr_pct"] = round(drag, 4)
    e["decision_apr_pct"] = (round(e["gross_apr_pct"] - drag, 4)
                             if e["gross_apr_pct"] is not None else None)
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
            # apr 은 예전부터 "수수료 전" 값이었다. 뜻이 바뀌면 과거 줄과 섞이므로
            # 그대로 두고, 수수료 뺀 값을 apr_net 으로 따로 남긴다.
            "apr": d["best"]["gross_apr_pct"],
            "apr_net": d["best"]["decision_apr_pct"],
            "venue": d["best"]["venue"],
            "basis": d["best"]["basis"],
            "median": d["median_gross_apr_pct"],
            "median_net": d["median_apr_pct"],
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
    hl = hl_info()

    for name, fn in SOURCES:
        try:
            res = fn()
            if not res:
                raise RuntimeError("no rows")
            for c, e in res.items():
                per_coin[c].append(decorate(e, venue_fees(e["venue"], c, hl)))
            ok.append(name)
        except Exception as e:
            failed[name] = str(e)[:200]

    # 바이낸스·바이비트가 막혔으면 한국 중계 파일로 대신한다
    relay = read_relay()
    relay["used"] = []
    for name in RELAY_VENUES:
        if name in failed and name in relay["venues"]:
            for c, e in relay["venues"][name].items():
                if c not in per_coin:
                    continue
                e = dict(e)
                e["via"] = "한국 중계"
                e["relay_age_hours"] = relay["age_hours"]
                per_coin[c].append(decorate(e, venue_fees(name, c, hl)))
            ok.append(name)
            relay["used"].append(name)
            failed[name] = failed.pop(name)[:80] + " → 한국 중계 파일 사용"
    relay.pop("venues", None)

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
            "median_gross_apr_pct": round(median([v["gross_apr_pct"] for v in vs]), 4),
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
        verdict = "%s %s 순 연 %.1f%% — 문턱은 넘었으나 7일 평균 없음(단발값). 진입 보류" % (
            best_coin, best["venue"], best["decision_apr_pct"])
    elif best["decision_apr_pct"] >= ENTER_APR:
        state = "진입 가능"
        verdict = "%s %s 순 연 %.1f%% (펀딩 %.1f%% − 수수료 %.1f%%, 7일평균) — 진입 문턱(연 %.0f%%) 충족" % (
            best_coin, best["venue"], best["decision_apr_pct"],
            best["gross_apr_pct"], best["fee_drag_apr_pct"], ENTER_APR)
    elif best["decision_apr_pct"] < EXIT_APR:
        state = "청산"
        verdict = "최고 %s %s 순 연 %.1f%% (펀딩 %.1f%% − 수수료 %.1f%%) — 청산 문턱(연 %.0f%%) 미만" % (
            best_coin, best["venue"], best["decision_apr_pct"],
            best["gross_apr_pct"], best["fee_drag_apr_pct"], EXIT_APR)
    else:
        state = "대기"
        verdict = "최고 %s %s 순 연 %.1f%% (펀딩 %.1f%% − 수수료 %.1f%%) — 진입 문턱(연 %.0f%%) 미달, 보유 중이면 유지" % (
            best_coin, best["venue"], best["decision_apr_pct"],
            best["gross_apr_pct"], best["fee_drag_apr_pct"], ENTER_APR)

    out = {
        "schema": "carrygate-funding/1",
        "date": now.strftime("%Y-%m-%d"),
        "generated_at_kst": now.strftime("%Y-%m-%d %H:%M:%S"),
        "strategy": "현물 매수 + 무기한선물 동일수량 숏 (델타 중립) / 펀딩비 수취",
        "thresholds": {"enter_apr_pct": ENTER_APR, "exit_apr_pct": EXIT_APR,
                       "compared_against": "수수료 차감 후 순수익(net)"},
        "fee_assumption": {
            "note": "하이퍼리퀴드는 API 실측 수수료, 바이낸스·바이비트는 공식 수수료표, 나머지 거래소는 보수적 기본 가정.",
            "assumed_hold_days": HOLD_DAYS,
            "hyperliquid": {
                "source": hl["fee_source"],
                "perp_taker_pct": round(hl["perp_taker_pct"], 4),
                "spot_taker_pct": round(hl["spot_taker_pct"], 4),
                "spot_pairs": hl["spot_pairs"],
                "spot_checked": hl["spot_checked"],
                "fee_drag_apr_pct_with_hl_spot": round(
                    fee_drag(hl["spot_taker_pct"], hl["perp_taker_pct"]), 4),
            },
            "documented": {v: {"spot_taker_pct": sp, "perp_taker_pct": pp, "source": note}
                           for v, (sp, pp, note) in VENUE_DOC_FEES.items()},
            "default": {
                "taker_fee_spot_pct": TAKER_FEE_SPOT_PCT,
                "taker_fee_perp_pct": TAKER_FEE_PERP_PCT,
                "round_trip_fee_pct": round(ROUND_TRIP_FEE_PCT, 4),
                "fee_drag_apr_pct": round(FEE_DRAG_APR_PCT, 4),
            },
        },
        "sources_ok": ok,
        "sources_failed": {k: v for k, v in failed.items() if k not in relay["used"]},
        "sources_relayed": {k: failed[k] for k in relay["used"]},
        "kr_relay": relay,
        "gate_ref": gate,
        "coins": coins,
        "summary": {
            "state": state,
            "best_coin": best_coin,
            "best_venue": best["venue"] if best else None,
            "best_apr_pct": best["decision_apr_pct"] if best else None,
            "best_gross_apr_pct": best["gross_apr_pct"] if best else None,
            "fee_drag_apr_pct": best["fee_drag_apr_pct"] if best else None,
            "best_fee_note": best["fee_note"] if best else None,
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
