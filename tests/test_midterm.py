# -*- coding: utf-8 -*-
"""
basis.py / kimchi.py / funding_signal.py / macro_events.py / listing.py 계산 로직 검증 — 가짜 데이터, 인터넷 없음.

  python3 -m unittest tests.test_midterm -v
"""
import os, sys, json, tempfile, unittest
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import basis, kimchi, funding_signal as fs, macro_events as me, listing  # noqa: E402


class Basis(unittest.TestCase):
    def test_annualize_and_fees(self):
        # 현물 100, 선물 101, 만기 73일 -> 베이시스 1% × 365/73 = 연 5%
        self.assertAlmostEqual(basis.annualize(101.0, 100.0, 73.0), 5.0)
        self.assertAlmostEqual(basis.fee_apr(73.0), basis.ONE_OFF_FEE_PCT * 5.0)
        c = basis.contract("okx", "BTC", "BTC-USDT-261225", datetime(2026, 12, 25, 8, tzinfo=timezone.utc), 73.0, 101.0, 100.0, 100.9, 101.1)
        self.assertAlmostEqual(c["gross_apr_pct"], 5.0)
        self.assertAlmostEqual(c["net_apr_pct"], 5.0 - basis.ONE_OFF_FEE_PCT * 5.0, places=3)
        self.assertTrue(c["eligible"])
        self.assertFalse(basis.contract("okx", "BTC", "x", datetime(2026, 12, 25, tzinfo=timezone.utc), 3.0, 101.0, 100.0, None, None)["eligible"])

    def test_negative_basis(self):
        c = basis.contract("gate", "ETH", "ETH_USDT_20261225", datetime(2026, 12, 25, tzinfo=timezone.utc), 365.0, 99.0, 100.0, None, None)
        self.assertAlmostEqual(c["gross_apr_pct"], -1.0)
        self.assertLess(c["net_apr_pct"], -1.0)


class Kimchi(unittest.TestCase):
    def test_premium_series(self):
        up = {"2026-01-01": 150_000_000.0, "2026-01-02": 151_500_000.0}
        ok = {"2026-01-01": 100_000.0, "2026-01-02": 100_000.0, "2026-01-03": 100_000.0}
        fx = {"2026-01-01": 1500.0, "2026-01-02": 1500.0}
        s = kimchi.premium_series(up, ok, fx)
        self.assertEqual([d for d, _, _, _ in s], ["2026-01-01", "2026-01-02"])
        self.assertAlmostEqual(s[0][1], 0.0)
        self.assertAlmostEqual(s[1][1], 1.0)

    def test_percentile_and_event_study(self):
        # 프리미엄이 올라간 날은 다음날 업비트가 내리는 가짜 세계
        series, up, okp = [], 100.0, 100.0
        import random
        rnd = random.Random(1)
        for i in range(120):
            prem = rnd.gauss(2.0, 1.0)
            okp *= 1 + rnd.gauss(0, 0.005)
            up = okp * (1 + prem / 100.0)
            series.append(("d%03d" % i, prem, up, okp))
        # 다음날 업비트 가격을 프리미엄 높은 날 이후 2% 낮춘다
        for i in range(1, len(series)):
            d, p, u, o = series[i]
            if series[i - 1][1] > 3.0:
                series[i] = (d, p, u * 0.98, o)
        es = kimchi.event_study(series)
        self.assertIn("high", es)
        self.assertGreater(es["high"]["n"], 0)
        self.assertLess(es["high"]["upbit_fwd1_pct"], es["mid"]["upbit_fwd1_pct"])
        self.assertEqual(kimchi.percentile_rank([1, 2, 3, 4], 4), 100.0)
        self.assertEqual(kimchi.percentile_rank([1, 2, 3, 4], 0), 0.0)

    def test_intraday_reversion(self):
        # 프리미엄이 사인파로 왔다갔다 -> 평균에서 벗어난 뒤 15분 후 되돌아온다 (음의 상관)
        import math
        up, ok = [], []
        for i in range(600):
            prem = 2.0 + 0.5 * math.sin(i / 10.0)
            o = 100.0
            up.append({"t": i * 60, "c": o * 1500.0 * (1 + prem / 100.0)})
            ok.append({"t": i * 60, "c": o})
        r = kimchi.intraday(up, ok, 1500.0, 0)
        self.assertEqual(r["n"], 600)
        self.assertAlmostEqual(r["mean_pct"], 2.0, places=1)
        self.assertLess(r["reversion_corr_15m"], -0.3)


class FundingSignal(unittest.TestCase):
    def test_daily_funding_sum(self):
        pairs = [(0, 0.0001), (3600, 0.0001), (86400, -0.0002)]
        d = fs.daily_funding(pairs)
        self.assertAlmostEqual(d["1970-01-01"], 0.02)
        self.assertAlmostEqual(d["1970-01-02"], -0.02)

    def test_study_rule_direction(self):
        # 펀딩이 높은 날 다음 3일은 떨어지고, 낮은 날 다음 3일은 오르는 가짜 세계
        import random
        rnd = random.Random(3)
        days = [(datetime(2026, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(150)]
        fund = {d: rnd.gauss(0.03, 0.03) for d in days}
        px, p = {}, 100.0
        for i, d in enumerate(days):
            drift = 0.0
            for j in range(1, 4):
                if i - j >= 0:
                    f_prev = fund[days[i - j]]
                    drift += -0.01 if f_prev > 0.08 else (0.01 if f_prev < -0.02 else 0.0)
            o = p
            p = p * (1 + drift + rnd.gauss(0, 0.002))
            px[d] = (o, p)
        res = fs.study(fund, px, 0.05)
        self.assertEqual(res["days"], 150)
        self.assertGreater(res["buckets"]["high"]["n"], 3)
        self.assertLess(res["buckets"]["high"]["fwd3_pct"], res["buckets"]["mid"]["fwd3_pct"])
        self.assertGreater(res["buckets"]["low"]["fwd3_pct"], res["buckets"]["mid"]["fwd3_pct"])
        self.assertGreater(res["rule"]["net_avg_pct"], 0)
        self.assertIn(res["signal_today"]["state"], ("과열", "냉각", "중립"))

    def test_rule_accounts_funding_and_fees(self):
        days = [(datetime(2026, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(40)]
        fund = {d: 0.0 for d in days}
        fund[days[35]] = 1.0            # 하루 1% 펀딩 -> 3일 평균도 상위 -> 과열 -> 숏
        px = {d: (100.0, 100.0) for d in days}   # 가격 불변
        for d in days[36:39]:
            fund[d] = 0.1               # 보유 중 숏이 받는 펀딩 0.3%
        res = fs.study(fund, px, 0.05)
        t = res["rule"]
        self.assertGreaterEqual(t["n"], 1)
        # 가격 변화 0, 펀딩 수취 +0.3, 수수료 -0.1 -> 순 +0.2 (마지막 매매 기준)
        self.assertAlmostEqual(t["net_avg_pct"] * t["n"], sum([0.3 - 0.1] * t["n"]) if t["n"] == 1 else t["net_sum_pct"], places=3)


class Macro(unittest.TestCase):
    def bars(self, t0, path):
        """t0 기준 ±90분 1분봉. path: 발표 뒤 분당 변화율 함수."""
        out = {}
        p = 100.0
        for k in range(-90, 91):
            t = t0 + k * 60
            o = p
            p = p * (1 + (path(k) if k >= 0 else 0.0))
            out[t] = (o, max(o, p), min(o, p), p)
        return out

    def test_reaction_and_surprise(self):
        t0 = 1_700_000_000 - 1_700_000_000 % 60
        bars = self.bars(t0, lambda k: 0.001 if k < 30 else 0.0)      # 발표 뒤 30분간 매분 +0.1%
        r = me.reaction(bars, t0)
        self.assertAlmostEqual(r["pre30_bp"], 0.0)
        self.assertGreater(r["post30_bp"], 290)
        self.assertGreater(r["maxup60_bp"], 290)
        self.assertEqual(round(r["maxdown60_bp"]), 0)
        self.assertIsNone(me.reaction({}, t0))

    def test_summarize_ratio_and_hit(self):
        ev = [{"type": "cpi", "post5_bp": 10, "post30_bp": 50, "post60_bp": 40, "base_abs_post30_bp": 10, "surprise_dir": -1, "maxup60_bp": 60, "maxdown60_bp": -5},
              {"type": "cpi", "post5_bp": -10, "post30_bp": -30, "post60_bp": -20, "base_abs_post30_bp": 10, "surprise_dir": -1, "maxup60_bp": 5, "maxdown60_bp": -40}]
        s = me.summarize(ev)
        self.assertEqual(s["cpi"]["n"], 2)
        self.assertAlmostEqual(s["cpi"]["abs_post30_bp"], 40.0)
        self.assertAlmostEqual(s["cpi"]["ratio_vs_base"], 4.0)
        self.assertEqual(s["cpi"]["hit_rate_pct"], 50.0)
        self.assertAlmostEqual(s["cpi"]["signed_post30_bp"], (-50 + 30) / 2.0)

    def test_surprise_direction_sign(self):
        ev = {"type": "cpi", "title": "x", "time_utc": "x", "ts": 0, "actual": 3.5, "forecast": 3.0}
        # process_event 는 네트워크를 쓰므로 부호 계산만 흉내낸다
        d = ev["actual"] - ev["forecast"]
        self.assertEqual((1 if d > 0 else -1) * me.SURPRISE_SIGN["cpi"], -1)


class Listing(unittest.TestCase):
    def test_parse_listings(self):
        notices = [
            {"id": 1, "title": "[거래] 신규 거래지원 안내 (ABC, XYZ) (KRW, BTC 마켓)", "first_listed_at": "2026-09-30T13:00:00+09:00"},
            {"id": 2, "title": "[거래] 디지털 자산 추가 안내 (DEF)", "created_at": "2026-09-29T10:00:00+09:00"},
            {"id": 3, "title": "[거래] 거래지원 종료 안내 (OLD)", "first_listed_at": "2026-09-28T10:00:00+09:00"},
            {"id": 4, "title": "[거래] 신규 거래지원 안내", "first_listed_at": "2026-09-27T10:00:00+09:00"},
            {"id": 5, "title": "[이벤트] 신규 거래지원 기념 이벤트 (ABC)", "first_listed_at": "2026-09-27T10:00:00+09:00"},
        ]
        ls = listing.parse_listings(notices)
        self.assertEqual([l["id"] for l in ls], [1, 2])
        self.assertEqual(ls[0]["tickers"], ["ABC", "XYZ"])
        self.assertEqual(ls[0]["time_utc"], "2026-09-30T04:00:00Z")
        self.assertEqual(ls[1]["tickers"], ["DEF"])

    def test_reaction_and_summary(self):
        t0 = 1_700_000_000 - 1_700_000_000 % 60
        bars = {}
        p = 100.0
        for k in range(-10, 245):
            o = p
            p = p * (1.01 if 0 <= k < 5 else 1.0)       # 공지 뒤 5분간 매분 +1%
            bars[t0 + k * 60] = (o, max(o, p), min(o, p), p)
        r = listing.reaction(bars, t0)
        self.assertAlmostEqual(r["post5_pct"], 5.101, places=2)
        self.assertAlmostEqual(r["post240_pct"], r["post60_pct"])
        self.assertGreaterEqual(r["maxup60_pct"], r["post5_pct"] - 0.01)
        ev = [{"id": 1, "ts": t0, "ticker": "ABC", "venue": "okx", "listed_abroad": True, "post5_pct": 5.1, "post15_pct": 5.1, "post60_pct": 5.1, "post240_pct": 5.1, "maxup60_pct": 5.1},
              {"id": 1, "ts": t0, "ticker": "ABC", "venue": "hyperliquid", "listed_abroad": False}]
        s = listing.summarize(ev)
        self.assertEqual(s["notices"], 1)
        self.assertEqual(s["with_price"], 1)
        self.assertEqual(s["by_venue"]["okx"]["post5_pos_pct"], 100.0)


if __name__ == "__main__":
    unittest.main()
