#!/usr/bin/env python3
"""판정 규율 — 네 트랙(한국주식 돌파·터틀·추세전환·초단타)의 순방향 기록을 한 곳에서 채점한다.

Phil(bennyjo/phil) 에서 가져온 방법 세 가지:
  1. 사전 등록: 합격선은 결과가 나오기 전에 criteria.json 에 봉인한다. 이 파일은 기준을 만들지 않고 읽기만 한다.
  2. 운 보정 z: 백테스트 승률대로면 몇 승이 기대되는지와 실제 승수를 비교한다. z 가 −2 아래면 불운이 아니라
     백테스트가 부풀려진 것(생존 편향·과최적화)으로 본다.
  3. 규칙 잠금: 세 트랙의 규칙 상수와 criteria.json 의 지문을 rules_lock.json 에 적어 두고, 단위시험이 매번 대조한다.
     규칙을 바꾸려면 proposals.md 에 근거 → RULE_FIXED 새 날짜 → `python verdict.py --lock`. 이 순서 없이 바꾸면 CI 가 빨간불.

이 파일은 보호 엔진이다(Phil 의 core/ 에 해당). 트랙 코드가 바뀌어도 여기 채점 규칙은 바뀌지 않는다.

사용:
  python verdict.py              # 채점 → verdict.json, verdict_history.jsonl (같은 날짜는 덮어쓴다)
  python verdict.py --report     # 이력 추이
  python verdict.py --check-lock # 잠금과 현재 규칙 대조 (어긋나면 종료코드 1)
  python verdict.py --lock       # 잠금 재생성 (사전 등록 절차의 마지막 단계)
"""
import os, sys, json, math, hashlib, argparse
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
KST = timezone(timedelta(hours=9))
CRITERIA_PATH = os.path.join(HERE, "criteria.json")
LOCK_PATH = os.path.join(HERE, "rules_lock.json")
OUT_PATH = os.path.join(HERE, "verdict.json")
HISTORY_PATH = os.path.join(HERE, "verdict_history.jsonl")

NOT_YET, PASS, FAIL = "NOT_YET", "PASS", "FAIL"


# ----------------------------------------------------------------- 순수 계산

def r2(x):
    return None if x is None else round(x, 2)


def luck_z(n, wins, p):
    """백테스트 승률 p 로 n 번 매매했을 때 기대 승수 대비 실제 승수의 z. n==0 이나 p 가 0/1 이면 None."""
    if not n or p is None or p <= 0 or p >= 1:
        return None
    exp = n * p
    sd = math.sqrt(n * p * (1 - p))
    return {"expected_wins": round(exp, 2), "actual_wins": wins, "z": round((wins - exp) / sd, 2)}


def profit_factor(pnls):
    """손익비 = 이익 합 ÷ 손실 합(절댓값). 손실이 없으면 None(무한대 대신)."""
    g = sum(x for x in pnls if x > 0)
    l = -sum(x for x in pnls if x < 0)
    if l == 0:
        return None
    return round(g / l, 3)


# ----------------------------------------------------------------- 트랙별 채점

def judge_kr_breakout(fwd, backtest, crit):
    """kr_breakout.json(순방향) + kr_backtest.json 으로 채점. 기준은 criteria.json['kr_breakout']."""
    v = crit["variant"]
    st = fwd["variants"][v]
    n, wins = st["trades"], st["wins"]
    trades = fwd.get("trades", {}).get(v, [])
    bt = backtest["variants"][v] if backtest else None
    p = (bt["win_rate"] / 100.0) if bt and bt.get("win_rate") else None
    out = {
        "variant": v, "forward_days": fwd.get("forward", {}).get("days"), "rule_fixed": fwd.get("rule_fixed"),
        "trades": n, "wins": wins, "win_rate_pct": st.get("win_rate"),
        "total_return_pct": st.get("total_return_pct"), "max_drawdown_pct": st.get("max_drawdown_pct"),
        "signals": st.get("signals"), "skipped_no_slot": (st.get("skipped") or {}).get("limit"),
        "backtest_win_rate_pct": bt.get("win_rate") if bt else None,
        "luck": luck_z(n, wins, p),
        "checks": {}, "warnings": [],
    }
    # 대조군(업종 동조 없음)과의 차이 — 기록만, 합격선에는 안 쓴다
    cv = crit.get("control_variant")
    if cv and cv in fwd["variants"]:
        c = fwd["variants"][cv]
        out["control"] = {"variant": cv, "trades": c["trades"], "win_rate_pct": c.get("win_rate"),
                          "total_return_pct": c.get("total_return_pct"),
                          "return_delta_pct": r2((st.get("total_return_pct") or 0) - (c.get("total_return_pct") or 0))}
    if n < crit["min_trades"]:
        out["status"] = NOT_YET
        out["reason"] = "순방향 매매 %d건 — 최소 %d건 전에는 판단하지 않는다" % (n, crit["min_trades"])
        return out

    wr = st.get("win_rate") or 0.0
    out["checks"]["win_rate"] = {"value": wr, "fail_below": crit["fail_if_win_rate_below_pct"],
                                 "pass_at_least": crit["pass_if_win_rate_at_least_pct"],
                                 "ok": wr >= crit["pass_if_win_rate_at_least_pct"], "fail": wr < crit["fail_if_win_rate_below_pct"]}
    stop_losses = [t["pnl_pct"] for t in trades if t.get("why") == "stop"]
    too_deep = [x for x in stop_losses if x < crit["stop_trade_max_loss_pct"]]
    out["checks"]["stop_loss_depth"] = {"stop_trades": len(stop_losses), "deeper_than_limit": len(too_deep),
                                        "limit_pct": crit["stop_trade_max_loss_pct"],
                                        "ok": len(too_deep) <= crit["stop_trade_tolerance_count"]}
    h20 = [t for t in trades if t.get("why") == "hold20"]
    h20_wins = sum(1 for t in h20 if t.get("pnl_krw", 0) > 0)
    share = (h20_wins / len(h20)) if h20 else None
    out["checks"]["hold20_win_share"] = {"trades": len(h20), "wins": h20_wins, "share": r2(share),
                                         "fail_below": crit["hold20_fail_if_win_share_below"],
                                         "applies": len(h20) >= crit["hold20_min_trades"],
                                         "fail": len(h20) >= crit["hold20_min_trades"] and share < crit["hold20_fail_if_win_share_below"]}
    if out["luck"] and out["luck"]["z"] < crit["luck_z_warn_below"]:
        out["warnings"].append("운 보정 z %.2f — 백테스트 승률(%.1f%%)대로면 %.1f승 기대, 실제 %d승. 생존 편향 의심" % (
            out["luck"]["z"], bt["win_rate"], out["luck"]["expected_wins"], wins))
    if out["checks"]["win_rate"]["fail"]:
        out["status"], out["reason"] = FAIL, "승률 %.1f%% < %.0f%% — 사전 등록 기준 미달" % (wr, crit["fail_if_win_rate_below_pct"])
    elif out["checks"]["hold20_win_share"]["fail"]:
        out["status"], out["reason"] = FAIL, "20일 보유 매매 승 비율 %.0f%% < %.0f%% — 백테스트(약 95%%)와 다른 모양" % (
            share * 100, crit["hold20_fail_if_win_share_below"] * 100)
    elif out["checks"]["win_rate"]["ok"] and out["checks"]["stop_loss_depth"]["ok"]:
        out["status"], out["reason"] = PASS, "승률 %.1f%%, 손절 깊이 정상 — 백테스트 모양과 맞다. 소액 실전 설계 후보" % wr
    else:
        out["status"], out["reason"] = NOT_YET, "승률 %.1f%% 는 15~20%% 사이이거나 손절이 기준보다 깊다 — 더 모은다" % wr
    return out


def _venue_forward(vd):
    trades = vd.get("portfolio", {}).get("forward_trades") or []
    pnls = [t.get("pnl", 0.0) for t in trades]
    wins = sum(1 for x in pnls if x > 0)
    st = vd.get("portfolio", {}).get("stats", {})
    return {
        "trades": len(trades), "wins": wins,
        "win_rate_pct": r2(100.0 * wins / len(trades)) if trades else None,
        "net_pct": (st.get("forward") or {}).get("net_pct"),
        "profit_factor": profit_factor(pnls) if trades else None,
        "avg_r": r2(sum(t.get("r_multiple", 0) for t in trades) / len(trades)) if trades else None,
        "backtest_win_rate_pct": st.get("win_rate_pct"), "backtest_profit_factor": st.get("profit_factor"),
        "backtest_trades": st.get("trades"),
        "luck": luck_z(len(trades), wins, (st.get("win_rate_pct") or 0) / 100.0),
    }


def judge_venue_track(data, crit, name):
    """터틀·추세전환 공통: 거래소별로 순방향 매매를 채점한다. 기준은 criteria.json[name]."""
    venues = {}
    total_n = 0
    for v, vd in (data.get("venues") or {}).items():
        f = _venue_forward(vd)
        f["variants"] = {}
        for k, e in (vd.get("variants") or {}).items():
            fe = _venue_forward(e)
            f["variants"][k] = {"trades": fe["trades"], "wins": fe["wins"], "net_pct": fe["net_pct"], "profit_factor": fe["profit_factor"]}
        f["warnings"] = []
        if f["luck"] and f["luck"]["z"] < crit["luck_z_warn_below"]:
            f["warnings"].append("운 보정 z %.2f — 백테스트 승률대로면 %.1f승 기대, 실제 %d승" % (
                f["luck"]["z"], f["luck"]["expected_wins"], f["wins"]))
        n = f["trades"]
        total_n += n
        if n < crit["min_trades"]:
            f["status"], f["reason"] = NOT_YET, "순방향 %d건 — 최소 %d건 전에는 판단하지 않는다" % (n, crit["min_trades"])
        else:
            pf = f["profit_factor"]
            z = f["luck"]["z"] if f["luck"] else None
            if pf is not None and pf < crit["fail_if_profit_factor_below"]:
                f["status"], f["reason"] = FAIL, "손익비 %.2f < %.1f" % (pf, crit["fail_if_profit_factor_below"])
            elif "fail_if_luck_z_below" in crit and z is not None and z < crit["fail_if_luck_z_below"]:
                f["status"], f["reason"] = FAIL, "운 보정 z %.2f < %.1f — 승률이 백테스트와 어긋난다" % (z, crit["fail_if_luck_z_below"])
            elif pf is None or pf >= crit["pass_if_profit_factor_at_least"]:
                f["status"], f["reason"] = PASS, "손익비 %s ≥ %.1f (손실 없음이면 None)" % (pf, crit["pass_if_profit_factor_at_least"])
            else:
                f["status"], f["reason"] = NOT_YET, "손익비 %.2f 는 %.1f~%.1f 사이 — 더 모은다" % (
                    pf, crit["fail_if_profit_factor_below"], crit["pass_if_profit_factor_at_least"])
        venues[v] = f
    statuses = [f["status"] for f in venues.values()]
    if not statuses:
        overall, reason = NOT_YET, "거래소 자료 없음"
    elif FAIL in statuses:
        overall, reason = FAIL, "거래소 중 하나가 불합격: " + ", ".join("%s=%s" % (v, f["status"]) for v, f in venues.items())
    elif all(s == PASS for s in statuses):
        overall, reason = PASS, "모든 거래소 합격"
    else:
        overall, reason = NOT_YET, "순방향 합계 %d건: " % total_n + ", ".join("%s=%s" % (v, f["status"]) for v, f in venues.items())
    return {"rule_fixed": (data.get("rules") or {}).get("rule_fixed"), "status": overall, "reason": reason,
            "forward_trades_total": total_n, "venues": venues}


def judge_scalp(history_rows, crit):
    """scalp_history.jsonl 의 누적 기록에 scalp.py 의 후보 규칙(cumulative_candidates)을 그대로 적용한다.
    (2026-10-07 수정: 처음엔 scalp.json 의 '어제 하루' 견고 표시를 읽어 근거가 틀렸다. 기준값은 그대로.)"""
    sys.path.insert(0, HERE)
    import scalp as S
    days = len(history_rows)
    acc = S.accumulate(history_rows)
    combos = sum(1 for k in acc if S.split_key(k)[3] != S.BASELINE)
    cands = S.cumulative_candidates(acc) if days >= crit["min_days"] else []
    out = {"history_days": days, "combos": combos, "candidates": len(cands),
           "top": [{"key": k, "net_pct": round(a["nt"], 3), "days": a["days"], "pos_days": a["pos"], "trades": a["n"]}
                   for k, a in cands[:10]],
           "warnings": []}
    if days < crit["min_days"]:
        out["status"], out["reason"] = NOT_YET, "기록 %d일 — 최소 %d일 전에는 후보를 말하지 않는다" % (days, crit["min_days"])
    elif cands:
        out["status"], out["reason"] = PASS, "누적 %d일, 후보 %d개 (조합 %d개 중) — 누적 순수익>0·플러스 일수 60%%·기준선 초과" % (
            days, len(cands), combos)
        out["warnings"].append("조합 %d개를 동시에 시험하므로 후보 일부는 우연이다. 합격 = 소액 체결 시험 후보이지 실력 확정이 아니다" % combos)
    else:
        out["status"], out["reason"] = FAIL, "누적 %d일, 후보 0개 — 지금 규칙 중 비용을 이기는 것이 없다" % days
    return out


# ----------------------------------------------------------------- 규칙 잠금

def _file_sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def current_rules():
    """세 트랙 + 초단타의 규칙 상수를 소스에서 그대로 읽는다 (저장된 JSON 이 아니라 코드가 기준)."""
    sys.path.insert(0, HERE)
    import kr_breakout as KB, turtle as T, reversal as R, scalp as S
    turtle_rules = {"entry_breakout_days": T.ENTRY_N, "exit_breakout_days": T.EXIT_N, "atr_days": T.ATR_N,
                    "stop_n_mult": T.STOP_MULT, "risk_pct_per_trade": T.RISK_PCT, "max_notional_pct": T.MAX_NOTIONAL_PCT,
                    "slippage_pct": T.SLIPPAGE_PCT,
                    "variants": {k: {"entry_n": v["entry_n"], "exit_n": v["exit_n"], "chandelier": v.get("chandelier")} for k, v in T.VARIANTS.items()},
                    "fees": {k: v["fee_pct"] for k, v in T.VENUES.items()}}
    reversal_rules = {"bar_minutes": R.BAR_MIN, "pivot_k": R.PIVOT_K, "lookback_bars": R.LOOKBACK, "vol_avg_bars": R.VOL_N,
                      "vol_mult": R.VOL_MULT, "min_height_atr": R.MIN_HEIGHT_ATR, "atr_bars": R.ATR_N,
                      "break_wait_bars": R.BREAK_WAIT, "pullback_wait_bars": R.PULLBACK_WAIT,
                      "risk_pct_per_trade": R.RISK_PCT, "max_notional_pct": R.MAX_NOTIONAL_PCT, "slippage_pct": R.SLIPPAGE_PCT,
                      "variants": {k: R.variant_kwargs(v) for k, v in R.VARIANTS.items()},
                      "fees": {k: v["fee_pct"] for k, v in R.VENUES.items()}}
    scalp_rules = {"strategies": [{"key": s["key"], "max_hold": s["max_hold"], "exec": s["exec"]} for s in S.STRATEGIES],
                   "baseline": S.BASELINE, "spread_ticks": S.SPREAD_TICKS, "limit_ttl": S.LIMIT_TTL,
                   "fees": {k: {"taker_pct": v["taker_pct"], "maker_pct": v["maker_pct"]} for k, v in S.VENUES.items()}}
    return {
        "kr_breakout": {"rule_fixed": KB.RULE_FIXED, "rules": KB.rules_dict()},
        "turtle": {"rule_fixed": T.RULE_FIXED, "rules": turtle_rules},
        "reversal": {"rule_fixed": R.RULE_FIXED, "rules": reversal_rules},
        "scalp": {"rules": scalp_rules},
        "criteria_sha256": _file_sha(CRITERIA_PATH),
    }


def _flatten(d, prefix=""):
    out = {}
    for k, v in d.items():
        key = prefix + "/" + str(k) if prefix else str(k)
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key] = json.dumps(v, ensure_ascii=False, sort_keys=True)
    return out


def check_lock(current=None, lock=None):
    """잠금과 현재 규칙의 차이 목록. 빈 목록이면 일치."""
    current = current or current_rules()
    if lock is None:
        if not os.path.exists(LOCK_PATH):
            return ["rules_lock.json 이 없다 — `python verdict.py --lock` 으로 만든다"]
        with open(LOCK_PATH, encoding="utf-8") as fh:
            lock = json.load(fh)
    a, b = _flatten({k: v for k, v in lock.items() if not k.startswith("_")}), _flatten(current)
    diffs = []
    for k in sorted(set(a) | set(b)):
        if a.get(k) != b.get(k):
            diffs.append("%s: 잠금=%s 현재=%s" % (k, a.get(k, "(없음)"), b.get(k, "(없음)")))
    return diffs


def write_lock():
    cur = current_rules()
    cur["_comment"] = ("규칙 잠금. 트랙 코드의 규칙 상수와 criteria.json 지문을 적어 둔다. tests/test_verdict.py 가 매번 대조하므로, "
                       "규칙을 바꾸면 반드시 (1) proposals.md 에 근거 (2) 그 트랙의 RULE_FIXED 를 새 날짜로 (3) `python verdict.py --lock` 순서로 간다.")
    cur["_locked_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(LOCK_PATH, "w", encoding="utf-8") as fh:
        json.dump(cur, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return cur


# ----------------------------------------------------------------- 조립·저장

def load_json(name):
    p = os.path.join(HERE, name)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def build(crit=None, files=None):
    crit = crit or load_json("criteria.json")
    files = files or {}
    get = lambda n: files[n] if n in files else load_json(n)
    now = datetime.now(KST)
    out = {"schema": "carrygate-verdict/1", "date": now.strftime("%Y-%m-%d"),
           "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "criteria_sealed": crit.get("sealed"), "tracks": {}, "errors": {}}
    try:
        kb = get("kr_breakout.json")
        if kb and kb.get("mode") == "forward":
            out["tracks"]["kr_breakout"] = judge_kr_breakout(kb, get("kr_backtest.json"), crit["kr_breakout"])
        else:
            out["errors"]["kr_breakout"] = "kr_breakout.json 없음 또는 forward 모드 아님"
    except Exception as e:
        out["errors"]["kr_breakout"] = "%s: %s" % (type(e).__name__, e)
    for name in ("turtle", "reversal"):
        try:
            d = get(name + ".json")
            if d:
                out["tracks"][name] = judge_venue_track(d, crit[name], name)
            else:
                out["errors"][name] = name + ".json 없음"
        except Exception as e:
            out["errors"][name] = "%s: %s" % (type(e).__name__, e)
    try:
        if "scalp_history" in files:
            hist = files["scalp_history"]
        else:
            sys.path.insert(0, HERE)
            import scalp as S
            hist = S.load_history(os.path.join(HERE, "scalp_history.jsonl"))
        if hist:
            out["tracks"]["scalp"] = judge_scalp(hist, crit["scalp"])
        else:
            out["errors"]["scalp"] = "scalp_history.jsonl 없음"
    except Exception as e:
        out["errors"]["scalp"] = "%s: %s" % (type(e).__name__, e)
    try:
        diffs = check_lock()
        out["rules_lock"] = {"ok": not diffs, "diffs": diffs}
    except Exception as e:
        out["rules_lock"] = {"ok": False, "diffs": ["%s: %s" % (type(e).__name__, e)]}
    out["summary"] = {k: v["status"] for k, v in out["tracks"].items()}
    return out


def history_line(out):
    t = out["tracks"]
    line = {"date": out["date"], "summary": out["summary"], "rules_lock_ok": out["rules_lock"]["ok"]}
    if "kr_breakout" in t:
        k = t["kr_breakout"]
        line["kr_breakout"] = {"n": k["trades"], "wins": k["wins"], "win_rate": k["win_rate_pct"], "ret": k["total_return_pct"],
                               "z": (k["luck"] or {}).get("z"), "days": k["forward_days"]}
    for name in ("turtle", "reversal"):
        if name in t:
            line[name] = {v: {"n": f["trades"], "wins": f["wins"], "pf": f["profit_factor"], "z": (f["luck"] or {}).get("z")}
                          for v, f in t[name]["venues"].items()}
    if "scalp" in t:
        line["scalp"] = {"days": t["scalp"]["history_days"], "candidates": t["scalp"]["candidates"]}
    return line


def write_history(out, path=HISTORY_PATH):
    """하루 한 줄. 같은 날짜는 덮어쓴다 (터틀·펀딩과 같은 방식)."""
    try:
        with open(path, encoding="utf-8") as fh:
            rows = [l for l in fh.read().splitlines() if l.strip()]
    except IOError:
        rows = []
    rows = [l for l in rows if json.loads(l).get("date") != out["date"]]
    rows.append(json.dumps(history_line(out), ensure_ascii=False))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(rows) + "\n")


def print_summary(out):
    print("=== 판정 (사전 등록 기준 봉인일 %s) ===" % out["criteria_sealed"])
    t = out["tracks"]
    if "kr_breakout" in t:
        k = t["kr_breakout"]
        z = (k["luck"] or {}).get("z")
        print("[kr_breakout] %-7s 순방향 %s일 매매 %d건 승 %d  승률 %s%%  수익 %s%%  z=%s  | %s" % (
            k["status"], k["forward_days"], k["trades"], k["wins"], k["win_rate_pct"] or "-", k["total_return_pct"], z if z is not None else "-", k["reason"]))
        if k.get("control"):
            c = k["control"]
            print("              대조군 %s: 매매 %d건 승률 %s%% 수익 %s%% (기본−대조 %s%%p)" % (
                c["variant"], c["trades"], c["win_rate_pct"] or "-", c["total_return_pct"], c["return_delta_pct"]))
        for w in k.get("warnings", []):
            print("              ⚠ " + w)
    for name in ("turtle", "reversal"):
        if name in t:
            tr = t[name]
            print("[%s] %-7s %s" % (name, tr["status"], tr["reason"]))
            for v, f in tr["venues"].items():
                z = (f["luck"] or {}).get("z")
                print("   %-6s %-7s 매매 %d건 승 %d  손익비 %s  순 %s%%  z=%s  (백테스트 승률 %s%% 손익비 %s)" % (
                    v, f["status"], f["trades"], f["wins"], f["profit_factor"] if f["profit_factor"] is not None else "-",
                    f["net_pct"] if f["net_pct"] is not None else "-", z if z is not None else "-",
                    f["backtest_win_rate_pct"], f["backtest_profit_factor"]))
                for w in f.get("warnings", []):
                    print("          ⚠ " + w)
    if "scalp" in t:
        s = t["scalp"]
        print("[scalp] %-7s %s" % (s["status"], s["reason"]))
        for c in s["top"][:5]:
            print("   %-38s 누적 %+.3f%%  %d일 중 %d일 플러스  매매 %d건" % (c["key"], c["net_pct"], c["days"], c["pos_days"], c["trades"]))
        for w in s.get("warnings", []):
            print("   ⚠ " + w)
    rl = out["rules_lock"]
    print("[rules_lock] %s" % ("일치" if rl["ok"] else "불일치 — 사전 등록 없이 규칙이 바뀌었다"))
    for d in rl["diffs"]:
        print("   " + d)
    for k, e in out["errors"].items():
        print("[오류] %s: %s" % (k, e))


def print_report(path=HISTORY_PATH):
    try:
        rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    except IOError:
        print("판정 이력 없음")
        return
    print("%-10s %-9s %-9s %-9s %-9s %s" % ("날짜", "돌파", "터틀", "추세전환", "초단타", "돌파 n/승/z"))
    for r in rows:
        s = r.get("summary", {})
        k = r.get("kr_breakout") or {}
        print("%-10s %-9s %-9s %-9s %-9s %s/%s/%s" % (r["date"], s.get("kr_breakout", "-"), s.get("turtle", "-"),
                                                    s.get("reversal", "-"), s.get("scalp", "-"), k.get("n", "-"), k.get("wins", "-"), k.get("z", "-")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--lock", action="store_true")
    ap.add_argument("--check-lock", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="저장하지 않고 출력만")
    a = ap.parse_args()
    if a.report:
        print_report()
        return
    if a.lock:
        write_lock()
        print("rules_lock.json 갱신")
        return
    if a.check_lock:
        diffs = check_lock()
        if diffs:
            print("규칙 잠금 불일치 — 사전 등록(proposals.md → RULE_FIXED → --lock) 없이 규칙이 바뀌었다:")
            for d in diffs:
                print("  " + d)
            sys.exit(1)
        print("규칙 잠금 일치")
        return
    out = build()
    print_summary(out)
    if not a.dry_run:
        with open(OUT_PATH, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2)
        write_history(out)


if __name__ == "__main__":
    main()
