"""백테스트 과적합 논문(Bailey·López de Prado, Harvey·Liu)의 공식을 CARRYGATE 실데이터에 적용. 표준 라이브러리만."""
import json, math, random, statistics, sys
random.seed(7)
REPO = sys.argv[1]

# ---------- 1. Bailey 2014: N개 무작위 전략 중 최댓값의 기대 샤프 (표준정규 단위) ----------
def z_inv(p):
    # 역정규 근사 (Acklam) — 표준 라이브러리만
    return statistics.NormalDist().inv_cdf(p)
def exp_max_sr(N):
    g = 0.5772156649
    return (1 - g) * z_inv(1 - 1.0 / N) + g * z_inv(1 - 1.0 / (N * math.e))
print("== 1. 무작위 전략 N개 중 최댓값의 기대 샤프(표준편차 단위) ==")
for N in (2, 5, 10, 45, 378, 900):
    print("N=%4d  E[max]=%.2f" % (N, exp_max_sr(N)))
# 15일 기록, 일별 샤프 표준오차 = 1/sqrt(15)  → 연환산 안 함. 그냥 '표준편차 단위' 로 읽는다.

# ---------- 2. 초단타 후보 규칙의 잡음 바닥 (부호 뒤집기 귀무가설) ----------
rows = [json.loads(l) for l in open(REPO + "/scalp_history.jsonl")]
MIN_DAYS, MIN_POS = 7, 0.6
BASELINE = "hold5_rand"
def split(k):
    v, c, tf, st = k.split(":", 3); return v, c, tf, st
# 조합별 일별 순수익 시계열
series = {}
for r in rows:
    for k, v in r["rows"].items():
        series.setdefault(k, []).append(v["nt"])
sts = sorted({split(k)[3] for k in series})
print("\n규칙 종류:", sts)
base_st = [s for s in sts if "rand" in s or "base" in s]
print("기준선 규칙 추정:", base_st)
BASELINE = base_st[0] if base_st else None

def candidates(get_nt, horizon):
    """horizon 일까지의 기록으로 후보 수. get_nt(k) -> 일별 순수익 리스트"""
    acc = {}
    for k, s in series.items():
        s = get_nt(k)[:horizon]
        if len(s) < MIN_DAYS:
            continue
        acc[k] = (sum(s), sum(1 for x in s if x > 0) / len(s), len(s))
    n = 0
    for k, (nt, pos, d) in acc.items():
        v, c, tf, st = split(k)
        if st == BASELINE:
            continue
        b = acc.get("%s:%s:%s:%s" % (v, c, tf, BASELINE), (0.0, 0, 0))[0]
        if nt > 0 and pos >= MIN_POS and nt > b:
            n += 1
    return n, sum(1 for k in acc if split(k)[3] != BASELINE)

print("\n== 2. 초단타 후보 규칙: 실제 후보 수 vs 운만으로 나오는 후보 수(부호 뒤집기 2,000회) ==")
print("%6s %8s %10s %18s %10s" % ("일수", "조합수", "실제후보", "잡음 기대(중앙/95%)", "실제/잡음"))
for horizon in (7, 9, 11, 13, 15, 17):
    actual, ncomb = candidates(lambda k: series[k], horizon)
    sims = []
    for _ in range(2000):
        flips = {k: [x * random.choice((-1, 1)) for x in s] for k, s in series.items()}
        sims.append(candidates(lambda k, f=flips: f[k], horizon)[0])
    sims.sort()
    med, p95 = sims[len(sims)//2], sims[int(len(sims)*0.95)]
    print("%6d %8d %10d %10d / %-6d %10s" % (horizon, ncomb, actual, med, p95, ("%.2f" % (actual/med)) if med else "-"))

# ---------- 3. 합격선의 1종 오류(가짜 PASS)와 2종 오류(진짜를 놓침), 20건에서 ----------
print("\n== 3. 순방향 합격선: 실력 0 인 규칙이 PASS 할 확률 / 백테스트만큼 실력이 있어도 PASS 못 할 확률 ==")
V = json.load(open(REPO + "/verdict.json"))["tracks"]
crit = json.load(open(REPO + "/criteria.json"))
def sim_pf(p, W, L, n, trials=20000):
    out = []
    for _ in range(trials):
        w = sum(1 for _ in range(n) if random.random() < p)
        gross_w, gross_l = w * W, (n - w) * L
        out.append((gross_w / gross_l if gross_l > 0 else float("inf"), w))
    return out
for track in ("turtle", "reversal"):
    c = crit[track]; n = c["min_trades"]; pf_pass = c["pass_if_profit_factor_at_least"]
    for venue, vd in V[track]["venues"].items():
        p = vd["backtest_win_rate_pct"] / 100.0; pf_bt = vd["backtest_profit_factor"]
        WL = pf_bt * (1 - p) / p            # 평균 승 / 평균 패
        p0 = 1.0 / (1.0 + WL)                # 기대손익 0 이 되는 승률
        null = sim_pf(p0, WL, 1.0, n); alt = sim_pf(p, WL, 1.0, n)
        zfail = c.get("fail_if_luck_z_below")
        def passes(res, p_ref):
            k = 0
            for pf, w in res:
                z = (w - n * p_ref) / math.sqrt(n * p_ref * (1 - p_ref))
                ok = pf >= pf_pass and (zfail is None or z >= zfail)
                k += ok
            return k / len(res)
        fp = passes(null, p); power = passes(alt, p)
        # 거래당 샤프와 최소 기록 길이(Bailey 2012 MinTRL, 정규 근사, 95%) 및 Harvey t>3
        mean = p * WL - (1 - p); var = p * WL**2 + (1 - p) - mean**2; sr = mean / math.sqrt(var)
        n95 = (1.645 / sr) ** 2; n3 = (3.0 / sr) ** 2
        print("%-8s %-5s 백테스트 승률 %.1f%% PF %.2f (평균승/평균패 %.2f) | 합격선 PF≥%.1f @%d건 | 가짜PASS %.0f%% | 진짜 놓침 %.0f%% | 거래당 샤프 %.2f → 95%% 확신 %d건, t>3 %d건"
              % (track, venue, p*100, pf_bt, WL, pf_pass, n, fp*100, (1-power)*100, sr, round(n95), round(n3)))

# kr_breakout: PASS 조건 승률 ≥ 20% @15건, FAIL < 15%
c = crit["kr_breakout"]; p = V["kr_breakout"]["backtest_win_rate_pct"] / 100; n = c["min_trades"]
def binom_tail(n, p, k):
    return sum(math.comb(n, i) * p**i * (1-p)**(n-i) for i in range(k, n+1))
print("kr_breakout 백테스트 승률 %.1f%% | PASS 승률≥%.0f%% @%d건: 백테스트대로면 PASS 확률 %.0f%%; 승률이 반토막(%.1f%%)이어도 PASS 확률 %.0f%%"
      % (p*100, c["pass_if_win_rate_at_least_pct"], n, binom_tail(n, p, 3)*100, p*50, binom_tail(n, p/2, 3)*100))
