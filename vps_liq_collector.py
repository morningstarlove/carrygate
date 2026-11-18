# -*- coding: utf-8 -*-
"""
CARRYGATE — 가상서버(VPS) 청산 수신기 (바이낸스 USDT 선물 강제청산 실시간 흐름 + BTC·ETH 초 단위 가격). 제안함 P40.

바이낸스는 강제청산 내역을 **실시간 흐름으로만** 내보낸다(과거치 없음). 이 파일을 사용자 가상서버에서 상시로 띄워
받은 것을 그대로 파일에 적는다. 매일 아침 Actions 의 liq_cascade.py 가 그 파일을 읽어 "큰 청산 뒤 수익"을 센다.

  - 읽기 전용. 거래소 키 없음. 주문 기능 없음. 누구나 볼 수 있는 공개 흐름만 받는다.
  - 이 파일 하나만 있으면 된다. 파이썬 3.8 이상. 외부 패키지 없음(웹소켓도 표준 라이브러리로 직접 구현).

  python3 vps_liq_collector.py --probe                        # 주소 후보를 15초씩 시험해 무엇이 오는지 보여 준다
  python3 vps_liq_collector.py --out-dir /var/www/carrygate/liq   # 상시 수신 (systemd 가 띄운다, scripts/vps_liq_install.sh)

쓰는 파일 (--out-dir, 날짜는 UTC):
  liq_YYYYMMDD.jsonl   한 줄 = 청산 1건  [받은 시각(초, 소수), 거래소 시각(ms), 종목, 방향(SELL=롱 청산), 금액(USDT), 평균 체결가]
  px_YYYYMMDD.csv      한 줄 = 1초      sec,btc_lo,btc_hi,btc_last,eth_lo,eth_hi,eth_last  (그 1초 안 최저·최고·마지막 체결가)
                       날이 바뀌면 px_YYYYMMDD.csv.gz 로 압축한다. KEEP_DAYS 일 지난 파일은 지운다.
  status.json          1분마다: 살아 있는지, 어떤 주소를 쓰는지, 오늘 몇 건 받았는지

주의: 바이낸스는 종목마다 1초에 청산 1건(그 1초 중 가장 큰 것)만 내보낸다 → 금액 합은 실제보다 작다.
주소는 2026-04 에 /market·/public 으로 나뉘었다. 어느 쪽이 살아 있는지 몰라도 되게 후보를 차례로 시도한다.
"""
import os, sys, ssl, json, time, gzip, glob, shutil, socket, struct, base64, threading
from datetime import datetime, timezone
from urllib.parse import urlsplit

SYMS = ["BTCUSDT", "ETHUSDT"]
KEEP_DAYS = 30
LIQ_URLS = [
    "wss://fstream.binance.com/market/ws/!forceOrder@arr",
    "wss://fstream.binance.com/ws/!forceOrder@arr",
    "wss://fstream.binance.com/public/ws/!forceOrder@arr",
]
PX_URLS = [
    "wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade/ethusdt@aggTrade",
    "wss://fstream.binance.com/public/stream?streams=btcusdt@bookTicker/ethusdt@bookTicker",
    "wss://fstream.binance.com/stream?streams=btcusdt@aggTrade/ethusdt@aggTrade",
    "wss://fstream.binance.com/public/stream?streams=btcusdt@aggTrade/ethusdt@aggTrade",
]
PX_SILENT_SEC = 20        # 가격이 이만큼 안 오면 다음 주소 후보로
LIQ_SILENT_SEC = 1800     # 전 종목 청산이 30분 동안 0건이면 주소를 의심한다
MAX_CONN_SEC = 23 * 3600  # 바이낸스는 24시간마다 끊는다. 먼저 다시 붙는다


# ----------------------------------------------------------------- 웹소켓 (RFC 6455 최소 구현)

class WSClosed(Exception):
    pass


class WS:
    def __init__(self, url, timeout=30):
        u = urlsplit(url)
        port = u.port or (443 if u.scheme == "wss" else 80)
        path = (u.path or "/") + ("?" + u.query if u.query else "")
        s = socket.create_connection((u.hostname, port), timeout=timeout)
        if u.scheme == "wss":
            s = ssl.create_default_context().wrap_socket(s, server_hostname=u.hostname)
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall(("GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\nUser-Agent: carrygate-liq/1.0\r\n\r\n"
                   % (path, u.hostname, key)).encode())
        self.s, self.buf = s, b""
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = s.recv(4096)
                if not chunk:
                    raise WSClosed("연결 직후 끊김")
                head += chunk
                if len(head) > 65536:
                    raise WSClosed("응답 머리가 너무 길다")
            head, self.buf = head.split(b"\r\n\r\n", 1)
            status = head.split(b"\r\n", 1)[0].decode("latin-1")
            if " 101" not in status:
                raise WSClosed("웹소켓 승격 실패: " + status)
        except Exception:
            s.close()
            raise

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.s.recv(65536)
            if not chunk:
                raise WSClosed("서버가 끊었다")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _send(self, opcode, payload=b""):
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        self.s.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def recv(self):
        """글 메시지 하나(str)를 돌려준다. 핑에는 퐁으로 답한다. 닫힘이면 WSClosed."""
        parts = []
        while True:
            b0, b1 = self._read(2)
            op, n = b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b1 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if op == 0x9:
                self._send(0xA, data)
            elif op == 0x8:
                raise WSClosed("서버가 닫았다")
            elif op in (0x0, 0x1, 0x2):
                parts.append(data)
                if b0 & 0x80:
                    return b"".join(parts).decode("utf-8", "replace")

    def close(self):
        try:
            self._send(0x8)
        except Exception:
            pass
        try:
            self.s.close()
        except Exception:
            pass


# ----------------------------------------------------------------- 메시지 해석

def parse_liq(msg):
    """청산 메시지(dict) → (거래소 시각 ms, 종목, 방향, 금액 USDT, 평균가) 또는 None. 묶음 흐름({"stream","data"})도 받는다."""
    d = msg.get("data", msg) if isinstance(msg, dict) else None
    o = d.get("o") if isinstance(d, dict) else None
    if not isinstance(o, dict) or d.get("e") != "forceOrder":
        return None
    try:
        qty = float(o.get("z") or 0) or float(o.get("q") or 0)
        px = float(o.get("ap") or 0) or float(o.get("p") or 0)
        return int(o.get("T") or d.get("E") or 0), str(o["s"]), str(o["S"]), round(qty * px, 2), px
    except (KeyError, TypeError, ValueError):
        return None


def parse_px(msg):
    """가격 메시지 → (종목, 낮은 값, 높은 값, 마지막 값) 또는 None. 체결(aggTrade)은 셋이 같고, 호가(bookTicker)는 매수호가·매도호가·중간."""
    d = msg.get("data", msg) if isinstance(msg, dict) else None
    if not isinstance(d, dict):
        return None
    try:
        if d.get("e") == "aggTrade":
            p = float(d["p"])
            return str(d["s"]), p, p, p
        if d.get("e") == "bookTicker" or ("b" in d and "a" in d and "s" in d):
            b, a = float(d["b"]), float(d["a"])
            return str(d["s"]), b, a, (a + b) / 2.0
    except (KeyError, TypeError, ValueError):
        return None
    return None


# ----------------------------------------------------------------- 수신기

class Collector:
    def __init__(self, out_dir):
        self.out = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.lock = threading.Lock()
        self.cur = {}                      # 종목 → [lo, hi, last] (지금 1초)
        self.last = {}                     # 종목 → 마지막 값 (체결 없는 초에 이어 쓴다)
        self.n_liq = self.n_px = 0
        self.url = {"liq": None, "px": None}
        self.last_msg = {"liq": 0.0, "px": 0.0}
        self.started = time.time()
        self.stop = False

    def day(self, ts):
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y%m%d")

    def on_liq(self, text):
        now = time.time()
        got = parse_liq(json.loads(text))
        if not got:
            return
        with self.lock:
            self.n_liq += 1
            self.last_msg["liq"] = now
            with open(os.path.join(self.out, "liq_%s.jsonl" % self.day(now)), "a", encoding="utf-8") as fp:
                fp.write(json.dumps([round(now, 3)] + list(got), separators=(",", ":")) + "\n")

    def on_px(self, text):
        got = parse_px(json.loads(text))
        if not got or got[0] not in SYMS:
            return
        s, lo, hi, last = got
        with self.lock:
            self.n_px += 1
            self.last_msg["px"] = time.time()
            c = self.cur.get(s)
            if c is None:
                self.cur[s] = [lo, hi, last]
            else:
                c[0], c[1], c[2] = min(c[0], lo), max(c[1], hi), last

    def run_stream(self, kind, urls, handler, silent_sec):
        i = 0
        while not self.stop:
            url, ws = urls[i % len(urls)], None
            try:
                ws = WS(url)
                ws.s.settimeout(silent_sec)
                self.url[kind] = url
                t0 = time.time()
                log("%s 연결: %s" % (kind, url))
                while not self.stop and time.time() - t0 < MAX_CONN_SEC:
                    handler(ws.recv())
            except (socket.timeout, TimeoutError):
                log("%s %d초 동안 아무것도 안 옴 → 다음 주소 후보" % (kind, silent_sec))
                i += 1
            except Exception as e:
                log("%s 끊김(%s: %s) → 5초 뒤 다시" % (kind, type(e).__name__, e))
                if self.last_msg[kind] == 0.0 or time.time() - self.last_msg[kind] > silent_sec:
                    i += 1                  # 이 주소로 받은 적이 없으면 다음 후보
                time.sleep(5)
            finally:
                if ws:
                    ws.close()

    def tick_loop(self):
        """1초마다 가격 한 줄을 쓴다. 날이 바뀌면 어제 파일을 압축하고 오래된 파일을 지운다."""
        sec = int(time.time())
        last_status = 0
        while not self.stop:
            time.sleep(max(0.0, sec + 1 - time.time()))
            with self.lock:
                snap, self.cur = self.cur, {}
                for s, c in snap.items():
                    self.last[s] = c[2]
            row = [str(sec)]
            for s in SYMS:
                c = snap.get(s) or ([self.last[s]] * 3 if s in self.last else None)
                row += ["%.2f" % v for v in c] if c else ["", "", ""]
            if len(self.last) == len(SYMS):
                with open(os.path.join(self.out, "px_%s.csv" % self.day(sec)), "a", encoding="utf-8") as fp:
                    fp.write(",".join(row) + "\n")
            if self.day(sec) != self.day(sec + 1):
                threading.Thread(target=self.rollover, args=(self.day(sec),), daemon=True).start()
            if sec - last_status >= 60:
                last_status = sec
                self.write_status(sec)
            sec += 1

    def rollover(self, done_day):
        src = os.path.join(self.out, "px_%s.csv" % done_day)
        try:
            if os.path.exists(src):
                with open(src, "rb") as a, gzip.open(src + ".gz.tmp", "wb", compresslevel=6) as b:
                    shutil.copyfileobj(a, b)
                os.replace(src + ".gz.tmp", src + ".gz")
                os.remove(src)
            cutoff = time.time() - KEEP_DAYS * 86400
            for f in glob.glob(os.path.join(self.out, "px_*")) + glob.glob(os.path.join(self.out, "liq_*")):
                if os.path.getmtime(f) < cutoff:
                    os.remove(f)
            log("날짜 넘김: %s 압축 완료" % done_day)
        except Exception as e:
            log("날짜 넘김 실패: %s" % e)

    def write_status(self, sec):
        st = {"schema": "carrygate-liq/1", "generated_at_utc": datetime.fromtimestamp(sec, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "uptime_sec": int(sec - self.started), "urls": self.url, "liq_received": self.n_liq, "px_received": self.n_px,
              "last_liq_age_sec": round(sec - self.last_msg["liq"], 1) if self.last_msg["liq"] else None,
              "last_px_age_sec": round(sec - self.last_msg["px"], 1) if self.last_msg["px"] else None}
        tmp = os.path.join(self.out, "status.json.tmp")
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(st, fp, ensure_ascii=False)
        os.replace(tmp, os.path.join(self.out, "status.json"))

    def run(self):
        threading.Thread(target=self.run_stream, args=("liq", LIQ_URLS, self.on_liq, LIQ_SILENT_SEC), daemon=True).start()
        threading.Thread(target=self.run_stream, args=("px", PX_URLS, self.on_px, PX_SILENT_SEC), daemon=True).start()
        self.tick_loop()


def log(msg):
    print("%s %s" % (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"), msg), flush=True)


def probe(seconds=15):
    """주소 후보마다 seconds 초 받아 보고 몇 건 왔는지 적는다. 가격 주소가 하나라도 되면 0 으로 끝난다."""
    ok_px = False
    for kind, urls, parse in (("가격", PX_URLS, parse_px), ("청산", LIQ_URLS, parse_liq)):
        for url in urls:
            n, sample, err = 0, None, None
            try:
                ws = WS(url, timeout=10)
                ws.s.settimeout(3)
                end = time.time() + seconds
                while time.time() < end:
                    try:
                        got = parse(json.loads(ws.recv()))
                    except (socket.timeout, TimeoutError):
                        continue
                    if got:
                        n += 1
                        sample = sample or got
                ws.close()
            except Exception as e:
                err = "%s: %s" % (type(e).__name__, e)
            print("[%s] %s\n      → %s" % (kind, url, ("실패 " + err) if err else ("연결 됨, %d초 동안 %d건%s" % (seconds, n, (" 예: %s" % (sample,)) if sample else ""))))
            if kind == "가격" and n > 0:
                ok_px = True
    print("\n가격 흐름: %s. 청산은 조용한 시간엔 15초 동안 0건일 수 있다(연결 됨이면 정상)." % ("정상" if ok_px else "전부 실패 — 바이낸스 접속이 막혔거나 주소가 바뀌었다"))
    return 0 if ok_px else 1


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--probe" in argv:
        return probe()
    out = argv[argv.index("--out-dir") + 1] if "--out-dir" in argv else os.path.join(os.path.dirname(os.path.abspath(__file__)), "liq")
    log("청산 수신기 시작 → %s" % out)
    Collector(out).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
