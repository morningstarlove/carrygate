# -*- coding: utf-8 -*-
"""
liq_cascade.py(P40 집계)·vps_liq_collector.py(가상서버 수신기) 검증 — 가짜 자료·로컬 웹소켓, 인터넷 없음.

  python3 -m unittest tests.test_liq_cascade -v
"""
import os, sys, json, gzip, time, socket, struct, base64, hashlib, tempfile, threading, unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import liq_cascade as L            # noqa: E402
import vps_liq_collector as C      # noqa: E402

D0 = int(datetime(2026, 11, 19, tzinfo=timezone.utc).timestamp())


def flat_px(eth=2000.0, btc=60000.0, n=86400):
    return {D0 + i: (btc, btc, btc, eth, eth, eth) for i in range(n)}


class Signals(unittest.TestCase):
    def test_window_sum_crosses_threshold(self):
        liqs = [(D0 + 10.2, "BTCUSDT", "SELL", 600_000.0), (D0 + 40.7, "BTCUSDT", "SELL", 500_000.0)]
        s = L.signals(liqs, 1_000_000, "SELL")
        self.assertEqual([x["t"] for x in s], [D0 + 41])            # 받은 시각을 초로 올림
        self.assertEqual(s[0]["sum_usdt"], 1_100_000)

    def test_old_amounts_fall_out_of_window(self):
        liqs = [(D0 + 10, "BTCUSDT", "SELL", 600_000.0), (D0 + 71, "BTCUSDT", "SELL", 500_000.0)]
        self.assertEqual(L.signals(liqs, 1_000_000, "SELL"), [])

    def test_other_symbol_and_side_ignored(self):
        liqs = [(D0 + 1, "ETHUSDT", "SELL", 9e6), (D0 + 2, "BTCUSDT", "BUY", 9e6)]
        self.assertEqual(L.signals(liqs, 1_000_000, "SELL"), [])
        self.assertEqual(len(L.signals(liqs, 1_000_000, "BUY")), 1)

    def test_cooldown(self):
        liqs = [(D0 + i, "BTCUSDT", "SELL", 2e6) for i in (0, 100, 299, 300, 650)]
        self.assertEqual([x["t"] for x in L.signals(liqs, 1_000_000, "SELL")], [D0, D0 + 300, D0 + 650])


class Trade(unittest.TestCase):
    def test_short_uses_adverse_prices(self):
        px = flat_px()
        px[D0 + 101] = (60000, 60000, 60000, 1990.0, 2000.0, 1995.0)     # 진입 초: 최저가에 판다
        px[D0 + 161] = (60000, 60000, 60000, 1970.0, 1980.0, 1975.0)     # 청산 초: 최고가에 되산다
        r = L.trade(px, D0 + 100, 60, -1, "ETHUSDT")
        self.assertAlmostEqual(r, -(1980.0 / 1990.0 - 1) * 100, places=9)

    def test_long_mirror_and_gap(self):
        px = flat_px()
        px[D0 + 101] = (0, 0, 0, 1990.0, 2000.0, 1995.0)
        px[D0 + 106] = (0, 0, 0, 2010.0, 2020.0, 2015.0)
        self.assertAlmostEqual(L.trade(px, D0 + 100, 5, 1, "ETHUSDT"), (2010.0 / 2000.0 - 1) * 100, places=9)
        for s in range(D0 + 101 - L.PX_GAP_SEC, D0 + 102):
            del px[s]
        self.assertIsNone(L.trade(px, D0 + 100, 5, 1, "ETHUSDT"))        # 가격이 없으면 그 건은 뺀다


class Day(unittest.TestCase):
    def test_evaluate_day_main_cell_and_fee(self):
        px = flat_px()
        for s in range(D0 + 1061, D0 + 3000):                            # 신호 60초 뒤부터 이더 −1%
            px[s] = (60000, 60000, 60000, 1980.0, 1980.0, 1980.0)
        liqs = [(D0 + 999.5, "BTCUSDT", "SELL", 1_500_000.0)]
        row = L.evaluate_day("20261119", liqs, px)
        self.assertTrue(row["counted"])
        c = row["cells"]["eth_short|1000000|60"]
        self.assertEqual(c["n"], 1)
        self.assertAlmostEqual(c["gross_sum"], 1.0, places=3)
        self.assertAlmostEqual(c["net_sum"], 1.0 - L.FEE_RT_PCT, places=3)
        self.assertEqual(row["cells"]["eth_short|3000000|60"]["n"], 0)   # 300만 문턱은 안 넘음
        self.assertEqual(row["cells"]["eth_short|1000000|5"]["wins"], 0)  # 5초 뒤는 제자리 → 수수료만큼 손실
        self.assertEqual(len(row["main_trades"]), 1)

    def test_low_coverage_not_counted_and_late_signal_dropped(self):
        row = L.evaluate_day("20261119", [], flat_px(n=40000))
        self.assertFalse(row["counted"])
        liqs = [(D0 + 86399, "BTCUSDT", "SELL", 5e6)]
        self.assertEqual(L.evaluate_day("20261119", liqs, flat_px())["cells"]["eth_short|1000000|60"]["n"], 0)

    def test_build_from_dir_and_judge(self):
        with tempfile.TemporaryDirectory() as d:
            lines = "\n".join("%d,60000.00,60000.00,60000.00,2000.00,2000.00,2000.00" % (D0 + i) for i in range(86400))
            with gzip.open(os.path.join(d, "px_20261119.csv.gz"), "wb") as fp:
                fp.write(lines.encode())
            with open(os.path.join(d, "liq_20261119.jsonl"), "w") as fp:
                fp.write(json.dumps([D0 + 500.1, (D0 + 500) * 1000, "BTCUSDT", "SELL", 2e6, 60000.0]) + "\n")
            now = datetime(2026, 11, 20, 0, 30, tzinfo=timezone.utc)
            out, new = L.build(d, now=now, hist_path=os.path.join(d, "h.jsonl"))
        self.assertEqual([r["date"] for r in new], ["2026-11-19"])
        self.assertEqual(out["days_counted"], 1)
        self.assertEqual(out["judge"]["state"], "기록 중")
        self.assertEqual(sorted(out["errors"]), ["20261116", "20261117", "20261118"])     # 없는 날은 오류로만 남는다
        self.assertAlmostEqual(out["cumulative"]["eth_short|1000000|60"]["avg_net_pct"], -L.FEE_RT_PCT, places=4)

    def test_judge_thresholds(self):
        k = "eth_short|%d|%d" % (L.MAIN_TH, L.MAIN_HOLD)
        self.assertEqual(L.judge(14, {k: {"n": 10}})["state"], "판단 보류")
        self.assertEqual(L.judge(14, {k: {"n": 40, "avg_net_pct": 0.05, "win_pct": 55.0}})["state"], "후보")
        self.assertEqual(L.judge(14, {k: {"n": 40, "avg_net_pct": 0.05, "win_pct": 45.0}})["state"], "탈락")
        self.assertEqual(L.judge(14, {k: {"n": 40, "avg_net_pct": -0.01, "win_pct": 60.0}})["state"], "탈락")

    def test_rules_dict_has_fixed_numbers(self):
        r = L.rules_dict()
        self.assertEqual((r["main_threshold_usdt"], r["main_hold_sec"], r["fee_round_trip_pct"]), (1_000_000, 60, 0.10))


class CollectorParse(unittest.TestCase):
    LIQ = {"e": "forceOrder", "E": 1568014460893, "o": {"s": "BTCUSDT", "S": "SELL", "o": "LIMIT", "f": "IOC", "q": "0.014", "p": "9910",
                                                         "ap": "9900", "X": "FILLED", "l": "0.014", "z": "0.010", "T": 1568014460893}}

    def test_parse_liq_raw_and_combined(self):
        want = (1568014460893, "BTCUSDT", "SELL", 99.0, 9900.0)
        self.assertEqual(C.parse_liq(self.LIQ), want)
        self.assertEqual(C.parse_liq({"stream": "!forceOrder@arr", "data": self.LIQ}), want)
        self.assertIsNone(C.parse_liq({"e": "aggTrade"}))

    def test_parse_px(self):
        self.assertEqual(C.parse_px({"data": {"e": "aggTrade", "s": "ETHUSDT", "p": "1810.5"}}), ("ETHUSDT", 1810.5, 1810.5, 1810.5))
        self.assertEqual(C.parse_px({"e": "bookTicker", "s": "BTCUSDT", "b": "100", "a": "102"}), ("BTCUSDT", 100.0, 102.0, 101.0))
        self.assertIsNone(C.parse_px({"result": None, "id": 1}))


def _frame(op, payload):
    n = len(payload)
    head = bytes([0x80 | op]) + (bytes([n]) if n < 126 else bytes([126]) + struct.pack(">H", n))
    return head + payload


def fake_server(messages, got_pong):
    """로컬 웹소켓 서버: 승격 → 핑 → 메시지들 → 닫기. (포트, 스레드) 를 돌려준다."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def run():
        c, _ = srv.accept()
        req = b""
        while b"\r\n\r\n" not in req:
            req += c.recv(4096)
        key = [l.split(b": ")[1] for l in req.split(b"\r\n") if l.lower().startswith(b"sec-websocket-key")][0]
        acc = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
        c.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + acc + b"\r\n\r\n")
        c.sendall(_frame(0x9, b"hi"))
        for m in messages:
            c.sendall(_frame(0x1, m.encode()))
        c.settimeout(3)
        try:
            h = c.recv(2)                                   # 클라이언트 퐁 (마스크됨)
            mask = c.recv(4)
            body = c.recv(h[1] & 0x7F)
            got_pong.append(h[0] & 0x0F == 0xA and bytes(b ^ mask[i % 4] for i, b in enumerate(body)) == b"hi")
        except Exception:
            got_pong.append(False)
        c.sendall(_frame(0x8, b""))
        c.close()
        srv.close()

    th = threading.Thread(target=run, daemon=True)
    th.start()
    return srv.getsockname()[1], th


class WebSocketLoopback(unittest.TestCase):
    def test_handshake_ping_pong_text_long_frame_close(self):
        big = json.dumps({"e": "aggTrade", "s": "ETHUSDT", "p": "1800.25", "pad": "x" * 300})      # 126바이트 넘는 프레임
        pong = []
        port, th = fake_server([json.dumps(CollectorParse.LIQ), big], pong)
        ws = C.WS("ws://127.0.0.1:%d/market/ws/!forceOrder@arr" % port, timeout=5)
        self.assertEqual(C.parse_liq(json.loads(ws.recv()))[1], "BTCUSDT")
        self.assertEqual(C.parse_px(json.loads(ws.recv()))[1], 1800.25)
        with self.assertRaises(C.WSClosed):
            ws.recv()
        ws.close()
        th.join(5)
        self.assertEqual(pong, [True])

    def test_bad_path_raises(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)

        def run():
            c, _ = srv.accept()
            c.recv(4096)
            c.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
            c.close()
        threading.Thread(target=run, daemon=True).start()
        with self.assertRaises(C.WSClosed):
            C.WS("ws://127.0.0.1:%d/x" % srv.getsockname()[1], timeout=5)
        srv.close()


class CollectorFiles(unittest.TestCase):
    def test_writes_liq_and_px_rows_readable_by_analysis(self):
        with tempfile.TemporaryDirectory() as d:
            col = C.Collector(d)
            col.on_liq(json.dumps(CollectorParse.LIQ))
            col.on_px(json.dumps({"e": "aggTrade", "s": "BTCUSDT", "p": "60000"}))
            col.on_px(json.dumps({"e": "aggTrade", "s": "ETHUSDT", "p": "1800"}))
            col.on_px(json.dumps({"e": "aggTrade", "s": "ETHUSDT", "p": "1790"}))
            th = threading.Thread(target=col.tick_loop, daemon=True)
            th.start()
            time.sleep(2.3)
            col.stop = True
            th.join(3)
            day = col.day(time.time())
            liqs = L.parse_liq_lines(open(os.path.join(d, "liq_%s.jsonl" % day)).read())
            px = L.parse_px(open(os.path.join(d, "px_%s.csv" % day)).read())
            self.assertTrue(os.path.exists(os.path.join(d, "status.json")))
        self.assertEqual((liqs[0][1], liqs[0][2], liqs[0][3]), ("BTCUSDT", "SELL", 99.0))
        first = px[min(px)]
        self.assertEqual(first, (60000.0, 60000.0, 60000.0, 1790.0, 1800.0, 1790.0))      # 그 1초의 최저·최고·마지막
        self.assertGreaterEqual(len(px), 2)                                                 # 체결 없는 초는 마지막 값으로 이어 쓴다

    def test_rollover_gzips(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "px_20261119.csv"), "w").write("%d,1.00,1.00,1.00,2.00,2.00,2.00\n" % D0)
            C.Collector(d).rollover("20261119")
            self.assertFalse(os.path.exists(os.path.join(d, "px_20261119.csv")))
            _, px = L.load_day("20261119", d)
        self.assertEqual(px[D0], (1.0, 1.0, 1.0, 2.0, 2.0, 2.0))


if __name__ == "__main__":
    unittest.main()
