# -*- coding: utf-8 -*-
"""
data/etf/<TICKER>.csv 에 최신 일봉을 덧붙인다 (stooq 공개 CSV). 실패해도 기존 파일은 그대로 둔다.

기존 파일은 TradingView 에서 받은 20년치(분할 조정, 배당 미반영)이고, 여기서 덧붙이는 stooq 값도
같은 성격(분할 조정 가격)이다. 월말 종가로만 쓰므로 하루 이틀 지연은 문제가 되지 않는다.
"""
import csv, os, sys, io, urllib.request
from datetime import datetime, timezone

TICKERS = ["SPY", "EFA", "AGG", "BIL", "QQQ", "GLD", "TLT", "IEF"]
DATA_DIR = "data/etf"
UA = {"User-Agent": "Mozilla/5.0 (compatible; carrygate/1.0)"}


def fetch_stooq(ticker):
    url = "https://stooq.com/q/d/l/?s=%s.us&i=d" % ticker.lower()
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        text = r.read().decode("utf-8", "replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    out = []
    for r in rows:
        try:
            d = r["Date"]
            o, h, l, c = float(r["Open"]), float(r["High"]), float(r["Low"]), float(r["Close"])
            v = float(r.get("Volume") or 0)
        except (KeyError, ValueError, TypeError):
            continue
        out.append((d, o, h, l, c, v))
    return out


def update(ticker):
    path = os.path.join(DATA_DIR, "%s.csv" % ticker)
    if not os.path.exists(path):
        return "%s: 파일 없음" % ticker
    with open(path, encoding="utf-8") as fp:
        have = list(csv.DictReader(fp))
    last = have[-1]["date"] if have else "1900-01-01"
    rows = fetch_stooq(ticker)
    add = [r for r in rows if r[0] > last]
    if not add:
        return "%s: 최신 (%s)" % (ticker, last)
    with open(path, "a", encoding="utf-8", newline="") as fp:
        w = csv.writer(fp)
        for d, o, h, l, c, v in add:
            t = int(datetime.strptime(d, "%Y-%m-%d").replace(hour=14, minute=30, tzinfo=timezone.utc).timestamp())
            w.writerow([d, t, o, h, l, c, v])
    return "%s: %d일 추가 (%s -> %s)" % (ticker, len(add), last, add[-1][0])


def main():
    fails = 0
    for t in TICKERS:
        try:
            print(update(t))
        except Exception as e:
            fails += 1
            print("%s: 실패 %s" % (t, str(e)[:100]))
    return 0 if fails < len(TICKERS) else 1


if __name__ == "__main__":
    sys.exit(main())
