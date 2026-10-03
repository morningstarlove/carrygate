# -*- coding: utf-8 -*-
"""
data/etf/<TICKER>.csv 에 최신 일봉을 덧붙인다 (stooq 공개 CSV, 비어 있으면 야후 차트 API). 실패해도 기존 파일은 그대로 둔다.

기존 파일은 TradingView 에서 받은 20년치(분할 조정, 배당 미반영)이고, 여기서 덧붙이는 stooq 값도
같은 성격(분할 조정 가격)이다. 월말 종가로만 쓰므로 하루 이틀 지연은 문제가 되지 않는다.
"""
import csv, os, sys, io, json, time, urllib.request, urllib.parse
from datetime import datetime, timezone

TICKERS = ["SPY", "EFA", "AGG", "BIL", "QQQ", "GLD", "TLT", "IEF",
           "SOXL", "TQQQ"]   # SOXL/TQQQ 는 그리드(종사종팔) 백테스트용. 파일이 없으면 stooq 전체 이력으로 새로 만든다
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


def fetch_yahoo(ticker):
    """야후 차트 API (분할 조정 가격, 배당 미반영 — stooq 와 같은 성격). stooq 가 비었을 때 대신 쓴다."""
    # range=max 로 부르면 야후가 일봉 대신 월봉을 돌려준다(2026-10-03 실행 #33 에서 확인). 기간을 직접 지정한다.
    url = ("https://query2.finance.yahoo.com/v8/finance/chart/%s?period1=0&period2=%d&interval=1d&includePrePost=false"
           % (urllib.parse.quote(ticker), int(time.time())))
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    res = ((data.get("chart") or {}).get("result") or [None])[0]
    if not res:
        return []
    if (res.get("meta") or {}).get("dataGranularity", "1d") != "1d":
        return []      # 일봉이 아니면 쓰지 않는다 (월봉이 섞여 들어오면 월말 종가·그리드 계산이 다 틀어진다)
    ts = res.get("timestamp") or []
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    out = []
    for i, t in enumerate(ts):
        try:
            o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
            if None in (o, h, l, c):
                continue
            v = q.get("volume", [None] * len(ts))[i] or 0
        except (KeyError, IndexError, TypeError):
            continue
        d = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
        out.append((d, float(o), float(h), float(l), float(c), float(v)))
    return out


def fetch_rows(ticker):
    """stooq 먼저, 비어 있으면(일일 한도 초과 등) 야후. 어디서 받았는지도 돌려준다."""
    errors = []
    for name, fn in (("stooq", fetch_stooq), ("yahoo", fetch_yahoo)):
        try:
            rows = fn(ticker)
        except Exception as e:
            errors.append("%s %s" % (name, str(e)[:60]))
            continue
        if rows:
            return rows, name
        errors.append("%s 응답 비어 있음" % name)
    return [], "; ".join(errors)


def update(ticker):
    path = os.path.join(DATA_DIR, "%s.csv" % ticker)
    rows, source = fetch_rows(ticker)
    created = False
    if not rows:
        return "%s: 받은 자료 없음 (%s)" % (ticker, source)
    if not os.path.exists(path):
        # 새 티커: 받은 전체 이력(분할 조정)으로 파일을 만든다.
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fp:
            fp.write("date,t,o,h,l,c,v\n")
        have, created = [], True
    else:
        with open(path, encoding="utf-8") as fp:
            have = list(csv.DictReader(fp))
    last = have[-1]["date"] if have else "1900-01-01"
    add = [r for r in rows if r[0] > last]
    if not add:
        return "%s: 최신 (%s, %s 기준)" % (ticker, last, source)
    with open(path, "a", encoding="utf-8", newline="") as fp:
        w = csv.writer(fp)
        for d, o, h, l, c, v in add:
            t = int(datetime.strptime(d, "%Y-%m-%d").replace(hour=14, minute=30, tzinfo=timezone.utc).timestamp())
            w.writerow([d, t, o, h, l, c, v])
    if created:
        return "%s: 새로 생성 %d일 (%s -> %s, %s)" % (ticker, len(add), add[0][0], add[-1][0], source)
    return "%s: %d일 추가 (%s -> %s, %s)" % (ticker, len(add), last, add[-1][0], source)


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
