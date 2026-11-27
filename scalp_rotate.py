# -*- coding: utf-8 -*-
"""
CARRYGATE — 초단타 국면 교대 규칙 (제안함 P30, 2026-11-07 추가)

"전날 이긴 쪽을 오늘 쓴다." 매일 쌓이는 scalp_history.jsonl 의 18개 시장 합계로
  되돌림 묶음 = 30·60분봉 × {VWAP 되돌림 지정가, 평균회귀 지정가}
  돌파 묶음   = 30·60분봉 × {20봉 돌파}
를 비교해, 전날 합계가 큰 쪽(같으면 되돌림)의 '오늘' 합계를 이 규칙의 성적으로 기록한다.
새 숫자 문턱은 없다. 기존 규칙 결과를 다시 쓰기만 하므로 주문·수집이 없다.

순방향: FORWARD_FROM 평가일부터 센다. 그 전은 규칙을 만들 때 본 기간(사후)이다.
  2026-11-06 보고에서 평가일 10/13~11/5 로 +101%(되돌림만 −65%, 돌파만 −142%)를 보고 만든 규칙이라
  평가일 11/06 부터가 진짜 시험이다.

사용법
  python scalp_rotate.py            # scalp_rotate.json 저장 + 요약 출력
  python scalp_rotate.py --report   # 저장 없이 요약만
"""
import json, sys, argparse

RULE_FIXED = "2026-11-07"
FORWARD_FROM = "2026-11-06"     # 이 평가일부터 순방향
FAMILIES = {
    "rev": {"tfs": ("30m", "60m"), "strategies": ("vwap_fade_lim", "mr_z20_lim")},
    "brk": {"tfs": ("30m", "60m"), "strategies": ("brk_20",)},
}
MIN_FORWARD_DAYS = 14          # 첫 판단까지 순방향 일수 (판단은 사람이 이 문서 기준으로 한다)

# 보조 기록(제안함 P34, 2026-11-08 추가): 60분봉만으로 같은 교대를 돌린 것과 '60분 되돌림 고정'을 나란히 센다.
# 11/8 보고에서 10/13~11/7 사후로 "60분만 교대 < 60분 되돌림 고정" 이 보여 세운 가설. P30 계산은 건드리지 않는다. 새 문턱 없음.
AUX_FIXED = "2026-11-08"
AUX_FORWARD_FROM = "2026-11-08"     # 이 평가일부터 순방향 (규칙은 평가일 11/7 까지 보고 만듦)
FAMILIES.update({
    "rev_off": {"tfs": ("30m", "60m"), "strategies": ("vwap_fade_lim", "mr_z20_lim"), "venues": ("okx", "hyperliquid")},
    "rev60": {"tfs": ("60m",), "strategies": ("vwap_fade_lim", "mr_z20_lim")},
    "brk60": {"tfs": ("60m",), "strategies": ("brk_20",)},
})

# 보조 기록 2(제안함 P35, 2026-11-13 추가): "되돌림은 전날 되돌림이 진 날에만 쓴다."
# 전날 30·60분 되돌림 묶음 합이 0 미만(부호만, 새 숫자 문턱 없음)이면 오늘 되돌림 묶음 합을 성적으로, 아니면 쉰다(0).
# 11/10 보고에서 평가일 10/13~11/9 사후로 세운 가설. 11/10·11/11 두 날은 제안 뒤 확인했지만 규칙 고정 전이라 사후로 둔다.
# 순방향은 평가일 2026-11-12 부터(11/12 결과를 보기 전에 규칙을 고정). P30·P34 계산은 건드리지 않는다.
AUX2_FIXED = "2026-11-13"
AUX2_FORWARD_FROM = "2026-11-12"

# 보조 기록 3(제안함 P39, 2026-11-18 추가): P35 를 해외 선물(OKX·하이퍼리퀴드) 12시장만으로.
# 11/18 보고에서 P35 쓰는 날(사후 12일) 18시장 중 16곳 플러스, 약한 곳은 업비트(현물·매수만)였다 → 실거래 후보를 해외 선물로 좁힐 근거를 순방향으로 확인.
# 전날·오늘 모두 같은 12시장 합으로 본다(부호만, 새 숫자 문턱 없음). P35·P34·P30 계산은 그대로.
AUX3_FIXED = "2026-11-18"
AUX3_FORWARD_FROM = "2026-11-18"
OFFSHORE = ("okx", "hyperliquid")

# 보조 기록 4·5(제안함 P41, 2026-11-24 추가): '늦게 시작' — P35·P39 를 하루 UTC 2시간 늦게 시작한 경우.
# 전날 판단은 그대로(전날 하루 전체 합, UTC 0시에 이미 앎), 오늘 성적만 scalp.py 의 ntl(UTC 02시 이후 진입 매매 합)로 센다.
# 자동 실행이 늦게 도는 날이 잦아(예: 11/17 7시간 반) 실거래가 늦게 시작해도 이익이 남는지 본다. 새 문턱 없음(2시간은 scalp.LATE_START_SEC 로 고정).
# ntl 은 2026-11-24 평가일부터 기록되므로 사후 구간이 없다 — 순방향만.
AUX4_FIXED = "2026-11-24"
AUX4_FORWARD_FROM = "2026-11-24"


def load_history(path="scalp_history.jsonl"):
    rows = []
    with open(path, encoding="utf-8") as fp:
        for l in fp:
            l = l.strip()
            if l:
                try:
                    rows.append(json.loads(l))
                except ValueError:
                    pass
    rows.sort(key=lambda r: r.get("eval_day_utc") or r.get("date", ""))
    return rows


def family_sum(rec, fam, field="nt"):
    """묶음 합. field="ntl" 이면 늦게 시작(P41) 합 — 그 값이 없는 행(옛 기록)은 세지 않는다."""
    f = FAMILIES[fam]
    tot, n = 0.0, 0
    for k, v in (rec.get("rows") or {}).items():
        parts = k.split(":")
        if len(parts) != 4:
            continue
        venue, _, tf, st = parts
        if tf in f["tfs"] and st in f["strategies"] and (not f.get("venues") or venue in f["venues"]):
            val = v.get(field)
            if val is None:
                continue
            tot += val
            n += 1
    return (round(tot, 4), n)


def rotate(rows, rev_fam="rev", brk_fam="brk"):
    """[(평가일, 고른 묶음, 그날 성적, 되돌림 합, 돌파 합)] — 첫날은 전날이 없어 빠진다.
    30·60분봉 행이 없는 날(10/13 전)은 묶음 합이 0개 행이라 건너뛴다.
    rev_fam/brk_fam 을 "rev60"/"brk60" 으로 주면 60분봉만의 교대(P34 보조 기록)."""
    days = []
    for r in rows:
        rev, nr = family_sum(r, rev_fam)
        brk, nb = family_sum(r, brk_fam)
        if nr == 0 or nb == 0:
            continue
        days.append((r.get("eval_day_utc") or r.get("date"), rev, brk))
    out = []
    for i in range(1, len(days)):
        prev_rev, prev_brk = days[i - 1][1], days[i - 1][2]
        pick = "rev" if prev_rev >= prev_brk else "brk"
        day, rev, brk = days[i]
        out.append({"eval_day": day, "pick": pick, "net_pct": rev if pick == "rev" else brk,
                    "rev_pct": rev, "brk_pct": brk})
    nxt = None
    if days:
        nxt = "rev" if days[-1][1] >= days[-1][2] else "brk"
    return out, nxt


def rev_after_loss(rows, fam="rev"):
    """P35: 전날 되돌림 묶음 합 < 0 인 날만 오늘 되돌림을 쓴다. [{eval_day, active, net_pct, rev_pct, prev_rev_pct}], 다음날 쓸지."""
    days = []
    for r in rows:
        rev, nr = family_sum(r, fam)
        if nr == 0:
            continue
        days.append((r.get("eval_day_utc") or r.get("date"), rev))
    out = []
    for i in range(1, len(days)):
        prev = days[i - 1][1]
        day, rev = days[i]
        active = prev < 0
        out.append({"eval_day": day, "active": active, "net_pct": rev if active else 0.0, "rev_pct": rev, "prev_rev_pct": prev})
    nxt = (days[-1][1] < 0) if days else None
    return out, nxt


def rev_after_loss_late(rows, fam="rev"):
    """P41: P35 와 같은 판단(전날 fam 의 nt 합 < 0)이지만 오늘 성적은 ntl(UTC 02시 이후 진입) 합. ntl 없는 날은 뺀다."""
    days = []
    for r in rows:
        rev, nr = family_sum(r, fam)
        if nr == 0:
            continue
        late, nl = family_sum(r, fam, "ntl")
        days.append((r.get("eval_day_utc") or r.get("date"), rev, late if nl else None))
    out = []
    for i in range(1, len(days)):
        day, rev, late = days[i]
        if late is None:
            continue
        active = days[i - 1][1] < 0
        out.append({"eval_day": day, "active": active, "net_pct": late if active else 0.0, "rev_pct": late,
                    "rev_full_pct": rev, "prev_rev_pct": days[i - 1][1]})
    return out


def summarize_after_loss(seq):
    if not seq:
        return {"days": 0}
    act = [d for d in seq if d["active"]]
    return {"days": len(seq), "active_days": len(act), "net_pct": round(sum(d["net_pct"] for d in act), 3),
            "plus_days": sum(1 for d in act if d["net_pct"] > 0),
            "rev_only_pct": round(sum(d["rev_pct"] for d in seq), 3),
            "skipped_rev_pct": round(sum(d["rev_pct"] for d in seq if not d["active"]), 3)}


def summarize(seq):
    if not seq:
        return {"days": 0}
    nets = [d["net_pct"] for d in seq]
    return {"days": len(seq), "net_pct": round(sum(nets), 3),
            "plus_days": sum(1 for x in nets if x > 0),
            "rev_only_pct": round(sum(d["rev_pct"] for d in seq), 3),
            "brk_only_pct": round(sum(d["brk_pct"] for d in seq), 3),
            "picked_rev": sum(1 for d in seq if d["pick"] == "rev")}


def build(rows):
    seq, nxt = rotate(rows)
    back = [d for d in seq if d["eval_day"] < FORWARD_FROM]
    fwd = [d for d in seq if d["eval_day"] >= FORWARD_FROM]
    seq60, nxt60 = rotate(rows, "rev60", "brk60")
    back60 = [d for d in seq60 if d["eval_day"] < AUX_FORWARD_FROM]
    fwd60 = [d for d in seq60 if d["eval_day"] >= AUX_FORWARD_FROM]
    aux = {"fixed": AUX_FIXED, "forward_from": AUX_FORWARD_FROM,
           "method": "P34 보조: 60분봉만(되돌림=60분 VWAP 지정가+평균회귀 지정가, 돌파=60분 20봉 돌파) 같은 교대. rev_only_pct 가 '60분 되돌림 고정'",
           "next_pick": nxt60, "backtest": summarize(back60), "forward": summarize(fwd60), "forward_days": fwd60[-30:]}
    seqL, nxtL = rev_after_loss(rows)
    aux2 = {"fixed": AUX2_FIXED, "forward_from": AUX2_FORWARD_FROM,
            "method": "P35 보조: 전날 30·60분 되돌림 묶음 합 < 0 인 날만 오늘 되돌림 묶음을 쓴다(아니면 쉼). skipped_rev_pct = 쉰 날 되돌림이 냈을 합",
            "next_active": nxtL,
            "backtest": summarize_after_loss([d for d in seqL if d["eval_day"] < AUX2_FORWARD_FROM]),
            "forward": summarize_after_loss([d for d in seqL if d["eval_day"] >= AUX2_FORWARD_FROM]),
            "forward_days": [d for d in seqL if d["eval_day"] >= AUX2_FORWARD_FROM][-30:]}
    seqO, nxtO = rev_after_loss(rows, "rev_off")
    aux3 = {"fixed": AUX3_FIXED, "forward_from": AUX3_FORWARD_FROM, "venues": list(OFFSHORE),
            "method": "P39 보조: P35 를 OKX·하이퍼리퀴드 12시장 합만으로(전날 12시장 되돌림 합 < 0 이면 오늘 12시장 되돌림을 씀)",
            "next_active": nxtO,
            "backtest": summarize_after_loss([d for d in seqO if d["eval_day"] < AUX3_FORWARD_FROM]),
            "forward": summarize_after_loss([d for d in seqO if d["eval_day"] >= AUX3_FORWARD_FROM]),
            "forward_days": [d for d in seqO if d["eval_day"] >= AUX3_FORWARD_FROM][-30:]}
    late = {}
    for key, fam, label in (("all", "rev", "P35(18시장)"), ("offshore", "rev_off", "P39(OKX·하이퍼리퀴드 12시장)")):
        sq = [d for d in rev_after_loss_late(rows, fam) if d["eval_day"] >= AUX4_FORWARD_FROM]
        sm = summarize_after_loss(sq)
        sm["full_day_net_pct"] = round(sum(d["rev_full_pct"] for d in sq if d["active"]), 3)
        late[key] = {"label": label, "forward": sm, "forward_days": sq[-30:]}
    aux4 = {"fixed": AUX4_FIXED, "forward_from": AUX4_FORWARD_FROM, "late_start_sec": 7200,
            "method": "P41 보조: P35·P39 를 하루 UTC 2시간 늦게 시작(그 전 진입 매매 제외). 전날 판단은 그대로. full_day_net_pct = 같은 날 하루 전체로 셌다면", "late": late}
    return {"schema": "carrygate-scalp-rotate/5", "aux_late": aux4, "rule_fixed": RULE_FIXED, "forward_from": FORWARD_FROM, "aux60": aux, "aux_after_loss": aux2,
            "aux_after_loss_offshore": aux3,
            "method": "전날 18시장 합계가 큰 묶음(되돌림=30·60분 VWAP 지정가+평균회귀 지정가, 돌파=30·60분 20봉 돌파; 같으면 되돌림)의 오늘 합계",
            "date": rows[-1].get("date") if rows else None,
            "next_pick": nxt,
            "backtest": summarize(back), "forward": summarize(fwd),
            "forward_days": fwd[-30:],
            "min_forward_days": MIN_FORWARD_DAYS}


def print_summary(out):
    b, f = out["backtest"], out["forward"]
    print("=== 초단타 국면 교대 (P30: 전날 이긴 묶음을 오늘) ===")
    print("사후(만들 때 본 기간): %s일  교대 %+.1f%%  되돌림만 %+.1f%%  돌파만 %+.1f%%  플러스일 %s"
          % (b.get("days", 0), b.get("net_pct", 0), b.get("rev_only_pct", 0), b.get("brk_only_pct", 0), b.get("plus_days", "-")))
    print("순방향(평가일 %s~): %s일  교대 %+.1f%%  되돌림만 %+.1f%%  돌파만 %+.1f%%  플러스일 %s"
          % (out["forward_from"], f.get("days", 0), f.get("net_pct", 0), f.get("rev_only_pct", 0), f.get("brk_only_pct", 0), f.get("plus_days", "-")))
    for d in out["forward_days"][-7:]:
        print("  %s  고름 %-3s  %+7.2f%%  (되돌림 %+.1f / 돌파 %+.1f)" % (d["eval_day"], d["pick"], d["net_pct"], d["rev_pct"], d["brk_pct"]))
    print("내일 고를 묶음: %s" % ({"rev": "되돌림", "brk": "돌파"}.get(out["next_pick"], "-")))
    if f.get("days", 0) < MIN_FORWARD_DAYS:
        print("순방향 %d일 — %d일 전에는 판단하지 않는다." % (f.get("days", 0), MIN_FORWARD_DAYS))
    a = out.get("aux60")
    if a:
        ab, af = a["backtest"], a["forward"]
        print("--- P34 보조: 60분봉만 (순방향 평가일 %s~) ---" % a["forward_from"])
        print("사후 %s일: 60분만 교대 %+.1f%%  60분 되돌림 고정 %+.1f%%  60분 돌파만 %+.1f%%  교대 플러스일 %s"
              % (ab.get("days", 0), ab.get("net_pct", 0), ab.get("rev_only_pct", 0), ab.get("brk_only_pct", 0), ab.get("plus_days", "-")))
        print("순방향 %s일: 60분만 교대 %+.1f%%  60분 되돌림 고정 %+.1f%%  60분 돌파만 %+.1f%%  교대 플러스일 %s  내일 %s"
              % (af.get("days", 0), af.get("net_pct", 0), af.get("rev_only_pct", 0), af.get("brk_only_pct", 0), af.get("plus_days", "-"),
                 {"rev": "되돌림", "brk": "돌파"}.get(a["next_pick"], "-")))
    a2 = out.get("aux_after_loss")
    if a2:
        for label, x in (("사후", a2["backtest"]), ("순방향(평가일 %s~)" % a2["forward_from"], a2["forward"])):
            print("%s P35 전날 되돌림이 진 날만: %s일 중 %s일 씀  합 %+.1f%%  플러스 %s/%s  | 되돌림 고정 %+.1f%%  쉰 날 되돌림 %+.1f%%"
                  % ("---" if label == "사후" else "   ", x.get("days", 0), x.get("active_days", 0), x.get("net_pct", 0),
                     x.get("plus_days", 0), x.get("active_days", 0), x.get("rev_only_pct", 0), x.get("skipped_rev_pct", 0)) + "  [" + label + "]")
        print("    내일: %s" % ({True: "되돌림 씀(오늘 되돌림이 마이너스)", False: "쉼(오늘 되돌림이 플러스)"}.get(a2["next_active"], "-")))
    a3 = out.get("aux_after_loss_offshore")
    if a3:
        for label, x in (("사후", a3["backtest"]), ("순방향(평가일 %s~)" % a3["forward_from"], a3["forward"])):
            print("%s P39 같은 규칙, OKX·하이퍼리퀴드 12시장만: %s일 중 %s일 씀  합 %+.1f%%  플러스 %s/%s  | 12시장 되돌림 고정 %+.1f%%  쉰 날 %+.1f%%"
                  % ("---" if label == "사후" else "   ", x.get("days", 0), x.get("active_days", 0), x.get("net_pct", 0),
                     x.get("plus_days", 0), x.get("active_days", 0), x.get("rev_only_pct", 0), x.get("skipped_rev_pct", 0)) + "  [" + label + "]")
        print("    내일(12시장 기준): %s" % ({True: "씀", False: "쉼"}.get(a3["next_active"], "-")))
    a4 = out.get("aux_late")
    if a4:
        for k in ("all", "offshore"):
            x = a4["late"][k]["forward"]
            print("--- P41 늦게 시작(UTC 2시간 뒤) %s: %s일 중 %s일 씀  합 %+.1f%%  플러스 %s/%s  | 같은 날 하루 전체였다면 %+.1f%%  [순방향 평가일 %s~]"
                  % (a4["late"][k]["label"], x.get("days", 0), x.get("active_days", 0), x.get("net_pct", 0),
                     x.get("plus_days", 0), x.get("active_days", 0), x.get("full_day_net_pct", 0), a4["forward_from"]))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", default="scalp_history.jsonl")
    ap.add_argument("--out", default="scalp_rotate.json")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)
    try:
        rows = load_history(a.history)
    except IOError:
        print("기록 없음 — %s" % a.history)
        return 0
    out = build(rows)
    print_summary(out)
    if not a.report:
        with open(a.out, "w", encoding="utf-8") as fp:
            json.dump(out, fp, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
