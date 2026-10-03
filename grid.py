# -*- coding: utf-8 -*-
"""
CARRYGATE — 그리드(종사종팔 v4/v5) 백테스트 + 순방향 기록 (중기, 미국 레버리지 ETF)

"종가에 사서 종가에 판다"는 분할 그리드 규칙을 코드에 고정하고, 일봉 CSV(data/etf/<티커>.csv)로
(1) 과거 전 구간 성적(CAGR·최대낙폭·승률 등)을 다시 계산하고 (2) 규칙 고정일 이후의 가상 계좌를 순방향으로 기록한다.

공통 규칙 (v4·v5 같음)
  ① 매일 종가에 한 티어씩 산다. 그날 매도가 있었으면 사지 않는다.
  ② 티어별로 종가가 매수가 × (1 + 익절률) 이상이면 그 종가에 판다 (LOC 매도).
  ③ 산 뒤 HOLD_DAYS 거래일째 종가에는 못 팔았어도 무조건 판다 (MOC 매도).
  ④ 체결마다 FEE_PCT 를 뗀다 (편도). 주식 수는 소수점 허용(분할 조정 가격이라 정수 주 계산은 의미가 없다).
  ⑤ v4 는 보유 티어가 N_TIERS(10)개면 더 사지 않는다. v5 는 티어 수 제한 없이 현금 한도 안에서 산다.

버전별 하루 매수 금액
  v4: 총 투자금 ÷ N_TIERS 로 시작. HOLD_DAYS 거래일을 한 사이클로 보고, 2사이클 전에 산 티어들의 실현 손익이
      플러스면 그 REINVEST 비율만큼을 N_TIERS 로 나눠 하루 매수 금액에 더한다. 마이너스면 그대로 둔다(줄이지 않는다).
  v5: 어제 종가 기준 전략 자산(현금 + 보유 평가액) × DAILY_BUY_PCT. 자산이 줄면 금액도 준다.
  현금이 모자라면 남은 현금만큼만 산다 (v4 는 하락장에서 이 일이 실제로 생긴다 — 기록에 남긴다).

핵심 원칙
  - 규칙과 매개변수는 코드 맨 위 상수에 고정돼 있다. 결과를 보고 고치면 과최적화다.
  - 규칙은 공개된 원본 프로그램(jongsajongpal_kis_V4_V5_v3.py 의 run_backtest)과 같은 순서·같은 식으로 맞췄다.
    블로그(와치독, 2026-10) 가 공개한 숫자는 "외부 주장"으로 따로 적고, 여기서 실데이터로 다시 계산한 값과 나란히 둔다.
  - 이 전략은 CARRYGATE 의 다른 트랙(캐리·터틀 등)과 묶지 않는 독립 전략이다. 미국 레버리지 ETF 를 증권사 계좌로 산다.
  - 주문 기능 없음. 파일만 읽는다. 외부 접속 없음.

사용법
  python grid.py                     # 백테스트 + 순방향 기록, grid.json / grid_history.jsonl 저장
  python grid.py --dry-run           # 저장하지 않고 화면에만
  python grid.py --data DIR          # 다른 폴더의 CSV 사용 (기본 data/etf)
  python grid.py --report            # 쌓인 기록으로 순방향 계좌가 어떻게 움직였는지
"""
import json, sys, os, argparse
from datetime import datetime, timezone, timedelta

from momentum import load_ticker, max_drawdown, round_floats

KST = timezone(timedelta(hours=9))

DATA_DIR = "data/etf"
SYMBOLS = ["SOXL", "TQQQ"]          # SOXL 이 원문 기준. TQQQ 는 같은 규칙이 다른 종목에서도 서는지 보려고 나란히 둔다
CAPITAL = 10_000.0                  # 전략에 배정한 총 투자금 (USD)
HOLD_DAYS = 10                      # 만기 (거래일)
FEE_PCT = 0.09                      # 편도 수수료 % (한국투자증권 해외주식 이벤트 수수료. 블로그 기준과 같다)
FEE_SENSITIVITY_PCT = [0.03, 0.044, 0.09, 0.25]   # 소개글 가정 / 키움 이벤트 / 한투 이벤트 / 일반 수수료
START = "2010-04-05"                # 원문 프로그램의 백테스트 기본 시작일
WINDOWS = [("2010-04-05", "2024-11-30", "원문 소개글 구간"),
           ("2010-04-05", None,         "2026년 10월까지 (현재)")]
RULE_FIXED = "2026-10-03"           # 이 날부터 가상 계좌를 순방향으로 기록한다 (결과를 모르고 미리 정한 규칙)

VERSIONS = {
    "v4": {"label": "종사종팔 v4", "target_pct": 2.7,  "n_tiers": 10, "reinvest": 0.7},
    "v5": {"label": "종사종팔 v5", "target_pct": 2.75, "daily_buy_pct": 10.0},
}

# 외부 주장 (블로그 '와치독', 2026-10, SOXL·만기 10일·왕복 0.18%). 재계산 값과 비교용. 사실로 쓰지 않는다.
BLOG_CLAIMS = {
    "source": "https://m.blog.naver.com/ascbbs/224430179468",
    "note": "블로그 글이 밝힌 백테스트 수치. 이 저장소는 같은 규칙을 stooq 분할조정 일봉으로 다시 계산해 아래 results 와 비교한다.",
    "SOXL": {
        "v4": {"2010-04-05~2024-11-30": {"cagr_pct": 39.5, "mdd_pct": -36.4},
               "2010-04-05~2026-10": {"cagr_pct": 42.4, "mdd_pct": -51.8}},
        "v5": {"2010-04-05~2024-11-30": {"cagr_pct": 27.9, "mdd_pct": -31.3},
               "2010-04-05~2026-10": {"cagr_pct": 29.9, "mdd_pct": -40.2}},
    },
}

METHOD = ("일봉 종가만 사용. 매일 종가에 한 티어 매수(그날 매도가 있었으면 매수 없음). 티어별 종가 ≥ 매수가×(1+익절률)이면 그 종가에 매도, "
          "매수 후 HOLD_DAYS 거래일째 종가에는 무조건 매도. 체결마다 FEE_PCT(편도) 차감, 주식 수 소수점 허용. "
          "v4 하루 매수 금액 = 투자금/N_TIERS 에서 시작해 2사이클(=HOLD_DAYS 거래일) 전에 산 티어들의 실현 손익이 +면 그 REINVEST 만큼을 N_TIERS 로 나눠 더함(래칫, 줄이지 않음), 보유 티어 N_TIERS 개면 매수 없음. "
          "v5 하루 매수 금액 = 어제 종가 자산 × DAILY_BUY_PCT. 현금 부족 시 남은 현금만큼만 매수. "
          "자산 = 현금 + 보유주 × 종가(일별). CAGR 은 달력일 기준 연환산, MDD 는 일별 자산 고점 대비.")


# ----------------------------------------------------------------- 시뮬레이션
def simulate(bars, version, start=None, end=None, fee_pct=FEE_PCT, capital=CAPITAL, hold_days=HOLD_DAYS):
    """bars: [(date, close)] 오름차순. start~end(포함) 구간만 돈다. 결과: dict(equity 곡선, 매매 목록, 상태)."""
    cfg = VERSIONS[version]
    fee = fee_pct / 100.0
    target = cfg["target_pct"] / 100.0
    idx = [i for i, (d, _) in enumerate(bars) if (start is None or d >= start) and (end is None or d <= end)]
    if not idx:
        return None
    cash = capital
    tiers = []                 # {"buy_i", "buy_date", "px", "shares", "cost", "target", "cycle"}
    trades = []                # 닫힌 티어
    eq_dates, eq = [], []
    cycle_pnl = {}             # v4: 사이클번호 -> 그 사이클에 산 티어들의 실현 손익
    daily_amt = capital / cfg["n_tiers"] if version == "v4" else None
    last_cycle = -1
    prev_equity = capital
    cash_short_days = 0
    max_buy_ratio = 0.0
    max_tiers = 0
    for k, i in enumerate(idx):
        d, c = bars[i]
        # --- 매도 (종가)
        sold_today = False
        keep = []
        for t in tiers:
            days_held = k - t["k"]
            hit = c >= t["target"]
            expired = days_held >= hold_days
            if hit or expired:
                proceeds = t["shares"] * c * (1.0 - fee)
                cash += proceeds
                pnl = proceeds - t["cost"]
                trades.append({"buy_date": t["buy_date"], "sell_date": d, "buy_px": t["px"], "sell_px": c,
                               "cost": t["cost"], "hold_days": days_held, "pnl": pnl, "ret_pct": pnl / t["cost"] * 100.0,
                               "reason": "target" if hit else "expire"})
                if version == "v4":
                    cycle_pnl[t["cycle"]] = cycle_pnl.get(t["cycle"], 0.0) + pnl
                sold_today = True
            else:
                keep.append(t)
        tiers = keep
        # --- 매수 금액 결정
        if version == "v4":
            cycle = k // hold_days
            if cycle != last_cycle:
                # 새 사이클 시작: 2사이클 전 실현 손익이 +면 그 70% 를 N 으로 나눠 하루 금액에 더한다
                p = cycle_pnl.get(cycle - 2, 0.0)
                if cycle >= 2 and p > 0:
                    daily_amt += cfg["reinvest"] * p / cfg["n_tiers"]
                last_cycle = cycle
            amt = daily_amt
        else:
            amt = prev_equity * cfg["daily_buy_pct"] / 100.0
            cycle = None
        # --- 매수 (종가). 그날 매도가 있었으면 안 산다. v4 는 보유 티어가 N_TIERS 개면 안 산다(원문 코드와 같음)
        tier_full = version == "v4" and len(tiers) >= cfg["n_tiers"]
        if not sold_today and not tier_full and amt > 0 and c > 0:
            spend = min(amt, cash)
            if spend < amt - 1e-9:
                cash_short_days += 1
            if spend > 1e-9:
                shares = spend / (c * (1.0 + fee))      # 수량 × 체결가 × (1+수수료) = 매수 금액 (원문 코드와 같은 식)
                cash -= spend
                tiers.append({"k": k, "buy_date": d, "px": c, "shares": shares, "cost": spend,
                              "target": c * (1.0 + target), "cycle": cycle})
                if prev_equity > 0:
                    max_buy_ratio = max(max_buy_ratio, spend / prev_equity)
        max_tiers = max(max_tiers, len(tiers))
        equity = cash + sum(t["shares"] * c for t in tiers)
        eq_dates.append(d)
        eq.append(equity)
        prev_equity = equity
    return {"dates": eq_dates, "equity": eq, "trades": trades, "tiers": tiers, "cash": cash,
            "cash_short_days": cash_short_days, "max_buy_ratio": max_buy_ratio, "max_tiers": max_tiers,
            "daily_amt": daily_amt if version == "v4" else prev_equity * cfg["daily_buy_pct"] / 100.0,
            "last_close": bars[idx[-1]][1], "last_k": len(idx) - 1}


def _days_between(d0, d1):
    a = datetime.strptime(d0, "%Y-%m-%d")
    b = datetime.strptime(d1, "%Y-%m-%d")
    return (b - a).days


def metrics(sim, capital=CAPITAL):
    eq, dates, trades = sim["equity"], sim["dates"], sim["trades"]
    end_eq = eq[-1]
    days = max(1, _days_between(dates[0], dates[-1]))
    years = days / 365.25
    cagr = (end_eq / capital) ** (1.0 / years) - 1.0 if end_eq > 0 and years > 0 else -1.0
    wins = [t for t in trades if t["pnl"] > 0]
    exp = [t for t in trades if t["reason"] == "expire"]
    yearly = {}
    last_by_year = {}
    for d, v in zip(dates, eq):
        last_by_year[d[:4]] = v
    prev = capital
    for y in sorted(last_by_year):
        yearly[y] = (last_by_year[y] / prev - 1.0) * 100.0
        prev = last_by_year[y]
    worst_year = min(yearly.items(), key=lambda kv: kv[1]) if yearly else (None, None)
    return {
        "start": dates[0], "end": dates[-1], "years": years, "bars": len(eq),
        "end_equity": end_eq, "total_return_pct": (end_eq / capital - 1.0) * 100.0,
        "cagr_pct": cagr * 100.0, "max_drawdown_pct": max_drawdown(eq) * 100.0,
        "trades": len(trades), "win_rate_pct": (len(wins) / len(trades) * 100.0) if trades else None,
        "expired_sells": len(exp), "expired_share_pct": (len(exp) / len(trades) * 100.0) if trades else None,
        "avg_hold_days": (sum(t["hold_days"] for t in trades) / len(trades)) if trades else None,
        "avg_trade_ret_pct": (sum(t["ret_pct"] for t in trades) / len(trades)) if trades else None,
        "max_tiers_held": sim["max_tiers"],
        "max_daily_buy_pct_of_equity": sim["max_buy_ratio"] * 100.0,
        "cash_short_days": sim["cash_short_days"],
        "worst_year": {"year": worst_year[0], "ret_pct": worst_year[1]},
        "yearly_ret_pct": yearly,
    }


def forward_state(sim, version):
    """순방향 가상 계좌의 지금 상태와 내일 종가에 나갈 주문."""
    cfg = VERSIONS[version]
    c = sim["last_close"]
    tiers = []
    for t in sim["tiers"]:
        held = sim["last_k"] - t["k"]
        tiers.append({"buy_date": t["buy_date"], "buy_px": t["px"], "shares": t["shares"], "cost": t["cost"],
                      "target_px": t["target"], "held_days": held, "days_left": HOLD_DAYS - held,
                      "unrealized_pct": (c / t["px"] - 1.0) * 100.0})
    equity = sim["equity"][-1]
    return {
        "equity": equity, "cash": sim["cash"], "tiers_held": len(tiers), "tiers": tiers,
        "next_buy_usd": min(sim["daily_amt"], sim["cash"]),
        "next_buy_rule": ("고정 금액 %.2f (래칫)" % sim["daily_amt"]) if version == "v4"
                         else ("어제 자산 %.2f × %.0f%%" % (equity, cfg["daily_buy_pct"])),
        "sell_orders": [{"buy_date": t["buy_date"], "loc_px": t["target_px"],
                         "moc_if_days_left_le": 1 if t["days_left"] <= 1 else None} for t in tiers],
        "realized_pnl": sum(t["pnl"] for t in sim["trades"]), "closed_trades": len(sim["trades"]),
    }


def run_symbol(bars):
    first, last = bars[0][0], bars[-1][0]
    out = {"data_range": [first, last], "bars": len(bars), "last_close": bars[-1][1], "windows": {}, "fee_sensitivity": {},
           "forward": {}}
    for s, e, label in WINDOWS:
        key = "%s~%s" % (s, e or last)
        w = {"label": label, "start": s, "end": e or last}
        for v in VERSIONS:
            sim = simulate(bars, v, s, e)
            if sim is None:
                continue
            w[v] = metrics(sim)
            w[v]["recent_trades"] = sim["trades"][-5:]
        out["windows"][key] = w
    for v in VERSIONS:
        out["fee_sensitivity"][v] = {}
        for fp in FEE_SENSITIVITY_PCT:
            sim = simulate(bars, v, START, None, fee_pct=fp)
            if sim is None:
                continue
            m = metrics(sim)
            out["fee_sensitivity"][v]["%.3f" % fp] = {"fee_pct_one_way": fp, "cagr_pct": m["cagr_pct"],
                                                      "mdd_pct": m["max_drawdown_pct"], "end_equity": m["end_equity"]}
    for v in VERSIONS:
        sim = simulate(bars, v, RULE_FIXED, None)
        out["forward"][v] = forward_state(sim, v) if sim else {"note": "규칙 고정일 이후 자료 없음"}
    return out


def compare_to_blog(symbol, res):
    """재계산 값과 블로그 주장 차이."""
    claims = BLOG_CLAIMS.get(symbol)
    if not claims:
        return None
    rows = []
    for v, by_window in claims.items():
        for wk, c in by_window.items():
            s, e = wk.split("~")
            # 끝이 '2026-10' 처럼 달까지만 적힌 주장은 현재 구간과 짝짓는다
            match = None
            for key, w in res["windows"].items():
                if key.startswith(s) and (key.endswith(e) or (len(e) == 7 and w["end"].startswith(e))):
                    match = w
                    break
            if not match or v not in match:
                continue
            m = match[v]
            rows.append({"version": v, "window": wk, "blog_cagr_pct": c["cagr_pct"], "ours_cagr_pct": m["cagr_pct"],
                         "cagr_diff_pt": m["cagr_pct"] - c["cagr_pct"],
                         "blog_mdd_pct": c["mdd_pct"], "ours_mdd_pct": m["max_drawdown_pct"],
                         "mdd_diff_pt": m["max_drawdown_pct"] - c["mdd_pct"]})
    return rows


def run_all(data_dir, symbols=None, today=None):
    now = today or datetime.now(KST)
    out = {"date": now.strftime("%Y-%m-%d"), "generated_at": now.isoformat(timespec="seconds"),
           "rules": {"capital_usd": CAPITAL, "hold_days": HOLD_DAYS, "fee_pct_one_way": FEE_PCT, "start": START,
                     "rule_fixed": RULE_FIXED, "versions": VERSIONS},
           "method": METHOD, "blog_claims": BLOG_CLAIMS, "symbols": {}, "comparison": {}, "missing": []}
    for sym in symbols or SYMBOLS:
        bars = load_ticker(data_dir, sym)
        if not bars:
            out["missing"].append(sym)
            continue
        res = run_symbol(bars)
        out["symbols"][sym] = res
        cmp_rows = compare_to_blog(sym, res)
        if cmp_rows:
            out["comparison"][sym] = cmp_rows
    return out


# ----------------------------------------------------------------- 저장·출력
def write_history(out, path):
    rec = {"date": out["date"], "symbols": {}}
    for sym, res in out["symbols"].items():
        cur_key = [k for k in res["windows"] if res["windows"][k]["end"] == res["data_range"][1]]
        cur = res["windows"][cur_key[0]] if cur_key else {}
        rec["symbols"][sym] = {"last": res["data_range"][1], "close": res["last_close"]}
        for v in VERSIONS:
            m = cur.get(v) or {}
            fw = res["forward"].get(v) or {}
            rec["symbols"][sym][v] = {"cagr_pct": m.get("cagr_pct"), "mdd_pct": m.get("max_drawdown_pct"),
                                      "fw_equity": fw.get("equity"), "fw_tiers": fw.get("tiers_held"),
                                      "fw_next_buy": fw.get("next_buy_usd"), "fw_closed": fw.get("closed_trades")}
    line = json.dumps(round_floats(rec), ensure_ascii=False)
    try:
        with open(path, encoding="utf-8") as fp:
            rows = [l for l in fp.read().splitlines() if l.strip()]
    except IOError:
        rows = []
    keep = []
    for l in rows:
        try:
            if json.loads(l).get("date") == out["date"]:
                continue
        except ValueError:
            pass
        keep.append(l)
    keep.append(line)
    with open(path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(keep) + "\n")


def fmt(v, spec="%+.1f", none="  —  "):
    return none if v is None else spec % v


def print_summary(out):
    print("그리드(종사종팔) 백테스트  %s   투자금 $%.0f, 만기 %d거래일, 수수료 편도 %.2f%%" % (
        out["date"], CAPITAL, HOLD_DAYS, FEE_PCT))
    for sym, res in out["symbols"].items():
        print()
        print("[%s]  자료 %s ~ %s (%d일), 마지막 종가 %.2f" % (sym, res["data_range"][0], res["data_range"][1], res["bars"], res["last_close"]))
        print("  %-28s %-4s %8s %8s %6s %6s %6s %7s %8s %6s" % ("구간", "버전", "CAGR%", "MDD%", "매매", "승률%", "만기%", "최대티어", "최대매수%", "현금부족일"))
        for key, w in res["windows"].items():
            for v in VERSIONS:
                m = w.get(v)
                if not m:
                    continue
                print("  %-28s %-4s %8s %8s %6d %6s %6s %7d %8.1f %6d" % (
                    key, v, fmt(m["cagr_pct"]), fmt(m["max_drawdown_pct"]), m["trades"], fmt(m["win_rate_pct"], "%.1f"),
                    fmt(m["expired_share_pct"], "%.1f"), m["max_tiers_held"], m["max_daily_buy_pct_of_equity"], m["cash_short_days"]))
        print("  수수료 민감도 (%s~현재, 편도 %%): " % START, end="")
        for v in VERSIONS:
            parts = ["%s: CAGR %s / MDD %s" % (k, fmt(x["cagr_pct"]), fmt(x["mdd_pct"])) for k, x in res["fee_sensitivity"][v].items()]
            print("\n    %s  " % v + " | ".join(parts), end="")
        print()
        for v in VERSIONS:
            fw = res["forward"].get(v) or {}
            if "equity" in fw:
                print("  순방향 %s (%s~): 자산 $%.2f, 보유 티어 %d, 내일 매수 $%.2f, 닫힌 매매 %d" % (
                    v, RULE_FIXED, fw["equity"], fw["tiers_held"], fw["next_buy_usd"], fw["closed_trades"]))
        cmp_rows = out["comparison"].get(sym)
        if cmp_rows:
            print("  블로그 주장 대비:")
            for r in cmp_rows:
                print("    %s %-24s CAGR 블로그 %5.1f / 재계산 %5.1f (%+.1fp)   MDD 블로그 %5.1f / 재계산 %5.1f (%+.1fp)" % (
                    r["version"], r["window"], r["blog_cagr_pct"], r["ours_cagr_pct"], r["cagr_diff_pt"],
                    r["blog_mdd_pct"], r["ours_mdd_pct"], r["mdd_diff_pt"]))
    if out["missing"]:
        print()
        print("자료 없음: %s  (etf_update.py 가 stooq 에서 받아 data/etf/ 에 만든다)" % ", ".join(out["missing"]))


def report(path="grid_history.jsonl"):
    try:
        with open(path, encoding="utf-8") as fp:
            rows = [json.loads(l) for l in fp if l.strip()]
    except IOError:
        print("기록 없음: %s" % path)
        return 1
    print("그리드 순방향 기록 %d일 (%s ~ %s)" % (len(rows), rows[0]["date"], rows[-1]["date"]))
    for r in rows[-14:]:
        parts = []
        for sym, s in r["symbols"].items():
            for v in VERSIONS:
                x = s.get(v) or {}
                if x.get("fw_equity") is not None:
                    parts.append("%s %s $%.0f/%d티어" % (sym, v, x["fw_equity"], x.get("fw_tiers") or 0))
        print("  %s  %s" % (r["date"], "  ".join(parts)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="저장하지 않는다")
    ap.add_argument("--report", action="store_true", help="순방향 기록만 출력")
    ap.add_argument("--data", default=DATA_DIR, help="ETF 일봉 CSV 폴더")
    ap.add_argument("--symbols", default=",".join(SYMBOLS))
    ap.add_argument("--out", default="grid.json")
    ap.add_argument("--history", default="grid_history.jsonl")
    a = ap.parse_args(argv)
    if a.report:
        return report(a.history)
    out = run_all(a.data, [s for s in a.symbols.split(",") if s])
    print_summary(out)
    if not out["symbols"]:
        return 1
    if not a.dry_run:
        with open(a.out, "w", encoding="utf-8") as fp:
            json.dump(round_floats(out), fp, ensure_ascii=False, indent=1)
        write_history(out, a.history)
        print()
        print("저장: %s, %s" % (a.out, a.history))
    return 0


if __name__ == "__main__":
    sys.exit(main())
