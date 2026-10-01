# -*- coding: utf-8 -*-
"""
CARRYGATE — 업비트 상장 공지 반응 연구 (⑥)

업비트가 "신규 거래지원" 공지를 내면 그 코인은 해외 선물 시장에서 즉시 급등하는 일이 잦다.
공지 시각을 기준으로 해외 선물(OKX·하이퍼리퀴드) 가격이 5·15·60·240분 뒤 얼마나 움직였는지,
60분 안 최고점(최대 상승폭)이 얼마였는지를 쌓는다. 드물지만 폭이 큰 이벤트라 수집부터 시작한다.

공지: 업비트 공지 API (api-manager.upbit.com). 미국 서버에서 막히면 그날은 건너뛴다.
주문 기능 없음. 읽기 전용 공개 API 만 쓴다.

사용법
  python listing.py            # 새 공지 수집·반응 계산·저장 (listing.json 누적)
  python listing.py --dry-run
  python listing.py --report
"""
import json, sys, time, re, argparse
from datetime import datetime, timezone

from scalp import http_json, f, KST

OUT_PATH = "listing.json"
PAGES = 3                 # 매일 확인할 공지 페이지 수 (20건/페이지). 첫 실행은 --pages 10
POST = (5, 15, 60, 240)
KEYWORDS = ("신규 거래지원", "거래지원 안내", "디지털 자산 추가", "원화 마켓 추가", "마켓 추가")
EXCLUDE = ("종료", "유의", "해제", "변경", "이벤트", "입출금", "지연")
TICKER_RE = re.compile(r"\(([A-Z0-9]{2,10}(?:\s*,\s*[A-Z0-9]{2,10})*)\)")


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def r2(x):
    return None if x is None else round(x, 2)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fp:
            return json.load(fp)
    except (IOError, ValueError):
        return default


# ----------------------------------------------------------------- 공지

def fetch_notices(pages):
    out = []
    for p in range(1, pages + 1):
        d = http_json("https://api-manager.upbit.com/api/v1/announcements?os=web&page=%d&per_page=20&category=trade" % p)
        data = d.get("data") or {}
        rows = data.get("notices") or data.get("list") or []
        if not rows:
            break
        out.extend(rows)
        time.sleep(0.3)
    return out


def parse_time(s):
    """'2026-09-30T13:00:00+09:00' 같은 문자열 -> UTC 초."""
    if not s:
        return None
    try:
        s = s.replace("Z", "+00:00")
        return int(datetime.fromisoformat(s).timestamp())
    except ValueError:
        return None


def parse_listings(notices):
    """공지 목록 -> [{id, title, ts, tickers}] 신규 거래지원 공지만."""
    out = []
    for n in notices:
        title = n.get("title") or ""
        if not any(k in title for k in KEYWORDS) or any(x in title for x in EXCLUDE):
            continue
        m = TICKER_RE.search(title)
        if not m:
            continue
        tickers = [t.strip() for t in m.group(1).split(",") if t.strip() and t.strip() not in ("KRW", "BTC", "USDT")]
        ts = parse_time(n.get("first_listed_at") or n.get("listed_at") or n.get("created_at"))
        if tickers and ts:
            out.append({"id": n.get("id"), "title": title, "ts": ts,
                        "time_utc": datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "tickers": tickers})
    return out


# ----------------------------------------------------------------- 가격 반응

def okx_1m(coin, start, end):
    out, after = {}, end * 1000
    for _ in range(6):
        d = http_json("https://www.okx.com/api/v5/market/history-candles?instId=%s-USDT-SWAP&bar=1m&limit=100&after=%d" % (coin, after))
        rows = d.get("data") or []
        if not rows:
            break
        for r in rows:
            t = int(r[0]) // 1000
            if start <= t < end:
                out[t] = (f(r[1]), f(r[2]), f(r[3]), f(r[4]))
        after = min(int(r[0]) for r in rows)
        if after // 1000 <= start:
            break
        time.sleep(0.12)
    return out


def hl_1m(coin, start, end):
    rows = http_json("https://api.hyperliquid.xyz/info",
                     data={"type": "candleSnapshot", "req": {"coin": coin, "interval": "1m",
                                                             "startTime": start * 1000, "endTime": end * 1000}})
    return {int(r["t"]) // 1000: (f(r["o"]), f(r["h"]), f(r["l"]), f(r["c"])) for r in rows}


def reaction(bars, ts):
    t0 = ts - ts % 60
    if t0 not in bars:
        return None
    p0 = bars[t0][0]
    out = {}
    for h in POST:
        if t0 + h * 60 in bars:
            out["post%d_pct" % h] = round((bars[t0 + h * 60][0] / p0 - 1.0) * 100.0, 3)
    hi = [bars[t][1] for t in range(t0, t0 + 3600, 60) if t in bars]
    if hi:
        out["maxup60_pct"] = round((max(hi) / p0 - 1.0) * 100.0, 3)
    return out if "post15_pct" in out else None


def process(listing, done):
    recs = []
    for tk in listing["tickers"]:
        for venue, fn in (("okx", okx_1m), ("hyperliquid", hl_1m)):
            key = (listing["id"], tk, venue)
            if key in done:
                continue
            try:
                bars = fn(tk, listing["ts"] - 600, listing["ts"] + 4 * 3600 + 120)
            except Exception:
                bars = {}
            r = reaction(bars, listing["ts"]) if bars else None
            rec = {"id": listing["id"], "title": listing["title"], "time_utc": listing["time_utc"], "ts": listing["ts"],
                   "ticker": tk, "venue": venue, "listed_abroad": bool(r)}
            if r:
                rec.update(r)
            recs.append(rec)
            time.sleep(0.15)
    return recs


def summarize(events):
    es = [e for e in events if e.get("listed_abroad")]
    by_venue = {}
    for v in sorted(set(e["venue"] for e in es)):
        vs = [e for e in es if e["venue"] == v]
        s = {"n": len(vs)}
        for h in POST:
            xs = [e["post%d_pct" % h] for e in vs if e.get("post%d_pct" % h) is not None]
            s["post%d_pct" % h] = r2(mean(xs))
            s["post%d_pos_pct" % h] = round(100.0 * sum(1 for x in xs if x > 0) / len(xs), 1) if xs else None
        s["maxup60_pct"] = r2(mean([e["maxup60_pct"] for e in vs if e.get("maxup60_pct") is not None]))
        by_venue[v] = s
    return {"notices": len(set(e["id"] for e in events)), "pairs": len(events), "with_price": len(es), "by_venue": by_venue}


def print_summary(s, latest):
    print("공지 %d건, 코인×거래소 %d쌍, 해외 선물 가격 있는 쌍 %d" % (s["notices"], s["pairs"], s["with_price"]))
    for v, x in s["by_venue"].items():
        print("%-12s n=%-3d 5분 %s%% (상승 %s%%)  15분 %s%%  60분 %s%%  240분 %s%%  60분내 최고 %s%%"
              % (v, x["n"], x["post5_pct"], x["post5_pos_pct"], x["post15_pct"], x["post60_pct"], x["post240_pct"], x["maxup60_pct"]))
    for e in latest[:5]:
        print("  %s %-6s %-11s 5분 %s%%  60분 %s%%  최고 %s%%" % (e["time_utc"][:16], e["ticker"], e["venue"],
                                                             e.get("post5_pct"), e.get("post60_pct"), e.get("maxup60_pct")))


def report(path=OUT_PATH):
    d = load_json(path, None)
    if not d or not d.get("events"):
        print("기록 없음 — listing.py 가 실행되면서 쌓인다.")
        return 0
    print("=== 업비트 상장 공지 반응 누적 ===")
    print_summary(d["summary"], [e for e in sorted(d["events"], key=lambda e: -e["ts"]) if e.get("listed_abroad")])
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--pages", type=int, default=PAGES)
    ap.add_argument("--out", default=OUT_PATH)
    a = ap.parse_args(argv)
    if a.report:
        return report(a.out)

    prev = load_json(a.out, {"events": []})
    done = set((e["id"], e["ticker"], e["venue"]) for e in prev.get("events", []))
    # 첫 실행이면 더 깊이 거슬러 올라간다
    pages = a.pages if prev.get("events") else max(a.pages, 10)
    errors, new, listings = {}, [], []
    try:
        notices = fetch_notices(pages)
        listings = parse_listings(notices)
    except Exception as e:
        errors["notices"] = str(e)[:200]
        notices = []
    now = time.time()
    for L in listings:
        if L["ts"] + 4 * 3600 + 300 > now:        # 240분 뒤 가격까지 있어야 한다
            continue
        new.extend(process(L, done))
    events = sorted(prev.get("events", []) + new, key=lambda e: (e["ts"], e["ticker"], e["venue"]))
    summary = summarize(events) if events else {"notices": 0, "pairs": 0, "with_price": 0, "by_venue": {}}
    now_kst = datetime.now(KST)
    out = {"schema": "carrygate-listing/1", "date": now_kst.strftime("%Y-%m-%d"),
           "generated_at_kst": now_kst.strftime("%Y-%m-%d %H:%M:%S"),
           "method": {"notice": "업비트 공지(category=trade) 중 제목에 %s 가 있고 %s 가 없는 것. 괄호 안 티커 추출" % (KEYWORDS, EXCLUDE),
                      "reaction": "공지 시각(분)의 해외 선물 시가 대비 %s분 뒤 시가 수익률(%%), 60분 내 최고가 상승폭" % (POST,)},
           "notices_seen": len(notices), "listings_seen": len(listings), "new_today": len(new),
           "events": events, "errors": errors, "summary": summary}
    print("=== 업비트 상장 공지 반응 (%s) — 공지 %d건 확인, 상장 공지 %d건, 새로 처리 %d쌍, 누적 %d쌍 ===" % (
        out["date"], len(notices), len(listings), len(new), len(events)))
    if events:
        print_summary(summary, [e for e in sorted(events, key=lambda e: -e["ts"]) if e.get("listed_abroad")])
    if errors:
        print("오류:", errors)
    if a.dry_run:
        print("\n(dry-run: 저장하지 않음)")
        return 0
    with open(a.out, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    return 0 if not errors.get("notices") else 1


if __name__ == "__main__":
    sys.exit(main())
