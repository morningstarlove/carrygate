# -*- coding: utf-8 -*-
"""
CARRYGATE — 한국 주식 유니버스 + 업종 동조 (설계서 docs/KR_SECTOR_BREAKOUT_PLAN.md 의 1~3단계)

매일 장 마감 뒤(15:45 KST) 한국 주식 **거래대금 상위 100** 을 받아, 업종(industry)별로 묶고,
"같이 오른" 업종(동조)을 고정 규칙으로 표시해 kr_sector.json / kr_sector_history.jsonl 에 남긴다.
주문 기능 없음. 공개 API 만 읽는다. 외부 라이브러리 없음(파이썬 3.8+ 표준만).

  ① 유니버스: 거래량이 아닌 **거래대금** 상위 100 (ETF·ETN·스팩 제외, 우선주는 포함하되 표시)
  ② 업종 묶기: 데이터에 붙어 오는 industry 로 결정적으로 묶는다 (AI 없음, 매일 같은 결과)
  ③ 동조 업종: 상위 100 안의 그룹 종목 ≥ MIN_GROUP, 당일 상승 비율 ≥ UP_RATIO,
              그리고 (당일 등락 중앙값 ≥ MED_CHANGE_PCT 또는 5일 수익률 중앙값 ≥ MED_WEEK_PCT)

자료 경로 (순서대로 시도):
  1. TradingView 공개 스캐너 API (scanner.tradingview.com, 키 불필요)
  2. 한국 PC 중계 파일 data/kr_universe.json (RELAY_MAX_AGE_H 안의 것) — 스캐너가 막혔을 때
  3. --fixture 로 저장된 응답 파일 (시험용, 인터넷 불필요)

사용법
  python kr_universe.py                     # 수집·계산·저장
  python kr_universe.py --dry-run           # 저장 안 함
  python kr_universe.py --fixture tests/fixtures/kr/top100_2026-10-03.json --dry-run
  python kr_universe.py --report            # 기록 누적 보고 (동조 업종 추이)
"""
import os, re, sys, json, time, argparse, statistics, urllib.request
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (compatible; carrygate/1.0)", "Accept": "application/json"}

# ----------------------------------------------------------------- 고정 규칙 (사후 조정 금지)
TOP_N = 100                 # 거래대금 상위 몇 종목
MIN_VALUE_TRADED = 1e9      # 스캐너 1차 필터: 거래대금 10억 원 이상
MIN_GROUP = 3               # 동조 판정에 필요한 그룹 최소 종목 수
UP_RATIO = 0.60             # 당일 상승 종목 비율
MED_CHANGE_PCT = 2.0        # 당일 등락 중앙값 (%)
MED_WEEK_PCT = 5.0          # 또는 5거래일 수익률 중앙값 (%)
GROUP_KEY = "industry"      # 1차 묶음 기준 (sector 는 13개로 뭉쳐 너무 거칠다 — 설계서 1절)
RULE_FIXED = "2026-10-04"

SCANNER_URL = "https://scanner.tradingview.com/korea/scan"
RELAY_PATH = "data/kr_universe.json"
RELAY_MAX_AGE_H = 36.0
OUT_PATH = "kr_sector.json"
HISTORY_PATH = "kr_sector_history.jsonl"

# 스캐너에 요청하는 열. 순서가 응답 d[] 의 순서다.
COLUMNS = ["name", "description", "close", "change", "volume", "Value.Traded", "sector", "industry",
           "market_cap_basic", "Perf.W", "Perf.1M", "High.3M", "Low.3M", "average_volume_10d_calc",
           "relative_volume_10d_calc", "exchange", "type", "typespecs"]
EXCLUDE_WORDS = ("스팩", "SPAC", "ETN", "ETF")   # 종목명에 있으면 제외


def f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def r2(x):
    return None if x is None else round(x, 2)


def http_json(url, data=None, tries=3):
    last = None
    for i in range(tries):
        try:
            body = json.dumps(data).encode("utf-8") if data is not None else None
            hdr = dict(UA)
            if body is not None:
                hdr["Content-Type"] = "application/json"
            req = urllib.request.Request(url, headers=hdr, data=body)
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(1.5 + i * 2)
    raise RuntimeError("fetch failed %s : %s" % (url, last))


# ----------------------------------------------------------------- 수집
def scanner_payload(top=TOP_N):
    # 제외 종목이 섞여 들어와도 100개를 채우도록 여유 있게 받는다
    return {"columns": COLUMNS, "markets": ["korea"], "symbols": {}, "options": {"lang": "en"},
            "filter": [{"left": "type", "operation": "equal", "right": "stock"},
                       {"left": "Value.Traded", "operation": "greater", "right": MIN_VALUE_TRADED}],
            "sort": {"sortBy": "Value.Traded", "sortOrder": "desc"},
            "range": [0, int(top * 1.5)], "ignore_unknown_fields": False}


def fetch_scanner():
    raw = http_json(SCANNER_URL, data=scanner_payload())
    if not isinstance(raw, dict) or "data" not in raw:
        raise RuntimeError("scanner: unexpected response %s" % str(raw)[:200])
    return {"columns": COLUMNS, "data": raw["data"], "totalCount": raw.get("totalCount"),
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def read_saved(path, max_age_h=None, now=None):
    """저장된 스캐너 응답(중계 파일 또는 fixture). max_age_h 를 주면 그보다 오래된 것은 None."""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fp:
        d = json.load(fp)
    if "data" not in d or "columns" not in d:
        return None
    gen = d.get("generated_at_utc")
    if max_age_h is not None:
        try:
            t = datetime.strptime(gen, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None
        age = ((now or datetime.now(timezone.utc)) - t).total_seconds() / 3600.0
        if age > max_age_h:
            return None
        d["age_hours"] = round(age, 1)
    return d


# ----------------------------------------------------------------- 정리
_EXCLUDE_RE = re.compile(r"(스팩|\b(SPAC|ETF|ETN)\b)", re.IGNORECASE)   # 단어 단위 — "Aerospace" 의 spac 에 걸리면 안 된다


def is_excluded(name, code=""):
    return bool(_EXCLUDE_RE.search(name or "")) or bool(_EXCLUDE_RE.search(code or ""))


def parse_rows(resp):
    """스캐너 응답 → 종목 dict 목록 (거래대금 내림차순, 제외어 종목 뺀 뒤 TOP_N 개)."""
    cols = resp["columns"]
    out = []
    for item in resp.get("data") or []:
        d = dict(zip(cols, item.get("d") or []))
        name = d.get("description") or ""
        code = d.get("name") or (item.get("s") or "").split(":")[-1]
        if is_excluded(name, code):
            continue
        if (d.get("type") or "stock") != "stock":
            continue
        specs = d.get("typespecs") or []
        out.append({
            "code": code, "symbol": item.get("s") or ("KRX:" + code), "name": name,
            "exchange": d.get("exchange"), "preferred": "preference" in specs,
            "close": f(d.get("close")), "change_pct": f(d.get("change")), "volume": f(d.get("volume")),
            "value_traded_krw": f(d.get("Value.Traded")), "sector": d.get("sector"), "industry": d.get("industry"),
            "market_cap_krw": f(d.get("market_cap_basic")), "perf_week_pct": f(d.get("Perf.W")),
            "perf_month_pct": f(d.get("Perf.1M")), "high_3m": f(d.get("High.3M")), "low_3m": f(d.get("Low.3M")),
            "avg_volume_10d": f(d.get("average_volume_10d_calc")), "rel_volume_10d": f(d.get("relative_volume_10d_calc")),
        })
    out = [r for r in out if r["value_traded_krw"] is not None]
    out.sort(key=lambda r: -r["value_traded_krw"])
    out = out[:TOP_N]
    for i, r in enumerate(out, 1):
        r["rank"] = i
        hi = r["high_3m"]
        r["pct_from_high_3m"] = r2((r["close"] / hi - 1) * 100) if (hi and r["close"]) else None
    return out


def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def group_rows(rows, key=GROUP_KEY):
    """업종별 묶음 + 동조 판정. 규칙은 맨 위 상수."""
    groups = {}
    for r in rows:
        groups.setdefault(r.get(key) or "(미분류)", []).append(r)
    out = {}
    for g, members in groups.items():
        ch = [m["change_pct"] for m in members if m["change_pct"] is not None]
        up = sum(1 for c in ch if c > 0)
        up_ratio = (up / len(ch)) if ch else 0.0
        med_c, med_w, med_m = median(ch), median([m["perf_week_pct"] for m in members]), median([m["perf_month_pct"] for m in members])
        day_ok = med_c is not None and med_c >= MED_CHANGE_PCT
        week_ok = med_w is not None and med_w >= MED_WEEK_PCT
        sync = len(members) >= MIN_GROUP and up_ratio >= UP_RATIO and (day_ok or week_ok)
        out[g] = {"n": len(members), "up": up, "up_ratio": round(up_ratio, 2),
                  "median_change_pct": r2(med_c), "median_week_pct": r2(med_w), "median_month_pct": r2(med_m),
                  "value_traded_krw": round(sum(m["value_traded_krw"] or 0 for m in members)),
                  "sync": sync, "sync_by": ("day" if day_ok else "week") if sync else None,
                  "members": [m["code"] for m in sorted(members, key=lambda m: m["rank"])]}
    return dict(sorted(out.items(), key=lambda kv: (-kv[1]["sync"], -kv[1]["n"], -(kv[1]["median_change_pct"] or 0))))


def build(resp, source, now=None):
    now = now or datetime.now(timezone.utc)
    rows = parse_rows(resp)
    groups = group_rows(rows)
    sync_list = [g for g, v in groups.items() if v["sync"]]
    total = round(sum(r["value_traded_krw"] or 0 for r in rows))
    return {
        "date": now.astimezone(KST).strftime("%Y-%m-%d"),
        "generated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_at_kst": now.astimezone(KST).strftime("%Y-%m-%d %H:%M KST"),
        "rule_fixed": RULE_FIXED,
        "rules": {"top_n": TOP_N, "group_key": GROUP_KEY, "min_group": MIN_GROUP, "up_ratio": UP_RATIO,
                  "median_change_pct": MED_CHANGE_PCT, "median_week_pct": MED_WEEK_PCT, "exclude_words": list(EXCLUDE_WORDS)},
        "source": dict(source, total_count=resp.get("totalCount"), data_generated_at_utc=resp.get("generated_at_utc")),
        "summary": {"universe_n": len(rows), "value_traded_total_krw": total,
                    "group_n": len(groups), "sync_n": len(sync_list), "sync_groups": sync_list},
        "groups": groups,
        "universe": rows,
    }


# ----------------------------------------------------------------- 저장·보고
def mark_duplicate(result, history_path=HISTORY_PATH):
    """휴장일에 돌면 스캐너가 전 거래일 자료를 그대로 준다. 직전 기록과 거래대금 합계가 같으면 표시한다."""
    result["duplicate_of_previous"] = False
    if not os.path.exists(history_path):
        return
    last = None
    with open(history_path, encoding="utf-8") as fp:
        for line in fp:
            if line.strip():
                last = json.loads(line)
    if last and last.get("value_traded_total_krw") == result["summary"]["value_traded_total_krw"] \
            and last.get("date") != result["date"]:
        result["duplicate_of_previous"] = True
        result["duplicate_note"] = "직전 기록(%s)과 거래대금 합계가 같다 — 휴장일 재실행으로 보인다" % last.get("date")


def save(result):
    with open(OUT_PATH, "w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=1)
    rec = {"date": result["date"], "generated_at_utc": result["generated_at_utc"], "source": result["source"].get("kind"),
           "universe_n": result["summary"]["universe_n"], "value_traded_total_krw": result["summary"]["value_traded_total_krw"],
           "duplicate_of_previous": result.get("duplicate_of_previous", False),
           "sync_groups": {g: {"n": v["n"], "up_ratio": v["up_ratio"], "median_change_pct": v["median_change_pct"],
                               "median_week_pct": v["median_week_pct"], "by": v["sync_by"]}
                           for g, v in result["groups"].items() if v["sync"]},
           "top10": [r["code"] for r in result["universe"][:10]]}
    with open(HISTORY_PATH, "a", encoding="utf-8") as fp:
        fp.write(json.dumps(rec, ensure_ascii=False) + "\n")


def print_result(result):
    s = result["summary"]
    print("[%s] 자료=%s  유니버스 %d종목  거래대금 합계 %.1f조원  업종 %d개  동조 %d개%s" % (
        result["date"], result["source"].get("kind"), s["universe_n"], s["value_traded_total_krw"] / 1e12,
        s["group_n"], s["sync_n"], "  (휴장일 중복?)" if result.get("duplicate_of_previous") else ""))
    print("%-36s %3s %4s %6s %7s %7s  %s" % ("업종", "n", "상승", "당일중앙", "5일중앙", "1달중앙", "동조"))
    for g, v in result["groups"].items():
        if v["n"] < 2 and not v["sync"]:
            continue
        print("%-36s %3d %4d %6s %7s %7s  %s" % (g[:36], v["n"], v["up"],
              "%+.1f" % v["median_change_pct"] if v["median_change_pct"] is not None else "-",
              "%+.1f" % v["median_week_pct"] if v["median_week_pct"] is not None else "-",
              "%+.1f" % v["median_month_pct"] if v["median_month_pct"] is not None else "-",
              ("동조(%s)" % v["sync_by"]) if v["sync"] else ""))
    print("상위 10:", ", ".join("%s %s(%+.1f%%)" % (r["code"], r["name"][:14], r["change_pct"] or 0) for r in result["universe"][:10]))


def report(history_path=HISTORY_PATH, days=15):
    if not os.path.exists(history_path):
        print("기록 없음")
        return
    recs = [json.loads(l) for l in open(history_path, encoding="utf-8") if l.strip()]
    print("최근 %d일 동조 업종 추이 (%d일 기록)" % (min(days, len(recs)), len(recs)))
    for r in recs[-days:]:
        tag = " 중복" if r.get("duplicate_of_previous") else ""
        syncs = ", ".join("%s(%d, %+.1f%%)" % (g, v["n"], v["median_change_pct"] or 0) for g, v in (r.get("sync_groups") or {}).items())
        print("%s %-8s n=%3d%s  %s" % (r["date"], r.get("source"), r.get("universe_n", 0), tag, syncs or "-"))


# ----------------------------------------------------------------- 실행
def collect(fixture=None):
    """자료를 순서대로 시도해 (응답, 출처) 를 돌려준다."""
    if fixture:
        resp = read_saved(fixture)
        if not resp:
            raise RuntimeError("fixture 를 읽을 수 없다: %s" % fixture)
        return resp, {"kind": "fixture", "path": fixture}
    errors = {}
    try:
        return fetch_scanner(), {"kind": "scanner", "url": SCANNER_URL}
    except Exception as e:
        errors["scanner"] = str(e)[:300]
        print("스캐너 실패: %s" % errors["scanner"], file=sys.stderr)
    relay = read_saved(RELAY_PATH, max_age_h=RELAY_MAX_AGE_H)
    if relay:
        return relay, {"kind": "relay", "path": RELAY_PATH, "age_hours": relay.get("age_hours"), "errors": errors}
    errors["relay"] = "%s 없음 또는 %.0f시간보다 오래됨" % (RELAY_PATH, RELAY_MAX_AGE_H)
    raise RuntimeError("자료 경로 모두 실패: %s" % json.dumps(errors, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fixture", help="저장된 스캐너 응답 파일 (인터넷 불필요)")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        report()
        return 0
    resp, source = collect(a.fixture)
    result = build(resp, source)
    mark_duplicate(result)
    print_result(result)
    if a.dry_run:
        print("(dry-run: 저장 안 함)")
    else:
        save(result)
        print("저장: %s, %s" % (OUT_PATH, HISTORY_PATH))
    return 0


if __name__ == "__main__":
    sys.exit(main())
