"""Breaker Block 매매법 카드 실데이터 측정 (2026-11-13, 제안함 P36). 트랙 아님, 주문 없음, 표준 라이브러리만.
자료: data/breaker/*.csv (TradingView, 2026-11-13 받음). 4시간봉 2023~24 → 2026-11, 1시간봉 2026-01 → 2026-11.
재현: python scripts/breaker_check.py
결과를 보기 전에 고정한 규칙 (카드 = 숏. 롱은 가격을 뒤집은 거울 규칙):
  스윙     : 좌우 N=3봉 프랙탈 고점·저점. 피벗 뒤 N봉이 닫혀야 "확인"(미래 정보 금지)
  ① 스윕   : 새 스윙고점 H2 > 직전 스윙고점 H1. 둘 사이 가장 낮은 스윙저점 = HL
  OB       : H1~HL 구간에서 HL 쪽부터 찾은 마지막 음봉(종가<시가). 구간 = 그 봉의 [저가, 고가]
  ② MSS    : H2 뒤 30봉 안에 종가 < HL 저가. 그 전에 H2 위로 가면 무효
  ③ 브레이커: OB 구간이 공급 구간이 됨
  ④ 진입   : MSS 뒤(그리고 H2 확인 뒤) 40봉 안에 고가가 구간 하단에 닿고(되돌림),
             닿은 봉부터 10봉 안에 종가 < 구간 하단(반응 확인) → 다음 봉 시가 숏.
             확인 전 종가 > 구간 상단 이거나 고가 > H2 면 무효
  손절     : max(구간 상단, 닿은 봉~확인 봉 최고가). 시가가 이미 손절 위면 건너뜀
  목표     : 카드 빈칸 → 2R(손절폭의 2배). 50봉 지나면 종가 청산. 같은 봉에 둘 다면 손절 먼저(보수적)
  비용     : 왕복 — 암호화폐 0.12%(선물 시장가 0.05%×2 + 미끄러짐), 금·유로달러·나스닥 0.04%
  한 종목·한 방향에 한 포지션
변형(비교용, 함께 고정):
  touch : 확인 없이 구간 하단에 닿는 순간 지정가 진입, 손절 = 구간 상단
  tp1 / tp3 : 목표 1R / 3R
  n5    : 프랙탈 N=5
기준선(잡음): 같은 종목·방향·손절폭·목표·50봉으로 아무 봉에서나 진입한 평균 R (신호당 100회 추첨, 씨앗 고정).
             "카드 − 기준선" 이 이 패턴만의 몫이다. 상승장에서 숏이 지는 것까지 빼 준다.
"""
import csv, glob, math, os, random, sys

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "..", "data", "breaker")
MSS_WIN, TOUCH_WIN, CONF_WIN, MAX_HOLD = 30, 40, 10, 50


def cost(sym):
    return 0.04 if sym.split("_")[0] in ("xauusd", "eurusd", "nq") else 0.12


def load(f):
    r = list(csv.DictReader(open(f)))
    return ([int(x["t"]) for x in r], [float(x["o"]) for x in r], [float(x["h"]) for x in r],
            [float(x["l"]) for x in r], [float(x["c"]) for x in r])


def flip(o, h, l, c):  # 롱 = 가격을 뒤집어 같은 숏 규칙
    return [-x for x in o], [-x for x in l], [-x for x in h], [-x for x in c]


def pivots(h, l, n):
    sh, sl = [], []  # (확인 봉, 피벗 봉)
    for i in range(n, len(h) - n):
        if all(h[i] > h[j] for j in range(i - n, i)) and all(h[i] >= h[j] for j in range(i + 1, i + n + 1)):
            sh.append((i + n, i))
        if all(l[i] < l[j] for j in range(i - n, i)) and all(l[i] <= l[j] for j in range(i + 1, i + n + 1)):
            sl.append((i + n, i))
    return sh, sl


def simulate(o, h, l, c, e, stop, tp_r, limit_fill=False):
    """숏: e 봉 시가 진입. 반환 (R 총, 보유봉, 이유).
    limit_fill: e 봉 중간에 지정가 체결 — 그 봉의 저가는 체결 전일 수 있으므로 그 봉에서는 손절만 본다"""
    entry = o[e]; risk = stop - entry; tp = entry - tp_r * risk
    for k in range(e, min(len(o), e + MAX_HOLD)):
        if h[k] >= stop:
            px = max(o[k], stop) if k > e else stop
            return (entry - px) / risk, k - e + 1, "stop"
        if limit_fill and k == e:
            continue
        if l[k] <= tp:
            px = min(o[k], tp) if k > e else tp
            return (entry - px) / risk, k - e + 1, "tp"
    k = min(len(o), e + MAX_HOLD) - 1
    if k < e + MAX_HOLD - 1:
        return None  # 자료 끝: 미완료 거래는 빼다
    return (entry - c[k]) / risk, k - e + 1, "time"


def setups(o, h, l, c, n, touch):
    sh, sl = pivots(h, l, n)
    out = []
    for a in range(1, len(sh)):
        t2, p2 = sh[a]; p1 = sh[a - 1][1]
        if h[p2] <= h[p1]:
            continue
        lows = [p for (_, p) in sl if p1 < p < p2]
        if not lows:
            continue
        pL = min(lows, key=lambda p: l[p])
        ob = next((i for i in range(pL, p1, -1) if c[i] < o[i]), None)
        if ob is None:
            continue
        zlo, zhi = l[ob], h[ob]
        m = None
        for k in range(p2 + 1, min(len(c), p2 + 1 + MSS_WIN)):
            if h[k] > h[p2]:
                break
            if c[k] < l[pL]:
                m = k; break
        if m is None:
            continue
        start = max(m, t2) + 1
        for k in range(start, min(len(c), start + TOUCH_WIN)):
            if h[k] < zlo:  # 아직 안 닿음 (닿기 전 무효 조건은 h<구간하단이라 생길 수 없음)
                continue
            # 닿은 봉: 그 봉의 종가·고가로 진입 여부를 고르면 미래 정보 — 여기서는 보지 않는다
            if touch:  # 지정가 = 구간 하단 (갭으로 시가가 위면 시가)
                fill = max(o[k], zlo)
                out.append(dict(kind="touch", e=k, fill=fill, stop=zhi, sig=k, h2=p2, hl=pL, ob=ob, mss=m, zlo=zlo, zhi=zhi))
                break
            hi = None; ent = None
            for j in range(k, min(len(c) - 1, k + CONF_WIN)):
                hi = h[j] if hi is None else max(hi, h[j])
                if c[j] > zhi or h[j] > h[p2]:
                    break
                if c[j] < zlo:
                    ent = j; break
            if ent is not None:
                out.append(dict(kind="conf", e=ent + 1, stop=max(zhi, hi), sig=ent, h2=p2, hl=pL, ob=ob, mss=m, zlo=zlo, zhi=zhi, touch=k))
            break
    return out


def run(series, n=3, touch=False, tp_r=2.0, rng=None):
    t, o, h, l, c, fee = series
    trades = []; busy = -1
    for s in sorted(setups(o, h, l, c, n, touch), key=lambda s: s["e"]):
        e = s["e"]
        if e <= busy or e >= len(o):
            continue
        if touch:
            entry = s["fill"]; stop = s["stop"]
            if entry >= stop:
                continue
            o2 = o[:]; o2[e] = entry  # 체결가를 진입가로
            res = simulate(o2, h, l, c, e, stop, tp_r, limit_fill=True)
        else:
            entry = o[e]; stop = s["stop"]
            if entry >= stop:
                continue
            res = simulate(o, h, l, c, e, stop, tp_r)
        if res is None:
            continue
        r, held, why = res
        risk_pct = (stop - entry) / abs(entry) * 100
        fee_r = fee / risk_pct
        base = None
        if rng is not None:
            acc = []
            for _ in range(100):
                q = rng.randrange(50, len(o) - MAX_HOLD - 1)
                st = o[q] + (stop - entry) / abs(entry) * abs(o[q])
                rr = simulate(o, h, l, c, q, st, tp_r)
                if rr:
                    acc.append(rr[0] - fee_r)
            base = sum(acc) / len(acc)
        trades.append(dict(t=t[e], r=r - fee_r, pct=(r - fee_r) * risk_pct, risk=risk_pct, held=held, why=why, base=base, s=s))
        busy = e + held - 1
    return trades


def stats(tr):
    if not tr:
        return dict(n=0)
    rs = [x["r"] for x in tr]; n = len(rs); m = sum(rs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in rs) / (n - 1)) if n > 1 else 0
    w = [x for x in rs if x > 0]; ls = [x for x in rs if x <= 0]
    pf = sum(w) / -sum(ls) if ls and sum(ls) < 0 else float("inf")
    b = [x["base"] for x in tr if x["base"] is not None]
    out = dict(n=n, r=m, t=m / (sd / math.sqrt(n)) if sd else 0, win=len(w) / n, pf=pf,
               pct=sum(x["pct"] for x in tr) / n, risk=sum(x["risk"] for x in tr) / n)
    if b:
        ex = [x["r"] - x["base"] for x in tr]; me = sum(ex) / n
        se = math.sqrt(sum((x - me) ** 2 for x in ex) / (n - 1)) / math.sqrt(n) if n > 1 else 0
        out.update(base=sum(b) / len(b), ex=me, ex_t=me / se if se else 0)
    return out


def fmt(name, s):
    if not s.get("n"):
        return f"{name:<22} 0건"
    x = f"{name:<22} {s['n']:>4}건 R {s['r']:+.3f} t {s['t']:+.2f} 승 {s['win']*100:4.0f}% PF {s['pf']:.2f} 건당 {s['pct']:+.2f}% (손절폭 {s['risk']:.2f}%)"
    if "base" in s:
        x += f" | 기준선 R {s['base']:+.3f} 초과 {s['ex']:+.3f} (t {s['ex_t']:+.2f})"
    return x


def load_all():
    out = {}
    for f in sorted(glob.glob(os.path.join(D, "*.csv"))):
        sym = os.path.basename(f)[:-4]
        t, o, h, l, c = load(f)
        out[sym] = {"short": (t, o, h, l, c, cost(sym)), "long": (t, *flip(o, h, l, c), cost(sym))}
    return out


def main():
    data = load_all()
    variants = [("card", {}), ("touch", {"touch": True}), ("tp1", {"tp_r": 1.0}), ("tp3", {"tp_r": 3.0}), ("n5", {"n": 5})]
    allres = {}
    for vname, kw in variants:
        for sym, dd in data.items():
            for side in ("short", "long"):
                rng = random.Random(f"{sym}{side}{vname}")
                allres[(vname, sym, side)] = run(dd[side], rng=rng, **kw)
    groups = {"코인 4H": lambda s: s.endswith("4h") and cost(s) > 0.1, "코인 1H": lambda s: s.endswith("1h") and cost(s) > 0.1,
              "금·유로·나스닥 4H": lambda s: s.endswith("4h") and cost(s) < 0.1, "금·유로·나스닥 1H": lambda s: s.endswith("1h") and cost(s) < 0.1}
    print("== 1. 카드 규칙(숏), 종목별 ==")
    for sym in data:
        print(fmt(sym, stats(allres[("card", sym, "short")])))
    print("\n== 2. 카드 규칙(숏), 시장군 ==")
    for g, fn in groups.items():
        print(fmt(g, stats([x for s in data if fn(s) for x in allres[("card", s, "short")]])))
    print(fmt("전체 숏", stats([x for s in data for x in allres[("card", s, "short")]])))
    print("\n== 3. 거울(롱 브레이커), 시장군 ==")
    for g, fn in groups.items():
        print(fmt(g, stats([x for s in data if fn(s) for x in allres[("card", s, "long")]])))
    print(fmt("전체 롱", stats([x for s in data for x in allres[("card", s, "long")]])))
    print("\n== 4. 변형 (숏+롱 전체) ==")
    for vname, _ in variants:
        print(fmt(vname, stats([x for s in data for side in ("short", "long") for x in allres[(vname, s, side)]])))
    print("\n== 5. 앞뒤 절반 (카드, 숏+롱, 시장군별 기간 중간 시각 기준) ==")
    for g, fn in groups.items():
        tr = sorted([x for s in data if fn(s) for side in ("short", "long") for x in allres[("card", s, side)]], key=lambda x: x["t"])
        if not tr:
            continue
        mid = (tr[0]["t"] + tr[-1]["t"]) / 2
        print(fmt(g + " 앞", stats([x for x in tr if x["t"] < mid])))
        print(fmt(g + " 뒤", stats([x for x in tr if x["t"] >= mid])))
    print("\n== 6. 청산 이유 (카드, 숏+롱) ==")
    tr = [x for s in data for side in ("short", "long") for x in allres[("card", s, side)]]
    for why in ("stop", "tp", "time"):
        k = [x for x in tr if x["why"] == why]
        print(f"{why:<5} {len(k)}건 ({len(k)/len(tr)*100:.0f}%)")
    print(f"평균 보유 {sum(x['held'] for x in tr)/len(tr):.1f}봉")
    if "--last" in sys.argv:  # 카드 그림용: 가장 최근 BTC 4H 숏 신호
        x = allres[("card", "btc_4h", "short")][-1]
        print("\nLAST", x["t"], {k: v for k, v in x["s"].items()}, round(x["r"], 3), x["why"])
    return allres


if __name__ == "__main__":
    main()
