# -*- coding: utf-8 -*-
"""
CARRYGATE — 추세 전환 매매법 (4시간봉, "저점이 안 깨지고 직전 고점을 거래량 실어 뚫은 뒤 눌림에 진입")

사용자가 공유한 카드(2026-10-05)의 5문장을 기계가 매일 똑같이 판정할 수 있는 규칙으로 고정했다. 보조지표 없음.

  1. 4시간봉.
  2. 하락추세인지 본다            → 직전 저점(L0) 보다 낮은 저점(L1 = 바닥)이 있고, 바닥 앞 구간의 고점이 그 앞 구간 고점보다 낮다(고점 낮아짐).
  3. 마지막 저점을 안 깨고 올라오는지 → 바닥(L1) 뒤에 생긴 새 저점(L2) 이 L1 보다 낮지 않다 (쌍바닥·역헤드앤숄더·저점 높이기 공통).
  4. 직전 고점을 거래량 터지면서 뚫는지 → 바닥과 L2 사이 최고가(목선) 위에서 종가 마감 + 그 봉 거래량 ≥ 직전 20봉 평균 × 2.
  5. 뚫고 한 번 눌렸을 때 들어간다   → 돌파 뒤 12봉(2일) 안에 저가가 목선까지 내려오면 목선에 지정가 체결. 눌림이 없으면 안 들어간다.
  손절: 쌍바닥 저점(L2) 아래로 저가가 내려가면 끝 ("시그널이 틀렸다는 뜻").
  청산(카드에 없음, 같은 논리의 연장): 진입 뒤 확정된 저점(피벗 저점)이 생길 때마다 손절선을 그 저점으로 올린다(내리지 않음).
         "저점이 깨지면 추세 끝" 이므로 저점이 깨질 때 나온다. 목표가 없다.
  수량: 계좌 1% ÷ (진입가 − 손절가). 명목가 합계 ≤ 계좌 100%.

규칙 고정일(RULE_FIXED) 이후 진입한 매매만 forward(순방향)로 따로 집계한다. 변형(VARIANTS)은 나란히 기록만 한다.
시장: 업비트 현물 6개(KRW) + OKX 무기한선물 6개(USDT), 모두 롱만(카드가 롱 매매법). 주문 기능 없음, 읽기 전용 공개 API.

사용법
  python reversal.py                                  # 수집·계산·저장 (reversal.json, reversal_history.jsonl)
  python reversal.py --dry-run                        # 저장 안 함
  python reversal.py --fixture tests/fixtures/reversal --dry-run   # 저장된 4시간봉 CSV 로 (인터넷 불필요)
  python reversal.py --save-fixture tests/fixtures/reversal        # 거래소에서 받은 봉을 CSV 로 저장 (Actions 에서)
  python reversal.py --report                         # 기록 누적 보고
"""
import os, sys, json, math, csv, time, argparse
from datetime import datetime, timezone, timedelta

from scalp import http_json, f, KST, COINS

# ----------------------------------------------------------------- 고정 규칙 (사후 조정 금지)
BAR_MIN = 240               # 4시간봉
PIVOT_K = 3                 # 피벗 저점/고점: 좌우 3봉보다 낮/높. 오른쪽 3봉이 닫혀야 확정된다
LOOKBACK = 120              # 하락추세 구조를 찾는 구간(봉) = 20일
VOL_N = 20                  # 거래량 평균 구간(봉)
VOL_MULT = 2.0              # 돌파 봉 거래량 ≥ 직전 20봉 평균 × 2  ("2배 이상 터져야 진짜 손바뀜")
ATR_N = 20                  # 구조 최소 높이 기준용 ATR 구간(봉)
MIN_HEIGHT_ATR = 1.0        # 목선 − 저점 ≥ ATR × 1 (너무 작은 구조 제외)
BREAK_WAIT = 30             # 저점 확정 뒤 이 봉 수(5일) 안에 목선을 뚫어야 한다
PULLBACK_WAIT = 12          # 돌파 뒤 이 봉 수(2일) 안에 목선까지 눌려야 진입. 아니면 포기
RISK_PCT = 1.0              # 한 번에 계좌의 1% 만 흔들리게
MAX_NOTIONAL_PCT = 100.0    # 포지션 명목가 합계 ≤ 계좌 100% (레버리지 1배)
SLIPPAGE_PCT = 0.05         # 손절(스톱) 체결 시 불리하게 밀리는 가정. 진입은 지정가라 0
RULE_FIXED = "2026-10-05"   # 이 날 이후 진입한 매매만 순방향 시험
# 변형: 기본과 나란히 기록만 한다. 카드의 두 주장("거래량 없는 돌파는 가짜", "눌림을 기다려라")을 각각 끈 것.
VARIANTS = {
    "no_vol":      {"vol_mult": 0.0, "label": "거래량 조건 없음 (돌파만)"},
    "break_entry": {"pullback": False, "label": "눌림 안 기다리고 돌파 봉 종가에 진입"},
}
BARS = 6600                 # 받아올 4시간봉 수 (약 3년)
MIN_BARS = LOOKBACK + VOL_N + 50

VENUES = {
    "upbit": {"kind": "spot", "quote": "KRW",  "fee_pct": 0.05, "equity": 10_000_000.0,
              "fee_source": "업비트 기본 수수료 0.05%"},
    "okx":   {"kind": "perp", "quote": "USDT", "fee_pct": 0.05, "equity": 10_000.0,
              "fee_source": "OKX 선물 테이커 0.05% 일반 등급 (지정가도 보수적으로 같은 값)"},
}


def r4(x):
    return None if x is None else round(x, 4)


def rp(x):
    return None if x is None else round(x, 8)


def px(x):
    if x is None:
        return "-"
    return "{:,.0f}".format(x) if abs(x) >= 1000 else "%.4g" % x


# ----------------------------------------------------------------- 4시간봉 수집

def _bar_open_cutoff():
    """지금 진행 중인(미완성) 봉의 시작 시각(unix 초). 이 시각 이상의 봉은 뺀다."""
    now = int(datetime.now(timezone.utc).timestamp())
    return now - now % (BAR_MIN * 60)


def _mk(t, o, h, l, c, v):
    return {"t": int(t), "date": datetime.fromtimestamp(int(t), timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "o": o, "h": h, "l": l, "c": c, "v": v}


def upbit_4h(coin, bars=BARS):
    """업비트 240분봉 (UTC 00:00 기준 정렬). 200개씩 과거로 넘기며 받는다. 미완성 봉은 뺀다."""
    out, to = {}, None
    while len(out) < bars + 2:
        url = "https://api.upbit.com/v1/candles/minutes/%d?market=KRW-%s&count=200" % (BAR_MIN, coin)
        if to:
            url += "&to=%s" % to
        rows = http_json(url)
        if not rows:
            break
        for r in rows:
            # timestamp 필드는 마지막 체결 시각이라 쓰지 않는다. 봉 시작 시각(UTC)을 쓴다.
            t = int(datetime.strptime(r["candle_date_time_utc"], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
            out[t] = _mk(t, f(r["opening_price"]), f(r["high_price"]), f(r["low_price"]), f(r["trade_price"]),
                         f(r["candle_acc_trade_volume"]))
        to = rows[-1]["candle_date_time_utc"] + "Z"
        if len(rows) < 200:
            break
        time.sleep(0.25)
    cut = _bar_open_cutoff()
    res = [out[t] for t in sorted(out) if t < cut]
    return res[-bars:]


def okx_4h(coin, bars=BARS):
    """OKX 무기한선물 4H 봉 (4H 는 UTC 정렬 — 1D 와 달리 홍콩시간 문제 없음). 최신 300 + history-candles 100개씩. confirm=0 제외.
    거래량은 r[6](volCcy, 코인 수량) 을 쓴다 — r[5] 는 계약 수."""
    inst = "%s-USDT-SWAP" % coin
    out = {}

    def take(rows):
        for r in rows:
            if len(r) > 8 and str(r[8]) == "0":
                continue
            t = int(r[0]) // 1000
            out[t] = _mk(t, f(r[1]), f(r[2]), f(r[3]), f(r[4]), f(r[6]) if len(r) > 6 else f(r[5]))

    rows = http_json("https://www.okx.com/api/v5/market/candles?instId=%s&bar=4H&limit=300" % inst).get("data") or []
    take(rows)
    while rows and len(out) < bars + 2:
        oldest = min(int(r[0]) for r in rows)
        time.sleep(0.25)
        rows = http_json("https://www.okx.com/api/v5/market/history-candles?instId=%s&bar=4H&limit=100&after=%d"
                         % (inst, oldest)).get("data") or []
        take(rows)
    cut = _bar_open_cutoff()
    res = [out[t] for t in sorted(out) if t < cut]
    return res[-bars:]


def fixture_path(path, venue, coin):
    return os.path.join(path, "%s_%s_4h.csv" % (venue, coin))


def load_fixture(path, venue, coin):
    with open(fixture_path(path, venue, coin), encoding="utf-8") as fh:
        return [_mk(r["t"], float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r["volume"]))
                for r in csv.DictReader(fh)]


def save_fixture(path, venue, coin, bars):
    os.makedirs(path, exist_ok=True)
    with open(fixture_path(path, venue, coin), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["t", "date", "open", "high", "low", "close", "volume"])
        for b in bars:
            w.writerow([b["t"], b["date"], b["o"], b["h"], b["l"], b["c"], b["v"]])


# ----------------------------------------------------------------- 지표

def wilder_atr(bars, n=ATR_N):
    out = [None] * len(bars)
    trs = []
    for i, b in enumerate(bars):
        tr = b["h"] - b["l"] if i == 0 else max(b["h"] - b["l"], abs(b["h"] - bars[i - 1]["c"]), abs(b["l"] - bars[i - 1]["c"]))
        trs.append(tr)
        if i == n - 1:
            out[i] = sum(trs) / n
        elif i >= n:
            out[i] = (out[i - 1] * (n - 1) + tr) / n
    return out


def vol_avg(bars, i, n=VOL_N):
    """직전 n 봉(i 제외) 평균 거래량. 부족하면 None."""
    if i < n:
        return None
    return sum(b["v"] for b in bars[i - n:i]) / n


def is_pivot_low(bars, j, k=PIVOT_K):
    if j < k or j + k >= len(bars):
        return False
    l = bars[j]["l"]
    return all(l < bars[x]["l"] for x in range(j - k, j + k + 1) if x != j)


def is_pivot_high(bars, j, k=PIVOT_K):
    if j < k or j + k >= len(bars):
        return False
    h = bars[j]["h"]
    return all(h > bars[x]["h"] for x in range(j - k, j + k + 1) if x != j)


# ----------------------------------------------------------------- 구조 판정

def find_setup(bars, lows, j, atr_j, lookback=LOOKBACK, min_height_atr=MIN_HEIGHT_ATR):
    """
    j 번째 봉이 방금 확정된 피벗 저점(Ln)일 때, 그 앞 구조가 "하락추세 → 바닥 → 저점이 안 깨짐" 인지 본다.
    lows: 지금까지 확정된 피벗 저점 인덱스 목록(j 포함, 오름차순). atr_j: j 시점 ATR.
    반환: {"neckline", "stop", "bottom", "prior_low", "lower_high": (앞 고점, 바닥 앞 고점)} 또는 None.
      바닥(L1)  = 구간(j−lookback, j) 안의 피벗 저점 중 가장 낮은 것 (j 제외)
      직전저점(L0) = 바닥 바로 앞 피벗 저점. L0 > L1 이어야 "저점이 깨지며 내려온" 하락추세
      고점 낮아짐  = max(고가, 구간 시작~L0) > max(고가, L0~L1)
      저점 유지    = Ln ≥ L1
      목선        = max(고가, L1~Ln). 목선 − Ln ≥ ATR × min_height_atr
    """
    start = max(0, j - lookback)
    prev = [i for i in lows if start <= i < j]
    if len(prev) < 2:
        return None
    b_idx = min(prev, key=lambda i: bars[i]["l"])           # 바닥 L1
    before = [i for i in prev if i < b_idx]
    if not before:
        return None
    l0 = before[-1]                                           # 직전 저점 L0
    if not (bars[l0]["l"] > bars[b_idx]["l"]):
        return None                                           # 바닥이 "더 낮은 저점" 이 아니다
    if bars[j]["l"] < bars[b_idx]["l"]:
        return None                                           # 저점이 깨졌다 (이 봉이 새 바닥)
    h_before = max(b["h"] for b in bars[start:l0 + 1])
    h_into = max(b["h"] for b in bars[l0:b_idx + 1])
    if not (h_before > h_into):
        return None                                           # 고점이 낮아지지 않았다
    neck = max(b["h"] for b in bars[b_idx:j + 1])
    if atr_j is None or neck - bars[j]["l"] < min_height_atr * atr_j:
        return None
    return {"neckline": neck, "stop": bars[j]["l"], "bottom": b_idx, "prior_low": l0, "low_idx": j,
            "lower_high": (h_before, h_into)}


# ----------------------------------------------------------------- 백테스트 (여러 시장이 한 계좌를 나눠 쓴다)

def run(markets, fee_pct, equity0, slip_pct=SLIPPAGE_PCT, max_notional_pct=MAX_NOTIONAL_PCT,
        vol_mult=VOL_MULT, pullback=True, break_wait=BREAK_WAIT, pullback_wait=PULLBACK_WAIT):
    """
    markets: {이름: 4시간봉 목록(오름차순, 같은 t 축)}. 한 계좌(equity0)를 모든 시장이 같이 쓴다.
    vol_mult: 돌파 봉 거래량 배수 조건 (0 이면 조건 없음). pullback: False 면 돌파 봉 종가에 바로 진입(슬리피지 적용).
    반환: {"trades", "curve", "equity", "state": {이름: 시장 상태}, "counts": {...}}
    """
    atr = {m: wilder_atr(b) for m, b in markets.items()}
    idx = {m: {b["t"]: i for i, b in enumerate(bars)} for m, bars in markets.items()}
    ts = sorted(set(b["t"] for bars in markets.values() for b in bars))
    equity = float(equity0)
    fee, slip = fee_pct / 100.0, slip_pct / 100.0
    trades, curve = [], []
    counts = {"setups": 0, "fake_breaks": 0, "no_pullback": 0, "stopped_before_entry": 0, "expired": 0, "ambiguous": 0}
    st = {m: {"phase": "scan", "lows": [], "pos": None, "setup": None, "breakout": None} for m in markets}

    def enter(m, bars, i, fill, setup, how):
        nonlocal equity
        t = bars[i]["t"]
        used = 0.0
        for x, sx in st.items():
            if sx["pos"] and t in idx[x]:
                used += sx["pos"]["units"] * markets[x][idx[x][t]]["c"]
        stop = setup["stop"]
        if fill <= stop:
            return False
        units = equity * RISK_PCT / 100.0 / (fill - stop)
        cap = max(0.0, equity * max_notional_pct / 100.0 - used)
        capped = False
        if units * fill > cap:
            units, capped = cap / fill, True
        if units <= 0:
            return False
        st[m]["pos"] = {"units": units, "entry": fill, "entry_idx": i, "entry_date": bars[i]["date"], "stop": stop,
                        "stop0": stop, "neckline": setup["neckline"], "pattern_low_date": bars[setup["low_idx"]]["date"],
                        "bottom_date": bars[setup["bottom"]]["date"], "best": fill, "bars": 0, "capped": capped,
                        "raises": 0, "how": how}
        return True

    def close_pos(m, bars, i, fill, reason):
        nonlocal equity
        p = st[m]["pos"]
        gross = (fill - p["entry"]) * p["units"]
        fees = (p["entry"] + fill) * p["units"] * fee
        pnl = gross - fees
        eq_before = equity
        equity += pnl
        trades.append({
            "market": m, "dir": "long", "entry_date": p["entry_date"], "exit_date": bars[i]["date"], "bars": p["bars"],
            "entry": rp(p["entry"]), "exit": rp(fill), "units": p["units"], "neckline": rp(p["neckline"]),
            "pattern_low": rp(p["stop0"]), "pattern_low_date": p["pattern_low_date"], "bottom_date": p["bottom_date"],
            "stop_final": rp(p["stop"]), "stop_raises": p["raises"], "reason": reason, "entry_how": p["how"],
            "pnl": r4(pnl), "fees": r4(fees),
            "mfe_pct": r4((p["best"] / p["entry"] - 1.0) * 100.0),
            "price_ret_pct": r4((fill / p["entry"] - 1.0) * 100.0),
            "ret_pct": pnl / eq_before * 100.0,
            "r_multiple": r4(pnl / (eq_before * RISK_PCT / 100.0)),
            "forward": p["entry_date"][:10] >= RULE_FIXED,
        })
        st[m]["pos"] = None

    def step_setup(m, bars, i):
        """ARMED / BROKEN 단계: 돌파 확인 → 눌림 체결. True 면 이 봉에서 진입했다."""
        s = st[m]
        b = bars[i]
        su = s["setup"]
        if b["l"] < su["stop"]:                                # 저점이 깨졌다 → 신호 무효
            counts["stopped_before_entry"] += 1
            s["phase"], s["setup"], s["breakout"] = "scan", None, None
            return False
        if s["phase"] == "armed":
            if i > su["deadline"]:
                counts["expired"] += 1
                s["phase"], s["setup"] = "scan", None
                return False
            if b["c"] > su["neckline"]:
                va = vol_avg(bars, i)
                if vol_mult > 0 and (va is None or b["v"] < vol_mult * va):
                    if not su.get("fake"):                     # 거래량 없는 돌파: 가짜로 보고 무시 (구조당 한 번 센다)
                        counts["fake_breaks"] += 1
                        su["fake"] = True
                    return False
                s["breakout"] = {"idx": i, "date": b["date"], "vol_ratio": r4(b["v"] / va) if va else None, "close": b["c"]}
                if not pullback:
                    fill = b["c"] * (1 + slip)
                    ok = enter(m, bars, i, fill, su, "breakout_close")
                    s["phase"], s["setup"] = ("long" if ok else "scan"), None
                    return ok
                s["phase"] = "broken"
                su["pb_deadline"] = i + pullback_wait
            return False
        if s["phase"] == "broken":
            if b["l"] <= su["neckline"]:                       # 눌림: 목선 지정가 체결 (갭이면 시가)
                fill = min(b["o"], su["neckline"])
                ok = enter(m, bars, i, fill, su, "pullback_limit")
                s["phase"], s["setup"] = ("long" if ok else "scan"), None
                return ok
            if i > su["pb_deadline"]:
                counts["no_pullback"] += 1
                s["phase"], s["setup"], s["breakout"] = "scan", None, None
            return False
        return False

    for t in ts:
        for m, bars in markets.items():
            i = idx[m].get(t)
            if i is None:
                continue
            s = st[m]
            b = bars[i]
            entered_now = False

            # 1) 보유 중: 손절선 이탈 확인 (저점이 깨지면 끝)
            if s["pos"]:
                p = s["pos"]
                if b["l"] < p["stop"]:
                    fill = min(b["o"], p["stop"]) * (1 - slip)
                    close_pos(m, bars, i, fill, "stop" if p["stop"] == p["stop0"] else "trail")
                    s["phase"] = "scan"
                else:
                    p["bars"] += 1
                    p["best"] = max(p["best"], b["h"])

            # 2) 피벗 저점 확정 (j = i − K 봉이 좌우 K 봉보다 낮다)
            j = i - PIVOT_K
            new_low = j >= 0 and is_pivot_low(bars, j)
            if new_low:
                s["lows"].append(j)
                if s["pos"] and j > s["pos"]["entry_idx"] and bars[j]["l"] > s["pos"]["stop"]:
                    s["pos"]["stop"] = bars[j]["l"]              # 손절선을 새 저점으로 올린다 (래칫)
                    s["pos"]["raises"] += 1

            # 3) 비어 있는 시장: 구조 찾기 → 돌파 → 눌림
            if s["pos"] is None:
                if s["phase"] == "scan" and new_low:
                    su = find_setup(bars, s["lows"], j, atr[m][j])
                    if su:
                        counts["setups"] += 1
                        su["deadline"] = i + break_wait
                        s["setup"], s["phase"] = su, "armed"
                        for x in range(j + 1, i + 1):            # 확정 전 봉(j+1..i)에 이미 돌파·눌림이 있었으면 되돌려 적용
                            if step_setup(m, bars, x):
                                entered_now = True
                                break
                            if s["phase"] == "scan":
                                break
                elif s["phase"] in ("armed", "broken"):
                    entered_now = step_setup(m, bars, i)
                if entered_now:
                    p = s["pos"]
                    # 체결 봉(x)부터 지금 봉(i)까지 손절 확인. 체결 봉에서 바로 깨졌으면 순서를 몰라 건너뛴다.
                    for y in range(p["entry_idx"], i + 1):
                        by = bars[y]
                        if by["l"] < p["stop"]:
                            if y == p["entry_idx"]:
                                counts["ambiguous"] += 1
                                s["pos"] = None
                            else:
                                close_pos(m, bars, y, min(by["o"], p["stop"]) * (1 - slip), "stop")
                            s["phase"] = "scan"
                            break
                        p["best"] = max(p["best"], by["h"])
                        if y > p["entry_idx"]:
                            p["bars"] += 1

        # 4) 봉별 계좌 가치 (미실현 포함)
        unreal = 0.0
        for m, s in st.items():
            p = s["pos"]
            if p and t in idx[m]:
                unreal += (markets[m][idx[m][t]]["c"] - p["entry"]) * p["units"]
        curve.append((datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M"), equity + unreal))

    state = {}
    for m, s in st.items():
        state[m] = {"phase": s["phase"], "pos": s["pos"], "setup": s["setup"], "breakout": s["breakout"]}
    return {"trades": trades, "curve": curve, "equity": equity, "state": state, "counts": counts}


def stats(res, equity0, bars_by_market=None):
    tr, curve = res["trades"], res["curve"]
    if not curve:
        return {"trades": 0}
    peak, mdd = -1e18, 0.0
    for _, e in curve:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1.0)
    d0, d1 = datetime.strptime(curve[0][0], "%Y-%m-%d %H:%M"), datetime.strptime(curve[-1][0], "%Y-%m-%d %H:%M")
    days = max(1, (d1 - d0).days)
    final = curve[-1][1]
    wins = [t for t in tr if t["pnl"] > 0]
    gp = sum(t["pnl"] for t in wins)
    gl = -sum(t["pnl"] for t in tr if t["pnl"] <= 0)
    by_year, year_start, prev_e = {}, {}, equity0
    for d, e in curve:
        y = d[:4]
        if y not in year_start:
            year_start[y] = prev_e
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
        "avg_hold_bars": r4(sum(t["bars"] for t in tr) / len(tr)) if tr else None,
        "stops": sum(1 for t in tr if t["reason"] == "stop"),
        "trails": sum(1 for t in tr if t["reason"] == "trail"),
        "avg_mfe_pct": r4(sum(t["mfe_pct"] for t in tr) / len(tr)) if tr else None,
        "avg_giveback_pct": r4(sum(t["mfe_pct"] - t["price_ret_pct"] for t in tr) / len(tr)) if tr else None,
        "counts": res["counts"],
        "by_year": by_year,
        "forward": {"trades": len(fwd), "wins": sum(1 for t in fwd if t["pnl"] > 0),
                    "net_pct": r4((math.prod(1 + t["ret_pct"] / 100.0 for t in fwd) - 1.0) * 100.0) if fwd else None},
    }
    if bars_by_market and len(bars_by_market) == 1:
        bars = list(bars_by_market.values())[0]
        out["buy_hold_pct"] = r4((bars[-1]["c"] / bars[0]["c"] - 1.0) * 100.0)
    return out


# ----------------------------------------------------------------- 오늘의 신호

def signal(bars, state, equity):
    """마지막 완성 봉 기준으로 지금 어느 단계이고 어느 가격에서 무엇을 할지."""
    close = bars[-1]["c"]
    atr = wilder_atr(bars)[-1]
    out = {"last_bar": bars[-1]["date"], "close": rp(close), "atr": rp(atr), "phase": state["phase"]}
    p, su, br = state.get("pos"), state.get("setup"), state.get("breakout")
    if p:
        out.update({"position": "long", "entry": rp(p["entry"]), "entry_date": p["entry_date"], "units": p["units"],
                    "stop": rp(p["stop"]), "stop0": rp(p["stop0"]), "neckline": rp(p["neckline"]),
                    "stop_distance_pct": r4((p["stop"] / close - 1.0) * 100.0),
                    "unrealized_pct": r4((close / p["entry"] - 1.0) * 100.0), "stop_raises": p["raises"],
                    "action": "보유 — 저가가 %s 아래로 내려오면 청산" % px(p["stop"])})
        return out
    out["position"] = "flat"
    if su:
        out.update({"neckline": rp(su["neckline"]), "stop": rp(su["stop"]), "pattern_low_date": bars[su["low_idx"]]["date"],
                    "bottom_date": bars[su["bottom"]]["date"],
                    "neckline_distance_pct": r4((su["neckline"] / close - 1.0) * 100.0)})
        if state["phase"] == "armed":
            out["bars_left"] = su["deadline"] - (len(bars) - 1)
            out["action"] = "구조 확인 — 종가 %s 위 + 거래량 2배면 돌파 (남은 %d봉)" % (px(su["neckline"]), out["bars_left"])
        else:
            units = equity * RISK_PCT / 100.0 / (su["neckline"] - su["stop"])
            out["bars_left"] = su["pb_deadline"] - (len(bars) - 1)
            out["breakout"] = br
            out["units_if_fill"] = units
            out["notional_pct_if_fill"] = r4(min(100.0, units * su["neckline"] / equity * 100.0))
            out["action"] = "돌파 확인 — %s 에 지정가 매수 대기, 손절 %s (남은 %d봉)" % (px(su["neckline"]), px(su["stop"]), out["bars_left"])
    else:
        out["action"] = "대기 — 하락추세 뒤 저점 유지 구조 없음"
    return out


# ----------------------------------------------------------------- 실행

def evaluate(bars_by, cfg, **kw):
    markets = {}
    for coin, bars in bars_by.items():
        res = run({coin: bars}, cfg["fee_pct"], cfg["equity"], **kw)
        markets[coin] = {
            "bars": len(bars), "stats": stats(res, cfg["equity"], {coin: bars}),
            "returns_pct": [t["ret_pct"] for t in res["trades"]],
            "forward_trades": [t for t in res["trades"] if t["forward"]],
            "recent_trades": res["trades"][-5:],
            "signal": signal(bars, res["state"][coin], cfg["equity"]),
        }
    pres = run(bars_by, cfg["fee_pct"], cfg["equity"], **kw)
    portfolio = {
        "markets": sorted(bars_by), "stats": stats(pres, cfg["equity"]),
        "returns_pct": [t["ret_pct"] for t in pres["trades"]],
        "open_positions": {m: {"entry": rp(s["pos"]["entry"]), "entry_date": s["pos"]["entry_date"], "stop": rp(s["pos"]["stop"]),
                               "units": s["pos"]["units"], "capped": s["pos"]["capped"]}
                           for m, s in pres["state"].items() if s["pos"]},
        "forward_trades": [t for t in pres["trades"] if t["forward"]],
        "equity_now": r4(pres["curve"][-1][1]) if pres["curve"] else None,
    }
    return {"markets": markets, "portfolio": portfolio}


def variant_kwargs(var):
    return {"vol_mult": var.get("vol_mult", VOL_MULT), "pullback": var.get("pullback", True)}


def build(fixture=None, save_to=None):
    now = datetime.now(KST)
    out = {
        "schema": "carrygate-reversal/1",
        "date": now.strftime("%Y-%m-%d"),
        "generated_at_kst": now.strftime("%Y-%m-%d %H:%M:%S"),
        "rules": {"bar_minutes": BAR_MIN, "pivot_k": PIVOT_K, "lookback_bars": LOOKBACK, "vol_avg_bars": VOL_N, "vol_mult": VOL_MULT,
                  "min_height_atr": MIN_HEIGHT_ATR, "atr_bars": ATR_N, "break_wait_bars": BREAK_WAIT, "pullback_wait_bars": PULLBACK_WAIT,
                  "risk_pct_per_trade": RISK_PCT, "max_notional_pct": MAX_NOTIONAL_PCT, "slippage_pct": SLIPPAGE_PCT,
                  "rule_fixed": RULE_FIXED, "long_only": True,
                  "entry": "목선(바닥~유지저점 사이 최고가) 위 종가 + 거래량 ≥ 20봉평균×2 → 12봉 안에 목선까지 눌리면 목선 지정가",
                  "stop": "유지된 저점(L2) 아래로 저가 → 청산. 진입 뒤 확정 피벗 저점마다 손절선을 올린다(래칫)",
                  "variants": {k: {"label": v["label"], **variant_kwargs(v)} for k, v in VARIANTS.items()}},
        "source": "fixture %s" % fixture if fixture else "upbit /v1/candles/minutes/240 (KRW 현물) + okx /v5/market/candles bar=4H (USDT 무기한)",
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
                    bars = upbit_4h(coin)
                else:
                    bars = okx_4h(coin)
                if len(bars) < MIN_BARS:
                    raise RuntimeError("4시간봉 부족 %d" % len(bars))
                bars_by[coin] = bars
                if save_to:
                    save_fixture(save_to, v, coin, bars)
            except Exception as e:
                out["errors"]["%s/%s" % (v, coin)] = str(e)[:300]
            if not fixture:
                time.sleep(0.3)
        if not bars_by:
            continue
        vd = {"kind": cfg["kind"], "quote": cfg["quote"], "fee_pct": cfg["fee_pct"], "fee_source": cfg["fee_source"], "equity0": cfg["equity"]}
        vd.update(evaluate(bars_by, cfg))
        vd["variants"] = {}
        for name, var in VARIANTS.items():
            ev = evaluate(bars_by, cfg, **variant_kwargs(var))
            ev["label"] = var["label"]
            vd["variants"][name] = ev
        out["venues"][v] = vd

    summ = {"ok": not out["errors"], "markets": sum(len(v["markets"]) for v in out["venues"].values())}
    for v, vd in out["venues"].items():
        p = vd["portfolio"]["stats"]
        summ[v] = {"portfolio_net_pct": p.get("net_pct"), "cagr_pct": p.get("cagr_pct"), "max_drawdown_pct": p.get("max_drawdown_pct"),
                   "trades": p.get("trades"), "win_rate_pct": p.get("win_rate_pct"), "profit_factor": p.get("profit_factor"),
                   "phases": {m: s["signal"]["phase"] for m, s in vd["markets"].items()},
                   "open": [m for m, s in vd["markets"].items() if s["signal"]["position"] == "long"],
                   "forward_trades": p.get("forward", {}).get("trades"),
                   "forward_net_pct": p.get("forward", {}).get("net_pct"),
                   "variants": {k: {"portfolio_net_pct": e["portfolio"]["stats"].get("net_pct"), "cagr_pct": e["portfolio"]["stats"].get("cagr_pct"),
                                    "max_drawdown_pct": e["portfolio"]["stats"].get("max_drawdown_pct"), "trades": e["portfolio"]["stats"].get("trades"),
                                    "win_rate_pct": e["portfolio"]["stats"].get("win_rate_pct"),
                                    "forward_trades": e["portfolio"]["stats"].get("forward", {}).get("trades"),
                                    "forward_net_pct": e["portfolio"]["stats"].get("forward", {}).get("net_pct")}
                                for k, e in vd.get("variants", {}).items()}}
    out["summary"] = summ
    return out


def history_line(out):
    line = {"date": out["date"], "summary": out["summary"], "signals": {}}
    keys = ("phase", "position", "close", "neckline", "stop", "entry", "entry_date", "unrealized_pct", "bars_left", "last_bar")
    for v, vd in out["venues"].items():
        for m, s in vd["markets"].items():
            line["signals"]["%s/%s" % (v, m)] = {k: s["signal"].get(k) for k in keys}
    return line


def write_history(out, path="reversal_history.jsonl"):
    """하루 한 줄. 같은 날짜가 이미 있으면 덮어쓴다."""
    try:
        with open(path, encoding="utf-8") as fh:
            rows = [l for l in fh.read().splitlines() if l.strip()]
    except IOError:
        rows = []
    rows = [l for l in rows if json.loads(l).get("date") != out["date"]]
    rows.append(json.dumps(history_line(out), ensure_ascii=False))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(rows) + "\n")


def print_summary(out):
    print("=== 추세 전환 매매법 (4시간봉: 저점 유지 → 목선 거래량 돌파 → 눌림 진입 / 저점 깨지면 청산) ===")
    for v, vd in out["venues"].items():
        p = vd["portfolio"]["stats"]
        print("[%s] 포트폴리오 %s: 순 %+.1f%%  CAGR %+.1f%%  최대낙폭 %.1f%%  매매 %d  승률 %.0f%%  PF %s  순방향 %d건" % (
            v, p["period"], p["net_pct"], p["cagr_pct"] or 0, p["max_drawdown_pct"], p["trades"], p["win_rate_pct"] or 0,
            p["profit_factor"], p["forward"]["trades"]))
        print("  연도별 %s  구조 %d / 가짜돌파 %d / 눌림없음 %d / 진입전이탈 %d / 만료 %d" % (
            json.dumps(p["by_year"]), p["counts"]["setups"], p["counts"]["fake_breaks"], p["counts"]["no_pullback"],
            p["counts"]["stopped_before_entry"], p["counts"]["expired"]))
        for m, s in vd["markets"].items():
            st, sg = s["stats"], s["signal"]
            print("  %-5s 순 %+7.1f%% (보유 %+7.1f%%)  낙폭 %6.1f%%  매매 %3d  승률 %3.0f%%  PF %s  평균R %s | %s" % (
                m, st["net_pct"], st.get("buy_hold_pct") or 0, st["max_drawdown_pct"], st["trades"], st["win_rate_pct"] or 0,
                st["profit_factor"], st["avg_r"], sg["action"]))
        for name, e in vd.get("variants", {}).items():
            q = e["portfolio"]["stats"]
            print("  변형 %s: 순 %+.1f%%  CAGR %+.1f%%  낙폭 %.1f%%  매매 %d  승률 %.0f%%  PF %s  연도별 %s" % (
                e["label"], q["net_pct"], q["cagr_pct"] or 0, q["max_drawdown_pct"], q["trades"], q["win_rate_pct"] or 0,
                q["profit_factor"], json.dumps(q["by_year"])))
    if out["errors"]:
        print("ERRORS:", json.dumps(out["errors"], ensure_ascii=False))


def report(path="reversal_history.jsonl"):
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
        print("추세 전환 기록 없음")
        return
    print("=== 추세 전환 신호 추이 (%d일, %s ~ %s) ===" % (len(rows), rows[0]["date"], rows[-1]["date"]))
    prev = {}
    for r in rows:
        changes = []
        for k, s in (r.get("signals") or {}).items():
            if prev.get(k) != s.get("phase"):
                changes.append("%s %s→%s" % (k, prev.get(k, "-"), s.get("phase")))
            prev[k] = s.get("phase")
        sm = r.get("summary", {})
        print("%s  업비트 순방향 %s건 %s%%  OKX 순방향 %s건 %s%%  %s" % (
            r["date"], (sm.get("upbit") or {}).get("forward_trades"), (sm.get("upbit") or {}).get("forward_net_pct"),
            (sm.get("okx") or {}).get("forward_trades"), (sm.get("okx") or {}).get("forward_net_pct"),
            ("변화: " + ", ".join(changes)) if changes else ""))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fixture", default=None)
    ap.add_argument("--save-fixture", default=None, help="거래소에서 받은 4시간봉을 이 폴더에 CSV 로 저장")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)
    if a.report:
        report()
        return 0
    out = build(a.fixture, a.save_fixture)
    print_summary(out)
    if not out["venues"]:
        print("시장 데이터 전부 실패", file=sys.stderr)
        return 1
    if not a.dry_run:
        with open("reversal.json", "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1)
        write_history(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
