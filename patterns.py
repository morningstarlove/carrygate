# -*- coding: utf-8 -*-
"""
CARRYGATE — 차트 패턴 판정 (설계서 docs/KR_SECTOR_BREAKOUT_PLAN.md 4단계). 순수 계산, 외부 접속 없음.

입력: 일봉 목록 [{"date","o","h","l","c","v","value"}] 오래된 것부터. 마지막 봉이 "오늘"(돌파 확인 봉).
출력: scan(bars) → {"ok", "filters", "patterns": [...], ...}. 패턴마다 저항선·패턴 하단(손절 힌트)·구간을 남긴다.

규칙(고정값, 사후 조정 금지):
  공통 필터  종가 ≥ 20일 이동평균, 거래대금 ≥ MIN_VALUE_KRW, 당일 등락 < LIMIT_UP_PCT(상한가 제외)
  돌파 확인  종가 > 저항선  그리고  거래량 ≥ VOL_MULT × 직전 20일 평균 거래량
  박스권     직전 15~40봉: 폭(상단−하단)/상단 ≤ 25%, 상단 터치(고가 ≥ 상단×0.97) 2회+, 하단 터치(저가 ≤ 하단×1.03) 2회+. 저항 = 상단
  삼각수렴   직전 20~60봉: 피벗 고점 2개+ 가 내려오고(기울기<0) 피벗 저점 2개+ 가 올라감(기울기>0), 끝 폭 ≤ 처음 폭의 50%,
             꼭짓점이 앞에 있음. 저항 = 고점 추세선의 오늘 값
  깃발형     최근 25봉 안에 10봉 이하로 저점→고점 +20% 이상(깃대). 그 뒤 3~15봉 깃발: 되돌림 ≤ 깃대의 50%,
             깃발 평균 거래량 ≤ 깃대 평균의 70%. 저항 = 깃발 최고가
  컵앤핸들   컵 30~120봉: 좌측 고점 → 바닥(깊이 12~35%, 컵의 가운데 20~80% 구간) → 우측 고점이 좌측의 95~105%.
             핸들 5~20봉: 되돌림 ≤ 컵 깊이의 1/3 이고 ≤ 12%. 저항 = 핸들 최고가
"""
import math

# ----------------------------------------------------------------- 고정 규칙
ATR_N = 20
VOL_AVG_N = 20
SMA_N = 20
VOL_MULT = 1.5
MIN_VALUE_KRW = 1e10        # 거래대금 100억
LIMIT_UP_PCT = 29.5
PIVOT_K = 3                 # 피벗: 좌우 3봉보다 높/낮

BOX_LEN = (15, 40); BOX_WIDTH_MAX = 0.25; BOX_TOUCH_TOL = 0.03; BOX_TOUCHES = 2
TRI_LEN = (20, 60); TRI_CONTRACT = 0.50
FLAG_POLE_LOOKBACK = 25; FLAG_POLE_MAX_LEN = 10; FLAG_POLE_MIN_RISE = 0.20
FLAG_LEN = (3, 15); FLAG_RETRACE_MAX = 0.50; FLAG_VOL_RATIO_MAX = 0.70
CUP_LEN = (30, 120); CUP_DEPTH = (0.12, 0.35); CUP_RIM_TOL = (0.95, 1.05); CUP_BOTTOM_POS = (0.20, 0.80)
HANDLE_LEN = (5, 20); HANDLE_DEPTH_FRAC = 1.0 / 3.0; HANDLE_DEPTH_MAX = 0.12

MIN_BARS = 60               # 이보다 적으면 판정하지 않는다 (컵은 더 길어야 하지만 다른 패턴은 가능)


# ----------------------------------------------------------------- 보조
def atr(bars, n=ATR_N):
    """와일더 ATR. 길이 bars 와 같고 앞쪽은 None."""
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


def sma(xs, n):
    out = [None] * len(xs)
    s = 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def pivots(bars, lo, hi, k=PIVOT_K):
    """[lo, hi) 구간 안의 피벗 고점/저점 인덱스. 좌우 k 봉보다 높은(낮은) 봉. 구간 밖 봉도 비교에 쓴다."""
    highs, lows = [], []
    for i in range(max(lo, k), min(hi, len(bars) - k)):
        h, l = bars[i]["h"], bars[i]["l"]
        if all(h > bars[j]["h"] for j in range(i - k, i + k + 1) if j != i):
            highs.append(i)
        if all(l < bars[j]["l"] for j in range(i - k, i + k + 1) if j != i):
            lows.append(i)
    return highs, lows


def linfit(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return 0.0, my
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return slope, my - slope * mx


def _value(b):
    return b["value"] if b.get("value") else b["c"] * b["v"]


# ----------------------------------------------------------------- 패턴
def box(bars, i):
    """오늘(i) 직전 L봉이 박스였고 오늘 종가가 상단을 넘었나. 가장 긴 L 하나를 돌려준다."""
    best = None
    for L in range(BOX_LEN[0], BOX_LEN[1] + 1):
        if i - L < 0:
            break
        w = bars[i - L:i]
        top, bottom = max(b["h"] for b in w), min(b["l"] for b in w)
        if top <= 0 or (top - bottom) / top > BOX_WIDTH_MAX:
            continue
        t_top = sum(1 for b in w if b["h"] >= top * (1 - BOX_TOUCH_TOL))
        t_bot = sum(1 for b in w if b["l"] <= bottom * (1 + BOX_TOUCH_TOL))
        if t_top < BOX_TOUCHES or t_bot < BOX_TOUCHES:
            continue
        if bars[i]["c"] <= top:
            continue
        best = {"pattern": "box", "resistance": top, "pattern_low": bottom, "window_len": L,
                "window_start": bars[i - L]["date"], "width_pct": round((top - bottom) / top * 100, 2),
                "touches_top": t_top, "touches_bottom": t_bot}
    return best


def triangle(bars, i):
    best = None
    for L in range(TRI_LEN[0], TRI_LEN[1] + 1):
        if i - L < PIVOT_K:
            break
        ph, pl = pivots(bars, i - L, i)
        if len(ph) < 2 or len(pl) < 2:
            continue
        sh, bh = linfit(ph, [bars[j]["h"] for j in ph])
        sl, bl = linfit(pl, [bars[j]["l"] for j in pl])
        if not (sh < 0 and sl > 0):
            continue
        third = max(3, L // 3)
        first = bars[i - L:i - L + third]; last = bars[i - third:i]
        r0 = max(b["h"] for b in first) - min(b["l"] for b in first)
        r1 = max(b["h"] for b in last) - min(b["l"] for b in last)
        if r0 <= 0 or r1 > r0 * TRI_CONTRACT:
            continue
        apex = (bl - bh) / (sh - sl)          # 두 선이 만나는 인덱스
        if apex <= i:
            continue
        upper_today = sh * i + bh
        lower_today = sl * i + bl
        if bars[i]["c"] <= upper_today:
            continue
        best = {"pattern": "triangle", "resistance": upper_today, "pattern_low": lower_today, "window_len": L,
                "window_start": bars[i - L]["date"], "pivot_highs": len(ph), "pivot_lows": len(pl),
                "contraction": round(r1 / r0, 2), "apex_in_bars": round(apex - i, 1)}
    return best


def flag(bars, i):
    """깃대(저점 j → 고점 k, ≤10봉, +20%+) 뒤 깃발(k+1..i-1, 3~15봉), 오늘 종가 > 깃발 최고가."""
    best = None
    lo_start = max(0, i - FLAG_POLE_LOOKBACK)
    for j in range(lo_start, i):
        low_j = bars[j]["l"]
        for k in range(j + 1, min(j + FLAG_POLE_MAX_LEN, i - FLAG_LEN[0]) + 1):
            high_k = bars[k]["h"]
            rise = high_k / low_j - 1
            if rise < FLAG_POLE_MIN_RISE:
                continue
            fl = i - 1 - k                      # 깃발 봉 수 (k+1 .. i-1)
            if not (FLAG_LEN[0] <= fl <= FLAG_LEN[1]):
                continue
            fw = bars[k + 1:i]
            pole_h = high_k - low_j
            if min(b["l"] for b in fw) < high_k - FLAG_RETRACE_MAX * pole_h:
                continue
            pole_vol = sum(b["v"] for b in bars[j:k + 1]) / (k - j + 1)
            flag_vol = sum(b["v"] for b in fw) / len(fw)
            if pole_vol <= 0 or flag_vol > FLAG_VOL_RATIO_MAX * pole_vol:
                continue
            res = max(b["h"] for b in fw)
            if bars[i]["c"] <= res:
                continue
            cand = {"pattern": "flag", "resistance": res, "pattern_low": min(b["l"] for b in fw), "window_len": fl,
                    "window_start": bars[k + 1]["date"], "pole_start": bars[j]["date"], "pole_rise_pct": round(rise * 100, 1),
                    "pole_bars": k - j, "flag_vol_ratio": round(flag_vol / pole_vol, 2)}
            if best is None or cand["pole_rise_pct"] > best["pole_rise_pct"]:
                best = cand
    return best


def cup_handle(bars, i):
    best = None
    for hl in range(HANDLE_LEN[0], HANDLE_LEN[1] + 1):
        b_end = i - hl                           # 컵 끝(우측 고점 포함) = 핸들 시작 직전
        for cl in range(CUP_LEN[0], CUP_LEN[1] + 1, 2):
            a = b_end - cl
            if a < 0:
                break
            cup = bars[a:b_end]
            left_part = cup[:max(3, cl // 5)]; right_part = cup[-max(3, cl // 5):]
            left_rim = max(b["h"] for b in left_part); right_rim = max(b["h"] for b in right_part)
            if left_rim <= 0 or not (CUP_RIM_TOL[0] <= right_rim / left_rim <= CUP_RIM_TOL[1]):
                continue
            bottom_idx = min(range(len(cup)), key=lambda t: cup[t]["l"])
            bottom = cup[bottom_idx]["l"]
            depth = (left_rim - bottom) / left_rim
            if not (CUP_DEPTH[0] <= depth <= CUP_DEPTH[1]):
                continue
            if not (CUP_BOTTOM_POS[0] <= bottom_idx / cl <= CUP_BOTTOM_POS[1]):
                continue
            handle = bars[b_end:i]
            h_low = min(b["l"] for b in handle); h_high = max(b["h"] for b in handle)
            h_depth = (right_rim - h_low) / right_rim
            if h_depth < 0 or h_depth > min(HANDLE_DEPTH_FRAC * depth, HANDLE_DEPTH_MAX):
                continue
            if h_high > right_rim * CUP_RIM_TOL[1]:
                continue                         # 핸들이 컵 위로 솟으면 핸들이 아니다
            if bars[i]["c"] <= h_high:
                continue
            cand = {"pattern": "cup_handle", "resistance": h_high, "pattern_low": h_low, "window_len": cl + hl,
                    "window_start": bars[a]["date"], "cup_len": cl, "handle_len": hl,
                    "cup_depth_pct": round(depth * 100, 1), "handle_depth_pct": round(h_depth * 100, 1)}
            if best is None or cand["cup_len"] > best["cup_len"]:
                best = cand
    return best


DETECTORS = [box, triangle, flag, cup_handle]


# ----------------------------------------------------------------- 종합
def scan(bars, i=None):
    """bars[i](기본: 마지막 봉)를 돌파 확인 봉으로 보고 공통 필터 + 4패턴을 판정한다."""
    if i is None:
        i = len(bars) - 1
    res = {"date": bars[i]["date"] if bars else None, "ok": False, "filters": {}, "patterns": [], "reason": None}
    if i + 1 < MIN_BARS:
        res["reason"] = "봉 부족(%d<%d)" % (i + 1, MIN_BARS)
        return res
    closes = [b["c"] for b in bars[:i + 1]]
    sma20 = sma(closes, SMA_N)[i]
    prev_vol = [b["v"] for b in bars[i - VOL_AVG_N:i]]
    vol_avg = sum(prev_vol) / len(prev_vol) if prev_vol else 0.0
    vol_ratio = (bars[i]["v"] / vol_avg) if vol_avg > 0 else 0.0
    change = (bars[i]["c"] / bars[i - 1]["c"] - 1) * 100 if bars[i - 1]["c"] else 0.0
    value = _value(bars[i])
    a = atr(bars[:i + 1])[i]
    res["filters"] = {"close": bars[i]["c"], "sma20": round(sma20, 2) if sma20 else None, "above_sma20": bool(sma20 and bars[i]["c"] >= sma20),
                      "value_krw": round(value), "value_ok": value >= MIN_VALUE_KRW,
                      "change_pct": round(change, 2), "limit_up": change >= LIMIT_UP_PCT,
                      "vol_ratio": round(vol_ratio, 2), "vol_ok": vol_ratio >= VOL_MULT, "atr20": round(a, 2) if a else None}
    f = res["filters"]
    if not f["above_sma20"]:
        res["reason"] = "20일선 아래"
    elif not f["value_ok"]:
        res["reason"] = "거래대금 부족"
    elif f["limit_up"]:
        res["reason"] = "상한가(추격 금지)"
    elif not f["vol_ok"]:
        res["reason"] = "거래량 부족(%.2fx)" % vol_ratio
    for det in DETECTORS:
        p = det(bars, i)
        if p:
            p["resistance"] = round(p["resistance"], 2); p["pattern_low"] = round(p["pattern_low"], 2)
            res["patterns"].append(p)
    res["ok"] = res["reason"] is None and bool(res["patterns"])
    if res["reason"] is None and not res["patterns"]:
        res["reason"] = "패턴 없음"
    return res


def scan_history(bars, start=MIN_BARS - 1):
    """모든 날에 대해 scan 을 돌려 ok 인 날만 돌려준다 (백테스트·회귀 시험용)."""
    out = []
    for i in range(start, len(bars)):
        r = scan(bars, i)
        if r["ok"]:
            out.append(r)
    return out
