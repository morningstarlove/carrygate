# -*- coding: utf-8 -*-
"""live_monitor.py 계산 검증 — 가짜 조회 결과로."""
import os, sys, unittest
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import live_monitor as lm  # noqa: E402


class Evaluate(unittest.TestCase):
    def setUp(self):
        start = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
        self.cfg = {"address": "0xabc", "started_utc": start, "coin": "ETH"}
        self.perp = {"account_value_usdc": 150.0, "total_margin_used_usdc": 70.0, "withdrawable_usdc": 80.0,
                     "positions": [{"coin": "ETH", "size": -0.02, "side": "숏", "entry_px": 3500.0, "position_value_usdc": 70.0,
                                    "unrealized_pnl_usdc": 0.5, "liquidation_px": 7000.0, "leverage": 1.0, "margin_used_usdc": 70.0}]}
        self.spot = {"UETH": 0.02, "USDC": 10.0}
        # 10일간 펀딩 0.2 USDC 수취, 수수료 0.05
        self.fund = [{"t": 0, "coin": "ETH", "usdc": 0.02, "rate": 0.0001, "size": -0.02} for _ in range(10)]
        self.fills = [{"t": 0, "coin": "ETH", "side": "A", "px": 3500.0, "sz": 0.02, "fee": 0.03, "crossed": True},
                      {"t": 0, "coin": "UETH/USDC", "side": "B", "px": 3500.0, "sz": 0.02, "fee": 0.02, "crossed": True}]
        self.mids = {"ETH": 3500.0}

    def test_realized_apr_and_delta(self):
        s = lm.evaluate(self.cfg, self.perp, self.spot, self.fund, self.fills, self.mids, predicted_apr=8.0)
        self.assertAlmostEqual(s["notional_usdc"], 70.0)
        self.assertAlmostEqual(s["funding_received_usdc"], 0.2)
        self.assertAlmostEqual(s["fees_paid_usdc"], 0.05)
        # 경과 일수는 시작일 00:00 UTC 기준이라 10.x 일. 그 값으로 연환산을 되짚는다.
        d = s["days"]
        self.assertGreaterEqual(d, 10.0)
        self.assertAlmostEqual(s["realized_gross_apr_pct"], 0.2 / 70.0 * 365.0 / d * 100, places=1)
        self.assertAlmostEqual(s["realized_net_apr_pct"], 0.15 / 70.0 * 365.0 / d * 100, places=1)
        self.assertEqual(s["delta_mismatch_pct"], 0.0)
        self.assertEqual(s["liquidation_distance_pct"], 100.0)
        self.assertEqual(s["warnings"], [])
        # 저장값은 반올림 전 연환산으로 나눈다. 반올림된 연환산으로 되짚으면 0.01 차이가 날 수 있다
        self.assertAlmostEqual(s["realized_vs_predicted"], s["realized_gross_apr_pct"] / 8.0, delta=0.011)

    def test_warnings(self):
        self.spot["UETH"] = 0.03                 # 현물이 50% 많다
        self.perp["positions"][0]["liquidation_px"] = 4000.0   # 14% 위
        s = lm.evaluate(self.cfg, self.perp, self.spot, self.fund, self.fills, self.mids, predicted_apr=1.0)
        self.assertEqual(len(s["warnings"]), 3)
        self.assertTrue(any("델타" in w for w in s["warnings"]))
        self.assertTrue(any("강제청산" in w for w in s["warnings"]))
        self.assertTrue(any("청산 문턱" in w for w in s["warnings"]))

    def test_no_position(self):
        self.perp["positions"] = []
        s = lm.evaluate(self.cfg, self.perp, self.spot, [], [], self.mids, predicted_apr=8.0)
        self.assertIn("선물 포지션 없음", s["warnings"])
        self.assertIsNone(s["realized_net_apr_pct"])

    def test_no_wallet_file_is_quiet(self):
        self.assertEqual(lm.main(["--wallet", "/nonexistent/live_wallet.json"]), 0)


if __name__ == "__main__":
    unittest.main()
