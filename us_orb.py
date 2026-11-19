# -*- coding: utf-8 -*-
"""
CARRYGATE — 미국 주식 개장 범위 돌파 종이매매 (제안함 P40, 단기·당일 청산). "개장 15분 고가를 종가로 넘으면 산다."

왜 만들었나
  2026-11-19 사용자가 공개 저장소 Jakeschincariol/day-trader-skill(Claude 스킬 11개)을 보고 "진입 청산 판단해서 페이퍼 돌려보자".
  그 스킬은 AI 가 단계마다 판단하지만, 이 저장소는 매매 판단에 AI 를 쓰지 않는다(원칙). 그래서 스킬 문서에 적힌
  기본값 규칙(trader-entry·trader-exit SKILL.md)만 고정 코드로 옮겨 순방향 종이 기록을 쌓는다. 주문 없음, 키 없음.

규칙 (고정값, 사후 조정 금지 — 종목은 UNIVERSE 에 추가만)
  봉         미국 정규장 5분봉(뉴욕 시각). 닫힌 봉만 쓴다.
  개장 범위   09:30·09:35·09:40 세 봉의 최고 고가(범위 고가)·최저 저가(범위 저가). 셋 중 하나라도 없으면 그날 그 종목은 건너뜀.
  진입       범위 뒤 처음으로 종가가 범위 고가보다 '엄격히' 높은 5분봉의 종가에 매수. 꼬리만 넘은 것은 아님.
             종목·하루 1번. 그 봉이 닫히는 시각이 15:30 이하일 때만(조기 폐장일 12:30). 매수만(공매도 없음).
  손절       범위 중간값((고가+저가)/2), 센트 내림. 위험 R = 진입가 − 손절가. R 이 진입가의 0.05% 미만이면 진입 안 함.
  목표       진입가 + 2R, 센트 반올림.
  청산       진입 다음 봉부터: 시가가 손절 이하면 시가(갭), 저가가 손절 이하면 손절가, 고가가 목표 이상이면 목표가.
             한 봉에 둘 다 닿으면 손절 먼저(보수적). 둘 다 아니면 15:50 봉 종가(=15:55, 조기 폐장일 12:50 봉)에 정리. 하룻밤 보유 없음.
  비용       체결 1번당 FEE_PCT 0.05% (수수료 0 가정, 호가폭·미끄러짐 몫). 왕복 0.10%.
  크기       매 거래 같은 금액. 성적은 건당 순수익률(%) 로 센다.
  자료       야후 5분봉(무료·비공식, 키 없음). 순방향은 FORWARD_FROM 세션부터.

첫 판단 (사전 등록, proposals.md P40)
  순방향 20세션 뒤: 건당 평균 순수익 > 0 이고 t ≥ 2 → 후보(별도 저장소 carrygate-exec 에서 모의계좌 체결 시험 설계).
  건당 평균 ≤ 0 → 탈락(기록은 계속). 그 사이면 20세션 더.

사용법
  python us_orb.py                          # 수집·계산·저장 (us_orb.json, us_orb_history.jsonl)
  python us_orb.py --dry-run                # 저장 안 함
  python us_orb.py --report                 # 저장된 순방향 기록 요약
  python us_orb.py --bars-dir docs/cards/data --backtest   # us_<종목>_5m.csv 로 사후 계산(인터넷 불필요, 저장 안 함)
"""
import os, sys, csv, json, math, argparse
from datetime import datetime, timezone, timedelta

from common import KST, UA, r2, r4, mean, load_history, append_history

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(HERE, "us_orb.json")
HISTORY_PATH = os.path.join(HERE, "us_orb_history.jsonl")

RULE_FIXED = "2026-11-19"
FORWARD_FROM = "2026-11-19"      # 뉴욕 세션 날짜. 규칙은 이 세션 개장(14:30 UTC) 전에 고정했다.
# 스킬은 ETF 를 빼고 거래대금 큰 순으로 본다. 여기서는 거래대금 큰 미국 보통주 20개를 고정(추가만).
UNIVERSE = ["NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "AMD", "AVGO", "PLTR",
            "NFLX", "MU", "INTC", "COIN", "MSTR", "ORCL", "JPM", "BAC", "XOM", "UNH"]
BAR_MIN = 5
OPEN_HM = 9 * 60 + 30
OR_MINUTES = 15
NO_ENTRY_AFTER_HM = 15 * 60 + 30     # 진입 봉이 닫히는 시각 상한
FLAT_BY_HM = 15 * 60 + 55
EARLY_CLOSE_SHIFT = 180              # 조기 폐장(13:00)일은 두 시각을 3시간 당긴다 → 12:30 / 12:55
EARLY_CLOSES = ["2026-11-27", "2026-12-24", "2027-11-26"]   # NYSE 13:00 폐장일. 추가만
STOP_MODE = "or-mid"
TARGET_R = 2.0
MIN_RISK_PCT = 0.05
FEE_PCT = 0.05                       # 체결 1번당
JUDGE_SESSIONS = 20
JUDGE_T = 2.0


def rules_dict():
    return {"universe": list(UNIVERSE), "bar_minutes": BAR_MIN, "or_minutes": OR_MINUTES,
            "entry": "first closed bar after the range with close > range high (strict), one per symbol per day, long only",
            "no_entry_after": "15:30", "flat_by": "15:55", "early_closes": list(EARLY_CLOSES),
            "early_close_shift_min": EARLY_CLOSE_SHIFT, "stop": STOP_MODE, "target_r": TARGET_R,
            "min_risk_pct": MIN_RISK_PCT, "same_bar": "stop first", "fee_pct_per_fill": FEE_PCT,
            "forward_from": FORWARD_FROM, "judge_sessions": JUDGE_SESSIONS, "judge_t": JUDGE_T,
            "source": "yahoo chart 5m regular session"}


# ----------------------------------------------------------------- 시각

def _nth_sunday(year, month, n):
    d = datetime(year, month, 1)
    first = (6 - d.weekday()) % 7 + 1
    return first + 7 * (n - 1)


def ny_offset_sec(ts):
    """미국 동부 서머타임(3월 둘째 일요일 07:00 UTC ~ 11월 첫째 일요일 06:00 UTC)이면 −4h, 아니면 −5h."""
    y = datetime.fromtimestamp(ts, timezone.utc).year
    start = datetime(y, 3, _nth_sunday(y, 3, 2), 7, tzinfo=timezone.utc).timestamp()
    end = datetime(y, 11, _nth_sunday(y, 11, 1), 6, tzinfo=timezone.utc).timestamp()
    return -4 * 3600 if start <= ts < end else -5 * 3600


def ny_parts(ts):
    """(뉴욕 날짜 'YYYY-MM-DD', 자정부터 분)."""
    d = datetime.fromtimestamp(ts + ny_offset_sec(ts), timezone.utc)
    return d.strftime("%Y-%m-%d"), d.hour * 60 + d.minute


def hm_str(hm):
    return "%02d:%02d" % (hm // 60, hm % 60)


def split_sessions(bars):
    """봉 목록 → {뉴욕 날짜: [정규장 5분봉, 'hm' 붙임]}. 장전·장후·격자 밖 봉은 버린다."""
    out = {}
    for b in bars:
        if None in (b.get("o"), b.get("h"), b.get("l"), b.get("c")) or b["c"] <= 0:
            continue
        day, hm = ny_parts(b["t"])
        if hm < OPEN_HM or hm >= 16 * 60 or (hm - OPEN_HM) % BAR_MIN:
            continue
        nb = dict(b); nb["hm"] = hm
        out.setdefault(day, {})[hm] = nb
    return {d: [v[k] for k in sorted(v)] for d, v in out.items()}


# ----------------------------------------------------------------- 규칙 (순수 함수)

def floor_cent(x):
    return math.floor(x * 100 + 1e-9) / 100.0


def round_cent(x):
    return math.floor(x * 100 + 0.5 + 1e-9) / 100.0


def session_times(day):
    shift = EARLY_CLOSE_SHIFT if day in EARLY_CLOSES else 0
    return NO_ENTRY_AFTER_HM - shift, FLAT_BY_HM - shift


def simulate_day(day, bars):
    """한 종목 한 세션. 돌려주는 것: {'status': 'trade'|'no_trigger'|'no_range'|'incomplete'|'risk_small', ...거래 칸}."""
    no_entry_after, flat_by = session_times(day)
    by = {b["hm"]: b for b in bars}
    rng = [by.get(OPEN_HM + i * BAR_MIN) for i in range(OR_MINUTES // BAR_MIN)]
    if any(b is None for b in rng):
        return {"status": "no_range"}
    if not any(b["hm"] >= flat_by - BAR_MIN for b in bars):
        return {"status": "incomplete"}       # 정리 봉까지 자료가 없다(장중 실행·자료 끊김)
    or_high = max(b["h"] for b in rng); or_low = min(b["l"] for b in rng)
    after = [b for b in bars if b["hm"] >= OPEN_HM + OR_MINUTES and b["hm"] < flat_by]
    ei = None
    for i, b in enumerate(after):
        if b["hm"] + BAR_MIN > no_entry_after:
            break
        if b["c"] > or_high:
            ei = i
            break
    base = {"or_high": r4(or_high), "or_low": r4(or_low)}
    if ei is None:
        return dict(base, status="no_trigger")
    e = after[ei]
    entry = e["c"]
    stop = floor_cent((or_high + or_low) / 2.0)
    risk = entry - stop
    if risk < entry * MIN_RISK_PCT / 100.0:
        return dict(base, status="risk_small")
    target = round_cent(entry + TARGET_R * risk)
    exit_px, reason, xb = None, None, None
    for b in after[ei + 1:]:
        if b["o"] <= stop:
            exit_px, reason, xb = b["o"], "stop_gap", b
        elif b["l"] <= stop:
            exit_px, reason, xb = stop, "stop", b
        elif b["h"] >= target:
            exit_px, reason, xb = target, "target", b
        if reason:
            break
    if reason is None:
        xb = after[-1]
        exit_px, reason = xb["c"], "flat"
    gross = (exit_px / entry - 1) * 100
    return dict(base, status="trade", entry_t=e["t"] + BAR_MIN * 60, entry_hm=hm_str(e["hm"] + BAR_MIN), entry=r4(entry),
                stop=r4(stop), target=r4(target), exit_t=xb["t"] + (BAR_MIN * 60 if reason == "flat" else 0),
                exit_hm=hm_str(xb["hm"] + (BAR_MIN if reason == "flat" else 0)), exit=r4(exit_px), reason=reason,
                r=r2((exit_px - entry) / risk), gross_pct=r4(gross), net_pct=r4(gross - 2 * FEE_PCT))


def day_record(day, per_symbol):
    """per_symbol: {종목: simulate_day 결과}. 세션 한 줄."""
    trades = []
    for sym in sorted(per_symbol):
        res = per_symbol[sym]
        if res["status"] == "trade":
            t = {k: v for k, v in res.items() if k != "status"}
            t["sym"] = sym
            trades.append(t)
    nets = [t["net_pct"] for t in trades]
    skipped = {s: r["status"] for s, r in sorted(per_symbol.items()) if r["status"] in ("no_range", "incomplete", "risk_small")}
    return {"date": day, "forward": day >= FORWARD_FROM, "symbols": len(per_symbol), "n": len(trades),
            "wins": sum(1 for x in nets if x > 0), "sum_net_pct": r4(sum(nets)), "avg_net_pct": r4(mean(nets)) if nets else None,
            "no_trigger": sum(1 for r in per_symbol.values() if r["status"] == "no_trigger"),
            "skipped": skipped, "trades": trades}


def aggregate(rows):
    """세션 줄 목록 → 누적 요약(건당 평균·t·사유별)."""
    nets = [t["net_pct"] for r in rows for t in r.get("trades", [])]
    n = len(nets)
    out = {"sessions": len(rows), "trades": n, "sum_net_pct": r2(sum(nets)), "avg_net_pct": r4(mean(nets)) if n else None,
           "win_rate_pct": r2(100.0 * sum(1 for x in nets if x > 0) / n) if n else None, "t": None,
           "plus_sessions": sum(1 for r in rows if (r.get("sum_net_pct") or 0) > 0), "by_reason": {}}
    if n >= 2:
        m = mean(nets)
        sd = math.sqrt(sum((x - m) ** 2 for x in nets) / (n - 1))
        out["t"] = r2(m / (sd / math.sqrt(n))) if sd > 0 else None
    for r in rows:
        for t in r.get("trades", []):
            g = out["by_reason"].setdefault(t["reason"], {"n": 0, "sum_net_pct": 0.0})
            g["n"] += 1
            g["sum_net_pct"] = r4(g["sum_net_pct"] + t["net_pct"])
    return out


def judge(agg):
    """사전 등록 첫 판단. 매매 신호가 아니라 '다음 단계로 갈지'만."""
    if agg["sessions"] < JUDGE_SESSIONS or not agg["trades"]:
        return "기록 중 (%d/%d 세션)" % (agg["sessions"], JUDGE_SESSIONS)
    if agg["avg_net_pct"] <= 0:
        return "탈락 (건당 평균 ≤ 0)"
    if agg["t"] is not None and agg["t"] >= JUDGE_T:
        return "후보 (건당 평균 > 0 · t ≥ %.0f)" % JUDGE_T
    return "보류 (플러스지만 t < %.0f — %d세션 더)" % (JUDGE_T, JUDGE_SESSIONS)


def compute_sessions(series, complete_before=None):
    """series: {종목: 봉 목록}. 세션 날짜별 day_record 목록(날짜순). complete_before(뉴욕 날짜) 이상인 날은 뺀다(아직 장중)."""
    per_day = {}
    for sym, bars in series.items():
        for day, sb in split_sessions(bars).items():
            if complete_before and day >= complete_before:
                continue
            per_day.setdefault(day, {})[sym] = simulate_day(day, sb)
    out = []
    for day in sorted(per_day):
        rec = day_record(day, per_day[day])
        if rec["symbols"] and len(rec["skipped"]) < rec["symbols"]:
            out.append(rec)
    return out


# ----------------------------------------------------------------- 자료

def yahoo_5m(symbol, rng="5d"):
    import urllib.request, urllib.parse, time
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=5m&includePrePost=false" % (urllib.parse.quote(symbol), rng)
    last = None
    for i in range(3):
        try:
            hdr = dict(UA); hdr["User-Agent"] = "Mozilla/5.0"
            with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=20) as r:
                d = json.loads(r.read().decode("utf-8"))
            res = (d.get("chart") or {}).get("result") or []
            if not res:
                raise RuntimeError("yahoo: result 없음")
            ts = res[0].get("timestamp") or []
            q = (res[0].get("indicators") or {}).get("quote", [{}])[0]
            out = []
            for j, t in enumerate(ts):
                o, h, l, c, v = ((q.get(k) or [None] * len(ts))[j] for k in ("open", "high", "low", "close", "volume"))
                if None in (o, h, l, c):
                    continue
                out.append({"t": int(t), "o": float(o), "h": float(h), "l": float(l), "c": float(c), "v": float(v or 0)})
            return out
        except Exception as e:
            last = e
            time.sleep(1.5 + i * 2)
    raise RuntimeError("yahoo 5m %s: %s" % (symbol, last))


def csv_bars(path):
    out = []
    with open(path, encoding="utf-8") as fp:
        for r in csv.DictReader(fp):
            out.append({"t": int(float(r["t"])), "o": float(r["o"]), "h": float(r["h"]), "l": float(r["l"]),
                        "c": float(r["c"]), "v": float(r.get("v") or 0)})
    return out


def load_dir(bars_dir):
    series = {}
    for sym in UNIVERSE:
        p = os.path.join(bars_dir, "us_%s_5m.csv" % sym.lower())
        if os.path.exists(p):
            series[sym] = csv_bars(p)
    return series


# ----------------------------------------------------------------- 실행

def build(history_rows, errors, now=None):
    now = now or datetime.now(KST)
    fwd = [r for r in history_rows if r.get("date", "") >= FORWARD_FROM]
    agg = aggregate(fwd)
    verdict = judge(agg)
    last = fwd[-1] if fwd else None
    summary = ("순방향 %d세션 %d건, 건당 %s%%, 합 %s%% — %s" % (agg["sessions"], agg["trades"], agg["avg_net_pct"], agg["sum_net_pct"], verdict)
               if fwd else "순방향 기록 전 (첫 세션 %s)" % FORWARD_FROM)
    return {"date": now.strftime("%Y-%m-%d"), "generated_at_kst": now.strftime("%Y-%m-%d %H:%M"),
            "rules": dict(rules_dict(), rule_fixed=RULE_FIXED), "last_session": last["date"] if last else None,
            "last": last, "forward": agg, "verdict": verdict, "recent": [{k: r.get(k) for k in ("date", "n", "wins", "sum_net_pct", "avg_net_pct")} for r in fwd[-10:]],
            "errors": errors, "summary": summary}


def report(rows, title):
    print("=== %s ===" % title)
    print("%-10s %3s %3s %9s %9s" % ("세션", "건", "승", "합%", "건당%"))
    for r in rows:
        print("%-10s %3d %3d %9.2f %9s" % (r["date"], r["n"], r["wins"], r["sum_net_pct"], "-" if r["avg_net_pct"] is None else "%.3f" % r["avg_net_pct"]))
    a = aggregate(rows)
    print("누적: %d세션 %d건 | 건당 %s%% | 합 %s%% | 승률 %s%% | t %s | 플러스 세션 %d | 사유별 %s" % (
        a["sessions"], a["trades"], a["avg_net_pct"], a["sum_net_pct"], a["win_rate_pct"], a["t"], a["plus_sessions"],
        json.dumps(a["by_reason"], ensure_ascii=False)))
    return a


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--bars-dir")
    ap.add_argument("--backtest", action="store_true", help="--bars-dir 의 모든 세션을 사후 계산해 표로만 보여준다")
    a = ap.parse_args()

    if a.report:
        rows = [r for r in load_history(HISTORY_PATH) if r.get("date", "") >= FORWARD_FROM]
        if not rows:
            print("순방향 기록 없음 (첫 세션 %s)" % FORWARD_FROM)
            return 0
        report(rows, "미국 주식 개장 범위 돌파 — 순방향")
        return 0

    errors = {}
    if a.bars_dir:
        series = load_dir(a.bars_dir)
    else:
        series = {}
        for sym in UNIVERSE:
            try:
                series[sym] = yahoo_5m(sym)
            except Exception as e:
                errors[sym] = str(e)[:160]
    if not series:
        print("봉 자료 없음:", json.dumps(errors, ensure_ascii=False)[:400])
        return 1

    if a.backtest:
        rows = compute_sessions(series)
        report(rows, "사후 계산 (약한 근거, 규칙 고정 전 자료)")
        return 0

    # 오늘(뉴욕) 세션은 장이 끝난 뒤(16:00 이후)에만 센다.
    now_ts = datetime.now(timezone.utc).timestamp()
    ny_day, ny_hm = ny_parts(now_ts)
    complete_before = ny_day if ny_hm < 16 * 60 + 5 else None
    rows = [r for r in compute_sessions(series, complete_before) if r["date"] >= FORWARD_FROM]
    for r in rows:
        print("세션 %s: %d종목 %d건 %d승 합 %s%% (건너뜀 %s)" % (r["date"], r["symbols"], r["n"], r["wins"], r["sum_net_pct"], r["skipped"] or "없음"))
        if not a.dry_run:
            append_history(r, HISTORY_PATH)
    hist = load_history(HISTORY_PATH)
    if a.dry_run:
        seen = {r["date"] for r in rows}
        hist = sorted([h for h in hist if h.get("date") not in seen] + rows, key=lambda r: r["date"])
    out = build(hist, errors)
    print(out["summary"])
    if errors:
        print("수집 실패:", json.dumps(errors, ensure_ascii=False)[:400])
    if not a.dry_run:
        with open(OUT_PATH, "w", encoding="utf-8") as fp:
            json.dump(out, fp, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
