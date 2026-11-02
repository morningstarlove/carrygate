"""common.py — 여러 모듈이 같이 쓰는 헬퍼의 계산 로직 시험 (인터넷 불필요)."""
import json
import os
import sys
import tempfile
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common  # noqa: E402


class TestNumbers(unittest.TestCase):
    def test_f(self):
        self.assertEqual(common.f("1.5"), 1.5)
        self.assertEqual(common.f(3), 3.0)
        self.assertIsNone(common.f(None))
        self.assertIsNone(common.f("abc"))

    def test_round(self):
        self.assertEqual(common.r2(1.23456), 1.23)
        self.assertEqual(common.r4(1.23456), 1.2346)
        self.assertIsNone(common.r2(None))
        self.assertIsNone(common.r4(None))

    def test_mean_std(self):
        self.assertIsNone(common.mean([]))
        self.assertEqual(common.mean([1, 2, 3]), 2.0)
        self.assertIsNone(common.std([1]))
        self.assertAlmostEqual(common.std([2, 4, 4, 4, 5, 5, 7, 9]), 2.0)   # 모표준편차

    def test_corr(self):
        self.assertIsNone(common.corr([1, 2], [1, 2]))                       # 표본 3개 미만
        self.assertIsNone(common.corr([1, 1, 1], [1, 2, 3]))                 # 분산 0
        self.assertAlmostEqual(common.corr([1, 2, 3, 4], [2, 4, 6, 8]), 1.0)
        self.assertAlmostEqual(common.corr([1, 2, 3, 4], [8, 6, 4, 2]), -1.0)


class TestFiles(unittest.TestCase):
    def test_load_json(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.json")
            self.assertIsNone(common.load_json(p))
            self.assertEqual(common.load_json(p, {}), {})
            with open(p, "w", encoding="utf-8") as fp:
                fp.write("{bad json")
            self.assertEqual(common.load_json(p, "dflt"), "dflt")
            with open(p, "w", encoding="utf-8") as fp:
                json.dump({"a": 1}, fp)
            self.assertEqual(common.load_json(p), {"a": 1})

    def test_append_and_load_history(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "h.jsonl")
            self.assertEqual(common.load_history(p), [])
            common.append_history({"date": "2026-10-02", "v": 1}, p)
            common.append_history({"date": "2026-10-01", "v": 2}, p)
            common.append_history({"date": "2026-10-02", "v": 3}, p)      # 같은 날 다시 → 덮어씀
            rows = common.load_history(p)
            self.assertEqual([(r["date"], r["v"]) for r in rows], [("2026-10-01", 2), ("2026-10-02", 3)])
            with open(p, "a", encoding="utf-8") as fp:
                fp.write("not json\n")
            self.assertEqual(len(common.load_history(p)), 2)                # 깨진 줄은 건너뜀


class _Resp:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestHttp(unittest.TestCase):
    def test_http_json_retries_then_succeeds(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append((req.full_url, req.data, dict(req.header_items())))
            if len(calls) < 2:
                raise IOError("boom")
            return _Resp(b'{"ok": 1}')

        saved = (urllib.request.urlopen, common.time.sleep)
        urllib.request.urlopen, common.time.sleep = fake_urlopen, lambda s: None
        try:
            self.assertEqual(common.http_json("http://x/a", data={"q": 1}), {"ok": 1})
        finally:
            urllib.request.urlopen, common.time.sleep = saved
        self.assertEqual(len(calls), 2)
        self.assertEqual(json.loads(calls[0][1]), {"q": 1})
        self.assertEqual(calls[0][2].get("Content-type"), "application/json")

    def test_http_json_gives_up(self):
        def fake_urlopen(req, timeout=None):
            raise IOError("down")

        saved = (urllib.request.urlopen, common.time.sleep)
        urllib.request.urlopen, common.time.sleep = fake_urlopen, lambda s: None
        try:
            with self.assertRaises(RuntimeError):
                common.http_json("http://x/a", tries=2)
        finally:
            urllib.request.urlopen, common.time.sleep = saved


if __name__ == "__main__":
    unittest.main()
