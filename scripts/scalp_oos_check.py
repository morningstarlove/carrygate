# -*- coding: utf-8 -*-
"""
초단타 후보 "표본 밖" 확인 — 후보를 고른 날 목록을 얼려 두고, 그 뒤 날짜만으로 다시 센다.

후보는 수백 개 조합 중 성적이 좋은 것을 고른 것이라 고른 날까지의 성적에는 운이 섞인다.
고른 뒤의 성적(표본 밖)만 운이 섞이지 않는다. 10/7 후보 12개는 표본 밖 14일 동안 12개 모두
손해였다(제안함 P10). 같은 실수를 막으려고 목록과 통과 조건을 결과 전에 파일로 고정한다(제안함 P19).

  python scripts/scalp_oos_check.py                     # 얼린 목록 전부 확인
  python scripts/scalp_oos_check.py --freeze 2026-10-21 --tf 30m,60m --rule vwap \
      --primary okx:XRP:30m:vwap_fade_lim               # 새로 얼리기 (같은 날짜는 덮어쓰지 않음)

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


def load_rows(path=HISTORY):
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
    return {"date": date, "tfs": tfs, "rule_sub": rule_sub, "primary": primary,
            "rule": dict(DEFAULT_RULE), "keys": keys}


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
    return {"date": fz["date"], "oos_days": days, "plus": plus, "total": len(out),
            "share": round(share, 3), "primary": prim, "primary_ok": prim_ok, "verdict": verdict, "rows": out}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--freeze")
    ap.add_argument("--tf", help="쉼표로 구분, 예: 30m,60m")
    ap.add_argument("--rule", help="규칙 이름에 들어갈 글자, 예: vwap")
    ap.add_argument("--primary")
    ap.add_argument("--until")
    a = ap.parse_args()
    rows = load_rows()
    data = json.load(open(FROZEN, encoding="utf-8")) if os.path.exists(FROZEN) else {"freezes": []}
    if a.freeze:
        if any(f["date"] == a.freeze for f in data["freezes"]):
            raise SystemExit("%s 는 이미 얼려 있다 — 덮어쓰지 않는다" % a.freeze)
        tfs = a.tf.split(",") if a.tf else None
        data["freezes"].append(freeze(rows, a.freeze, tfs, a.rule, a.primary))
        with open(FROZEN, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        print("얼림 %s: %d개" % (a.freeze, len(data["freezes"][-1]["keys"])))
        return
    for fz in data["freezes"]:
        r = check(rows, fz, a.until)
        print("=== 얼린 날 %s — 표본 밖 %d일, 플러스 %d/%d (%.0f%%), 판정 %s ===" % (
            r["date"], r["oos_days"], r["plus"], r["total"], 100 * r["share"], r["verdict"]))
        if r["primary"]:
            p = r["primary"]
            print("  주 후보 %s: 표본 밖 %+.3f%% (%d/%d일 플러스, %d건) → %s" % (
                p["key"], p["oos_nt"], p["oos_pos"], p["oos_days"], p["oos_n"],
                "대기" if r["verdict"] == "WAIT" else ("통과" if r["primary_ok"] else "미달")))
        for o in sorted(r["rows"], key=lambda o: o["oos_nt"], reverse=True):
            print("  %-36s 고른 날까지 %+8.3f%% | 표본 밖 %+8.3f%% (%d/%d일, %d건)" % (
                o["key"], o["in_nt"], o["oos_nt"], o["oos_pos"], o["oos_days"], o["oos_n"]))


if __name__ == "__main__":
    main()
