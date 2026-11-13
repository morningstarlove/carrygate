"""BNF(고테가와 다카시) 25일 이격도 역추세 매매 실데이터 측정 (2026-11-13, 제안함 P35). 트랙 아님, 주문 없음, 표준 라이브러리만.
자료: data/bnf/*.csv.gz (TradingView 일봉, 2026-11-13 받음, 분할 반영). 재현: python scripts/bnf_check.py
사용자 공유 SNS 카드: "25일 이동평균선(-15%~-35%), 사업 악화 종목 X, RSI=과매도, 볼린저밴드=과도함, 볼륨 레이시오=거래량 강도.
그림: 25일선 아래로 크게 벌어진 과매도 → 한 번 반등을 확인하고 BUY → 25일선에 닿으면 EXIT."

결과를 보기 전에 고정한 규칙 (이 머리말은 자료를 받기 전에 커밋했다):
  이격도 : dev = 종가 / 25일 단순평균 - 1, 일봉
  준비   : 종가 dev <= -T (기본 T = 20%, 카드 범위 15~35 의 아래쪽 가운데)
  확인   : 준비일 다음 1~5거래일 안에 '전날보다 높은 종가'가 처음 나온 날 (그림의 과매도 → BUY 한 칸)
           5일 안에 없으면 준비 무효(그 뒤 다시 dev <= -T 면 새 준비)
  진입   : 확인일 다음 날 시가
  청산   : 종가 >= 25일 평균 이면 그 종가 (그림의 EXIT). 20거래일 지나면 종가. 손절 없음(카드에 없음)
  비용   : 왕복 — 한국 0.25%(거래세 포함), 일본 0.10%, 미국 0.05%, 암호화폐 0.20%
  한 종목에 한 포지션. 마지막(미완성일 수 있는) 봉은 신호에 쓰지 않음.
변형(비교용, 함께 고정. 결과를 보고 더하지 않는다):
  T15 T25 T30 T35 : 문턱만 바꿈
  nowait : 확인 없이 준비일 다음 날 시가 진입 (그림의 '과매도' 점에서 바로 사기)
  rsi    : 기본 + 준비일 RSI(14, 와일더) <= 30
  bb     : 기본 + 준비일 종가 < 볼린저 하단(20일, 2배)
  vr     : 기본 + 준비일 볼륨 레이시오(25일, 상승일 거래량+보합 절반 / 하락일+보합 절반 x100) <= 70
  all    : 기본 + rsi + bb + vr (카드 도구 네 개 전부)
  stop   : 기본 + 손절 = 준비일~확인일 최저가 (장중 닿으면 손절가, 갭이면 시가. 같은 날은 손절 먼저)
기준선: 같은 종목 매수보유의 하루 평균 수익 x 보유일수 (같은 기간 그냥 들고 있었으면). 초과 = 건당 순수익 - 기준선.
t: 건당 초과의 평균 / 표준오차. 폭락장에 신호가 몰리므로 '진입 월' 묶음 t(월별 평균의 t)를 함께 본다 — 판단은 월 묶음 t.
판단선(저장소 관례): 월 묶음 t > 3 이고 앞·뒤 기간(2005~2014 / 2015~) 모두 초과 플러스여야 '후보'.
한계: 종목 목록은 2026-11-13 시가총액 상위(살아남은 승자) — 망한 종목이 빠져 깊은 하락 매수가 실제보다 좋아 보인다(생존 편향).
      카드 2번 '사업 악화 종목 제외'는 가격 자료로 재현 불가 — 생존 편향이 그 역할을 '미래를 보고' 대신하는 셈이다.
"""
import csv, gzip, glob, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
D = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "data", "bnf")

GROUPS = (("일본", "TSE_"), ("한국", "KRX_"), ("미국", ("NASDAQ_", "NYSE_")), ("암호화폐", "BINANCE_"))
COST = {"일본": 0.10, "한국": 0.25, "미국": 0.05, "암호화폐": 0.20}
VARIANTS = ("base", "T15", "T25", "T30", "T35", "nowait", "rsi", "bb", "vr", "all", "stop")
SPLIT = "2015-01-01"


def group_of(sym):
    for g, p in GROUPS:
        if sym.startswith(p):
            return g
    return None


def load(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        rows = [(r["date"], float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"]), float(r["v"] or 0))
                for r in csv.DictReader(f)]
    return rows[:-1]  # 마지막 봉은 미완성일 수 있어 뺀다


def sma(x, n):
    out = [None] * len(x); s = 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n: s -= x[i - n]
        if i >= n - 1: out[i] = s / n
    return out


def bb_lower(c, n=20, k=2.0):
    out = [None] * len(c)
    for i in range(n - 1, len(c)):
        w = c[i - n + 1:i + 1]; m = sum(w) / n
        out[i] = m - k * math.sqrt(sum((x - m) ** 2 for x in w) / n)
    return out


def rsi(c, n=14):
    out = [None] * len(c)
    if len(c) <= n: return out
    g = [max(c[i] - c[i - 1], 0) for i in range(1, len(c))]
    l = [max(c[i - 1] - c[i], 0) for i in range(1, len(c))]
    ag = sum(g[:n]) / n; al = sum(l[:n]) / n
    for i in range(n, len(c)):
        if i > n:
            ag = (ag * (n - 1) + g[i - 1]) / n; al = (al * (n - 1) + l[i - 1]) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def vratio(c, v, n=25):
    out = [None] * len(c)
    for i in range(n, len(c)):
        up = dn = eq = 0.0
        for j in range(i - n + 1, i + 1):
            if c[j] > c[j - 1]: up += v[j]
            elif c[j] < c[j - 1]: dn += v[j]
            else: eq += v[j]
        den = dn + eq / 2
        out[i] = None if den == 0 else (up + eq / 2) / den * 100
    return out


def run(rows, mode, cost):
    d = [r[0] for r in rows]; o = [r[1] for r in rows]; h = [r[2] for r in rows]
    l = [r[3] for r in rows]; c = [r[4] for r in rows]; v = [r[5] for r in rows]
    m25 = sma(c, 25); lo = bb_lower(c); rs = rsi(c); vr = vratio(c, v)
    T = {"T15": 0.15, "T25": 0.25, "T30": 0.30, "T35": 0.35}.get(mode, 0.20)
    trades = []; i = 25; n = len(c)
    while i < n - 1:
        if m25[i] is None or c[i] / m25[i] - 1 > -T:
            i += 1; continue
        ok = True
        if mode in ("rsi", "all"): ok = ok and rs[i] is not None and rs[i] <= 30
        if mode in ("bb", "all"): ok = ok and lo[i] is not None and c[i] < lo[i]
        if mode in ("vr", "all"): ok = ok and vr[i] is not None and vr[i] <= 70
        if not ok:
            i += 1; continue
        setup = i
        if mode == "nowait":
            trig = setup
        else:
            trig = None
            for j in range(setup + 1, min(setup + 6, n - 1)):
                if c[j] > c[j - 1]:
                    trig = j; break
            if trig is None:
                i = setup + 1; continue
        e = trig + 1
        if e >= n:
            break
        stop = min(l[setup:trig + 1]) if mode == "stop" else None
        px = o[e]
        if stop is not None and px <= stop:
            i = e; continue
        x = None; xp = None
        for k in range(e, min(e + 20, n)):
            if stop is not None and l[k] <= stop:
                x = k; xp = min(stop, o[k]) if k > e else stop; break
            if m25[k] is not None and c[k] >= m25[k]:
                x = k; xp = c[k]; break
        if x is None:
            x = min(e + 19, n - 1); xp = c[x]
        trades.append({"date": d[e], "days": x - e + 1, "ret": (xp / px - 1) * 100 - cost})
        i = x + 1
    return trades


def bh_daily(rows):
    c = [r[4] for r in rows]
    return (c[-1] / c[0]) ** (1 / max(len(c) - 1, 1)) * 100 - 100 if len(c) > 1 else 0.0


def stats(xs):
    if not xs: return None
    n = len(xs); m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)) if n > 1 else 0.0
    return {"n": n, "mean": m, "t": m / (sd / math.sqrt(n)) if sd > 0 else 0.0}


def month_t(trs):
    by = {}
    for t in trs: by.setdefault(t["date"][:7], []).append(t["ex"])
    s = stats([sum(v) / len(v) for v in by.values()])
    return (s["t"], s["n"]) if s else (0.0, 0)


def summarize(trs):
    s = stats([t["ex"] for t in trs]) or {"n": 0, "mean": 0.0, "t": 0.0}
    r = stats([t["ret"] for t in trs]) or {"mean": 0.0}
    wins = sum(1 for t in trs if t["ret"] > 0)
    gp = sum(t["ret"] for t in trs if t["ret"] > 0); gl = -sum(t["ret"] for t in trs if t["ret"] < 0)
    mt, mn = month_t(trs)
    a = [t["ex"] for t in trs if t["date"] < SPLIT]; b = [t["ex"] for t in trs if t["date"] >= SPLIT]
    return {"n": s["n"], "ret": r["mean"], "ex": s["mean"], "t": s["t"], "mt": mt, "months": mn,
            "win": wins / len(trs) * 100 if trs else 0.0, "pf": gp / gl if gl > 0 else float("inf"),
            "days": sum(t["days"] for t in trs) / len(trs) if trs else 0.0,
            "ex_a": sum(a) / len(a) if a else None, "na": len(a), "ex_b": sum(b) / len(b) if b else None, "nb": len(b)}


def collect(d, m):
    """폴더 d 의 모든 종목에 변형 m 을 돌려 시장군별 매매 목록과 종목별 요약을 돌려준다."""
    res = {g: [] for g, _ in GROUPS}; per = {}
    for f in sorted(glob.glob(os.path.join(d, "*.csv.gz"))):
        sym = os.path.basename(f)[:-7]; g = group_of(sym)
        if g is None: continue
        rows = load(f)
        if len(rows) < 60: continue
        bh = bh_daily(rows)
        trs = run(rows, m, COST[g])
        for t in trs: t["ex"] = t["ret"] - bh * t["days"]
        res[g].extend(trs); per[sym] = (g, summarize(trs), rows[0][0])
    return res, per


def line(s):
    f2 = lambda x: "—" if x is None else "%+.2f" % x
    return ("%4d건 순 %+.2f%% 초과 %+.2f%% t %+.2f 월t %+.2f(%d개월) 승률 %.0f%% PF %.2f 보유 %.1f일 | 앞 %s(%d) 뒤 %s(%d)"
            % (s["n"], s["ret"], s["ex"], s["t"], s["mt"], s["months"], s["win"], s["pf"], s["days"],
               f2(s["ex_a"]), s["na"], f2(s["ex_b"]), s["nb"]))


def without_top_months(trs, k=5):
    by = {}
    for t in trs: by.setdefault(t["date"][:7], []).append(t)
    top = sorted(by, key=lambda m: -sum(x["ex"] for x in by[m]))[:k]
    return [t for t in trs if t["date"][:7] not in top], top


def report(d, title):
    print("\n# %s (%s)" % (title, os.path.relpath(d)))
    allres = {m: collect(d, m) for m in VARIANTS}
    print("## 시장군 x 변형 (건당 순수익 / 같은 일수 매수보유 대비 초과 / 월 묶음 t / 앞(~2014)·뒤(2015~) 초과)")
    for g, _ in GROUPS:
        for m in VARIANTS:
            trs = allres[m][0][g]
            print("%s %-6s %s" % (g, m, line(summarize(trs)) if trs else "   0건"))
    print("## 기본 규칙 중앙값·집중도 (사후 기술 통계, 판단 기준 아님)")
    for g, _ in GROUPS:
        trs = allres["base"][0][g]
        if not trs: continue
        r = sorted(t["ret"] for t in trs); rest, top = without_top_months(trs)
        rs = summarize(rest)
        print("%s 순수익 중앙값 %+.2f%% | 초과가 큰 5개월 %s 빼면 %d건 초과 %+.2f%% 월t %+.2f"
              % (g, r[len(r) // 2], ",".join(top), rs["n"], rs["ex"], rs["mt"]))
    print("## 종목별 기본 규칙")
    per = allres["base"][1]
    for sym in sorted(per, key=lambda k: (per[k][0], k)):
        g, s, first = per[sym]
        print("%s %-16s 시작 %s %3d건 초과 %s t %s" % (g, sym, first, s["n"],
              "%+.2f" % s["ex"] if s["n"] else "—", "%+.2f" % s["t"] if s["n"] else "—"))
    return allres


def main():
    dirs = sys.argv[1:] or [D, os.path.join(HERE, "..", "data", "bnf_losers")]
    titles = ["본 표본: 2026-11-13 시가총액 상위 (생존 편향 위쪽)", "비교군: 10년 수익률 최하위 (사업 악화, 편향 아래쪽)"]
    got = [report(d, titles[i] if i < 2 else d) for i, d in enumerate(dirs) if glob.glob(os.path.join(d, "*.csv.gz"))]
    if len(got) < 2: return
    print("\n# 주식 전체 묶음 (본 표본 + 비교군, 암호화폐 제외)")
    for m in VARIANTS:
        trs = [t for a in got for g, _ in GROUPS if g != "암호화폐" for t in a[m][0][g]]
        rest, _ = without_top_months(trs)
        print("%-6s %s | 상위5개월 빼면 초과 %+.2f%% 월t %+.2f" % (m, line(summarize(trs)), summarize(rest)["ex"], summarize(rest)["mt"]))


if __name__ == "__main__":
    main()
