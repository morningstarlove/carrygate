# -*- coding: utf-8 -*-
"""P43 — OKX 모의계좌 실행기(별도 저장소 carrygate-exec)의 하루 합계를 읽어 기록만 한다.

사용자 서버가 Caddy 로 내보내는 `exec_demo/latest.json`(하루 합계만, 키·주문번호 없음)을 읽어
`exec_demo_history.jsonl` 에 날짜별로 한 줄 추가하고, `exec_demo.json` 에 P43 판단용 누적을 쓴다.
주문 없음, 키 없음, 읽기만. 서버가 꺼져 있거나 아직 설치 전이면 아무것도 바꾸지 않는다.

합격선(설계서 docs/EXEC_DEMO_PLAN.md 3절, 2026-11-27 고정 — 결과 보고 고치지 않는다):
  모의 14일, 쓴 날만: ① 모의 합 > 0 ② 모의/종이(같은 날 OKX 24개 종이 합) ≥ 0.5 ③ 오류로 빠진 날 0.
  쓴 날 < 3일이면 7일 연장(1회).
"""
import argparse, json, os

from common import http_json

URL = os.environ.get("CARRYGATE_EXEC_URL", "http://158.247.206.213/carrygate/exec_demo/")
FIXED = "2026-11-27"
WINDOW_DAYS = 14
EXTEND_DAYS = 7
MIN_ACTIVE = 3
RATIO_MIN = 0.5
# 저장소에 남길 칸만(서버 파일에 다른 것이 있어도 버린다)
KEEP = ("day", "trades", "net_pct_sum", "net_usdt_sum", "maker_entries", "maker_exits",
        "paper_okx_net_pct_sum", "paper_okx_trades", "by_inst", "paper_by_inst")


def clean(rep):
    """서버 하루 결과 -> 기록 한 줄. 형식이 이상하면 None."""
    if not isinstance(rep, dict) or not isinstance(rep.get("day"), str) or len(rep["day"]) != 10:
        return None
    if rep.get("partial"):          # 설치 첫날처럼 하루 중간에 시작한 날은 세지 않는다(설계서: 설치 다음 UTC 날부터)
        return None
    row = {k: rep[k] for k in KEEP if k in rep}
    g = rep.get("gate") or {}
    row["gate_ok"] = bool(g.get("ok"))
    row["active"] = bool(g.get("use_p35"))
    row["gate_rev_all"] = g.get("rev_all")
    row["error"] = (not row["gate_ok"]) or ("paper_okx_net_pct_sum" not in rep)
    return row


def merge(hist, row):
    """같은 날은 한 번만. 새 줄이면 True."""
    if row is None or any(h.get("day") == row["day"] for h in hist):
        return False
    hist.append(row)
    hist.sort(key=lambda h: h["day"])
    return True


def summarize(hist):
    need = WINDOW_DAYS
    days = hist[:need]
    act = [h for h in days if h.get("active")]
    if len(days) >= WINDOW_DAYS and len(act) < MIN_ACTIVE:
        need = WINDOW_DAYS + EXTEND_DAYS
        days = hist[:need]
        act = [h for h in days if h.get("active")]
    demo = round(sum(h.get("net_pct_sum") or 0.0 for h in act), 4)
    paper = round(sum(h.get("paper_okx_net_pct_sum") or 0.0 for h in act), 4)
    ratio = round(demo / paper, 3) if paper > 0 else None
    errors = sum(1 for h in days if h.get("error"))
    s = {"days": len(days), "need_days": need, "active_days": len(act), "demo_net_pct": demo,
         "paper_okx_net_pct": paper, "ratio": ratio, "error_days": errors,
         "demo_usdt": round(sum(h.get("net_usdt_sum") or 0.0 for h in act), 2),
         "trades": sum(h.get("trades") or 0 for h in act),
         "paper_trades": sum(h.get("paper_okx_trades") or 0 for h in act)}
    if len(days) < need:
        s["verdict"] = "WAIT"
    else:
        ok = demo > 0 and ratio is not None and ratio >= RATIO_MIN and errors == 0 and len(act) >= MIN_ACTIVE
        s["verdict"] = "PASS" if ok else "FAIL"
    return s


def load_hist(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fp:
        return [json.loads(l) for l in fp if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", default="exec_demo_history.jsonl")
    ap.add_argument("--out", default="exec_demo.json")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    hist = load_hist(a.history)
    if not a.report:
        try:
            rep = http_json(URL.rstrip("/") + "/latest.json", tries=2)
        except Exception as e:
            print("모의 실행기 결과 없음(서버 꺼짐·설치 전): %s" % str(e)[:120])
            rep = None
        if merge(hist, clean(rep)):
            with open(a.history, "w", encoding="utf-8") as fp:
                for h in hist:
                    fp.write(json.dumps(h, ensure_ascii=False) + "\n")
            print("기록 추가: %s" % hist[-1]["day"])
    if not hist:
        print("P43 모의계좌: 아직 기록 없음(설치 대기)")
        return
    s = summarize(hist)
    out = {"fixed": FIXED, "first_day": hist[0]["day"], "last_day": hist[-1]["day"], "summary": s,
           "rule": "모의 14일 쓴 날: 모의 합 > 0 · 모의/종이 ≥ 0.5 · 오류로 빠진 날 0 (쓴 날 < 3 이면 7일 연장 1회)"}
    if not a.report:
        with open(a.out, "w", encoding="utf-8") as fp:
            json.dump(out, fp, ensure_ascii=False, indent=1)
    print("P43 모의계좌 %s~%s: %d/%d일, 쓴 날 %d — 모의 %+.1f%% vs 종이 %+.1f%% (비율 %s), 오류 %d일 → %s"
          % (out["first_day"], out["last_day"], s["days"], s["need_days"], s["active_days"], s["demo_net_pct"],
             s["paper_okx_net_pct"], s["ratio"], s["error_days"], s["verdict"]))


if __name__ == "__main__":
    main()
