# -*- coding: utf-8 -*-
"""
CARRYGATE — 한국 주식 "업종 동조 + 패턴 돌파" 신호·종이매매·백테스트 (설계서 docs/KR_SECTOR_BREAKOUT_PLAN.md 5단계)

하나의 엔진(simulate)에 두 가지 입력을 넣는다.
  순방향(기본)  kr_sector_history.jsonl 에 매일 **실제로 기록된** 유니버스·동조 업종을 그대로 쓴다. RULE_FIXED 이후만 센다.
               결과를 모르고 미리 정한 규칙으로 낸 성적이므로 이것만이 진짜 시험이다. 매일 처음부터 다시 계산하므로 상태 파일이 없다.
  백테스트      --backtest. 지금 거래대금 상위 BT_UNIVERSE 종목의 3년 일봉을 받아, 매일 종가×거래량 상위 100 을 다시 만들고
               업종은 지금 분류를 그대로 쓴다. **생존 편향**(그 사이 상장폐지·거래정지 종목 누락)이 있어 실제보다 좋게 나올 수 있다.

매매 규칙(고정, 사후 조정 금지)
  신호   동조 업종 안의 종목이 patterns.scan 으로 돌파 확인(종가>저항, 거래량 1.5배, 20일선 위, 거래대금 100억+, 상한가 제외)
  진입   다음날 시가. 시가가 신호일 종가의 +GAP_SKIP_PCT 이상이면 포기. 슬리피지 SLIP_PCT
  손절   max(패턴 하단, 진입가×(1−STOP_PCT)). 당일 저가가 손절가 이하이면 손절가(갭이면 시가)에 체결
  청산   직전 EXIT_LOW_N 일 최저가 아래 종가 → 다음날 시가 / 보유 HOLD_MAX 봉 → 다음날 시가
  수량   계좌×RISK_PCT ÷ (진입가−손절가), 명목가 ≤ 계좌×MAX_POS_PCT. 동시 MAX_POS 종목, 같은 업종 MAX_PER_GROUP
  비용   매수 수수료 FEE_PCT, 매도 수수료 FEE_PCT + 거래세 TAX_PCT, 양쪽 슬리피지 SLIP_PCT
  변형   base(동조 필요, 20일) / no_sector(동조 없이 패턴만 — 대조군) / hold_10(동조 필요, 10일). 나란히 기록만 한다.

  python kr_breakout.py                 # 순방향 계산·저장 (kr_breakout.json, kr_breakout_history.jsonl)
  python kr_breakout.py --dry-run
  python kr_breakout.py --backtest      # 3년 백테스트 → kr_backtest.json (느리다: 종목 수백 개 일봉 수집)
  python kr_breakout.py --backtest --fixture tests/fixtures/kr/ohlcv --dry-run   # 저장된 일봉만으로 (인터넷 불필요, 시험용)
  python kr_breakout.py --report
"""
import os, sys, json, math, time, argparse, statistics
from datetime import datetime, timezone, timedelta

import kr_universe as KU
import kr_ohlcv as KO
import patterns as P

KST = timezone(timedelta(hours=9))

# ----------------------------------------------------------------- 고정 규칙 (사후 조정 금지)
RULE_FIXED = "2026-10-06"       # 이 날 이후 기록된 유니버스로 낸 신호만 순방향으로 센다 (첫 평일 실행일)
EQUITY0 = 10_000_000.0          # 시작 계좌 (원)
RISK_PCT = 1.0                  # 한 번에 계좌의 1%
MAX_POS = 5
MAX_PER_GROUP = 2
MAX_POS_PCT = 20.0              # 종목당 명목가 상한 (계좌 %)
STOP_PCT = 7.0
GAP_SKIP_PCT = 8.0
EXIT_LOW_N = 10
HOLD_MAX = 20
COOLDOWN = 10                   # 청산 뒤 같은 종목 재진입 금지 봉 수
FEE_PCT = 0.015                 # 증권사 수수료 (비대면 기준)
TAX_PCT = 0.15                  # 매도 거래세(농특세 포함)
SLIP_PCT = 0.30
VARIANTS = {
    "base":      {"need_sync": True,  "hold_max": HOLD_MAX, "label": "동조 업종 + 패턴 돌파 (기본)"},
    "no_sector": {"need_sync": False, "hold_max": HOLD_MAX, "label": "패턴 돌파만 (업종 동조 없음, 대조군)"},
    "hold_10":   {"need_sync": True,  "hold_max": 10,       "label": "동조 + 패턴, 보유 10일"},
}
BT_UNIVERSE = 300               # 백테스트 유니버스: 지금 거래대금 상위 N
BT_DAYS = 760                   # 약 3년 일봉
FWD_BARS = 200                  # 순방향: 종목당 받아오는 일봉 수
PASS = {"min_trades": 100, "max_dd_pct": 25.0}   # 백테스트 합격선 (설계서 4절)

OUT_PATH = "kr_breakout.json"
HISTORY_PATH = "kr_breakout_history.jsonl"
BT_PATH = "kr_backtest.json"


def r2(x):
    return None if x is None else round(x, 2)


# ----------------------------------------------------------------- 엔진
class Sim:
    """변형 하나의 종이매매. 날짜 순서대로 step(day) 를 부른다."""

    def __init__(self, name, cfg, equity0=EQUITY0):
        self.name, self.cfg = name, cfg
        self.cash = equity0
        self.positions = {}        # code -> dict
        self.pending_entries = []  # 다음날 시가 매수 후보 (신호일 순)
        self.pending_exits = set()
        self.cooldown = {}         # code -> 재진입 가능해지는 봉 번호(날짜 순번)
        self.trades = []
        self.curve = []            # (date, equity)
        self.peak = equity0
        self.max_dd = 0.0
        self.signals = 0
        self.skipped = {"gap": 0, "limit": 0, "cooldown": 0, "size": 0, "no_bar": 0}
        self.day_no = 0

    def equity(self, bars_today):
        v = self.cash
        for code, p in self.positions.items():
            b = bars_today.get(code)
            v += p["shares"] * (b["c"] if b else p["last_close"])
        return v

    def _group_count(self, group):
        return sum(1 for p in self.positions.values() if p["group"] == group)

    def _sell(self, code, price, date, why, p):
        gross = price * (1 - SLIP_PCT / 100) * p["shares"]
        cost = gross * (FEE_PCT + TAX_PCT) / 100
        self.cash += gross - cost
        pnl = gross - cost - p["cost_in"]
        self.trades.append({"code": code, "name": p.get("name"), "group": p["group"], "pattern": p["pattern"], "patterns": p["patterns"],
                            "sync_by": p["sync_by"], "signal_date": p["signal_date"], "entry_date": p["entry_date"], "entry": r2(p["entry"]),
                            "stop": r2(p["stop"]), "exit_date": date, "exit": r2(price * (1 - SLIP_PCT / 100)), "why": why,
                            "shares": p["shares"], "pnl_krw": round(pnl), "pnl_pct": r2(pnl / p["cost_in"] * 100),
                            "hold_days": p["bars_held"]})
        del self.positions[code]
        self.cooldown[code] = self.day_no + COOLDOWN

    def step(self, date, bars_today, idx_today, all_bars, universe, sync_groups, meta):
        """bars_today: code->오늘 봉, idx_today: code->오늘 봉 인덱스(all_bars[code] 안), universe: [(code, group)], sync_groups: set."""
        self.day_no += 1
        eq_open = self.equity({c: {"c": b["o"]} for c, b in bars_today.items()})
        # 1) 예약된 청산 → 오늘 시가
        for code in list(self.pending_exits):
            p = self.positions.get(code); b = bars_today.get(code)
            if p and b:
                self._sell(code, b["o"], date, p.pop("exit_why", "exit"), p)
            self.pending_exits.discard(code)
        # 2) 예약된 진입 → 오늘 시가
        for sig in self.pending_entries:
            code = sig["code"]; b = bars_today.get(code)
            if not b:
                self.skipped["no_bar"] += 1; continue
            if code in self.positions or len(self.positions) >= MAX_POS or self._group_count(sig["group"]) >= MAX_PER_GROUP:
                self.skipped["limit"] += 1; continue
            if b["o"] > sig["signal_close"] * (1 + GAP_SKIP_PCT / 100):
                self.skipped["gap"] += 1; continue
            entry = b["o"] * (1 + SLIP_PCT / 100)
            stop = max(sig["pattern_low"], entry * (1 - STOP_PCT / 100))
            if stop >= entry:
                self.skipped["size"] += 1; continue
            risk_amt = eq_open * RISK_PCT / 100
            shares = int(min(risk_amt / (entry - stop), eq_open * MAX_POS_PCT / 100 / entry))
            if shares < 1 or shares * entry > self.cash:
                self.skipped["size"] += 1; continue
            cost_in = shares * entry * (1 + FEE_PCT / 100)
            self.cash -= cost_in
            self.positions[code] = {"group": sig["group"], "pattern": sig["pattern"], "patterns": sig["patterns"], "sync_by": sig["sync_by"],
                                    "signal_date": sig["date"], "entry_date": date, "entry": entry, "stop": stop, "shares": shares,
                                    "cost_in": cost_in, "bars_held": 0, "last_close": b["c"], "name": sig.get("name")}
        self.pending_entries = []
        # 3) 보유 종목: 손절 → 10일 최저가 → 보유일
        for code in list(self.positions):
            p = self.positions[code]; b = bars_today.get(code)
            if not b:
                continue
            p["bars_held"] += 1; p["last_close"] = b["c"]
            if b["l"] <= p["stop"]:
                self._sell(code, min(b["o"], p["stop"]), date, "stop", p); continue
            i = idx_today[code]; hist = all_bars[code]
            low_n = min(x["l"] for x in hist[max(0, i - EXIT_LOW_N):i]) if i > 0 else None
            if low_n is not None and b["c"] < low_n:
                p["exit_why"] = "low%d" % EXIT_LOW_N; self.pending_exits.add(code)
            elif p["bars_held"] >= self.cfg["hold_max"]:
                p["exit_why"] = "hold%d" % self.cfg["hold_max"]; self.pending_exits.add(code)
        # 4) 평가·낙폭
        eq = self.equity(bars_today)
        self.curve.append((date, round(eq)))
        self.peak = max(self.peak, eq)
        self.max_dd = max(self.max_dd, (self.peak - eq) / self.peak * 100)
        # 5) 오늘 종가 신호 → 내일 시가 후보
        for code, group in universe:
            if self.cfg["need_sync"] and group not in sync_groups:
                continue
            if code in self.positions or code in self.pending_exits:
                continue
            if self.cooldown.get(code, -1) > self.day_no:
                self.skipped["cooldown"] += 1; continue
            i = idx_today.get(code)
            if i is None:
                continue
            r = P.scan(all_bars[code], i)
            if not r["ok"]:
                continue
            self.signals += 1
            best = max(r["patterns"], key=lambda q: q["pattern_low"])   # 가장 가까운 하단 = 가장 타이트한 손절
            self.pending_entries.append({"code": code, "group": group, "date": date, "signal_close": bars_today[code]["c"],
                                         "pattern": best["pattern"], "patterns": [q["pattern"] for q in r["patterns"]],
                                         "pattern_low": best["pattern_low"], "sync_by": (meta.get("sync_by") or {}).get(group),
                                         "name": (meta.get("names") or {}).get(code)})

    def stats(self):
        t = self.trades
        wins = [x for x in t if x["pnl_krw"] > 0]; losses = [x for x in t if x["pnl_krw"] <= 0]
        gp = sum(x["pnl_krw"] for x in wins); gl = -sum(x["pnl_krw"] for x in losses)
        final = self.curve[-1][1] if self.curve else EQUITY0
        by_pat, by_sync, by_why, by_year = {}, {}, {}, {}
        for x in t:
            for k, d in ((x["pattern"], by_pat), (x["sync_by"] or "none", by_sync), (x["why"], by_why), (x["exit_date"][:4], by_year)):
                e = d.setdefault(k, {"n": 0, "wins": 0, "pnl_krw": 0})
                e["n"] += 1; e["wins"] += 1 if x["pnl_krw"] > 0 else 0; e["pnl_krw"] += x["pnl_krw"]
        return {"label": self.cfg["label"], "signals": self.signals, "trades": len(t), "wins": len(wins), "win_rate": r2(len(wins) / len(t) * 100) if t else None,
                "avg_win_pct": r2(statistics.mean(x["pnl_pct"] for x in wins)) if wins else None,
                "avg_loss_pct": r2(statistics.mean(x["pnl_pct"] for x in losses)) if losses else None,
                "payoff": r2((gp / len(wins)) / (gl / len(losses))) if wins and losses and gl > 0 else None,
                "profit_factor": r2(gp / gl) if gl > 0 else None,
                "total_return_pct": r2((final / EQUITY0 - 1) * 100), "equity_final": round(final), "max_drawdown_pct": r2(self.max_dd),
                "avg_hold_days": r2(statistics.mean(x["hold_days"] for x in t)) if t else None,
                "skipped": self.skipped, "by_pattern": by_pat, "by_sync": by_sync, "by_exit": by_why, "by_year": by_year,
                "yearly_return_pct": yearly(self.curve),
                "open_positions": [{"code": c, "name": p.get("name"), "group": p["group"], "pattern": p["pattern"], "entry_date": p["entry_date"],
                                    "entry": r2(p["entry"]), "stop": r2(p["stop"]), "shares": p["shares"], "bars_held": p["bars_held"],
                                    "last_close": p["last_close"], "unrealized_pct": r2((p["last_close"] / p["entry"] - 1) * 100)}
                                   for c, p in self.positions.items()]}


def yearly(curve):
    """평가액 곡선 → 연도별 수익률(%). 첫 해는 시작 계좌 기준."""
    out, last = {}, EQUITY0
    for date, eq in curve:
        out.setdefault(date[:4], {"start": last, "end": eq})["end"] = eq
        last = eq
    ys = sorted(out)
    res = {}
    prev_end = EQUITY0
    for y in ys:
        res[y] = r2((out[y]["end"] / prev_end - 1) * 100)
        prev_end = out[y]["end"]
    return res


def benchmark(first, last, fixture=None):
    """같은 기간 코스피·코스닥 단순 보유 수익률 (야후 ^KS11, ^KQ11). 못 받으면 None."""
    out = {}
    if fixture:
        return out
    for name, sym in (("KOSPI", "^KS11"), ("KOSDAQ", "^KQ11")):
        try:
            bars = KO.yahoo_symbol(sym, BT_DAYS)
            bars = [b for b in bars if first <= b["date"] <= last]
            if len(bars) > 2:
                closes = [b["c"] for b in bars]
                peak, mdd = closes[0], 0.0
                for c in closes:
                    peak = max(peak, c); mdd = max(mdd, (peak - c) / peak * 100)
                out[name] = {"from": bars[0]["date"], "to": bars[-1]["date"], "return_pct": r2((closes[-1] / closes[0] - 1) * 100),
                             "max_drawdown_pct": r2(mdd), "by_year": yearly_from_closes(bars)}
        except Exception as e:
            out[name] = {"error": str(e)[:160]}
        time.sleep(KO.PAUSE)
    return out


def yearly_from_closes(bars):
    res, prev = {}, bars[0]["c"]
    for y in sorted({b["date"][:4] for b in bars}):
        end = [b["c"] for b in bars if b["date"][:4] == y][-1]
        res[y] = r2((end / prev - 1) * 100); prev = end
    return res


def simulate(days, all_bars, names=None):
    """days: [{date, universe: [(code, group)], sync_groups: set, sync_by: {group: by}}] 날짜 오름차순. all_bars: code -> 봉 목록."""
    index = {code: {b["date"]: i for i, b in enumerate(bars)} for code, bars in all_bars.items()}
    sims = {k: Sim(k, v) for k, v in VARIANTS.items()}
    for d in days:
        date = d["date"]
        bars_today, idx_today = {}, {}
        for code in index:
            i = index[code].get(date)
            if i is not None:
                bars_today[code] = all_bars[code][i]; idx_today[code] = i
        meta = {"sync_by": d.get("sync_by") or {}, "names": names or {}}
        for s in sims.values():
            s.step(date, bars_today, idx_today, all_bars, d["universe"], d["sync_groups"], meta)
    return sims


# ----------------------------------------------------------------- 입력 만들기
def days_from_bars(all_bars, groups_by_code, top_n=KU.TOP_N):
    """백테스트: 매일 종가×거래량 상위 top_n 을 유니버스로, kr_universe 규칙으로 동조 업종을 다시 계산한다."""
    dates = sorted({b["date"] for bars in all_bars.values() for b in bars})
    index = {code: {b["date"]: i for i, b in enumerate(bars)} for code, bars in all_bars.items()}
    days = []
    for date in dates:
        rows = []
        for code, bars in all_bars.items():
            i = index[code].get(date)
            if i is None or i < 5:
                continue
            b = bars[i]
            val = b["value"] or b["c"] * b["v"]
            rows.append({"code": code, "industry": groups_by_code.get(code, "(미분류)"), "value_traded_krw": val,
                         "change_pct": (b["c"] / bars[i - 1]["c"] - 1) * 100 if bars[i - 1]["c"] else None,
                         "perf_week_pct": (b["c"] / bars[i - 5]["c"] - 1) * 100 if bars[i - 5]["c"] else None,
                         "perf_month_pct": (b["c"] / bars[i - 21]["c"] - 1) * 100 if i >= 21 and bars[i - 21]["c"] else None, "rank": 0})
        rows.sort(key=lambda r: -r["value_traded_krw"])
        rows = rows[:top_n]
        for k, r in enumerate(rows, 1):
            r["rank"] = k
        if len(rows) < min(10, len(all_bars)):      # 시험용 fixture(종목 몇 개)도 돌게
            continue
        g = KU.group_rows(rows)
        days.append({"date": date, "universe": [(r["code"], r["industry"]) for r in rows],
                     "sync_groups": {k for k, v in g.items() if v["sync"]}, "sync_by": {k: v["sync_by"] for k, v in g.items() if v["sync"]}})
    return days


def days_from_history(path=KU.HISTORY_PATH, since=RULE_FIXED):
    """순방향: kr_sector_history.jsonl 에 기록된 그날의 유니버스·동조 업종. 중복(휴장일) 기록은 뺀다."""
    if not os.path.exists(path):
        return []
    days, seen = [], set()
    for line in open(path, encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("date", "") < since or r.get("duplicate_of_previous") or not r.get("universe") or r["date"] in seen:
            continue
        seen.add(r["date"])
        sg = r.get("sync_groups") or {}
        days.append({"date": r["date"], "universe": [(c, g) for c, g in r["universe"]],
                     "sync_groups": set(sg), "sync_by": {k: v.get("by") for k, v in sg.items()}})
    days.sort(key=lambda d: d["date"])
    return days


def load_bars(codes, days, fixture=None, log=print):
    all_bars, sources, errors = {}, {}, {}
    for n, code in enumerate(sorted(codes), 1):
        try:
            if fixture:
                path = os.path.join(fixture, code + ".csv")
                if not os.path.exists(path):
                    continue
                all_bars[code], sources[code] = KO.load_csv(path), "fixture"
            else:
                all_bars[code], sources[code] = KO.fetch_daily(code, days)
                time.sleep(KO.PAUSE)
        except Exception as e:
            errors[code] = str(e)[:200]
        if n % 50 == 0:
            log("  일봉 %d/%d" % (n, len(codes)))
    return all_bars, sources, errors


# ----------------------------------------------------------------- 실행 모드
def run_backtest(fixture=None, universe_n=BT_UNIVERSE):
    t0 = time.time()
    if fixture:
        codes = [f[:-4] for f in os.listdir(fixture) if f.endswith(".csv")]
        groups = {}
        try:
            fx = KU.read_saved(os.path.join("tests", "fixtures", "kr", "top100_2026-10-03.json"))
            groups = {r["code"]: r.get("industry") or "(미분류)" for r in KU.parse_rows(fx)} if fx else {}
        except Exception:
            pass
        names = {}
        src = {"kind": "fixture", "path": fixture}
    else:
        resp = KU.fetch_scanner(top=universe_n)
        rows = KU.parse_rows(resp, top=universe_n)
        codes = [r["code"] for r in rows]
        groups = {r["code"]: r.get("industry") or "(미분류)" for r in rows}
        names = {r["code"]: r["name"] for r in rows}
        src = {"kind": "scanner", "universe_n": len(codes)}
    print("백테스트 유니버스 %d종목, 일봉 수집 시작" % len(codes))
    all_bars, sources, errors = load_bars(codes, BT_DAYS, fixture)
    print("일봉 %d종목 수집, 실패 %d, %.0f초" % (len(all_bars), len(errors), time.time() - t0))
    days = days_from_bars(all_bars, groups)
    sims = simulate(days, all_bars, names)
    first, last = (days[0]["date"], days[-1]["date"]) if days else (None, None)
    out = {"date": datetime.now(KST).strftime("%Y-%m-%d"), "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "mode": "backtest", "rule_fixed": RULE_FIXED, "rules": rules_dict(), "source": src,
           "period": {"from": first, "to": last, "days": len(days)}, "bars": {"codes": len(all_bars), "errors": len(errors),
           "by_source": {s: sum(1 for v in sources.values() if v == s) for s in set(sources.values())}},
           "caveat": "생존 편향: 지금 거래대금 상위 종목의 과거만 본다. 그 사이 상장폐지·거래정지 종목이 빠져 실제보다 좋게 나올 수 있다. 업종은 지금 분류.",
           "sync_days": sum(1 for d in days if d["sync_groups"]),
           "benchmark": benchmark(first, last, fixture) if first else {},
           "variants": {k: s.stats() for k, s in sims.items()},
           "trades": {k: s.trades for k, s in sims.items()},
           "curve": {k: s.curve[::5] for k, s in sims.items()},
           "errors": dict(list(errors.items())[:30]), "elapsed_sec": round(time.time() - t0)}
    b = out["variants"]["base"]
    out["pass"] = {"criteria": PASS, "trades_ok": b["trades"] >= PASS["min_trades"], "return_ok": (b["total_return_pct"] or 0) > 0,
                   "dd_ok": (b["max_drawdown_pct"] or 0) < PASS["max_dd_pct"]}
    out["pass"]["all"] = all(out["pass"][k] for k in ("trades_ok", "return_ok", "dd_ok"))
    return out


def run_forward(fixture=None, dry_run=False):
    days = days_from_history()
    codes = {c for d in days for c, _ in d["universe"]}
    names = {}
    if os.path.exists(KU.OUT_PATH):
        try:
            ks = json.load(open(KU.OUT_PATH, encoding="utf-8"))
            names = {r["code"]: r["name"] for r in ks.get("universe") or []}
        except Exception:
            pass
    all_bars, sources, errors = load_bars(codes, FWD_BARS, fixture) if codes else ({}, {}, {})
    # 오늘 봉이 빠진 종목(자료 지연)은 표시한다 — 그날 신호가 하루 늦게 잡힌다
    last_date = days[-1]["date"] if days else None
    stale = [c for c, bars in all_bars.items() if last_date and (not bars or bars[-1]["date"] < last_date)]
    sims = simulate(days, all_bars, names)
    today_sig = {k: [e for e in s.pending_entries] for k, s in sims.items()}
    out = {"date": datetime.now(KST).strftime("%Y-%m-%d"), "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "mode": "forward", "rule_fixed": RULE_FIXED, "rules": rules_dict(),
           "forward": {"days": len(days), "from": days[0]["date"] if days else None, "to": last_date,
                       "codes": len(all_bars), "bar_errors": len(errors), "stale_codes": len(stale)},
           "signals_today": {k: [{"code": e["code"], "name": e.get("name"), "group": e["group"], "pattern": e["pattern"], "patterns": e["patterns"],
                                   "signal_close": e["signal_close"], "pattern_low": e["pattern_low"], "sync_by": e["sync_by"],
                                   "entry_plan": "내일 시가 (시가가 %.0f 이상이면 포기)" % (e["signal_close"] * (1 + GAP_SKIP_PCT / 100)),
                                   "stop_plan": "max(%.0f, 진입가×%.2f)" % (e["pattern_low"], 1 - STOP_PCT / 100)} for e in v]
                             for k, v in today_sig.items()},
           "variants": {k: s.stats() for k, s in sims.items()},
           "trades": {k: s.trades[-100:] for k, s in sims.items()},
           "errors": dict(list(errors.items())[:20]), "stale": stale[:20]}
    return out


def rules_dict():
    return {"equity0": EQUITY0, "risk_pct": RISK_PCT, "max_pos": MAX_POS, "max_per_group": MAX_PER_GROUP, "max_pos_pct": MAX_POS_PCT,
            "stop_pct": STOP_PCT, "gap_skip_pct": GAP_SKIP_PCT, "exit_low_n": EXIT_LOW_N, "hold_max": HOLD_MAX, "cooldown": COOLDOWN,
            "fee_pct": FEE_PCT, "tax_pct": TAX_PCT, "slip_pct": SLIP_PCT, "variants": {k: v["label"] for k, v in VARIANTS.items()},
            "pattern_rules": {"vol_mult": P.VOL_MULT, "min_value_krw": P.MIN_VALUE_KRW, "limit_up_pct": P.LIMIT_UP_PCT},
            "sync_rules": {"min_group": KU.MIN_GROUP, "up_ratio": KU.UP_RATIO, "median_change_pct": KU.MED_CHANGE_PCT, "median_week_pct": KU.MED_WEEK_PCT}}


def print_stats(out):
    print("[%s] %s  %s" % (out["date"], out["mode"], out.get("period") or out.get("forward")))
    print("%-10s %5s %5s %6s %7s %7s %6s %7s %7s %6s" % ("변형", "신호", "매매", "승률%", "평균익%", "평균손%", "PF", "수익%", "낙폭%", "보유일"))
    for k, v in out["variants"].items():
        print("%-10s %5d %5d %6s %7s %7s %6s %7s %7s %6s" % (k, v["signals"], v["trades"], v["win_rate"] or "-", v["avg_win_pct"] or "-",
              v["avg_loss_pct"] or "-", v["profit_factor"] or "-", v["total_return_pct"], v["max_drawdown_pct"], v["avg_hold_days"] or "-"))
    b = out["variants"]["base"]
    if b["by_pattern"]:
        print("패턴별(base):", ", ".join("%s n=%d 승%d %+d원" % (k, v["n"], v["wins"], v["pnl_krw"]) for k, v in b["by_pattern"].items()))
    if b["by_exit"]:
        print("청산별(base):", ", ".join("%s n=%d %+d원" % (k, v["n"], v["pnl_krw"]) for k, v in b["by_exit"].items()))
    if b.get("yearly_return_pct"):
        print("연도별(base):", ", ".join("%s %+.1f%%" % (y, v) for y, v in b["yearly_return_pct"].items()),
              "| 벤치마크:", ", ".join("%s %s%% (낙폭 %s%%)" % (k, v.get("return_pct"), v.get("max_drawdown_pct")) for k, v in (out.get("benchmark") or {}).items()))
    if out.get("pass"):
        print("합격선:", json.dumps(out["pass"], ensure_ascii=False))
    for k, v in (out.get("signals_today") or {}).items():
        if v:
            print("오늘 신호(%s): %s" % (k, ", ".join("%s %s %s" % (e["code"], e.get("name") or "", e["pattern"]) for e in v)))
    if b["open_positions"]:
        print("보유(base): %s" % ", ".join("%s %s %+.1f%%" % (p["code"], p["pattern"], p["unrealized_pct"]) for p in b["open_positions"]))


def report(path=HISTORY_PATH, days=15):
    if not os.path.exists(path):
        print("기록 없음"); return
    recs = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    print("순방향 추이 (%d일 기록)" % len(recs))
    for r in recs[-days:]:
        b = r["base"]
        print("%s 신호%2d 매매%3d 승률%s 수익%s%% 낙폭%s%% 보유%d  오늘신호 %s" % (r["date"], b["signals"], b["trades"], b["win_rate"], b["total_return_pct"],
              b["max_drawdown_pct"], b["open"], ",".join(r.get("signals_today") or []) or "-"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--fixture", help="저장된 일봉 CSV 폴더 (인터넷 불필요)")
    ap.add_argument("--universe", type=int, default=BT_UNIVERSE)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        report(); return 0
    if a.backtest:
        out = run_backtest(a.fixture, a.universe)
        print_stats(out)
        if not a.dry_run:
            json.dump(out, open(BT_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print("저장:", BT_PATH)
        return 0
    out = run_forward(a.fixture, a.dry_run)
    print_stats(out)
    if a.dry_run:
        print("(dry-run: 저장 안 함)")
    else:
        json.dump(out, open(OUT_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        rec = {"date": out["date"], "forward_days": out["forward"]["days"],
               "signals_today": [e["code"] for e in out["signals_today"]["base"]]}
        for k, v in out["variants"].items():
            rec[k] = {"signals": v["signals"], "trades": v["trades"], "win_rate": v["win_rate"], "total_return_pct": v["total_return_pct"],
                      "max_drawdown_pct": v["max_drawdown_pct"], "open": len(v["open_positions"])}
        with open(HISTORY_PATH, "a", encoding="utf-8") as fp:
            fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print("저장: %s, %s" % (OUT_PATH, HISTORY_PATH))
    return 0


if __name__ == "__main__":
    sys.exit(main())
