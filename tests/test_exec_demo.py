# -*- coding: utf-8 -*-
import unittest
import exec_demo as ed


def rep(day, active=True, demo=1.0, paper=2.0, ok=True, partial=False, secret=None):
    r = {"day": day, "trades": 3, "net_pct_sum": demo, "net_usdt_sum": demo, "maker_entries": 3, "maker_exits": 2,
         "gate": {"ok": ok, "use_p35": active, "rev_all": -5.0}, "by_inst": {}}
    if ok:
        r.update({"paper_okx_net_pct_sum": paper, "paper_okx_trades": 4, "paper_by_inst": {}})
    if partial:
        r["partial"] = True
    if secret:
        r["api_key"] = secret
    return r


class Clean(unittest.TestCase):
    def test_keeps_only_whitelisted_fields(self):
        row = ed.clean(rep("2026-11-29", secret="xyz"))
        self.assertNotIn("api_key", row)
        self.assertNotIn("gate", row)
        self.assertTrue(row["active"])
        self.assertFalse(row["error"])

    def test_partial_and_bad_rows_skipped(self):
        self.assertIsNone(ed.clean(rep("2026-11-28", partial=True)))
        self.assertIsNone(ed.clean({"day": "bad"}))
        self.assertIsNone(ed.clean(None))

    def test_gate_failure_is_error_day(self):
        self.assertTrue(ed.clean(rep("2026-11-29", ok=False))["error"])

    def test_same_day_once(self):
        h = []
        self.assertTrue(ed.merge(h, ed.clean(rep("2026-11-29"))))
        self.assertFalse(ed.merge(h, ed.clean(rep("2026-11-29", demo=9))))
        self.assertEqual(len(h), 1)


class Summary(unittest.TestCase):
    def days(self, n, active_every=3, demo=1.0, paper=2.0, ok=True):
        return [ed.clean(rep("2026-12-%02d" % (i + 1), active=(i % active_every == 0), demo=demo, paper=paper, ok=ok))
                for i in range(n)]

    def test_wait_before_14_days(self):
        self.assertEqual(ed.summarize(self.days(10))["verdict"], "WAIT")

    def test_pass(self):
        s = ed.summarize(self.days(14))                    # 쓴 날 5일, 비율 0.5
        self.assertEqual(s["active_days"], 5)
        self.assertEqual(s["ratio"], 0.5)
        self.assertEqual(s["verdict"], "PASS")

    def test_fail_low_ratio_or_negative(self):
        self.assertEqual(ed.summarize(self.days(14, demo=0.9))["verdict"], "FAIL")
        self.assertEqual(ed.summarize(self.days(14, demo=-1.0))["verdict"], "FAIL")

    def test_error_day_fails(self):
        h = self.days(14)
        h[1] = ed.clean(rep(h[1]["day"], active=False, ok=False))
        self.assertEqual(ed.summarize(h)["verdict"], "FAIL")

    def test_extend_when_few_active_days(self):
        h = self.days(14, active_every=7)                  # 쓴 날 2일 → 21일까지 연장
        s = ed.summarize(h)
        self.assertEqual(s["need_days"], 21)
        self.assertEqual(s["verdict"], "WAIT")
        s = ed.summarize(self.days(21, active_every=7))
        self.assertEqual(s["active_days"], 3)
        self.assertEqual(s["verdict"], "PASS")

    def test_only_first_window_counts(self):
        h = self.days(14) + self.days(30)[14:]
        h[20]["net_pct_sum"] = -999
        self.assertEqual(ed.summarize(h)["verdict"], "PASS")


if __name__ == "__main__":
    unittest.main()
