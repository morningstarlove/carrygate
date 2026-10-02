# -*- coding: utf-8 -*-
"""
CARRYGATE — 한국 중계 수집기 (바이낸스·바이비트 펀딩비)

GitHub Actions(미국 서버)는 바이낸스(451)·바이비트(403)가 막혀 있다. 이 파일을 **한국에 있는 PC** 에서 하루 한 번
돌리면 두 거래소의 펀딩비(현재값 + 최근 7일 정산 이력)를 읽어 저장소의 data/kr_funding.json 에 올린다.
매일 아침 Actions 가 도는 funding.py 는 바이낸스·바이비트가 막혔을 때 이 파일(36시간 안의 것)을 대신 쓴다.

이 파일 하나만 있으면 된다(저장소의 다른 파일에 의존하지 않는다). 파이썬 3.8 이상.

  python kr_relay.py              # 수집 + 저장소 업로드 (옆에 kr_relay_config.json 필요)
  python kr_relay.py --no-upload  # 수집만 하고 kr_funding.json 을 옆에 저장 (시험용)

kr_relay_config.json (이 파일과 같은 폴더):
  {"token": "github_pat_...", "repo": "morningstarlove/carrygate", "branch": "main"}

토큰은 GitHub 의 Fine-grained personal access token 으로, **이 저장소 하나에만 Contents: Read and write** 권한.
거래소 API 키는 쓰지 않는다 — 거래소에는 공개 API 만 읽는다. 주문 기능 없음.
설정 절차: docs/KR_RELAY_SETUP.md
"""
import os, sys, json, time, base64, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

COINS = ["BTC", "ETH", "XRP", "TRX", "LINK", "DOGE"]
KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (compatible; carrygate-kr-relay/1.0)", "Accept": "application/json"}
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "kr_relay_config.json")
LOCAL_OUT = os.path.join(HERE, "kr_funding.json")
LOCAL_HISTORY = os.path.join(HERE, "kr_funding_history.jsonl")
REMOTE_PATH = "data/kr_funding.json"
AVG_DAYS = 7


def http_json(url, tries=3, headers=None, data=None, method=None):
    last = None
    for i in range(tries):
        try:
            hdr = dict(UA)
            if headers:
                hdr.update(headers)
            body = json.dumps(data).encode("utf-8") if data is not None else None
            if body is not None:
                hdr["Content-Type"] = "application/json"
            req = urllib.request.Request(url, headers=hdr, data=body, method=method)
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404 and method in (None, "GET") and "api.github.com" in url:
                return None                       # 파일이 아직 없다
            last = "HTTP %s %s" % (e.code, (e.read() or b"")[:200].decode("utf-8", "replace"))
            if e.code in (401, 403, 404, 422):
                break                             # 다시 해도 같은 결과
        except Exception as e:
            last = e
        time.sleep(1.5 + i * 2)
    raise RuntimeError("fetch failed %s : %s" % (url, last))


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def avg_recent(pairs, days=AVG_DAYS):
    """[(정산시각_ms, 비율)] -> 최근 N일 평균. 없으면 None. (funding.py 와 같은 식)"""
    cutoff = (time.time() - days * 86400) * 1000.0
    vals = [r for t, r in pairs if t is not None and r is not None and t >= cutoff]
    return sum(vals) / len(vals) if vals else None


# ----------------------------------------------------------------- 거래소

def src_binance():
    prem = http_json("https://fapi.binance.com/fapi/v1/premiumIndex")
    by_sym = {r["symbol"]: r for r in prem}
    iv = {}
    try:
        for r in http_json("https://fapi.binance.com/fapi/v1/fundingInfo"):
            h = f(r.get("fundingIntervalHours"))
            if h:
                iv[r["symbol"]] = h
    except Exception:
        pass
    out = {}
    for c in COINS:
        sym = c + "USDT"
        r = by_sym.get(sym)
        if not r:
            continue
        hours = iv.get(sym, 8.0)
        mark, index = f(r.get("markPrice")), f(r.get("indexPrice"))
        pairs = []
        try:
            n = int(round(AVG_DAYS * 24 / hours)) + 2
            for h in http_json("https://fapi.binance.com/fapi/v1/fundingRate?symbol=%s&limit=%d" % (sym, n)):
                pairs.append((f(h.get("fundingTime")), f(h.get("fundingRate"))))
        except Exception:
            pass
        time.sleep(0.15)
        out[c] = {
            "venue": "binance", "interval_hours": hours,
            "funding_rate_now": f(r.get("lastFundingRate")),
            "funding_rate_avg7d": avg_recent(pairs),
            "history_points": len(pairs),
            "mark_price": mark, "index_price": index,
            "premium_pct": round((mark / index - 1.0) * 100, 4) if (mark and index) else None,
        }
    return out


def src_bybit():
    t = http_json("https://api.bybit.com/v5/market/tickers?category=linear")
    by_sym = {r["symbol"]: r for r in t.get("result", {}).get("list", [])}
    iv = {}
    try:
        info = http_json("https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000")
        for r in info.get("result", {}).get("list", []):
            m = f(r.get("fundingInterval"))
            if m:
                iv[r["symbol"]] = m / 60.0
    except Exception:
        pass
    out = {}
    for c in COINS:
        sym = c + "USDT"
        r = by_sym.get(sym)
        if not r:
            continue
        hours = iv.get(sym, 8.0)
        mark, index = f(r.get("markPrice")), f(r.get("indexPrice"))
        pairs = []
        try:
            n = int(round(AVG_DAYS * 24 / hours)) + 2
            hist = http_json("https://api.bybit.com/v5/market/funding/history?category=linear&symbol=%s&limit=%d" % (sym, min(n, 200)))
            for h in hist.get("result", {}).get("list", []):
                pairs.append((f(h.get("fundingRateTimestamp")), f(h.get("fundingRate"))))
        except Exception:
            pass
        time.sleep(0.15)
        out[c] = {
            "venue": "bybit", "interval_hours": hours,
            "funding_rate_now": f(r.get("fundingRate")),
            "funding_rate_avg7d": avg_recent(pairs),
            "history_points": len(pairs),
            "mark_price": mark, "index_price": index,
            "premium_pct": round((mark / index - 1.0) * 100, 4) if (mark and index) else None,
        }
    return out


SOURCES = [("binance", src_binance), ("bybit", src_bybit)]


# ----------------------------------------------------------------- 저장소 업로드

def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise RuntimeError("설정 파일이 없다: %s  (docs/KR_RELAY_SETUP.md 3단계)" % CONFIG_PATH)
    with open(CONFIG_PATH, encoding="utf-8") as fp:
        cfg = json.load(fp)
    for k in ("token", "repo"):
        if not cfg.get(k):
            raise RuntimeError("설정 파일에 %s 가 비어 있다" % k)
    cfg.setdefault("branch", "main")
    return cfg


def upload(cfg, payload):
    """GitHub Contents API 로 data/kr_funding.json 을 만들거나 덮어쓴다. git 설치 불필요."""
    url = "https://api.github.com/repos/%s/contents/%s" % (cfg["repo"], REMOTE_PATH)
    hdr = {"Authorization": "Bearer " + cfg["token"], "X-GitHub-Api-Version": "2022-11-28"}
    cur = http_json(url + "?ref=" + cfg["branch"], headers=hdr)
    body = {
        "message": "kr-relay %s" % payload["date"],
        "content": base64.b64encode(json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8")).decode("ascii"),
        "branch": cfg["branch"],
    }
    if cur and cur.get("sha"):
        body["sha"] = cur["sha"]
    res = http_json(url, headers=hdr, data=body, method="PUT")
    return (res or {}).get("commit", {}).get("sha")


# ----------------------------------------------------------------- 실행

def build():
    now_utc = datetime.now(timezone.utc)
    out = {
        "schema": "carrygate-kr-relay/1",
        "date": now_utc.astimezone(KST).strftime("%Y-%m-%d"),
        "generated_at_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_at_kst": now_utc.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S"),
        "venues": {}, "errors": {},
    }
    for name, fn in SOURCES:
        try:
            res = fn()
            if not res:
                raise RuntimeError("no rows")
            out["venues"][name] = res
        except Exception as e:
            out["errors"][name] = str(e)[:300]
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    no_upload = "--no-upload" in argv
    out = build()
    print("=== 한국 중계 수집 %s ===" % out["generated_at_kst"])
    for v, rows in out["venues"].items():
        for c, e in rows.items():
            avg = e["funding_rate_avg7d"]
            apr7 = avg * (24.0 / e["interval_hours"]) * 365.0 * 100.0 if avg is not None else None
            print("  %-8s %-5s 현재 %.5f%%  7일평균 %s  (정산 %gh, 이력 %d개)" % (
                v, c, (e["funding_rate_now"] or 0) * 100, ("연 %.2f%%" % apr7) if apr7 is not None else "없음",
                e["interval_hours"], e["history_points"]))
    if out["errors"]:
        print("오류:", json.dumps(out["errors"], ensure_ascii=False))
    with open(LOCAL_OUT, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    with open(LOCAL_HISTORY, "a", encoding="utf-8") as fp:
        fp.write(json.dumps({"date": out["date"], "generated_at_utc": out["generated_at_utc"],
                             "venues": {v: {c: e["funding_rate_avg7d"] for c, e in rows.items()} for v, rows in out["venues"].items()}},
                            ensure_ascii=False) + "\n")
    if not out["venues"]:
        print("두 거래소 모두 실패 — 업로드하지 않는다", file=sys.stderr)
        return 1
    if no_upload:
        print("업로드 생략 (--no-upload). 결과: %s" % LOCAL_OUT)
        return 0
    cfg = load_config()
    sha = upload(cfg, out)
    print("업로드 완료 → %s/%s (%s)" % (cfg["repo"], REMOTE_PATH, (sha or "")[:7]))
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception as e:
        print("실패:", e, file=sys.stderr)
        code = 1
    if os.name == "nt" and sys.stdin.isatty():
        input("아무 키나 누르면 닫힙니다...")
    sys.exit(code)
