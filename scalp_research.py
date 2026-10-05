# -*- coding: utf-8 -*-
"""
CARRYGATE — 초단타 보조 연구 (A 시간대·펀딩 이벤트, B 거래소 선행·후행)

scalp.py 가 받아 둔 1분봉(bars_cache.json)을 다시 써서, 규칙 검증과는 별개로
"언제(A)" 와 "무엇을 보고(B)" 들어가야 하는지의 근거를 매일 계산해 쌓는다.
규칙 검증(scalp.py)은 건드리지 않는다. 여기서 나온 사실은 새 규칙을 설계할 때 근거로만 쓴다.

A1. 시간대별 비용 문턱 — 하루 24시간 중 어느 시간에 15분 변동폭이 비용을 넘는가
A2. 펀딩 정산 전후 — 펀딩비가 클 때 정산 15분 전·후로 가격이 한쪽으로 움직이는가
B.  거래소 선행·후행 — 한 거래소의 1분 움직임이 다른 거래소의 다음 1분을 예측하는가

사용법
  python scalp_research.py                 # bars_cache.json 을 읽어 평가·저장 (없으면 직접 받음)
  python scalp_research.py --dry-run
  python scalp_research.py --fixture tests/fixtures
  python scalp_research.py --report        # 쌓인 기록의 누적 요약
"""
import json, sys, math, argparse, time
from datetime import datetime, timezone, timedelta

import scalp
from scalp import http_json, f, KST

LEAD_PAIRS = [("okx", "upbit"), ("hyperliquid", "upbit"), ("hyperliquid", "okx"),
              ("okx", "hyperliquid"), ("upbit", "okx"), ("upbit", "hyperliquid"),
              ("binance", "upbit"), ("upbit", "binance")]   # binance 는 오프라인 CSV 검증용
BIG_SIGMA = 2.0          # "큰 움직임" 기준: 선행 거래소 1분 수익률의 표준편차 × 2
EVENT_WINDOW_MIN = 15    # 펀딩 정산 전후로 보는 분
MIN_DAYS = 5             # 누적 판단 최소 일수


# ----------------------------------------------------------------- 공용

def mean(xs):
    return sum(xs) / len(xs) if xs else None


def std(xs):
    if len(xs) < 2:
        return None
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def corr(xs, ys):
    if len(xs) < 3:
        return None
    mx, my = mean(xs), mean(ys)
    sx, sy = std(xs), std(ys)
    if not sx or not sy:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (len(xs) * sx * sy)


def r4(x):
    return None if x is None else round(x, 4)


def cost_of(venue, bars, eval_start, fees):
    tick = scalp.tick_estimate(bars)
    mid = bars[eval_start]["c"] if eval_start < len(bars) else bars[-1]["c"]
    spread = (scalp.SPREAD_TICKS * tick / mid * 100.0) if (tick and mid) else 0.0
    return {"round_trip_taker_pct": round(2.0 * fees["taker_pct"] + spread, 5),
            "round_trip_maker_pct": round(2.0 * fees["maker_pct"], 5),
            "spread_pct_est": round(spread, 5)}


# ----------------------------------------------------------------- A1 시간대

def hourly_hurdle(bars, eval_start, rt_taker, horizons=(5, 15)):
    """시간대(UTC 0~23)별 h분 뒤 평균 변동폭과 본전 승률."""
    c = [b["c"] for b in bars]
    t = [b["t"] for b in bars]
    out = {}
    for h in horizons:
        buckets = [[] for _ in range(24)]
        for i in range(eval_start, len(c) - h):
            buckets[(t[i] // 3600) % 24].append(abs(c[i + h] / c[i] - 1.0) * 100.0)
        moves = [mean(b) for b in buckets]
        out["h%d_mean_abs_move_pct" % h] = [r4(m) for m in moves]
        out["h%d_needed_win_rate_pct" % h] = [round(100.0 * (1.0 + rt_taker / m) / 2.0, 1) if m else None
                                             for m in moves]
        out["h%d_n" % h] = [len(b) for b in buckets]
    return out


def kst_hour(h_utc):
    return (h_utc + 9) % 24


# ----------------------------------------------------------------- A2 펀딩 이벤트

def fetch_funding_events(venue, coin, start, end):
    """[(정산시각 sec, 펀딩비율)] — 하이퍼리퀴드는 1시간, OKX 는 8시간 주기."""
    out = []
    if venue == "hyperliquid":
        rows = http_json("https://api.hyperliquid.xyz/info",
                         data={"type": "fundingHistory", "coin": coin,
                               "startTime": start * 1000, "endTime": end * 1000})
        for r in rows:
            t, rate = f(r.get("time")), f(r.get("fundingRate"))
            if t is not None and rate is not None:
                out.append((int(t) // 1000, rate))
    elif venue == "okx":
        d = http_json("https://www.okx.com/api/v5/public/funding-rate-history"
                      "?instId=%s-USDT-SWAP&limit=100" % coin)
        for r in d.get("data") or []:
            t, rate = f(r.get("fundingTime")), f(r.get("realizedRate") or r.get("fundingRate"))
            if t is not None and rate is not None:
                out.append((int(t) // 1000, rate))
    return sorted((t, r) for t, r in out if start <= t < end)


def funding_event_study(bars, events, venue, coin, eval_t0):
    """정산 시각 T 기준: 직전 15분 수익률(pre), 직후 15분(post15)·60분(post60). 단위 bp."""
    by_t = {b["t"]: b["c"] for b in bars}
    out = []
    w = EVENT_WINDOW_MIN * 60
    for t_ev, rate in events:
        t0 = t_ev - t_ev % 60          # 분 경계로 내림
        if t0 < eval_t0:
            continue
        c0, cpre, c15, c60 = by_t.get(t0), by_t.get(t0 - w), by_t.get(t0 + w), by_t.get(t0 + 3600)
        if not c0 or not cpre or not c15:
            continue
        out.append({"venue": venue, "coin": coin, "t": t0, "rate_pct": round(rate * 100.0, 5),
                    "pre15_bp": round((c0 / cpre - 1.0) * 1e4, 2),
                    "post15_bp": round((c15 / c0 - 1.0) * 1e4, 2),
                    "post60_bp": round((c60 / c0 - 1.0) * 1e4, 2) if c60 else None})
    return out


def summarize_events(events):
    """가설: 펀딩이 +면(롱이 지불) 정산 직전에 눌리고 직후 되오른다. sign(펀딩)×수익률로 본다."""
    if not events:
        return {"n": 0}
    sgn = lambda x: 1 if x > 0 else (-1 if x < 0 else 0)
    pre = [sgn(e["rate_pct"]) * e["pre15_bp"] for e in events]
    post = [sgn(e["rate_pct"]) * e["post15_bp"] for e in events]
    post60 = [sgn(e["rate_pct"]) * e["post60_bp"] for e in events if e["post60_bp"] is not None]
    mags = sorted(abs(e["rate_pct"]) for e in events)
    med = mags[len(mags) // 2]
    big = [e for e in events if abs(e["rate_pct"]) >= med and e["rate_pct"] != 0]
    post_big = [sgn(e["rate_pct"]) * e["post15_bp"] for e in big]
    return {"n": len(events),
            "abs_rate_median_pct": round(med, 5),
            "signed_pre15_bp": r4(mean(pre)),
            "signed_post15_bp": r4(mean(post)),
            "signed_post60_bp": r4(mean(post60)),
            "post15_hit_rate_pct": round(100.0 * sum(1 for x in post if x > 0) / len(post), 1),
            "n_big": len(big),
            "signed_post15_big_bp": r4(mean(post_big)),
            "post15_big_hit_rate_pct": round(100.0 * sum(1 for x in post_big if x > 0) / len(post_big), 1) if post_big else None}


# ----------------------------------------------------------------- B 선행·후행

def lead_lag(lead_bars, follow_bars, eval_t0):
    """선행 거래소 1분 수익률이 후행 거래소의 다음 1분·5분을 얼마나 예측하나."""
    L = {b["t"]: b["c"] for b in lead_bars}
    F = {b["t"]: b["c"] for b in follow_bars}
    ts = sorted(t for t in L if t >= eval_t0 and t - 60 in L and t in F and t + 60 in F and t - 60 in F)
    rl, rf0, rf1, rf5 = [], [], [], []
    for t in ts:
        rl.append(L[t] / L[t - 60] - 1.0)
        rf0.append(F[t] / F[t - 60] - 1.0)
        rf1.append(F[t + 60] / F[t] - 1.0)
        rf5.append((F[t + 300] / F[t] - 1.0) if t + 300 in F else None)
    if len(ts) < 30:
        return {"n": len(ts)}
    # 반대 방향(후행 -> 선행)도 같은 표본으로
    rev = corr(rf0, [L[t + 60] / L[t] - 1.0 for t in ts if t + 60 in L]) if all(t + 60 in L for t in ts) else None
    sd = std(rl) or 0.0
    sgn = lambda x: 1 if x > 0 else (-1 if x < 0 else 0)
    follow_all = [sgn(a) * b * 1e4 for a, b in zip(rl, rf1) if a != 0]
    big_idx = [i for i, a in enumerate(rl) if abs(a) > BIG_SIGMA * sd and a != 0]
    follow_big1 = [sgn(rl[i]) * rf1[i] * 1e4 for i in big_idx]
    follow_big5 = [sgn(rl[i]) * rf5[i] * 1e4 for i in big_idx if rf5[i] is not None]
    return {"n": len(ts),
            "corr_same_minute": r4(corr(rl, rf0)),
            "corr_lead_next1": r4(corr(rl, rf1)),        # 선행(t) vs 후행(t+1): 양수면 예측력
            "corr_reverse_next1": r4(rev),               # 후행(t) vs 선행(t+1)
            "follow_next1_bp": r4(mean(follow_all)),     # 선행 방향으로 후행 다음 1분을 따라갔을 때 (bp)
            "n_big": len(big_idx),
            "follow_big_next1_bp": r4(mean(follow_big1)),
            "follow_big_next5_bp": r4(mean(follow_big5)),
            "follow_big_hit_rate_pct": round(100.0 * sum(1 for x in follow_big1 if x > 0) / len(follow_big1), 1) if follow_big1 else None}


# ----------------------------------------------------------------- 저장·보고

def append_history(rec, path):
    try:
        with open(path, encoding="utf-8") as fp:
            rows = [l for l in fp.read().splitlines() if l.strip()]
    except IOError:
        rows = []
    rows = [l for l in rows if json.loads(l).get("date") != rec["date"]]
    rows.append(json.dumps(rec, ensure_ascii=False))
    with open(path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(rows) + "\n")


def print_day(out):
    print("=== 초단타 보조 연구 (%s, 평가일 %s UTC) ===" % (out["date"], out["eval_day_utc"]))
    print()
    print("--- A1. 시간대별 15분 본전 승률 (한국시간, 거래소 평균) ---")
    for venue, hours in out["hourly_by_venue"].items():
        cells = " ".join("%02d:%s" % (kst_hour(h), ("%.0f" % v if v is not None else "-")) for h, v in enumerate(hours))
        print("%-12s %s" % (venue, cells))
    good = out["summary"]["hours_under_100"]
    print("100%% 아래인 (거래소, 한국시간) 조합: %s" % (", ".join("%s %02d시" % (v, h) for v, h in good) if good else "없음"))
    print()
    print("--- A2. 펀딩 정산 전후 (sign(펀딩) × 수익률, bp) ---")
    for venue, s in out["funding_summary"].items():
        if s.get("n"):
            print("%-12s 이벤트 %3d  직전15분 %+6.1f  직후15분 %+6.1f (적중 %s%%)  직후60분 %s  |펀딩|상위절반: 직후15분 %+6.1f (n=%d)"
                  % (venue, s["n"], s["signed_pre15_bp"] or 0, s["signed_post15_bp"] or 0, s["post15_hit_rate_pct"],
                     "%+6.1f" % s["signed_post60_bp"] if s["signed_post60_bp"] is not None else "-",
                     s["signed_post15_big_bp"] or 0, s["n_big"]))
        else:
            print("%-12s 이벤트 없음" % venue)
    print()
    print("--- B. 거래소 선행·후행 (코인 평균) ---")
    print("%-24s %5s %8s %8s %8s %10s %10s %8s" % ("선행->후행", "n", "동시상관", "선행상관", "역상관", "큰움직임1분bp", "큰움직임5분bp", "적중%"))
    for pair, s in out["leadlag_by_pair"].items():
        print("%-24s %5d %8s %8s %8s %10s %10s %8s"
              % (pair, s["n"], "%.3f" % s["corr_same_minute"] if s.get("corr_same_minute") is not None else "-",
                 "%.3f" % s["corr_lead_next1"] if s.get("corr_lead_next1") is not None else "-",
                 "%.3f" % s["corr_reverse_next1"] if s.get("corr_reverse_next1") is not None else "-",
                 "%+.1f" % s["follow_big_next1_bp"] if s.get("follow_big_next1_bp") is not None else "-",
                 "%+.1f" % s["follow_big_next5_bp"] if s.get("follow_big_next5_bp") is not None else "-",
                 "%.0f" % s["follow_big_hit_rate_pct"] if s.get("follow_big_hit_rate_pct") is not None else "-"))
    print()
    print("판정:", out["summary"]["verdict"])


def load_history(path):
    rows = []
    try:
        with open(path, encoding="utf-8") as fp:
            for l in fp:
                l = l.strip()
                if l:
                    try:
                        rows.append(json.loads(l))
                    except ValueError:
                        pass
    except IOError:
        pass
    rows.sort(key=lambda r: r.get("date", ""))
    return rows


def wmean(pairs):
    """[(값, 가중치)] 가중 평균."""
    pairs = [(v, w) for v, w in pairs if v is not None and w]
    tot = sum(w for _, w in pairs)
    return sum(v * w for v, w in pairs) / tot if tot else None


def report(path="research_history.jsonl"):
    rows = load_history(path)
    if not rows:
        print("기록 없음 — scalp_research.py 가 하루 한 번 실행되면서 쌓인다.")
        return 0
    days = len(rows)
    print("=== 초단타 보조 연구 누적 (%d일치: %s ~ %s) ===" % (days, rows[0]["date"], rows[-1]["date"]))
    print()
    print("--- A1. 시간대별 15분 본전 승률, 일 평균 (한국시간) ---")
    venues = sorted(set(v for r in rows for v in r.get("hourly_by_venue", {})))
    for v in venues:
        acc = [[] for _ in range(24)]
        for r in rows:
            hs = r.get("hourly_by_venue", {}).get(v)
            if hs:
                for h, x in enumerate(hs):
                    if x is not None:
                        acc[h].append(x)
        avg = [mean(a) for a in acc]
        best = sorted([(x, h) for h, x in enumerate(avg) if x is not None])[:4]
        print("%-12s 가장 유리한 시간: %s" % (v, ", ".join("%02d시 %.0f%%" % (kst_hour(h), x) for x, h in best)))
        under = [h for h, x in enumerate(avg) if x is not None and x < 100]
        print("%-12s 100%% 아래 시간대: %s" % ("", ", ".join("%02d" % kst_hour(h) for h in under) if under else "없음"))
    print()
    print("--- A2. 펀딩 정산 전후 (이벤트 수 가중 평균, bp) ---")
    for v in sorted(set(v for r in rows for v in r.get("funding_summary", {}))):
        ss = [r["funding_summary"][v] for r in rows if r.get("funding_summary", {}).get(v, {}).get("n")]
        if not ss:
            continue
        n = sum(s["n"] for s in ss)
        post = wmean([(s["signed_post15_bp"], s["n"]) for s in ss])
        post_big = wmean([(s["signed_post15_big_bp"], s["n_big"]) for s in ss])
        pos_days = sum(1 for s in ss if (s["signed_post15_bp"] or 0) > 0)
        print("%-12s 이벤트 %4d  직후15분 %+6.1f bp  |펀딩| 상위절반 %+6.1f bp  플러스 일수 %d/%d"
              % (v, n, post or 0, post_big or 0, pos_days, len(ss)))
    print()
    print("--- B. 거래소 선행·후행 (표본 수 가중 평균) ---")
    print("%-24s %6s %8s %8s %12s %12s %8s" % ("선행->후행", "n", "선행상관", "역상관", "큰움직임1분bp", "큰움직임5분bp", "플러스일"))
    pairs = sorted(set(p for r in rows for p in r.get("leadlag_by_pair", {})))
    for p in pairs:
        ss = [r["leadlag_by_pair"][p] for r in rows if r.get("leadlag_by_pair", {}).get(p, {}).get("n")]
        if not ss:
            continue
        n = sum(s["n"] for s in ss)
        print("%-24s %6d %8s %8s %12s %12s %5d/%d"
              % (p, n,
                 "%.3f" % wmean([(s.get("corr_lead_next1"), s["n"]) for s in ss]) if wmean([(s.get("corr_lead_next1"), s["n"]) for s in ss]) is not None else "-",
                 "%.3f" % wmean([(s.get("corr_reverse_next1"), s["n"]) for s in ss]) if wmean([(s.get("corr_reverse_next1"), s["n"]) for s in ss]) is not None else "-",
                 "%+.1f" % wmean([(s.get("follow_big_next1_bp"), s.get("n_big")) for s in ss]) if wmean([(s.get("follow_big_next1_bp"), s.get("n_big")) for s in ss]) is not None else "-",
                 "%+.1f" % wmean([(s.get("follow_big_next5_bp"), s.get("n_big")) for s in ss]) if wmean([(s.get("follow_big_next5_bp"), s.get("n_big")) for s in ss]) is not None else "-",
                 sum(1 for s in ss if (s.get("follow_big_next1_bp") or 0) > 0), len(ss)))
    print()
    if days < MIN_DAYS:
        print("아직 %d일치. %d일 이상 모여야 방향을 말할 수 있다." % (days, MIN_DAYS))
    return 0


# ----------------------------------------------------------------- 메인

def load_bars(a):
    """bars_cache.json -> (data, fees, eval_t0, eval_day). 없으면 scalp 의 수집기로 직접 받는다."""
    if a.fixture:
        data = scalp.load_fixture(a.fixture)
        fees = {k: dict(v) for k, v in scalp.VENUES.items()}
        # fixture: 마지막 1440봉을 평가 구간으로
        t0 = None
        for v in data.values():
            for bars in v.values():
                cand = bars[max(0, len(bars) - scalp.EVAL_BARS)]["t"]
                t0 = cand if t0 is None else max(t0, cand)
        return data, fees, t0, "fixture", "오프라인 CSV"
    try:
        with open(a.bars, encoding="utf-8") as fp:
            c = json.load(fp)
        return c["bars"], c["fees"], c["eval_t0"], c["eval_day_utc"], "bars_cache.json 재사용"
    except (IOError, ValueError, KeyError):
        pass
    fees = {k: dict(v) for k, v in scalp.VENUES.items()}
    live = scalp.hl_live_fees()
    if live:
        fees["hyperliquid"].update(live)
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end = int(today.timestamp())
    d0 = end - scalp.EVAL_BARS * 60
    start = d0 - scalp.WARMUP_MIN * 60
    data = {}
    for venue, fn in scalp.SOURCES:
        got = {}
        for coin in scalp.COINS:
            try:
                got[coin] = fn(coin, start, end)
            except Exception:
                pass
            time.sleep(0.2)
        if got:
            data[venue] = got
    return data, fees, d0, (today - timedelta(days=1)).strftime("%Y-%m-%d"), "직접 수집"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", default="bars_cache.json")
    ap.add_argument("--fixture")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--no-funding", action="store_true", help="펀딩 이벤트 조회(네트워크) 생략")
    ap.add_argument("--out", default="research.json")
    ap.add_argument("--history", default="research_history.jsonl")
    a = ap.parse_args(argv)
    if a.report:
        return report(a.history)

    data, fees, eval_t0, eval_day, mode = load_bars(a)
    now = datetime.now(KST)
    if eval_day != "fixture":
        # 기록 날짜는 평가일 다음 날(KST). 되채우기로 지난 날을 받았을 때도 그날 자리에 들어가게.
        rec_day = datetime.strptime(eval_day, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
        if rec_day.date() < now.date():
            now = rec_day.astimezone(KST).replace(hour=9, minute=18)
    d1 = eval_t0 + scalp.EVAL_BARS * 60

    # A1
    hourly, hourly_by_venue, costs = {}, {}, {}
    for venue, coins in data.items():
        if venue not in fees:
            continue
        acc = [[] for _ in range(24)]
        for coin, bars in coins.items():
            es = next((i for i, b in enumerate(bars) if b["t"] >= eval_t0), len(bars))
            if len(bars) - es < 120:
                continue
            cost = cost_of(venue, bars, es, fees[venue])
            costs["%s:%s" % (venue, coin)] = cost
            hh = hourly_hurdle(bars, es, cost["round_trip_taker_pct"])
            hourly["%s:%s" % (venue, coin)] = hh
            for h, x in enumerate(hh["h15_needed_win_rate_pct"]):
                if x is not None:
                    acc[h].append(x)
        hourly_by_venue[venue] = [r4(mean(x)) for x in acc]
    hours_under_100 = [(v, kst_hour(h)) for v, hs in hourly_by_venue.items() for h, x in enumerate(hs)
                       if x is not None and x < 100.0]

    # A2
    events, funding_summary, funding_errors = [], {}, {}
    if not a.fixture and not a.no_funding:
        for venue in ("hyperliquid", "okx"):
            if venue not in data:
                continue
            ev_v = []
            for coin, bars in data[venue].items():
                try:
                    evs = fetch_funding_events(venue, coin, eval_t0 - 3600, d1 + 3600)
                    ev_v.extend(funding_event_study(bars, evs, venue, coin, eval_t0))
                except Exception as e:
                    funding_errors["%s/%s" % (venue, coin)] = str(e)[:120]
                time.sleep(0.15)
            events.extend(ev_v)
            funding_summary[venue] = summarize_events(ev_v)

    # B
    leadlag, by_pair = {}, {}
    for lead, follow in LEAD_PAIRS:
        if lead not in data or follow not in data:
            continue
        per = []
        for coin in scalp.COINS:
            if coin in data[lead] and coin in data[follow]:
                r = lead_lag(data[lead][coin], data[follow][coin], eval_t0)
                leadlag["%s->%s:%s" % (lead, follow, coin)] = r
                if r.get("n", 0) >= 30:
                    per.append(r)
        if per:
            agg = {"n": sum(r["n"] for r in per), "coins": len(per),
                   "n_big": sum(r.get("n_big", 0) for r in per)}
            for k in ("corr_same_minute", "corr_lead_next1", "corr_reverse_next1", "follow_next1_bp"):
                agg[k] = r4(wmean([(r.get(k), r["n"]) for r in per]))
            for k in ("follow_big_next1_bp", "follow_big_next5_bp", "follow_big_hit_rate_pct"):
                agg[k] = r4(wmean([(r.get(k), r.get("n_big")) for r in per]))
            by_pair["%s->%s" % (lead, follow)] = agg

    # 판정 문장
    best_pair = max(by_pair.items(), key=lambda kv: kv[1].get("corr_lead_next1") or -9) if by_pair else None
    parts = ["시간대: 15분 본전 승률 100%% 아래 (거래소,시) %d개" % len(hours_under_100)]
    if funding_summary:
        parts.append("펀딩 직후15분: " + ", ".join("%s %+.1fbp(n=%d)" % (v, s.get("signed_post15_bp") or 0, s.get("n", 0))
                                             for v, s in funding_summary.items()))
    if best_pair:
        parts.append("선행성 최고 %s 상관 %.3f, 큰움직임 따라가기 1분 %+.1fbp" % (
            best_pair[0], best_pair[1].get("corr_lead_next1") or 0, best_pair[1].get("follow_big_next1_bp") or 0))
    verdict = " / ".join(parts) + " — 하루치, 누적으로 판단"

    out = {"schema": "carrygate-research/1", "date": now.strftime("%Y-%m-%d"),
           "generated_at_kst": now.strftime("%Y-%m-%d %H:%M:%S"), "eval_day_utc": eval_day, "mode": mode,
           "method": {"A1": "시간대(UTC)별 15분·5분 뒤 |변동폭| 평균과 본전 승률 (1+비용/변동폭)/2. 비용 = 시장가 수수료 2회 + 호가 1틱",
                      "A2": "펀딩 정산 시각 T 기준 [T-15,T], [T,T+15], [T,T+60] 수익률에 sign(펀딩비)를 곱해 평균. 양수면 '펀딩 방향으로 되오름'",
                      "B": "선행 거래소 1분 수익률 r_L(t) 와 후행 거래소 다음 1분 r_F(t+1) 상관. 큰움직임 = |r_L| > %.0fσ 일 때 r_L 방향으로 따라간 bp" % BIG_SIGMA},
           "costs": costs, "hourly": hourly, "hourly_by_venue": hourly_by_venue,
           "funding_events": events, "funding_summary": funding_summary, "funding_errors": funding_errors,
           "leadlag": leadlag, "leadlag_by_pair": by_pair,
           "summary": {"hours_under_100": hours_under_100, "verdict": verdict}}
    print_day(out)
    if a.dry_run:
        print("\n(dry-run: 저장하지 않음)")
        return 0
    with open(a.out, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    append_history({"date": out["date"], "eval_day_utc": eval_day, "hourly_by_venue": hourly_by_venue,
                    "funding_summary": funding_summary, "leadlag_by_pair": by_pair,
                    "summary": out["summary"]}, a.history)
    return 0


if __name__ == "__main__":
    sys.exit(main())
