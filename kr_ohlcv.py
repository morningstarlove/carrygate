# -*- coding: utf-8 -*-
"""
CARRYGATE — 한국 주식 일봉 수집 (공개 API, 키 없음, 표준 라이브러리만)

경로를 순서대로 시도한다. 성공한 첫 경로의 자료를 쓰고 어느 경로였는지 남긴다.
  1. 야후 파이낸스    https://query1.finance.yahoo.com/v8/finance/chart/CODE.KS|.KQ?range=..&interval=1d           (JSON, 수정주가, 정규장만)
  2. 네이버 stock API https://api.stock.naver.com/chart/domestic/item/CODE/day?startDateTime=..&endDateTime=..     (JSON)
  3. 네이버 fchart   https://fchart.stock.naver.com/sise.nhn?symbol=CODE&timeframe=day&count=N&requestType=0   (XML euc-kr)

왜 야후가 먼저인가 (2026-10-04 Actions 실행 #2 로 확인): 네이버 API 의 고가·종가에는 **시간외 거래(16~18시)** 가 섞인다.
리노공업 10/1 네이버 고가·종가 81,900 vs 야후·TradingView 81,400. 우리는 15:45 KST(정규장 마감 뒤, 시간외 전)에 판단하므로
정규장 값인 야후가 맞다. 네이버는 야후가 막힐 때의 예비.

봉 형식(모든 경로 공통): {"date": "YYYY-MM-DD", "o","h","l","c": float, "v": float(주), "value": float|None(원)}  오래된 것부터.

  python kr_ohlcv.py --probe 005930 058470        # 각 경로가 되는지 Actions 로그로 확인 (저장 안 함)
  python kr_ohlcv.py --save 058470 080220 043260 --days 160 --out tests/fixtures/kr/ohlcv   # CSV 로 저장 (시험 자료)
"""
import os, sys, csv, json, time, argparse, urllib.request, urllib.error, urllib.parse, xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
      "Accept": "*/*", "Referer": "https://finance.naver.com/"}
TIMEOUT = 20
PAUSE = 0.25          # 종목 사이 쉬는 시간(초). 공개 API 예절


def _get(url, tries=2):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = "HTTP %s" % e.code
            if e.code in (400, 403, 404, 422):
                break
        except Exception as e:
            last = e
        time.sleep(1 + i)
    raise RuntimeError("%s : %s" % (url[:90], last))


def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------- 경로별
def naver_fchart(code, days):
    raw = _get("https://fchart.stock.naver.com/sise.nhn?symbol=%s&timeframe=day&count=%d&requestType=0" % (code, days))
    # 응답이 <?xml encoding="euc-kr"?> 로 와서 ElementTree 가 바이트 그대로는 못 읽는다 — 문자열로 바꾸고 선언을 뗀다
    txt = raw.decode("euc-kr", "replace")
    if txt.startswith("<?xml"):
        txt = txt[txt.index("?>") + 2:]
    root = ET.fromstring(txt)
    out = []
    for it in root.iter("item"):
        p = (it.get("data") or "").split("|")
        if len(p) < 6 or not p[0]:
            continue
        o, h, l, c, v = (_f(x) for x in p[1:6])
        if None in (o, h, l, c, v) or c <= 0:
            continue
        out.append({"date": "%s-%s-%s" % (p[0][:4], p[0][4:6], p[0][6:8]), "o": o, "h": h, "l": l, "c": c, "v": v, "value": None})
    if not out:
        raise RuntimeError("fchart: 빈 응답")
    return out


def naver_api(code, days):
    end = datetime.now(timezone(timedelta(hours=9)))
    start = end - timedelta(days=int(days * 1.6) + 10)
    url = ("https://api.stock.naver.com/chart/domestic/item/%s/day?startDateTime=%s&endDateTime=%s"
           % (code, start.strftime("%Y%m%d") + "000000", end.strftime("%Y%m%d") + "000000"))
    d = json.loads(_get(url).decode("utf-8"))
    out = []
    for it in d if isinstance(d, list) else []:
        ld = str(it.get("localDate") or "")
        o, h, l, c, v = (_f(it.get(k)) for k in ("openPrice", "highPrice", "lowPrice", "closePrice", "accumulatedTradingVolume"))
        if len(ld) != 8 or None in (o, h, l, c, v) or c <= 0:
            continue
        out.append({"date": "%s-%s-%s" % (ld[:4], ld[4:6], ld[6:8]), "o": o, "h": h, "l": l, "c": c, "v": v,
                    "value": _f(it.get("accumulatedTradingValue"))})
    if not out:
        raise RuntimeError("naver api: 빈 응답")
    return out[-days:]


def yahoo_symbol(symbol, days):
    """야후 심볼 그대로 (지수 ^KS11, ^KQ11 등). 수정주가, 정규장."""
    rng = "%dd" % int(days * 1.6 + 10) if days < 600 else "%dy" % (days // 250 + 1)
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=1d" % (urllib.parse.quote(symbol), rng)
    d = json.loads(_get(url, tries=2).decode("utf-8"))
    res = (d.get("chart") or {}).get("result") or []
    if not res:
        raise RuntimeError("yahoo: result 없음")
    r0 = res[0]; ts = r0.get("timestamp") or []
    q = (r0.get("indicators") or {}).get("quote", [{}])[0]
    out = []
    for i, t in enumerate(ts):
        o, h, l, c, v = (_f((q.get(k) or [None] * len(ts))[i]) for k in ("open", "high", "low", "close", "volume"))
        if c is None or c <= 0:
            continue
        out.append({"date": datetime.fromtimestamp(t, timezone(timedelta(hours=9))).strftime("%Y-%m-%d"),
                    "o": o or c, "h": h or c, "l": l or c, "c": c, "v": v or 0.0, "value": None})
    out.sort(key=lambda b: b["date"])
    return out[-days:]


def yahoo(code, days):
    rng = "%dd" % int(days * 1.6 + 10) if days < 600 else "%dy" % (days // 250 + 1)
    last = None
    for suf in (".KS", ".KQ"):
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%s%s?range=%s&interval=1d" % (code, suf, rng)
        try:
            d = json.loads(_get(url, tries=1).decode("utf-8"))
            res = (d.get("chart") or {}).get("result") or []
            if not res:
                raise RuntimeError("yahoo: result 없음")
            r0 = res[0]
            ts = r0.get("timestamp") or []
            q = (r0.get("indicators") or {}).get("quote", [{}])[0]
            out = []
            for i, t in enumerate(ts):
                o, h, l, c, v = (_f((q.get(k) or [None] * len(ts))[i]) for k in ("open", "high", "low", "close", "volume"))
                if None in (o, h, l, c, v) or c <= 0:
                    continue
                out.append({"date": datetime.fromtimestamp(t, timezone(timedelta(hours=9))).strftime("%Y-%m-%d"),
                            "o": o, "h": h, "l": l, "c": c, "v": v, "value": None})
            if out:
                return out[-days:]
            last = "yahoo %s: 봉 없음" % suf
        except Exception as e:
            last = e
    raise RuntimeError(str(last))


SOURCES = [("yahoo", yahoo), ("naver_api", naver_api), ("naver_fchart", naver_fchart)]


def fetch_daily(code, days=160, sources=None, need_date=None):
    """(봉 목록, 출처 이름). 모두 실패하면 RuntimeError 에 경로별 사유.

    need_date 를 주면(YYYY-MM-DD) 첫 경로의 마지막 봉이 그 날짜보다 이르면 다음 경로에서 **빠진 날만** 받아 뒤에 붙인다.
    2026-10-06 확인: 야후는 코스닥(.KQ) 일봉이 하루 이상 늦다(10/6 15:45 KST 에 10/2 까지만). 네이버는 그날 봉이 있다.
    과거 봉은 야후(정규장만, 수정주가)를 그대로 쓰고, 야후가 아직 안 준 최근 며칠만 네이버로 메운다. 출처 이름은 "yahoo+naver_api" 처럼 남긴다."""
    errors = {}
    srcs = list(sources or SOURCES)
    for i, (name, fn) in enumerate(srcs):
        try:
            bars = fn(code, days)
            bars.sort(key=lambda b: b["date"])
        except Exception as e:
            errors[name] = str(e)[:160]
            continue
        if need_date and bars and bars[-1]["date"] < need_date:
            for name2, fn2 in srcs[i + 1:]:
                try:
                    extra = [b for b in fn2(code, 10) if b["date"] > bars[-1]["date"] and b["date"] <= need_date]
                except Exception as e:
                    errors[name2] = str(e)[:160]
                    continue
                if extra:
                    extra.sort(key=lambda b: b["date"])
                    return bars + extra, "%s+%s" % (name, name2)
        return bars, name
    raise RuntimeError("일봉 실패 %s: %s" % (code, json.dumps(errors, ensure_ascii=False)))


# ----------------------------------------------------------------- CSV
FIELDS = ["date", "o", "h", "l", "c", "v", "value"]


def save_csv(bars, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fp:
        w = csv.DictWriter(fp, fieldnames=FIELDS)
        w.writeheader()
        for b in bars:
            w.writerow({k: ("" if b.get(k) is None else b.get(k)) for k in FIELDS})


def load_csv(path):
    with open(path, encoding="utf-8") as fp:
        rows = list(csv.DictReader(fp))
    out = []
    for r in rows:
        out.append({"date": r["date"], "o": float(r["o"]), "h": float(r["h"]), "l": float(r["l"]), "c": float(r["c"]),
                    "v": float(r["v"]), "value": _f(r.get("value"))})
    out.sort(key=lambda b: b["date"])
    return out


# ----------------------------------------------------------------- 실행
def probe(codes, days=30):
    ok_any = False
    for code in codes:
        for name, fn in SOURCES:
            try:
                bars = fn(code, days)
                print("  %-12s %s  OK  %d봉  %s..%s  마지막 종가 %s" % (name, code, len(bars), bars[0]["date"], bars[-1]["date"], bars[-1]["c"]))
                ok_any = True
            except Exception as e:
                print("  %-12s %s  실패  %s" % (name, code, str(e)[:120]))
    return ok_any


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", nargs="+", metavar="CODE")
    ap.add_argument("--save", nargs="+", metavar="CODE")
    ap.add_argument("--days", type=int, default=160)
    ap.add_argument("--out", default="tests/fixtures/kr/ohlcv")
    a = ap.parse_args()
    if a.probe:
        print("일봉 경로 시험 (%s)" % datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
        return 0 if probe(a.probe) else 1
    if a.save:
        for code in a.save:
            bars, src = fetch_daily(code, a.days)
            path = os.path.join(a.out, code + ".csv")
            save_csv(bars, path)
            print("저장 %s  %d봉  출처=%s  %s..%s" % (path, len(bars), src, bars[0]["date"], bars[-1]["date"]))
            time.sleep(PAUSE)
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
