# -*- coding: utf-8 -*-
"""
CARRYGATE — 지수 편출 이벤트 기록 (제안함 P37, 단기 이벤트). "지수 펀드가 규칙대로 팔아야 하는 날 전후를 잰다."

왜 만들었나
  MSCI 가 2026-11-11(미국 장 마감 뒤) 분기 정기변경에서 Strategy(MSTR)를 MSCI USA·World·ACWI 에서 빼기로 확정했다
  (첫 '비영업 회사' 규칙 적용). 적용은 2026-11-30 장 마감 뒤 → 지수를 따라가는 펀드는 11/30 종가에 판다.
  알려진 경향(지수 편출 효과): 발표~적용일까지 눌리고, 적용 뒤 일부 되돌린다. 표본 1건이므로 판정 트랙이 아니라 기록이다.

규칙 (고정값, 사후 조정 금지 — 이벤트 추가는 EVENTS 에 추가만)
  비교       MSTR 는 비트코인에 크게 움직이므로 BTC 현물 ETF(IBIT)를 빼고 본다: 초과 = MSTR 수익 − IBIT 수익.
  A 적용 전   기준일(ref) 종가 → 적용일(effective) 종가. 가설: 초과 < 0 (강제 매도 압력). 기준일 = 규칙을 고정한 날(11/13) 종가.
  B 적용 뒤   적용일 종가에 MSTR 매수 + 같은 금액 IBIT 매도(종이), HOLD_DAYS=20 거래일 뒤 종가에 둘 다 정리.
              성적 = 초과 − 수수료(체결 4번 × 0.05%). 가설: 성적 > 0 (편출 뒤 되돌림).
  판단       B 가 끝나는 날(약 2026-12-29) 한 번. 1건이라 '가설 성립/불성립'만 적고 실거래 제안은 같은 종류 이벤트 5건 이상 쌓인 뒤.
  자료       야후 일봉(정규장, 수정주가). 키 없음. 주문 없음.

사용법
  python index_event.py                    # 수집·계산·저장 (index_event.json, index_event_history.jsonl)
  python index_event.py --dry-run
  python index_event.py --bars-dir docs/cards/data   # nasdaq_mstr_1d.csv·nasdaq_ibit_1d.csv 로 (인터넷 불필요)
"""
import os, sys, csv, json, argparse
from datetime import datetime, timezone

from common import KST, r2, load_history

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(HERE, "index_event.json")
HISTORY_PATH = os.path.join(HERE, "index_event_history.jsonl")

RULE_FIXED = "2026-11-13"
FEE_PCT = 0.05          # 체결 1번당 (미국 주식·ETF 보수적)
EVENTS = [
    {"id": "msci_mstr_2026_11", "desc": "MSCI USA·World·ACWI 에서 Strategy(MSTR) 편출 (2026-11 정기변경, 비영업 회사 규칙)",
     "ticker": "MSTR", "bench": "IBIT", "announce": "2026-11-11", "ref": "2026-11-13", "effective": "2026-11-30",
     "hold_days": 20},
]


def rules_dict():
    return {"fee_pct": FEE_PCT, "events": [dict(e) for e in EVENTS],
            "leg_a": "ref close -> effective close, excess = ticker - bench, hypothesis excess < 0",
            "leg_b": "long ticker / short bench at effective close, exit after hold_days trading days, minus 4 fills fee, hypothesis > 0"}


# ----------------------------------------------------------------- 자료

def yahoo_closes(symbol, days=120):
    import kr_ohlcv
    return {b["date"]: b["c"] for b in kr_ohlcv.yahoo_symbol(symbol, days)}


def csv_closes(path):
    out = {}
    with open(path, encoding="utf-8") as fp:
        for r in csv.DictReader(fp):
            out[datetime.fromtimestamp(int(float(r["t"])), timezone.utc).strftime("%Y-%m-%d")] = float(r["c"])
    return out


# ----------------------------------------------------------------- 계산 (순수 함수)

def close_on_or_before(closes, day):
    ds = [d for d in closes if d <= day]
    return (max(ds), closes[max(ds)]) if ds else (None, None)


def leg(ct, cb, d0, d1):
    a0, b0 = ct.get(d0), cb.get(d0)
    a1, b1 = ct.get(d1), cb.get(d1)
    if None in (a0, b0, a1, b1):
        return None
    rt, rb = a1 / a0 - 1, b1 / b0 - 1
    return {"from": d0, "to": d1, "ticker_pct": r2(rt * 100), "bench_pct": r2(rb * 100), "excess_pct": r2((rt - rb) * 100)}


def evaluate(ev, ct, cb):
    days = sorted(d for d in ct if d in cb)
    out = {"id": ev["id"], "desc": ev["desc"], "ticker": ev["ticker"], "bench": ev["bench"], "announce": ev["announce"],
           "ref": ev["ref"], "effective": ev["effective"], "last_date": days[-1] if days else None}
    # A: 기준일 → 적용일 (적용일 전이면 오늘까지, 진행 중)
    if ev["ref"] not in ct or ev["ref"] not in cb:
        out["leg_a"] = {"status": "기준일 종가 대기"}
    else:
        end = ev["effective"] if ev["effective"] in ct and ev["effective"] in cb else days[-1]
        a = leg(ct, cb, ev["ref"], end) or {}
        done = end == ev["effective"]
        a["status"] = "끝" if done else "진행 중"
        if done:
            a["hypothesis_holds"] = a["excess_pct"] < 0
        out["leg_a"] = a
    # B: 적용일 → +hold_days 거래일
    if ev["effective"] not in ct or ev["effective"] not in cb:
        out["leg_b"] = {"status": "적용일(%s) 종가 대기" % ev["effective"]}
    else:
        after = [d for d in days if d > ev["effective"]]
        end = after[ev["hold_days"] - 1] if len(after) >= ev["hold_days"] else (after[-1] if after else ev["effective"])
        b = leg(ct, cb, ev["effective"], end) or {}
        b["days_held"] = min(len(after), ev["hold_days"])
        b["fee_pct"] = r2(4 * FEE_PCT)
        b["net_pct"] = r2(b["excess_pct"] - 4 * FEE_PCT) if "excess_pct" in b else None
        done = len(after) >= ev["hold_days"]
        b["status"] = "끝" if done else "진행 중 (%d/%d 거래일)" % (b["days_held"], ev["hold_days"])
        if done:
            b["hypothesis_holds"] = b["net_pct"] > 0
        out["leg_b"] = b
    # 참고: 발표일 전날 → 기준일 (규칙 고정 전, 사후)
    prev = [d for d in days if d < ev["announce"]]
    if prev:
        p = leg(ct, cb, prev[-1], ev["ref"]) if ev["ref"] in ct else leg(ct, cb, prev[-1], days[-1])
        if p:
            p["note"] = "규칙 고정 전 구간 — 참고만"
            out["before_fix"] = p
    return out


def build(series):
    now = datetime.now(KST)
    out = {"date": now.strftime("%Y-%m-%d"), "generated_at_kst": now.strftime("%Y-%m-%d %H:%M"),
           "rules": dict(rules_dict(), rule_fixed=RULE_FIXED), "events": [], "errors": {}}
    for ev in EVENTS:
        try:
            ct, cb = series(ev["ticker"]), series(ev["bench"])
            out["events"].append(evaluate(ev, ct, cb))
        except Exception as e:
            out["errors"][ev["id"]] = "%s: %s" % (type(e).__name__, e)
    return out


def history_row(out):
    row = {"date": out["date"], "events": {}}
    for e in out["events"]:
        row["events"][e["id"]] = {"last_date": e["last_date"], "a_excess": e["leg_a"].get("excess_pct"),
                                  "a_status": e["leg_a"].get("status"), "b_net": e["leg_b"].get("net_pct"),
                                  "b_status": e["leg_b"].get("status")}
    return row


def write_history(row, path=HISTORY_PATH):
    rows = [r for r in load_history(path) if r.get("date") != row["date"]]
    rows.append(row)
    rows.sort(key=lambda r: r["date"])
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def print_summary(out):
    print("=== 지수 편출 이벤트 기록 (P37, 규칙 고정 %s) ===" % RULE_FIXED)
    for e in out["events"]:
        print("[%s] %s (자료 %s까지)" % (e["id"], e["desc"], e["last_date"]))
        for k, lbl in (("before_fix", "고정 전(참고)"), ("leg_a", "A 적용 전"), ("leg_b", "B 적용 뒤")):
            x = e.get(k)
            if x:
                print("  %-12s %s" % (lbl, json.dumps(x, ensure_ascii=False)))
    for k, v in out["errors"].items():
        print("  오류 %s: %s" % (k, v))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--bars-dir", help="nasdaq_<ticker>_1d.csv 가 있는 폴더")
    a = ap.parse_args(argv)
    if a.bars_dir:
        series = lambda s: csv_closes(os.path.join(a.bars_dir, "nasdaq_%s_1d.csv" % s.lower()))
    else:
        series = yahoo_closes
    out = build(series)
    print_summary(out)
    if not a.dry_run:
        with open(OUT_PATH, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        write_history(history_row(out))
    return 1 if out["errors"] and not out["events"] else 0


if __name__ == "__main__":
    sys.exit(main())
