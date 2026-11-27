# -*- coding: utf-8 -*-
"""
CARRYGATE — 추세선 채널 하단 매수 순방향 종이 기록 (제안함 P42, 사용자 결정 2026-11-18 "A"). 판정 여덟 번째 트랙.

무엇을 하나
  매일 닫힌 일봉으로 trendline.py 의 고정 규칙(저점①-② 하단, ③고점 평행 상단, 하단 터치 매수 → 상단 익절/하단 이탈 손절/만료)을
  처음부터 다시 돌려, (1) 진입일이 FORWARD_FROM 이후인 닫힌 매매 = 순방향 성적, (2) 그 전 구간 = 사후 성적(참고),
  (3) 오늘 종가 기준 신호(내일 시가 매수 예정)와 보유 중인 종이 포지션, (4) 시장마다 오늘의 하단·중단·상단 가격을 기록한다.
  상태 파일이 없다 — 같은 봉이면 언제 다시 돌려도 같은 결과.

시장 (고정, 추가만)
  upbit  업비트 KRW 현물 6개 (scalp.COINS)            왕복 비용 0.20% (수수료 0.05%×2 + 미끄러짐 0.05%×2)
  okx    OKX USDT 무기한선물 6개, 롱만                 왕복 비용 0.20%
  kr     한국 대형주 39 + KODEX 200 (KR_CODES, 2026-11-18 고정)  왕복 비용 0.25% (거래세 포함)
판정    criteria.json trendline — 시장군별 닫힌 순방향 20건: 손익비(PF) ≥ 1.3 PASS, < 1.0 FAIL. 주 규칙 = 중(120봉)·base.
        나머지 창·변형(소·대, mid, midentry)은 나란히 기록만.

사용법
  python trendline_track.py                                  # 수집·계산·저장 (trendline.json, trendline_history.jsonl)
  python trendline_track.py --dry-run                        # 저장 안 함
  python trendline_track.py --fixture tests/fixtures/turtle --dry-run   # 저장된 일봉(업비트·OKX)으로, 인터넷 불필요
  python trendline_track.py --report                         # 누적 기록
주문 없음, 키 없음, AI 판단 없음.
"""
import os, sys, json, time, argparse
from datetime import datetime

import trendline as T
from scalp import COINS
from common import KST, r2, r4, load_history

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(HERE, "trendline.json")
HISTORY_PATH = os.path.join(HERE, "trendline_history.jsonl")

# ----------------------------------------------------------------- 고정 (사후 조정 금지 — proposals.md → RULE_FIXED → verdict.py --lock)
RULE_FIXED = "2026-11-18"           # 순방향 기록 설정(시장·비용·시작일)을 고정한 날. 매매 규칙 자체는 trendline.RULE_FIXED
FORWARD_FROM = "2026-11-19"         # 진입일이 이 날 이후인 매매만 순방향 (11/18 봉 종가 신호 → 11/19 시가가 첫 진입)
DAYS = 600                          # 받아올 일봉 수
MIN_BARS = T.WINDOWS["대"] + 10
VENUES = {
    "upbit": {"label": "업비트 현물", "cost_pct": 0.20},
    "okx": {"label": "OKX 무기한선물(롱만)", "cost_pct": 0.20},
    "kr": {"label": "한국 대형주", "cost_pct": 0.25},
}
KR_CODES = [  # 한국 대형주 39 + KODEX 200. 2026-11-18 고정, 추가만. 못 받는 코드는 errors 에 남기고 건너뛴다.
    "005930", "000660", "373220", "207940", "005380", "000270", "068270", "105560", "055550", "035420",
    "005490", "012330", "051910", "006400", "035720", "028260", "066570", "003550", "096770", "034730",
    "086790", "032830", "015760", "033780", "017670", "030200", "009150", "010130", "012450", "329180",
    "042660", "034020", "011200", "316140", "018260", "259960", "247540", "086520", "196170", "069500",
]
VARIANTS = [("소", "base"), ("대", "base"), ("중", "mid"), ("중", "midentry")]   # 주 규칙(중·base)과 나란히 기록만


def rules_dict():
    return {"forward_from": FORWARD_FROM, "primary": list(T.PRIMARY), "variants": [list(v) for v in VARIANTS],
            "venues": {k: v["cost_pct"] for k, v in VENUES.items()}, "markets": {"upbit": list(COINS), "okx": list(COINS), "kr": KR_CODES},
            "draw": {"rule_fixed": T.RULE_FIXED, "windows": T.WINDOWS, "pivot_k": T.PIVOT_K, "min_span_div": T.MIN_SPAN_DIV,
                     "valid_mult": T.VALID_MULT, "tol": T.TOL, "min_width_pct": T.MIN_WIDTH_PCT, "modes": list(T.MODES)}}


# ----------------------------------------------------------------- 수집

def fetch_market(venue, market, fixture=None):
    if fixture:
        import turtle as TU
        return TU.load_fixture(fixture, venue, market)
    if venue == "upbit":
        import turtle as TU
        return TU.upbit_daily(market, DAYS)
    if venue == "okx":
        import turtle as TU
        return TU.okx_daily(market, DAYS)
    import kr_ohlcv as KO
    bars, _ = KO.fetch_daily(market, DAYS)
    today = datetime.now(KST).strftime("%Y-%m-%d")
    return [b for b in bars if b["date"] < today]          # 09:18 KST 실행 — 오늘(장중, 미완성) 봉은 뺀다


# ----------------------------------------------------------------- 계산 (순수 함수)

def profit_factor(pnls):
    gp = sum(x for x in pnls if x > 0)
    gl = -sum(x for x in pnls if x < 0)
    return r2(gp / gl) if gl > 0 else None


def trade_row(market, t, mode):
    ch = t["ch"]
    ref = T.mid if mode == "midentry" else T.lower
    stop = ref(ch, t["entry_i"]) - T.TOL * ch["width"]
    risk = (t["entry"] - stop) / t["entry"] * 100.0
    return {"market": market, "entry_date": t["date_in"], "exit_date": t["date_out"], "entry": r4(t["entry"]), "exit": r4(t["exit"]),
            "why": t["why"], "days": t["days"], "pnl": r4(t["net_pct"]), "r_multiple": r2(t["net_pct"] / risk) if risk > 0 else 0.0}


def evaluate(bars_by, win, mode, cost_pct, forward_from=FORWARD_FROM):
    """시장별 일봉 → 터틀·추세전환과 같은 모양(portfolio.forward_trades / portfolio.stats) — verdict.judge_venue_track 가 그대로 읽는다."""
    back, fwd, opened = [], [], []
    for m in sorted(bars_by):
        for t in T.backtest(bars_by[m], win, mode, cost_pct):
            row = trade_row(m, t, mode)
            if t["why"] == "open":
                row["mark"] = row.pop("exit")
                row["pnl_now"] = row.pop("pnl")
                row["forward"] = row["entry_date"] >= forward_from
                ch = t["ch"]
                j = len(bars_by[m]) - 1
                row.update(lower=r4(T.lower(ch, j)), mid=r4(T.mid(ch, j)), upper=r4(T.upper(ch, j)), bars_left=ch["valid_to"] - j)
                opened.append(row)
            elif row["entry_date"] >= forward_from:
                fwd.append(row)
            else:
                back.append(row)
    fwd.sort(key=lambda r: (r["exit_date"], r["market"]))
    bp = [r["pnl"] for r in back]
    fp = [r["pnl"] for r in fwd]
    stats = {"trades": len(bp), "win_rate_pct": r2(100.0 * sum(1 for x in bp if x > 0) / len(bp)) if bp else None,
             "profit_factor": profit_factor(bp), "net_pct": r2(sum(bp)), "avg_pct": r2(sum(bp) / len(bp)) if bp else None,
             "forward": {"trades": len(fp), "wins": sum(1 for x in fp if x > 0), "net_pct": r2(sum(fp)),
                         "avg_pct": r2(sum(fp) / len(fp)) if fp else None, "profit_factor": profit_factor(fp)}}
    return {"portfolio": {"forward_trades": fwd, "stats": stats, "open": opened}}


def market_state(bars):
    s = T.today(bars, T.PRIMARY[0])
    return {"bar_date": s["date"], "close": s["close"], "signal": s["signal"], "up": s["up"], "down": s["down"]}


def build_venue(venue, bars_by):
    cost = VENUES[venue]["cost_pct"]
    vd = {"label": VENUES[venue]["label"], "cost_pct": cost}
    vd.update(evaluate(bars_by, T.PRIMARY[0], T.PRIMARY[1], cost))
    vd["variants"] = {"%s_%s" % (w, m): evaluate(bars_by, w, m, cost) for w, m in VARIANTS}
    vd["markets"] = {m: market_state(b) for m, b in sorted(bars_by.items())}
    vd["signals_today"] = [{"market": m, "bar_date": s["bar_date"], "close": s["close"], "lower": s["up"]["lower"], "mid": s["up"]["mid"],
                            "upper": s["up"]["upper"], "plan": "다음 봉 시가 매수 (시가가 손절선 아래거나 상단 이상이면 포기)"}
                           for m, s in vd["markets"].items() if s["signal"]]
    return vd


def build(fixture=None, venues=None):
    now = datetime.now(KST)
    out = {"schema": "carrygate-trendline/1", "date": now.strftime("%Y-%m-%d"), "generated_at_kst": now.strftime("%Y-%m-%d %H:%M"),
           "rules": dict(rules_dict(), rule_fixed=RULE_FIXED),
           "source": "fixture %s" % fixture if fixture else "upbit·okx 일봉(turtle.py 수집 함수) + 한국 일봉(kr_ohlcv: 야후→네이버)",
           "venues": {}, "errors": {}}
    for v in (venues or VENUES):
        if fixture and v == "kr":
            continue
        bars_by = {}
        for m in (KR_CODES if v == "kr" else COINS):
            try:
                bars = fetch_market(v, m, fixture)
                if len(bars) < MIN_BARS:
                    raise RuntimeError("일봉 부족 %d" % len(bars))
                bars_by[m] = bars
            except Exception as e:
                out["errors"]["%s/%s" % (v, m)] = str(e)[:200]
            if not fixture:
                time.sleep(0.3)
        if bars_by:
            out["venues"][v] = build_venue(v, bars_by)
    summ = {"ok": not out["errors"], "markets": sum(len(vd["markets"]) for vd in out["venues"].values())}
    for v, vd in out["venues"].items():
        st = vd["portfolio"]["stats"]
        summ[v] = {"markets": len(vd["markets"]), "forward_trades": st["forward"]["trades"], "forward_net_pct": st["forward"]["net_pct"],
                   "forward_profit_factor": st["forward"]["profit_factor"], "open": len(vd["portfolio"]["open"]),
                   "signals_today": [s["market"] for s in vd["signals_today"]],
                   "backtest_trades": st["trades"], "backtest_avg_pct": st["avg_pct"], "backtest_profit_factor": st["profit_factor"]}
    out["summary"] = summ
    return out


# ----------------------------------------------------------------- 기록·출력

def history_line(out):
    line = {"date": out["date"], "errors": len(out["errors"])}
    for v, vd in out["venues"].items():
        f = vd["portfolio"]["stats"]["forward"]
        line[v] = {"n": f["trades"], "wins": f["wins"], "net": f["net_pct"], "pf": f["profit_factor"],
                   "open": len(vd["portfolio"]["open"]), "signals": [s["market"] for s in vd["signals_today"]]}
    return line


def write_history(out, path=HISTORY_PATH):
    """하루 한 줄. 같은 날짜는 덮어쓴다."""
    rows = [r for r in load_history(path) if r.get("date") != out["date"]]
    rows.append(history_line(out))
    rows.sort(key=lambda r: r["date"])
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def print_summary(out):
    print("=== 추세선 채널 하단 매수 (P42, 작도 규칙 고정 %s, 순방향 진입 %s~) ===" % (T.RULE_FIXED, FORWARD_FROM))
    for v, vd in out["venues"].items():
        st = vd["portfolio"]["stats"]
        f = st["forward"]
        print("[%s] 시장 %d개 | 순방향 %d건 승 %d 순 %s%% PF %s | 보유 %d | 사후(참고) %d건 건당 %s%% PF %s" % (
            v, len(vd["markets"]), f["trades"], f["wins"], f["net_pct"], f["profit_factor"] if f["profit_factor"] is not None else "-",
            len(vd["portfolio"]["open"]), st["trades"], st["avg_pct"], st["profit_factor"]))
        for s in vd["signals_today"]:
            print("   신호 %-8s %s 종가 %s | 하단 %s 중단 %s 상단 %s → 다음 봉 시가 매수" % (s["market"], s["bar_date"], s["close"], s["lower"], s["mid"], s["upper"]))
        for o in vd["portfolio"]["open"]:
            print("   보유 %-8s %s 진입 %s 지금 %+.2f%% | 하단 %s 상단 %s 남은 유효 %d봉%s" % (
                o["market"], o["entry_date"], o["entry"], o["pnl_now"], o["lower"], o["upper"], o["bars_left"], "" if o["forward"] else " (순방향 전 진입)"))
        for k, e in vd["variants"].items():
            ef = e["portfolio"]["stats"]["forward"]
            print("   변형 %-10s 순방향 %d건 순 %s%% | 사후 %d건 PF %s" % (k, ef["trades"], ef["net_pct"], e["portfolio"]["stats"]["trades"], e["portfolio"]["stats"]["profit_factor"]))
    for k, e in out["errors"].items():
        print("[오류] %s: %s" % (k, e))


def print_report(path=HISTORY_PATH):
    rows = load_history(path)
    if not rows:
        print("기록 없음")
        return
    print("날짜        " + "  ".join("%-28s" % v for v in VENUES))
    for r in rows[-30:]:
        cells = []
        for v in VENUES:
            x = r.get(v)
            cells.append("%-28s" % ("-" if not x else "%d건 %s%% PF %s 보유 %d 신호 %d" % (x["n"], x["net"], x["pf"] if x["pf"] is not None else "-", x["open"], len(x["signals"]))))
        print("%s  %s" % (r["date"], "  ".join(cells)))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fixture", help="일봉 CSV 폴더 (tests/fixtures/turtle 형식, 업비트·OKX 만)")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)
    if a.report:
        print_report()
        return 0
    out = build(a.fixture)
    print_summary(out)
    if not out["venues"]:
        print("받은 시장이 없다 — 저장하지 않는다")
        return 1
    if not a.dry_run:
        with open(OUT_PATH, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        write_history(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
