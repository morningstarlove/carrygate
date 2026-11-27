# -*- coding: utf-8 -*-
"""
CARRYGATE — 추세선(평행 채널) 작도 + 하단 매수 규칙 (설계서 docs/TRENDLINE_PLAN.md, 제안함 P42). 순수 계산, 외부 접속 없음.

출처: 사용자가 공유한 강의 자료 "7장. 추세선 작도" 4장(2026-11-17). 사람이 눈으로 긋는 선을 숫자로 고정했다.
입력: 일봉 목록 [{"date","o","h","l","c"}] 오래된 것부터.  주문 없음. AI 판단 없음 — 아래 고정 규칙만.

작도 (자료 → 코드)
  "최외각은 꼬리"                 저가·고가(꼬리)로 긋는다. 종가 아님.
  "오늘을 기준으로"               오늘까지의 최근 N봉만 본다. N = 소 60 / 중 120 / 대 250 (WINDOWS).
  "상향은 하단(저점-저점)부터"    ① = N봉 안 최저 저가. ② = ① 뒤 확정된 저점(좌우 PIVOT_K봉 중 최저) 가운데
                                  ①과 이은 기울기가 가장 작은 것(= 그 아래로 어떤 저점도 없는 최외각 선). 기울기 > 0 이어야 상향.
                                  ①~② 간격은 N/4 봉 이상(MIN_SPAN_DIV). 사이에 종가가 선 아래로 마감한 날이 있으면 선 아님.
  "③고점에 평행하게 → 상단"       ①~② 사이에서 (고가 − 하단선)이 가장 큰 봉이 ③. 그 거리 = 채널 폭 W.
  "1:1 로 중단"                   하단 + W/2.
  "두 점 기간의 향후 1배 유효"    ② 뒤 (②−①) 봉까지만 유효(VALID_MULT). 지나면 그 선으로 새 진입 없음, 보유분은 종가 정리.
  "하향은 상단(고점-고점)부터"    down_channel() — 거울상. 기록·표시용(매매 규칙에는 안 씀).
  "살짝 튀어나감 = 보조"          하단 − TOL×W 까지는 이탈로 치지 않는다(TOL = 0.10).

매매 규칙 "하단에서 잡고 상단에서 놓는다" (결과를 보기 전에 고정, RULE_FIXED)
  신호   오늘 채널이 유효 · 저가 ≤ 하단 + TOL×W(하단 터치) · 종가 ≥ 하단(지켰다) · 채널 폭 ≥ 하단가의 MIN_WIDTH_PCT%
  진입   다음 날 시가. 시가가 손절선(하단 − TOL×W) 아래거나 목표선 이상이면 건너뜀. 진입 때 선을 얼린다(뒤에 다시 긋지 않음).
  익절   고가 ≥ 목표선이면 목표선 가격(시가가 이미 위면 시가).  base: 상단 / mid: 중단
  손절   종가 < 하단 − TOL×W 이면 다음 날 시가.
  만료   유효 기간이 끝나는 날 종가.
  변형   base(하단→상단) · mid(하단→중단) · midentry(자료가 "손익비 반반"이라 말린 것: 중단 터치 매수→상단, 손절 중단 − TOL×W)
  한 종목·한 창에 한 포지션. 사전 등록 주 규칙 = 중(120봉) · base.
"""

RULE_FIXED = "2026-11-17"
WINDOWS = {"소": 60, "중": 120, "대": 250}
PIVOT_K = 5
MIN_SPAN_DIV = 4
VALID_MULT = 1.0
TOL = 0.10
MIN_WIDTH_PCT = 5.0
MODES = ("base", "mid", "midentry")
PRIMARY = ("중", "base")


def pivot_flags(vals, k=PIVOT_K, low=True):
    """좌우 k봉 안에서 가장 낮은(높은) 봉이면 True. 오른쪽 k봉이 있어야 확정 — 앞날을 미리 보지 않게 호출 쪽이 t−k 까지만 쓴다."""
    n = len(vals)
    out = [False] * n
    for i in range(k, n - k):
        w = vals[i - k:i + k + 1]
        out[i] = (vals[i] == (min(w) if low else max(w)))
    return out


def up_channel(h, l, c, t, n, piv_lo, k=PIVOT_K):
    """오늘(t)까지의 최근 n봉으로 상향 채널을 긋는다. 없으면 None.
    반환: i1(①), i2(②), i3(③), slope(봉당), base(①의 저가), width(W), valid_to(마지막 유효 봉 번호)."""
    a = t - n + 1
    if a < 0:
        return None
    i1 = a
    for j in range(a, t + 1):
        if l[j] <= l[i1]:
            i1 = j
    span_min = n // MIN_SPAN_DIV
    best = None
    for j in range(i1 + span_min, t - k + 1):
        if piv_lo[j]:
            s = (l[j] - l[i1]) / (j - i1)
            if best is None or s <= best[0]:        # 같은 기울기면 더 최근 저점(오늘 기준)
                best = (s, j)
    if best is None or best[0] <= 0:
        return None
    slope, i2 = best
    line = lambda j: l[i1] + slope * (j - i1)
    for j in range(i1 + 1, i2 + 1):
        if c[j] < line(j):
            return None
    i3, width = i1, 0.0
    for j in range(i1, i2 + 1):
        d = h[j] - line(j)
        if d > width:
            width, i3 = d, j
    if width <= 0:
        return None
    return {"i1": i1, "i2": i2, "i3": i3, "slope": slope, "base": l[i1], "width": width,
            "valid_to": i2 + int((i2 - i1) * VALID_MULT)}


def down_channel(h, l, c, t, n, piv_hi, k=PIVOT_K):
    """하향 채널(거울상): ① = 최고 고가, ② = 뒤 확정 고점 중 기울기가 가장 큰(덜 가파른) 것, 기울기 < 0. '파란 지붕'. 기록·표시용."""
    neg = lambda xs: [-x for x in xs]
    ch = up_channel(neg(l), neg(h), neg(c), t, n, piv_hi, k)
    if ch is None:
        return None
    ch["slope"], ch["base"], ch["down"] = -ch["slope"], -ch["base"], True
    return ch


def lower(ch, j):
    return ch["base"] + ch["slope"] * (j - ch["i1"])


def mid(ch, j):
    return lower(ch, j) + ch["width"] / 2.0


def upper(ch, j):
    return lower(ch, j) + ch["width"]


def intact(ch, c, t):
    """② 뒤 오늘까지 종가가 하단 − TOL×W 아래로 마감한 적이 없고, 유효 기간 안이다."""
    if t > ch["valid_to"]:
        return False
    for j in range(ch["i2"] + 1, t + 1):
        if c[j] < lower(ch, j) - TOL * ch["width"]:
            return False
    return True


def signal(h, l, c, t, n, piv_lo, mode="base"):
    """오늘 종가 기준 매수 신호면 채널을, 아니면 None."""
    ch = up_channel(h, l, c, t, n, piv_lo)
    if ch is None or not intact(ch, c, t):
        return None
    lo = lower(ch, t)
    if lo <= 0 or ch["width"] / lo * 100.0 < MIN_WIDTH_PCT:
        return None
    ref = mid(ch, t) if mode == "midentry" else lo
    if l[t] <= ref + TOL * ch["width"] and c[t] >= ref:
        return ch
    return None


def backtest(bars, win="중", mode="base", cost_pct=0.0):
    """고정 규칙을 봉 순서대로 돌린다(앞날 안 봄). 반환: 거래 목록 [{entry_i, exit_i, entry, exit, why, net_pct, days, ch}]."""
    n = WINDOWS[win]
    o = [b["o"] for b in bars]; h = [b["h"] for b in bars]; l = [b["l"] for b in bars]; c = [b["c"] for b in bars]
    piv = pivot_flags(l)
    trades = []
    t = n - 1
    last = len(bars) - 1
    while t < last:
        ch = signal(h, l, c, t, n, piv, mode)
        if ch is None:
            t += 1
            continue
        e = t + 1
        ref = (mid if mode == "midentry" else lower)
        tgt = (mid if mode == "mid" else upper)
        stop_at = lambda j: ref(ch, j) - TOL * ch["width"]
        if o[e] < stop_at(e) or o[e] >= tgt(ch, e):
            t += 1
            continue
        k, px, why = e, None, None
        while k <= last:
            if h[k] >= tgt(ch, k):
                px, why = max(o[k], tgt(ch, k)), "target"      # 시가가 이미 목표 위면 시가
                break
            if c[k] < stop_at(k):
                if k + 1 <= last:
                    k += 1
                    px = o[k]
                else:
                    px = c[k]
                why = "stop"
                break
            if k >= ch["valid_to"]:
                px, why = c[k], "expire"
                break
            k += 1
        if px is None:
            k, px, why = last, c[last], "open"
        trades.append({"entry_i": e, "exit_i": k, "entry": o[e], "exit": px, "why": why,
                       "net_pct": (px / o[e] - 1.0) * 100.0 - cost_pct, "days": k - e + 1, "ch": ch,
                       "date_in": bars[e]["date"], "date_out": bars[k]["date"]})
        t = k + 1 if why != "open" else last
    return trades


def today(bars, win="중"):
    """오늘 봉 기준 상태(기록용): 상향/하향 채널과 세 선의 오늘 값, 신호 여부."""
    n = WINDOWS[win]
    h = [b["h"] for b in bars]; l = [b["l"] for b in bars]; c = [b["c"] for b in bars]
    t = len(bars) - 1
    out = {"date": bars[t]["date"], "win": win, "close": c[t], "up": None, "down": None, "signal": False}
    ch = up_channel(h, l, c, t, n, pivot_flags(l)) if t >= n - 1 else None
    if ch:
        out["up"] = {"p1": bars[ch["i1"]]["date"], "p2": bars[ch["i2"]]["date"], "p3": bars[ch["i3"]]["date"],
                     "lower": round(lower(ch, t), 4), "mid": round(mid(ch, t), 4), "upper": round(upper(ch, t), 4),
                     "intact": intact(ch, c, t), "valid_to_bars_left": ch["valid_to"] - t}
        out["signal"] = signal(h, l, c, t, n, pivot_flags(l), "base") is not None
    dn = down_channel(h, l, c, t, n, pivot_flags(h, low=False)) if t >= n - 1 else None
    if dn:
        out["down"] = {"p1": bars[dn["i1"]]["date"], "p2": bars[dn["i2"]]["date"],
                       "roof": round(lower(dn, t), 4), "floor": round(lower(dn, t) - dn["width"], 4)}
    return out
