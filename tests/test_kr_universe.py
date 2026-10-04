# -*- coding: utf-8 -*-
"""
kr_universe.py (한국 거래대금 상위 100 + 업종 동조) 검증 — 저장된 실데이터 fixture + 가짜 데이터, 인터넷 없음.

  python3 -m unittest tests.test_kr_universe -v
"""
import os, sys, json, tempfile, unittest
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kr_universe as ku  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "kr", "top100_2026-10-03.json")


def row(code, name, change, value, industry="X", week=0.0, month=0.0, typ="stock", specs=("common",)):
    d = [code, name, 100.0, change, 1000, value, "S", industry, 1e12, week, month, 120.0, 80.0, 900, 1.1, "KRX", typ, list(specs)]
    return {"s": "KRX:" + code, "d": d}


def resp(rows):
    return {"columns": ku.COLUMNS, "data": rows, "totalCount": len(rows), "generated_at_utc": "2026-10-03T07:00:00Z"}


class RealFixture(unittest.TestCase):
    """2026-10-03 장 마감 뒤 실제로 받은 상위 100 (설계서 1절의 숫자가 재현돼야 한다)."""

    def setUp(self):
        self.r = ku.build(ku.read_saved(FIXTURE), {"kind": "fixture"},
                          now=datetime(2026, 10, 3, 22, 30, tzinfo=timezone.utc))

    def test_universe_100_sorted_by_value(self):
        u = self.r["universe"]
        self.assertEqual(len(u), 100)
        self.assertEqual(u[0]["code"], "000660")                     # SK하이닉스가 거래대금 1위
        vals = [x["value_traded_krw"] for x in u]
        self.assertEqual(vals, sorted(vals, reverse=True))
        self.assertAlmostEqual(self.r["summary"]["value_traded_total_krw"] / 1e12, 18.2, places=1)
        self.assertTrue(any(x["code"] == "005935" and x["preferred"] for x in u))   # 우선주는 포함하되 표시

    def test_sync_groups_match_design_doc(self):
        g = self.r["groups"]
        self.assertEqual(g["Electrical Products"]["n"], 11)
        self.assertEqual(g["Electrical Products"]["up"], 10)
        self.assertTrue(g["Electrical Products"]["sync"]); self.assertEqual(g["Electrical Products"]["sync_by"], "day")
        self.assertTrue(g["Aerospace & Defense"]["sync"])              # 5중 4 상승, 중앙값 +5.1
        self.assertTrue(g["Chemicals: Specialty"]["sync"])
        self.assertTrue(g["Semiconductors"]["sync"]); self.assertEqual(g["Semiconductors"]["sync_by"], "week")  # 당일 +0.9 미달, 주간 +8.8
        self.assertFalse(g["Oil Refining/Marketing"]["sync"])           # 2종목뿐 (MIN_GROUP 미달)
        self.assertFalse(g["Industrial Machinery"]["sync"])             # 11중 5 상승 (45%)
        self.assertEqual(self.r["summary"]["sync_n"], 4)

    def test_aerospace_not_excluded_by_spac_word(self):
        # "Aerospace" 안의 spac 가 스팩 제외어에 걸리면 안 된다 (실제로 났던 버그)
        self.assertEqual(self.r["groups"]["Aerospace & Defense"]["n"], 5)
        self.assertFalse(ku.is_excluded("Hanwha Aerospace Co., Ltd."))
        self.assertTrue(ku.is_excluded("KB 제25호 스팩"))
        self.assertTrue(ku.is_excluded("Samsung SPAC No.9"))
        self.assertTrue(ku.is_excluded("KODEX 200 ETF"))


class Rules(unittest.TestCase):
    def test_top_n_and_exclusions(self):
        rows = [row("%06d" % i, "Co %d" % i, 1.0, 1e12 - i * 1e6) for i in range(130)]
        rows.append(row("999990", "엔에이치스팩30호", 5.0, 9e12))
        rows.append(row("999991", "KODEX 레버리지", 5.0, 9e12, typ="fund"))
        u = ku.parse_rows(resp(rows))
        self.assertEqual(len(u), ku.TOP_N)
        self.assertNotIn("999990", [x["code"] for x in u])
        self.assertNotIn("999991", [x["code"] for x in u])
        self.assertEqual(u[0]["rank"], 1)

    def test_sync_rule_boundaries(self):
        # 3종목, 상승 2/3 = 67%, 당일 중앙값 +2.0 → 동조(day)
        rows = [row("1", "a", 2.0, 3e9, "G"), row("2", "b", 3.0, 2e9, "G"), row("3", "c", -1.0, 1e9, "G")]
        g = ku.group_rows(ku.parse_rows(resp(rows)))["G"]
        self.assertTrue(g["sync"]); self.assertEqual(g["sync_by"], "day")
        # 당일 중앙값 +1.9 → 미달, 주간 중앙값 +5.0 → 동조(week)
        rows = [row("1", "a", 1.9, 3e9, "G", week=5.0), row("2", "b", 3.0, 2e9, "G", week=6.0), row("3", "c", -1.0, 1e9, "G", week=1.0)]
        g = ku.group_rows(ku.parse_rows(resp(rows)))["G"]
        self.assertTrue(g["sync"]); self.assertEqual(g["sync_by"], "week")
        # 상승 비율 50% → 아무리 올라도 미달
        rows = [row("1", "a", 9.0, 3e9, "G"), row("2", "b", 9.0, 2e9, "G"), row("3", "c", -1.0, 1e9, "G"), row("4", "d", -1.0, 1e9, "G")]
        self.assertFalse(ku.group_rows(ku.parse_rows(resp(rows)))["G"]["sync"])
        # 2종목뿐 → 미달
        rows = [row("1", "a", 9.0, 3e9, "G"), row("2", "b", 9.0, 2e9, "G")]
        self.assertFalse(ku.group_rows(ku.parse_rows(resp(rows)))["G"]["sync"])

    def test_missing_industry_goes_to_unclassified(self):
        rows = [row("1", "a", 1.0, 1e9, industry=None)]
        self.assertIn("(미분류)", ku.group_rows(ku.parse_rows(resp(rows))))


class RelayAndHistory(unittest.TestCase):
    def _write(self, obj):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(obj, fp)
        self.addCleanup(os.remove, path)
        return path

    def test_relay_freshness(self):
        now = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
        fresh = dict(resp([row("1", "a", 1.0, 1e9)]), generated_at_utc="2026-10-03T07:00:00Z")
        stale = dict(fresh, generated_at_utc="2026-10-01T07:00:00Z")
        self.assertIsNotNone(ku.read_saved(self._write(fresh), max_age_h=36, now=now))
        self.assertIsNone(ku.read_saved(self._write(stale), max_age_h=36, now=now))
        self.assertIsNone(ku.read_saved(self._write({"foo": 1}), max_age_h=36, now=now))
        self.assertIsNone(ku.read_saved("/nonexistent.json"))

    def test_duplicate_detection(self):
        r = ku.build(resp([row("1", "a", 1.0, 5e9)]), {"kind": "x"}, now=datetime(2026, 10, 6, 7, 0, tzinfo=timezone.utc))
        fd, hp = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            fp.write(json.dumps({"date": "2026-10-03", "value_traded_total_krw": 5000000000}) + "\n")
        self.addCleanup(os.remove, hp)
        ku.mark_duplicate(r, hp)
        self.assertTrue(r["duplicate_of_previous"])
        r2 = ku.build(resp([row("1", "a", 1.0, 6e9)]), {"kind": "x"}, now=datetime(2026, 10, 6, 7, 0, tzinfo=timezone.utc))
        ku.mark_duplicate(r2, hp)
        self.assertFalse(r2["duplicate_of_previous"])

    def test_scanner_payload_shape(self):
        p = ku.scanner_payload()
        self.assertEqual(p["columns"], ku.COLUMNS)
        self.assertEqual(p["markets"], ["korea"])
        self.assertEqual(p["sort"]["sortBy"], "Value.Traded")
        self.assertGreaterEqual(p["range"][1], ku.TOP_N)


if __name__ == "__main__":
    unittest.main()
