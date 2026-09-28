# -*- coding: utf-8 -*-
"""
CARRYGATE — 초단타(스캘핑) 연구 브리지

거래소 공개 API 에서 1분봉을 받아, **미리 고정해 둔 초단타 규칙**들을 전날 하루치에
그대로 적용해 보고, 수수료·호가 차이를 뺀 순수익을 scalp.json 에 기록한다.
같은 규칙을 1분·5분·15분봉 세 가지 시간 단위에 적용해 "어느 시간 단위부터 비용을
넘는가"를 본다. 매일 한 줄씩 scalp_history.jsonl 에 쌓여 "어떤 규칙이 꾸준히 남는가"를 본다.

핵심 원칙
  - 규칙은 코드에 고정돼 있고 매일 같은 규칙을 **새 데이터**에 적용한다.
    과거 데이터에 맞춰 규칙을 고치면(과최적화) 그날부터 기록을 새로 세어야 한다.
  - 수익은 항상 수수료와 호가 차이를 뺀 뒤에 말한다. 초단타는 한 번에 먹는 폭이
    작아서 비용을 빼면 뒤집히는 경우가 대부분이다.
  - 지정가(메이커) 규칙은 **가격이 주문가를 지나쳐야 체결**로 인정한다(보수적).
    3봉 안에 체결되지 않으면 취소하고, 청산은 3봉 뒤 시장가로 강제한다.
  - 주문 기능 없음. 읽기 전용 공개 API 만 쓴다. API 키 불필요.

사용법
  python scalp.py                      # 전날(UTC) 하루치를 받아 평가하고 저장
  python scalp.py --dry-run            # 저장하지 않고 화면에만
  python scalp.py --fixture tests/fixtures   # 저장된 CSV 로 평가 (오프라인 검증용)
  python scalp.py --report             # 쌓인 기록으로 규칙별 누적 성적표
"""
import json, sys, time, csv, os, math, argparse, urllib.request
from datetime import datetime, timezone, timedelta

COINS = ["BTC", "ETH", "XRP", "TRX", "LINK", "DOGE"]
KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (compatible; carrygate/1.0)",
      "Accept": "application/json"}

EVAL_BARS = 1440      # 평가 구간: 하루 = 1분봉 1440개
WARMUP_MIN = 1080     # 지표 계산용 준비 구간(분). 15분봉 기준 72봉 (가장 긴 지표 60봉 + 여유)
TIMEFRAMES = [1, 5, 15]   # 같은 규칙을 적용해 보는 봉 길이(분)
SPREAD_TICKS = 1.0    # 시장가 체결 시 왕복으로 잃는 호가 폭 (틱 단위). 1틱 = 매수호가·매도호가 차이가 최소일 때
LIMIT_TTL = 3         # 지정가 주문을 기다리는 최대 봉 수. 넘으면 진입은 취소, 청산은 시장가

# --- 거래소별 수수료 (1회 체결, %) ------------------------------------------
# taker = 시장가(바로 체결), maker = 지정가(대기 후 체결).
VENUES = {
    "upbit":       {"kind": "spot", "quote": "KRW",  "taker_pct": 0.05,  "maker_pct": 0.05,
                    "short_ok": False, "fee_source": "업비트 기본 수수료 0.05% (시장가·지정가 동일)"},
    "hyperliquid": {"kind": "perp", "quote": "USDC", "taker_pct": 0.045, "maker_pct": 0.015,
                    "short_ok": True,  "fee_source": "공식 문서 기본 등급 (API 실측 시 덮어씀)"},
    "okx":         {"kind": "perp", "quote": "USDT", "taker_pct": 0.05,  "maker_pct": 0.02,
                    "short_ok": True,  "fee_source": "OKX 공식 수수료표 일반 등급"},
    # 아래는 GitHub Actions(미국 서버)에서 지역 차단돼 자동 수집은 안 되지만,
    # 오프라인 CSV(--fixture) 검증에는 쓸 수 있도록 수수료만 둔다.
    "binance":     {"kind": "perp", "quote": "USDT", "taker_pct": 0.05,  "maker_pct": 0.02,
                    "short_ok": True,  "fee_source": "바이낸스 선물 일반 등급 (fixture 전용)"},
}
HL_ZERO_ADDR = "0x" + "0" * 40


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
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------- 수집

def bar(t, o, h, l, c, v):
    return {"t": int(t), "o": o, "h": h, "l": l, "c": c, "v": v}


def finish(bars, start, end):
    """중복 제거, 시간순 정렬, [start, end) 로 자른다."""
    by_t = {}
    for b in bars:
        if b["t"] is None or None in (b["o"], b["h"], b["l"], b["c"]):
            continue
        if start <= b["t"] < end:
            by_t[b["t"]] = b
    return [by_t[t] for t in sorted(by_t)]


def src_upbit(coin, start, end):
    """업비트 1분봉. 한 번에 200개, to= 로 과거 방향으로 넘긴다."""
    out, to = [], end
    for _ in range(40):
        url = ("https://api.upbit.com/v1/candles/minutes/1?market=KRW-%s&count=200&to=%s"
               % (coin, datetime.fromtimestamp(to, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
        rows = http_json(url)
        if not isinstance(rows, list) or not rows:
            break
        oldest = None
        for r in rows:
            t = int(datetime.strptime(r["candle_date_time_utc"], "%Y-%m-%dT%H:%M:%S")
                    .replace(tzinfo=timezone.utc).timestamp())
            out.append(bar(t, f(r["opening_price"]), f(r["high_price"]), f(r["low_price"]),
                           f(r["trade_price"]), f(r.get("candle_acc_trade_volume"))))
            oldest = t if oldest is None else min(oldest, t)
        if oldest is None or oldest <= start or oldest >= to:
            break
        to = oldest
        time.sleep(0.12)
    return finish(out, start, end)


def src_hyperliquid(coin, start, end):
    rows = http_json("https://api.hyperliquid.xyz/info",
                     data={"type": "candleSnapshot",
                           "req": {"coin": coin, "interval": "1m",
                                   "startTime": start * 1000, "endTime": end * 1000}})
    out = [bar(int(r["t"]) // 1000, f(r["o"]), f(r["h"]), f(r["l"]), f(r["c"]), f(r["v"]))
           for r in rows]
    return finish(out, start, end)


def src_okx(coin, start, end):
    """OKX 1분봉. history-candles 는 한 번에 100개, after= 보다 과거를 준다."""
    out, after = [], end * 1000
    for _ in range(60):
        d = http_json("https://www.okx.com/api/v5/market/history-candles"
                      "?instId=%s-USDT-SWAP&bar=1m&limit=100&after=%d" % (coin, after))
        rows = d.get("data") or []
        if not rows:
            break
        oldest = None
        for r in rows:
            t = int(r[0]) // 1000
            out.append(bar(t, f(r[1]), f(r[2]), f(r[3]), f(r[4]), f(r[5])))
            oldest = t if oldest is None else min(oldest, t)
        if oldest is None or oldest <= start:
            break
        after = oldest * 1000
        time.sleep(0.12)
    return finish(out, start, end)


SOURCES = [("upbit", src_upbit), ("hyperliquid", src_hyperliquid), ("okx", src_okx)]


def hl_live_fees():
    """하이퍼리퀴드 실제 수수료표(기본 등급). 실패하면 None."""
    try:
        d = http_json("https://api.hyperliquid.xyz/info",
                      data={"type": "userFees", "user": HL_ZERO_ADDR})
        fs = d.get("feeSchedule") or {}
        taker, maker = f(fs.get("cross")), f(fs.get("add"))
        if taker is None:
            taker = f(d.get("userCrossRate"))
        if maker is None:
            maker = f(d.get("userAddRate"))
        if taker is not None and maker is not None:
            return {"taker_pct": taker * 100.0, "maker_pct": maker * 100.0,
                    "fee_source": "하이퍼리퀴드 API 실측 (userFees, 기본 등급)"}
    except Exception:
        pass
    return None


def load_fixture(path):
    """tests/fixtures/<venue>_<COIN>_1m.csv 를 읽는다. 오프라인 검증용."""
    data = {}
    for name in sorted(os.listdir(path)):
        if not name.endswith("_1m.csv"):
            continue
        venue, coin = name[:-7].split("_", 1)
        rows = []
        with open(os.path.join(path, name), encoding="utf-8") as fp:
            for r in csv.DictReader(fp):
                rows.append(bar(r["t"], f(r["o"]), f(r["h"]), f(r["l"]), f(r["c"]), f(r["v"])))
        data.setdefault(venue, {})[coin] = finish(rows, -1, 1 << 40)
    return data


def resample(bars, minutes):
    """1분봉 -> N분봉. 시각은 N분 경계로 내림. 거래가 없던 구간은 봉이 생기지 않는다."""
    if minutes == 1:
        return bars
    sec = minutes * 60
    out = []
    for b in bars:
        t = b["t"] // sec * sec
        if out and out[-1]["t"] == t:
            o = out[-1]
            o["h"] = max(o["h"], b["h"])
            o["l"] = min(o["l"], b["l"])
            o["c"] = b["c"]
            o["v"] = (o["v"] or 0.0) + (b["v"] or 0.0)
        else:
            out.append(bar(t, b["o"], b["h"], b["l"], b["c"], b["v"] or 0.0))
    return out


# ----------------------------------------------------------------- 지표

def roll_mean(x, n):
    """직전 n개(현재 봉 포함)의 평균. 자료가 모자라면 None."""
    out, s = [None] * len(x), 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n:
            s -= x[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def roll_std(x, n):
    out = [None] * len(x)
    for i in range(n - 1, len(x)):
        w = x[i - n + 1:i + 1]
        m = sum(w) / n
        out[i] = math.sqrt(sum((v - m) ** 2 for v in w) / n)
    return out


def roll_max_prev(x, n):
    """현재 봉을 뺀 직전 n개의 최대. 돌파 판정용."""
    return [max(x[i - n:i]) if i >= n else None for i in range(len(x))]


def roll_min_prev(x, n):
    return [min(x[i - n:i]) if i >= n else None for i in range(len(x))]


def tick_estimate(bars):
    """가격이 움직인 최소 단위(틱)를 데이터에서 추정한다."""
    px = sorted(set(v for b in bars for v in (b["o"], b["h"], b["l"], b["c"])))
    diffs = [b - a for a, b in zip(px, px[1:]) if b - a > 0]
    if not diffs:
        return None
    d = min(diffs)
    # 부동소수 찌꺼기 정리: 유효숫자 3자리로
    return float("%.3g" % d)


class Ctx(object):
    def __init__(self, bars, eval_start):
        self.t = [b["t"] for b in bars]
        self.o = [b["o"] for b in bars]
        self.h = [b["h"] for b in bars]
        self.l = [b["l"] for b in bars]
        self.c = [b["c"] for b in bars]
        self.v = [b["v"] if b["v"] is not None else 0.0 for b in bars]
        self.eval_start = eval_start
        c, h, l, v = self.c, self.h, self.l, self.v
        sma20, std20 = roll_mean(c, 20), roll_std(c, 20)
        self.z20 = [((c[i] - sma20[i]) / std20[i]) if (sma20[i] is not None and std20[i]) else None
                    for i in range(len(c))]
        self.hh20, self.ll20, self.ll10, self.hh10 = (roll_max_prev(h, 20), roll_min_prev(l, 20),
                                                      roll_min_prev(l, 10), roll_max_prev(h, 10))
        vavg = roll_mean(v, 20)
        self.vavg20 = [None] + vavg[:-1]          # 현재 봉을 뺀 직전 20봉 평균 거래량
        pv = roll_mean([c[i] * v[i] for i in range(len(c))], 60)
        vs = roll_mean(v, 60)
        std60 = roll_std(c, 60)
        self.zvwap = [None] * len(c)
        for i in range(len(c)):
            if pv[i] is not None and vs[i] and std60[i]:
                self.zvwap[i] = (c[i] - pv[i] / vs[i]) / std60[i]


# ----------------------------------------------------------------- 규칙 (고정)
# 신호는 i번째 봉의 종가에서 계산한다. 체결은
#   시장가 규칙: i+1번째 봉의 시가
#   지정가 규칙: i번째 봉 종가에 주문을 걸고, 다음 봉들의 저가(매수)/고가(매도)가 주문가를 지나쳐야 체결
# 반환값 1 = 매수(롱), -1 = 매도(숏), None = 신호 없음.

def e_mr(ctx, i):
    z = ctx.z20[i]
    if z is None:
        return None
    return 1 if z <= -2.0 else (-1 if z >= 2.0 else None)


def x_mr(ctx, i, pos):
    z = ctx.z20[i]
    return z is not None and (z >= 0 if pos["side"] == 1 else z <= 0)


def e_brk(ctx, i):
    if ctx.hh20[i] is None:
        return None
    if ctx.c[i] > ctx.hh20[i]:
        return 1
    if ctx.c[i] < ctx.ll20[i]:
        return -1
    return None


def x_brk(ctx, i, pos):
    if ctx.ll10[i] is None:
        return False
    return ctx.c[i] < ctx.ll10[i] if pos["side"] == 1 else ctx.c[i] > ctx.hh10[i]


def e_vol(ctx, i):
    va = ctx.vavg20[i]
    rng = ctx.h[i] - ctx.l[i]
    if not va or rng <= 0 or ctx.v[i] < 3.0 * va:
        return None
    pos_in_bar = (ctx.c[i] - ctx.l[i]) / rng
    return 1 if pos_in_bar >= 0.8 else (-1 if pos_in_bar <= 0.2 else None)


def e_vwap(ctx, i):
    z = ctx.zvwap[i]
    if z is None:
        return None
    return 1 if z <= -1.5 else (-1 if z >= 1.5 else None)


def x_vwap(ctx, i, pos):
    z = ctx.zvwap[i]
    return z is not None and (z >= 0 if pos["side"] == 1 else z <= 0)


def e_base(ctx, i):
    return 1 if (i - ctx.eval_start) % 10 == 0 else None


def x_never(ctx, i, pos):
    return False


BASELINE = "baseline_hold5"

STRATEGIES = [
    {"key": "mr_z20", "name": "평균회귀(20봉 z±2)", "entry": e_mr, "exit": x_mr, "max_hold": 30, "exec": "market",
     "desc": "최근 20봉 평균에서 표준편차 2배 넘게 벗어나면 되돌아온다고 보고 반대로 진입. 평균 복귀 시 청산, 최대 30봉"},
    {"key": "mr_z20_lim", "name": "평균회귀 지정가", "entry": e_mr, "exit": x_mr, "max_hold": 30, "exec": "limit",
     "desc": "평균회귀와 같은 신호를 지정가로. 신호 봉 종가에 주문, 가격이 지나쳐야 체결, 3봉 미체결 시 취소"},
    {"key": "brk_20", "name": "돌파(20봉 고저)", "entry": e_brk, "exit": x_brk, "max_hold": 60, "exec": "market",
     "desc": "직전 20봉 최고가를 종가로 넘으면 추세 시작으로 보고 진입. 직전 10봉 최저가 깨지면 청산, 최대 60봉"},
    {"key": "vol_spike", "name": "거래량 급증 추종", "entry": e_vol, "exit": x_never, "max_hold": 5, "exec": "market",
     "desc": "거래량이 평소 3배 이상이고 봉이 한쪽 끝에서 마감하면 그 방향으로 5봉 보유"},
    {"key": "vwap_fade", "name": "VWAP 이탈 되돌림", "entry": e_vwap, "exit": x_vwap, "max_hold": 60, "exec": "market",
     "desc": "60봉 거래량가중평균가에서 1.5σ 넘게 벗어나면 반대로 진입, 평균가 복귀 시 청산, 최대 60봉"},
    {"key": "vwap_fade_lim", "name": "VWAP 되돌림 지정가", "entry": e_vwap, "exit": x_vwap, "max_hold": 60, "exec": "limit",
     "desc": "VWAP 되돌림과 같은 신호를 지정가로. 신호 봉 종가에 주문, 가격이 지나쳐야 체결, 3봉 미체결 시 취소"},
    {"key": BASELINE, "name": "기준선(무작위 5봉 보유)", "entry": e_base, "exit": x_never, "max_hold": 5, "exec": "market",
     "desc": "규칙 없이 10봉마다 사서 5봉 뒤 파는 것. 다른 규칙이 이것도 못 이기면 규칙에 정보가 없는 것"},
]


# ----------------------------------------------------------------- 검증 엔진

def limit_filled(ctx, i, side, px):
    """지정가 주문이 i번째 봉에서 체결됐나. 매수는 저가가 주문가 아래로, 매도는 고가가 위로 지나쳐야 한다."""
    return ctx.l[i] < px if side == 1 else ctx.h[i] > px


def backtest(ctx, strat, short_ok):
    """체결 목록을 돌려준다. 각 체결에 진입·청산이 지정가(maker)였는지 남긴다."""
    limit = strat.get("exec") == "limit"
    n = len(ctx.c)
    pos, pending, exit_order, trades = None, None, None, []
    for i in range(1, n):
        if pos is not None:
            held = i - pos["entry_i"]
            if exit_order is not None:
                # 청산 지정가가 걸려 있다. 이 봉에서 체결되나?
                if limit_filled(ctx, i, -pos["side"], exit_order["px"]):
                    trades.append(close_trade(pos, exit_order["px"], i, True, False))
                    pos, exit_order = None, None
                elif held >= strat["max_hold"] or i - exit_order["placed_i"] >= LIMIT_TTL:
                    trades.append(close_trade(pos, ctx.o[i], i, False, False))
                    pos, exit_order = None, None
            elif held >= strat["max_hold"]:
                trades.append(close_trade(pos, ctx.o[i], i, False, False))
                pos = None
            elif strat["exit"](ctx, i - 1, pos):
                if limit:
                    exit_order = {"px": ctx.c[i - 1], "placed_i": i}
                    if limit_filled(ctx, i, -pos["side"], exit_order["px"]):
                        trades.append(close_trade(pos, exit_order["px"], i, True, False))
                        pos, exit_order = None, None
                else:
                    trades.append(close_trade(pos, ctx.o[i], i, False, False))
                    pos = None
        if pos is None and pending is not None:
            # 진입 지정가가 걸려 있다.
            if limit_filled(ctx, i, pending["side"], pending["px"]):
                pos = {"side": pending["side"], "entry_i": i, "entry_px": pending["px"], "entry_maker": True}
                pending = None
            elif i - pending["placed_i"] >= LIMIT_TTL - 1:
                pending = None
        if pos is None and pending is None and i >= ctx.eval_start:
            s = strat["entry"](ctx, i - 1)
            if s == 1 or (s == -1 and short_ok):
                if limit:
                    pending = {"side": s, "px": ctx.c[i - 1], "placed_i": i}
                    if limit_filled(ctx, i, s, pending["px"]):
                        pos = {"side": s, "entry_i": i, "entry_px": pending["px"], "entry_maker": True}
                        pending = None
                else:
                    pos = {"side": s, "entry_i": i, "entry_px": ctx.o[i], "entry_maker": False}
    if pos is not None:
        trades.append(close_trade(pos, ctx.c[n - 1], n - 1, False, True))
    return trades


def close_trade(pos, px, i, exit_maker, forced):
    gross = pos["side"] * (px / pos["entry_px"] - 1.0) * 100.0
    return {"side": pos["side"], "entry_i": pos["entry_i"], "exit_i": i,
            "hold": i - pos["entry_i"], "gross_pct": gross, "forced": forced,
            "entry_maker": pos["entry_maker"], "exit_maker": exit_maker}


def max_drawdown(seq):
    peak, worst, cum = 0.0, 0.0, 0.0
    for x in seq:
        cum += x
        peak = max(peak, cum)
        worst = min(worst, cum - peak)
    return worst


def side_cost(maker, fees, spread_pct):
    """체결 1회 비용(%). 시장가는 수수료 + 호가 폭의 절반, 지정가는 수수료만."""
    return fees["maker_pct"] if maker else fees["taker_pct"] + spread_pct / 2.0


def summarize(trades, fees, spread_pct, mid_i):
    """체결 목록 -> 하루 성적. 수익률은 매매 1건당 투입금 대비 %, 단순 합산."""
    n = len(trades)
    costs = [side_cost(t["entry_maker"], fees, spread_pct) + side_cost(t["exit_maker"], fees, spread_pct)
             for t in trades]
    nets = [t["gross_pct"] - c for t, c in zip(trades, costs)]
    gross = sum(t["gross_pct"] for t in trades)
    fee = sum(costs)
    first = sum(x for t, x in zip(trades, nets) if t["entry_i"] < mid_i)
    second = sum(x for t, x in zip(trades, nets) if t["entry_i"] >= mid_i)
    wins = sum(1 for x in nets if x > 0)
    gain = sum(x for x in nets if x > 0)
    loss = -sum(x for x in nets if x < 0)
    holds = sorted(t["hold"] for t in trades)
    maker_sides = sum(int(t["entry_maker"]) + int(t["exit_maker"]) for t in trades)
    rt_taker = 2.0 * fees["taker_pct"] + spread_pct
    return {
        "trades": n,
        "longs": sum(1 for t in trades if t["side"] == 1),
        "shorts": sum(1 for t in trades if t["side"] == -1),
        "forced_close": sum(1 for t in trades if t["forced"]),
        "maker_sides": maker_sides,
        "taker_sides": 2 * n - maker_sides,
        "win_rate_pct": round(100.0 * wins / n, 1) if n else None,
        "gross_pct": round(gross, 4),
        "fee_pct": round(fee, 4),
        "net_pct": round(gross - fee, 4),
        "net_if_all_maker_pct": round(gross - n * 2.0 * fees["maker_pct"], 4),
        "avg_gross_per_trade_pct": round(gross / n, 4) if n else None,
        "avg_net_per_trade_pct": round((gross - fee) / n, 4) if n else None,
        "edge_vs_cost": round((gross / n) / rt_taker, 2) if (n and rt_taker) else None,
        "profit_factor": round(gain / loss, 2) if loss > 0 else (None if not n else 99.0),
        "max_drawdown_pct": round(max_drawdown(nets), 4),
        "median_hold_bars": holds[len(holds) // 2] if holds else None,
        "first_half_net_pct": round(first, 4),
        "second_half_net_pct": round(second, 4),
    }


def hurdle(ctx, rt_taker, horizons=(1, 5, 15, 30, 60)):
    """비용 문턱: 이 시장에서 h분 뒤 가격 변동폭이 왕복 비용보다 큰 경우가 얼마나 되나.

    needed_win_rate = 평균 변동폭만큼 벌거나 잃는다고 할 때, 비용을 내고도 본전이
    되려면 필요한 승률. (1 + 비용/변동폭) / 2. 100% 를 넘으면 그 시간 단위로는
    아무리 잘 맞혀도 비용을 못 넘는다는 뜻이다."""
    c = ctx.c
    out = {}
    for h in horizons:
        moves = [abs(c[i + h] / c[i] - 1.0) * 100.0
                 for i in range(ctx.eval_start, len(c) - h)]
        if not moves:
            continue
        m = sum(moves) / len(moves)
        s = sorted(moves)
        out["h%d" % h] = {
            "mean_abs_move_pct": round(m, 4),
            "median_abs_move_pct": round(s[len(s) // 2], 4),
            "share_above_cost_pct": round(100.0 * sum(1 for x in moves if x > rt_taker) / len(moves), 1),
            "needed_win_rate_pct": round(100.0 * (1.0 + rt_taker / m) / 2.0, 1) if m else None,
        }
    rets = [(c[i] / c[i - 1] - 1.0) * 100.0 for i in range(max(1, ctx.eval_start), len(c))]
    if rets:
        mu = sum(rets) / len(rets)
        out["ret_1m_std_pct"] = round(math.sqrt(sum((r - mu) ** 2 for r in rets) / len(rets)), 4)
    return out


def evaluate(venue, coin, bars, eval_start, fees):
    """거래소·코인 하나에 모든 시간 단위 × 모든 규칙을 적용한다. bars 는 1분봉."""
    tick = tick_estimate(bars)
    mid_px = bars[eval_start]["c"] if eval_start < len(bars) else bars[-1]["c"]
    spread_pct = (SPREAD_TICKS * tick / mid_px * 100.0) if (tick and mid_px) else 0.0
    rt_taker = 2.0 * fees["taker_pct"] + spread_pct
    rt_maker = 2.0 * fees["maker_pct"]
    eval_t0 = bars[eval_start]["t"] if eval_start < len(bars) else bars[-1]["t"] + 1
    rows = []
    hz = None
    for tf in TIMEFRAMES:
        tb = resample(bars, tf)
        es = next((i for i, b in enumerate(tb) if b["t"] >= eval_t0), len(tb))
        if len(tb) - es < 4:
            continue
        ctx = Ctx(tb, es)
        if tf == 1:
            hz = hurdle(ctx, rt_taker)
        mid_i = es + (len(tb) - es) // 2
        tf_rows = []
        for st in STRATEGIES:
            tr = backtest(ctx, st, fees["short_ok"])
            r = {"venue": venue, "coin": coin, "tf": "%dm" % tf, "strategy": st["key"],
                 "strategy_name": st["name"], "exec": st["exec"]}
            r.update(summarize(tr, fees, spread_pct, mid_i))
            tf_rows.append(r)
        base = next(r for r in tf_rows if r["strategy"] == BASELINE)
        for r in tf_rows:
            r["beats_baseline"] = r["net_pct"] > base["net_pct"]
            r["robust"] = bool(r["trades"] >= 10 and r["net_pct"] > 0
                               and r["first_half_net_pct"] > 0 and r["second_half_net_pct"] > 0
                               and r["beats_baseline"])
        rows.extend(tf_rows)
    cost = {"tick_est": tick, "spread_pct_est": round(spread_pct, 5),
            "taker_pct": fees["taker_pct"], "maker_pct": fees["maker_pct"],
            "round_trip_taker_pct": round(rt_taker, 5), "round_trip_maker_pct": round(rt_maker, 5),
            "fee_source": fees["fee_source"]}
    return rows, cost, hz or {}


# ----------------------------------------------------------------- 저장·보고

def history_key(r):
    return "%s:%s:%s:%s" % (r["venue"], r["coin"], r["tf"], r["strategy"])


def split_key(k):
    """'upbit:BTC:5m:mr_z20' -> (venue, coin, tf, strategy). 옛 형식(tf 없음)은 1m 으로 본다."""
    p = k.split(":")
    if len(p) == 3:
        return p[0], p[1], "1m", p[2]
    return p[0], p[1], p[2], p[3]


def write_history(out, path):
    # 요약은 숫자만 남긴다 (상위 조합 목록 같은 덩어리는 scalp.json 에 있다)
    s = out["summary"]
    slim = {k: s[k] for k in ("rows_total", "rows_positive", "rows_robust", "markets", "verdict") if k in s}
    rec = {"date": out["date"], "eval_day_utc": out["eval_day_utc"], "summary": slim,
           "rows": {history_key(r): {"n": r["trades"], "g": r["gross_pct"], "f": r["fee_pct"],
                                     "nt": r["net_pct"], "rb": r["robust"]} for r in out["rows"]},
           "hurdle": {k: dict([("h%d_needed_win_rate_pct" % h,
                                v["hurdle"].get("h%d" % h, {}).get("needed_win_rate_pct")) for h in (1, 5, 15, 30, 60)]
                              + [("round_trip_taker_pct", v["cost"]["round_trip_taker_pct"])])
                      for k, v in out["markets"].items()}}
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


def print_day(out):
    s = out["summary"]
    print("=== 초단타 하루 검증 (%s, 평가일 %s UTC) ===" % (out["date"], out["eval_day_utc"]))
    print("시장 %d곳, 규칙 조합 %d개 중 순수익 플러스 %d개, 견고 %d개"
          % (len(out["markets"]), s["rows_total"], s["rows_positive"], s["rows_robust"]))
    print()
    print("--- 비용 문턱 (시장가 왕복 비용 vs 보유 시간별 변동폭 -> 본전 승률) ---")
    print("%-12s %-5s %9s %8s %8s %8s %8s %8s" % ("거래소", "코인", "왕복비용%", "1분", "5분", "15분", "30분", "60분"))
    for k, m in out["markets"].items():
        cells = ""
        for h in (1, 5, 15, 30, 60):
            v = m["hurdle"].get("h%d" % h, {}).get("needed_win_rate_pct")
            cells += "%8s" % ("%.0f%%" % v if v is not None else "-")
        print("%-12s %-5s %9.4f %s" % (m["venue"], m["coin"], m["cost"]["round_trip_taker_pct"], cells))
    print()
    print("--- 시간 단위 × 규칙 합계 (모든 시장 합산, 순수익 %) ---")
    print("%-5s %-22s %6s %9s %9s %9s %10s" % ("봉", "규칙", "매매", "총수익", "비용", "순수익", "플러스시장"))
    for tf in TIMEFRAMES:
        for st in STRATEGIES:
            a = s["by_tf_strategy"].get("%dm:%s" % (tf, st["key"]))
            if not a:
                continue
            print("%-5s %-22s %6d %+9.3f %9.3f %+9.3f %7d/%d"
                  % ("%dm" % tf, st["name"], a["trades"], a["gross_pct"], a["fee_pct"], a["net_pct"],
                     a["rows_positive"], a["rows_total"]))
    print()
    print("--- 상위 8개 조합 (순수익) ---")
    for r in s["top"]:
        print("%-12s %-5s %-4s %-22s 매매 %3d  승률 %5s  순 %+7.3f%%  기준선 대비 %s  견고 %s"
              % (r["venue"], r["coin"], r["tf"], r["strategy_name"], r["trades"],
                 "%.0f%%" % r["win_rate_pct"] if r["win_rate_pct"] is not None else "-",
                 r["net_pct"], "O" if r["beats_baseline"] else "X", "O" if r["robust"] else "X"))
    print()
    print("판정:", s["verdict"])


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


MIN_DAYS = 7       # 이 일수 미만이면 후보를 뽑지 않는다
MIN_POS_RATIO = 0.6


def report(path="scalp_history.jsonl"):
    rows = load_history(path)
    if not rows:
        print("기록 없음 — scalp.py 가 하루 한 번 실행되면서 쌓인다.")
        return 0
    acc = {}
    for r in rows:
        for k, v in r.get("rows", {}).items():
            a = acc.setdefault(k, {"days": 0, "pos": 0, "nt": 0.0, "n": 0, "rb": 0})
            a["days"] += 1
            a["pos"] += 1 if v["nt"] > 0 else 0
            a["nt"] += v["nt"]
            a["n"] += v["n"]
            a["rb"] += 1 if v.get("rb") else 0
    days = len(rows)
    print("=== 초단타 규칙별 누적 성적 (%d일치: %s ~ %s) ===" % (days, rows[0]["date"], rows[-1]["date"]))
    print("%-38s %4s %6s %12s %6s %5s" % ("거래소:코인:봉:규칙", "일수", "플러스", "누적순수익%", "매매", "견고"))
    ranked = sorted(acc.items(), key=lambda kv: kv[1]["nt"], reverse=True)
    for k, a in ranked[:15]:
        print("%-38s %4d %6d %+12.3f %6d %5d" % (k, a["days"], a["pos"], a["nt"], a["n"], a["rb"]))
    if len(ranked) > 15:
        print("... (%d개 더)" % (len(ranked) - 15))
    print()
    print("--- 시간 단위 × 규칙 종류별 합산 ---")
    by = {}
    for k, a in acc.items():
        _, _, tf, st = split_key(k)
        b = by.setdefault((tf, st), {"nt": 0.0, "n": 0, "cells": 0, "pos": 0})
        b["nt"] += a["nt"]; b["n"] += a["n"]; b["cells"] += 1; b["pos"] += a["pos"]
    for tf in TIMEFRAMES:
        for st in STRATEGIES:
            b = by.get(("%dm" % tf, st["key"]))
            if b:
                print("%-4s %-22s 누적순수익 %+9.3f%%  매매 %5d건  플러스 일수 %d/%d"
                      % ("%dm" % tf, st["name"], b["nt"], b["n"], b["pos"], b["cells"] * days))
    print()
    if days < MIN_DAYS:
        print("아직 %d일치뿐이다. 최소 %d일은 모여야 후보를 말할 수 있다." % (days, MIN_DAYS))
        return 0
    cands = []
    for k, a in acc.items():
        venue, coin, tf, st = split_key(k)
        if st == BASELINE or a["days"] < MIN_DAYS:
            continue
        base = acc.get("%s:%s:%s:%s" % (venue, coin, tf, BASELINE), {"nt": 0.0})
        if a["nt"] > 0 and a["pos"] / a["days"] >= MIN_POS_RATIO and a["nt"] > base["nt"]:
            cands.append((k, a))
    if cands:
        cands.sort(key=lambda kv: kv[1]["nt"], reverse=True)
        print("후보 (누적 플러스 + 플러스 일수 %.0f%% 이상 + 기준선 초과):" % (MIN_POS_RATIO * 100))
        for k, a in cands:
            print("  %s — 누적 순 %+.3f%% (%d일 중 %d일 플러스, 매매 %d건)" % (k, a["nt"], a["days"], a["pos"], a["n"]))
        print("후보라도 실전 전에 소액으로 체결 가능 여부를 확인해야 한다. 지정가 규칙은 '가격이 주문가를 지나쳐야 체결' 가정이다.")
    else:
        print("후보 없음 — %d일 동안 비용을 넘기며 꾸준히 남은 규칙이 없다." % days)
    return 0


# ----------------------------------------------------------------- 메인

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", help="CSV 폴더로 오프라인 평가 (<venue>_<COIN>_1m.csv)")
    ap.add_argument("--dry-run", action="store_true", help="저장하지 않는다")
    ap.add_argument("--report", action="store_true", help="누적 성적표만 출력")
    ap.add_argument("--out", default="scalp.json")
    ap.add_argument("--history", default="scalp_history.jsonl")
    ap.add_argument("--eval-bars", type=int, default=EVAL_BARS)
    a = ap.parse_args(argv)
    if a.report:
        return report(a.history)

    now = datetime.now(KST)
    fees = {k: dict(v) for k, v in VENUES.items()}
    data, failed = {}, {}
    d0 = None

    if a.fixture:
        data = load_fixture(a.fixture)
        eval_day = "fixture"
        mode = "오프라인 CSV (%s)" % a.fixture
    else:
        live = hl_live_fees()
        if live:
            fees["hyperliquid"].update(live)
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        end = int(today.timestamp())
        d0 = end - a.eval_bars * 60
        start = d0 - WARMUP_MIN * 60
        eval_day = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        mode = "실시간 수집 %s ~ %s UTC" % (datetime.fromtimestamp(start, timezone.utc).strftime("%m-%d %H:%M"),
                                          today.strftime("%m-%d %H:%M"))
        for venue, fn in SOURCES:
            got = {}
            for c in COINS:
                try:
                    bars = fn(c, start, end)
                    n_eval = sum(1 for b in bars if b["t"] >= d0)
                    if n_eval < a.eval_bars // 2:
                        raise RuntimeError("평가 구간 봉 %d개뿐" % n_eval)
                    got[c] = bars
                except Exception as e:
                    failed["%s/%s" % (venue, c)] = str(e)[:160]
                time.sleep(0.2)
            if got:
                data[venue] = got

    markets, rows = {}, []
    for venue in data:
        if venue not in fees:
            continue
        for coin, bars in data[venue].items():
            if a.fixture:
                eval_start = max(0, len(bars) - a.eval_bars)
            else:
                eval_start = next((i for i, b in enumerate(bars) if b["t"] >= d0), len(bars))
            if len(bars) - eval_start < 60:
                failed["%s/%s" % (venue, coin)] = "평가 구간 봉 부족 (%d)" % (len(bars) - eval_start)
                continue
            rs, cost, hz = evaluate(venue, coin, bars, eval_start, fees[venue])
            markets["%s:%s" % (venue, coin)] = {
                "venue": venue, "coin": coin, "bars": len(bars), "eval_bars": len(bars) - eval_start,
                "first_bar_utc": datetime.fromtimestamp(bars[0]["t"], timezone.utc).strftime("%Y-%m-%d %H:%M"),
                "last_bar_utc": datetime.fromtimestamp(bars[-1]["t"], timezone.utc).strftime("%Y-%m-%d %H:%M"),
                "cost": cost, "hurdle": hz}
            rows.extend(rs)

    by = {}
    for tf in TIMEFRAMES:
        for st in STRATEGIES:
            rs = [r for r in rows if r["strategy"] == st["key"] and r["tf"] == "%dm" % tf]
            if not rs:
                continue
            by["%dm:%s" % (tf, st["key"])] = {
                "trades": sum(r["trades"] for r in rs),
                "gross_pct": round(sum(r["gross_pct"] for r in rs), 4),
                "fee_pct": round(sum(r["fee_pct"] for r in rs), 4),
                "net_pct": round(sum(r["net_pct"] for r in rs), 4),
                "rows_total": len(rs),
                "rows_positive": sum(1 for r in rs if r["net_pct"] > 0),
                "rows_robust": sum(1 for r in rs if r["robust"])}
    real = [r for r in rows if r["strategy"] != BASELINE]
    top = sorted(real, key=lambda r: r["net_pct"], reverse=True)[:8]
    pos = sum(1 for r in real if r["net_pct"] > 0)
    rob = sum(1 for r in real if r["robust"])
    hist_days = len(load_history(a.history)) + (0 if a.dry_run else 1)

    if not rows:
        verdict = "데이터 없음 — 모든 시장 수집 실패"
    else:
        h5 = [m["hurdle"]["h5"]["needed_win_rate_pct"] for m in markets.values()
              if m["hurdle"].get("h5", {}).get("needed_win_rate_pct") is not None]
        h30 = [m["hurdle"]["h30"]["needed_win_rate_pct"] for m in markets.values()
               if m["hurdle"].get("h30", {}).get("needed_win_rate_pct") is not None]
        best_tf = max(TIMEFRAMES, key=lambda tf: sum(by.get("%dm:%s" % (tf, st["key"]), {}).get("net_pct", 0.0)
                                                       for st in STRATEGIES if st["key"] != BASELINE))
        verdict = ("%d개 조합 중 순수익 플러스 %d개, 견고 %d개. 본전 승률: 5분 보유 %.0f~%.0f%%, 30분 보유 %.0f~%.0f%%. "
                   "규칙 합산이 가장 나은 시간 단위 %dm. 기록 %d일차 — %d일 이상 모여야 후보 판단"
                   % (len(real), pos, rob, min(h5) if h5 else 0, max(h5) if h5 else 0,
                      min(h30) if h30 else 0, max(h30) if h30 else 0, best_tf, hist_days, MIN_DAYS))

    out = {
        "schema": "carrygate-scalp/2",
        "date": now.strftime("%Y-%m-%d"),
        "generated_at_kst": now.strftime("%Y-%m-%d %H:%M:%S"),
        "eval_day_utc": eval_day,
        "mode": mode,
        "method": {
            "bars": "1분봉을 받아 1·5·15분봉으로 묶어 같은 규칙을 적용. 신호는 봉 종가에서 계산",
            "market": "시장가 규칙: 다음 봉 시가 체결. 체결 1회 비용 = 시장가 수수료 + 호가 %.0f틱의 절반(데이터에서 추정)" % SPREAD_TICKS,
            "limit": "지정가 규칙: 신호 봉 종가에 주문, 다음 봉 저가(매수)/고가(매도)가 주문가를 지나쳐야 체결. "
                     "%d봉 미체결 시 진입은 취소, 청산은 시장가. 체결 1회 비용 = 지정가 수수료" % LIMIT_TTL,
            "eval_bars": a.eval_bars, "warmup_min": WARMUP_MIN, "timeframes": ["%dm" % tf for tf in TIMEFRAMES],
            "robust": "매매 10건 이상, 순수익 플러스, 앞·뒤 반나절 모두 플러스, 같은 시장·봉의 기준선(무작위 5봉 보유)보다 나음",
            "strategies": [{"key": s["key"], "name": s["name"], "exec": s["exec"], "max_hold_bars": s["max_hold"], "desc": s["desc"]}
                           for s in STRATEGIES],
        },
        "fees": {k: {"taker_pct": v["taker_pct"], "maker_pct": v["maker_pct"], "kind": v["kind"],
                     "short_ok": v["short_ok"], "fee_source": v["fee_source"]}
                 for k, v in fees.items() if k in data},
        "markets": markets,
        "rows": rows,
        "failed": failed,
        "summary": {"rows_total": len(real), "rows_positive": pos, "rows_robust": rob,
                    "markets": len(markets), "by_tf_strategy": by, "top": top,
                    "history_days": hist_days, "verdict": verdict},
    }
    print_day(out)
    if failed:
        print()
        print("수집 실패 %d건:" % len(failed))
        for k, v in failed.items():
            print("  - %s: %s" % (k, v))
    if a.dry_run:
        print("\n(dry-run: 저장하지 않음)")
        return 0
    with open(a.out, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    write_history(out, a.history)
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
