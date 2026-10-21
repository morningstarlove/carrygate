# -*- coding: utf-8 -*-
"""
scalp.py 계산 로직 검증 — 외부 접속 없이 가짜 데이터로 돌린다.

  python3 -m unittest tests.test_scalp -v
"""
import json, math, os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scalp  # noqa: E402


def mk(prices, vols=None):
    """종가 목록 -> 봉 목록. 시가는 직전 종가, 고저는 둘의 최대/최소."""
    bars, prev = [], prices[0]
    for i, c in enumerate(prices):
        v = vols[i] if vols else 1.0
        bars.append(scalp.bar(i * 60, prev, max(prev, c), min(prev, c), c, v))
        prev = c
    return bars


FEE = {"taker_pct": 0.05, "maker_pct": 0.02, "short_ok": True, "fee_source": "test", "kind": "perp"}
# 참고: 시장가 체결 1회 비용 = taker + 호가폭/2, 지정가 = maker


class FillTiming(unittest.TestCase):
    def test_entry_uses_next_bar_open(self):
        # 300봉 평탄 -> 한 번 급락(z<-2) -> 회복. 진입은 신호 다음 봉 시가여야 한다.
        px = [100.0] * 300 + [90.0] + [100.0] * 50
        # 평탄 구간은 표준편차 0 이라 z 가 없다. 미세한 흔들림을 준다.
        px = [p + (0.01 if i % 2 else -0.01) for i, p in enumerate(px)]
        ctx = scalp.Ctx(mk(px), eval_start=240)
        st = next(s for s in scalp.STRATEGIES if s["key"] == "mr_z20")
        tr = scalp.backtest(ctx, st, True)
        self.assertTrue(tr, "급락 뒤 평균회귀 매수가 있어야 한다")
        t = tr[0]
        self.assertEqual(t["side"], 1)
        # 신호 봉(300)의 종가 90 -> 다음 봉(301)의 시가 = 직전 종가 90 에 체결
        self.assertEqual(t["entry_i"], 301)
        self.assertAlmostEqual(ctx.o[301], px[300])
        self.assertGreater(t["gross_pct"], 5.0)   # 90 -> 100 근처 회복

    def test_no_entries_before_eval_window(self):
        px = [100.0 + math.sin(i / 3.0) * 3 for i in range(600)]
        ctx = scalp.Ctx(mk(px), eval_start=400)
        st = next(s for s in scalp.STRATEGIES if s["key"] == "mr_z20")
        for t in scalp.backtest(ctx, st, True):
            self.assertGreaterEqual(t["entry_i"], 400)

    def test_spot_never_shorts(self):
        px = [100.0 + math.sin(i / 3.0) * 3 for i in range(600)]
        ctx = scalp.Ctx(mk(px), eval_start=240)
        for st in scalp.STRATEGIES:
            for t in scalp.backtest(ctx, st, short_ok=False):
                self.assertEqual(t["side"], 1, st["key"])

    def test_max_hold_respected(self):
        px = [100.0 + math.sin(i / 7.0) * 2 for i in range(900)]
        ctx = scalp.Ctx(mk(px), eval_start=240)
        for st in scalp.STRATEGIES:
            for t in scalp.backtest(ctx, st, True):
                self.assertLessEqual(t["hold"], st["max_hold"], st["key"])


class Arithmetic(unittest.TestCase):
    def test_net_equals_gross_minus_fees(self):
        mk_t = lambda side, ei, xi, g, forced=False, em=False, xm=False: {
            "side": side, "entry_i": ei, "exit_i": xi, "hold": xi - ei, "gross_pct": g,
            "forced": forced, "entry_maker": em, "exit_maker": xm}
        trades = [mk_t(1, 10, 15, 0.3), mk_t(-1, 20, 22, -0.1), mk_t(1, 40, 41, 0.05, forced=True)]
        # 시장가 체결 1회 = 0.05 + 0.01/2 = 0.055 -> 왕복 0.11
        s = scalp.summarize(trades, FEE, spread_pct=0.01, mid_i=30)
        self.assertEqual(s["trades"], 3)
        self.assertAlmostEqual(s["gross_pct"], 0.25)
        self.assertAlmostEqual(s["fee_pct"], 0.33)
        self.assertAlmostEqual(s["net_pct"], 0.25 - 0.33)
        self.assertAlmostEqual(s["net_if_all_maker_pct"], 0.25 - 0.12)
        self.assertEqual(s["maker_sides"], 0)
        self.assertEqual(s["taker_sides"], 6)
        self.assertAlmostEqual(s["first_half_net_pct"] + s["second_half_net_pct"], s["net_pct"])
        self.assertEqual(s["forced_close"], 1)
        self.assertEqual(s["longs"], 2)
        self.assertEqual(s["shorts"], 1)
        self.assertEqual(s["win_rate_pct"], round(100.0 / 3, 1))   # 0.3-0.11 만 플러스

    def test_mixed_maker_taker_fees(self):
        trades = [{"side": 1, "entry_i": 10, "exit_i": 15, "hold": 5, "gross_pct": 0.3, "forced": False,
                   "entry_maker": True, "exit_maker": False}]
        s = scalp.summarize(trades, FEE, spread_pct=0.01, mid_i=30)
        # 진입 지정가 0.02 + 청산 시장가 (0.05 + 0.005) = 0.075
        self.assertAlmostEqual(s["fee_pct"], 0.075)
        self.assertAlmostEqual(s["net_pct"], 0.3 - 0.075)
        self.assertEqual(s["maker_sides"], 1)
        self.assertEqual(s["taker_sides"], 1)

    def test_empty_trades(self):
        s = scalp.summarize([], FEE, 0.01, 0)
        self.assertEqual(s["trades"], 0)
        self.assertEqual(s["net_pct"], 0.0)
        self.assertIsNone(s["win_rate_pct"])

    def test_hurdle_needed_win_rate(self):
        # 1분마다 정확히 ±0.05% 움직이는 시장, 왕복 비용 0.1% -> 필요 승률 (1+0.1/0.05)/2 = 150%
        px, p = [], 100.0
        for i in range(500):
            p = p * (1.0005 if i % 2 else 1 / 1.0005)
            px.append(p)
        ctx = scalp.Ctx(mk(px), eval_start=100)
        h = scalp.hurdle(ctx, 0.1)
        self.assertAlmostEqual(h["h1"]["mean_abs_move_pct"], 0.05, places=3)
        self.assertAlmostEqual(h["h1"]["needed_win_rate_pct"], 150.0, places=0)
        self.assertEqual(h["h1"]["share_above_cost_pct"], 0.0)

    def test_tick_estimate(self):
        bars = mk([133.0, 134.0, 133.0, 135.0, 134.0])
        self.assertEqual(scalp.tick_estimate(bars), 1.0)
        bars = mk([113397000.0, 113398000.0, 113395000.0])
        self.assertEqual(scalp.tick_estimate(bars), 1000.0)

    def test_max_drawdown(self):
        self.assertAlmostEqual(scalp.max_drawdown([1, -2, 1, -3, 5]), -4.0)
        self.assertAlmostEqual(scalp.max_drawdown([1, 1, 1]), 0.0)


class Behaviour(unittest.TestCase):
    def test_mean_reversion_wins_on_spike_and_recover_market(self):
        # 평탄한 시장에 40봉마다 급락이 오고 10봉에 걸쳐 회복한다 (급락 = z<-2, 회복 = 이익)
        px = []
        for i in range(1200):
            k = i % 40
            dip = -3.0 * (1 - k / 10.0) if k < 10 else 0.0
            px.append(100.0 + dip + (0.03 if i % 2 else -0.03))
        ctx = scalp.Ctx(mk(px), eval_start=240)
        st = next(s for s in scalp.STRATEGIES if s["key"] == "mr_z20")
        tr = scalp.backtest(ctx, st, True)
        self.assertGreater(len(tr), 10)
        self.assertGreater(sum(t["gross_pct"] for t in tr), 0)

    def test_breakout_wins_on_trending_market(self):
        px = [100.0 * (1.0005 ** i) + (0.02 if i % 2 else -0.02) for i in range(600)]
        ctx = scalp.Ctx(mk(px), eval_start=240)
        st = next(s for s in scalp.STRATEGIES if s["key"] == "brk_20")
        tr = scalp.backtest(ctx, st, True)
        self.assertTrue(tr)
        self.assertGreater(sum(t["gross_pct"] for t in tr), 0)
        self.assertTrue(all(t["side"] == 1 for t in tr))

    def test_evaluate_marks_costs_and_robustness(self):
        px = [100.0 + math.sin(i / 4.0) * 2 for i in range(1200)]
        rows, cost, hz = scalp.evaluate("okx", "BTC", mk(px), 240, FEE)
        # 1200봉 = 1분봉 1200 / 5분봉 240 / 15분봉 80 -> 세 시간 단위 모두 평가된다
        self.assertEqual(len(rows), len(scalp.STRATEGIES) * len(scalp.TIMEFRAMES))
        self.assertEqual(sorted(set(r["tf"] for r in rows)), sorted("%dm" % tf for tf in scalp.TIMEFRAMES))
        self.assertAlmostEqual(cost["round_trip_maker_pct"], 0.04)
        self.assertGreaterEqual(cost["round_trip_taker_pct"], 0.10)
        self.assertIn("h5", hz)
        for r in rows:
            per_taker = FEE["taker_pct"] + cost["spread_pct_est"] / 2.0
            fee = r["maker_sides"] * FEE["maker_pct"] + r["taker_sides"] * per_taker
            self.assertAlmostEqual(r["fee_pct"], fee, places=3, msg=r["strategy"])
            self.assertAlmostEqual(r["net_pct"], r["gross_pct"] - r["fee_pct"], places=3)
            if r["exec"] == "market":
                self.assertEqual(r["maker_sides"], 0)
            if r["robust"]:
                self.assertGreater(r["net_pct"], 0)
                self.assertGreaterEqual(r["trades"], 10)
                self.assertTrue(r["beats_baseline"])


class Resample(unittest.TestCase):
    def test_five_minute_bars(self):
        bars = [scalp.bar(i * 60, 100 + i, 101 + i, 99 + i, 100.5 + i, 1.0) for i in range(10)]
        tb = scalp.resample(bars, 5)
        self.assertEqual(len(tb), 2)
        self.assertEqual(tb[0]["t"], 0)
        self.assertEqual(tb[0]["o"], 100)          # 첫 봉 시가
        self.assertEqual(tb[0]["c"], 104.5)        # 마지막 봉 종가
        self.assertEqual(tb[0]["h"], 105)          # 최고
        self.assertEqual(tb[0]["l"], 99)           # 최저
        self.assertEqual(tb[0]["v"], 5.0)          # 합계
        self.assertEqual(tb[1]["t"], 300)
        self.assertIs(scalp.resample(bars, 1), bars)

    def test_gap_minutes_do_not_create_empty_bars(self):
        bars = [scalp.bar(0, 1, 1, 1, 1, 1), scalp.bar(60 * 12, 2, 2, 2, 2, 1)]
        tb = scalp.resample(bars, 5)
        self.assertEqual([b["t"] for b in tb], [0, 600])


class LimitOrders(unittest.TestCase):
    def setUp(self):
        # 5봉에 걸쳐 급락하고 5봉에 걸쳐 회복하는 V자 시장(40봉 주기).
        # 급락 도중 신호가 나므로 다음 봉 저가가 주문가 아래로 내려가 지정가가 체결된다.
        px = []
        for i in range(600):
            k = i % 40
            dip = -3.0 * (k + 1) / 5.0 if k < 5 else (-3.0 * (10 - k) / 5.0 if k < 10 else 0.0)
            px.append(100.0 + dip + (0.03 if i % 2 else -0.03))
        self.bars = mk(px)
        self.ctx = scalp.Ctx(self.bars, eval_start=240)
        self.st = next(s for s in scalp.STRATEGIES if s["key"] == "mr_z20_lim")

    def test_limit_fill_requires_price_through(self):
        # 매수 지정가: 저가가 주문가보다 낮아야 체결. 같으면 미체결.
        ctx = scalp.Ctx(mk([100.0, 100.0, 100.0]), 0)
        ctx.l[1] = 99.0
        self.assertTrue(scalp.limit_filled(ctx, 1, 1, 99.5))
        self.assertFalse(scalp.limit_filled(ctx, 1, 1, 99.0))
        ctx.h[1] = 101.0
        self.assertTrue(scalp.limit_filled(ctx, 1, -1, 100.5))
        self.assertFalse(scalp.limit_filled(ctx, 1, -1, 101.0))

    def test_limit_entries_are_maker_and_fill_at_limit_price(self):
        tr = scalp.backtest(self.ctx, self.st, True)
        self.assertTrue(tr)
        for t in tr:
            self.assertTrue(t["entry_maker"])
            self.assertLessEqual(t["hold"], self.st["max_hold"])
        # 지정가 진입 가격은 어떤 봉의 종가여야 한다 (신호 봉 종가에 주문)
        closes = set(self.ctx.c)
        for t in tr:
            entry_px = self.ctx.c[t["entry_i"] - 1]
            # 체결 봉의 저가가 주문가보다 낮다 (지나쳤다)
            self.assertLess(self.ctx.l[t["entry_i"]], entry_px + 1e-9)

    def test_limit_variant_pays_less_fee_than_market_variant(self):
        mkt = next(s for s in scalp.STRATEGIES if s["key"] == "mr_z20")
        a = scalp.summarize(scalp.backtest(self.ctx, mkt, True), FEE, 0.01, 400)
        b = scalp.summarize(scalp.backtest(self.ctx, self.st, True), FEE, 0.01, 400)
        self.assertEqual(a["maker_sides"], 0)
        self.assertGreater(b["maker_sides"], 0)
        self.assertLess(b["fee_pct"] / max(b["trades"], 1), a["fee_pct"] / max(a["trades"], 1))

    def test_no_fill_in_flat_market(self):
        # 가격이 전혀 움직이지 않으면 지정가는 절대 체결되지 않는다
        px = [100.0 + (0.01 if i % 2 else -0.01) for i in range(400)]
        ctx = scalp.Ctx(mk(px), 240)
        for t in scalp.backtest(ctx, self.st, True):
            self.assertLess(ctx.l[t["entry_i"]], ctx.c[t["entry_i"] - 1])


class HistoryFile(unittest.TestCase):
    def test_same_date_overwrites(self):
        out = {"date": "2026-09-29", "eval_day_utc": "2026-09-28", "summary": {"rows_total": 1},
               "rows": [{"venue": "okx", "coin": "BTC", "tf": "1m", "strategy": "mr_z20", "trades": 3,
                         "gross_pct": 0.1, "fee_pct": 0.3, "net_pct": -0.2, "robust": False}],
               "markets": {"okx:BTC": {"hurdle": {"h5": {"needed_win_rate_pct": 120.0}, "h1": {"needed_win_rate_pct": 150.0}},
                                       "cost": {"round_trip_taker_pct": 0.11}}}}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "h.jsonl")
            scalp.write_history(out, p)
            scalp.write_history(out, p)
            out2 = dict(out, date="2026-09-30")
            scalp.write_history(out2, p)
            rows = scalp.load_history(p)
            self.assertEqual([r["date"] for r in rows], ["2026-09-29", "2026-09-30"])
            self.assertEqual(rows[0]["rows"]["okx:BTC:1m:mr_z20"]["nt"], -0.2)
            self.assertEqual(rows[0]["hurdle"]["okx:BTC"]["h5_needed_win_rate_pct"], 120.0)
            self.assertEqual(rows[0]["rows"]["okx:BTC:1m:mr_z20"]["f"], 0.3)


class BackfillDay(unittest.TestCase):
    """--day 로 지난 날을 되채울 때 평가일·기록 날짜가 그 날짜 기준으로 잡히는지 (수집은 비워 두고 날짜 계산만 본다)."""

    def test_day_sets_eval_and_record_dates(self):
        import io, contextlib
        saved = (scalp.SOURCES, scalp.hl_live_fees)
        scalp.SOURCES, scalp.hl_live_fees = [], lambda: None
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                scalp.main(["--day", "2026-10-03", "--dry-run", "--save-bars", ""])
        finally:
            scalp.SOURCES, scalp.hl_live_fees = saved
        out = buf.getvalue()
        self.assertIn("(2026-10-04, 평가일 2026-10-03 UTC)", out)   # 기록 날짜 = 평가일 다음 날
        self.assertIn("데이터 없음", out)


class BinanceSource(unittest.TestCase):
    """바이낸스 공개 일별 파일(zip 속 CSV) → 1분봉, --venues 로 따로 쌓기 (제안함 P20)."""

    def _zip(self, rows, header=True):
        import io as _io, zipfile
        lines = (["open_time,open,high,low,close,volume,close_time"] if header else []) + \
                [",".join(str(x) for x in r) for r in rows]
        buf = _io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("X-1m.csv", "\n".join(lines))
        return buf.getvalue()

    def test_parse_days_ms_and_us_and_header(self):
        d0 = 1760918400                                     # 2025-10-20 00:00 UTC
        files = {"2025-10-20": self._zip([(d0 * 1000, 1, 2, 0.5, 1.5, 10), ((d0 + 60) * 1000, 1.5, 2, 1, 1.8, 5)]),
                 "2025-10-21": self._zip([((d0 + 86400) * 1000000, 2, 3, 1, 2.5, 7)], header=False)}
        seen = []

        def fake(url, tries=3):
            seen.append(url)
            return files[url.rsplit("-1m-", 1)[1][:-4]]
        saved = scalp.http_bytes
        scalp.http_bytes = fake
        try:
            bars = scalp.src_binance("XRP", d0, d0 + 86400 + 120)
        finally:
            scalp.http_bytes = saved
        self.assertEqual([b["t"] for b in bars], [d0, d0 + 60, d0 + 86400])
        self.assertEqual(bars[2]["c"], 2.5)
        self.assertIn("futures/um/daily/klines/XRPUSDT/1m/XRPUSDT-1m-2025-10-20.zip", seen[0])

    def test_relay_file_preferred_then_zip_fallback(self):
        from datetime import datetime, timezone
        d0 = 1760918400
        start, end = d0, d0 + 7200
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with tempfile.TemporaryDirectory() as d:
            bars = [[d0 + 60 * i, 1, 2, 0.5, 1.5, 10] for i in range(120)]
            with open(os.path.join(d, "XRP.json"), "w") as fp:
                json.dump({"generated_at_utc": now, "bars": bars}, fp)
            with open(os.path.join(d, "ETH.json"), "w") as fp:                       # 오래된 파일
                json.dump({"generated_at_utc": "2020-01-01T00:00:00Z", "bars": bars}, fp)
            with open(os.path.join(d, "BTC.json"), "w") as fp:                       # 끝이 비어 있는 파일
                json.dump({"generated_at_utc": now, "bars": bars[:30]}, fp)
            got = scalp.src_binance_relay("XRP", start, end, relay_dir=d)
            self.assertEqual(len(got), 120)
            self.assertIsNone(scalp.src_binance_relay("ETH", start, end, relay_dir=d))
            self.assertIsNone(scalp.src_binance_relay("BTC", start, end, relay_dir=d))
            self.assertIsNone(scalp.src_binance_relay("DOGE", start, end, relay_dir=d))
            saved = (scalp.RELAY_DIR, scalp.http_bytes, scalp.RELAY_URL)
            calls = []
            scalp.RELAY_DIR, scalp.http_bytes = d, lambda url, tries=3: calls.append(url) or self._zip([(d0 * 1000, 1, 2, 0.5, 1.5, 1)])
            scalp.RELAY_URL = ""                                                     # 서버 주소는 건너뛴다(오프라인 시험)
            try:
                self.assertEqual(len(scalp.src_binance("XRP", start, end)), 120)   # 중계 파일 → 다운로드 없음
                self.assertEqual(calls, [])
                scalp.src_binance("DOGE", start, end)                                # 파일 없음 → 일별 zip
                self.assertEqual(len(calls), 1)
            finally:
                scalp.RELAY_DIR, scalp.http_bytes, scalp.RELAY_URL = saved

    def test_relay_http_first_then_repo_file(self):
        from datetime import datetime, timezone
        d0 = 1760918400
        start, end = d0, d0 + 7200
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = {"generated_at_utc": now, "bars": [[d0 + 60 * i, 1, 2, 0.5, 1.5, 10] for i in range(120)]}
        urls = []

        def fake_http(url, tries=3, data=None):
            urls.append(url)
            if "XRP" in url:
                return payload
            raise RuntimeError("503")
        saved = (scalp.http_json, scalp.RELAY_URL, scalp.RELAY_DIR, scalp.http_bytes)
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "ETH.json"), "w") as fp:
                json.dump(payload, fp)
            scalp.http_json, scalp.RELAY_URL, scalp.RELAY_DIR = fake_http, "http://x/relay/", d
            scalp.http_bytes = lambda url, tries=3: (_ for _ in ()).throw(RuntimeError("no zip"))
            try:
                self.assertEqual(len(scalp.src_binance("XRP", start, end)), 120)     # ① 서버 주소
                self.assertEqual(urls, ["http://x/relay/XRP.json"])
                self.assertEqual(len(scalp.src_binance("ETH", start, end)), 120)     # ② 서버 실패 → 저장소 파일
                with self.assertRaises(RuntimeError):
                    scalp.src_binance("DOGE", start, end)                            # ③ 둘 다 없음 → zip
                scalp.RELAY_URL = ""
                self.assertIsNone(scalp.src_binance_relay_http("XRP", start, end))   # 비우면 건너뜀
            finally:
                scalp.http_json, scalp.RELAY_URL, scalp.RELAY_DIR, scalp.http_bytes = saved

    def test_vps_relay_out_dir_atomic_write(self):
        import io as _io, contextlib, vps_relay
        saved = vps_relay.build
        vps_relay.build = lambda days=4, coins=None: ({"XRP": {"to_utc": "2026-10-21 00:00", "from_utc": "x", "bars": [[1, 1, 1, 1, 1, 1]]}}, {})
        with tempfile.TemporaryDirectory() as d:
            try:
                with contextlib.redirect_stdout(_io.StringIO()):
                    rc = vps_relay.main(["--no-upload", "--out-dir", os.path.join(d, "w")])
            finally:
                vps_relay.build = saved
            self.assertEqual(rc, 0)
            self.assertEqual(sorted(os.listdir(os.path.join(d, "w"))), ["XRP.json"])

    def test_vps_relay_fetch_and_dedupe(self):
        import vps_relay
        d0 = 1760918400
        pages = [[[(d0 + 60 * i) * 1000, "1", "2", "0.5", "1.5", "9"] for i in range(1500)],
                 [[(d0 + 60 * i) * 1000, "1", "2", "0.5", "1.5", "9"] for i in range(1499, 1600)]]   # 겹치는 봉 하나
        saved = vps_relay.http_json
        vps_relay.http_json = lambda url, **kw: pages.pop(0) if pages else []
        try:
            bars = vps_relay.fetch_coin("XRP", d0, d0 + 1600 * 60)
        finally:
            vps_relay.http_json = saved
        self.assertEqual(len(bars), 1600)
        self.assertEqual(bars[0], [d0, 1.0, 2.0, 0.5, 1.5, 9.0])
        self.assertEqual(bars[-1][0], d0 + 1599 * 60)

    def test_missing_days(self):
        from datetime import datetime, timezone
        today = datetime(2026, 10, 23, tzinfo=timezone.utc)
        rows = [{"eval_day_utc": "2026-10-21", "summary": {"markets": 6}},
                {"eval_day_utc": "2026-10-22", "summary": {"markets": 4}}]
        self.assertEqual(scalp.missing_days(rows, 3, today), ["2026-10-20", "2026-10-22"])

    def test_venues_no_result_does_not_save(self):
        import io as _io, contextlib
        saved = (scalp.EXTRA_SOURCES, scalp.hl_live_fees)
        scalp.EXTRA_SOURCES = [("binance", lambda c, s, e: (_ for _ in ()).throw(RuntimeError("404")))]
        scalp.hl_live_fees = lambda: None
        with tempfile.TemporaryDirectory() as d:
            hist, out = os.path.join(d, "h.jsonl"), os.path.join(d, "o.json")
            try:
                with contextlib.redirect_stdout(_io.StringIO()):
                    rc = scalp.main(["--venues", "binance", "--day", "2026-10-20", "--save-bars", "",
                                     "--history", hist, "--out", out])
            finally:
                scalp.EXTRA_SOURCES, scalp.hl_live_fees = saved
            self.assertEqual(rc, 1)
            self.assertFalse(os.path.exists(hist))
            self.assertFalse(os.path.exists(out))


class NightFilter(unittest.TestCase):
    """밤(한국시간 21~02시) 필터 규칙: 같은 신호라도 낮에는 None, 밤에는 원래 신호."""

    def _ctx_with_signal(self, t_utc):
        # 60봉 뒤 종가가 VWAP 에서 -1.5σ 넘게 벗어나도록: 평평하다가 마지막에 급락
        closes = [100.0] * 70 + [100.0 + 0.3 * math.sin(i) for i in range(30)] + [90.0]
        bars = [scalp.bar(t_utc - (len(closes) - 1 - i) * 900, c, c + 0.1, c - 0.1, c, 1.0) for i, c in enumerate(closes)]
        return scalp.Ctx(bars, 0)

    def test_day_signal_blocked_night_signal_passes(self):
        from datetime import datetime, timezone
        night = int(datetime(2026, 10, 13, 13, 0, tzinfo=timezone.utc).timestamp())   # 22:00 KST
        day = int(datetime(2026, 10, 13, 3, 0, tzinfo=timezone.utc).timestamp())      # 12:00 KST
        for t, expect_signal in ((night, True), (day, False)):
            ctx = self._ctx_with_signal(t)
            i = len(ctx.c) - 1
            self.assertEqual(scalp.e_vwap(ctx, i), 1)          # 원래 규칙은 롱 신호
            self.assertEqual(scalp.e_vwap_night(ctx, i), 1 if expect_signal else None)
            self.assertEqual(scalp.kst_hour_of(t) in scalp.NIGHT_KST, expect_signal)

    def test_new_strategies_registered_and_timeframes(self):
        keys = [s["key"] for s in scalp.STRATEGIES]
        self.assertIn("vwap_fade_lim_n", keys)
        self.assertIn("mr_z20_lim_n", keys)
        self.assertEqual(scalp.TIMEFRAMES, [1, 5, 15, 30, 60])
        self.assertGreaterEqual(scalp.WARMUP_MIN, 60 * 60)   # 60분봉 VWAP 60봉이 평가 시작 전에 차야 한다


class OutOfSample(unittest.TestCase):
    """scripts/scalp_oos_check.py — 얼린 후보를 고른 날 뒤 이력만으로 다시 센다 (제안함 P19)."""

    def setUp(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
        import scalp_oos_check
        self.m = scalp_oos_check
        base = "okx:XRP:30m:" + scalp.BASELINE

        def day(d, a, b):
            return {"date": "2026-10-%02d" % d, "rows": {
                "okx:XRP:30m:vwap_fade_lim": {"nt": a, "n": 2},
                "okx:ETH:30m:vwap_fade_lim": {"nt": b, "n": 2},
                base: {"nt": -1.0, "n": 5}}}
        self.ins = [day(d, 1.0, 0.5) for d in range(1, 9)]          # 8일 모두 플러스 → 둘 다 후보
        self.day = day

    def test_freeze_only_uses_rows_up_to_date(self):
        fz = self.m.freeze(self.ins + [self.day(9, -50.0, -50.0)], "2026-10-08", ["30m"], "vwap", "okx:XRP:30m:vwap_fade_lim")
        self.assertEqual(len(fz["keys"]), 2)
        self.assertEqual(fz["keys"][0]["nt"], 8.0)

    def test_wait_go_no(self):
        fz = self.m.freeze(self.ins, "2026-10-08", ["30m"], "vwap", "okx:XRP:30m:vwap_fade_lim")
        self.assertEqual(self.m.check(self.ins, fz)["verdict"], "WAIT")       # 표본 밖 0일
        good = [self.day(d, 0.3, 0.2) for d in range(9, 19)]                  # 10일
        self.assertEqual(self.m.check(self.ins + good, fz)["verdict"], "GO")
        bad = [self.day(d, -0.3, 0.2) for d in range(9, 19)]                  # 주 후보만 손해
        r = self.m.check(self.ins + bad, fz)
        self.assertEqual(r["verdict"], "NO")
        self.assertFalse(r["primary_ok"])
        self.assertEqual(r["plus"], 1)

    def test_map_venue_dedupes_and_checks_own_rows(self):
        fz = self.m.freeze(self.ins, "2026-10-08", ["30m"], "vwap", None)
        fz["keys"].append({"key": "upbit:XRP:30m:vwap_fade_lim", "nt": 1.0})       # 같은 코인·봉·규칙 → 하나로
        bz = self.m.map_venue(fz, "binance", "binance:XRP:30m:vwap_fade_lim")
        self.assertEqual(sorted(x["key"] for x in bz["keys"]),
                         ["binance:ETH:30m:vwap_fade_lim", "binance:XRP:30m:vwap_fade_lim"])
        self.assertEqual(bz["history"], "scalp_binance_history.jsonl")
        rows = [{"date": "2026-10-%02d" % d, "rows": {"binance:XRP:30m:vwap_fade_lim": {"nt": 0.2, "n": 1},
                                                      "binance:ETH:30m:vwap_fade_lim": {"nt": -0.1, "n": 1}}}
                for d in range(9, 19)]
        r = self.m.check(rows, bz)
        self.assertEqual((r["plus"], r["verdict"]), (1, "GO"))                    # 절반(1/2) + 주 후보 통과

    def test_until_cuts_window(self):
        fz = self.m.freeze(self.ins, "2026-10-08", ["30m"], "vwap", None)
        rows = self.ins + [self.day(d, 0.3, 0.2) for d in range(9, 19)] + [self.day(25, -99.0, -99.0)]
        self.assertEqual(self.m.check(rows, fz, until="2026-10-18")["verdict"], "GO")


if __name__ == "__main__":
    unittest.main()
