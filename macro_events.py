# -*- coding: utf-8 -*-
"""
CARRYGATE — 매크로 발표 전후 반응 연구 (⑤)

미국 CPI·고용(NFP)·FOMC 금리결정·PCE·GDP 발표 시각 전후로 BTC·ETH 가 얼마나, 어느 방향으로
움직이는지 잰다. 초단타가 비용 문턱을 넘지 못하는 이유는 평소 변동폭이 작기 때문인데,
발표 직후는 변동폭이 커지는 예외 구간이다. 질문은 두 가지다.
  (1) 발표 뒤 30분 변동폭이 평소(전날 같은 시각)의 몇 배인가
  (2) 발표가 예상보다 높았는지/낮았는지(서프라이즈)로 방향을 맞힐 수 있나

달력: data/macro_calendar.json (TradingView 경제 캘린더에서 받아 둔 것. 매달 갱신 필요)
가격: 하이퍼리퀴드 1분봉 (발표 ±90분). 이미 기록한 이벤트는 다시 받지 않는다.
주문 기능 없음. 읽기 전용 공개 API 만 쓴다.

사용법
  python macro_events.py            # 새 이벤트 처리·저장 (macro.json 누적)
  python macro_events.py --dry-run
  python macro_events.py --report
"""
import json, sys, time, math, argparse
from datetime import datetime, timezone

from scalp import http_json, f, KST

CAL_PATH = "data/macro_calendar.json"
OUT_PATH = "macro.json"
COINS = ["BTC", "ETH"]
PRE_MIN = 30
POST = (5, 15, 30, 60)
# 서프라이즈 부호 -> 코인 가격 방향 가설. +1: 예상보다 높으면 오른다, -1: 예상보다 높으면 내린다.
# 물가·고용이 예상보다 뜨거우면 금리 인하 기대가 줄어 위험자산에 불리(−1). 금리결정은 "높으면 불리"(−1).
SURPRISE_SIGN = {"cpi": -1, "nfp": -1, "fomc": -1, "pce": -1, "gdp": -1}


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def r3(x):
    return None if x is None else round(x, 3)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fp:
            return json.load(fp)
    except (IOError, ValueError):
        return default


def hl_1m(coin, start, end):
    rows = http_json("https://api.hyperliquid.xyz/info",
                     data={"type": "candleSnapshot", "req": {"coin": coin, "interval": "1m",
                                                             "startTime": start * 1000, "endTime": end * 1000}})
    return {int(r["t"]) // 1000: (f(r["o"]), f(r["h"]), f(r["l"]), f(r["c"])) for r in rows}


def okx_1m(coin, start, end):
    """OKX 선물 1분봉 (과거 이력이 깊다). after= 로 과거 방향 페이지."""
    out, after = {}, end * 1000
    for _ in range(4):
        d = http_json("https://www.okx.com/api/v5/market/history-candles?instId=%s-USDT-SWAP&bar=1m&limit=100&after=%d" % (coin, after))
        rows = d.get("data") or []
        if not rows:
            break
        for r in rows:
            t = int(r[0]) // 1000
            if start <= t < end:
                out[t] = (f(r[1]), f(r[2]), f(r[3]), f(r[4]))
        after = min(int(r[0]) for r in rows)
        if after // 1000 <= start:
            break
        time.sleep(0.12)
    return out


def get_1m(coin, start, end):
    """하이퍼리퀴드 1분봉, 없으면 OKX. (출처, 봉) 을 돌려준다."""
    try:
        b = hl_1m(coin, start, end)
        if len(b) >= (end - start) // 60 * 0.8:
            return "hyperliquid", b
    except Exception:
        pass
    b = okx_1m(coin, start, end)
    return ("okx", b) if b else (None, {})


def reaction(bars, ts):
    """발표 시각 ts(분 경계) 기준: 직전 30분 수익률, 직후 5·15·30·60분 수익률(bp), 60분 내 최대 상승·하락폭."""
    t0 = ts - ts % 60
    if t0 not in bars or t0 - PRE_MIN * 60 not in bars:
        return None
    p0 = bars[t0][0]                 # 발표 분의 시가 = 발표 직전 가격
    out = {"pre%d_bp" % PRE_MIN: round((p0 / bars[t0 - PRE_MIN * 60][0] - 1.0) * 1e4, 2)}
    for h in POST:
        t1 = t0 + h * 60
        if t1 in bars:
            out["post%d_bp" % h] = round((bars[t1][0] / p0 - 1.0) * 1e4, 2)
    hi = max((bars[t][1] for t in range(t0, t0 + 3600, 60) if t in bars), default=None)
    lo = min((bars[t][2] for t in range(t0, t0 + 3600, 60) if t in bars), default=None)
    if hi and lo:
        out["maxup60_bp"] = round((hi / p0 - 1.0) * 1e4, 2)
        out["maxdown60_bp"] = round((lo / p0 - 1.0) * 1e4, 2)
    return out if "post30_bp" in out else None


def process_event(ev, coin):
    ts = ev["ts"]
    src, bars = get_1m(coin, ts - 5400, ts + 5400)
    r = reaction(bars, ts) if bars else None
    if not r:
        return None
    _, base = get_1m(coin, ts - 86400 - 5400, ts - 86400 + 5400)     # 전날 같은 시각 = 평소 기준
    b = reaction(base, ts - 86400) if base else None
    rec = {"type": ev["type"], "title": ev["title"], "time_utc": ev["time_utc"], "ts": ts, "coin": coin, "source": src,
           "actual": ev.get("actual"), "forecast": ev.get("forecast"), "previous": ev.get("previous")}
    rec.update(r)
    rec["base_abs_post30_bp"] = abs(b["post30_bp"]) if b else None
    a, fc = ev.get("actual"), ev.get("forecast")
    if a is not None and fc is not None:
        d = a - fc
        rec["surprise"] = d
        rec["surprise_dir"] = (1 if d > 0 else (-1 if d < 0 else 0)) * SURPRISE_SIGN.get(ev["type"], -1)
    return rec


def summarize(events):
    """이벤트 종류별: 변동폭 배수, 서프라이즈 방향 적중률, 방향대로 30분 보유했을 때 bp."""
    out = {}
    for typ in sorted(set(e["type"] for e in events)):
        es = [e for e in events if e["type"] == typ]
        absm = [abs(e["post30_bp"]) for e in es]
        base = [e["base_abs_post30_bp"] for e in es if e.get("base_abs_post30_bp") is not None]
        sur = [e for e in es if e.get("surprise_dir")]
        signed = [e["surprise_dir"] * e["post30_bp"] for e in sur]
        signed60 = [e["surprise_dir"] * e["post60_bp"] for e in sur if e.get("post60_bp") is not None]
        out[typ] = {"n": len(es), "n_surprise": len(sur),
                    "abs_post30_bp": r3(mean(absm)), "base_abs_post30_bp": r3(mean(base)),
                    "ratio_vs_base": r3(mean(absm) / mean(base)) if base and mean(base) else None,
                    "abs_post5_bp": r3(mean([abs(e["post5_bp"]) for e in es if e.get("post5_bp") is not None])),
                    "maxup60_bp": r3(mean([e["maxup60_bp"] for e in es if e.get("maxup60_bp") is not None])),
                    "maxdown60_bp": r3(mean([e["maxdown60_bp"] for e in es if e.get("maxdown60_bp") is not None])),
                    "signed_post30_bp": r3(mean(signed)),
                    "signed_post60_bp": r3(mean(signed60)),
                    "hit_rate_pct": round(100.0 * sum(1 for x in signed if x > 0) / len(signed), 1) if signed else None}
    allm = [abs(e["post30_bp"]) for e in events]
    allb = [e["base_abs_post30_bp"] for e in events if e.get("base_abs_post30_bp") is not None]
    out["all"] = {"n": len(events), "abs_post30_bp": r3(mean(allm)), "base_abs_post30_bp": r3(mean(allb)),
                  "ratio_vs_base": r3(mean(allm) / mean(allb)) if allb and mean(allb) else None}
    return out


def print_summary(s, n_events, upcoming):
    print("%-6s %4s %8s %8s %6s %8s %8s %9s %6s" % ("종류", "n", "30분폭bp", "평소bp", "배수", "60분최대↑", "60분최대↓", "방향30분bp", "적중%"))
    for typ, v in s.items():
        if typ == "all":
            continue
        print("%-6s %4d %8s %8s %6s %8s %8s %9s %6s"
              % (typ, v["n"], v["abs_post30_bp"], v["base_abs_post30_bp"], v["ratio_vs_base"],
                 v["maxup60_bp"], v["maxdown60_bp"], v["signed_post30_bp"], v["hit_rate_pct"]))
    a = s.get("all", {})
    print("전체   n=%d  30분 변동폭 %s bp vs 평소 %s bp (%s배)" % (a.get("n", 0), a.get("abs_post30_bp"), a.get("base_abs_post30_bp"), a.get("ratio_vs_base")))
    if upcoming:
        print("다음 발표: " + ", ".join("%s %s" % (e["type"], e["time_utc"]) for e in upcoming[:5]))


def report(path=OUT_PATH):
    d = load_json(path, None)
    if not d or not d.get("events"):
        print("기록 없음 — macro_events.py 가 실행되면서 쌓인다.")
        return 0
    print("=== 매크로 발표 전후 반응 누적 (이벤트 %d건, 코인 %s) ===" % (len(d["events"]), ", ".join(COINS)))
    print_summary(d["summary"], len(d["events"]), d.get("upcoming", []))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--calendar", default=CAL_PATH)
    ap.add_argument("--out", default=OUT_PATH)
    ap.add_argument("--max-new", type=int, default=80, help="한 번에 처리할 최대 (이벤트×코인) 수")
    a = ap.parse_args(argv)
    if a.report:
        return report(a.out)

    cal = load_json(a.calendar, {"events": []})
    prev = load_json(a.out, {"events": []})
    done = set((e["type"], e["ts"], e["coin"]) for e in prev.get("events", []))
    skipped = {tuple(k.split("|")): v for k, v in (prev.get("skipped") or {}).items()}
    now = time.time()
    new, errors, processed = [], {}, 0
    # 최신 이벤트부터. 오래된 것은 1분봉이 없을 수 있다 — 한 번 없다고 확인되면 skipped 에 적고 다시 받지 않는다.
    for ev in sorted(cal.get("events", []), key=lambda e: -e["ts"]):
        if ev["ts"] + 7200 > now:          # 발표 뒤 2시간이 지나야 60분 뒤 가격까지 있다
            continue
        for coin in COINS:
            key = (ev["type"], str(ev["ts"]), coin)
            if (ev["type"], ev["ts"], coin) in done or key in skipped or processed >= a.max_new:
                continue
            try:
                rec = process_event(ev, coin)
                if rec:
                    new.append(rec)
                else:
                    skipped[key] = "1분봉 없음 (하이퍼리퀴드·OKX 모두)"
            except Exception as e:
                errors["%s@%s/%s" % (ev["type"], ev["time_utc"], coin)] = str(e)[:120]
            processed += 1
            time.sleep(0.15)
    events = sorted(prev.get("events", []) + new, key=lambda e: (e["ts"], e["coin"]))
    upcoming = [{"type": e["type"], "title": e["title"], "time_utc": e["time_utc"]}
                for e in sorted(cal.get("events", []), key=lambda e: e["ts"]) if e["ts"] > now]
    summary = summarize(events) if events else {}
    now_kst = datetime.now(KST)
    cal_last = max((e["ts"] for e in cal.get("events", [])), default=0)
    stale = cal_last < now + 14 * 86400
    out = {"schema": "carrygate-macro/1", "date": now_kst.strftime("%Y-%m-%d"),
           "generated_at_kst": now_kst.strftime("%Y-%m-%d %H:%M:%S"),
           "method": {"window": "발표 분의 시가를 기준가로, 직전 %d분과 직후 %s분 수익률(bp), 60분 내 최대 상승·하락" % (PRE_MIN, POST),
                      "baseline": "전날 같은 시각의 30분 변동폭 = 평소 기준",
                      "surprise": "실제−예상 의 부호 × 가설 부호(%s). 양수면 '가설 방향대로 움직였다'" % SURPRISE_SIGN},
           "calendar_last_event_utc": datetime.fromtimestamp(cal_last, timezone.utc).strftime("%Y-%m-%d") if cal_last else None,
           "calendar_stale": stale,
           "events": events, "new_today": len(new), "errors": errors,
           "skipped": {"|".join(k): v for k, v in skipped.items()}, "upcoming": upcoming, "summary": summary}
    print("=== 매크로 발표 전후 (%s) — 새로 처리 %d건, 누적 %d건, 자료 없어 건너뜀 %d건 ===" % (out["date"], len(new), len(events), len(skipped)))
    if events:
        print_summary(summary, len(events), upcoming)
    if errors:
        print("오류 %d건: %s" % (len(errors), json.dumps(dict(list(errors.items())[:5]), ensure_ascii=False)))
    if stale:
        print("주의: 달력의 마지막 이벤트가 %s 다. data/macro_calendar.json 을 갱신해야 한다." % out["calendar_last_event_utc"])
    if a.dry_run:
        print("\n(dry-run: 저장하지 않음)")
        return 0
    with open(a.out, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
