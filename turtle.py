# -*- coding: utf-8 -*-
"""
CARRYGATE — 터틀(리처드 데니스) 추세추종 규칙 (중기)

규칙 3개를 코드에 고정하고, 매일 일봉으로 (1) 과거 성적을 다시 계산하고 (2) 오늘의 신호 상태를 기록한다.

  ① 진입: 직전 20일봉 최고가를 넘으면 롱 (선물은 직전 20일 최저가를 깨면 숏)
  ② 청산: 직전 10일봉 최저가를 깨면 전량 (숏은 10일 최고가). 손절은 진입가 − 2N
  ③ 수량: 계좌 × 1% ÷ N   (N = 최근 20일 평균 변동폭, 와일더 ATR)

규칙 고정일(RULE_FIXED) 이후 생긴 매매는 forward(순방향) 로 따로 집계한다 — 결과를 모르고 미리 정한 규칙으로
낸 성적이므로 그것만이 진짜 시험이다. 그 전 구간은 사후 검증(과거 성적)이다.

시장: 업비트 현물(롱만, KRW) 6개 + OKX 무기한선물(롱·숏, USDT) 6개 = 12개.
체결 가정: 돌파 가격에 스톱 주문이 걸려 있다고 보고 max(시가, 돌파가) 에 체결, 슬리피지 0.05% 와 수수료를 뺀다.
주문 기능 없음. 읽기 전용 공개 API 만 쓴다.

사용법
  python turtle.py                      # 수집·계산·저장 (turtle.json, turtle_history.jsonl)
  python turtle.py --dry-run            # 저장 안 함
  python turtle.py --fixture tests/fixtures/turtle --dry-run   # 저장된 일봉 CSV 로 (인터넷 불필요)
  python turtle.py --report             # 기록 누적 보고

변형(VARIANTS)은 기본 규칙과 나란히 계산해 기록만 한다. ch_22_3 은 Chandelier Exit(청산선 = 진입 후 최고가 − 3×ATR22, 래칫).
"""
import os, sys, json, math, csv, time, argparse
from datetime import datetime, timezone, timedelta

from scalp import http_json, f, KST, COINS

# ----------------------------------------------------------------- 고정 규칙 (사후 조정 금지)
ENTRY_N = 20            # 진입 돌파 구간(일)
EXIT_N = 10             # 청산 돌파 구간(일)
ATR_N = 20              # N 계산 구간(일)
STOP_MULT = 2.0         # 손절 = 진입가 ∓ N × 2
RISK_PCT = 1.0          # 한 번에 계좌의 1% 만 흔들리게
MAX_NOTIONAL_PCT = 100.0  # 포지션 명목가 합계 ≤ 계좌 100% (레버리지 1배, 현물 기준)
SLIPPAGE_PCT = 0.05     # 돌파가 체결 시 불리하게 밀리는 가정
RULE_FIXED = "2026-10-02"   # 이 날 이후 진입한 매매만 순방향 시험
# 변형: 기본(20/10)과 나란히 기록만 한다. 손절·수량은 같다.
#  - s2_55_20: 원래 터틀 System 2 (55일 돌파 진입 / 20일 이탈 청산)
#  - ch_22_3 : Chandelier Exit — 10일 최저가 대신 "진입 후 최고가 − 3×ATR(22일)" 를 청산선으로 쓴다 (숏은 최저가 + 3×ATR).
#              청산선은 올라가기만 한다(래칫). 2N 손절은 그대로 바닥으로 둔다. 진입은 기본과 같은 20일 돌파.
VARIANTS = {
    "s2_55_20": {"entry_n": 55, "exit_n": 20, "label": "System 2 (55일 돌파 / 20일 이탈)"},
    "ch_22_3": {"entry_n": 20, "exit_n": 10, "chandelier": {"atr_n": 22, "mult": 3.0},
                "label": "Chandelier Exit (최고가 − 3×ATR22)"},
}
DAYS = 1100             # 받아올 일봉 수 (약 3년)
MIN_BARS = ATR_N + ENTRY_N + 30

VENUES = {
    "upbit": {"kind": "spot", "quote": "KRW",  "fee_pct": 0.05, "short_ok": False, "equity": 10_000_000.0,
              "fee_source": "업비트 기본 수수료 0.05%"},
    "okx":   {"kind": "perp", "quote": "USDT", "fee_pct": 0.05, "short_ok": True,  "equity": 10_000.0,
              "fee_source": "OKX 선물 테이커 0.05% 일반 등급"},
}


def r4(x):
    return None if x is None else round(x, 4)


def rp(x):
    """가격·N 은 소수 8자리 (DOGE 처럼 1원 미만 코인도 검산이 맞게)."""
    return None if x is None else round(x, 8)


def px(x):
    """가격을 사람이 읽기 쉽게: 1,000 이상은 정수 콤마, 그 아래는 유효숫자 4자리."""
    if x is None:
        return "-"
    return "{:,.0f}".format(x) if abs(x) >= 1000 else "%.4g" % x


# ----------------------------------------------------------------- 일봉 수집

def _today_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def upbit_daily(coin, days=DAYS):
    """업비트 일봉(09:00 KST = 00:00 UTC 시작). 200개씩 과거로 넘기며 받는다. 오늘 미완성 봉은 뺀다."""
    out, to = {}, None
    while len(out) < days:
        url = "https://api.upbit.com/v1/candles/days?market=KRW-%s&count=200" % coin
        if to:
            url += "&to=%s" % to
        rows = http_json(url)
        if not rows:
            break
        for r in rows:
            d = r["candle_date_time_utc"][:10]
            out[d] = {"date": d, "o": f(r["opening_price"]), "h": f(r["high_price"]),
                      "l": f(r["low_price"]), "c": f(r["trade_price"])}
        to = rows[-1]["candle_date_time_utc"] + "Z"      # 가장 오래된 봉의 시작 시각 → 그 이전을 받는다
        if len(rows) < 200:
            break
        time.sleep(0.25)
    today = _today_utc()
    bars = [out[d] for d in sorted(out) if d != today]
    return bars[-days:]


def okx_daily(coin, days=DAYS):
    """OKX 무기한선물 일봉. bar=1Dutc 로 받아야 00:00 UTC 시작(업비트 09:00 KST 와 같은 날짜)이 된다 —
    그냥 1D 는 홍콩시간(UTC+8) 기준이라 하루가 어긋난다(Actions 실행 #26 에서 확인). 최신 300개 + history-candles 100개씩.
    confirm=0(미완성) 은 뺀다."""
    inst = "%s-USDT-SWAP" % coin
    out = {}

    def take(rows):
        for r in rows:
            if len(r) > 8 and str(r[8]) == "0":
                continue
            t = int(r[0]) // 1000
            d = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")
            out[d] = {"date": d, "o": f(r[1]), "h": f(r[2]), "l": f(r[3]), "c": f(r[4]), "_ts": int(r[0])}

    rows = http_json("https://www.okx.com/api/v5/market/candles?instId=%s&bar=1Dutc&limit=300" % inst).get("data") or []
    take(rows)
    while rows and len(out) < days:
        oldest = min(int(r[0]) for r in rows)
        time.sleep(0.25)
        rows = http_json("https://www.okx.com/api/v5/market/history-candles?instId=%s&bar=1Dutc&limit=100&after=%d"
                         % (inst, oldest)).get("data") or []
        take(rows)
    bars = [out[d] for d in sorted(out)]
    for b in bars:
        b.pop("_ts", None)
    return bars[-days:]


def load_fixture(path, venue, coin):
    fn = os.path.join(path, "%s_%s.csv" % (venue, coin))
    with open(fn, encoding="utf-8") as fh:
        return [{"date": r["date"], "o": float(r["open"]), "h": float(r["high"]),
                 "l": float(r["low"]), "c": float(r["close"])} for r in csv.DictReader(fh)]


# ----------------------------------------------------------------- 지표

def wilder_atr(bars, n=ATR_N):
    """N 값 목록 (bars 와 같은 길이, 앞쪽 n 개는 None). 처음 n 개 TR 단순평균 → 이후 (19·N + TR)/20."""
    out = [None] * len(bars)
    trs = []
    for i, b in enumerate(bars):
        if i == 0:
            tr = b["h"] - b["l"]
        else:
            pc = bars[i - 1]["c"]
            tr = max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc))
        trs.append(tr)
        if i == n - 1:
            out[i] = sum(trs) / n
        elif i >= n:
            out[i] = (out[i - 1] * (n - 1) + tr) / n
    return out


def channel(bars, i, n, key, fn):
    """직전 n 개 봉(i 제외)의 key 의 max/min. 부족하면 None."""
    if i < n:
        return None
    return fn(b[key] for b in bars[i - n:i])


# ----------------------------------------------------------------- 백테스트 (여러 시장이 한 계좌를 나눠 쓴다)

def run(markets, short_ok, fee_pct, equity0, slip_pct=SLIPPAGE_PCT, max_notional_pct=MAX_NOTIONAL_PCT,
        entry_n=ENTRY_N, exit_n=EXIT_N, chandelier=None):
    """
    markets: {이름: 일봉 목록}. 한 계좌(equity0)를 모든 시장이 같이 쓴다. 시장이 하나면 단일 시장 성적.
    chandelier: None 이면 기본(exit_n 일 채널 이탈). {"atr_n": 22, "mult": 3.0} 이면 채널 대신
                "진입 후 최고가 − mult×ATR(atr_n)" 트레일링 청산선(래칫, 내려오지 않음). 2N 손절은 어느 쪽이든 바닥.
    반환: {"trades": [...], "curve": [(date, equity)], "equity": 마지막 계좌, "state": {이름: 포지션 또는 None}, "skipped": n}
    """
    atr = {m: wilder_atr(b) for m, b in markets.items()}
    catr = {m: wilder_atr(b, chandelier["atr_n"]) for m, b in markets.items()} if chandelier else None
    idx = {m: {b["date"]: i for i, b in enumerate(bars)} for m, bars in markets.items()}
    dates = sorted(set(d for bars in markets.values() for d in (b["date"] for b in bars)))
    equity = float(equity0)
    pos, trades, curve, skipped = {m: None for m in markets}, [], [], 0
    fee = fee_pct / 100.0
    slip = slip_pct / 100.0

    for d in dates:
        exited_today = set()
        # 1) 청산 (보유 중인 시장)
        for m, bars in markets.items():
            p = pos[m]
            i = idx[m].get(d)
            if p is None or i is None:
                continue
            b = bars[i]
            if p["dir"] > 0:
                trail = p["trail"] if chandelier else channel(bars, i, exit_n, "l", min)
                lvl = max(p["stop"], trail)
                hit = b["l"] <= lvl
                fill = min(b["o"], lvl) * (1 - slip) if hit else None
            else:
                trail = p["trail"] if chandelier else channel(bars, i, exit_n, "h", max)
                lvl = min(p["stop"], trail)
                hit = b["h"] >= lvl
                fill = max(b["o"], lvl) * (1 + slip) if hit else None
            if not hit:
                continue
            gross = (fill - p["entry"]) * p["units"] * p["dir"]
            fees = (p["entry"] + fill) * p["units"] * fee
            pnl = gross - fees
            eq_before = equity
            equity += pnl
            trades.append({
                "market": m, "dir": "long" if p["dir"] > 0 else "short",
                "entry_date": p["entry_date"], "exit_date": d, "days": p["days"],
                "entry": rp(p["entry"]), "exit": rp(fill), "units": p["units"], "n_at_entry": rp(p["n"]),
                "stop": rp(p["stop"]), "reason": "stop" if lvl == p["stop"] else ("chandelier" if chandelier else "channel"),
                "pnl": r4(pnl), "fees": r4(fees),
                "mfe_pct": r4((p["best"] / p["entry"] - 1.0) * 100.0 * p["dir"]),        # 보유 중 최대 유리 가격 (반납 계산용)
                "price_ret_pct": r4((fill / p["entry"] - 1.0) * 100.0 * p["dir"]),
                "ret_pct": pnl / eq_before * 100.0,            # 계좌 대비 수익률 (복리 재계산용, 반올림 안 함)
                "r_multiple": r4(pnl / (eq_before * RISK_PCT / 100.0)),
                "forward": p["entry_date"] >= RULE_FIXED,
            })
            pos[m] = None
            exited_today.add(m)

        # 2) 진입 (비어 있는 시장) — 같은 날 청산한 시장은 다음 날부터
        used = sum(p["units"] * markets[m][idx[m][d]]["c"] for m, p in pos.items() if p and d in idx[m])
        for m, bars in markets.items():
            i = idx[m].get(d)
            if pos[m] is not None or i is None or i < max(entry_n, ATR_N) or atr[m][i - 1] is None:
                continue
            if chandelier and catr[m][i - 1] is None:
                continue
            if m in exited_today:
                continue
            b = bars[i]
            n = atr[m][i - 1]
            hi = channel(bars, i, entry_n, "h", max)
            lo = channel(bars, i, entry_n, "l", min)
            go_long = b["h"] > hi
            go_short = short_ok and b["l"] < lo
            if go_long and go_short:
                skipped += 1          # 하루에 위아래 다 뚫음 — 순서를 알 수 없어 건너뛴다
                continue
            if not (go_long or go_short) or n <= 0:
                continue
            direction = 1 if go_long else -1
            fill = max(b["o"], hi) * (1 + slip) if go_long else min(b["o"], lo) * (1 - slip)
            units = equity * RISK_PCT / 100.0 / n
            cap = max(0.0, equity * max_notional_pct / 100.0 - used)
            capped = False
            if units * fill > cap:
                units, capped = cap / fill, True
            if units <= 0:
                continue
            stop = fill - direction * STOP_MULT * n
            pos[m] = {"dir": direction, "units": units, "entry": fill, "n": n, "stop": stop,
                      "best": fill, "trail": stop,          # best: 진입 후 최고가(숏은 최저가), trail: Chandelier 청산선 (래칫)
                      "entry_date": d, "days": 0, "capped": capped}
            used += units * fill

        # 3) 일별 계좌 가치 (미실현 포함)
        unreal = 0.0
        for m, p in pos.items():
            if p and d in idx[m]:
                i = idx[m][d]
                b = markets[m][i]
                p["days"] += 1
                unreal += (b["c"] - p["entry"]) * p["units"] * p["dir"]
                if p["dir"] > 0:
                    p["best"] = max(p["best"], b["h"])
                    if chandelier and catr[m][i] is not None:
                        p["trail"] = max(p["trail"], p["best"] - chandelier["mult"] * catr[m][i])
                else:
                    p["best"] = min(p["best"], b["l"])
                    if chandelier and catr[m][i] is not None:
                        p["trail"] = min(p["trail"], p["best"] + chandelier["mult"] * catr[m][i])
        curve.append((d, equity + unreal))

    return {"trades": trades, "curve": curve, "equity": equity, "state": pos, "skipped": skipped}


def stats(res, equity0, bars_by_market=None):
    tr = res["trades"]
    curve = res["curve"]
    if not curve:
        return {"trades": 0}
    peak, mdd = -1e18, 0.0
    for _, e in curve:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1.0)
    days = max(1, (datetime.strptime(curve[-1][0], "%Y-%m-%d") - datetime.strptime(curve[0][0], "%Y-%m-%d")).days)
    final = curve[-1][1]
    wins = [t for t in tr if t["pnl"] > 0]
    gp = sum(t["pnl"] for t in wins)
    gl = -sum(t["pnl"] for t in tr if t["pnl"] <= 0)
    by_year, year_start = {}, {}
    for d, e in curve:
        y = d[:4]
        if y not in year_start:
            year_start[y] = prev_e if by_year else equity0      # 연초 = 전년 마지막 값
        by_year[y] = r4((e / year_start[y] - 1.0) * 100.0)
        prev_e = e
    fwd = [t for t in tr if t["forward"]]
    out = {
        "period": "%s ~ %s" % (curve[0][0], curve[-1][0]),
        "days": days,
        "trades": len(tr),
        "wins": len(wins),
        "win_rate_pct": r4(len(wins) / len(tr) * 100.0) if tr else None,
        "net_pct": r4((final / equity0 - 1.0) * 100.0),
        "realized_net_pct": r4((res["equity"] / equity0 - 1.0) * 100.0),
        "cagr_pct": r4(((final / equity0) ** (365.0 / days) - 1.0) * 100.0) if final > 0 else None,
        "max_drawdown_pct": r4(mdd * 100.0),
        "profit_factor": r4(gp / gl) if gl > 0 else None,
        "avg_r": r4(sum(t["r_multiple"] for t in tr) / len(tr)) if tr else None,
        "avg_hold_days": r4(sum(t["days"] for t in tr) / len(tr)) if tr else None,
        "stops": sum(1 for t in tr if t["reason"] == "stop"),
        "avg_mfe_pct": r4(sum(t["mfe_pct"] for t in tr) / len(tr)) if tr else None,            # 보유 중 평균 최대 유리폭
        "avg_giveback_pct": r4(sum(t["mfe_pct"] - t["price_ret_pct"] for t in tr) / len(tr)) if tr else None,   # 최고점에서 청산가까지 평균 반납
        "skipped_ambiguous_days": res["skipped"],
        "by_year": by_year,
        "forward": {"trades": len(fwd), "wins": sum(1 for t in fwd if t["pnl"] > 0),
                    "net_pct": r4((math.prod(1 + t["ret_pct"] / 100.0 for t in fwd) - 1.0) * 100.0) if fwd else None},
    }
    if bars_by_market and len(bars_by_market) == 1:
        bars = list(bars_by_market.values())[0]
        start = next((b for b in bars if b["date"] >= curve[0][0]), bars[0])
        out["buy_hold_pct"] = r4((bars[-1]["c"] / start["c"] - 1.0) * 100.0)
    return out


# ----------------------------------------------------------------- 오늘의 신호

def signal(bars, state, short_ok, equity, entry_n=ENTRY_N, exit_n=EXIT_N, chandelier=None):
    """마지막 완성 봉 기준으로 내일 어느 가격에서 무엇을 할지."""
    i = len(bars)              # 다음 봉의 인덱스
    atr = wilder_atr(bars)
    n = atr[-1]
    close = bars[-1]["c"]
    hi20 = channel(bars, i, entry_n, "h", max)
    lo20 = channel(bars, i, entry_n, "l", min)
    lo10 = channel(bars, i, exit_n, "l", min)
    hi10 = channel(bars, i, exit_n, "h", max)
    out = {"last_bar": bars[-1]["date"], "close": rp(close), "n": rp(n), "n_pct": r4(n / close * 100.0) if n else None,
           "high20": rp(hi20), "low20": rp(lo20), "low10": rp(lo10), "high10": rp(hi10)}
    if state:
        d = state["dir"]
        if chandelier:
            exit_lvl = max(state["stop"], state["trail"]) if d > 0 else min(state["stop"], state["trail"])
            out["chandelier"] = rp(state["trail"])
            out["best_since_entry"] = rp(state["best"])
        else:
            exit_lvl = max(state["stop"], lo10) if d > 0 else min(state["stop"], hi10)
        out.update({
            "position": "long" if d > 0 else "short",
            "entry": rp(state["entry"]), "entry_date": state["entry_date"], "units": state["units"],
            "stop": rp(state["stop"]), "exit_level": rp(exit_lvl),
            "exit_distance_pct": r4((exit_lvl / close - 1.0) * 100.0),
            "unrealized_pct": r4((close / state["entry"] - 1.0) * 100.0 * d),
            "action": "보유 — %s 이탈 시 청산" % px(exit_lvl),
        })
    else:
        units = equity * RISK_PCT / 100.0 / n if n else None
        out.update({
            "position": "flat",
            "entry_long_at": rp(hi20),
            "long_distance_pct": r4((hi20 / close - 1.0) * 100.0) if hi20 else None,
            "units_if_long": units,
            "notional_pct_if_long": r4(min(100.0, units * hi20 / equity * 100.0)) if units else None,
            "stop_if_long": rp(hi20 - STOP_MULT * n) if n else None,
        })
        if short_ok:
            out.update({"entry_short_at": rp(lo20),
                        "short_distance_pct": r4((lo20 / close - 1.0) * 100.0) if lo20 else None,
                        "stop_if_short": rp(lo20 + STOP_MULT * n) if n else None})
        out["action"] = "대기 — %s 돌파 시 롱" % px(hi20) + (" / %s 이탈 시 숏" % px(lo20) if short_ok else "")
    return out


# ----------------------------------------------------------------- 실행

def evaluate(bars_by, cfg, entry_n=ENTRY_N, exit_n=EXIT_N, chandelier=None):
    """한 거래소의 시장들에 규칙(entry_n/exit_n[/chandelier])을 적용해 시장별 성적·신호와 한 계좌 포트폴리오 성적을 만든다."""
    markets = {}
    for coin, bars in bars_by.items():
        res = run({coin: bars}, cfg["short_ok"], cfg["fee_pct"], cfg["equity"], entry_n=entry_n, exit_n=exit_n, chandelier=chandelier)
        markets[coin] = {
            "bars": len(bars), "stats": stats(res, cfg["equity"], {coin: bars}),
            "returns_pct": [t["ret_pct"] for t in res["trades"]],           # 검산용: 곱하면 realized_net 이 나와야 한다
            "forward_trades": [t for t in res["trades"] if t["forward"]],
            "recent_trades": res["trades"][-5:],
            "signal": signal(bars, res["state"][coin], cfg["short_ok"], cfg["equity"], entry_n, exit_n, chandelier),
        }
    pres = run(bars_by, cfg["short_ok"], cfg["fee_pct"], cfg["equity"], entry_n=entry_n, exit_n=exit_n, chandelier=chandelier)
    portfolio = {
        "markets": sorted(bars_by), "stats": stats(pres, cfg["equity"]),
        "returns_pct": [t["ret_pct"] for t in pres["trades"]],
        "open_positions": {m: {"dir": "long" if p["dir"] > 0 else "short", "entry": rp(p["entry"]), "entry_date": p["entry_date"],
                               "stop": rp(p["stop"]), "units": p["units"], "capped": p["capped"],
                               "trail": rp(p["trail"]) if chandelier else None, "best": rp(p["best"])}
                           for m, p in pres["state"].items() if p},
        "forward_trades": [t for t in pres["trades"] if t["forward"]],
        "equity_now": r4(pres["curve"][-1][1]) if pres["curve"] else None,
    }
    return {"markets": markets, "portfolio": portfolio}


def build(fixture=None):
    now = datetime.now(KST)
    out = {
        "schema": "carrygate-turtle/1",
        "date": now.strftime("%Y-%m-%d"),
        "generated_at_kst": now.strftime("%Y-%m-%d %H:%M:%S"),
        "rules": {"entry_breakout_days": ENTRY_N, "exit_breakout_days": EXIT_N, "atr_days": ATR_N,
                  "variants": {k: {"entry_breakout_days": v["entry_n"], "exit_breakout_days": v["exit_n"], "label": v["label"],
                                   "chandelier": v.get("chandelier")} for k, v in VARIANTS.items()},
                  "stop_n_mult": STOP_MULT, "risk_pct_per_trade": RISK_PCT, "max_notional_pct": MAX_NOTIONAL_PCT,
                  "slippage_pct": SLIPPAGE_PCT, "rule_fixed": RULE_FIXED,
                  "fill": "돌파가에 스톱 주문 → max(시가, 돌파가) 체결. 청산도 같은 방식"},
        "source": "fixture %s" % fixture if fixture else "upbit /v1/candles/days (KRW 현물) + okx /v5/market/candles bar=1Dutc (USDT 무기한)",
        "venues": {},
        "errors": {},
    }
    for v, cfg in VENUES.items():
        bars_by = {}
        for coin in COINS:
            try:
                if fixture:
                    bars = load_fixture(fixture, v, coin)
                elif v == "upbit":
                    bars = upbit_daily(coin)
                else:
                    bars = okx_daily(coin)
                if len(bars) < MIN_BARS:
                    raise RuntimeError("일봉 부족 %d" % len(bars))
                bars_by[coin] = bars
            except Exception as e:
                out["errors"]["%s/%s" % (v, coin)] = str(e)[:300]
            if not fixture:
                time.sleep(0.3)
        if not bars_by:
            continue
        vd = {"kind": cfg["kind"], "quote": cfg["quote"], "fee_pct": cfg["fee_pct"], "fee_source": cfg["fee_source"],
              "short_ok": cfg["short_ok"], "equity0": cfg["equity"]}
        vd.update(evaluate(bars_by, cfg))
        vd["variants"] = {}
        for name, var in VARIANTS.items():
            ev = evaluate(bars_by, cfg, var["entry_n"], var["exit_n"], var.get("chandelier"))
            ev["label"], ev["entry_n"], ev["exit_n"], ev["chandelier"] = var["label"], var["entry_n"], var["exit_n"], var.get("chandelier")
            vd["variants"][name] = ev
        out["venues"][v] = vd

    # 요약
    summ = {"ok": not out["errors"], "markets": sum(len(v["markets"]) for v in out["venues"].values())}
    for v, vd in out["venues"].items():
        p = vd["portfolio"]["stats"]
        summ[v] = {"portfolio_net_pct": p.get("net_pct"), "cagr_pct": p.get("cagr_pct"), "max_drawdown_pct": p.get("max_drawdown_pct"),
                   "trades": p.get("trades"), "win_rate_pct": p.get("win_rate_pct"),
                   "open": {m: s["signal"]["position"] for m, s in vd["markets"].items() if s["signal"]["position"] != "flat"},
                   "nearest_breakout": min(((m, s["signal"].get("long_distance_pct")) for m, s in vd["markets"].items()
                                            if s["signal"]["position"] == "flat" and s["signal"].get("long_distance_pct") is not None),
                                           key=lambda x: x[1], default=None),
                   "forward_trades": p.get("forward", {}).get("trades"),
                   "forward_net_pct": p.get("forward", {}).get("net_pct"),
                   "variants": {k: {"portfolio_net_pct": e["portfolio"]["stats"].get("net_pct"), "cagr_pct": e["portfolio"]["stats"].get("cagr_pct"),
                                    "max_drawdown_pct": e["portfolio"]["stats"].get("max_drawdown_pct"), "trades": e["portfolio"]["stats"].get("trades"),
                                    "forward_trades": e["portfolio"]["stats"].get("forward", {}).get("trades"),
                                    "forward_net_pct": e["portfolio"]["stats"].get("forward", {}).get("net_pct"),
                                    "open": {m: s["signal"]["position"] for m, s in e["markets"].items() if s["signal"]["position"] != "flat"}}
                                for k, e in vd.get("variants", {}).items()}}
    out["summary"] = summ
    return out


def history_line(out):
    line = {"date": out["date"], "summary": out["summary"], "signals": {}, "signals_variants": {}}
    keys = ("position", "close", "n", "high20", "low20", "low10", "stop", "exit_level", "chandelier", "entry", "entry_date", "unrealized_pct", "long_distance_pct")
    for v, vd in out["venues"].items():
        for m, s in vd["markets"].items():
            line["signals"]["%s/%s" % (v, m)] = {k: s["signal"].get(k) for k in keys}
        for name, e in vd.get("variants", {}).items():
            for m, s in e["markets"].items():
                line["signals_variants"]["%s/%s/%s" % (name, v, m)] = {k: s["signal"].get(k) for k in keys}
    return line


def print_summary(out):
    print("=== 터틀 규칙 (20일 돌파 / 10일 이탈 / 2N 손절 / 1%÷N) ===")
    for v, vd in out["venues"].items():
        p = vd["portfolio"]["stats"]
        print("[%s] 포트폴리오 %s: 순 %+.1f%%  CAGR %+.1f%%  최대낙폭 %.1f%%  매매 %d  승률 %.0f%%  순방향 %d건" % (
            v, p["period"], p["net_pct"], p["cagr_pct"] or 0, p["max_drawdown_pct"], p["trades"], p["win_rate_pct"] or 0,
            p["forward"]["trades"]))
        print("  연도별: %s   평균 최대유리폭 %s%%  평균 반납 %s%%" % (json.dumps(p["by_year"]), p["avg_mfe_pct"], p["avg_giveback_pct"]))
        for m, s in vd["markets"].items():
            st, sg = s["stats"], s["signal"]
            print("  %-5s 순 %+7.1f%% (보유 %+7.1f%%)  낙폭 %6.1f%%  매매 %3d  승률 %3.0f%%  PF %s  평균R %s | %s" % (
                m, st["net_pct"], st.get("buy_hold_pct") or 0, st["max_drawdown_pct"], st["trades"], st["win_rate_pct"] or 0,
                st["profit_factor"], st["avg_r"], sg["action"]))
        for name, e in vd.get("variants", {}).items():
            q = e["portfolio"]["stats"]
            print("  변형 %s: 순 %+.1f%%  CAGR %+.1f%%  낙폭 %.1f%%  매매 %d  승률 %.0f%%  평균반납 %s%%  연도별 %s  보유 %s" % (
                e["label"], q["net_pct"], q["cagr_pct"] or 0, q["max_drawdown_pct"], q["trades"], q["win_rate_pct"] or 0,
                q["avg_giveback_pct"], json.dumps(q["by_year"]), [m for m, s in e["markets"].items() if s["signal"]["position"] != "flat"]))
    if out["errors"]:
        print("ERRORS:", json.dumps(out["errors"], ensure_ascii=False))


def report(path="turtle_history.jsonl"):
    rows = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
    if not rows:
        print("터틀 기록 없음")
        return
    print("=== 터틀 신호 추이 (%d일, %s ~ %s) ===" % (len(rows), rows[0]["date"], rows[-1]["date"]))
    prev = {}
    for r in rows:
        changes = []
        for k, s in (r.get("signals") or {}).items():
            if prev.get(k) != s.get("position"):
                changes.append("%s %s→%s" % (k, prev.get(k, "-"), s.get("position")))
            prev[k] = s.get("position")
        sm = r.get("summary", {})
        print("%s  업비트 순방향 %s건 %s%%  OKX 순방향 %s건 %s%%  %s" % (
            r["date"], (sm.get("upbit") or {}).get("forward_trades"), (sm.get("upbit") or {}).get("forward_net_pct"),
            (sm.get("okx") or {}).get("forward_trades"), (sm.get("okx") or {}).get("forward_net_pct"),
            ("변화: " + ", ".join(changes)) if changes else ""))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fixture", default=None)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)
    if a.report:
        report()
        return 0
    out = build(a.fixture)
    print_summary(out)
    if not out["venues"]:
        print("시장 데이터 전부 실패", file=sys.stderr)
        return 1
    if not a.dry_run:
        with open("turtle.json", "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1)
        # 같은 날 두 번 돌면(수동 실행 등) 그날 줄을 바꿔 끼운다 — 날짜 중복이면 검산(selfcheck)이 실패한다
        line = json.dumps(history_line(out), ensure_ascii=False)
        try:
            with open("turtle_history.jsonl", encoding="utf-8") as fh:
                rows = [l for l in fh.read().splitlines() if l.strip()]
        except IOError:
            rows = []
        keep = []
        for l in rows:
            try:
                if json.loads(l).get("date") == out.get("date"):
                    continue
            except ValueError:
                pass
            keep.append(l)
        keep.append(line)
        with open("turtle_history.jsonl", "w", encoding="utf-8") as fh:
            fh.write("\n".join(keep) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
