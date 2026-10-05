# -*- coding: utf-8 -*-
"""
한국 중계(kr_relay.py) 와 funding.py 의 중계 파일 처리 검증 — 가짜 데이터, 인터넷 없음.

  python3 -m unittest tests.test_relay -v
"""
import os, sys, json, time, tempfile, unittest
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import funding, kr_relay  # noqa: E402


def relay_payload(hours_ago):
    gen = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    entry = {"venue": "binance", "interval_hours": 8.0, "funding_rate_now": 0.0001,
             "funding_rate_avg7d": 0.0002, "history_points": 21, "mark_price": 100.0, "index_price": 99.9, "premium_pct": 0.1}
    return {"schema": "carrygate-kr-relay/1", "date": gen.strftime("%Y-%m-%d"),
            "generated_at_utc": gen.strftime("%Y-%m-%dT%H:%M:%SZ"), "generated_at_kst": "x",
            "venues": {"binance": {"BTC": entry}, "bybit": {"BTC": dict(entry, venue="bybit", interval_hours=4.0)},
                       "okx": {"BTC": dict(entry, venue="okx")}},
            "errors": {}}


class ReadRelay(unittest.TestCase):
    def _write(self, hours_ago):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(relay_payload(hours_ago), fp)
        self.addCleanup(os.remove, path)
        return path

    def test_fresh_file_used(self):
        info = funding.read_relay(self._write(5))
        self.assertTrue(info["present"] and info["fresh"])
        self.assertAlmostEqual(info["age_hours"], 5.0, delta=0.2)
        self.assertEqual(sorted(info["venues"]), ["binance", "bybit"])       # okx 는 중계 대상이 아니다

    def test_stale_file_ignored(self):
        info = funding.read_relay(self._write(funding.RELAY_MAX_AGE_H + 1))
        self.assertTrue(info["present"])
        self.assertFalse(info["fresh"])
        self.assertEqual(info["venues"], {})

    def test_missing_or_broken(self):
        self.assertFalse(funding.read_relay("/nonexistent/kr.json")["present"])
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as fp:
            fp.write("{not json")
        self.addCleanup(os.remove, path)
        self.assertFalse(funding.read_relay(path)["present"])


class Fees(unittest.TestCase):
    def test_documented_fees_for_relay_venues(self):
        self.assertEqual(funding.venue_fees("binance", "BTC", None)[:2], (0.10, 0.05))
        self.assertEqual(funding.venue_fees("bybit", "BTC", None)[:2], (0.10, 0.055))
        self.assertEqual(funding.venue_fees("okx", "BTC", None)[:2], (funding.TAKER_FEE_SPOT_PCT, funding.TAKER_FEE_PERP_PCT))

    def test_relay_entry_decorates_like_direct(self):
        e = dict(relay_payload(1)["venues"]["bybit"]["BTC"])
        d = funding.decorate(e, funding.venue_fees("bybit", "BTC", None))
        self.assertEqual(d["basis"], "7일평균")
        self.assertAlmostEqual(d["gross_apr_pct"], 0.0002 * (24 / 4.0) * 365 * 100, places=3)   # 43.8%
        self.assertAlmostEqual(d["fee_drag_apr_pct"], 2 * (0.10 + 0.055) * 365 / funding.HOLD_DAYS, places=3)
        self.assertAlmostEqual(d["decision_apr_pct"], d["gross_apr_pct"] - d["fee_drag_apr_pct"], places=3)


class CoinGecko(unittest.TestCase):
    def _payload(self):
        return {"name": "Binance (Futures)", "tickers": [
            {"symbol": "BTCUSDT", "base": "BTC", "target": "USDT", "contract_type": "perpetual", "funding_rate": 0.0085, "last": 100.0, "index": 99.9},
            {"symbol": "BTCUSD_260327", "base": "BTC", "target": "USD", "contract_type": "futures", "funding_rate": 0.0, "last": 101.0, "index": 99.9},
            {"symbol": "DOGEUSDT", "base": "DOGE", "target": "USDT", "contract_type": "perpetual", "funding_rate": -0.02, "last": 0.1, "index": 0.1},
            {"symbol": "ETHBTC", "base": "ETH", "target": "BTC", "contract_type": "perpetual", "funding_rate": 0.01, "last": 0.03, "index": 0.03},
        ]}

    def test_parse_percent_units_and_filters(self):
        now = 1_800_000_000_000
        rows, new = funding.cg_parse("binance", self._payload(), [], now)
        self.assertEqual(sorted(rows), ["BTC", "DOGE"])                  # 만기 선물·BTC 마진 쌍은 제외
        self.assertAlmostEqual(rows["BTC"]["funding_rate_now"], 0.000085)   # 0.0085% -> 비율
        self.assertIsNone(rows["BTC"]["funding_rate_avg7d"])               # 표본 1개 — 평균 없음
        self.assertEqual(rows["BTC"]["avg_samples"], 1)
        self.assertEqual(rows["BTC"]["interval_hours"], 8.0)
        self.assertTrue(rows["BTC"]["interval_assumed"])
        self.assertEqual(len(new), 2)
        d = funding.decorate(dict(rows["BTC"]), funding.venue_fees("binance", "BTC", None))
        self.assertEqual(d["basis"], "단발값")

    def test_avg_needs_min_samples_and_window(self):
        now = 1_800_000_000_000
        day = 86400_000
        snaps = [{"t_ms": now - k * day, "venue": "binance", "coin": "BTC", "rate": 0.0001 * (k + 1)} for k in range(1, 5)]   # 4개
        self.assertEqual(funding.cg_avg7(snaps, "binance", "BTC", now), (None, 4))
        rows, _ = funding.cg_parse("binance", self._payload(), snaps, now)      # 오늘 것이 더해져 5개
        self.assertEqual(rows["BTC"]["avg_samples"], 5)
        self.assertAlmostEqual(rows["BTC"]["funding_rate_avg7d"], (0.0002 + 0.0003 + 0.0004 + 0.0005 + 0.000085) / 5)
        old = snaps + [{"t_ms": now - 9 * day, "venue": "binance", "coin": "BTC", "rate": 1.0}]   # 9일 전은 창 밖
        self.assertEqual(funding.cg_avg7(old, "binance", "BTC", now)[1], 4)

    def test_snapshot_save_dedupes_same_day_and_prunes(self):
        now = 1_800_000_000_000
        day = 86400_000
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        self.addCleanup(os.remove, path)
        old = [{"t_ms": now - 20 * day, "venue": "bybit", "coin": "BTC", "rate": 0.5},        # 14일 넘음 — 삭제
               {"t_ms": now - 1 * day, "venue": "bybit", "coin": "BTC", "rate": 0.1},
               {"t_ms": now - 3600_000, "venue": "bybit", "coin": "BTC", "rate": 0.2}]        # 오늘 아침 (dry_run)
        new = [{"t_ms": now, "venue": "bybit", "coin": "BTC", "rate": 0.3}]                     # 오늘 다시 — 덮어쓴다
        n = funding.cg_snapshots_save(old, new, path=path, now_ms=now)
        self.assertEqual(n, 2)
        kept = funding.cg_snapshots_load(path)
        self.assertEqual([r["rate"] for r in kept], [0.1, 0.3])


class KrRelay(unittest.TestCase):
    def test_avg_recent_window(self):
        now_ms = time.time() * 1000
        pairs = [(now_ms - 8 * 86400e3, 1.0), (now_ms - 3 * 86400e3, 0.0002), (now_ms - 1 * 86400e3, 0.0004), (None, 9.0)]
        self.assertAlmostEqual(kr_relay.avg_recent(pairs), 0.0003)      # 8일 전·시각 없는 값은 제외
        self.assertIsNone(kr_relay.avg_recent([]))

    def test_config_validation(self):
        kr_relay.CONFIG_PATH = os.path.join(tempfile.gettempdir(), "no_such_cfg.json")
        with self.assertRaises(RuntimeError):
            kr_relay.load_config()
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as fp:
            json.dump({"token": "", "repo": "a/b"}, fp)
        self.addCleanup(os.remove, path)
        kr_relay.CONFIG_PATH = path
        with self.assertRaises(RuntimeError):
            kr_relay.load_config()


if __name__ == "__main__":
    unittest.main()
