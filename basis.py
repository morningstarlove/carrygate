# -*- coding: utf-8 -*-
"""
CARRYGATE — 분기 선물 베이시스 캐리 (②)

만기가 있는 선물(분기물)은 현물보다 비싸게 거래되는 일이 많다. 현물을 사고 같은 수량의
선물을 팔아 만기까지 들고 가면, 그 가격차(베이시스)가 **진입 시점에 확정된 수익**이 된다.
펀딩 캐리(funding.py)는 이자가 매일 바뀌지만, 이건 만기까지 숫자가 고정된다는 점이 다르다.

거래소: OKX(분기 선물 USDT 마진), 게이트(델리버리 선물). 둘 다 GitHub Actions(미국 서버)에서 접속된다.
주문 기능 없음. 읽기 전용 공개 API 만 쓴다.

사용법
  python basis.py            # 수집·계산·저장 (basis.json, basis_history.jsonl)
  python basis.py --dry-run
  python basis.py --report   # 코인별 확정 연수익 추이
"""
import json, sys, time, argparse
from datetime import datetime, timezone

from scalp import http_json, f, KST, COINS

# 수수료 가정 (1회 체결 %, 보수적). 포지션 수명 동안 현물 매수·매도 2회, 선물 진입 1회, 만기 정산 1회.
SPOT_TAKER_PCT = 0.10
FUT_TAKER_PCT = 0.05
DELIVERY_FEE_PCT = 0.05      # 만기 정산 수수료 (OKX 공식 0.02% 안팎, 보수적으로)
ONE_OFF_FEE_PCT = 2 * SPOT_TAKER_PCT + FUT_TAKER_PCT + DELIVERY_FEE_PCT
MIN_DAYS = 7                 # 만기가 이보다 가까우면 연환산이 튄다 — 제외
ENTER_APR = 8.0              # funding.py 와 같은 문턱 (순 연 %)


def fee_apr(days):
    return ONE_OFF_FEE_PCT * 365.0 / days


def annualize(fut, spot, days):
    """선물가/현물가 - 1 을 만기까지 일수로 연환산 (%)."""
    return (fut / spot - 1.0) * 365.0 / days * 100.0


# ----------------------------------------------------------------- 수집

def src_okx(now):
    out = {}
    spot = {}
    for r in (http_json("https://www.okx.com/api/v5/market/tickers?instType=SPOT").get("data") or []):
        spot[r["instId"]] = f(r.get("last"))
    futs = http_json("https://www.okx.com/api/v5/market/tickers?instType=FUTURES").get("data") or []
    for c in COINS:
        s = spot.get("%s-USDT" % c)
        if not s:
            continue
        rows = []
        for r in futs:
            inst = r.get("instId", "")
            if not inst.startswith("%s-USDT-" % c):
                continue
            # 만기: instId 끝 6자리 YYMMDD, 08:00 UTC 정산
            try:
                exp = datetime.strptime(inst[-6:], "%y%m%d").replace(hour=8, tzinfo=timezone.utc)
            except ValueError:
                continue
            days = (exp.timestamp() - now) / 86400.0
            bid, ask, last = f(r.get("bidPx")), f(r.get("askPx")), f(r.get("last"))
            px = (bid + ask) / 2.0 if (bid and ask) else last
            if not px or days <= 0:
                continue
            rows.append(contract("okx", c, inst, exp, days, px, s, bid, ask))
        if rows:
            out[c] = rows
    return out


def src_gate(now):
    out = {}
    rows = http_json("https://api.gateio.ws/api/v4/delivery/usdt/contracts")
    spot = {}
    try:
        for r in http_json("https://api.gateio.ws/api/v4/spot/tickers"):
            spot[r["currency_pair"]] = f(r.get("last"))
    except Exception:
        pass
    for c in COINS:
        s = spot.get("%s_USDT" % c)
        if not s:
            continue
        got = []
        for r in rows:
            name = r.get("name", "")
            if not name.startswith("%s_USDT_" % c):
                continue
            exp_s = f(r.get("expire_time"))
            if not exp_s:
                continue
            exp = datetime.fromtimestamp(exp_s, timezone.utc)
            days = (exp_s - now) / 86400.0
            px = f(r.get("last_price")) or f(r.get("mark_price"))
            if not px or days <= 0:
                continue
            got.append(contract("gate", c, name, exp, days, px, s, None, None))
        if got:
            out[c] = got
    return out


def contract(venue, coin, inst, exp, days, fut, spot, bid, ask):
    gross = annualize(fut, spot, days)
    fa = fee_apr(days)
    return {"venue": venue, "coin": coin, "inst": inst, "expiry_utc": exp.strftime("%Y-%m-%d %H:%M"),
            "days": round(days, 2), "fut_px": fut, "spot_px": spot,
            "bid": bid, "ask": ask,
            "basis_pct": round((fut / spot - 1.0) * 100.0, 4),
            "gross_apr_pct": round(gross, 4), "fee_apr_pct": round(fa, 4),
            "net_apr_pct": round(gross - fa, 4), "fee_one_off_pct": ONE_OFF_FEE_PCT,
            "eligible": days >= MIN_DAYS}


SOURCES = [("okx", src_okx), ("gate", src_gate)]


# ----------------------------------------------------------------- 저장·보고

def read_funding_best():
    """funding.json 에서 코인별 펀딩 캐리 순 연수익(7일평균)을 읽어 비교에 쓴다."""
    try:
        with open("funding.json", encoding="utf-8") as fp:
            d = json.load(fp)
        return {c: v["best"]["decision_apr_pct"] for c, v in (d.get("coins") or {}).items()}
    except Exception:
        return {}


def append_history(rec, path):
    try:
        with open(path, encoding="utf-8") as fp:
            rows = [l for l in fp.read().splitlines() if l.strip()]
    except IOError:
        rows = []
    rows = [l for l in rows if json.loads(l).get("date") != rec["date"]]
    rows.append(json.dumps(rec, ensure_ascii=False))
    with open(path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(rows) + "\n")


def report(path="basis_history.jsonl"):
    rows = []
    try:
        with open(path, encoding="utf-8") as fp:
            rows = [json.loads(l) for l in fp if l.strip()]
    except IOError:
        pass
    rows.sort(key=lambda r: r.get("date", ""))
    if not rows:
        print("기록 없음 — basis.py 가 하루 한 번 실행되면서 쌓인다.")
        return 0
    coins = []
    for r in rows:
        for c in r.get("coins", {}):
            if c not in coins:
                coins.append(c)
    print("=== 날짜별 베이시스 캐리 확정 순 연수익 (%) — 코인별 최고 계약 ===")
    print("%-12s %s" % ("날짜", "".join("%9s" % c for c in coins)))
    for r in rows:
        print("%-12s %s" % (r["date"], "".join("%9s" % ("%.1f" % r["coins"][c]["net"] if c in r["coins"] else "-") for c in coins)))
    print()
    print("=== 펀딩 캐리(변동형) vs 베이시스 캐리(확정형), 최근 날 ===")
    last = rows[-1]
    for c in coins:
        v = last["coins"].get(c)
        if v:
            print("%-5s 베이시스 순 %6.2f%% (%s, 만기 %s일)  펀딩 순 %s"
                  % (c, v["net"], v["inst"], v["days"], "%.2f%%" % v["funding_net"] if v.get("funding_net") is not None else "-"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--out", default="basis.json")
    ap.add_argument("--history", default="basis_history.jsonl")
    a = ap.parse_args(argv)
    if a.report:
        return report(a.history)

    now = time.time()
    per_coin, ok, failed = {c: [] for c in COINS}, [], {}
    for name, fn in SOURCES:
        try:
            res = fn(now)
            for c, rows in res.items():
                per_coin[c].extend(rows)
            ok.append(name)
        except Exception as e:
            failed[name] = str(e)[:200]
        time.sleep(0.2)

    funding = read_funding_best()
    coins, best_all = {}, None
    for c in COINS:
        rows = sorted(per_coin[c], key=lambda r: r["net_apr_pct"], reverse=True)
        if not rows:
            continue
        elig = [r for r in rows if r["eligible"]]
        best = elig[0] if elig else None
        coins[c] = {"contracts": rows, "best": best, "funding_net_apr_pct": funding.get(c)}
        if best and (best_all is None or best["net_apr_pct"] > best_all["net_apr_pct"]):
            best_all = best

    if not coins:
        verdict, state = "데이터 없음", "미상"
    elif best_all is None:
        verdict, state = "만기 %d일 이상 남은 계약 없음" % MIN_DAYS, "대기"
    elif best_all["net_apr_pct"] >= ENTER_APR:
        state = "진입 가능"
        verdict = "%s %s 확정 순 연 %.1f%% (베이시스 %.2f%%, 만기 %.0f일) — 문턱(연 %.0f%%) 충족" % (
            best_all["coin"], best_all["inst"], best_all["net_apr_pct"], best_all["basis_pct"], best_all["days"], ENTER_APR)
    else:
        state = "대기"
        verdict = "최고 %s %s 확정 순 연 %.1f%% — 문턱(연 %.0f%%) 미달" % (
            best_all["coin"], best_all["inst"], best_all["net_apr_pct"], ENTER_APR)

    now_kst = datetime.now(KST)
    out = {"schema": "carrygate-basis/1", "date": now_kst.strftime("%Y-%m-%d"),
           "generated_at_kst": now_kst.strftime("%Y-%m-%d %H:%M:%S"),
           "strategy": "현물 매수 + 분기 선물 동일수량 숏, 만기까지 보유. 베이시스가 진입 시 확정 수익",
           "fee_assumption": {"spot_taker_pct": SPOT_TAKER_PCT, "fut_taker_pct": FUT_TAKER_PCT,
                              "delivery_fee_pct": DELIVERY_FEE_PCT, "one_off_total_pct": ONE_OFF_FEE_PCT,
                              "note": "1회성 비용을 만기까지 일수로 연환산해 뺀다. 만기가 가까울수록 연 부담이 커진다"},
           "min_days": MIN_DAYS, "enter_apr_pct": ENTER_APR,
           "coins": coins, "sources_ok": ok, "sources_failed": failed,
           "summary": {"state": state, "verdict": verdict,
                       "best_coin": best_all["coin"] if best_all else None,
                       "best_inst": best_all["inst"] if best_all else None,
                       "best_net_apr_pct": best_all["net_apr_pct"] if best_all else None}}

    print("=== 베이시스 캐리 (%s) ===" % out["date"])
    print("%-5s %-8s %-18s %6s %9s %9s %9s %9s  %s" % ("코인", "거래소", "계약", "만기일", "베이시스%", "총연%", "수수료연%", "순연%", "펀딩순연%"))
    for c, d in coins.items():
        for r in d["contracts"]:
            print("%-5s %-8s %-18s %6.0f %9.3f %9.2f %9.2f %9.2f  %s%s"
                  % (c, r["venue"], r["inst"], r["days"], r["basis_pct"], r["gross_apr_pct"], r["fee_apr_pct"], r["net_apr_pct"],
                     "%.2f" % d["funding_net_apr_pct"] if d["funding_net_apr_pct"] is not None else "-",
                     "" if r["eligible"] else "  (만기 임박, 제외)"))
    if failed:
        print("수집 실패:", failed)
    print("판정:", verdict)
    if a.dry_run:
        print("\n(dry-run: 저장하지 않음)")
        return 0
    with open(a.out, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    append_history({"date": out["date"], "summary": out["summary"],
                    "coins": {c: {"net": d["best"]["net_apr_pct"], "gross": d["best"]["gross_apr_pct"],
                                  "inst": d["best"]["inst"], "days": d["best"]["days"],
                                  "funding_net": d["funding_net_apr_pct"]}
                              for c, d in coins.items() if d["best"]}}, a.history)
    return 0 if coins else 1


if __name__ == "__main__":
    sys.exit(main())
