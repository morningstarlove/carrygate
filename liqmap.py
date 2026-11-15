# -*- coding: utf-8 -*-
"""
CARRYGATE — 청산 맵 비대칭 모형 (제안함 P38, 설계서 docs/LIQMAP_PLAN.md). 순수 계산 모듈. 주문 없음, 키 없음, 네트워크 없음.

무엇인가
  공개 자료(무기한 선물 1시간봉 + 미결제약정 OI)만으로 "청산 물량이 어느 쪽에 쌓였나"를 추정해
  지금 가격 위(숏 청산)·아래(롱 청산) 물량의 비대칭 A 를 낸다. 가설(자석): A>=+0.2 → 24시간 뒤 오름, A<=-0.2 → 내림.

규칙 요약 (설계서 2절, 고정값 — 결과를 보고 고치지 않는다)
  봉마다 순서: ① 쓸기(롱 청산가 >= 봉 저가, 숏 청산가 <= 봉 고가인 묶음 제거)
               ② OI 가 줄었으면 남은 묶음 전부에 OI_지금/OI_직전 곱함
               ③ OI 가 늘었으면 진입가 (고+저+종)/3 로 롱·숏 묶음을 배율별로 추가(새 묶음은 자기 봉에서 안 쓸림)
               ④ 14일(LOOKBACK_H) 넘은 묶음 버림.  OI 가 None 이면 ②③ 건너뜀(직전 OI 유지).
  매일 00:00 UTC 종가 P 에서 UP = 청산가 P 초과 ~ P*(1+BAND) 숏 금액 합, DN = P*(1-BAND) ~ P 미만 롱 금액 합,
  A = (UP-DN)/(UP+DN).  성적 = 방향 x 24시간 수익률 - FEE_PCT (종이 계산).

봉 형식: {"t": 봉 시작 UTC 초(int), "o","h","l","c": float, "oi": float 또는 None}  (oi 단위 = 코인 개수)
"""

MODEL_FIXED = "2026-11-15"
LOOKBACK_H = 336            # 14일
LEVS = (10, 25, 50, 100)    # 새 포지션 레버리지 가정, 각 25%
MMR = 0.005                 # 유지증거금률 가정
BAND = 0.10                 # 지금 가격 위·아래 이 범위만 센다
A_MIN = 0.2                 # 비대칭이 이보다 작으면 신호 없음
HORIZON_H = 24
FEE_PCT = 0.10              # 왕복 수수료 (%)
BAR_SEC = 3600
DAY_SEC = 86400


def rules_dict():
    return {"model_fixed": MODEL_FIXED, "lookback_h": LOOKBACK_H, "levs": list(LEVS), "mmr": MMR, "band": BAND,
            "a_min": A_MIN, "horizon_h": HORIZON_H, "fee_pct": FEE_PCT, "bar_sec": BAR_SEC}


def liq_prices(entry, lev):
    """(롱 청산가, 숏 청산가)"""
    return entry * (1 - 1.0 / lev + MMR), entry * (1 + 1.0 / lev - MMR)


def new_state():
    return {"cohorts": [], "prev_oi": None}


def step(state, bar):
    """봉 하나를 처리해 상태를 갱신하고 이 봉이 쓸어낸 금액을 돌려준다."""
    lo, hi = bar["l"], bar["h"]
    swept_long = swept_short = 0.0
    keep = []
    for c in state["cohorts"]:
        if c["side"] == "L":
            if c["liq"] >= lo:
                swept_long += c["amt"]
                continue
        elif c["liq"] <= hi:
            swept_short += c["amt"]
            continue
        keep.append(c)
    state["cohorts"] = keep

    oi = bar.get("oi")
    prev = state["prev_oi"]
    if oi is not None:
        if prev is None:
            pass                                    # 첫 OI 봉: 기록만
        elif oi < prev:
            k = oi / prev
            for c in keep:
                c["amt"] *= k
        elif oi > prev:
            entry = (bar["h"] + bar["l"] + bar["c"]) / 3.0
            amt = (oi - prev) * entry / len(LEVS)
            for lev in LEVS:
                ll, sl = liq_prices(entry, lev)
                keep.append({"t": bar["t"], "side": "L", "lev": lev, "liq": ll, "amt": amt})
                keep.append({"t": bar["t"], "side": "S", "lev": lev, "liq": sl, "amt": amt})
        state["prev_oi"] = oi

    limit = LOOKBACK_H * BAR_SEC
    t = bar["t"]
    state["cohorts"] = [c for c in state["cohorts"] if t - c["t"] < limit]
    return {"swept_long": swept_long, "swept_short": swept_short}


def asymmetry(cohorts, px, band=BAND):
    up = dn = 0.0
    top, bot = px * (1 + band), px * (1 - band)
    for c in cohorts:
        x = c["liq"]
        if c["side"] == "S":
            if px < x <= top:
                up += c["amt"]
        elif bot <= x < px:
            dn += c["amt"]
    tot = up + dn
    return {"up": up, "dn": dn, "a": (up - dn) / tot if tot > 0 else None}


def call(a, a_min=A_MIN):
    if a is None:
        return 0
    if a >= a_min:
        return 1
    if a <= -a_min:
        return -1
    return 0


def daily_snapshots(bars, bands=(BAND,), collect_swept=False):
    state = new_state()
    snaps, swept_rows = [], []
    n_oi = 0                                         # oi 가 처음 들어온 봉부터 센 처리 봉 수
    for b in bars:
        r = step(state, b)
        if n_oi > 0 or b.get("oi") is not None:
            n_oi += 1
        if collect_swept:
            swept_rows.append({"t": b["t"], "swept_long": r["swept_long"], "swept_short": r["swept_short"]})
        if (b["t"] + BAR_SEC) % DAY_SEC == 0:
            by = {}
            for bd in bands:
                x = asymmetry(state["cohorts"], b["c"], bd)
                x["call"] = call(x["a"])
                by[bd] = x
            snaps.append({"t": b["t"] + BAR_SEC, "px": b["c"], "ready": n_oi >= LOOKBACK_H, "by_band": by})
    return (snaps, swept_rows) if collect_swept else snaps
