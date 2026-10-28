"""
common.py — 여러 모듈이 똑같이 복사해 쓰던 작은 헬퍼를 한 곳에 모은다 (2026-10-28).

규칙 상수는 여기 없다. 여기 있는 것은 HTTP 요청·숫자 변환·반올림·평균·기록 파일 읽고 쓰기뿐이라
매매 판단과 무관하다. 바꾸면 모든 모듈에 한 번에 적용된다.

여기서 가져가지 않는 것(일부러 다르게 둔 복사본):
- orderbook.py `http_json` — 재시도 없이 바로 실패해야 초 단위 표본 간격이 안 밀린다.
- kr_universe.py `http_json`·`f`·`append_history` — KRX 25초 타임아웃, NaN 처리, (path, rec) 인자 순서.
- verdict.py `load_json` — 저장소 기준 경로, 깨진 JSON 은 숨기지 않고 오류를 낸다.
- kr_relay.py·vps_relay.py — 한국 PC·가상서버에서 이 저장소 없이 단독으로 돌아야 해서 복사본을 유지한다.
- kr_ohlcv.py `UA` — 네이버·야후용 브라우저 UA.
"""
import json
import math
import time
import urllib.request
from datetime import timedelta, timezone

KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (compatible; carrygate/1.0)",
      "Accept": "application/json"}


# ----------------------------------------------------------------- HTTP

def http_json(url, tries=3, data=None):
    """GET(또는 data 가 있으면 JSON POST) 뒤 JSON 을 돌려준다. 실패하면 1.5·3.5·5.5초 쉬며 tries 번 시도."""
    last = None
    for i in range(tries):
        try:
            body = json.dumps(data).encode("utf-8") if data is not None else None
            hdr = dict(UA)
            if body is not None:
                hdr["Content-Type"] = "application/json"
            req = urllib.request.Request(url, headers=hdr, data=body)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:          # 네트워크·레이트리밋 모두 여기로
            last = e
            time.sleep(1.5 + i * 2)
    raise RuntimeError("fetch failed %s : %s" % (url, last))


# ----------------------------------------------------------------- 숫자

def f(x):
    """문자열/None 안전 float 변환. 못 바꾸면 None."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def r2(x):
    return None if x is None else round(x, 2)


def r4(x):
    return None if x is None else round(x, 4)


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def std(xs):
    """모표준편차(n 으로 나눔). 표본 2개 미만이면 None."""
    if len(xs) < 2:
        return None
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def corr(xs, ys):
    """피어슨 상관. 표본 3개 미만이거나 한쪽 분산이 0이면 None."""
    if len(xs) < 3:
        return None
    mx, my, sx, sy = mean(xs), mean(ys), std(xs), std(ys)
    if not sx or not sy:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (len(xs) * sx * sy)


# ----------------------------------------------------------------- 파일

def load_json(path, default=None):
    """JSON 파일. 없거나 깨졌으면 default."""
    try:
        with open(path, encoding="utf-8") as fp:
            return json.load(fp)
    except (IOError, ValueError):
        return default


def append_history(rec, path):
    """하루 한 줄 jsonl. 같은 날짜가 이미 있으면 덮어쓴다 (수동 실행 뒤 정기 실행이 와도 중복되지 않게)."""
    try:
        with open(path, encoding="utf-8") as fp:
            rows = [l for l in fp.read().splitlines() if l.strip()]
    except IOError:
        rows = []
    rows = [l for l in rows if json.loads(l).get("date") != rec["date"]]
    rows.append(json.dumps(rec, ensure_ascii=False))
    with open(path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(rows) + "\n")


def load_history(path):
    """jsonl 전체를 날짜순 목록으로. 없으면 빈 목록, 깨진 줄은 건너뛴다."""
    rows = []
    try:
        with open(path, encoding="utf-8") as fp:
            for l in fp:
                l = l.strip()
                if l:
                    try:
                        rows.append(json.loads(l))
                    except ValueError:
                        pass
    except IOError:
        pass
    rows.sort(key=lambda r: r.get("date", ""))
    return rows
