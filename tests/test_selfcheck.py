# -*- coding: utf-8 -*-
"""selfcheck.py 형식 검산(P14) 시험 — 키 누락·타입 불일치·null 허용·파일 없음."""
import os, sys, json, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import selfcheck as S  # noqa: E402


class ShapeErrors(unittest.TestCase):
    def test_ok(self):
        d = {"date": "2026-10-10", "summary": {"verdict": "열림", "n": 3, "gap": 1.5, "ok": True}, "coins": {}}
        shape = {"date": "str", "summary": "dict", "summary.verdict": "str", "summary.n": "int", "summary.gap": "num",
                 "summary.ok": "bool", "coins": "dict"}
        self.assertEqual(S.shape_errors(d, shape), [])

    def test_missing_key(self):
        errs = S.shape_errors({"date": "x", "summary": {}}, {"date": "str", "summary.verdict": "str"})
        self.assertEqual(errs, ["summary.verdict: 없음"])

    def test_wrong_type(self):
        errs = S.shape_errors({"date": 20261010, "summary": []}, {"date": "str", "summary": "dict"})
        self.assertIn("date: str 이어야 하는데 int", errs)
        self.assertIn("summary: dict 이어야 하는데 list", errs)

    def test_bool_is_not_int(self):
        self.assertEqual(S.shape_errors({"n": True}, {"n": "int"}), ["n: int 이어야 하는데 bool"])
        self.assertEqual(S.shape_errors({"n": 2}, {"n": "num"}), [])
        self.assertEqual(S.shape_errors({"n": 2.5}, {"n": "int"}), ["n: int 이어야 하는데 float"])

    def test_nullable_and_any(self):
        self.assertEqual(S.shape_errors({"a": None, "b": None}, {"a": "?num", "b": "any"}), [])
        self.assertEqual(S.shape_errors({"a": None}, {"a": "num"}), ["a: num 이어야 하는데 null"])

    def test_top_not_dict(self):
        self.assertEqual(len(S.shape_errors([1, 2], {"date": "str"})), 1)


class CheckShapes(unittest.TestCase):
    def _run(self, files, shapes):
        problems, notes = [], []
        n = S.check_shapes(problems, notes, shapes=shapes, loader=lambda p: files.get(p))
        return n, problems, notes

    def test_pass_and_count(self):
        n, problems, notes = self._run({"a.json": {"date": "d"}, "b.json": {"date": "d"}}, {"a.json": {"date": "str"}, "b.json": {"date": "str"}})
        self.assertEqual((n, problems, notes), (2, [], []))

    def test_broken_file_is_problem(self):
        n, problems, notes = self._run({"a.json": {"date": 1}}, {"a.json": {"date": "str"}})
        self.assertEqual(n, 0)
        self.assertEqual(len(problems), 1)
        self.assertIn("a.json", problems[0])

    def test_missing_file_is_note_only(self):
        n, problems, notes = self._run({}, {"a.json": {"date": "str"}, "live.json": {"date": "str"}})
        self.assertEqual((n, problems), (0, []))
        self.assertEqual(len(notes), 1)
        self.assertIn("a.json", notes[0])
        self.assertNotIn("live.json", notes[0])   # 선택 파일은 없어도 알리지 않는다


class RealFiles(unittest.TestCase):
    """저장소에 커밋된 실제 결과 파일이 SHAPES 를 만족하는지. 파일이 없으면 건너뛴다."""
    def test_committed_results_match_shapes(self):
        for path, shape in S.SHAPES.items():
            full = os.path.join(ROOT, path)
            if not os.path.exists(full):
                continue
            with open(full, encoding="utf-8") as fp:
                d = json.load(fp)
            self.assertEqual(S.shape_errors(d, shape), [], path)

    def test_shapes_cover_dashboard_files(self):
        """dashboard/index.html 의 FILES 목록에 있는 파일은 모두 SHAPES 에 있어야 한다."""
        import re
        html = open(os.path.join(ROOT, "dashboard", "index.html"), encoding="utf-8").read()
        m = re.search(r"var FILES=\{(.*?)\};", html, re.S)
        self.assertIsNotNone(m)
        files = set(re.findall(r'"([\w_]+\.json)"', m.group(1)))
        self.assertTrue(files, "FILES 가 비었다")
        self.assertEqual(files - set(S.SHAPES), set())


if __name__ == "__main__":
    unittest.main()
