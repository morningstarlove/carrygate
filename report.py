# -*- coding: utf-8 -*-
"""
며칠치 기록을 표로 보여준다.

funding_history.jsonl 을 읽어 날짜별 이자율과, 코인별 안정성(최저/평균/최고)을
출력한다. "며칠 지켜보니 이자가 꾸준한가"를 눈으로 확인하기 위한 것이다.
계산만 하고 아무것도 저장하지 않는다.
"""
import json, sys

COL = 8          # 코인 열 너비
MIN_DAYS = 3     # 이 일수 미만이면 판단하지 말라고 알린다


def load(path="funding_history.jsonl"):
    rows = []
    try:
        with open(path, encoding="utf-8") as fp:
            for l in fp:
                l = l.strip()
                if not l:
                    continue
                try:
                    r = json.loads(l)
                except ValueError:
                    continue
                if r.get("coins"):          # 코인별 수치가 있는 줄만
                    rows.append(r)
    except IOError:
        pass
    rows.sort(key=lambda r: r.get("date", ""))
    return rows


def main():
    rows = load()
    if not rows:
        print("기록 없음 — 코인별 수치가 담긴 날이 아직 없다.")
        print("(funding.py 가 하루 한 번 실행되면서 쌓인다)")
        return 0

    coins = []
    for r in rows:
        for c in r["coins"]:
            if c not in coins:
                coins.append(c)

    print("=== 날짜별 연환산 이자율 (%) — 거래소 중 가장 높은 곳 기준 ===")
    print("%-12s %-7s %s" % ("날짜", "신호등",
                             "".join(("%" + str(COL) + "s") % c for c in coins)))
    for r in rows:
        cells = ""
        for c in coins:
            v = r["coins"].get(c, {}).get("apr")
            cells += ("%" + str(COL) + "s") % ("%.1f" % v if v is not None else "-")
        print("%-12s %-7s %s" % (r["date"], r.get("gate") or "-", cells))

    print()
    print("=== 코인별 안정성 (%d일치) ===" % len(rows))
    print("%-6s %8s %8s %8s %8s  %s" % ("코인", "최저", "평균", "최고", "폭", "판단"))
    for c in coins:
        vals = [r["coins"][c]["apr"] for r in rows
                if c in r["coins"] and r["coins"][c].get("apr") is not None]
        if not vals:
            continue
        lo, hi = min(vals), max(vals)
        avg = sum(vals) / len(vals)
        spread = hi - lo
        if lo < 0:
            note = "마이너스로 떨어진 날 있음 — 제외"
        elif spread > 10:
            note = "들쭉날쭉 — 더 지켜볼 것"
        elif avg >= 8:
            note = "꾸준히 높음"
        elif avg >= 2:
            note = "낮지만 안정적"
        else:
            note = "너무 낮음"
        print("%-6s %8.1f %8.1f %8.1f %8.1f  %s" % (c, lo, avg, hi, spread, note))

    print()
    if len(rows) < MIN_DAYS:
        print("아직 %d일치뿐이다. 최소 %d일은 모여야 판단할 수 있다." % (len(rows), MIN_DAYS))
    else:
        best = None
        for c in coins:
            vals = [r["coins"][c]["apr"] for r in rows
                    if c in r["coins"] and r["coins"][c].get("apr") is not None]
            if not vals or min(vals) < 0:
                continue
            avg = sum(vals) / len(vals)
            if best is None or avg > best[1]:
                best = (c, avg, min(vals), max(vals))
        if best:
            print("%d일 기준 가장 꾸준한 후보: %s — 평균 연 %.1f%% (최저 %.1f%% ~ 최고 %.1f%%)"
                  % (len(rows), best[0], best[1], best[2], best[3]))
            print("한 번도 마이너스로 떨어지지 않은 코인만 후보로 본다.")
        else:
            print("모든 코인이 한 번 이상 마이너스를 찍었다. 진입할 만한 후보 없음.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
