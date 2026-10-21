# -*- coding: utf-8 -*-
"""
CARRYGATE — 가상서버(VPS) 중계 수집기 (바이낸스 선물 1분봉)

GitHub Actions(미국 서버)는 바이낸스 API 가 막혀 있다(451). 이 파일을 **미국 밖에 있는 가상서버**에서 하루 한 번
돌리면 바이낸스 USDT 선물 1분봉(최근 4일)을 읽어 저장소의 data/binance_1m/<코인>.json 에 올린다.
매일 아침 Actions 의 scalp.py 는 이 파일이 있으면 그것을 쓰고, 없거나 오래됐으면 공개 일별 파일(data.binance.vision)로 받는다.

이 파일 하나만 있으면 된다(저장소의 다른 파일에 의존하지 않는다). 파이썬 3.8 이상. 외부 패키지 없음.

  python vps_relay.py              # 수집 + 저장소 업로드 (옆에 vps_relay_config.json 필요)
  python vps_relay.py --no-upload  # 수집만 하고 옆 폴더 data/binance_1m/ 에 저장 (시험용)
  python vps_relay.py --days 2     # 최근 2일만 (기본 4일: 평가 하루 + 지표 준비 60시간)

vps_relay_config.json (이 파일과 같은 폴더):
  {"token": "github_pat_...", "repo": "morningstarlove/carrygate", "branch": "main"}

토큰은 GitHub 의 Fine-grained personal access token 으로, **이 저장소 하나에만 Contents: Read and write** 권한.
거래소 API 키는 쓰지 않는다 — 바이낸스에는 누구나 볼 수 있는 공개 시세만 읽는다. 주문 기능 없음.
설정 절차: docs/VPS_SETUP.md
"""
import os, sys, json, time, base64, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

COINS = ["BTC", "ETH", "XRP", "TRX", "LINK", "DOGE"]
KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (compatible; carrygate-vps-relay/1.0)", "Accept": "application/json"}
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "vps_relay_config.json")
LOCAL_DIR = os.path.join(HERE, "data", "binance_1m")
REMOTE_DIR = "data/binance_1m"
KLINES = "https://fapi.binance.com/fapi/v1/klines?symbol=%sUSDT&interval=1m&startTime=%d&endTime=%d&limit=1500"
DAYS = 4


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
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404 and method in (None, "GET") and "api.github.com" in url:
                return None                      # 아직 없는 파일 — 새로 만든다
            if e.code == 451:
                raise RuntimeError("바이낸스가 이 서버 위치를 막았다(451). 미국 밖 서버가 필요하다")
            last = "HTTP %d" % e.code
        except Exception as e:
            last = e
        time.sleep(1.5 + i * 2)
    raise RuntimeError("fetch failed %s : %s" % (url, last))


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def fetch_coin(coin, start, end):
    """[start, end) 초 단위 구간의 1분봉을 [t,o,h,l,c,v] 로. 한 번에 1500봉."""
    out, frm = [], start * 1000
    while frm < end * 1000:
        rows = http_json(KLINES % (coin, frm, end * 1000 - 1))
        if not isinstance(rows, list) or not rows:
            break
        for r in rows:
            out.append([int(r[0]) // 1000, f(r[1]), f(r[2]), f(r[3]), f(r[4]), f(r[5])])
        frm = int(rows[-1][0]) + 60000
        if len(rows) < 1500:
            break
        time.sleep(0.15)
    seen, uniq = set(), []
    for b in out:
        if b[0] not in seen and start <= b[0] < end:
            seen.add(b[0]); uniq.append(b)
    uniq.sort(key=lambda b: b[0])
    return uniq


def build(days=DAYS, coins=COINS):
    now = datetime.now(timezone.utc)
    end = int(now.replace(second=0, microsecond=0).timestamp())
    start = end - days * 86400
    files, errors = {}, {}
    for c in coins:
        try:
            bars = fetch_coin(c, start, end)
            if len(bars) < days * 1440 // 2:
                raise RuntimeError("봉 %d개뿐" % len(bars))
            files[c] = {"schema": "carrygate-binance-1m/1", "venue": "binance", "coin": c,
                        "generated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "from_utc": datetime.fromtimestamp(bars[0][0], timezone.utc).strftime("%Y-%m-%d %H:%M"),
                        "to_utc": datetime.fromtimestamp(bars[-1][0], timezone.utc).strftime("%Y-%m-%d %H:%M"),
                        "bars": bars}
        except Exception as e:
            errors[c] = str(e)[:200]
        time.sleep(0.2)
    return files, errors


def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise RuntimeError("설정 파일이 없다: %s  (docs/VPS_SETUP.md 3단계)" % CONFIG_PATH)
    with open(CONFIG_PATH, encoding="utf-8") as fp:
        cfg = json.load(fp)
    for k in ("token", "repo"):
        if not cfg.get(k):
            raise RuntimeError("설정 파일에 %s 가 비어 있다" % k)
    cfg.setdefault("branch", "main")
    return cfg


def upload(cfg, coin, payload):
    """GitHub Contents API 로 data/binance_1m/<코인>.json 을 만들거나 덮어쓴다. git 설치 불필요."""
    url = "https://api.github.com/repos/%s/contents/%s/%s.json" % (cfg["repo"], REMOTE_DIR, coin)
    hdr = {"Authorization": "Bearer " + cfg["token"], "X-GitHub-Api-Version": "2022-11-28"}
    cur = http_json(url + "?ref=" + cfg["branch"], headers=hdr)
    body = {"message": "vps-relay binance %s %s" % (coin, payload["to_utc"][:10]),
            "content": base64.b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii"),
            "branch": cfg["branch"]}
    if cur and cur.get("sha"):
        body["sha"] = cur["sha"]
    res = http_json(url, headers=hdr, data=body, method="PUT")
    return (res or {}).get("commit", {}).get("sha")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    no_upload = "--no-upload" in argv
    days = int(argv[argv.index("--days") + 1]) if "--days" in argv else DAYS
    files, errors = build(days)
    print("=== 가상서버 중계 수집 %s KST ===" % datetime.now(KST).strftime("%Y-%m-%d %H:%M"))
    for c, p in files.items():
        print("  binance %-5s 1분봉 %5d개  %s ~ %s UTC" % (c, len(p["bars"]), p["from_utc"], p["to_utc"]))
    for c, e in errors.items():
        print("  binance %-5s 실패: %s" % (c, e))
    os.makedirs(LOCAL_DIR, exist_ok=True)
    for c, p in files.items():
        with open(os.path.join(LOCAL_DIR, c + ".json"), "w", encoding="utf-8") as fp:
            json.dump(p, fp, separators=(",", ":"))
    if not files:
        print("모든 코인 실패 — 업로드하지 않는다", file=sys.stderr)
        return 1
    if no_upload:
        print("업로드 생략 (--no-upload). 결과: %s" % LOCAL_DIR)
        return 0
    cfg = load_config()
    for c, p in files.items():
        sha = upload(cfg, c, p)
        print("  업로드 완료 → %s/%s/%s.json (%s)" % (cfg["repo"], REMOTE_DIR, c, (sha or "")[:7]))
        time.sleep(0.5)
    return 0 if not errors else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("[실패] %s" % e, file=sys.stderr)
        sys.exit(1)
