"""Sweep + MSS + FVG 카드 실데이터 측정 (2026-11-04, 제안함 P28). 트랙 아님, 주문 없음, 표준 라이브러리만.
자료: data/bb_reentry/*.csv (TradingView 일봉 15종목, P27 과 같은 파일)
      data/smc_fvg/*.csv   (TradingView 바이낸스 4시간봉 BTC·ETH·SOL·XRP 2024-01~, 1시간봉 BTC·ETH 2026-01~, 2026-11-04 받음)
재현: python scripts/smc_fvg_check.py

결과를 보기 전에 고정한 규칙 (이 파일을 결과 실행 전에 커밋했다):
  스윙   : 프랙탈 — 좌우 2봉보다 낮은 저가 = 스윙 저점, 높은 고가 = 스윙 고점. 오른쪽 2봉이 마감해야 확정(미리보기 없음)
  ① 스윕 : 확정된 마지막 스윙 저점 L 이 마지막 스윙 고점 H 보다 앞에 있고(저점→되반등 고점), H 뒤의 봉 저가가 L 아래로 내려감.
           스윕 저점 S = 그 봉부터 MSS 까지의 최저가. 한 L 은 한 번만 쓴다
  ② MSS  : 스윕 봉부터 20봉 안에 종가 > H. 못 하면 취소
  ③ FVG  : 스윕 다음 봉 ~ MSS 봉을 가운데 봉(j)으로 하는 상승 3봉 갭 중 가장 늦은 것: 저가[j+1] > 고가[j-1].
           구간 = [고가[j-1], 저가[j+1]]. 없으면 거래 없음. j+1 봉이 마감해야 확정
  진입   : 확정 다음 봉부터 지정가 = FVG 위쪽(저가[j+1]). 시가가 더 낮으면 시가 체결. MSS 뒤 20봉 안 미체결이면 취소.
           체결 전에 시가가 S 이하면 취소
  손절   : S. 저가가 닿으면 S(갭이면 시가). 체결 봉에서 바로 닿아도 손절, 체결 봉의 익절은 세지 않음(보수적)
  익절 (카드 빈칸, 우리가 정함): 2R (R = 진입가 − S). 고가가 닿으면 목표가(갭이면 시가). 같은 봉에 손절·익절 둘 다면 손절 먼저.
           50봉 지나면 종가 청산
  비용   : 왕복 — 암호화폐 0.20%, 한국 개별주 0.25%(세금 포함), 나머지 0.05% (P27 과 같음)
  한 시장에 한 포지션. 마지막(미완성) 봉 제외. 열린 포지션은 집계 제외
변형(비교용, 함께 고정):
  strict : 스윕 봉 종가가 L 위로 되돌아와야 함(같은 봉 되찜)
  ce     : 진입 지정가를 FVG 가운데(50%)로
  tp3r   : 익절 3R
  nofvg  : FVG 없이 MSS 다음 봉 시가 진입(FVG 가 보태는 게 있는지)
  short  : 거울(고점 스윕 → 하락 MSS → 하락 FVG 되돌림 숏). 가격 역수로 같은 코드를 돌린다
기준선: 같은 시장 매수보유의 봉당 평균 수익 × 보유 봉수 (숏은 부호 반대)
"""
import csv, os, sys, math, glob
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "data")
K = 2; SWEEP_WIN = 20; FILL_WIN = 20; MAX_HOLD = 50


def cost(sym):
    if sym.startswith("BINANCE"): return 0.20
    if sym.startswith("KRX_005930") or sym.startswith("KRX_000660"): return 0.25
    return 0.05


def load(f):
    return [(r["date"], float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"])) for r in csv.DictReader(open(f))]


def run(rows, mode):
    """롱 규칙. 숏은 호출 쪽에서 가격을 역수로 바꿔 넣는다. 반환: [(진입idx, 청산idx, 진입가, 청산가, 이유, R폭)]"""
    o = [r[1] for r in rows]; h = [r[2] for r in rows]; l = [r[3] for r in rows]; c = [r[4] for r in rows]
    n = len(rows); trades = []
    # 봉 i 시작 시점에 확정된 마지막 스윙(p + K <= i - 1). 건너뛰기와 무관하게 미리 계산
    SL = [None] * n; SH = [None] * n; curL = curH = None
    for i in range(n):
        p = i - 1 - K
        if p >= K:
            if all(l[p] < l[p - d] and l[p] < l[p + d] for d in range(1, K + 1)): curL = (l[p], p)
            if all(h[p] > h[p - d] and h[p] > h[p + d] for d in range(1, K + 1)): curH = (h[p], p)
        SL[i] = curL; SH[i] = curH
    usedL = set()
    i = 2 * K
    while i < n:
        lastL = SL[i]; lastH = SH[i]
        if not (lastL and lastH and lastL[1] < lastH[1] and i > lastH[1] and lastL[1] not in usedL and l[i] < lastL[0]):
            i += 1; continue
        if mode == "strict" and c[i] <= lastL[0]:
            usedL.add(lastL[1]); i += 1; continue
        usedL.add(lastL[1]); H = lastH[0]; sw = i; S = l[i]; mss = None
        m = i
        while m < n and m - sw < SWEEP_WIN:
            S = min(S, l[m])
            if c[m] > H: mss = m; break
            m += 1
        if mss is None: i = m; continue
        if mode == "nofvg":
            if mss + 1 >= n: break
            e = mss + 1; entry = o[e]; start_hold = e
            if entry <= S: i = mss + 1; continue
        else:
            fvg = None
            for j in range(mss, sw, -1):
                if j + 1 < n and l[j + 1] > h[j - 1]: fvg = (h[j - 1], l[j + 1], j); break
            if fvg is None: i = mss + 1; continue
            bot, top, j = fvg
            px = (bot + top) / 2 if mode == "ce" else top
            e = max(mss, j + 1) + 1; entry = None
            while e < n and e - mss <= FILL_WIN:
                if o[e] <= S: break
                if l[e] <= px: entry = min(o[e], px); break
                e += 1
            if entry is None: i = e + 1; continue
            start_hold = e
        R = entry - S
        if R <= 0: i = e + 1; continue
        tgt = entry + (3 if mode == "tp3r" else 2) * R
        k = start_hold; exitp = None; why = None
        while k < n:
            if l[k] <= S: exitp = min(o[k], S) if k > e or mode == "nofvg" else S; why = "stop"; break
            # 지정가 체결 봉에서는 고가가 체결 전일 수 있어 익절을 세지 않는다(보수적). 시가 진입(nofvg)은 센다
            if h[k] >= tgt and (k > e or mode == "nofvg"): exitp = max(o[k], tgt) if k > e else tgt; why = "target"; break
            if k - e + 1 >= MAX_HOLD: exitp = c[k]; why = "time"; break
            k += 1
        if exitp is None: break
        trades.append((e, k, entry, exitp, why, R / entry))
        i = k + 1
    return trades


def evaluate(rows, mode, cst, bh):
    short = mode == "short"
    if short:
        rr = [(d, 1 / o, 1 / l, 1 / h, 1 / c) for d, o, h, l, c in rows]
        raw = run(rr, "base")
    else:
        raw = run(rows, mode)
    out = []
    for e, k, en, ex, why, rpct in raw:
        gross = (1 - en / ex) * 100 if short else (ex / en - 1) * 100
        out.append({"in": rows[e][0], "bars": k - e + 1, "gross": gross, "net": gross - cst, "why": why,
                    "base": (-bh if short else bh) * (k - e + 1), "rpct": rpct * 100})
    return out


def summ(tr):
    if not tr: return None
    n = len(tr); nets = [t["net"] for t in tr]; m = sum(nets) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in nets) / (n - 1)) if n > 1 else 0
    gp = sum(x for x in nets if x > 0); gl = -sum(x for x in nets if x < 0)
    eq = 1.0
    for x in nets: eq *= 1 + x / 100
    return {"n": n, "mean": m, "gross": sum(t["gross"] for t in tr) / n, "t": m / (sd / math.sqrt(n)) if sd else 0,
            "win": sum(1 for x in nets if x > 0) / n * 100, "pf": gp / gl if gl else 99, "total": (eq - 1) * 100,
            "sum": sum(nets), "base": sum(t["base"] for t in tr), "edge": (sum(nets) - sum(t["base"] for t in tr)) / n,
            "stops": sum(1 for t in tr if t["why"] == "stop") / n * 100, "tgts": sum(1 for t in tr if t["why"] == "target") / n * 100,
            "bars": sum(t["bars"] for t in tr) / n, "rpct": sum(t["rpct"] for t in tr) / n}


def fmt(s):
    if s is None: return "-"
    return ("%3d건 건당%+6.2f%%(비용전%+6.2f) t%+5.2f 승%3.0f%% PF%4.2f 누적%+7.1f%% 초과/건%+5.2f%% 손절%3.0f%% 익절%3.0f%% %4.1f봉 R폭%4.1f%%"
            % (s["n"], s["mean"], s["gross"], s["t"], s["win"], s["pf"], s["total"], s["edge"], s["stops"], s["tgts"], s["bars"], s["rpct"]))


MODES = ("base", "strict", "ce", "tp3r", "nofvg", "short")
SETS = {"일봉": sorted(glob.glob(os.path.join(ROOT, "bb_reentry", "*.csv"))),
        "4시간봉": sorted(glob.glob(os.path.join(ROOT, "smc_fvg", "*_4h.csv"))),
        "1시간봉": sorted(glob.glob(os.path.join(ROOT, "smc_fvg", "*_1h.csv")))}
SPLIT = {"일봉": "2018-01-01", "4시간봉": "2025-06-01", "1시간봉": "2026-06-01"}
GROUPS = {"미국": ("AMEX_SPY", "NASDAQ_QQQ", "AMEX_IWM"), "한국": ("KRX_",), "암호화폐": ("BINANCE_",), "금·환율": ("TVC_GOLD", "FX_EURUSD")}

if __name__ == "__main__":
    agg = {}
    for tf, files in SETS.items():
        for f in files:
            sym = os.path.basename(f)[:-4]; rows = load(f); c = [r[4] for r in rows]
            bh = sum(c[i] / c[i - 1] - 1 for i in range(1, len(c))) / (len(c) - 1) * 100
            print("\n## [%s] %s %s ~ %s (%d봉)" % (tf, sym, rows[0][0], rows[-1][0], len(rows)))
            for mode in MODES:
                tr = evaluate(rows, mode, cost(sym), bh)
                print("  %-7s %s" % (mode, fmt(summ(tr))))
                g = next((g for g, pre in GROUPS.items() if any(sym.startswith(p) for p in pre)), "기타")
                agg.setdefault((tf, g, mode), []).extend(tr)
                if mode == "base":
                    print("  %-7s %s" % ("앞", fmt(summ([t for t in tr if t["in"] < SPLIT[tf]]))))
                    print("  %-7s %s" % ("뒤", fmt(summ([t for t in tr if t["in"] >= SPLIT[tf]]))))
    print("\n## 묶음 (봉 단위·시장군별)")
    for (tf, g, mode), tr in agg.items():
        print("  %-5s %-5s %-7s %s" % (tf, g, mode, fmt(summ(tr))))
