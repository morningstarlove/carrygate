# -*- coding: utf-8 -*-
"""
초단타 후보 "표본 밖" 확인 — 후보를 고른 날 목록을 얼려 두고, 그 뒤 날짜만으로 다시 센다.

후보는 수백 개 조합 중 성적이 좋은 것을 고른 것이라 고른 날까지의 성적에는 운이 섞인다.
고른 뒤의 성적(표본 밖)만 운이 섞이지 않는다. 10/7 후보 12개는 표본 밖 14일 동안 12개 모두
손해였다(제안함 P10). 같은 실수를 막으려고 목록과 통과 조건을 결과 전에 파일로 고정한다(제안함 P19).

  python scripts/scalp_oos_check.py                     # 얼린 목록 전부 확인
  python scripts/scalp_oos_check.py --freeze 2026-10-21 --tf 30m,60m --rule vwap \
      --primary okx:XRP:30m:vwap_fade_lim               # 새로 얼리기 (같은 이름은 덮어쓰지 않음)
  python scripts/scalp_oos_check.py --map 2026-10-21 --venue binance \
      --primary binance:XRP:30m:vwap_fade_lim           # 얼린 목록의 (코인·봉·규칙)을 다른 거래소로 옮겨 얼림 (P20)
  python scripts/scalp_oos_check.py --auto              # 매일(워크플로): 판정기가 잡음 95% 선을 넘으면 그날 후보를 스스로 얼리고,
                                                        # 얼린 목록 전부의 표본 밖 상태를 scalp_oos.json 에 쓴다 (P28)

표준 라이브러리만 쓴다. 매매 규칙·criteria.json·잠금과 무관(읽기만).
"""
import argparse, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import scalp  # noqa: E402

FROZEN = os.path.join(ROOT, "scalp_oos_frozen.json")
HISTORY = os.path.join(ROOT, "scalp_history.jsonl")
# 통과 조건 (P19, 2026-10-21 결과 전 등록). 얼릴 때 파일에 함께 적힌다 — 여기 값을 바꿔도 이미 얼린 것은 안 바뀐다
DEFAULT_RULE = {"min_oos_days": 10, "primary_min_pos_ratio": 0.6, "breadth_min_share": 0.5}
# 자동 얼림 (P28, 2026-11-05 사용자 결정 "A", 결과 전 등록): 사람이 날짜·후보를 고르지 않는다.
#   방아쇠: verdict.json 초단타 후보 수 > 잡음 바닥 95% 선(p95). 중앙값보다 엄격 — "운으로 설명되지 않는 합격"
#   얼리는 것: 그날 후보 전부, 주 후보 = 누적 1위. 통과 조건은 P19 와 같고 표본 밖 기간만 14일(2주)
#   간격: 직전 자동 얼림이 아직 판정 전(WAIT)이면 새로 얼리지 않는다 — 같은 운을 여러 번 세지 않게
AUTO_RULE = dict(DEFAULT_RULE, min_oos_days=14)
VERDICT = os.path.join(ROOT, "verdict.json")
OOS_OUT = os.path.join(ROOT, "scalp_oos.json")


def load_rows(path=HISTORY):
    if not os.path.isabs(path):
        path = os.path.join(ROOT, path)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def freeze(rows, date, tfs=None, rule_sub=None, primary=None):
    """date 까지의 이력으로 후보를 뽑아 얼린다. tfs·rule_sub 로 좁힌다."""
    upto = [r for r in rows if r.get("date", "") <= date]
    keys = []
    for k, a in scalp.cumulative_candidates(scalp.accumulate(upto)):
        _, _, tf, st = scalp.split_key(k)
        if tfs and tf not in tfs:
            continue
        if rule_sub and rule_sub not in st:
            continue
        keys.append({"key": k, "nt": round(a["nt"], 4), "pos": a["pos"], "days": a["days"], "n": a["n"]})
    if primary and primary not in [x["key"] for x in keys]:
        raise SystemExit("primary %s 가 %s 후보에 없다" % (primary, date))
    return {"name": date, "date": date, "history": "scalp_history.jsonl", "tfs": tfs, "rule_sub": rule_sub,
            "primary": primary, "rule": dict(DEFAULT_RULE), "keys": keys}


def map_venue(fz, venue, primary=None, history=None):
    """얼린 목록의 (코인·봉·규칙)을 다른 거래소로 옮긴다. 그 거래소 기록은 고르는 데 안 썼으므로
    얼린 날 뒤 성적은 그대로 표본 밖이다. 같은 조합이 여러 거래소에서 뽑혔으면 하나로 합친다."""
    keys, seen = [], set()
    for x in fz["keys"]:
        _, coin, tf, st = scalp.split_key(x["key"])
        k = "%s:%s:%s:%s" % (venue, coin, tf, st)
        if k not in seen:
            seen.add(k)
            keys.append({"key": k, "nt": None, "pos": None, "days": None, "n": None, "from": x["key"]})
    if primary and primary not in seen:
        raise SystemExit("primary %s 가 옮긴 목록에 없다" % primary)
    return {"name": "%s-%s" % (fz["name"], venue), "date": fz["date"],
            "history": history or "scalp_%s_history.jsonl" % venue, "derived_from": fz["name"],
            "tfs": fz.get("tfs"), "rule_sub": fz.get("rule_sub"), "primary": primary,
            "rule": dict(fz["rule"]), "keys": keys}


def check(rows, fz, until=None):
    """얼린 날 다음 날부터(until 까지) 표본 밖 누적. 판정 GO / NO / WAIT."""
    oos = [r for r in rows if r.get("date", "") > fz["date"] and (until is None or r.get("date", "") <= until)]
    acc = scalp.accumulate(oos)
    days = len({r.get("date") for r in oos})
    out = []
    for x in fz["keys"]:
        a = acc.get(x["key"], {"days": 0, "pos": 0, "nt": 0.0, "n": 0})
        out.append({"key": x["key"], "in_nt": x["nt"], "oos_nt": round(a["nt"], 4),
                    "oos_pos": a["pos"], "oos_days": a["days"], "oos_n": a["n"]})
    rule = fz["rule"]
    plus = sum(1 for o in out if o["oos_nt"] > 0)
    share = plus / len(out) if out else 0.0
    prim = next((o for o in out if o["key"] == fz.get("primary")), None)
    prim_ok = None
    if prim:
        prim_ok = prim["oos_nt"] > 0 and prim["oos_days"] > 0 and prim["oos_pos"] / prim["oos_days"] >= rule["primary_min_pos_ratio"]
    if days < rule["min_oos_days"]:
        verdict = "WAIT"
    elif share >= rule["breadth_min_share"] and (prim_ok is None or prim_ok):
        verdict = "GO"
    else:
        verdict = "NO"
    return {"name": fz.get("name", fz["date"]), "date": fz["date"], "oos_days": days, "plus": plus, "total": len(out),
            "share": round(share, 3), "primary": prim, "primary_ok": prim_ok, "verdict": verdict, "rows": out}


def auto_freeze(data, verdict, rows, loader=None):
    """방아쇠가 당겨졌으면 새 얼림 dict, 아니면 (None, 이유)."""
    sc = (verdict.get("tracks") or {}).get("scalp") or {}
    nf = sc.get("noise_floor") or {}
    n, p95, date = sc.get("candidates"), nf.get("p95"), verdict.get("date")
    if n is None or p95 is None or not date:
        return None, "판정 파일에 후보 수·잡음 95% 선이 없다"
    if n <= p95:
        return None, "후보 %d개 ≤ 잡음 95%% 선 %d개 — 자동 얼림 안 함" % (n, p95)
    name = "auto-" + date
    if any(f.get("name") == name for f in data["freezes"]):
        return None, "%s 는 이미 얼려 있다" % name
    for f in data["freezes"]:
        if f.get("auto") and check((loader or load_rows)(f.get("history", HISTORY)), f)["verdict"] == "WAIT":
            return None, "직전 자동 얼림 %s 가 아직 판정 전 — 새로 얼리지 않는다" % f["name"]
    upto = [r for r in rows if r.get("date", "") <= date]
    cands = scalp.cumulative_candidates(scalp.accumulate(upto))
    if not cands:
        return None, "후보 없음"
    fz = freeze(rows, date, None, None, cands[0][0])
    fz.update({"name": name, "auto": True, "rule": dict(AUTO_RULE),
               "trigger": {"candidates": n, "noise_p95": p95, "noise_median": nf.get("median")}})
    return fz, "후보 %d개 > 잡음 95%% 선 %d개 — %s 로 %d개 얼림, 주 후보 %s" % (n, p95, name, len(fz["keys"]), fz["primary"])


def status_all(data, until=None):
    out = []
    for fz in data["freezes"]:
        r = check(load_rows(fz.get("history", HISTORY)), fz, until)
        out.append({k: r[k] for k in ("name", "date", "oos_days", "plus", "total", "share", "primary_ok", "verdict")}
                   | {"auto": bool(fz.get("auto")), "min_oos_days": fz["rule"]["min_oos_days"],
                      "primary": (r["primary"] or {}).get("key"), "primary_oos_nt": (r["primary"] or {}).get("oos_nt")})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--freeze")
    ap.add_argument("--tf", help="쉼표로 구분, 예: 30m,60m")
    ap.add_argument("--rule", help="규칙 이름에 들어갈 글자, 예: vwap")
    ap.add_argument("--primary")
    ap.add_argument("--until")
    ap.add_argument("--map", help="이미 얼린 목록 이름 → --venue 거래소로 옮겨 얼림")
    ap.add_argument("--venue")
    ap.add_argument("--auto", action="store_true", help="자동 얼림 + scalp_oos.json 쓰기 (워크플로용)")
    a = ap.parse_args()
    data = json.load(open(FROZEN, encoding="utf-8")) if os.path.exists(FROZEN) else {"freezes": []}
    names = [f.get("name", f["date"]) for f in data["freezes"]]
    if a.auto:
        verdict = json.load(open(VERDICT, encoding="utf-8")) if os.path.exists(VERDICT) else {}
        fz, why = auto_freeze(data, verdict, load_rows())
        print("자동 얼림:", why)
        if fz:
            data["freezes"].append(fz)
            with open(FROZEN, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1)
        st = status_all(data)
        with open(OOS_OUT, "w", encoding="utf-8") as fh:
            json.dump({"schema": "carrygate-scalp-oos/1", "date": verdict.get("date"), "auto_freeze": why,
                       "freezes": st}, fh, ensure_ascii=False, indent=1)
        for x in st:
            print("  %-22s 표본 밖 %2d/%d일  플러스 %d/%d  주 후보 %s %s  → %s" % (
                x["name"], x["oos_days"], x["min_oos_days"], x["plus"], x["total"], x["primary"],
                "-" if x["primary_oos_nt"] is None else "%+.2f%%" % x["primary_oos_nt"], x["verdict"]))
        return
    if a.freeze or a.map:
        if a.freeze:
            new = freeze(load_rows(), a.freeze, a.tf.split(",") if a.tf else None, a.rule, a.primary)
        else:
            src = next((f for f in data["freezes"] if f.get("name", f["date"]) == a.map), None)
            if src is None or not a.venue:
                raise SystemExit("--map 은 얼린 목록 이름과 --venue 가 필요하다")
            new = map_venue(src, a.venue, a.primary)
        if new["name"] in names:
            raise SystemExit("%s 는 이미 얼려 있다 — 덮어쓰지 않는다" % new["name"])
        data["freezes"].append(new)
        with open(FROZEN, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        print("얼림 %s: %d개" % (new["name"], len(new["keys"])))
        return
    for fz in data["freezes"]:
        r = check(load_rows(fz.get("history", HISTORY)), fz, a.until)
        print("=== %s (얼린 날 %s) — 표본 밖 %d일, 플러스 %d/%d (%.0f%%), 판정 %s ===" % (
            r["name"], r["date"], r["oos_days"], r["plus"], r["total"], 100 * r["share"], r["verdict"]))
        if r["primary"]:
            p = r["primary"]
            print("  주 후보 %s: 표본 밖 %+.3f%% (%d/%d일 플러스, %d건) → %s" % (
                p["key"], p["oos_nt"], p["oos_pos"], p["oos_days"], p["oos_n"],
                "대기" if r["verdict"] == "WAIT" else ("통과" if r["primary_ok"] else "미달")))
        for o in sorted(r["rows"], key=lambda o: o["oos_nt"], reverse=True):
            print("  %-36s 고른 날까지 %8s | 표본 밖 %+8.3f%% (%d/%d일, %d건)" % (
                o["key"], "-" if o["in_nt"] is None else "%+.3f%%" % o["in_nt"], o["oos_nt"], o["oos_pos"], o["oos_days"], o["oos_n"]))


if __name__ == "__main__":
    main()
