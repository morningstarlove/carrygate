# -*- coding: utf-8 -*-
"""
CARRYGATE — 신호등(게이트) 브리지
업비트 일봉 200개를 받아 SMA200 / EMA20 / 게이트 상태를 계산하고 status.json 으로 저장한다.
주문 기능 없음. 읽기 전용 공개 API만 사용한다. API 키 불필요.
"""
import json, time, sys, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-TRX", "KRW-LINK", "KRW-DOGE"]
KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (compatible; carrygate/1.0)", "Accept": "application/json"}


def http_json(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:          # 네트워크·레이트리밋 모두 여기로
            last = e
            time.sleep(2 + i * 3)
    raise RuntimeError("fetch failed %s : %s" % (url, last))


def sma(vals, n):
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / float(n)


def ema(vals, n):
    """SMA(n) 시드 후 재귀식. alpha = 2/(n+1)"""
    if len(vals) < n:
        return None
    k = 2.0 / (n + 1)
    e = sum(vals[:n]) / float(n)
    for v in vals[n:]:
        e = v * k + e * (1 - k)
    return e


def drawdown_90d(closes):
    """최근 90일 고점 대비 현재 낙폭"""
    w = closes[-90:] if len(closes) >= 90 else closes
    peak = max(w)
    return closes[-1] / peak - 1.0 if peak else 0.0


def analyse(market):
    url = ("https://api.upbit.com/v1/candles/days"
           "?market=%s&count=200" % market)
    rows = http_json(url)
    if not isinstance(rows, list) or len(rows) < 60:
        raise RuntimeError("bad payload for %s (len=%s)" % (market, len(rows) if hasattr(rows, '__len__') else '?'))
    rows = list(reversed(rows))                       # 과거 -> 현재
    closes = [float(r["trade_price"]) for r in rows]

    close = closes[-1]
    prev = closes[-2] if len(closes) >= 2 else close
    s200 = sma(closes, 200)
    e20 = ema(closes, 20)
    dd90 = drawdown_90d(closes)

    gap = (close / s200 - 1.0) if s200 else None
    gate_regime = bool(s200 and close > s200)          # 200일선 레짐
    crash = bool(dd90 < -0.30 and e20 and close < e20)  # 급락 조건

    return {
        "market": market,
        "close": round(close, 4),
        "prev_close": round(prev, 4),
        "change_pct": round((close / prev - 1.0) * 100, 4) if prev else None,
        "sma200": round(s200, 4) if s200 else None,
        "sma200_gap_pct": round(gap * 100, 4) if gap is not None else None,
        "ema20": round(e20, 4) if e20 else None,
        "ema20_gap_pct": round((close / e20 - 1.0) * 100, 4) if e20 else None,
        "drawdown_90d_pct": round(dd90 * 100, 4),
        "candles": len(closes),
        "gate_regime_open": gate_regime,
        "crash_flag": crash,
        "gate": "열림" if (gate_regime and not crash) else "닫힘",
        "last_candle_kst": rows[-1].get("candle_date_time_kst"),
    }


def main():
    now = datetime.now(KST)
    out = {
        "schema": "carrygate-gate/1",
        "date": now.strftime("%Y-%m-%d"),
        "generated_at_kst": now.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "upbit /v1/candles/days count=200",
        "coins": {},
        "errors": {},
    }
    for m in MARKETS:
        try:
            out["coins"][m] = analyse(m)
        except Exception as e:
            out["errors"][m] = str(e)[:300]
        time.sleep(0.4)                                # 레이트리밋 여유

    btc = out["coins"].get("KRW-BTC")
    opens = [c for c in out["coins"].values() if c["gate"] == "열림"]
    out["summary"] = {
        "btc_gate": btc["gate"] if btc else "미상",
        "btc_close": btc["close"] if btc else None,
        "btc_sma200_gap_pct": btc["sma200_gap_pct"] if btc else None,
        "breadth_open": len(opens),
        "breadth_total": len(out["coins"]),
        "verdict": ("전량 현금 유지" if (btc and btc["gate"] == "닫힘")
                    else ("게이트 열림 — 확인 필요" if btc else "데이터 부족 — 판정 불가")),
        "ok": len(out["errors"]) == 0,
    }

    with open("status.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    # 히스토리 누적 (한 줄 = 하루)
    line = json.dumps({"date": out["date"], "summary": out["summary"]}, ensure_ascii=False)
    with open("history.jsonl", "a", encoding="utf-8") as f:
        f.write(line + "\n")

    print(json.dumps(out["summary"], ensure_ascii=False, indent=2))
    if out["errors"]:
        print("ERRORS:", json.dumps(out["errors"], ensure_ascii=False), file=sys.stderr)
    # 일부 실패해도 BTC만 있으면 성공 처리 (워크플로가 죽지 않게)
    return 0 if btc else 1


if __name__ == "__main__":
    sys.exit(main())
