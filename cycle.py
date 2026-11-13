# -*- coding: utf-8 -*-
"""
CARRYGATE — BTC 사이클 낙폭 분할매수 기록 (제안함 P36, 장기). "바닥을 맞히지 않고, 정해 둔 자리에서만 산다."

왜 만들었나
  2026-11-13 SNS 글 "사상 최고가 뒤 402일째, 과거 사이클은 −74~−81% 였는데 지금은 −41%" (차트 @CryptoReviewing).
  실데이터(Bitstamp 일봉 종가)로 확인: 최고 종가 124,728 (2025-10-06), 11/13 기준 −45%, 이번 사이클 최저 종가는 2026-06-30 58,526(−53%).
  과거 바닥 3개(표본 3)는 '지금이 바닥'(얕은 사이클)과 '아직 −75% 남음'(깊은 사이클) 두 해석을 다 허용한다 → 방향을 고르지 않는다.
  대신 두 경우 모두에서 손해를 줄이는 고정 규칙: 깊어지면 낙폭 계단에서 사고, 바닥이 이미 왔으면 추세가 돌아선 날 산다.

규칙 (고정값, 사후 조정 금지 — 바꾸려면 proposals.md → RULE_FIXED 새 날짜 → python verdict.py --lock)
  자료       Bitstamp BTC/USD 일봉(UTC) 종가. 실패하면 OKX BTC-USDT 현물 일봉(1Dutc). 닫힌 봉만 쓴다.
  기준 고점   ATH_CLOSE = 124,728 (2025-10-06 Bitstamp 종가). 낙폭 dd = 종가 / ATH_CLOSE − 1.
  예산       4 묶음 × 25%. 순방향 첫 봉 FORWARD_FROM(2026-11-13) 부터 센다. 각 묶음은 사이클당 한 번만.
    L50     종가 dd ≤ −50%  (≤ 62,364) 인 첫날 종가에 25%
    L60     종가 dd ≤ −60%  (≤ 49,891) 인 첫날 종가에 25%
    L70     종가 dd ≤ −70%  (≤ 37,418) 인 첫날 종가에 25%
    T200    전날 종가 ≤ 전날 200일 평균, 오늘 종가 > 오늘 200일 평균 (위로 건넘) 이고 오늘 dd ≤ −30% 인 첫날 종가에 25%
  비용       매수마다 수수료 0.10% + 미끄러짐 0.05%. 평가(시가평가)는 팔 때 같은 비용을 뺀 값.
  청산       없음(장기). 종가가 ATH_CLOSE 를 넘는 날 사이클 끝 — 그날 시가평가를 최종 성적으로 남기고 더 사지 않는다.
  비교 기준   주 1회 정액 매수(DCA): FORWARD_FROM 부터 7일마다 종가에 같은 금액. 같은 비용.
  판정       criteria.json cycle (180일 전 판단 금지 — verdict.py judge_cycle).

참고 기록(판정에 안 씀)
  data/cycle_past.json — 2013·2017·2021 사이클 Bitstamp 종가의 '최고가 뒤 n일째 낙폭'. 오늘과 같은 날의 과거 낙폭을 나란히 적는다.

사용법
  python cycle.py                     # 수집·계산·저장 (cycle.json, cycle_history.jsonl)
  python cycle.py --dry-run           # 저장 안 함
  python cycle.py --bars <CSV>        # 저장된 일봉 CSV(t,o,h,l,c,v)로 (인터넷 불필요)
  python cycle.py --report            # 누적 기록
주문 없음, 키 없음, AI 판단 없음.
"""
import os, sys, csv, json, argparse
from datetime import datetime, timezone

from common import http_json, f, KST, r2, r4, load_history

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(HERE, "cycle.json")
HISTORY_PATH = os.path.join(HERE, "cycle_history.jsonl")
PAST_PATH = os.path.join(HERE, "data", "cycle_past.json")

# ----------------------------------------------------------------- 고정 규칙 (사후 조정 금지)
RULE_FIXED = "2026-11-13"
FORWARD_FROM = "2026-11-13"         # 이 UTC 일봉(11/14 00:00 UTC 에 닫힘)부터 순방향
ATH_CLOSE = 124728.0                # Bitstamp BTC/USD 일봉 종가 최고 (2025-10-06)
ATH_DATE = "2025-10-06"
LADDER = [("L50", -0.50), ("L60", -0.60), ("L70", -0.70)]
TREND = ("T200", 200, -0.30)        # (이름, 이동평균 일수, 이 낙폭 이하일 때만)
TRANCHE = 0.25                      # 묶음당 예산 비율
DCA_EVERY_DAYS = 7
FEE_PCT = 0.10
SLIPPAGE_PCT = 0.05
DAY = 86400


def rules_dict():
    return {"source": "bitstamp btcusd 1d close (fallback okx BTC-USDT 1Dutc)", "forward_from": FORWARD_FROM,
            "ath_close": ATH_CLOSE, "ath_date": ATH_DATE, "ladder": [[n, x] for n, x in LADDER],
            "trend": {"name": TREND[0], "sma_days": TREND[1], "max_dd": TREND[2]}, "tranche": TRANCHE,
            "dca_every_days": DCA_EVERY_DAYS, "fee_pct": FEE_PCT, "slippage_pct": SLIPPAGE_PCT,
            "exit": "none; cycle ends on close > ath_close"}


# ----------------------------------------------------------------- 수집

def _day(t):
    return datetime.fromtimestamp(int(t), timezone.utc).strftime("%Y-%m-%d")


def closed_only(bars, now_ts=None):
    now_ts = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
    return [b for b in bars if b["t"] + DAY <= now_ts]


def bitstamp_daily(limit=1000):
    d = http_json("https://www.bitstamp.net/api/v2/ohlc/btcusd/?step=86400&limit=%d" % limit)
    rows = ((d or {}).get("data") or {}).get("ohlc") or []
    out = [{"t": int(r["timestamp"]), "o": f(r["open"]), "h": f(r["high"]), "l": f(r["low"]), "c": f(r["close"]),
            "v": f(r.get("volume")) or 0.0} for r in rows if f(r.get("close"))]
    if len(out) < TREND[1] + 2:
        raise RuntimeError("bitstamp: 봉 %d개" % len(out))
    return sorted(out, key=lambda b: b["t"])


def okx_daily(pages=4):
    out, after = {}, ""
    for _ in range(pages):
        url = "https://www.okx.com/api/v5/market/history-candles?instId=BTC-USDT&bar=1Dutc&limit=100" + (
            "&after=%s" % after if after else "")
        rows = (http_json(url) or {}).get("data") or []
        if not rows:
            break
        for r in rows:
            t = int(r[0]) // 1000
            out[t] = {"t": t, "o": f(r[1]), "h": f(r[2]), "l": f(r[3]), "c": f(r[4]), "v": f(r[5]) or 0.0}
        after = rows[-1][0]
    bars = sorted(out.values(), key=lambda b: b["t"])
    if len(bars) < TREND[1] + 2:
        raise RuntimeError("okx: 봉 %d개" % len(bars))
    return bars


def fetch():
    errs = []
    for name, fn in (("bitstamp", bitstamp_daily), ("okx", okx_daily)):
        try:
            return name, fn(), errs
        except Exception as e:
            errs.append("%s: %s" % (name, e))
    raise RuntimeError("; ".join(errs))


def load_csv(path):
    with open(path, encoding="utf-8") as fp:
        return sorted(({"t": int(float(r["t"])), "o": float(r["o"]), "h": float(r["h"]), "l": float(r["l"]),
                        "c": float(r["c"]), "v": float(r.get("v") or 0)} for r in csv.DictReader(fp)), key=lambda b: b["t"])


# ----------------------------------------------------------------- 계산 (순수 함수)

def sma_series(closes, n):
    out, s = [None] * len(closes), 0.0
    for i, c in enumerate(closes):
        s += c
        if i >= n:
            s -= closes[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def buy_cost_mult():
    return 1.0 + (FEE_PCT + SLIPPAGE_PCT) / 100.0


def sell_value_mult():
    return 1.0 - (FEE_PCT + SLIPPAGE_PCT) / 100.0


def simulate(bars, forward_from=FORWARD_FROM):
    """닫힌 일봉 → 순방향 분할매수·DCA 종이 기록. 같은 봉이면 언제 다시 돌려도 같은 결과(상태 파일 없음)."""
    closes = [b["c"] for b in bars]
    sma = sma_series(closes, TREND[1])
    fills, dca = [], []
    done = {n: False for n, _ in LADDER}
    done[TREND[0]] = False
    cycle_end = None
    first_i = None
    for i, b in enumerate(bars):
        day = _day(b["t"])
        if day < forward_from:
            continue
        if first_i is None:
            first_i = i
        c = b["c"]
        dd = c / ATH_CLOSE - 1.0
        if c > ATH_CLOSE:
            cycle_end = {"date": day, "close": c}
            break
        for name, lv in LADDER:
            if not done[name] and dd <= lv:
                done[name] = True
                fills.append({"name": name, "date": day, "price": c, "dd_pct": r2(dd * 100)})
        tn, _, tmax = TREND
        if (not done[tn] and i > 0 and sma[i] is not None and sma[i - 1] is not None
                and closes[i - 1] <= sma[i - 1] and c > sma[i] and dd <= tmax):
            done[tn] = True
            fills.append({"name": tn, "date": day, "price": c, "dd_pct": r2(dd * 100)})
        if (i - first_i) % DCA_EVERY_DAYS == 0:
            dca.append({"date": day, "price": c})
    return {"fills": fills, "dca": dca, "cycle_end": cycle_end, "sma": sma}


def book(buys, weight, mark):
    """같은 금액(weight) 씩 산 기록 → 투입·수량·평균단가·시가평가 %(파는 비용 뺀 뒤)."""
    if not buys:
        return {"n": 0, "invested": 0.0, "units": 0.0, "avg_cost": None, "mtm_pct": None}
    units = sum(weight / (x["price"] * buy_cost_mult()) for x in buys)
    invested = weight * len(buys)
    value = units * mark * sell_value_mult()
    return {"n": len(buys), "invested": r4(invested), "units": units, "avg_cost": r2(invested / units),
            "mtm_pct": r2((value / invested - 1.0) * 100)}


def load_past(path=PAST_PATH):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (IOError, ValueError):
        return None


def analog(days_since, past):
    """과거 사이클의 같은 날 낙폭, 그날까지의 최저 낙폭과 그 날짜(참고만)."""
    if not past:
        return None
    out = {}
    for name, cy in (past.get("cycles") or {}).items():
        dd = cy.get("dd_by_day") or []
        if days_since < len(dd):
            seg = dd[:days_since + 1]
            lo = min(seg)
            out[name] = {"ath_date": cy.get("ath_date"), "dd_same_day_pct": dd[days_since],
                         "min_so_far_pct": lo, "min_day": seg.index(lo),
                         "cycle_min_pct": cy.get("min_pct"), "cycle_min_day": cy.get("min_day")}
    return out


def build(bars, source="bitstamp", now_ts=None, past=None):
    bars = closed_only(bars, now_ts)
    if not bars:
        raise RuntimeError("닫힌 봉 없음")
    sim = simulate(bars)
    last = bars[-1]
    c = last["c"]
    i = len(bars) - 1
    sma = sim["sma"][i]
    days_since = (datetime.strptime(_day(last["t"]), "%Y-%m-%d") - datetime.strptime(ATH_DATE, "%Y-%m-%d")).days
    lad = book(sim["fills"], TRANCHE, c)
    lad["fills"] = sim["fills"]
    lad["budget_used_pct"] = r2(TRANCHE * len(sim["fills"]) * 100)
    dca = book(sim["dca"], 1.0, c)
    dca["last_buy"] = sim["dca"][-1] if sim["dca"] else None
    fired = {x["name"] for x in sim["fills"]}
    pending = [{"name": n, "price_at_or_below": r2(ATH_CLOSE * (1 + lv)), "dd_pct": lv * 100} for n, lv in LADDER if n not in fired]
    if TREND[0] not in fired:
        pending.append({"name": TREND[0], "rule": "종가가 200일 평균을 위로 건너는 날(낙폭 ≤ −30%)",
                        "sma200": r2(sma) if sma else None})
    now = datetime.now(KST)
    out = {"date": now.strftime("%Y-%m-%d"), "generated_at_kst": now.strftime("%Y-%m-%d %H:%M"), "source": source,
           "rules": dict(rules_dict(), rule_fixed=RULE_FIXED),
           "bar_date": _day(last["t"]), "close": c, "dd_pct": r2((c / ATH_CLOSE - 1) * 100),
           "days_since_ath": days_since, "sma200": r2(sma) if sma else None,
           "above_sma200": (c > sma) if sma else None,
           "forward_days": sum(1 for b in bars if _day(b["t"]) >= FORWARD_FROM),
           "ladder": lad, "dca": dca, "pending": pending, "cycle_end": sim["cycle_end"],
           "analog": analog(days_since, past if past is not None else load_past())}
    for k in ("ladder", "dca"):
        out[k].pop("units", None)
    out["summary"] = summary_line(out)
    return out


def summary_line(out):
    lad, dca = out["ladder"], out["dca"]
    s = "%s 종가 %s, 고점 대비 %s%% (%d일째). 분할매수 %d/4 묶음" % (
        out["bar_date"], "{:,.0f}".format(out["close"]), out["dd_pct"], out["days_since_ath"], lad["n"])
    if lad["n"]:
        s += " 평균 %s 평가 %s%%" % ("{:,.0f}".format(lad["avg_cost"]), lad["mtm_pct"])
    if dca["n"]:
        s += " | DCA %d회 평균 %s 평가 %s%%" % (dca["n"], "{:,.0f}".format(dca["avg_cost"]), dca["mtm_pct"])
    return s


def history_row(out):
    return {"date": out["bar_date"], "close": out["close"], "dd_pct": out["dd_pct"], "days_since_ath": out["days_since_ath"],
            "sma200": out["sma200"], "ladder_n": out["ladder"]["n"], "ladder_avg": out["ladder"]["avg_cost"],
            "ladder_mtm_pct": out["ladder"]["mtm_pct"], "dca_n": out["dca"]["n"], "dca_avg": out["dca"]["avg_cost"],
            "dca_mtm_pct": out["dca"]["mtm_pct"], "cycle_end": bool(out["cycle_end"]), "source": out["source"]}


def write_history(row, path=HISTORY_PATH):
    """봉 날짜당 한 줄. 같은 날짜는 덮어쓴다."""
    rows = [r for r in load_history(path) if r.get("date") != row["date"]]
    rows.append(row)
    rows.sort(key=lambda r: r["date"])
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def print_summary(out):
    print("=== BTC 사이클 낙폭 분할매수 (P36, 규칙 고정 %s) ===" % RULE_FIXED)
    print(out["summary"])
    print("200일 평균 %s (%s)" % (out["sma200"], "위" if out["above_sma200"] else "아래"))
    for p in out["pending"]:
        print("  대기 %-5s %s" % (p["name"], p.get("price_at_or_below") or p.get("rule")))
    for x in out["ladder"]["fills"]:
        print("  체결 %-5s %s @ %s (dd %s%%)" % (x["name"], x["date"], x["price"], x["dd_pct"]))
    for name, a in (out.get("analog") or {}).items():
        print("  과거 %s 같은 날 %s%% (그날까지 최저 %s%% @ %d일, 사이클 최저 %s%% @ %s일)" % (
            name, a["dd_same_day_pct"], a["min_so_far_pct"], a["min_day"], a["cycle_min_pct"], a["cycle_min_day"]))


def print_report(path=HISTORY_PATH):
    rows = load_history(path)
    if not rows:
        print("기록 없음")
        return
    print("날짜        종가       낙폭%   묶음  평균      평가%   | DCA 회  평균      평가%")
    for r in rows[-30:]:
        print("%s %10s %7s %4s %9s %7s | %5s %9s %7s" % (r["date"], r["close"], r["dd_pct"], r["ladder_n"], r["ladder_avg"],
                                                          r["ladder_mtm_pct"], r["dca_n"], r["dca_avg"], r["dca_mtm_pct"]))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--bars", help="일봉 CSV (t,o,h,l,c,v)")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)
    if a.report:
        print_report()
        return 0
    if a.bars:
        source, bars, errs = "csv:" + os.path.basename(a.bars), load_csv(a.bars), []
    else:
        source, bars, errs = fetch()
    out = build(bars, source)
    if errs:
        out["fetch_errors"] = errs
    print_summary(out)
    if not a.dry_run:
        with open(OUT_PATH, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        write_history(history_row(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
