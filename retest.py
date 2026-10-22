# -*- coding: utf-8 -*-
"""
CARRYGATE — 급상승·급하락 패턴 (1시간봉, "1차 상승 → 횡보(지지 구간) → 2차 상승 → 하락 조정 → 지지 구간에서 상승 캔들 확인 뒤 진입")

사용자가 공유한 SNS 카드(2026-10-22, "부자들은 여기서 진입" — 블룸에너지 15분봉·팔란티어 30분봉 예시)의 그림을
기계가 매일 똑같이 판정할 수 있는 규칙으로 고정했다. 보조지표 없음(ATR 은 크기 기준으로만 쓴다). 설계서 docs/RETEST_PLAN.md.

  그림의 순서 (급상승 패턴, 롱)                       규칙
  ① 1차 상승 + 거래량 상승                           피벗 고점 B 확정 시, 직전 36봉 안 최저가 A 에서 B 까지 높이 H ≥ ATR×3.
                                                     A~B 평균 거래량 ≥ A 앞 20봉 평균 × 1.5
  ② 횡보 : 쉬는 구간 → 지지 구간                      B 뒤 4~60봉. 저가가 A + H/2 아래로 안 내려가고(되돌림 ≤ 50%), 상단−하단 ≤ H/2,
                                                     평균 거래량 ≤ 1차 상승 평균 × 0.8. 지지 구간 = [횡보 최저가, 횡보 최고가]
  ③ 2차 상승                                         종가 > 횡보 최고가(돌파). 돌파 뒤 36봉 안에 피벗 고점 C 가 지지 상단 + ATR×1.5 이상
  ④ 하락 조정 : 투자자 익절, 거래량 감소               C 뒤 48봉 안에 저가가 지지 상단 이하로 내려옴. 종가가 지지 하단 아래면 무효
  ⑤ 상승 캔들(모닝스타) → 매수 : 롱                   지지 구간에 닿은 봉이 양봉이고 종가가 봉 범위 윗절반, 종가 ≥ 지지 하단,
                                                     조정 구간 평균 거래량 ≤ 2차 상승 평균 × 0.8 → 그 봉 종가에 진입(슬리피지 0.05%)
  손절                                                지지 하단 아래로 저가 → 청산
  청산(카드에 없음, 추세전환 트랙과 같은 연장)           진입 뒤 확정되는 피벗 저점마다 손절선을 올린다(래칫). 목표가 없음
  급하락 패턴(숏) = 위를 거울처럼 뒤집은 것. OKX 무기한선물만 숏. 업비트 현물은 롱만.
  수량: 계좌 1% ÷ |진입가 − 손절가|. 명목가 합계 ≤ 계좌 100%.

규칙 고정일(RULE_FIXED) 이후 진입한 매매만 forward(순방향)로 따로 집계한다. 변형(VARIANTS)은 나란히 기록만 한다.
주문 기능 없음, 읽기 전용 공개 API. 판단 근거만 기록한다(원칙 1).

사용법
  python retest.py                                   # 수집·계산·저장 (retest.json, retest_history.jsonl)
  python retest.py --dry-run                         # 저장 안 함
  python retest.py --fixture tests/fixtures/retest --dry-run   # 저장된 1시간봉 CSV 로 (인터넷 불필요)
  python retest.py --save-fixture tests/fixtures/retest        # 거래소에서 받은 봉을 CSV 로 저장 (Actions 에서)
  python retest.py --report                          # 기록 누적 보고
"""
import os, sys, json, math, csv, time, argparse
from datetime import datetime, timezone

from scalp import http_json, f, KST, COINS

# ----------------------------------------------------------------- 고정 규칙 (사후 조정 금지)
BAR_MIN = 60                # 1시간봉 (카드는 15·30분봉 미국 주식. 코인 15분봉은 초단타 트랙에서 잡음으로 확인돼 1시간봉으로 고정)
PIVOT_K = 3                 # 피벗 고점/저점: 좌우 3봉보다 높/낮. 오른쪽 3봉이 닫혀야 확정
ATR_N = 20                  # 크기 기준 ATR 구간(봉)
VOL_N = 20                  # 1차 상승 앞 "평소 거래량" 구간(봉)
POLE_MAX_BARS = 36          # 1차 상승(A→B)은 36봉(1.5일) 안에
POLE_MIN_ATR = 3.0          # 1차 상승 높이 H ≥ ATR × 3
POLE_VOL_MULT = 1.5         # 1차 상승 평균 거래량 ≥ 앞 20봉 평균 × 1.5  ("거래량 상승")
BOX_LEN = (4, 60)           # 횡보 길이(봉)
BOX_RETRACE_MAX = 0.5       # 횡보 저가 ≥ A + H × 0.5  (되돌림 50% 이하)
BOX_HEIGHT_FRAC_MAX = 0.5   # 횡보 폭(상단−하단) ≤ H × 0.5
BOX_VOL_RATIO_MAX = 0.8     # 횡보 평균 거래량 ≤ 1차 상승 평균 × 0.8  ("쉬는 구간")
RISE2_MIN_ATR = 1.5         # 2차 상승 고점 C ≥ 지지 상단 + ATR × 1.5
RISE2_WAIT = 36             # 돌파 뒤 이 봉 수 안에 C 가 확정돼야 한다
PULLBACK_WAIT = 48          # C 뒤 이 봉 수(2일) 안에 지지 구간까지 조정 + 확인 봉. 아니면 포기
PULL_VOL_RATIO_MAX = 0.8    # 조정 구간 평균 거래량 ≤ 2차 상승 평균 × 0.8  ("감소")
RISK_PCT = 1.0              # 한 번에 계좌의 1% 만 흔들리게
MAX_NOTIONAL_PCT = 100.0    # 포지션 명목가 합계 ≤ 계좌 100% (레버리지 1배)
SLIPPAGE_PCT = 0.05         # 종가 진입·손절 체결 시 불리하게 밀리는 가정
RULE_FIXED = "2026-10-22"   # 이 날 이후 진입한 매매만 순방향 시험
# 변형: 기본과 나란히 기록만 한다.
VARIANTS = {
    "no_vol":     {"vol": False, "label": "거래량 조건 3개 전부 없음 (모양만)"},
    "zone_limit": {"entry": "limit", "label": "상승 캔들 안 기다리고 지지 상단에 지정가 진입"},
    "target_2r":  {"target_r": 2.0, "label": "손절폭 2배에서 익절 (래칫 손절도 유지)"},
}
DAYS = 365
BARS = DAYS * 24            # 받아올 1시간봉 수 (1년)
MIN_BARS = 24 * 30          # 이보다 적으면 그 시장은 계산하지 않는다

VENUES = {
    "upbit": {"kind": "spot", "quote": "KRW",  "fee_pct": 0.05, "equity": 10_000_000.0, "dirs": ("long",),
              "fee_source": "업비트 기본 수수료 0.05%"},
    "okx":   {"kind": "perp", "quote": "USDT", "fee_pct": 0.05, "equity": 10_000.0, "dirs": ("long", "short"),
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


# ----------------------------------------------------------------- 1시간봉 수집

def _bar_open_cutoff():
    """지금 진행 중인(미완성) 봉의 시작 시각(unix 초). 이 시각 이상의 봉은 뺀다."""
    now = int(datetime.now(timezone.utc).timestamp())
    return now - now % (BAR_MIN * 60)


def _mk(t, o, h, l, c, v):
    return {"t": int(t), "date": datetime.fromtimestamp(int(t), timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "o": o, "h": h, "l": l, "c": c, "v": v}


def upbit_bars(coin, bars=BARS):
    """업비트 60분봉. 200개씩 과거로 넘기며 받는다. 미완성 봉은 뺀다."""
    out, to = {}, None
    while len(out) < bars + 2:
        url = "https://api.upbit.com/v1/candles/minutes/%d?market=KRW-%s&count=200" % (BAR_MIN, coin)
        if to:
            url += "&to=%s" % to
        rows = http_json(url)
        if not rows:
            break
        for r in rows:
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


def okx_bars(coin, bars=BARS):
    """OKX 무기한선물 1H 봉. 최신 300 + history-candles 100개씩. confirm=0(미완성) 제외. 거래량은 r[6](코인 수량)."""
    inst = "%s-USDT-SWAP" % coin
    out = {}

    def take(rows):
        for r in rows:
            if len(r) > 8 and str(r[8]) == "0":
                continue
            t = int(r[0]) // 1000
            out[t] = _mk(t, f(r[1]), f(r[2]), f(r[3]), f(r[4]), f(r[6]) if len(r) > 6 else f(r[5]))

    rows = http_json("https://www.okx.com/api/v5/market/candles?instId=%s&bar=1H&limit=300" % inst).get("data") or []
    take(rows)
    while rows and len(out) < bars + 2:
        oldest = min(int(r[0]) for r in rows)
        time.sleep(0.25)
        rows = http_json("https://www.okx.com/api/v5/market/history-candles?instId=%s&bar=1H&limit=100&after=%d"
                         % (inst, oldest)).get("data") or []
        take(rows)
    cut = _bar_open_cutoff()
    res = [out[t] for t in sorted(out) if t < cut]
    return res[-bars:]


def fixture_path(path, venue, coin):
    return os.path.join(path, "%s_%s_1h.csv" % (venue, coin))


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


# ----------------------------------------------------------------- 지표·보조

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


def mean_vol(bars, lo, hi):
    """[lo, hi] 구간(양 끝 포함) 평균 거래량. 비면 None."""
    if hi < lo:
        return None
    return sum(b["v"] for b in bars[lo:hi + 1]) / (hi - lo + 1)


def mirror(bars):
    """숏은 가격을 뒤집어(−가격) 롱 규칙을 그대로 쓴다. 고가·저가가 자리를 바꾼다. 거래량은 그대로."""
    return [{"t": b["t"], "date": b["date"], "o": -b["o"], "h": -b["l"], "l": -b["h"], "c": -b["c"], "v": b["v"]} for b in bars]


# ----------------------------------------------------------------- 구조 판정 (롱 기준. 숏은 거울)

def find_pole(bars, j, atr_j, vol=True, pole_max=POLE_MAX_BARS, pole_min_atr=POLE_MIN_ATR, pole_vol_mult=POLE_VOL_MULT):
    """j 가 방금 확정된 피벗 고점(B)일 때 ① 1차 상승인지. 반환 {"a","b","height","pole_vol"} 또는 None.
    A = [j − pole_max, j) 안의 최저가 봉(같으면 B 에 가까운 쪽). H = 고가[B] − 저가[A] ≥ ATR × pole_min_atr. A~B 평균 거래량 ≥ A 앞 20봉 평균 × pole_vol_mult."""
    if atr_j is None:
        return None
    lo = max(0, j - pole_max)
    a = min(range(lo, j), key=lambda i: (bars[i]["l"], -i)) if j > lo else None   # 같은 저가면 B 에 가까운 쪽(상승의 시작)
    if a is None:
        return None
    height = bars[j]["h"] - bars[a]["l"]
    if height < pole_min_atr * atr_j:
        return None
    pv = mean_vol(bars, a, j)
    if vol:
        if a < VOL_N:
            return None
        base = mean_vol(bars, a - VOL_N, a - 1)
        if base is None or base <= 0 or pv < pole_vol_mult * base:
            return None
    return {"a": a, "b": j, "height": height, "pole_vol": pv}


def is_confirm_bar(b, zone_lo, zone_hi):
    """⑤ 상승 캔들: 지지 구간에 닿고(저가 ≤ 상단), 양봉, 종가가 봉 범위 윗절반, 종가 ≥ 지지 하단."""
    if b["l"] > zone_hi:
        return False
    if b["c"] <= b["o"]:
        return False
    if b["c"] < (b["h"] + b["l"]) / 2.0:
        return False
    return b["c"] >= zone_lo


# ----------------------------------------------------------------- 백테스트 (여러 시장이 한 계좌를 나눠 쓴다)

def run(markets, fee_pct, equity0, slip_pct=SLIPPAGE_PCT, max_notional_pct=MAX_NOTIONAL_PCT,
        vol=True, entry="confirm", target_r=None):
    """
    markets: {이름: 1시간봉 목록} 또는 {이름: {"bars": [...], "dir": "long"|"short"}}. 한 계좌(equity0)를 모두 같이 쓴다.
    vol: False 면 거래량 조건 3개(1차 상승·횡보·조정)를 전부 끈다. entry: "confirm"(상승 캔들 종가) / "limit"(지지 상단 지정가).
    target_r: 손절폭 × 이 배수에서 익절 지정가(None 이면 없음).
    반환: {"trades", "curve", "equity", "state": {이름: 시장 상태}, "counts": {...}}
    숏 시장은 가격을 뒤집어 롱 규칙으로 돌리고, 기록할 때 다시 뒤집는다(sgn).
    """
    mk = {}
    for m, v in markets.items():
        if isinstance(v, dict):
            mk[m] = {"bars": mirror(v["bars"]) if v.get("dir") == "short" else v["bars"], "dir": v.get("dir", "long"),
                     "sgn": -1.0 if v.get("dir") == "short" else 1.0}
        else:
            mk[m] = {"bars": v, "dir": "long", "sgn": 1.0}
    atr = {m: wilder_atr(d["bars"]) for m, d in mk.items()}
    idx = {m: {b["t"]: i for i, b in enumerate(d["bars"])} for m, d in mk.items()}
    ts = sorted(set(b["t"] for d in mk.values() for b in d["bars"]))
    equity = float(equity0)
    fee, slip = fee_pct / 100.0, slip_pct / 100.0
    trades, curve = [], []
    counts = {"poles": 0, "box_failed": 0, "breakouts": 0, "rise2_failed": 0, "no_pullback": 0,
              "zone_lost": 0, "entries": 0, "ambiguous": 0}
    st = {m: {"phase": "scan", "pos": None, "setup": None} for m in mk}

    def real(m, x):
        return None if x is None else x * mk[m]["sgn"]

    def enter(m, bars, i, fill, su, how):
        nonlocal equity
        t = bars[i]["t"]
        used = 0.0
        for x, sx in st.items():
            if sx["pos"] and t in idx[x]:
                used += sx["pos"]["units"] * abs(mk[x]["bars"][idx[x][t]]["c"])
        stop = su["zone_lo"]
        if fill <= stop:
            return False
        units = equity * RISK_PCT / 100.0 / (fill - stop)
        cap = max(0.0, equity * max_notional_pct / 100.0 - used)
        capped = False
        if units * abs(fill) > cap:
            units, capped = cap / abs(fill), True
        if units <= 0:
            return False
        st[m]["pos"] = {"units": units, "entry": fill, "entry_idx": i, "entry_date": bars[i]["date"], "stop": stop,
                        "stop0": stop, "zone_lo": su["zone_lo"], "zone_hi": su["zone_hi"],
                        "target": (fill + target_r * (fill - stop)) if target_r else None,
                        "pole_date": bars[su["a"]]["date"], "box_date": bars[su["b"]]["date"],
                        "breakout_date": bars[su["bo"]]["date"], "rise2_date": bars[su["c"]]["date"],
                        "best": fill, "bars": 0, "capped": capped, "raises": 0, "how": how}
        counts["entries"] += 1
        return True

    def close_pos(m, bars, i, fill, reason):
        nonlocal equity
        p = st[m]["pos"]
        gross = (fill - p["entry"]) * p["units"]
        fees = (abs(p["entry"]) + abs(fill)) * p["units"] * fee
        pnl = gross - fees
        eq_before = equity
        equity += pnl
        sgn = mk[m]["sgn"]
        trades.append({
            "market": m, "dir": mk[m]["dir"], "entry_date": p["entry_date"], "exit_date": bars[i]["date"], "bars": p["bars"],
            "entry": rp(p["entry"] * sgn), "exit": rp(fill * sgn), "units": p["units"],
            "zone_lo": rp(min(p["zone_lo"] * sgn, p["zone_hi"] * sgn)), "zone_hi": rp(max(p["zone_lo"] * sgn, p["zone_hi"] * sgn)),
            "stop0": rp(p["stop0"] * sgn), "stop_final": rp(p["stop"] * sgn), "stop_raises": p["raises"],
            "target": rp(p["target"] * sgn) if p["target"] else None,
            "pole_date": p["pole_date"], "box_date": p["box_date"], "breakout_date": p["breakout_date"], "rise2_date": p["rise2_date"],
            "reason": reason, "entry_how": p["how"],
            "pnl": r4(pnl), "fees": r4(fees),
            "mfe_pct": r4((p["best"] - p["entry"]) / abs(p["entry"]) * 100.0),
            "price_ret_pct": r4((fill - p["entry"]) / abs(p["entry"]) * 100.0),
            "ret_pct": pnl / eq_before * 100.0,
            "r_multiple": r4(pnl / (eq_before * RISK_PCT / 100.0)),
            "forward": p["entry_date"][:10] >= RULE_FIXED,
        })
        st[m]["pos"] = None

    def step(m, bars, i):
        """비어 있는 시장의 한 봉 처리. True 면 이 봉에서 진입했다."""
        s = st[m]
        su = s["setup"]
        b = bars[i]
        if s["phase"] == "box":
            if b["l"] < su["a_low"] + BOX_RETRACE_MAX * su["height"]:        # 되돌림 50% 초과 → 횡보 아님
                counts["box_failed"] += 1
                s["phase"], s["setup"] = "scan", None
                return False
            if b["c"] > su["box_hi"]:                                        # ③ 돌파
                n = i - su["b"] - 1                                          # 횡보 봉 수 (B 와 돌파 봉 제외)
                ok = BOX_LEN[0] <= n <= BOX_LEN[1] and (su["box_hi"] - su["box_lo"]) <= BOX_HEIGHT_FRAC_MAX * su["height"]
                if ok and vol:
                    bv = mean_vol(bars, su["b"] + 1, i - 1)
                    ok = bv is not None and bv <= BOX_VOL_RATIO_MAX * su["pole_vol"]
                if not ok:
                    counts["box_failed"] += 1
                    s["phase"], s["setup"] = "scan", None
                    return False
                counts["breakouts"] += 1
                su.update({"bo": i, "zone_lo": su["box_lo"], "zone_hi": su["box_hi"], "rise2_deadline": i + RISE2_WAIT})
                s["phase"] = "rise2"
                return False
            su["box_hi"] = max(su["box_hi"], b["h"])
            su["box_lo"] = min(su["box_lo"], b["l"])
            if i - su["b"] - 1 > BOX_LEN[1]:
                counts["box_failed"] += 1
                s["phase"], s["setup"] = "scan", None
            return False
        if s["phase"] == "rise2":
            if b["c"] < su["zone_lo"]:                                       # 돌파 뒤 지지 구간 아래로 종가 → 실패
                counts["rise2_failed"] += 1
                s["phase"], s["setup"] = "scan", None
                return False
            j = i - PIVOT_K
            if j >= su["bo"] and is_pivot_high(bars, j) and bars[j]["h"] >= su["zone_hi"] + RISE2_MIN_ATR * (atr[m][j] or 0.0):
                su["c"] = j
                su["rise2_vol"] = mean_vol(bars, su["bo"], j)
                su["pb_deadline"] = j + PULLBACK_WAIT
                s["phase"] = "pullback"
                for x in range(j + 1, i + 1):                               # 확정 전 봉(C+1..i)에 이미 조정·확인이 있었으면 되돌려 적용
                    if step(m, bars, x):
                        return True
                    if s["phase"] != "pullback":
                        return False
                return False
            if i > su["rise2_deadline"]:
                counts["rise2_failed"] += 1
                s["phase"], s["setup"] = "scan", None
            return False
        if s["phase"] == "pullback":
            if b["c"] < su["zone_lo"]:                                       # 지지 깨짐 → 무효
                counts["zone_lost"] += 1
                s["phase"], s["setup"] = "scan", None
                return False
            if i > su["pb_deadline"]:
                counts["no_pullback"] += 1
                s["phase"], s["setup"] = "scan", None
                return False
            if entry == "limit":
                if b["l"] <= su["zone_hi"]:                                  # 변형: 지지 상단 지정가 (갭이면 시가)
                    fill = min(b["o"], su["zone_hi"])
                    ok = enter(m, bars, i, fill, su, "zone_limit")
                    s["phase"], s["setup"] = ("long" if ok else "scan"), None
                    return ok
                return False
            if is_confirm_bar(b, su["zone_lo"], su["zone_hi"]):
                ok = True
                if vol:
                    pv = mean_vol(bars, su["c"] + 1, i)
                    ok = pv is not None and su["rise2_vol"] and pv <= PULL_VOL_RATIO_MAX * su["rise2_vol"]
                if ok:
                    fill = b["c"] + abs(b["c"]) * slip                       # 불리한 쪽으로 (뒤집은 숏 가격은 음수라 |가격| 기준)
                    ok = enter(m, bars, i, fill, su, "confirm_close")
                    s["phase"], s["setup"] = ("long" if ok else "scan"), None
                    return ok
            return False
        return False

    def check_exit(m, bars, y):
        """보유 중 한 봉: 손절(래칫 포함) → 익절 순으로 본다. 같은 봉에 둘 다면 손절을 먼저(보수적)."""
        p = st[m]["pos"]
        b = bars[y]
        if b["l"] < p["stop"]:
            fill = min(b["o"], p["stop"])
            close_pos(m, bars, y, fill - abs(fill) * slip, "stop" if p["stop"] == p["stop0"] else "trail")   # 불리한 쪽으로 |가격|×slip
            return True
        if p["target"] is not None and b["h"] >= p["target"]:
            close_pos(m, bars, y, max(b["o"], p["target"]), "target")
            return True
        p["bars"] += 1
        p["best"] = max(p["best"], b["h"])
        return False

    for t in ts:
        for m, d in mk.items():
            bars = d["bars"]
            i = idx[m].get(t)
            if i is None:
                continue
            s = st[m]
            b = bars[i]
            entered_now = False

            # 1) 보유 중: 손절·익절
            if s["pos"]:
                if check_exit(m, bars, i):
                    s["phase"] = "scan"

            # 2) 피벗 확정 (j = i − K)
            j = i - PIVOT_K
            new_low = j >= 0 and is_pivot_low(bars, j)
            new_high = j >= 0 and is_pivot_high(bars, j)
            if new_low and s["pos"] and j > s["pos"]["entry_idx"] and bars[j]["l"] > s["pos"]["stop"]:
                s["pos"]["stop"] = bars[j]["l"]                              # 손절선을 새 저점으로 올린다 (래칫)
                s["pos"]["raises"] += 1

            # 3) 비어 있는 시장: ① 1차 상승 찾기 → ② 횡보 → ③ 돌파·2차 상승 → ④ 조정 → ⑤ 확인 봉
            if s["pos"] is None:
                if s["phase"] == "scan":
                    if new_high:
                        pole = find_pole(bars, j, atr[m][j], vol=vol)
                        if pole:
                            counts["poles"] += 1
                            s["setup"] = {"a": pole["a"], "b": j, "a_low": bars[pole["a"]]["l"], "height": pole["height"],
                                          "pole_vol": pole["pole_vol"], "box_hi": bars[j]["h"], "box_lo": bars[j]["h"]}
                            s["phase"] = "box"
                            for x in range(j + 1, i + 1):                    # 확정 전 봉(B+1..i)을 횡보로 되돌려 적용
                                if step(m, bars, x):
                                    entered_now = True
                                    break
                                if s["phase"] == "scan":
                                    break
                else:
                    entered_now = step(m, bars, i)
                if entered_now:
                    p = s["pos"]
                    if p["how"] == "zone_limit" and bars[p["entry_idx"]]["l"] < p["stop"]:
                        counts["ambiguous"] += 1                             # 지정가 체결 봉에서 바로 깨짐: 순서를 몰라 건너뛴다
                        s["pos"], s["phase"] = None, "scan"
                    else:
                        for y in range(p["entry_idx"] + 1, i + 1):           # 체결 봉 다음부터 지금 봉까지 손절·익절 확인
                            if check_exit(m, bars, y):
                                s["phase"] = "scan"
                                break

        # 4) 봉별 계좌 가치 (미실현 포함)
        unreal = 0.0
        for m, s in st.items():
            p = s["pos"]
            if p and t in idx[m]:
                unreal += (mk[m]["bars"][idx[m][t]]["c"] - p["entry"]) * p["units"]
        curve.append((datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M"), equity + unreal))

    state = {}
    for m, s in st.items():
        sgn = mk[m]["sgn"]
        pos = None
        if s["pos"]:
            p = s["pos"]
            pos = dict(p, entry=p["entry"] * sgn, stop=p["stop"] * sgn, stop0=p["stop0"] * sgn,
                       zone_lo=min(p["zone_lo"] * sgn, p["zone_hi"] * sgn), zone_hi=max(p["zone_lo"] * sgn, p["zone_hi"] * sgn),
                       target=(p["target"] * sgn) if p["target"] else None, best=p["best"] * sgn)
        setup = None
        if s["setup"]:
            su = s["setup"]
            setup = {"phase": s["phase"], "dir": mk[m]["dir"], "a_date": mk[m]["bars"][su["a"]]["date"], "b_date": mk[m]["bars"][su["b"]]["date"],
                     "height": su["height"]}
            lo, hi = su["box_lo"] * sgn, su["box_hi"] * sgn
            setup["box_lo"], setup["box_hi"] = min(lo, hi), max(lo, hi)
            if "bo" in su:
                setup["breakout_date"] = mk[m]["bars"][su["bo"]]["date"]
                setup["rise2_deadline"] = su["rise2_deadline"]
            if "c" in su:
                setup["rise2_date"] = mk[m]["bars"][su["c"]]["date"]
                setup["rise2_high"] = mk[m]["bars"][su["c"]]["h"] * sgn
                setup["pb_deadline"] = su["pb_deadline"]
        state[m] = {"phase": s["phase"], "dir": mk[m]["dir"], "pos": pos, "setup": setup}
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
        "targets": sum(1 for t in tr if t["reason"] == "target"),
        "longs": sum(1 for t in tr if t["dir"] == "long"),
        "shorts": sum(1 for t in tr if t["dir"] == "short"),
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

PHASE_KO = {"scan": "대기", "box": "횡보 중", "rise2": "돌파 — 2차 상승 대기", "pullback": "조정 중 — 확인 봉 대기", "long": "보유"}


def signal(bars, state, equity):
    """마지막 완성 봉 기준으로 지금 어느 단계이고 어느 가격에서 무엇을 할지. 숏이면 '매수'를 '매도'로 읽는다."""
    close = bars[-1]["c"]
    atr = wilder_atr(bars)[-1]
    d = state.get("dir", "long")
    side = "매수(롱)" if d == "long" else "매도(숏)"
    out = {"last_bar": bars[-1]["date"], "close": rp(close), "atr": rp(atr), "phase": state["phase"], "dir": d,
           "phase_ko": PHASE_KO.get(state["phase"], state["phase"])}
    p, su = state.get("pos"), state.get("setup")
    n = len(bars) - 1
    if p:
        sgn = 1.0 if d == "long" else -1.0
        out.update({"position": d, "entry": rp(p["entry"]), "entry_date": p["entry_date"], "units": p["units"],
                    "stop": rp(p["stop"]), "stop0": rp(p["stop0"]), "zone_lo": rp(p["zone_lo"]), "zone_hi": rp(p["zone_hi"]),
                    "target": rp(p["target"]),
                    "stop_distance_pct": r4((p["stop"] - close) / close * 100.0 * sgn),
                    "unrealized_pct": r4((close - p["entry"]) / p["entry"] * 100.0 * sgn), "stop_raises": p["raises"],
                    "action": "보유(%s) — %s %s 넘으면 청산" % (side, "저가가" if d == "long" else "고가가", px(p["stop"]))})
        return out
    out["position"] = "flat"
    if su:
        out.update({"zone_lo": rp(su["box_lo"]), "zone_hi": rp(su["box_hi"]), "pole_date": su["a_date"], "box_date": su["b_date"]})
        if state["phase"] == "box":
            out["action"] = "횡보 중(%s~%s) — 종가가 %s 을 넘으면 돌파. 되돌림 50%% 넘으면 무효" % (
                px(su["box_lo"]), px(su["box_hi"]), px(su["box_hi"] if d == "long" else su["box_lo"]))
        elif state["phase"] == "rise2":
            out["bars_left"] = su["rise2_deadline"] - n
            out["breakout_date"] = su.get("breakout_date")
            out["action"] = "돌파 확인(%s) — 2차 %s 고점 확정 대기 (지지 구간 %s~%s, 남은 %d봉)" % (
                su.get("breakout_date"), "상승" if d == "long" else "하락", px(su["box_lo"]), px(su["box_hi"]), out["bars_left"])
        elif state["phase"] == "pullback":
            out["bars_left"] = su["pb_deadline"] - n
            out["rise2_date"] = su.get("rise2_date")
            out["rise2_high"] = rp(su.get("rise2_high"))
            stop = su["box_lo"] if d == "long" else su["box_hi"]
            ref = su["box_hi"] if d == "long" else su["box_lo"]
            units = equity * RISK_PCT / 100.0 / abs(ref - stop) if ref != stop else None
            out["units_if_fill_at_zone_edge"] = units
            out["notional_pct_if_fill"] = r4(min(100.0, units * abs(ref) / equity * 100.0)) if units else None
            out["action"] = "조정 중 — 지지 구간 %s~%s 에서 %s 확인 봉이 닫히면 그 종가에 %s, 손절 %s (남은 %d봉)" % (
                px(su["box_lo"]), px(su["box_hi"]), "양봉" if d == "long" else "음봉", side, px(stop), out["bars_left"])
    else:
        out["action"] = "대기 — 1차 %s + 횡보 구조 없음" % ("상승" if d == "long" else "하락")
    return out


# ----------------------------------------------------------------- 실행

def evaluate(bars_by, cfg, **kw):
    """bars_by: {코인: 봉}. cfg["dirs"] 의 방향마다 시장을 하나씩 만든다 ("BTC/long", "BTC/short")."""
    mk = {}
    for coin, bars in bars_by.items():
        for d in cfg["dirs"]:
            mk["%s/%s" % (coin, d)] = {"bars": bars, "dir": d}
    markets = {}
    for name, m in mk.items():
        res = run({name: m}, cfg["fee_pct"], cfg["equity"], **kw)
        markets[name] = {
            "bars": len(m["bars"]), "dir": m["dir"], "stats": stats(res, cfg["equity"], {name: m["bars"]}),
            "returns_pct": [t["ret_pct"] for t in res["trades"]],
            "forward_trades": [t for t in res["trades"] if t["forward"]],
            "recent_trades": res["trades"][-5:],
            "signal": signal(m["bars"], res["state"][name], cfg["equity"]),
        }
    pres = run(mk, cfg["fee_pct"], cfg["equity"], **kw)
    portfolio = {
        "markets": sorted(mk), "stats": stats(pres, cfg["equity"]),
        "returns_pct": [t["ret_pct"] for t in pres["trades"]],
        "open_positions": {m: {"dir": s["dir"], "entry": rp(s["pos"]["entry"]), "entry_date": s["pos"]["entry_date"], "stop": rp(s["pos"]["stop"]),
                               "units": s["pos"]["units"], "capped": s["pos"]["capped"]}
                           for m, s in pres["state"].items() if s["pos"]},
        "forward_trades": [t for t in pres["trades"] if t["forward"]],
        "equity_now": r4(pres["curve"][-1][1]) if pres["curve"] else None,
    }
    return {"markets": markets, "portfolio": portfolio}


def variant_kwargs(var):
    return {"vol": var.get("vol", True), "entry": var.get("entry", "confirm"), "target_r": var.get("target_r")}


def rules_dict():
    return {"bar_minutes": BAR_MIN, "pivot_k": PIVOT_K, "atr_bars": ATR_N, "vol_avg_bars": VOL_N,
            "pole_max_bars": POLE_MAX_BARS, "pole_min_atr": POLE_MIN_ATR, "pole_vol_mult": POLE_VOL_MULT,
            "box_len": list(BOX_LEN), "box_retrace_max": BOX_RETRACE_MAX, "box_height_frac_max": BOX_HEIGHT_FRAC_MAX,
            "box_vol_ratio_max": BOX_VOL_RATIO_MAX, "rise2_min_atr": RISE2_MIN_ATR, "rise2_wait_bars": RISE2_WAIT,
            "pullback_wait_bars": PULLBACK_WAIT, "pull_vol_ratio_max": PULL_VOL_RATIO_MAX,
            "risk_pct_per_trade": RISK_PCT, "max_notional_pct": MAX_NOTIONAL_PCT, "slippage_pct": SLIPPAGE_PCT,
            "variants": {k: variant_kwargs(v) for k, v in VARIANTS.items()},
            "fees": {k: v["fee_pct"] for k, v in VENUES.items()},
            "dirs": {k: list(v["dirs"]) for k, v in VENUES.items()}}


def build(fixture=None, save_to=None):
    now = datetime.now(KST)
    out = {
        "schema": "carrygate-retest/1",
        "date": now.strftime("%Y-%m-%d"),
        "generated_at_kst": now.strftime("%Y-%m-%d %H:%M:%S"),
        "rules": dict(rules_dict(), rule_fixed=RULE_FIXED,
                      entry="1차 상승(ATR×3, 거래량 1.5배) → 횡보 4~60봉(되돌림≤50%, 폭≤H/2, 거래량≤0.8배) → 종가 돌파 → 2차 고점(지지 상단+ATR×1.5) → "
                            "48봉 안 조정(거래량≤0.8배) → 지지 구간 양봉(윗절반 마감) 종가 진입",
                      stop="지지 하단 아래로 저가 → 청산. 진입 뒤 확정 피벗 저점마다 손절선을 올린다(래칫). 숏은 거울",
                      variants={k: {"label": v["label"], **variant_kwargs(v)} for k, v in VARIANTS.items()}),
        "source": "fixture %s" % fixture if fixture else "upbit /v1/candles/minutes/60 (KRW 현물) + okx /v5/market/candles bar=1H (USDT 무기한)",
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
                    bars = upbit_bars(coin)
                else:
                    bars = okx_bars(coin)
                if len(bars) < MIN_BARS:
                    raise RuntimeError("1시간봉 부족 %d" % len(bars))
                bars_by[coin] = bars
                if save_to:
                    save_fixture(save_to, v, coin, bars)
            except Exception as e:
                out["errors"]["%s/%s" % (v, coin)] = str(e)[:300]
            if not fixture:
                time.sleep(0.3)
        if not bars_by:
            continue
        vd = {"kind": cfg["kind"], "quote": cfg["quote"], "fee_pct": cfg["fee_pct"], "fee_source": cfg["fee_source"],
              "equity0": cfg["equity"], "dirs": list(cfg["dirs"])}
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
                   "open": [m for m, s in vd["markets"].items() if s["signal"]["position"] != "flat"],
                   "waiting_entry": [m for m, s in vd["markets"].items() if s["signal"]["phase"] == "pullback"],
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
    keys = ("phase", "position", "close", "zone_lo", "zone_hi", "stop", "entry", "entry_date", "unrealized_pct", "bars_left", "last_bar")
    for v, vd in out["venues"].items():
        for m, s in vd["markets"].items():
            line["signals"]["%s/%s" % (v, m)] = {k: s["signal"].get(k) for k in keys}
    return line


def write_history(out, path="retest_history.jsonl"):
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
    print("=== 급상승·급하락 패턴 (1시간봉: 1차 상승 → 횡보 → 2차 상승 → 조정 → 지지 구간 확인 봉 진입 / 지지 깨지면 청산) ===")
    for v, vd in out["venues"].items():
        p = vd["portfolio"]["stats"]
        c = p.get("counts", {})
        print("[%s] 포트폴리오 %s: 순 %+.1f%%  CAGR %+.1f%%  최대낙폭 %.1f%%  매매 %d (롱 %d/숏 %d)  승률 %.0f%%  PF %s  순방향 %d건" % (
            v, p["period"], p["net_pct"], p["cagr_pct"] or 0, p["max_drawdown_pct"], p["trades"], p.get("longs", 0), p.get("shorts", 0),
            p["win_rate_pct"] or 0, p["profit_factor"], p["forward"]["trades"]))
        print("  연도별 %s  1차상승 %d / 횡보탈락 %d / 돌파 %d / 2차실패 %d / 조정없음 %d / 지지깨짐 %d / 진입 %d" % (
            json.dumps(p["by_year"]), c.get("poles", 0), c.get("box_failed", 0), c.get("breakouts", 0), c.get("rise2_failed", 0),
            c.get("no_pullback", 0), c.get("zone_lost", 0), c.get("entries", 0)))
        for m, s in vd["markets"].items():
            st, sg = s["stats"], s["signal"]
            print("  %-10s 순 %+7.1f%%  낙폭 %6.1f%%  매매 %3d  승률 %3.0f%%  PF %s  평균R %s | %s" % (
                m, st["net_pct"], st["max_drawdown_pct"], st["trades"], st["win_rate_pct"] or 0,
                st["profit_factor"], st["avg_r"], sg["action"]))
        for name, e in vd.get("variants", {}).items():
            q = e["portfolio"]["stats"]
            print("  변형 %s: 순 %+.1f%%  CAGR %+.1f%%  낙폭 %.1f%%  매매 %d  승률 %.0f%%  PF %s" % (
                e["label"], q["net_pct"], q["cagr_pct"] or 0, q["max_drawdown_pct"], q["trades"], q["win_rate_pct"] or 0, q["profit_factor"]))
    if out["errors"]:
        print("ERRORS:", json.dumps(out["errors"], ensure_ascii=False))


def report(path="retest_history.jsonl"):
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
        print("급상승 패턴 기록 없음")
        return
    print("=== 급상승·급하락 패턴 신호 추이 (%d일, %s ~ %s) ===" % (len(rows), rows[0]["date"], rows[-1]["date"]))
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
    ap.add_argument("--save-fixture", default=None, help="거래소에서 받은 1시간봉을 이 폴더에 CSV 로 저장")
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
        with open("retest.json", "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1)
        write_history(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
