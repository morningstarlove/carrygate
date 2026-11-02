"""CIS 원칙 글(bullstory, 2026-10-29)의 진입 모양을 숫자로 고정해 돌려 본다. 규칙은 결과 보기 전에 정함.
고점 상승 + 저점 상승 → 새 저점(L2) 확정 뒤 반등 고점 종가 돌파에 진입 → 손절 L2 − 0.5% → 새 피벗 저점마다 손절선 올림(래칫), 목표가 없음.
손절선까지 ATR×3 보다 멀면 패스(글: "너무 멀면 패스" — 숫자는 우리가 정함). 롱만. 왕복 비용 0.10%."""
import csv, glob, os, sys, statistics as st
sys.path.insert(0, os.path.dirname(__file__))
from ma3_convergence_check import load, atr, summarize

PIVOT_K = 3; BUF = 0.005; MAX_RISK_ATR = 3.0; COST = 0.10; MAX_HOLD = 250

def pivots(bars, k=PIVOT_K):
    hi, lo = [], []
    for i in range(k, len(bars)-k):
        if all(bars[i][2] > bars[j][2] for j in range(i-k, i+k+1) if j != i): hi.append(i)
        if all(bars[i][3] < bars[j][3] for j in range(i-k, i+k+1) if j != i): lo.append(i)
    return hi, lo

def backtest(bars, name):
    a = atr(bars); hi, lo = pivots(bars); trades = []; i = 60; stats = {"setup":0,"far":0,"nobreak":0,"entry":0}
    while i < len(bars):
        # i 시점에 확정된(i-K 이전) 피벗들
        H = [p for p in hi if p + PIVOT_K <= i]; L = [p for p in lo if p + PIVOT_K <= i]
        if len(H) < 2 or len(L) < 2: i += 1; continue
        L2, L1, H2, H1 = L[-1], L[-2], H[-1], H[-2]
        ok = (L2 + PIVOT_K == i) and bars[L2][3] > bars[L1][3] and bars[H2][2] > bars[H1][2] and H2 < L2 and H2 > L1
        if not ok: i += 1; continue
        stats["setup"] += 1
        stop = bars[L2][3] * (1 - BUF); bounce_hi = max(b[2] for b in bars[L2+1:i+1])
        ent = None
        for j in range(i, min(i+20, len(bars))):
            if bars[j][3] < stop: break
            if bars[j][4] > bounce_hi: ent = j; break
        if ent is None: stats["nobreak"] += 1; i += 1; continue
        ep = bars[ent][4]
        if a[ent] and ep - stop > MAX_RISK_ATR * a[ent]: stats["far"] += 1; i = ent + 1; continue
        stats["entry"] += 1; R0 = ep - stop; k = ent + 1; last = ent; why = "time"; px = None
        while k < len(bars) and k <= ent + MAX_HOLD:
            if bars[k][3] < stop: px = min(bars[k][1], stop); why = "stop"; last = k; break
            for p in lo:                      # k 시점에 새로 확정된 피벗 저점 → 래칫
                if p + PIVOT_K == k and p > ent and bars[p][3]*(1-BUF) > stop: stop = bars[p][3]*(1-BUF)
            k += 1
        if px is None: last = min(k, len(bars)-1); px = bars[last][4]
        pnl = (px/ep - 1)*100 - COST
        trades.append({"m":name,"entry":bars[ent][0],"exit":bars[last][0],"ep":ep,"stop0":round(bars[L2][3]*(1-BUF),4),"pnl":pnl,"R":pnl/(R0/ep*100),"why":why,"hold":last-ent})
        i = last + 1
    return trades, stats

if __name__ == "__main__":
    groups = {"ETF(미국,20년)": [f for f in sorted(glob.glob("data/etf/*.csv")) if os.path.basename(f)[:3] not in ("BIL","AGG","IEF")],
              "업비트(3.3년)": sorted(glob.glob("tests/fixtures/turtle/upbit_*.csv")),
              "OKX(3.3년)": sorted(glob.glob("tests/fixtures/turtle/okx_*.csv"))}
    allt = []
    for g, files in groups.items():
        gt = []; gs = {}
        for f in files:
            t, s = backtest(load(f), os.path.basename(f)[:-4]); gt += t
            for k, v in s.items(): gs[k] = gs.get(k, 0) + v
            summarize(t, "  " + os.path.basename(f)[:-4])
        print("  깔때기:", gs); summarize(gt, "= " + g); allt += gt; print()
    summarize(allt, "== 전체")
    if allt:
        rs = [t["R"] for t in allt]; w = [r for r in rs if r > 0]; l = [r for r in rs if r <= 0]
        print(f"   평균 이익 R {st.mean(w):+.2f} / 평균 손실 R {st.mean(l):+.2f} / 이익:손실 크기비 {st.mean(w)/abs(st.mean(l)):.2f} / 연 환산 매매 수(ETF 7×20년) {sum(1 for t in allt if not t['m'].startswith(('upbit','okx')))/140:.1f}")
    if "--trades" in sys.argv:
        for t in allt: print(t)
