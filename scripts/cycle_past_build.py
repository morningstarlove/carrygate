# -*- coding: utf-8 -*-
"""data/cycle_past.json 생성 — 과거 BTC 사이클(Bitstamp 일봉 종가)의 '최고가 뒤 n일째 낙폭' 표 (제안함 P36, 참고 기록).

  python scripts/cycle_past_build.py docs/cards/data/bitstamp_btcusd_1d_all.csv

사이클 고점 = 아래 창 안의 최고 종가. 낙폭 = 종가 / 고점 − 1 (%), 고점 날 = 0일째, 최대 900일째까지.
"""
import csv, json, os, sys
from datetime import datetime, timezone

WINDOWS = {"2013": ("2013-01-01", "2014-06-30"), "2017": ("2017-01-01", "2018-06-30"), "2021": ("2021-01-01", "2022-06-30")}
MAX_DAY = 900


def main(path):
    rows = []
    with open(path, encoding="utf-8") as fp:
        for r in csv.DictReader(fp):
            rows.append((datetime.fromtimestamp(int(float(r["t"])), timezone.utc).strftime("%Y-%m-%d"), float(r["c"])))
    rows.sort()
    by_date = dict(rows)
    out = {"source": "Bitstamp BTC/USD 1D close (TradingView), " + os.path.basename(path), "cycles": {}}
    for name, (a, b) in WINDOWS.items():
        win = [(d, c) for d, c in rows if a <= d <= b]
        ath_d, ath_c = max(win, key=lambda x: x[1])
        t0 = datetime.strptime(ath_d, "%Y-%m-%d")
        dd, last = [], None
        for n in range(MAX_DAY + 1):
            d = datetime.fromtimestamp(t0.timestamp() + n * 86400 + 43200, timezone.utc).strftime("%Y-%m-%d")
            c = by_date.get(d, last)          # 빠진 날은 전날 종가
            last = c
            dd.append(round((c / ath_c - 1) * 100, 2))
        lo = min(dd)
        out["cycles"][name] = {"ath_date": ath_d, "ath_close": ath_c, "min_pct": lo, "min_day": dd.index(lo), "dd_by_day": dd}
    os.makedirs(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"), exist_ok=True)
    dst = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "cycle_past.json")
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write("\n")
    for k, v in out["cycles"].items():
        print(k, v["ath_date"], v["ath_close"], "day402=%s" % v["dd_by_day"][402], "day403=%s" % v["dd_by_day"][403],
              "min=%s@%d" % (v["min_pct"], v["min_day"]))


if __name__ == "__main__":
    main(sys.argv[1])
