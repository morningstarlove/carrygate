"""P39 추세선 채널 카드 재료 만들기: 실데이터 일봉 → 카드용 봉 CSV + 설명 JSON(선·진입·청산은 trendline.py 가 실제로 낸 것).
재현: python scripts/trendline_card.py
      python track_card.py --bars docs/cards/data/krx_069500_1d.csv --spec docs/cards/p39_trendline.json --out docs/cards/p39_trendline.svg
표준 라이브러리만, 인터넷 불필요. 규칙은 바꾸지 않는다.
"""
import csv, os, sys, json
from datetime import datetime, timezone
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT)
import trendline as T

SYM, LAST, COST = "KRX_069500", 330, 0.05
SRC = os.path.join(ROOT, "data", "bb_reentry", SYM + ".csv")
BARS_OUT = os.path.join(ROOT, "docs", "cards", "data", "krx_069500_1d.csv")
SPEC_OUT = os.path.join(ROOT, "docs", "cards", "p39_trendline.json")


def ts(d):
    return int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def main():
    with open(SRC, encoding="utf-8") as fp:
        bars = [{"date": r["date"], "o": float(r["o"]), "h": float(r["h"]), "l": float(r["l"]), "c": float(r["c"])} for r in csv.DictReader(fp)]
    a = len(bars) - LAST
    with open(BARS_OUT, "w", encoding="utf-8", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["t", "o", "h", "l", "c", "v"])
        for b in bars[a:]:
            w.writerow([ts(b["date"]), b["o"], b["h"], b["l"], b["c"], 0])
    trades = [t for t in T.backtest(bars, "중", "base", COST) if t["exit_i"] >= a]
    markers, lines, notes = [], [], []
    for n, t in enumerate(trades, 1):
        ch = t["ch"]
        lo, hi = max(ch["i1"], a), t["exit_i"]
        for name, fn, col in (("하단%d" % n, T.lower, "#d33"), ("중단%d" % n, T.mid, "#999"), ("상단%d" % n, T.upper, "#d33")):
            lines.append({"name": name, "color": col, "points": [[ts(bars[j]["date"]), round(fn(ch, j), 2)] for j in range(lo, hi + 1)]})
        for i, key in ((ch["i1"], "l"), (ch["i2"], "l"), (ch["i3"], "h")):
            if i >= a:
                markers.append({"t": ts(bars[i]["date"]), "px": bars[i][key], "side": 1, "kind": "exit", "color": "#555"})
        markers.append({"t": ts(t["date_in"]), "px": t["entry"], "side": 1, "kind": "entry", "color": "#1a7f37"})
        m = {"t": ts(t["date_out"]), "px": t["exit"], "side": 1, "kind": "exit", "color": "#1a7f37", "pnl": round(t["net_pct"], 2)}
        markers.append(m)
        why = {"target": "상단 익절", "stop": "하단 이탈 손절", "expire": "유효 기간 만료", "open": "보유 중(자료 끝)"}[t["why"]]
        notes.append("%d) %s 매수 → %s %s %+.1f%%" % (n, t["date_in"][2:], t["date_out"][2:], why, t["net_pct"]))
    spec = {
        "title": "P39 추세선 채널 — 하단에서 잡고 상단에서 놓는다 (KODEX 200 일봉, 실데이터)",
        "subtitle": "%s ~ %s. 빨간 점선 = 코드가 그은 하단·상단(저점①-②를 잇고 ③고점에 평행), 회색 점선 = 중단. 회색 원 = ①②③, 초록 세모 = 매수, 초록 원 = 청산. 주문 없음."
                    % (bars[a]["date"], bars[-1]["date"]),
        "venue": "krx",
        "logic": ["강의 자료 '7장 추세선 작도'를 숫자로 고정: 최근 120봉에서 최저 저가 ①과 그 뒤 확정 저점 ②를 이어 하단, ③고점에 평행 이동해 상단, 가운데가 중단.",
                  "하단은 지지, 상단은 저항. 하단에서 사면 손절(하단 이탈)은 가깝고 목표(상단)는 멀다 = 손익비.",
                  "선은 두 점 간격만큼만 앞으로 유효(향후 1배). 살짝 튀어나간 것(폭의 10%)은 이탈로 안 본다(보조)."],
        "entry": "저가가 하단 + 폭 10% 안에 닿고 종가가 하단 위에서 끝난 날 → 다음 날 시가 매수. 진입 때 선을 얼린다.",
        "exit": "고가가 상단에 닿으면 익절 / 종가가 하단 − 폭 10% 아래면 다음 날 시가 손절 / 유효 기간 끝나는 날 종가.",
        "target": "이 그림: " + " · ".join(notes),
        "expected": "실데이터 15종목(미국·한국·코인·금·환율, 2010~2026-11) 691건: 건당 +1.13%, 승률 32%, 손익비로 버는 구조(PF 1.40), t +2.17. 단 같은 일수 그냥 보유보다 건당 −0.85% 낮다.",
        "prediction": "10번 중 8번은 하단 이탈 손절(평균 −3.0%), 상단 익절은 14번에 1번(평균 +31.5%). 작게 자주 잃고 드물게 크게 번다. 한국 개별주는 사후 0 근처라 약할 것.",
        "risk": "t 3 미만 — 우연과 구별 안 됨. 2010~2017 은 건당 −0.05%. 갭 하락이면 손절이 선보다 훨씬 아래에서 체결된다. 불합격이면 규칙을 고치지 않고 '안 된다'로 기록.",
        "next_check": "사용자 결정 대기(제안함 P39): A 순방향 종이 기록 시작 → 20건 쌓이면 첫 판정. 설계서 docs/TRENDLINE_PLAN.md, 규칙 trendline.py, 검증 scripts/trendline_check.py.",
        "markers": markers, "lines": lines,
    }
    with open(SPEC_OUT, "w", encoding="utf-8") as fp:
        json.dump(spec, fp, ensure_ascii=False, indent=1)
    print("봉", LAST, "| 거래", len(trades), "|", " / ".join(notes))


if __name__ == "__main__":
    main()
