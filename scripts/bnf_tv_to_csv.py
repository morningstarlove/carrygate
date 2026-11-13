"""TradingView get-ohlcv 결과(저장된 도구 출력 파일)를 data/bnf/<심볼>.csv.gz 로 바꾼다. 표준 라이브러리만.
사용: python scripts/bnf_tv_to_csv.py <도구출력파일> <EXCH:TICKER> [<도구출력파일2> ...]
여러 파일(과거로 넘긴 페이지)을 주면 날짜로 합치고 중복을 없앤다."""
import sys, os, json, gzip, datetime

def _objs(txt):
    """파일 안의 JSON 을 찾아 bars 를 가진 dict 를 돌려준다(감싼 형식도 처리)."""
    try:
        d = json.loads(txt)
    except Exception:
        i = txt.find('{"bars"'); d = json.loads(txt[i:]) if i >= 0 else None
    stack = [d]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            if "bars" in x: yield x
            else: stack.extend(x.values())
        elif isinstance(x, list): stack.extend(x)
        elif isinstance(x, str) and '"bars"' in x:
            try: stack.append(json.loads(x))
            except Exception: pass

def rows_of(path):
    out = []
    for d in _objs(open(path, encoding="utf-8").read()):
        b = d["bars"]
        if isinstance(b, dict):
            for i in range(len(b["t"])):
                out.append((b["t"][i], b["o"][i], b["h"][i], b["l"][i], b["c"][i], b["v"][i] if b.get("v") else 0))
        else:
            for r in b: out.append((r["t"], r["o"], r["h"], r["l"], r["c"], r.get("v", 0)))
    return out

def main():
    sym = sys.argv[2]; files = [sys.argv[1]] + sys.argv[3:]
    seen = {}
    for f in files:
        for r in rows_of(f): seen[r[0]] = r
    rows = [seen[k] for k in sorted(seen)]
    if not rows: sys.exit("bars 없음: %s" % files)
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "data", "bnf", sym.replace(":", "_") + ".csv.gz")
    with gzip.open(path, "wt", encoding="utf-8", newline="") as g:
        g.write("date,o,h,l,c,v\n")
        for t, o, h, l, c, v in rows:
            d = datetime.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d")
            g.write("%s,%s,%s,%s,%s,%s\n" % (d, o, h, l, c, v))
    print(sym, len(rows), datetime.datetime.utcfromtimestamp(rows[0][0]).date(), datetime.datetime.utcfromtimestamp(rows[-1][0]).date())

if __name__ == "__main__":
    main()
