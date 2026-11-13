"""MACD 다이버전스 카드 실데이터 측정 (2026-11-13, 제안함 P36). 트랙 아님, 주문 없음, 표준 라이브러리만.
자료: data/bb_reentry/*.csv (TradingView 일봉 15종목, 2010~2026-11-03)
      data/macd_div/*_4h.csv (TradingView 바이낸스 4시간봉 5코인, 2024-01-01~2026-11-13, 미완성 마지막 봉 제외)
재현: python scripts/macd_div_check.py   (인터넷 불필요)

결과를 보기 전에 고정한 규칙 (2026-11-13, 카드 "주가는 오르는데 MACD 는 못 따라올 때"):
  MACD   : 종가 EMA12 − EMA26, 시그널 EMA9. 앞 100봉은 버림(EMA 안정화).
  고점   : 봉 i 의 고가가 i−5..i+5 중 최고(왼쪽 5봉보다는 엄격히 큼). i+5 봉 종가에 확정(미래 정보 없음).
  저점   : 같은 방식으로 저가 최저.
  하락 다이버전스(카드): 새로 확정된 고점 P2 와 그 직전 고점 P1(간격 5~60봉)
           고가 P2 > P1 그리고 MACD(P2) < MACD(P1).
           P1 도 하락 다이버전스였으면 "3단계"(카드의 고점 3: 세 고점 연속), 아니면 "2단계".
  상승 다이버전스(대칭): 저가 P2 < P1 그리고 MACD(P2) > MACD(P1).
  변형 zero: 하락은 MACD(P1) > 0, 상승은 MACD(P1) < 0 일 때만(상승장/하락장 안의 다이버전스).
  신호 시점 : 확정봉(P2+5) 종가. 진입·청산은 다음 봉 시가.

측정 1 — 신호 뒤 수익(사건 연구): 다음 봉 시가 → H봉 뒤 종가(H = 5, 10, 20).
  기준선 = 같은 시장 모든 봉에서 같은 방식으로 잰 평균. 초과 = 신호 뒤 − 기준선.
  카드가 맞다면 하락 다이버전스 뒤 초과 < 0, 상승 다이버전스 뒤 초과 > 0.
  t = 평균 초과 / (표준편차/√n). 저장소 기준 t 의 절대값 > 3 (BACKTEST_OVERFITTING_REVIEW).
  기간 안정성: 신호를 날짜순 앞·뒤 절반으로 나눠 부호가 같은지 본다.

측정 2 — 보유자가 카드대로 판다면(실전 활용): 처음부터 들고 있다가
  하락 다이버전스 신호 → 다음 봉 시가에 판다(full: 전량, half: 절반, stage3: 3단계만 전량).
  다시 산다 = 종가가 P2 고가를 넘은 다음 봉 시가(다이버전스 실패) 또는 20봉 뒤 시가, 먼저 오는 쪽.
  비용 왕복: 암호화폐 0.20%, 한국 개별주 0.25%(세금 포함), 나머지 0.05%. 판 만큼 비례.
  매수보유와 총수익·최대낙폭 비교.

사후 추가(결과 1차를 본 뒤, 근거 약함 — 흔한 "확인 신호" 해석을 빠뜨리지 않으려고):
  confirm: 하락 다이버전스 확정 뒤 10봉 안에 MACD 가 시그널선 아래로 내려간 봉 종가에 신호. 상승은 반대.
  중앙값 열: 코인 일봉처럼 큰 값 하나가 평균을 끌어가는지 보려고.

측정 3 — 수수료 뒤 단독 매매: 하락 다이버전스 숏(암호화폐만) / 상승 다이버전스 롱, 10봉 보유, 건당 평균.
"""
import csv, os, sys, math, glob
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
DAILY = os.path.join(ROOT, "data", "bb_reentry")
H4 = os.path.join(ROOT, "data", "macd_div")
PIV = 5; GAP_MIN = 5; GAP_MAX = 60; WARM = 100; HS = (5, 10, 20); REBUY = 20

def cost(sym):
    if sym.startswith("BINANCE"): return 0.20
    if sym in ("KRX_005930", "KRX_000660"): return 0.25
    return 0.05

def group(sym, tf):
    if tf == "4h": return "코인 4H"
    if sym.startswith("BINANCE"): return "코인 일봉"
    if sym.startswith("KRX"): return "한국 일봉"
    if sym in ("AMEX_SPY", "AMEX_IWM", "NASDAQ_QQQ"): return "미국 일봉"
    return "금·환율 일봉"

def load(f):
    return [(r["date"], float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"])) for r in csv.DictReader(open(f))]

def ema(x, n):
    a = 2 / (n + 1); out = [x[0]]
    for v in x[1:]: out.append(out[-1] + a * (v - out[-1]))
    return out

def macd(c):
    e12, e26 = ema(c, 12), ema(c, 26)
    return [a - b for a, b in zip(e12, e26)]

def pivots(v, hi=True):
    """확정된 고점(저점) 인덱스. 확정 시점 = i+PIV."""
    out = []
    for i in range(PIV, len(v) - PIV):
        w = v[i - PIV:i + PIV + 1]
        if hi:
            if v[i] == max(w) and v[i] > max(v[i - PIV:i]): out.append(i)
        else:
            if v[i] == min(w) and v[i] < min(v[i - PIV:i]): out.append(i)
    return out

def confirmed(rows, m, sigs, bear=True, within=10):
    """사후 변형: 확정 뒤 within 봉 안에 MACD 가 시그널선을 반대로 건너간 봉으로 신호 시점을 옮긴다."""
    sg = ema(m, 9); out = []
    for c, p1, p2, st in sigs:
        for k in range(c, min(c + within + 1, len(rows))):
            if (bear and m[k] < sg[k] and m[k - 1] >= sg[k - 1]) or (not bear and m[k] > sg[k] and m[k - 1] <= sg[k - 1]):
                out.append((k, p1, p2, st)); break
            if (bear and m[k] < sg[k] and k == c) or (not bear and m[k] > sg[k] and k == c):
                out.append((k, p1, p2, st)); break
    return out

def divergences(rows, m, bear=True, zero=False):
    """[(확정봉 인덱스, P1, P2, 단계)]"""
    h = [r[2] for r in rows]; l = [r[3] for r in rows]
    piv = pivots(h if bear else l, hi=bear)
    price = h if bear else l
    out = []; div_at = set()
    for k in range(1, len(piv)):
        p1, p2 = piv[k - 1], piv[k]
        if p1 < WARM or not (GAP_MIN <= p2 - p1 <= GAP_MAX): continue
        if bear: ok = price[p2] > price[p1] and m[p2] < m[p1] and (not zero or m[p1] > 0)
        else: ok = price[p2] < price[p1] and m[p2] > m[p1] and (not zero or m[p1] < 0)
        if not ok: continue
        stage = 3 if p1 in div_at else 2
        div_at.add(p2)
        out.append((p2 + PIV, p1, p2, stage))
    return out

def fwd(rows, c, H):
    e = c + 1
    if e + H - 1 >= len(rows): return None
    return (rows[e + H - 1][4] / rows[e][1] - 1) * 100

def baseline(rows, H):
    xs = [fwd(rows, c, H) for c in range(WARM, len(rows))]
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs)

def stats(xs):
    n = len(xs)
    if n < 2: return n, (xs[0] if xs else 0.0), 0.0
    mu = sum(xs) / n; sd = math.sqrt(sum((x - mu) ** 2 for x in xs) / (n - 1))
    return n, mu, (mu / (sd / math.sqrt(n)) if sd > 0 else 0.0)

def hold_sim(rows, sigs, mode, cst):
    """보유자 시뮬레이션. 반환 (총수익%, 최대낙폭%, 판 횟수)."""
    sell_at = {}
    for c, p1, p2, st in sigs:
        if mode == "stage3" and st != 3: continue
        sell_at.setdefault(c + 1, rows[p2][2])
    frac_out = 0.5 if mode == "half" else 1.0
    pos = 1.0; eq = 1.0; peak = 1.0; mdd = 0.0; n = 0
    out_ref = None; out_since = None
    start = WARM
    for i in range(start + 1, len(rows)):
        o, c = rows[i][1], rows[i][4]; prev_c = rows[i - 1][4]
        # 시가에서 포지션 변경 (전날 종가→오늘 시가 구간은 기존 포지션)
        eq *= 1 + pos * (o / prev_c - 1)
        if pos < 1.0 and out_ref is not None and (rows[i - 1][4] > out_ref or i - out_since >= REBUY):
            eq *= 1 - (1.0 - pos) * cst / 200; pos = 1.0; out_ref = None
        if pos == 1.0 and i in sell_at:
            eq *= 1 - frac_out * cst / 200; pos = 1.0 - frac_out; out_ref = sell_at[i]; out_since = i; n += 1
        eq *= 1 + pos * (c / o - 1)
        peak = max(peak, eq); mdd = min(mdd, eq / peak - 1)
    return (eq - 1) * 100, mdd * 100, n

def bh(rows):
    eq = 1.0; peak = 1.0; mdd = 0.0
    for i in range(WARM + 1, len(rows)):
        eq *= rows[i][4] / rows[i - 1][4]; peak = max(peak, eq); mdd = min(mdd, eq / peak - 1)
    return (eq - 1) * 100, mdd * 100

def markets():
    out = []
    for f in sorted(glob.glob(os.path.join(DAILY, "*.csv"))):
        out.append((os.path.basename(f)[:-4], "1D", load(f)))
    for f in sorted(glob.glob(os.path.join(H4, "*_4h.csv"))):
        out.append((os.path.basename(f)[:-7], "4h", load(f)))
    return out

GROUPS = ["미국 일봉", "한국 일봉", "금·환율 일봉", "코인 일봉", "코인 4H"]

def main():
    mk = markets()
    # ---------- 측정 1
    print("== 측정 1: 신호 뒤 초과수익(같은 시장 평균 대비, %), 묶음별 ==")
    print("종류 | 묶음 | H | 건수 | 평균 초과 | 중앙값 | t | 앞 절반 | 뒤 절반")
    res = {}
    for kind, bear, zero, st in [("하락 2·3단계", True, False, None), ("하락 3단계만", True, False, 3),
                                 ("하락 zero", True, True, None), ("상승 다이버전스", False, False, None),
                                 ("상승 zero", False, True, None),
                                 ("하락 confirm(사후)", True, False, "c"), ("상승 confirm(사후)", False, False, "c")]:
        for H in HS:
            per = {g: [] for g in GROUPS}
            for sym, tf, rows in mk:
                m = macd([r[4] for r in rows]); b = baseline(rows, H)
                sigs = divergences(rows, m, bear, zero)
                if st == "c": sigs = confirmed(rows, m, sigs, bear)
                for c, p1, p2, s in sigs:
                    if st == 3 and s != 3: continue
                    x = fwd(rows, c, H)
                    if x is not None: per[group(sym, tf)].append((rows[c][0], x - b))
            for g in GROUPS:
                xs = sorted(per[g]); n, mu, t = stats([x for _, x in xs])
                a = stats([x for _, x in xs[:n // 2]])[1] if n > 3 else 0
                z = stats([x for _, x in xs[n // 2:]])[1] if n > 3 else 0
                srt = sorted(x for _, x in xs); med = srt[n // 2] if n else 0
                res[(kind, g, H)] = (n, mu, t)
                print(f"{kind} | {g} | {H} | {n} | {mu:+.2f} | {med:+.2f} | {t:+.2f} | {a:+.2f} | {z:+.2f}")
    # ---------- 측정 2
    print("\n== 측정 2: 보유자가 하락 다이버전스에 판다면 (수수료 뒤 총수익% / 최대낙폭%) ==")
    print("시장 | 봉 | 매수보유 | 전량 매도 | 절반 매도 | 3단계만 전량 | 매도 횟수(전량)")
    tot = {"bh": 0, "full": 0, "half": 0, "stage3": 0}; win = {"full": 0, "half": 0, "stage3": 0}; dd_better = 0
    for sym, tf, rows in mk:
        m = macd([r[4] for r in rows]); sigs = divergences(rows, m, True, False); cst = cost(sym)
        b, bdd = bh(rows); r = {k: hold_sim(rows, sigs, k, cst) for k in ("full", "half", "stage3")}
        for k in r:
            if r[k][0] > b: win[k] += 1
        if r["full"][1] > bdd: dd_better += 1
        print(f"{sym} | {tf} | {b:+.0f} / {bdd:.0f} | {r['full'][0]:+.0f} / {r['full'][1]:.0f} | "
              f"{r['half'][0]:+.0f} / {r['half'][1]:.0f} | {r['stage3'][0]:+.0f} / {r['stage3'][1]:.0f} | {r['full'][2]}")
    print(f"매수보유보다 총수익이 큰 시장 수 (20개 중): 전량 {win['full']}, 절반 {win['half']}, 3단계만 {win['stage3']}. "
          f"전량 매도가 최대낙폭을 줄인 시장 {dd_better}")
    # ---------- 측정 3
    print("\n== 측정 3: 수수료 뒤 단독 매매, 10봉 보유, 건당 평균% (기준선 빼지 않은 절대 수익) ==")
    print("묶음 | 하락 숏(코인만) 건수·평균·플러스비율 | 상승 롱 건수·평균·플러스비율")
    for g in GROUPS:
        sh, lo = [], []
        for sym, tf, rows in mk:
            if group(sym, tf) != g: continue
            m = macd([r[4] for r in rows]); cst = cost(sym)
            if sym.startswith("BINANCE"):
                for c, *_ in divergences(rows, m, True):
                    x = fwd(rows, c, 10)
                    if x is not None: sh.append(-x - cst)
            for c, *_ in divergences(rows, m, False):
                x = fwd(rows, c, 10)
                if x is not None: lo.append(x - cst)
        def f(xs):
            if not xs: return "-"
            return f"{len(xs)}건 {sum(xs)/len(xs):+.2f}% {100*sum(1 for x in xs if x>0)/len(xs):.0f}%"
        print(f"{g} | {f(sh)} | {f(lo)}")
    return res

if __name__ == "__main__":
    main()
