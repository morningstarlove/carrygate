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
from common import KST, UA, f, mean, std, corr, append_history

COINS = ["BTC", "ETH", "XRP", "TRX", "LINK", "DOGE"]
DEPTH_LEVELS = 5          # 불균형 계산에 쓰는 호가 단수 (최우선 5호가)
IMB_THRESHOLD = 0.3       # "치우쳤다"고 보는 불균형 기준 ((매수−매도)/(매수+매도))
HORIZONS = (60, 300)      # 초. 불균형 뒤 1분·5분 가격 변화를 본다
MIN_DAYS = 5
WIDE_SPREAD_BP = 20.0     # P31(2026-11-08 고정): 평균 호가폭이 이 값(=업비트 왕복 지정가 수수료 10bp 의 2배) 이상인 시장만 따로 합산. 결과 보고 바꾸지 않는다
MM_TTL_SEC = 300          # 호가 제공 종이 규칙: 재고가 생긴 뒤 지정가 청산을 기다리는 최대 초. 넘으면 상대 호가에 시장가
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


def fetch_upbit_trades(last_ms, interval):
    """업비트 최근 체결(공개, 키 불필요) -> 코인별 직전 표본 이후 체결 중
    매도 주도(ask_bid=ASK: 누군가 매수호가를 때림) 최저가 sell_lo, 매수 주도(BID) 최고가 buy_hi.
    호가 제공 '닿으면 체결' 종이 규칙(P29)에 쓴다. last_ms 는 코인별 마지막으로 본 체결 시각(ms)."""
    out = {}
    now_ms = int(time.time() * 1000)
    for c in COINS:
        rows = http_json("https://api.upbit.com/v1/trades/ticks?market=KRW-%s&count=200" % c)
        since = last_ms.get(c, now_ms - interval * 1000)
        lo, hi, n, mx = None, None, 0, since
        for r in rows or []:
            t = int(r.get("timestamp") or 0)
            if t <= since:
                continue
            px = f(r.get("trade_price"))
            if px is None:
                continue
            n += 1
            mx = max(mx, t)
            if r.get("ask_bid") == "ASK":
                lo = px if lo is None else min(lo, px)
            elif r.get("ask_bid") == "BID":
                hi = px if hi is None else max(hi, px)
        last_ms[c] = mx
        out[c] = {"sell_lo": lo, "buy_hi": hi, "n_trades": n}
        time.sleep(0.12)   # 업비트 공개 API 초당 10회 한도 안
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
CSV_FIELDS = ["ts", "venue", "coin", "bid", "ask", "bid_notional", "ask_notional", "sell_lo", "buy_hi"]


def collect(minutes, interval, path):
    """interval 초마다 세 거래소 호가를 찍어 CSV 에 바로 쓴다. 실패는 세고 계속한다."""
    end = time.time() + minutes * 60
    n, errors = 0, {}
    last_ms = {}
    with open(path, "w", newline="", encoding="utf-8") as fp:
        w = csv.DictWriter(fp, fieldnames=CSV_FIELDS)
        w.writeheader()
        while time.time() < end:
            t0 = time.time()
            ts = int(t0)
            for venue, fn in SOURCES:
                try:
                    snaps = fn()
                    trades = {}
                    if venue == "upbit":
                        try:
                            trades = fetch_upbit_trades(last_ms, interval)
                        except Exception as e:
                            errors["upbit_trades"] = errors.get("upbit_trades", 0) + 1
                            errors["upbit_trades_last"] = str(e)[:100]
                    for coin, s in snaps.items():
                        row = {"ts": ts, "venue": venue, "coin": coin}
                        row.update(s)
                        tr = trades.get(coin) or {}
                        row["sell_lo"] = "" if tr.get("sell_lo") is None else tr["sell_lo"]
                        row["buy_hi"] = "" if tr.get("buy_hi") is None else tr["buy_hi"]
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
            fx = lambda v: float(v) if v not in (None, "") else None
            by.setdefault(key, []).append({"ts": int(r["ts"]), "bid": float(r["bid"]), "ask": float(r["ask"]),
                                          "bq": float(r["bid_notional"]), "aq": float(r["ask_notional"]),
                                          "sell_lo": fx(r.get("sell_lo")), "buy_hi": fx(r.get("buy_hi"))})
    for k in by:
        by[k].sort(key=lambda x: x["ts"])
    return by


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


def mm_paper(rows, fee):
    """호가 제공(양방향 지정가) 종이 규칙 — 2026-11-03 추가(제안함 P26). 주문 없음, 표본으로만 계산.

    재고가 없으면 매 표본의 최우선 매수·매도호가에 1단위씩 걸어 둔다. 다음 표본에서 상대 호가가 내 가격을
    **지나쳐야**(매도1 < 내 매수가, 매수1 > 내 매도가) 체결로 본다 — scalp.limit_filled 와 같은 보수적 가정.
    재고가 생기면 반대쪽만 현재 최우선호가에 다시 걸고, 지나쳐야 체결. MM_TTL_SEC 안에 안 되면 상대 호가에 시장가.
    비용: 지정가 체결 = 메이커 수수료, 시장가 = 테이커 수수료(호가 폭은 가격에 이미 들어 있다). 끝에 남은 재고는 시장가."""
    n = len(rows)
    if n < 30:
        return {"n": n}
    mk, tk = fee.get("maker_pct", 0.0) * 100.0, fee.get("taker_pct", 0.0) * 100.0
    inv, entry_px, entry_ts, close_px = 0, None, None, None
    qb, qa = rows[0]["bid"], rows[0]["ask"]
    nets, grosses, fills, maker_exits = [], [], 0, 0

    def settle(exit_px, maker):
        g = inv * (exit_px / entry_px - 1.0) * 1e4
        nets.append(g - mk - (mk if maker else tk))
        grosses.append(g)

    for r in rows[1:]:
        if inv == 0:
            if r["ask"] < qb:
                inv, entry_px, entry_ts, fills = 1, qb, r["ts"], fills + 1
                close_px = r["ask"]
            elif r["bid"] > qa:
                inv, entry_px, entry_ts, fills = -1, qa, r["ts"], fills + 1
                close_px = r["bid"]
            else:
                qb, qa = r["bid"], r["ask"]
            continue
        hit = (r["bid"] > close_px) if inv == 1 else (r["ask"] < close_px)
        if hit:
            settle(close_px, True)
            maker_exits += 1
        elif r["ts"] - entry_ts >= MM_TTL_SEC:
            settle(r["bid"] if inv == 1 else r["ask"], False)
        else:
            close_px = r["ask"] if inv == 1 else r["bid"]
            continue
        inv, qb, qa = 0, r["bid"], r["ask"]
    if inv != 0:
        r = rows[-1]
        settle(r["bid"] if inv == 1 else r["ask"], False)
    t = len(nets)
    return {"n": n, "fills": fills, "trades": t, "maker_exits": maker_exits,
            "maker_exit_pct": round(100.0 * maker_exits / t, 1) if t else None,
            "gross_bp_avg": round(mean(grosses), 3) if t else None,
            "net_bp_avg": round(mean(nets), 3) if t else None,
            "net_bp_sum": round(sum(nets), 3),
            "hit_rate_pct": round(100.0 * sum(1 for x in nets if x > 0) / t, 1) if t else None}


def mm_touch(rows, fee):
    """호가 제공 '닿으면 체결' 종이 규칙 — 2026-11-05 추가(제안함 P29). mm_paper(P26)와 나란히, 주문 없음.

    P26 의 '지나쳐야 체결'은 체결 = 이미 불리하게 움직인 뒤라 역선택이 100% 다(첫 이틀 업비트 건당 −19.7bp, 적중 2%).
    여기서는 실제 체결 기록으로 본다: 직전 표본에 건 내 매수호가 이하에서 **매도 주도 체결**(sell_lo ≤ 내 매수가)이 찍히면 매수 체결,
    내 매도호가 이상에서 **매수 주도 체결**(buy_hi ≥ 내 매도가)이 찍히면 매도 체결. 줄 서기(앞 대기 물량)는 모른다 — 낙관 쪽 경계.
    한 구간에 양쪽이 다 찍히면 왕복 1건(반폭×2 를 번 경우). 재고 ±1, 반대쪽 최우선호가 재게시, MM_TTL_SEC 뒤 상대 호가 시장가.
    체결 기록(sell_lo·buy_hi)이 없는 시장(업비트 외)은 계산하지 않는다."""
    n = len(rows)
    if n < 30 or not any(r.get("sell_lo") is not None or r.get("buy_hi") is not None for r in rows):
        return {"n": n, "no_trades": True}
    mk, tk = fee.get("maker_pct", 0.0) * 100.0, fee.get("taker_pct", 0.0) * 100.0
    inv, entry_px, entry_ts, close_px = 0, None, None, None
    qb, qa = rows[0]["bid"], rows[0]["ask"]
    nets, grosses, fills, maker_exits, both = [], [], 0, 0, 0

    def buy_hit(r, px):
        return (r.get("sell_lo") is not None and r["sell_lo"] <= px) or r["ask"] < px

    def sell_hit(r, px):
        return (r.get("buy_hi") is not None and r["buy_hi"] >= px) or r["bid"] > px

    def book(entry, exit_px, side, maker_exit):
        g = side * (exit_px / entry - 1.0) * 1e4
        nets.append(g - mk - (mk if maker_exit else tk))
        grosses.append(g)

    for r in rows[1:]:
        if inv == 0:
            b, a = buy_hit(r, qb), sell_hit(r, qa)
            if b and a:
                fills += 2
                both += 1
                maker_exits += 1
                book(qb, qa, 1, True)
                qb, qa = r["bid"], r["ask"]
            elif b:
                inv, entry_px, entry_ts, close_px, fills = 1, qb, r["ts"], r["ask"], fills + 1
            elif a:
                inv, entry_px, entry_ts, close_px, fills = -1, qa, r["ts"], r["bid"], fills + 1
            else:
                qb, qa = r["bid"], r["ask"]
            continue
        hit = sell_hit(r, close_px) if inv == 1 else buy_hit(r, close_px)
        if hit:
            book(entry_px, close_px, inv, True)
            maker_exits += 1
        elif r["ts"] - entry_ts >= MM_TTL_SEC:
            book(entry_px, r["bid"] if inv == 1 else r["ask"], inv, False)
        else:
            close_px = r["ask"] if inv == 1 else r["bid"]
            continue
        inv, qb, qa = 0, r["bid"], r["ask"]
    if inv != 0:
        r = rows[-1]
        book(entry_px, r["bid"] if inv == 1 else r["ask"], inv, False)
    t = len(nets)
    return {"n": n, "fills": fills, "trades": t, "maker_exits": maker_exits, "both_sides": both,
            "maker_exit_pct": round(100.0 * maker_exits / t, 1) if t else None,
            "gross_bp_avg": round(mean(grosses), 3) if t else None,
            "net_bp_avg": round(mean(nets), 3) if t else None,
            "net_bp_sum": round(sum(nets), 3),
            "hit_rate_pct": round(100.0 * sum(1 for x in nets if x > 0) / t, 1) if t else None}


def analyze(by, fees=FEES):
    markets = {k: analyze_market(v) for k, v in by.items()}
    for k, v in by.items():
        if markets[k].get("n", 0) >= 30:
            markets[k]["mm"] = mm_paper(v, fees.get(k.split(":")[0], {}))
            t = mm_touch(v, fees.get(k.split(":")[0], {}))
            if not t.get("no_trades"):
                markets[k]["mm_touch"] = t
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
        # 호가 제공 종이 규칙 합산 (P26)
        mms = [m["mm"] for k, m in markets.items() if k.split(":")[0] == venue and m.get("mm", {}).get("trades")]
        tr = sum(m["trades"] for m in mms)
        summary[venue]["mm_fills"] = sum(m["fills"] for m in mms)
        summary[venue]["mm_trades"] = tr
        summary[venue]["mm_net_bp_sum"] = round(sum(m["net_bp_sum"] for m in mms), 3)
        summary[venue]["mm_net_bp_avg"] = round(sum(m["net_bp_sum"] for m in mms) / tr, 3) if tr else None
        summary[venue]["mm_maker_exit_pct"] = round(100.0 * sum(m["maker_exits"] for m in mms) / tr, 1) if tr else None
        summary[venue]["mm_hit_rate_pct"] = round(sum(m["hit_rate_pct"] * m["trades"] for m in mms) / tr, 1) if tr else None
        # 닿으면 체결 변형 (P29) — 체결 기록이 있는 거래소(업비트)만
        mts = [m["mm_touch"] for k, m in markets.items() if k.split(":")[0] == venue and m.get("mm_touch", {}).get("trades")]
        if mts:
            tr2 = sum(m["trades"] for m in mts)
            summary[venue]["mmt_trades"] = tr2
            summary[venue]["mmt_both_sides"] = sum(m["both_sides"] for m in mts)
            summary[venue]["mmt_net_bp_sum"] = round(sum(m["net_bp_sum"] for m in mts), 3)
            summary[venue]["mmt_net_bp_avg"] = round(sum(m["net_bp_sum"] for m in mts) / tr2, 3)
            summary[venue]["mmt_maker_exit_pct"] = round(100.0 * sum(m["maker_exits"] for m in mts) / tr2, 1)
            summary[venue]["mmt_hit_rate_pct"] = round(sum(m["hit_rate_pct"] * m["trades"] for m in mts) / tr2, 1)
            # P31: 시장별 기록 + 호가폭 ≥ WIDE_SPREAD_BP 시장만 합산 (history 에 남아 날짜별 누적이 가능하도록 summary 에 둔다)
            bym = {}
            for k, m in markets.items():
                if k.split(":")[0] != venue or not m.get("mm_touch", {}).get("trades"):
                    continue
                t = m["mm_touch"]
                bym[k.split(":")[1]] = {"spread_bp": m["spread_bp_mean"], "trades": t["trades"], "both_sides": t["both_sides"],
                                        "net_bp_sum": round(t["net_bp_sum"], 3), "net_bp_avg": round(t["net_bp_sum"] / t["trades"], 3),
                                        "wide": m["spread_bp_mean"] >= WIDE_SPREAD_BP}
            summary[venue]["mmt_by_market"] = bym
            wide = [x for x in bym.values() if x["wide"]]
            trw = sum(x["trades"] for x in wide)
            summary[venue]["mmt_wide_markets"] = sorted(c for c, x in bym.items() if x["wide"])
            summary[venue]["mmt_wide_trades"] = trw
            summary[venue]["mmt_wide_net_bp_sum"] = round(sum(x["net_bp_sum"] for x in wide), 3)
            summary[venue]["mmt_wide_net_bp_avg"] = round(sum(x["net_bp_sum"] for x in wide) / trw, 3) if trw else None
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
    print()
    print("--- 호가 제공 종이 규칙 (양방향 지정가, 지나쳐야 체결, %d초 안에 못 닫으면 시장가) ---" % MM_TTL_SEC)
    print("%-12s %6s %6s %9s %10s %10s %8s" % ("거래소", "체결", "왕복", "지정가청산%", "건당순bp", "순bp합", "적중%"))
    for v, s in out["summary"].items():
        print("%-12s %6d %6d %9s %10s %10s %8s"
              % (v, s.get("mm_fills", 0), s.get("mm_trades", 0),
                 "%.0f" % s["mm_maker_exit_pct"] if s.get("mm_maker_exit_pct") is not None else "-",
                 "%+.2f" % s["mm_net_bp_avg"] if s.get("mm_net_bp_avg") is not None else "-",
                 "%+.1f" % s["mm_net_bp_sum"] if s.get("mm_net_bp_sum") is not None else "-",
                 "%.0f" % s["mm_hit_rate_pct"] if s.get("mm_hit_rate_pct") is not None else "-"))
    tv = {v: s for v, s in out["summary"].items() if s.get("mmt_trades")}
    if tv:
        print("--- 호가 제공 '닿으면 체결' 변형 (실제 체결 기록, P29) ---")
        print("%-12s %6s %8s %9s %10s %10s %8s" % ("거래소", "왕복", "양쪽동시", "지정가청산%", "건당순bp", "순bp합", "적중%"))
        for v, s in tv.items():
            print("%-12s %6d %8d %9.0f %+10.2f %+10.1f %8.0f" % (v, s["mmt_trades"], s["mmt_both_sides"], s["mmt_maker_exit_pct"],
                                                              s["mmt_net_bp_avg"], s["mmt_net_bp_sum"], s["mmt_hit_rate_pct"]))
    for v, s in tv.items():
        if s.get("mmt_by_market"):
            print("--- 닿으면 체결 시장별 (P31: 호가폭 ≥ %.0fbp = ★) ---" % WIDE_SPREAD_BP)
            for c, x in sorted(s["mmt_by_market"].items(), key=lambda kv: -kv[1]["spread_bp"]):
                print("  %-6s %-4s 호가폭 %6.2fbp  왕복 %5d  건당 %+7.2fbp  합 %+9.1f" % (v, c + ("★" if x["wide"] else ""), x["spread_bp"], x["trades"], x["net_bp_avg"], x["net_bp_sum"]))
            if s.get("mmt_wide_trades"):
                print("  ★ 넓은 시장 합(%s): 왕복 %d 건당 %+.2fbp" % (",".join(s["mmt_wide_markets"]), s["mmt_wide_trades"], s["mmt_wide_net_bp_avg"]))
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
        pm = ("호가제공 종이 건당 %+.1fbp(%d왕복)" % (s["mm_net_bp_avg"], s["mm_trades"])) if s.get("mm_net_bp_avg") is not None else "호가제공 왕복 없음"
        parts.append("%s: %s, %s, %s" % (v, mm, im, pm))
    return " / ".join(parts) + " — 하루치, 누적으로 판단"


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
    mm_rows = [r for r in rows if any(s.get("mm_trades") for s in r.get("summary", {}).values())]
    if mm_rows:
        print()
        print("--- 호가 제공 종이 규칙 누적 (2026-11-03 시작, P26) ---")
        print("%-12s %4s %6s %6s %9s %10s %10s %8s" % ("거래소", "일수", "체결", "왕복", "지정가청산%", "건당순bp", "순bp합", "플러스일"))
        for v in venues:
            ss = [r["summary"][v] for r in mm_rows if r["summary"].get(v, {}).get("mm_trades")]
            if not ss:
                continue
            tr = sum(s["mm_trades"] for s in ss); net = sum(s["mm_net_bp_sum"] for s in ss)
            mk = sum(s["mm_maker_exit_pct"] * s["mm_trades"] for s in ss if s.get("mm_maker_exit_pct") is not None)
            print("%-12s %4d %6d %6d %9s %+10.2f %+10.1f %5d/%d"
                  % (v, len(ss), sum(s["mm_fills"] for s in ss), tr, "%.0f" % (mk / tr) if tr else "-",
                     net / tr if tr else 0.0, net, sum(1 for s in ss if s["mm_net_bp_sum"] > 0), len(ss)))
    t_rows = [r for r in rows if any(s.get("mmt_trades") for s in r.get("summary", {}).values())]
    if t_rows:
        print()
        print("--- 호가 제공 '닿으면 체결' 변형 누적 (2026-11-05 시작, P29) ---")
        print("%-12s %4s %6s %8s %10s %10s %8s" % ("거래소", "일수", "왕복", "양쪽동시", "건당순bp", "순bp합", "플러스일"))
        for v in venues:
            ss = [r["summary"][v] for r in t_rows if r["summary"].get(v, {}).get("mmt_trades")]
            if not ss:
                continue
            tr = sum(s["mmt_trades"] for s in ss); net = sum(s["mmt_net_bp_sum"] for s in ss)
            print("%-12s %4d %6d %8d %+10.2f %+10.1f %5d/%d" % (v, len(ss), tr, sum(s["mmt_both_sides"] for s in ss),
                                                                net / tr if tr else 0.0, net, sum(1 for s in ss if s["mmt_net_bp_sum"] > 0), len(ss)))
    m_rows = [r for r in rows if any(s.get("mmt_by_market") for s in r.get("summary", {}).values())]
    if m_rows:
        print()
        print("--- 닿으면 체결 시장별 누적 (2026-11-08 기록 시작, P31: 호가폭 ≥ %.0fbp 시장 = ★) ---" % WIDE_SPREAD_BP)
        print("%-14s %4s %6s %8s %10s %10s %8s" % ("시장", "일수", "왕복", "평균호가폭", "건당순bp", "순bp합", "플러스일"))
        for v in venues:
            acc = {}
            for r in m_rows:
                for c, x in r["summary"].get(v, {}).get("mmt_by_market", {}).items():
                    a = acc.setdefault(c, {"days": 0, "trades": 0, "net": 0.0, "sp": [], "plus": 0, "wide": 0})
                    a["days"] += 1; a["trades"] += x["trades"]; a["net"] += x["net_bp_sum"]; a["sp"].append(x["spread_bp"])
                    a["plus"] += x["net_bp_sum"] > 0; a["wide"] += x["wide"]
            for c, a in sorted(acc.items(), key=lambda kv: -mean(kv[1]["sp"])):
                print("%-14s %4d %6d %8.2f %+10.2f %+10.1f %5d/%d" % ("%s:%s%s" % (v, c, "★" if a["wide"] == a["days"] else ""), a["days"], a["trades"],
                                                                     mean(a["sp"]), a["net"] / a["trades"] if a["trades"] else 0.0, a["net"], a["plus"], a["days"]))
            ws = [r["summary"][v] for r in m_rows if r["summary"].get(v, {}).get("mmt_wide_trades")]
            if ws:
                tr = sum(s["mmt_wide_trades"] for s in ws); net = sum(s["mmt_wide_net_bp_sum"] for s in ws)
                print("%-14s %4d %6d %8s %+10.2f %+10.1f %5d/%d" % (v + ":★합계", len(ws), tr, "-", net / tr if tr else 0.0, net, sum(1 for s in ws if s["mmt_wide_net_bp_sum"] > 0), len(ws)))
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
                      "spread": "호가 폭 = (매도1−매수1)/중간가. 반폭 − 지정가 수수료 > 0 이면 양쪽 지정가로 한 번 왕복에 남는 여지가 있다는 뜻 (체결·역선택은 별도)",
                      "mm": "호가 제공 종이 규칙(P26): 최우선 매수·매도호가에 1단위씩, 다음 표본의 상대 호가가 내 가격을 지나쳐야 체결. 재고 ±1, 반대쪽 최우선호가에 재게시, %d초 안에 못 닫으면 상대 호가에 시장가. 지정가=메이커·시장가=테이커 수수료" % MM_TTL_SEC,
                      "mm_wide": "P31(2026-11-08): 닿으면 체결 결과를 시장별로 남기고, 평균 호가폭 ≥ %.0fbp(왕복 지정가 수수료의 2배) 시장만 따로 합산(mmt_wide_*). 문턱은 수수료에서 나온 값으로 고정" % WIDE_SPREAD_BP,
                      "mm_touch": "닿으면 체결 변형(P29, 업비트만): 직전 표본 이후 실제 체결 중 매도 주도 최저가 ≤ 내 매수가면 매수 체결, 매수 주도 최고가 ≥ 내 매도가면 매도 체결. 줄 서기 무시(낙관 쪽 경계). 나머지는 P26 과 같음"},
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
