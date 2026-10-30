"""5·20·60 이평선 수렴 → 장대양봉 돌파 → 첫 눌림(20일선) 롱. 규칙은 결과 보기 전에 고정(2026-10-29).
손절 = 수렴 박스 저점. 익절 = 절반 5일선 종가 이탈, 나머지 20일선 종가 이탈. 롱만."""
import csv, sys, glob, os, statistics as st

# ---- 고정 규칙 (ATR 단위라 시장 불문 동일) ----
MA = (5, 20, 60)
ATR_N = 20
CONV_BARS = 5          # 수렴: 마지막 5봉 연속으로 세 선 폭 <= ATR*CONV_SPREAD
CONV_SPREAD = 1.0
BOX_LEN = 20           # 박스: 최근 20봉 고저 폭 <= ATR*BOX_RANGE
BOX_RANGE = 6.0
BODY_MIN = 1.5         # 장대양봉: 몸통 >= ATR*1.5, 종가 봉범위 윗 30%, 종가 > 세 선 모두, 전일 종가는 세 선 모두 위가 아님
CLOSE_POS = 0.7
PULL_WAIT = 20         # 돌파 뒤 20봉 안에 저가 <= MA20 첫 봉 종가 진입. 그 전에 종가 < 박스저점이면 취소
COST = 0.10            # 왕복 수수료+슬리피지 % (진입·청산 합)
MAX_HOLD = 120

def load(path):
    rows = list(csv.DictReader(open(path)))
    out = []
    for r in rows:
        g = lambda *ks: next(float(r[k]) for k in ks if k in r)
        out.append((r["date"], g("open","o"), g("high","h"), g("low","l"), g("close","c")))
    return out

def sma(xs, n):
    out = [None]*len(xs); s = 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n: s -= xs[i-n]
        if i >= n-1: out[i] = s/n
    return out

def atr(bars, n=ATR_N):
    tr = [bars[0][2]-bars[0][3]] + [max(h-l, abs(h-bars[i-1][4]), abs(l-bars[i-1][4])) for i,(d,o,h,l,c) in enumerate(bars) if i>0]
    return sma(tr, n)

def backtest(bars, name):
    c = [b[4] for b in bars]; m = {n: sma(c, n) for n in MA}; a = atr(bars)
    trades = []; i = 65; stats = {"conv":0,"box":0,"brk":0,"cancel":0,"nopull":0,"entry":0}
    while i < len(bars):
        d,o,h,l,cl = bars[i]
        if any(m[n][i-1] is None for n in MA) or a[i-1] is None or a[i-1] <= 0: i += 1; continue
        # 수렴: i-1 까지 5봉 연속
        conv = all((max(m[n][j] for n in MA) - min(m[n][j] for n in MA)) <= CONV_SPREAD*a[j] for j in range(i-CONV_BARS, i))
        if not conv: i += 1; continue
        stats["conv"] += 1
        box_hi = max(b[2] for b in bars[i-BOX_LEN:i]); box_lo = min(b[3] for b in bars[i-BOX_LEN:i])
        if box_hi - box_lo > BOX_RANGE*a[i-1]: i += 1; continue
        stats["box"] += 1
        top = max(m[n][i] for n in MA); prev_top = max(m[n][i-1] for n in MA)
        big = (cl-o) >= BODY_MIN*a[i-1] and h > l and (cl-l)/(h-l) >= CLOSE_POS and cl > top and not (bars[i-1][4] > prev_top)
        if not big: i += 1; continue
        stats["brk"] += 1
        # 첫 눌림 대기
        ent = None
        for j in range(i+1, min(i+1+PULL_WAIT, len(bars))):
            if bars[j][4] < box_lo: stats["cancel"] += 1; break
            if bars[j][3] <= m[20][j]:
                ent = j; break
        if ent is None:
            if j >= i+PULL_WAIT: stats["nopull"] += 1
            i = j+1; continue
        stats["entry"] += 1
        ep = bars[ent][4]; stop = box_lo; R = ep - stop
        if R <= 0: i = ent+1; continue
        half_done = False; armed5 = False; pnl_pct = 0.0; k = ent+1; exit_reason = "time"; last = ent
        while k < len(bars) and k <= ent+MAX_HOLD:
            dk,ok,hk,lk,ck = bars[k]
            if lk < stop:
                px = min(ok, stop); frac = 0.5 if half_done else 1.0
                pnl_pct += frac*(px/ep-1)*100; exit_reason = "stop"; last = k; break
            if ck > m[5][k]: armed5 = True
            if not half_done and armed5 and ck < m[5][k]:
                pnl_pct += 0.5*(ck/ep-1)*100; half_done = True
            if half_done and ck < m[20][k]:
                pnl_pct += 0.5*(ck/ep-1)*100; exit_reason = "ma20"; last = k; break
            k += 1
        else:
            k = min(k, len(bars)-1); frac = 0.5 if half_done else 1.0
            pnl_pct += frac*(bars[k][4]/ep-1)*100; last = k
        pnl_pct -= COST
        trades.append({"m":name,"entry":bars[ent][0],"exit":bars[last][0],"ep":ep,"stop":stop,"pnl":pnl_pct,"R":pnl_pct/((R/ep)*100),"why":exit_reason,"hold":last-ent})
        i = last+1
    return trades, stats

def summarize(trades, label):
    if not trades: print(f"{label}: 0건"); return
    p = [t["pnl"] for t in trades]; w = [x for x in p if x>0]; lo = [x for x in p if x<=0]
    pf = (sum(w)/-sum(lo)) if lo and sum(lo)<0 else float("inf")
    rs = [t["R"] for t in trades]
    print(f"{label}: {len(trades)}건 승률 {len(w)/len(p)*100:.0f}% 평균 {st.mean(p):+.2f}% 중앙 {st.median(p):+.2f}% 합계 {sum(p):+.1f}% PF {pf:.2f} 평균R {st.mean(rs):+.2f} 손절 {sum(t['why']=='stop' for t in trades)} 보유중앙 {st.median(t['hold'] for t in trades):.0f}봉")

if __name__ == "__main__":
    groups = {"ETF(미국,20년)": sorted(glob.glob("data/etf/*.csv")),
              "업비트(3.3년)": sorted(glob.glob("tests/fixtures/turtle/upbit_*.csv")),
              "OKX(3.3년)": sorted(glob.glob("tests/fixtures/turtle/okx_*.csv"))}
    allt = []
    for g, files in groups.items():
        gt = []; gs = {}
        for f in files:
            name = os.path.basename(f).replace(".csv","")
            t, s = backtest(load(f), name); gt += t
            for k,v in s.items(): gs[k] = gs.get(k,0)+v
            summarize(t, f"  {name}")
        print("  깔때기:", gs); summarize(gt, "= "+g); allt += gt; print()
    summarize(allt, "== 전체")
    if "--trades" in sys.argv:
        for t in allt: print(t)
