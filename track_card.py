# -*- coding: utf-8 -*-
"""
CARRYGATE — 트랙 한 장 카드 (SVG, 표준 라이브러리만)

새 트랙·규칙이 시작될 때 반드시 한 장을 만든다 (CLAUDE.md "작업 방식"):
  캔들 + 진입·청산 지점 + 어떤 논리로 들어가는가 + 목표 + 기대값 + 결과 예측 + 다음 판정일.
코딩 지식이 없어도 그림 한 장으로 "이 규칙이 무엇을 하는지"를 알 수 있게.

사용법
  python track_card.py --bars docs/cards/data/okx_xrp_30m.csv --spec docs/cards/p30_rotate.json --out docs/cards/p30_rotate.svg
  python track_card.py --bars ... --spec ... --preset scalp     # spec 의 "preset_strategies" 로 scalp.py 규칙을 실제로 돌려 진입·청산을 표시

bars CSV: t,o,h,l,c,v (t=unix 초, UTC). spec JSON 예는 docs/cards/p30_rotate.json.
spec 필드: title, subtitle, logic[], entry, exit, target, expected, prediction, risk, next_check, venue(수수료용),
          markers[] ({"t":..,"px":..,"side":1|-1,"kind":"entry"|"exit","label":"..","tag":"롱/숏 대신 글자","color":".."}), lines[] ({"name":..,"points":[[t,px],..]}),
          preset_strategies[] (scalp.STRATEGIES 의 key), preset_tf(분)
"""
import csv, json, sys, argparse, os
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
W, H = 1400, 880
CHART = (40, 70, 830, 560)      # x, y, w, h
PANEL_X = 930


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def load_bars(path):
    out = []
    with open(path, encoding="utf-8") as fp:
        for r in csv.DictReader(fp):
            out.append({"t": int(float(r["t"])), "o": float(r["o"]), "h": float(r["h"]), "l": float(r["l"]),
                        "c": float(r["c"]), "v": float(r.get("v") or 0)})
    out.sort(key=lambda b: b["t"])
    return out


def wrap(text, width):
    """한국어·영어 섞인 글을 글자 수 기준으로 줄바꿈 (SVG 는 자동 줄바꿈이 없다)."""
    words, lines, cur = str(text).split(" "), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines


def run_preset(bars, spec):
    """scalp.py 의 고정 규칙을 그대로 돌려 진입·청산 표시를 만든다 (규칙 코드는 건드리지 않는다)."""
    import scalp
    tf = int(spec.get("preset_tf", 30))
    keys = spec.get("preset_strategies") or ["vwap_fade_lim"]
    tb = scalp.resample(bars, tf) if tf > 1 else bars
    ctx = scalp.Ctx(tb, 0)
    fees = scalp.VENUES.get(spec.get("venue", "okx"), scalp.VENUES["okx"])
    markers, stats = [], {}
    colors = {"vwap_fade_lim": "#1f77b4", "vwap_fade": "#1f77b4", "mr_z20_lim": "#9467bd", "brk_20": "#ff7f0e"}
    for key in keys:
        st = next(s for s in scalp.STRATEGIES if s["key"] == key)
        trades = scalp.backtest(ctx, st, fees["short_ok"])
        net = 0.0
        for tr in trades:
            cost = (fees["maker_pct"] if tr["entry_maker"] else fees["taker_pct"]) + (fees["maker_pct"] if tr["exit_maker"] else fees["taker_pct"])
            net += tr["gross_pct"] - cost
            ei, xi = tr["entry_i"], tr["exit_i"]
            epx = ctx.c[ei - 1] if tr["entry_maker"] and ei >= 1 else ctx.o[ei]
            xpx = ctx.c[xi - 1] if tr["exit_maker"] and xi >= 1 else ctx.o[xi]
            markers.append({"t": ctx.t[ei], "px": epx, "side": tr["side"], "kind": "entry", "rule": key, "color": colors.get(key, "#333")})
            markers.append({"t": ctx.t[xi], "px": xpx, "side": tr["side"], "kind": "exit", "rule": key, "color": colors.get(key, "#333"),
                            "pnl": tr["gross_pct"] - cost})
        stats[key] = {"trades": len(trades), "net_pct": round(net, 3), "name": st["name"]}
    lines = []
    if any(k.startswith("vwap") for k in keys):
        pts = []
        for i in range(len(tb)):
            lo = max(0, i - 59)
            vs = sum(b["v"] or 0 for b in tb[lo:i + 1])
            if vs > 0 and i >= 59:
                pts.append([tb[i]["t"], sum((b["c"]) * (b["v"] or 0) for b in tb[lo:i + 1]) / vs])
        lines.append({"name": "VWAP(60봉)", "points": pts, "color": "#1f77b4"})
    if "brk_20" in keys:
        pts = [[tb[i]["t"], max(b["h"] for b in tb[i - 20:i])] for i in range(20, len(tb))]
        lines.append({"name": "20봉 고가", "points": pts, "color": "#ff7f0e"})
    return tb, markers, lines, stats


def render(bars, spec, markers, lines, stats):
    x0, y0, cw, ch = CHART
    n = len(bars)
    lo = min(b["l"] for b in bars); hi = max(b["h"] for b in bars)
    for ln in lines:
        for _, px in ln["points"]:
            lo, hi = min(lo, px), max(hi, px)
    pad = (hi - lo) * 0.05 or 1.0
    lo, hi = lo - pad, hi + pad
    t_idx = {b["t"]: i for i, b in enumerate(bars)}
    bw = cw / max(1, n)
    X = lambda i: x0 + bw * (i + 0.5)
    Y = lambda p: y0 + ch - (p - lo) / (hi - lo) * ch
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" font-family="Apple SD Gothic Neo, Malgun Gothic, Noto Sans KR, sans-serif">' % (W, H, W, H),
           '<rect width="%d" height="%d" fill="#fbfbfd"/>' % (W, H),
           '<text x="40" y="40" font-size="24" font-weight="700" fill="#111">%s</text>' % esc(spec.get("title", "")),
           '<text x="40" y="62" font-size="13" fill="#555">%s</text>' % esc(spec.get("subtitle", ""))]
    # 배경 구간(bands): 날짜 단위로 켜고 끄는 규칙(예: P35 '전날 진 날만')을 보이게. [{from, to, color, label}]
    for bd in spec.get("bands") or []:
        idx = [i for i, b in enumerate(bars) if bd["from"] <= b["t"] < bd["to"]]
        if not idx:
            continue
        xa, xb = X(idx[0]) - bw / 2, X(idx[-1]) + bw / 2
        out.append('<rect x="%.1f" y="%d" width="%.1f" height="%d" fill="%s" fill-opacity="0.13"/>' % (xa, y0, xb - xa, ch, bd.get("color", "#999")))
        if bd.get("label"):
            for j, part in enumerate(str(bd["label"]).split("\n")[:2]):
                out.append('<text x="%.1f" y="%d" font-size="10" fill="%s" text-anchor="middle">%s</text>' % ((xa + xb) / 2, y0 + 12 + 12 * j, bd.get("color", "#555"), esc(part)))
    # grid + y labels
    for k in range(6):
        p = lo + (hi - lo) * k / 5
        y = Y(p)
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#e4e4ea" stroke-width="1"/>' % (x0, y, x0 + cw, y))
        out.append('<text x="%d" y="%.1f" font-size="11" fill="#777" text-anchor="start">%s</text>' % (x0 + cw + 6, y + 4, (("{:,.0f}".format(p) if abs(p) >= 1000 else "%.4g" % p))))
    # x labels (KST)
    step = max(1, n // 8)
    for i in range(0, n, step):
        d = datetime.fromtimestamp(bars[i]["t"], KST)
        out.append('<text x="%.1f" y="%d" font-size="11" fill="#777" text-anchor="middle">%s</text>' % (X(i), y0 + ch + 18, d.strftime("%m/%d %H:%M")))
    out.append('<text x="%d" y="%d" font-size="11" fill="#999">한국시간</text>' % (x0, y0 + ch + 36))
    # candles
    for i, b in enumerate(bars):
        up = b["c"] >= b["o"]
        col = "#d33" if up else "#2a6fd6"       # 한국식: 상승 빨강, 하락 파랑
        out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="1"/>' % (X(i), Y(b["h"]), X(i), Y(b["l"]), col))
        top, bot = Y(max(b["o"], b["c"])), Y(min(b["o"], b["c"]))
        out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s"/>' % (X(i) - bw * 0.35, top, bw * 0.7, max(1.0, bot - top), col))
    # lines
    for ln in lines:
        pts = [(X(t_idx[t]), Y(p)) for t, p in ln["points"] if t in t_idx]
        if len(pts) > 1:
            out.append('<polyline fill="none" stroke="%s" stroke-width="1.5" stroke-dasharray="4 3" points="%s"/>'
                       % (ln.get("color", "#333"), " ".join("%.1f,%.1f" % p for p in pts)))
            out.append('<text x="%.1f" y="%.1f" font-size="11" fill="%s" text-anchor="end">%s</text>' % (pts[-1][0] - 4, pts[-1][1] - 6, ln.get("color", "#333"), esc(ln["name"])))
    # markers
    for m in markers:
        if m["t"] not in t_idx:
            continue
        i = t_idx[m["t"]]; x, y = X(i), Y(m["px"])
        col = m.get("color", "#333")
        if m["kind"] == "entry":
            if m["side"] == 1:
                out.append('<polygon points="%.1f,%.1f %.1f,%.1f %.1f,%.1f" fill="%s"/>' % (x, y + 16, x - 7, y + 28, x + 7, y + 28, col))
                out.append('<text x="%.1f" y="%.1f" font-size="10" fill="%s" text-anchor="middle">%s</text>' % (x, y + 40, col, esc(m.get("tag", "롱"))))
            else:
                out.append('<polygon points="%.1f,%.1f %.1f,%.1f %.1f,%.1f" fill="%s"/>' % (x, y - 16, x - 7, y - 28, x + 7, y - 28, col))
                out.append('<text x="%.1f" y="%.1f" font-size="10" fill="%s" text-anchor="middle">%s</text>' % (x, y - 32, col, esc(m.get("tag", "숏"))))
        else:
            out.append('<circle cx="%.1f" cy="%.1f" r="5" fill="none" stroke="%s" stroke-width="2"/>' % (x, y, col))
            if "pnl" in m:
                out.append('<text x="%.1f" y="%.1f" font-size="10" fill="%s" text-anchor="middle">%+.2f%%</text>' % (x, y - 9 if m["side"] == 1 else y + 18, "#d33" if m["pnl"] > 0 else "#2a6fd6", m["pnl"]))
        if m.get("label"):   # 선택: 표시 옆 설명 글자 (P36 부터)
            dy = (52 if m["kind"] == "entry" else 18) if m["side"] == 1 else (-44 if m["kind"] == "entry" else -10)
            out.append('<text x="%.1f" y="%.1f" font-size="10.5" font-weight="700" fill="%s" text-anchor="middle">%s</text>' % (x, y + dy, col, esc(m["label"])))
    # right panel
    y = 90
    def section(head, body, color="#111"):
        nonlocal y
        out.append('<text x="%d" y="%d" font-size="14" font-weight="700" fill="%s">%s</text>' % (PANEL_X, y, color, esc(head)))
        y += 20
        items = body if isinstance(body, list) else [body]
        for it in items:
            for ln in wrap(it, 34):
                out.append('<text x="%d" y="%d" font-size="12.5" fill="#333">%s</text>' % (PANEL_X + 8, y, esc(ln)))
                y += 17
        y += 10
    section("■ 어떤 논리로 들어가나", spec.get("logic", []))
    section("■ 진입", spec.get("entry", ""))
    section("■ 청산", spec.get("exit", ""))
    section("■ 목표", spec.get("target", ""), "#1a7f37")
    section("■ 기대값 (수수료 뺀 뒤)", spec.get("expected", ""), "#1a7f37")
    section("■ 결과 예측", spec.get("prediction", ""), "#b35c00")
    section("■ 위험 / 틀리면", spec.get("risk", ""), "#b00020")
    section("■ 다음 판정", spec.get("next_check", ""))
    if stats:
        line = "  ".join("%s %d건 %+.2f%%" % (v["name"], v["trades"], v["net_pct"]) for v in stats.values())
        out.append('<text x="%d" y="%d" font-size="11.5" fill="#555">이 그림 구간 실제 규칙 결과: %s</text>' % (x0, y0 + ch + 56, esc(line)))
    out.append('<text x="%d" y="%d" font-size="10.5" fill="#999">주문 없음 · 판단 기록만 · 생성 %s · carrygate/track_card.py</text>'
               % (x0, H - 14, datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")))
    out.append('</svg>')
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", required=True)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--preset", choices=["scalp"], help="scalp.py 규칙을 실제로 돌려 진입·청산 표시")
    ap.add_argument("--last", type=int, default=0, help="마지막 N봉만 그린다 (0=전부)")
    a = ap.parse_args(argv)
    bars = load_bars(a.bars)
    spec = json.load(open(a.spec, encoding="utf-8"))
    markers, lines, stats = list(spec.get("markers") or []), list(spec.get("lines") or []), {}
    if a.preset == "scalp":
        bars, pm, pl, stats = run_preset(bars, spec)
        markers += pm; lines += pl
    if a.last and len(bars) > a.last:
        keep = {b["t"] for b in bars[-a.last:]}
        bars = bars[-a.last:]
        markers = [m for m in markers if m["t"] in keep]
        lines = [{"name": l["name"], "color": l.get("color", "#333"), "points": [p for p in l["points"] if p[0] in keep]} for l in lines]
    svg = render(bars, spec, markers, lines, stats)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fp:
        fp.write(svg)
    print("카드 저장:", a.out, "| 봉", len(bars), "| 표시", len(markers), "| 규칙 결과", json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
