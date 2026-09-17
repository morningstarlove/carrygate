# -*- coding: utf-8 -*-
"""
검산 — 저장된 결과가 맞는지 스스로 다시 계산해 대조한다.

두 가지를 본다.
  1) 계산 검산: funding.json 에 저장된 연환산 수치를, 원본 펀딩비와 정산주기로
     처음부터 다시 계산해 일치하는지 본다. 어긋나면 계산이나 저장 어딘가가 틀린 것이다.
  2) 기록 검산: funding_history.jsonl 의 날짜 중복·누락·순서와 수집 품질을 본다.

문제를 찾으면 화면에 남기고 1번으로 끝낸다(워크플로가 빨간불로 알려준다).
아무것도 고치지 않고 아무것도 저장하지 않는다.
"""
import json, sys
from datetime import datetime, timedelta

TOL = 0.01          # 재계산과 저장값의 허용 오차 (연 %)
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

            net = v.get("decision_apr_pct")
            if net is not None and abs((gross - fee) - net) > TOL:
                problems.append("%s/%s: 수수료 차감 불일치 (저장 %.4f vs 재계산 %.4f)"
                                % (coin, v.get("venue"), net, gross - fee))
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

    print("=== 검산 ===")
    print("재계산 대조: %d건 통과" % checked)
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
