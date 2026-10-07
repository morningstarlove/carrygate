# -*- coding: utf-8 -*-
"""
검산 — 저장된 결과가 맞는지 스스로 다시 계산해 대조한다.

두 가지를 본다.
  1) 계산 검산: funding.json 에 저장된 연환산 수치를, 원본 펀딩비와 정산주기로
     처음부터 다시 계산해 일치하는지 본다. 어긋나면 계산이나 저장 어딘가가 틀린 것이다.
  2) 기록 검산: funding_history.jsonl 의 날짜 중복·누락·순서와 수집 품질을 본다.
  3) 초단타 검산: scalp.json 의 순수익을 총수익·매매 건수·왕복 비용에서 다시 계산해
     대조하고, scalp_history.jsonl 의 날짜 중복·순서를 본다.

문제를 찾으면 화면에 남기고 1번으로 끝낸다(워크플로가 빨간불로 알려준다).
아무것도 고치지 않고 아무것도 저장하지 않는다.
"""
import json, sys
from datetime import datetime, timedelta

TOL = 0.01          # 재계산과 저장값의 허용 오차 (연 %)
HOLD_DAYS = 30.0    # funding.py 와 같은 보유기간 가정
MAX_GAP_DAYS = 2    # 기록이 이 일수 넘게 끊기면 알린다


def load_json(path):
    try:
        with open(path, encoding="utf-8") as fp:
            return json.load(fp)
    except (IOError, ValueError):
        return None


def load_rows(path="funding_history.jsonl"):
    rows = []
    try:
        with open(path, encoding="utf-8") as fp:
            for n, line in enumerate(fp, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append((n, json.loads(line)))
                except ValueError:
                    rows.append((n, None))
    except IOError:
        pass
    return rows


def check_math(d, problems, notes):
    """저장된 연환산을 원본 펀딩비에서 다시 계산해 대조한다."""
    fee = (d.get("summary") or {}).get("fee_drag_apr_pct")
    if fee is None:
        notes.append("수수료 차감 정보 없음 — 예전 형식 파일로 보인다")
        fee = 0.0

    # 보유기간은 funding.json 에 기록된 값을 우선한다 (가정이 바뀌어도 대조가 맞도록)
    hold = (d.get("fee_assumption") or {}).get("assumed_hold_days") or HOLD_DAYS
    checked, legacy = 0, 0
    for coin, cd in (d.get("coins") or {}).items():
        for v in cd.get("venues", []):
            h = v.get("interval_hours")
            if not h:
                problems.append("%s/%s: 정산주기가 비어 있다" % (coin, v.get("venue")))
                continue

            # 판정에 쓴 원본 비율을 고른다 (7일평균 우선 — funding.py 와 같은 규칙)
            raw = v.get("funding_rate_avg7d")
            if raw is None:
                raw = v.get("funding_rate_now")
            if raw is None:
                continue

            redone = raw * (24.0 / h) * 365.0 * 100.0
            gross = v.get("gross_apr_pct")
            if gross is None:
                legacy += 1          # 수수료 반영 전에 저장된 값. 대조할 대상이 없다.
                continue
            if abs(redone - gross) > TOL:
                problems.append("%s/%s: 펀딩 연환산 불일치 (저장 %.4f vs 재계산 %.4f)"
                                % (coin, v.get("venue"), gross, redone))
                continue

            # 거래소마다 수수료가 다르다. 저장된 체결 수수료율로 연 부담을 다시 계산한다.
            sp, pp = v.get("fee_spot_taker_pct"), v.get("fee_perp_taker_pct")
            vfee = (2.0 * (sp + pp) * 365.0 / hold) if (sp is not None and pp is not None) \
                else v.get("fee_drag_apr_pct", fee)
            if v.get("fee_drag_apr_pct") is not None and abs(vfee - v["fee_drag_apr_pct"]) > TOL:
                problems.append("%s/%s: 수수료 연환산 불일치 (저장 %.4f vs 재계산 %.4f)"
                                % (coin, v.get("venue"), v["fee_drag_apr_pct"], vfee))
                continue
            net = v.get("decision_apr_pct")
            if net is not None and abs((gross - vfee) - net) > TOL:
                problems.append("%s/%s: 수수료 차감 불일치 (저장 %.4f vs 재계산 %.4f)"
                                % (coin, v.get("venue"), net, gross - vfee))
                continue
            checked += 1

        # 대표 거래소가 실제로 순수익 1위인지
        vs = cd.get("venues") or []
        best = cd.get("best")
        if vs and best:
            cand = [v for v in vs if v.get("decision_apr_pct") is not None]
            top = max(cand, key=lambda v: (1 if v.get("basis") == "7일평균" else 0,
                                           v["decision_apr_pct"])) if cand else None
            if top is None:
                continue
            if top.get("venue") != best.get("venue"):
                problems.append("%s: 대표 거래소가 1위가 아니다 (%s 선택, %s 가 위)"
                                % (coin, best.get("venue"), top.get("venue")))

    if legacy:
        notes.append("%d건은 수수료 반영 전 형식이라 대조를 건너뛰었다" % legacy)
    return checked


def check_quality(d, problems, notes):
    s = d.get("summary") or {}
    ok_n = len(d.get("sources_ok") or [])
    failed = d.get("sources_failed") or {}

    if ok_n == 0:
        problems.append("모든 거래소 수집 실패 — 수치를 믿을 수 없다")
    elif ok_n < 2:
        problems.append("거래소 %d곳만 성공 — 교차 확인이 안 된다" % ok_n)

    cov = s.get("avg_coverage")
    if cov and "/" in str(cov):
        got, total = str(cov).split("/")
        if total != "0" and got != total:
            notes.append("7일 평균이 %s 코인만 붙었다" % cov)

    if s.get("state") == "진입 가능" and s.get("best_basis") != "7일평균":
        problems.append("7일 평균 없이 진입 판정이 나왔다 — 안전장치가 안 먹었다")

    for name, msg in failed.items():
        notes.append("%s 수집 실패: %s" % (name, str(msg)[:80]))


def check_history(problems, notes):
    rows = load_rows()
    if not rows:
        notes.append("기록 파일이 아직 없다")
        return

    seen, dates = {}, []
    for n, r in rows:
        if r is None:
            problems.append("%d번째 줄이 깨져 있다" % n)
            continue
        date = r.get("date")
        if not date:
            problems.append("%d번째 줄에 날짜가 없다" % n)
            continue
        if date in seen:
            problems.append("날짜 %s 가 %d번, %d번 줄에 중복" % (date, seen[date], n))
        seen[date] = n
        dates.append(date)

    if dates != sorted(dates):
        problems.append("기록이 날짜순이 아니다")

    parsed = []
    for date in sorted(set(dates)):
        try:
            parsed.append(datetime.strptime(date, "%Y-%m-%d"))
        except ValueError:
            problems.append("날짜 형식이 이상하다: %s" % date)
    for a, b in zip(parsed, parsed[1:]):
        gap = (b - a).days
        if gap > MAX_GAP_DAYS:
            notes.append("%s 와 %s 사이 %d일 비어 있다"
                         % (a.strftime("%Y-%m-%d"), b.strftime("%Y-%m-%d"), gap))

    notes.append("기록 %d일치 (%s ~ %s)"
                 % (len(parsed),
                    parsed[0].strftime("%Y-%m-%d") if parsed else "-",
                    parsed[-1].strftime("%Y-%m-%d") if parsed else "-"))


def check_scalp(problems, notes):
    """초단타(scalp.json) 검산: 저장된 순수익을 총수익과 체결 비용에서 다시 계산해 대조한다.

    체결 1회 비용 = 시장가면 수수료 + 호가폭/2, 지정가면 수수료. 규칙별로 시장가·지정가
    체결 횟수(taker_sides / maker_sides)가 저장돼 있어 비용 총액을 그대로 재계산할 수 있다."""
    d = load_json("scalp.json")
    if d is None:
        notes.append("scalp.json 이 없다 — 초단타 검증이 아직 안 돌았다")
        return 0
    rows = d.get("rows") or []
    markets = d.get("markets") or {}
    if not rows:
        problems.append("초단타: 규칙 결과가 하나도 없다 (모든 시장 수집 실패)")
        return 0
    checked = 0
    for r in rows:
        m = markets.get("%s:%s" % (r.get("venue"), r.get("coin")))
        if not m:
            problems.append("초단타 %s/%s: 시장 정보가 없다" % (r.get("venue"), r.get("coin")))
            continue
        cost = m.get("cost") or {}
        taker, maker, spread = cost.get("taker_pct"), cost.get("maker_pct"), cost.get("spread_pct_est")
        if None in (taker, maker, spread):
            problems.append("초단타 %s/%s: 비용 정보가 비어 있다" % (r["venue"], r["coin"]))
            continue
        tag = "%s/%s/%s/%s" % (r["venue"], r["coin"], r.get("tf"), r.get("strategy"))
        n, g = r.get("trades", 0), r.get("gross_pct", 0.0)
        ms, ts = r.get("maker_sides"), r.get("taker_sides")
        if ms is None or ts is None or ms + ts != 2 * n:
            problems.append("초단타 %s: 체결 횟수가 매매 건수와 안 맞는다 (지정가 %s + 시장가 %s != 2×%d)" % (tag, ms, ts, n))
            continue
        if r.get("exec") == "market" and ms != 0:
            problems.append("초단타 %s: 시장가 규칙에 지정가 체결이 있다" % tag)
            continue
        redo_fee = ms * maker + ts * (taker + spread / 2.0)
        if abs(redo_fee - r.get("fee_pct", 0.0)) > TOL:
            problems.append("초단타 %s: 비용 불일치 (저장 %.4f vs 재계산 %.4f)" % (tag, r.get("fee_pct"), redo_fee))
            continue
        if abs((g - redo_fee) - r.get("net_pct", 0.0)) > TOL:
            problems.append("초단타 %s: 순수익 불일치 (저장 %.4f vs 재계산 %.4f)" % (tag, r.get("net_pct"), g - redo_fee))
            continue
        if abs(r.get("first_half_net_pct", 0.0) + r.get("second_half_net_pct", 0.0) - r.get("net_pct", 0.0)) > TOL:
            problems.append("초단타 %s: 앞·뒤 반나절 합이 하루 순수익과 다르다" % tag)
            continue
        if r.get("robust") and (n < 10 or r.get("net_pct", 0) <= 0 or not r.get("beats_baseline")):
            problems.append("초단타 %s: 견고 표시 조건이 안 맞는다" % tag)
            continue
        checked += 1
    s = d.get("summary") or {}
    real = [r for r in rows if r.get("strategy") != "baseline_hold5"]
    if s.get("rows_total") != len(real):
        problems.append("초단타: 요약의 조합 수(%s)가 실제(%d)와 다르다" % (s.get("rows_total"), len(real)))
    pos = sum(1 for r in real if r.get("net_pct", 0) > 0)
    if s.get("rows_positive") != pos:
        problems.append("초단타: 요약의 플러스 조합 수(%s)가 실제(%d)와 다르다" % (s.get("rows_positive"), pos))
    if len(markets) < 2:
        notes.append("초단타: 시장 %d곳뿐 — 교차 확인이 약하다" % len(markets))
    for k, v in (d.get("failed") or {}).items():
        notes.append("초단타 수집 실패 %s: %s" % (k, str(v)[:80]))
    return checked


def check_scalp_history(problems, notes):
    rows = load_rows("scalp_history.jsonl")
    if not rows:
        notes.append("초단타 기록 파일이 아직 없다")
        return
    seen, dates = {}, []
    for n, r in rows:
        if r is None:
            problems.append("초단타 기록 %d번째 줄이 깨져 있다" % n)
            continue
        date = r.get("date")
        if not date:
            problems.append("초단타 기록 %d번째 줄에 날짜가 없다" % n)
            continue
        if date in seen:
            problems.append("초단타 기록 날짜 %s 가 %d번, %d번 줄에 중복" % (date, seen[date], n))
        seen[date] = n
        dates.append(date)
    if dates != sorted(dates):
        problems.append("초단타 기록이 날짜순이 아니다")
    notes.append("초단타 기록 %d일치 (%s ~ %s)" % (len(dates), dates[0] if dates else "-", dates[-1] if dates else "-"))


def check_jsonl_dates(path, label, problems, notes):
    """기록 파일의 날짜 중복·순서만 본다 (연구 파일은 수치 재계산 대상이 아니다)."""
    rows = load_rows(path)
    if not rows:
        notes.append("%s 기록 파일이 아직 없다" % label)
        return
    seen, dates = {}, []
    for n, r in rows:
        if r is None or not r.get("date"):
            problems.append("%s 기록 %d번째 줄이 깨졌거나 날짜가 없다" % (label, n))
            continue
        if r["date"] in seen:
            problems.append("%s 기록 날짜 %s 가 %d번, %d번 줄에 중복" % (label, r["date"], seen[r["date"]], n))
        seen[r["date"]] = n
        dates.append(r["date"])
    if dates != sorted(dates):
        problems.append("%s 기록이 날짜순이 아니다" % label)
    notes.append("%s 기록 %d일치 (%s ~ %s)" % (label, len(dates), dates[0] if dates else "-", dates[-1] if dates else "-"))


def check_basis(problems, notes):
    """베이시스 캐리: 저장된 연환산을 선물가·현물가·만기일수에서 다시 계산해 대조한다."""
    d = load_json("basis.json")
    if d is None:
        notes.append("basis.json 이 없다")
        return 0
    one_off = (d.get("fee_assumption") or {}).get("one_off_total_pct")
    checked = 0
    for coin, cd in (d.get("coins") or {}).items():
        for c in cd.get("contracts", []):
            days, fut, spot = c.get("days"), c.get("fut_px"), c.get("spot_px")
            if not days or not fut or not spot:
                problems.append("베이시스 %s/%s: 값이 비어 있다" % (coin, c.get("inst")))
                continue
            if not c.get("eligible"):
                continue        # 만기 임박 계약은 연환산이 튀어 판정에 안 쓴다 — 대조도 생략
            gross = (fut / spot - 1.0) * 365.0 / days * 100.0
            fee = (one_off if one_off is not None else c.get("fee_one_off_pct", 0.0)) * 365.0 / days
            tol = max(0.05, 0.002 * abs(gross))
            if abs(gross - c.get("gross_apr_pct", 0)) > tol or abs((gross - fee) - c.get("net_apr_pct", 0)) > tol:
                problems.append("베이시스 %s/%s: 연환산 불일치 (저장 총 %.3f 순 %.3f vs 재계산 %.3f / %.3f)"
                                % (coin, c.get("inst"), c.get("gross_apr_pct", 0), c.get("net_apr_pct", 0), gross, gross - fee))
                continue
            checked += 1
        best = cd.get("best")
        elig = [c for c in cd.get("contracts", []) if c.get("eligible")]
        if best and elig and max(elig, key=lambda c: c["net_apr_pct"])["inst"] != best["inst"]:
            problems.append("베이시스 %s: 대표 계약이 순수익 1위가 아니다" % coin)
    return checked


def check_turtle(problems, notes):
    """터틀: 저장된 실현 순수익을 매매별 수익률의 복리 곱에서 다시 계산해 대조하고, 손절가 산식을 본다."""
    d = load_json("turtle.json")
    if d is None:
        notes.append("turtle.json 이 없다")
        return 0
    checked = 0
    for v, vd in (d.get("venues") or {}).items():
        items = [("%s/%s" % (v, m), md) for m, md in (vd.get("markets") or {}).items()]
        if vd.get("portfolio"):
            items.append(("%s/포트폴리오" % v, vd["portfolio"]))
        for vn, e in (vd.get("variants") or {}).items():
            items += [("%s/%s/%s" % (vn, v, m), md) for m, md in (e.get("markets") or {}).items()]
            if e.get("portfolio"):
                items.append(("%s/%s/포트폴리오" % (vn, v), e["portfolio"]))
        for name, md in items:
            rets = md.get("returns_pct") or []
            st = md.get("stats") or {}
            net = 1.0
            for r in rets:
                net *= 1.0 + r / 100.0
            net = (net - 1.0) * 100.0
            if st.get("trades") != len(rets):
                problems.append("터틀 %s: 매매 수(%s)와 수익률 목록(%d)이 다르다" % (name, st.get("trades"), len(rets)))
                continue
            if abs(net - (st.get("realized_net_pct") or 0.0)) > max(0.01, 0.001 * abs(net)):
                problems.append("터틀 %s: 실현 순수익 불일치 (저장 %.3f vs 재계산 %.3f)" % (name, st.get("realized_net_pct") or 0.0, net))
                continue
            mult = (d.get("rules") or {}).get("stop_n_mult", 2.0)
            for t in (md.get("recent_trades") or []) + (md.get("forward_trades") or []):
                sign = 1 if t.get("dir") == "long" else -1
                if abs(t["stop"] - (t["entry"] - sign * mult * t["n_at_entry"])) > max(1e-6, 1e-3 * t["entry"]):
                    problems.append("터틀 %s: 손절가가 진입가 ∓ %sN 이 아니다 (%s)" % (name, mult, t.get("entry_date")))
                    break
            checked += 1
    return checked


def check_grid(problems, notes):
    """그리드(종사종팔): 저장된 승률·만기 비율이 매매 수와 맞는지, 순방향 티어의 익절가가 매수가×(1+익절률)인지 본다."""
    d = load_json("grid.json")
    if d is None:
        notes.append("grid.json 이 없다")
        return 0
    checked = 0
    versions = (d.get("rules") or {}).get("versions") or {}
    for sym, res in (d.get("symbols") or {}).items():
        for wk, w in (res.get("windows") or {}).items():
            for v in versions:
                m = w.get(v)
                if not m:
                    continue
                if m.get("max_drawdown_pct", 0) > 1e-9:
                    problems.append("그리드 %s %s %s: 최대낙폭이 양수다" % (sym, wk, v))
                    continue
                if m.get("trades") and m.get("expired_sells") is not None:
                    share = m["expired_sells"] / m["trades"] * 100.0
                    if abs(share - (m.get("expired_share_pct") or 0.0)) > 0.05:
                        problems.append("그리드 %s %s %s: 만기 매도 비율 불일치 (저장 %.2f vs 재계산 %.2f)" % (
                            sym, wk, v, m.get("expired_share_pct") or 0.0, share))
                        continue
                checked += 1
        for v, fw in (res.get("forward") or {}).items():
            tgt = (versions.get(v) or {}).get("target_pct")
            for t in fw.get("tiers") or []:
                if tgt is None:
                    break
                want = t["buy_px"] * (1.0 + tgt / 100.0)
                if abs(t["target_px"] - want) > max(1e-6, 1e-4 * want):
                    problems.append("그리드 %s %s 순방향: 익절가가 매수가×(1+%s%%) 가 아니다 (%s)" % (sym, v, tgt, t.get("buy_date")))
                    break
    return checked


def check_reversal(problems, notes):
    """추세 전환(4시간봉): 실현 순수익을 매매별 수익률의 복리 곱에서 다시 계산해 대조하고, 손절·진입 규칙을 본다.
    - 손절 시작값(pattern_low) 은 최종 손절선 이하여야 한다(손절선은 올라가기만 한다)
    - 눌림 지정가 진입(pullback_limit)은 목선 이하 가격에 체결돼야 한다
    - 손절 청산(reason=stop)은 pattern_low 이하 가격에 나가야 한다"""
    d = load_json("reversal.json")
    if d is None:
        notes.append("reversal.json 이 없다")
        return 0
    checked = 0
    for v, vd in (d.get("venues") or {}).items():
        items = [("%s/%s" % (v, m), md) for m, md in (vd.get("markets") or {}).items()]
        if vd.get("portfolio"):
            items.append(("%s/포트폴리오" % v, vd["portfolio"]))
        for vn, e in (vd.get("variants") or {}).items():
            items += [("%s/%s/%s" % (vn, v, m), md) for m, md in (e.get("markets") or {}).items()]
            if e.get("portfolio"):
                items.append(("%s/%s/포트폴리오" % (vn, v), e["portfolio"]))
        for name, md in items:
            rets = md.get("returns_pct") or []
            st = md.get("stats") or {}
            net = 1.0
            for r in rets:
                net *= 1.0 + r / 100.0
            net = (net - 1.0) * 100.0
            if st.get("trades") != len(rets):
                problems.append("추세전환 %s: 매매 수(%s)와 수익률 목록(%d)이 다르다" % (name, st.get("trades"), len(rets)))
                continue
            if abs(net - (st.get("realized_net_pct") or 0.0)) > max(0.01, 0.001 * abs(net)):
                problems.append("추세전환 %s: 실현 순수익 불일치 (저장 %.3f vs 재계산 %.3f)" % (name, st.get("realized_net_pct") or 0.0, net))
                continue
            bad = None
            for t in (md.get("recent_trades") or []) + (md.get("forward_trades") or []):
                tol = 1e-6 * max(1.0, t["entry"])
                if t["stop_final"] < t["pattern_low"] - tol:
                    bad = "손절선이 패턴 저점보다 낮다 (%s)" % t.get("entry_date")
                elif t.get("entry_how") == "pullback_limit" and t["entry"] > t["neckline"] + tol:
                    bad = "눌림 지정가 진입이 목선보다 높다 (%s)" % t.get("entry_date")
                elif t.get("reason") == "stop" and t["exit"] > t["pattern_low"] + tol:
                    bad = "손절 청산가가 패턴 저점보다 높다 (%s)" % t.get("entry_date")
                if bad:
                    break
            if bad:
                problems.append("추세전환 %s: %s" % (name, bad))
                continue
            checked += 1
    return checked


def main():
    problems, notes = [], []

    d = load_json("funding.json")
    checked = 0
    if d is None:
        notes.append("funding.json 이 없다 — 오늘 수집이 아직 안 돌았다")
    else:
        checked = check_math(d, problems, notes)
        check_quality(d, problems, notes)

    check_history(problems, notes)
    scalp_checked = check_scalp(problems, notes)
    check_scalp_history(problems, notes)
    check_jsonl_dates("research_history.jsonl", "보조 연구", problems, notes)
    check_jsonl_dates("orderbook_history.jsonl", "호가창", problems, notes)
    basis_checked = check_basis(problems, notes)
    turtle_checked = check_turtle(problems, notes)
    reversal_checked = check_reversal(problems, notes)
    grid_checked = check_grid(problems, notes)
    for path, label in (("basis_history.jsonl", "베이시스"), ("funding_signal_history.jsonl", "펀딩 신호"),
                        ("kimchi_history.jsonl", "김치프리미엄"), ("momentum_history.jsonl", "듀얼 모멘텀"),
                        ("live_history.jsonl", "실전 캐리"), ("turtle_history.jsonl", "터틀"),
                        ("reversal_history.jsonl", "추세 전환"), ("grid_history.jsonl", "그리드")):
        check_jsonl_dates(path, label, problems, notes)

    print("=== 검산 ===")
    print("재계산 대조: 캐리 %d건, 초단타 %d건, 베이시스 %d건, 터틀 %d건, 추세전환 %d건, 그리드 %d건 통과" % (checked, scalp_checked, basis_checked, turtle_checked, reversal_checked, grid_checked))
    for m in notes:
        print("  · %s" % m)

    if problems:
        print()
        print("!!! 문제 %d건 !!!" % len(problems))
        for m in problems:
            print("  X %s" % m)
        return 1

    print("문제 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
