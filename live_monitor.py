# -*- coding: utf-8 -*-
"""
CARRYGATE — 실전 캐리 감시 (A)

하이퍼리퀴드는 **지갑 주소만으로** 포지션·잔고·펀딩 수취·체결 내역을 공개 조회할 수 있다.
그래서 API 키 없이, 이 저장소의 원칙(주문 없음·키 없음)을 지키면서 소액 실전 계좌를 매일 기록한다.

  data/live_wallet.json  →  {"address": "0x...", "started_utc": "2026-10-03", "coin": "ETH",
                             "note": "소액 실전 1차 (ETH 현물 UETH + 선물 숏)"}
파일이 없으면 "실전 지갑 미설정"으로 조용히 끝난다.

기록하는 것
  - 현물 잔고(UETH 등)와 선물 숏 수량이 맞는가 (델타 중립 확인)
  - 시작 이후 받은 펀딩 합계(USDC), 낸 수수료 합계, 경과 일수 → 실현 연환산
  - funding.json 이 예측한 연환산과 비교 → 실현/예측 비율
  - 강제청산 가격까지 거리

사용법
  python live_monitor.py            # 조회·저장 (live.json, live_history.jsonl)
  python live_monitor.py --dry-run
  python live_monitor.py --report
"""
import json, sys, time, argparse, os
from datetime import datetime, timezone

from scalp import http_json, f, KST

WALLET_PATH = "data/live_wallet.json"
HL = "https://api.hyperliquid.xyz/info"
EXIT_APR = 2.0          # funding.py 와 같은 청산 문턱 (7일 평균 순 연 %)


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fp:
            return json.load(fp)
    except (IOError, ValueError):
        return default


def hl(payload):
    return http_json(HL, data=payload)


# ----------------------------------------------------------------- 조회

def perp_state(addr):
    d = hl({"type": "clearinghouseState", "user": addr})
    ms = d.get("marginSummary") or {}
    out = {"account_value_usdc": f(ms.get("accountValue")), "total_margin_used_usdc": f(ms.get("totalMarginUsed")),
           "withdrawable_usdc": f(d.get("withdrawable")), "positions": []}
    for p in d.get("assetPositions") or []:
        pos = p.get("position") or {}
        szi = f(pos.get("szi"))
        if not szi:
            continue
        lev = pos.get("leverage") or {}
        out["positions"].append({"coin": pos.get("coin"), "size": szi, "side": "숏" if szi < 0 else "롱",
                                 "entry_px": f(pos.get("entryPx")), "position_value_usdc": f(pos.get("positionValue")),
                                 "unrealized_pnl_usdc": f(pos.get("unrealizedPnl")), "liquidation_px": f(pos.get("liquidationPx")),
                                 "leverage": f(lev.get("value")), "margin_used_usdc": f(pos.get("marginUsed"))})
    return out


def spot_state(addr):
    d = hl({"type": "spotClearinghouseState", "user": addr})
    bal = {}
    for b in d.get("balances") or []:
        tot = f(b.get("total"))
        if tot:
            bal[b.get("coin")] = tot
    return bal


def funding_received(addr, start_ms):
    rows = hl({"type": "userFunding", "user": addr, "startTime": start_ms})
    out = []
    for r in rows or []:
        d = r.get("delta") or {}
        if d.get("type") != "funding":
            continue
        out.append({"t": int(f(r.get("time")) // 1000), "coin": d.get("coin"), "usdc": f(d.get("usdc")),
                    "rate": f(d.get("fundingRate")), "size": f(d.get("szi"))})
    return out


def fills(addr, start_ms):
    rows = hl({"type": "userFills", "user": addr})
    out = []
    for r in rows or []:
        t = f(r.get("time"))
        if t is None or t < start_ms:
            continue
        out.append({"t": int(t // 1000), "coin": r.get("coin"), "side": r.get("side"), "px": f(r.get("px")),
                    "sz": f(r.get("sz")), "fee": f(r.get("fee")), "crossed": r.get("crossed")})
    return out


def mid_prices():
    d = hl({"type": "allMids"})
    return {k: f(v) for k, v in (d or {}).items()}


# ----------------------------------------------------------------- 계산

def spot_symbol_for(coin):
    return {"BTC": "UBTC", "ETH": "UETH"}.get(coin, coin)


def evaluate(cfg, perp, spot, fund, fl, mids, predicted_apr):
    coin = cfg.get("coin", "ETH")
    start = datetime.strptime(cfg["started_utc"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    days = max((datetime.now(timezone.utc) - start).total_seconds() / 86400.0, 1e-6)
    pos = next((p for p in perp["positions"] if p["coin"] == coin), None)
    spot_qty = spot.get(spot_symbol_for(coin), 0.0)
    perp_qty = -pos["size"] if pos else 0.0          # 숏이면 양수
    px = mids.get(coin) or (pos["entry_px"] if pos else None)
    notional = (abs(perp_qty) * px) if (px and perp_qty) else 0.0
    fund_coin = [x for x in fund if x["coin"] == coin]
    fund_sum = sum(x["usdc"] for x in fund_coin if x["usdc"] is not None)
    fee_sum = sum(x["fee"] for x in fl if x["fee"] is not None)
    realized_apr = (fund_sum / notional) * (365.0 / days) * 100.0 if notional else None
    net_apr = ((fund_sum - fee_sum) / notional) * (365.0 / days) * 100.0 if notional else None
    delta_pct = ((spot_qty - perp_qty) / perp_qty * 100.0) if perp_qty else None
    liq_dist = ((pos["liquidation_px"] / px - 1.0) * 100.0) if (pos and pos.get("liquidation_px") and px) else None
    warnings = []
    if pos is None:
        warnings.append("선물 포지션 없음")
    if perp_qty and abs(delta_pct or 0) > 5:
        warnings.append("현물·선물 수량 차이 %.1f%% — 델타 중립이 깨졌다" % delta_pct)
    if liq_dist is not None and liq_dist < 30:
        warnings.append("강제청산 가격까지 %.0f%% — 증거금을 더 넣어야 한다" % liq_dist)
    if predicted_apr is not None and predicted_apr < EXIT_APR:
        warnings.append("예측 순 연수익 %.1f%% < 청산 문턱 %.0f%% — 정리 검토" % (predicted_apr, EXIT_APR))
    return {"coin": coin, "days": round(days, 2), "price": px,
            "spot_qty": spot_qty, "perp_short_qty": perp_qty, "delta_mismatch_pct": round(delta_pct, 2) if delta_pct is not None else None,
            "notional_usdc": round(notional, 2),
            "funding_received_usdc": round(fund_sum, 4), "funding_events": len(fund_coin),
            "fees_paid_usdc": round(fee_sum, 4), "fills": len(fl),
            "realized_gross_apr_pct": round(realized_apr, 2) if realized_apr is not None else None,
            "realized_net_apr_pct": round(net_apr, 2) if net_apr is not None else None,
            "predicted_net_apr_pct": predicted_apr,
            "realized_vs_predicted": round(realized_apr / predicted_apr, 2) if (realized_apr is not None and predicted_apr) else None,
            "liquidation_px": pos["liquidation_px"] if pos else None,
            "liquidation_distance_pct": round(liq_dist, 1) if liq_dist is not None else None,
            "unrealized_pnl_usdc": pos["unrealized_pnl_usdc"] if pos else None,
            "account_value_usdc": perp["account_value_usdc"], "warnings": warnings}


def predicted_from_funding_json(coin):
    d = load_json("funding.json")
    try:
        for v in d["coins"][coin]["venues"]:
            if v["venue"] == "hyperliquid":
                return v.get("decision_apr_pct")
    except (KeyError, TypeError):
        return None


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


def report(path="live_history.jsonl"):
    rows = []
    try:
        with open(path, encoding="utf-8") as fp:
            rows = [json.loads(l) for l in fp if l.strip()]
    except IOError:
        pass
    rows.sort(key=lambda r: r.get("date", ""))
    if not rows:
        print("기록 없음 — 실전 지갑이 설정되고 live_monitor.py 가 돌면 쌓인다.")
        return 0
    print("=== 실전 캐리 일별 (%d일치) ===" % len(rows))
    print("%-12s %5s %10s %9s %9s %9s %8s %s" % ("날짜", "경과", "펀딩합계$", "수수료$", "실현순연%", "예측순연%", "청산거리", "경고"))
    for r in rows:
        s = r["status"]
        print("%-12s %5s %10s %9s %9s %9s %8s %s" % (r["date"], s["days"], s["funding_received_usdc"], s["fees_paid_usdc"],
                                                     s["realized_net_apr_pct"], s["predicted_net_apr_pct"],
                                                     "%s%%" % s["liquidation_distance_pct"] if s["liquidation_distance_pct"] is not None else "-",
                                                     "; ".join(s["warnings"]) or "-"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--wallet", default=WALLET_PATH)
    ap.add_argument("--out", default="live.json")
    ap.add_argument("--history", default="live_history.jsonl")
    a = ap.parse_args(argv)
    if a.report:
        return report(a.history)
    cfg = load_json(a.wallet)
    if not cfg or not cfg.get("address"):
        print("실전 지갑 미설정 (%s 없음) — 건너뜀" % a.wallet)
        return 0
    addr = cfg["address"]
    start_ms = int(datetime.strptime(cfg["started_utc"], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    errors = {}
    try:
        perp = perp_state(addr)
        spot = spot_state(addr)
        fund = funding_received(addr, start_ms)
        fl = fills(addr, start_ms)
        mids = mid_prices()
    except Exception as e:
        print("조회 실패:", str(e)[:200])
        return 1
    status = evaluate(cfg, perp, spot, fund, fl, mids, predicted_from_funding_json(cfg.get("coin", "ETH")))
    now = datetime.now(KST)
    out = {"schema": "carrygate-live/1", "date": now.strftime("%Y-%m-%d"), "generated_at_kst": now.strftime("%Y-%m-%d %H:%M:%S"),
           "wallet": {"address": addr, "started_utc": cfg["started_utc"], "coin": cfg.get("coin", "ETH"), "note": cfg.get("note")},
           "status": status, "perp": perp, "spot_balances": spot, "funding": fund[-50:], "fills": fl[-50:]}
    s = status
    print("=== 실전 캐리 감시 (%s, %s, %d일차) ===" % (out["date"], s["coin"], int(s["days"])))
    print("현물 %s %.6f  /  선물 숏 %.6f  /  수량 차이 %s%%" % (spot_symbol_for(s["coin"]), s["spot_qty"], s["perp_short_qty"], s["delta_mismatch_pct"]))
    print("명목 %.2f USDC  펀딩 수취 %.4f USDC (%d회)  수수료 %.4f USDC (%d체결)" % (s["notional_usdc"], s["funding_received_usdc"], s["funding_events"], s["fees_paid_usdc"], s["fills"]))
    print("실현 연환산: 총 %s%%  순 %s%%   /  예측(funding.json) 순 %s%%   /  실현÷예측 %s" % (s["realized_gross_apr_pct"], s["realized_net_apr_pct"], s["predicted_net_apr_pct"], s["realized_vs_predicted"]))
    print("강제청산가 %s (거리 %s%%)  미실현 %s USDC  계정가치 %s USDC" % (s["liquidation_px"], s["liquidation_distance_pct"], s["unrealized_pnl_usdc"], s["account_value_usdc"]))
    for w in s["warnings"]:
        print("경고:", w)
    if a.dry_run:
        print("\n(dry-run: 저장하지 않음)")
        return 0
    with open(a.out, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    append_history({"date": out["date"], "status": status}, a.history)
    return 0


if __name__ == "__main__":
    sys.exit(main())
