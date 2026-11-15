# -*- coding: utf-8 -*-
"""청산 맵 비대칭 선별 (2026-11-15, 제안함 P38, 설계서 docs/LIQMAP_PLAN.md 3·4절). 트랙 아님, 주문 없음, 표준 라이브러리만.
자료: data/liqmap/binance_<코인>_1h.csv (열 t,o,h,l,c,v,oi), 선택 binance_BTC_liq_1h.csv (열 t,sell_v,buy_v)
재현: python scripts/liqmap_screen.py [자료폴더] [--out data/liqmap/screen_result.json]
판단 기준(설계서 4절, 결과를 보기 전에 고정):
  근거 있음 = t >= 2.0 · 앞 절반·뒤 절반 평균 둘 다 > 0 · B14 대비 차이 평균 > 0 · band 0.05·0.20 평균이 주 설정과 같은 부호.
  하나라도 실패하면 근거 없음. t >= 3 이 아니면 약한 근거. 반대 방향 t <= -2 여도 채택하지 않는다.
주 지표: 날짜별 성적(그날 신호 낸 코인들의 수수료 뺀 성적 평균)의 평균과 t.
"""
import os, sys, csv, json, math, argparse
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import liqmap as LM  # noqa: E402

COINS = ["BTC", "ETH", "XRP", "TRX", "LINK", "DOGE"]
BANDS = (0.05, 0.10, 0.20)
MAIN_BAND = 0.10
T_PASS = 2.0
T_STRONG = 3.0


# ----------------------------------------------------------------- 통계 (순수 함수)

def mean(xs):
    return sum(xs) / len(xs) if xs else None


def sd(xs):
    """표본표준편차 (n-1)"""
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def tstat(xs):
    n = len(xs)
    s = sd(xs)
    if n < 2 or not s:
        return None
    return mean(xs) / (s / math.sqrt(n))


def split_halves(xs):
    """날짜순으로 선 값들을 앞 n//2 / 나머지로 나눈 평균."""
    h = len(xs) // 2
    return mean(xs[:h]), mean(xs[h:])


def pearson(xs, ys):
    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy)


def ranks(xs):
    """같은 값은 평균 순위."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        r = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            out[order[k]] = r
        i = j + 1
    return out


def spearman(xs, ys):
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    return pearson(ranks(xs), ranks(ys))


def tercile_means(pairs):
    """pairs = [(a, r)] → a 오름차순 3등분(앞 n//3, 다음 n//3, 나머지)의 r 평균·행 수."""
    ps = sorted(pairs, key=lambda p: p[0])
    k = len(ps) // 3
    parts = [ps[:k], ps[k:2 * k], ps[2 * k:]]
    return [{"n": len(p), "mean_r_pct": (mean([r for _, r in p]) * 100 if p else None)} for p in parts]


def sign(x):
    return (x > 0) - (x < 0)


# ----------------------------------------------------------------- 자료

def load_bars(path):
    out = []
    with open(path, encoding="utf-8") as fp:
        for r in csv.DictReader(fp):
            oi = r.get("oi")
            out.append({"t": int(float(r["t"])), "o": float(r["o"]), "h": float(r["h"]), "l": float(r["l"]),
                        "c": float(r["c"]), "oi": float(oi) if oi not in (None, "") else None})
    out.sort(key=lambda b: b["t"])
    return out


def load_liq(path):
    out = {}
    with open(path, encoding="utf-8") as fp:
        for r in csv.DictReader(fp):
            out[int(float(r["t"]))] = (float(r["sell_v"]), float(r["buy_v"]))
    return out


def day_str(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")


def build_rows(coin, bars):
    """ready 인 스냅샷마다 한 행. 'X 시각의 종가' = 닫힌 시각이 X 인 봉의 c."""
    close_at = {b["t"] + LM.BAR_SEC: b["c"] for b in bars}
    rows = []
    for s in LM.daily_snapshots(bars, bands=BANDS):
        if not s["ready"]:
            continue
        t, px = s["t"], s["px"]

        def ret(dh):
            c = close_at.get(t + dh * 3600)
            return c / px - 1 if c else None

        def past(dh):
            c = close_at.get(t - dh * 3600)
            return px / c - 1 if c else None

        row = {"coin": coin, "t": t, "px": px, "r24": ret(24), "r72": ret(72),
               "rp14": past(LM.LOOKBACK_H), "rp24": past(24)}
        for b in BANDS:
            k = "%02d" % int(round(b * 100))
            row["a" + k] = s["by_band"][b]["a"]
            row["c" + k] = s["by_band"][b]["call"]
        rows.append(row)
    return rows


# ----------------------------------------------------------------- 방향·성적

def dir_liq(band_key):
    def f(row):
        c = row["c" + band_key]
        return c if c else None
    return f


def dir_contrarian(past_key):
    """B14/B24: 같은 날·같은 코인에서 주 설정이 신호를 낸 행만. 과거 수익률의 반대 방향(0 이면 포지션 없음 → 제외)."""
    def f(row):
        if not row["c10"] or row[past_key] is None:
            return None
        p = row[past_key]
        return -1 if p > 0 else (1 if p < 0 else None)
    return f


def perf_pct(direction, ret):
    return direction * ret * 100 - LM.FEE_PCT


def eval_variant(rows, dir_fn, ret_key):
    """→ (요약, {t: 그날 성적 평균}). 날짜 = 스냅샷 시각 t."""
    by_day, sig, hits, gross = {}, 0, 0, []
    for row in rows:
        d = dir_fn(row)
        r = row[ret_key]
        if not d or r is None:
            continue
        by_day.setdefault(row["t"], []).append(perf_pct(d, r))
        sig += 1
        hits += 1 if d * r > 0 else 0
        gross.append(d * r * 100)
    daily = {t: mean(v) for t, v in by_day.items()}
    return summarize(daily, sig, hits, gross), daily


def summarize(daily, sig=None, hits=None, gross=None):
    xs = [daily[t] for t in sorted(daily)]
    a, b = split_halves(xs) if xs else (None, None)
    return {"n_days": len(xs), "mean": mean(xs), "sd": sd(xs), "t": tstat(xs), "first_half": a, "second_half": b,
            "n_signals": sig, "hit_rate": (hits / sig if sig else None),
            "gross_mean_pct": (mean(xs) + LM.FEE_PCT if xs else None)}


def diff_series(d1, d2):
    return {t: d1[t] - d2[t] for t in d1 if t in d2}


def judge(main, d14, b05, b20):
    """설계서 4절 표 그대로."""
    t_ok = main["t"] is not None and main["t"] >= T_PASS
    half_ok = (main["first_half"] is not None and main["second_half"] is not None
               and main["first_half"] > 0 and main["second_half"] > 0)
    diff_ok = d14["mean"] is not None and d14["mean"] > 0
    ms = sign(main["mean"]) if main["mean"] is not None else 0
    band_ok = all(x["mean"] is not None and ms != 0 and sign(x["mean"]) == ms for x in (b05, b20))
    ok = t_ok and half_ok and diff_ok and band_ok
    return {"t_ge_2": t_ok, "halves_both_positive": half_ok, "beats_b14": diff_ok, "bands_same_sign": band_ok,
            "verdict": "근거 있음" if ok else "근거 없음",
            "t_ge_3": bool(main["t"] is not None and main["t"] >= T_STRONG),
            "reverse_t_le_minus2": bool(main["t"] is not None and main["t"] <= -T_PASS),
            "reverse_note": "반대 방향 t<=-2 이나 채택하지 않음(새 가설로 따로 등록)" if (main["t"] is not None and main["t"] <= -T_PASS) else None}


# ----------------------------------------------------------------- 분석

def analyze(rows, model_check=None):
    out = {"rules": LM.rules_dict(), "n_rows": len(rows)}
    liq24, d_main = eval_variant(rows, dir_liq("10"), "r24")
    b14, d_b14 = eval_variant(rows, dir_contrarian("rp14"), "r24")
    b24, d_b24 = eval_variant(rows, dir_contrarian("rp24"), "r24")
    b05, _ = eval_variant(rows, dir_liq("05"), "r24")
    b20, _ = eval_variant(rows, dir_liq("20"), "r24")
    l72, _ = eval_variant(rows, dir_liq("10"), "r72")
    l72["note"] = "겹치는 구간이라 t 는 과장됨"
    out["main"] = liq24
    out["b14"] = b14
    out["b24"] = b24
    out["diff_vs_b14"] = summarize(diff_series(d_main, d_b14))
    out["diff_vs_b24"] = summarize(diff_series(d_main, d_b24))
    out["band05"] = b05
    out["band20"] = b20
    out["h72"] = l72
    out["judgement"] = judge(liq24, out["diff_vs_b14"], b05, b20)

    # 참고
    ref = {}
    for name, c in (("up_signal", 1), ("down_signal", -1)):
        ps = [perf_pct(c, r["r24"]) for r in rows if r["c10"] == c and r["r24"] is not None]
        ref[name] = {"n": len(ps), "mean_perf_pct": mean(ps)}
    btc = [r for r in rows if r["coin"] == "BTC"]
    ref["btc_only"] = eval_variant(btc, dir_liq("10"), "r24")[0] if btc else None
    ref["a_terciles"] = tercile_means([(r["a10"], r["r24"]) for r in rows if r["a10"] is not None and r["r24"] is not None])
    pa = [(r["a10"], r["rp14"]) for r in rows if r["a10"] is not None and r["rp14"] is not None]
    ref["corr_a_rp14"] = pearson([p[0] for p in pa], [p[1] for p in pa])
    ref["frac_abs_a_ge_amin"] = (sum(1 for r in rows if r["a10"] is not None and abs(r["a10"]) >= LM.A_MIN) / len(rows)) if rows else None
    per = {}
    for coin in sorted({r["coin"] for r in rows}):
        ps = [perf_pct(r["c10"], r["r24"]) for r in rows if r["coin"] == coin and r["c10"] and r["r24"] is not None]
        per[coin] = {"n_signals": len(ps), "mean_perf_pct": mean(ps)}
    ref["per_coin"] = per
    out["reference"] = ref
    out["model_check"] = model_check
    return out


def model_check(bars, liq):
    """BTC: 모형이 쓸었다고 본 달러 vs 실제 청산 체결(코인 x 종가). 준비 구간 뒤 봉만."""
    _, sw = LM.daily_snapshots(bars, collect_swept=True)
    first = next((b["t"] for b in bars if b["oi"] is not None), None)
    if first is None:
        return None
    cut = first + (LM.LOOKBACK_H - 1) * LM.BAR_SEC
    close = {b["t"]: b["c"] for b in bars}
    ml, al, ms, as_ = [], [], [], []
    for r in sw:
        if r["t"] < cut or r["t"] not in liq or r["t"] not in close:
            continue
        sv, bv = liq[r["t"]]
        ml.append(r["swept_long"]); al.append(sv * close[r["t"]])
        ms.append(r["swept_short"]); as_.append(bv * close[r["t"]])
    return {"n": len(ml), "long_pearson": pearson(ml, al), "long_spearman": spearman(ml, al),
            "short_pearson": pearson(ms, as_), "short_spearman": spearman(ms, as_)}


# ----------------------------------------------------------------- 출력

def f(x, nd=3, pct=False):
    if x is None:
        return "-"
    return ("%.*f%%" % (nd, x * 100)) if pct else ("%.*f" % (nd, x))


def line(name, s):
    return "  %-18s 날 %4s  평균 %8s%%  sd %7s  t %7s  앞 %8s  뒤 %8s  신호 %5s  적중 %7s  수수료전 %8s%%" % (
        name, s["n_days"], f(s["mean"]), f(s["sd"]), f(s["t"], 2), f(s["first_half"]), f(s["second_half"]),
        s["n_signals"] if s["n_signals"] is not None else "-", f(s["hit_rate"], 1, True), f(s["gross_mean_pct"]))


def render(res):
    p = print
    p("=== 청산 맵 비대칭 선별 (설계서 docs/LIQMAP_PLAN.md) — 성적 %%, 수수료 %.2f%% 뺀 값 ===" % LM.FEE_PCT)
    p("스냅샷 행 수 %d" % res["n_rows"])
    p("[날짜별 계열]")
    for k, name in (("main", "청산맵 band10%"), ("b14", "대조 B14"), ("b24", "대조 B24"),
                    ("diff_vs_b14", "차이(맵-B14)"), ("diff_vs_b24", "차이(맵-B24)"),
                    ("band05", "청산맵 band5%"), ("band20", "청산맵 band20%"), ("h72", "청산맵 72시간")):
        p(line(name, res[k]))
    p("  (72시간은 겹치는 구간이라 t 는 과장됨)")
    r = res["reference"]
    p("[참고 — 판단에 안 씀]")
    p("  오름 신호 %s건 평균 %s%% / 내림 신호 %s건 평균 %s%%" % (r["up_signal"]["n"], f(r["up_signal"]["mean_perf_pct"]),
                                                          r["down_signal"]["n"], f(r["down_signal"]["mean_perf_pct"])))
    if r["btc_only"]:
        p(line("BTC 단독", r["btc_only"]))
    for i, q in enumerate(r["a_terciles"]):
        p("  a 3분위 %d: 행 %s, r24 평균 %s%%" % (i + 1, q["n"], f(q["mean_r_pct"])))
    p("  a 와 rp14 피어슨 %s / |a|>=%.1f 행 비율 %s" % (f(r["corr_a_rp14"]), LM.A_MIN, f(r["frac_abs_a_ge_amin"], 1, True)))
    for c, v in r["per_coin"].items():
        p("  %-5s 신호 %4s건 평균 성적 %s%%" % (c, v["n_signals"], f(v["mean_perf_pct"])))
    mc = res.get("model_check")
    if mc:
        p("[모형 대조 BTC] 표본 %s | 롱쓸림 vs 매도청산 피어슨 %s 스피어만 %s | 숏쓸림 vs 매수청산 피어슨 %s 스피어만 %s" % (
            mc["n"], f(mc["long_pearson"]), f(mc["long_spearman"]), f(mc["short_pearson"]), f(mc["short_spearman"])))
    j = res["judgement"]
    yn = lambda b: "통과" if b else "실패"
    p("[판정] t>=2.0 %s · 앞뒤 절반 둘 다 플러스 %s · B14 보다 나음(차이 평균>0) %s · band 5%%·20%% 같은 부호 %s" % (
        yn(j["t_ge_2"]), yn(j["halves_both_positive"]), yn(j["beats_b14"]), yn(j["bands_same_sign"])))
    p("  결론: %s%s" % (j["verdict"], " (t>=3 아님: 약한 근거)" if j["verdict"] == "근거 있음" and not j["t_ge_3"] else ""))
    p("  t>=3: %s" % ("예" if j["t_ge_3"] else "아니오"))
    if j["reverse_t_le_minus2"]:
        p("  " + j["reverse_note"])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", default=os.path.join(os.path.dirname(HERE), "data", "liqmap"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    out_path = a.out or os.path.join(a.folder, "screen_result.json")
    rows, btc_bars = [], None
    for c in COINS:
        p = os.path.join(a.folder, "binance_%s_1h.csv" % c)
        if not os.path.exists(p):
            continue
        bars = load_bars(p)
        rows += build_rows(c, bars)
        if c == "BTC":
            btc_bars = bars
    if not rows:
        print("자료 없음: %s" % a.folder)
        return 1
    mc = None
    lp = os.path.join(a.folder, "binance_BTC_liq_1h.csv")
    if btc_bars and os.path.exists(lp):
        mc = model_check(btc_bars, load_liq(lp))
    res = analyze(rows, mc)
    render(res)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(res, fp, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
