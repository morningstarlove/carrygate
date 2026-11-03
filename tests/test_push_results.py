# -*- coding: utf-8 -*-
"""
push_results.py (결과 파일 커밋·푸시, 충돌 자동 해결) 검증 — 임시 폴더에 원격 저장소와 복제본 둘을 만들어
두 워크플로가 같은 파일을 동시에 쓰는 상황(2026-11-03 사고)을 그대로 재현한다. 인터넷 불필요.

  python3 -m unittest tests.test_push_results -v
"""
import json, os, subprocess, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import push_results as P  # noqa: E402


def run(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True).stdout


def write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def line(**kw):
    return json.dumps(kw, ensure_ascii=False)


class TestMergeJsonl(unittest.TestCase):
    def test_same_date_takes_mine_and_keeps_both_sides(self):
        up = "\n".join([line(date="2026-11-01", v=1), line(date="2026-11-03", v="kr")]) + "\n"
        mine = "\n".join([line(date="2026-11-01", v=1), line(date="2026-11-02", v=2),
                          line(date="2026-11-03", v="daily")]) + "\n"
        out = [json.loads(l) for l in P.merge_jsonl(up, mine).splitlines()]
        self.assertEqual([r["date"] for r in out], ["2026-11-01", "2026-11-02", "2026-11-03"])
        self.assertEqual(out[2]["v"], "daily")

    def test_bars_keyed_by_bar_not_date(self):
        up = line(bar="2026-11-03T00:00", date="2026-11-03", s=1) + "\n"
        mine = line(bar="2026-11-03T04:00", date="2026-11-03", s=2) + "\n"
        out = [json.loads(l) for l in P.merge_jsonl(up, mine).splitlines()]
        self.assertEqual([r["bar"] for r in out], ["2026-11-03T00:00", "2026-11-03T04:00"])

    def test_keyless_lines_union_without_duplicates(self):
        up = "a\nb\n"
        mine = "a\nc\n"
        self.assertEqual(P.merge_jsonl(up, mine), "a\nb\nc\n")


class TestPushRace(unittest.TestCase):
    """원격 1개 + 복제본 2개(daily, kr). kr 가 먼저 올리고 daily 가 같은 파일로 뒤따른다."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.remote = os.path.join(self.tmp, "remote.git")
        run(self.tmp, "git", "init", "-q", "--bare", "-b", "main", self.remote)
        seed = os.path.join(self.tmp, "seed")
        run(self.tmp, "git", "clone", "-q", self.remote, seed)
        self._user(seed)
        write(os.path.join(seed, "verdict.json"), '{"date": "2026-11-02"}\n')
        write(os.path.join(seed, "verdict_history.jsonl"), line(date="2026-11-02", n=0) + "\n")
        write(os.path.join(seed, "kr_sector.json"), '{"d": 2}\n')
        write(os.path.join(seed, "status.json"), '{"d": 2}\n')
        run(seed, "git", "add", "-A")
        run(seed, "git", "commit", "-q", "-m", "seed")
        run(seed, "git", "push", "-q", "origin", "HEAD:main")
        self.kr = self._clone("kr")
        self.daily = self._clone("daily")
        self.cwd = os.getcwd()
        self.logs = []

    def tearDown(self):
        os.chdir(self.cwd)

    def _user(self, d):
        run(d, "git", "config", "user.name", "t")
        run(d, "git", "config", "user.email", "t@example.com")

    def _clone(self, name):
        d = os.path.join(self.tmp, name)
        run(self.tmp, "git", "clone", "-q", self.remote, d)
        self._user(d)
        return d

    def _push(self, repo, paths, msg, recompute=None):
        os.chdir(repo)
        return P.push_results(paths, msg, "main", recompute=recompute,
                              recompute_paths=("verdict.json", "verdict_history.jsonl"),
                              sleep=lambda s: None, log=self.logs.append)

    def _remote_file(self, path):
        return run(self.tmp, "git", "--git-dir", self.remote, "show", "main:" + path)

    def test_conflict_on_shared_files_is_resolved_and_pushed(self):
        # kr: 업종 결과 + 판정(같은 날짜 줄)
        write(os.path.join(self.kr, "kr_sector.json"), '{"d": 3}\n')
        write(os.path.join(self.kr, "verdict.json"), '{"date": "2026-11-03", "by": "kr"}\n')
        with open(os.path.join(self.kr, "verdict_history.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(line(date="2026-11-03", n="kr") + "\n")
        self.assertEqual(self._push(self.kr, ["kr_sector.json", "verdict.json", "verdict_history.jsonl"],
                                    "kr_sector 2026-11-03"), "pushed")
        # daily: 자기 결과 + 같은 판정 파일 (예전 방식이면 여기서 rebase 충돌로 전부 유실)
        write(os.path.join(self.daily, "status.json"), '{"d": 3}\n')
        write(os.path.join(self.daily, "verdict.json"), '{"date": "2026-11-03", "by": "daily"}\n')
        with open(os.path.join(self.daily, "verdict_history.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(line(date="2026-11-03", n="daily") + "\n")
        # verdict.py 대신: 합친 뒤 입력(kr_sector.json)을 보고 다시 계산했다는 표시를 남긴다
        res = self._push(self.daily, ["status.json", "verdict.json", "verdict_history.jsonl", "live.json"],
                         "status 2026-11-03",
                         recompute=[sys.executable, "-c",
                                    "import json;ks=json.load(open('kr_sector.json'));"
                                    "open('verdict.json','w').write(json.dumps({'recomputed_with_kr':ks['d']})+'\\n')"])
        self.assertEqual(res, "pushed", self.logs)
        self.assertIn("없음(건너뜀): live.json", self.logs)
        self.assertIn("  충돌 해결(기록 합침): verdict_history.jsonl", self.logs)
        self.assertIn("  충돌 해결(방금 계산한 값 사용): verdict.json", self.logs)
        # 양쪽 결과 모두 원격에 있다
        self.assertEqual(json.loads(self._remote_file("status.json"))["d"], 3)
        self.assertEqual(json.loads(self._remote_file("kr_sector.json"))["d"], 3)
        # 판정은 합친 입력으로 다시 계산됐다
        self.assertEqual(json.loads(self._remote_file("verdict.json"))["recomputed_with_kr"], 3)
        # 기록: 날짜 중복 없이, 같은 날짜는 나중에 올린 쪽
        rows = [json.loads(l) for l in self._remote_file("verdict_history.jsonl").splitlines()]
        self.assertEqual([r["date"] for r in rows], ["2026-11-02", "2026-11-03"])
        self.assertEqual(rows[1]["n"], "daily")
        # 원격 이력: seed → kr → daily, 모두 남는다
        msgs = run(self.tmp, "git", "--git-dir", self.remote, "log", "--format=%s", "main").split("\n")
        self.assertEqual(msgs[:3], ["status 2026-11-03", "kr_sector 2026-11-03", "seed"])
        self.assertFalse(os.path.isdir(os.path.join(self.daily, ".git", "rebase-merge")))

    def test_push_rejected_then_retried(self):
        # daily 가 당겨온 직후·올리기 직전에 kr 가 또 올린다 → 첫 푸시는 밀리고 두 번째에 올라가야 한다
        write(os.path.join(self.daily, "status.json"), '{"d": 4}\n')
        flag = os.path.join(self.tmp, "raced")
        sneak = ("import os,subprocess\n"
                 "if not os.path.exists(%r):\n"
                 "    open(%r,'w').close()\n"
                 "    kr=%r\n"
                 "    open(os.path.join(kr,'kr_sector.json'),'w').write('{\"d\": 5}\\n')\n"
                 "    subprocess.run(['git','commit','-qam','kr again'],cwd=kr,check=True)\n"
                 "    subprocess.run(['git','push','-q','origin','HEAD:main'],cwd=kr,check=True)\n"
                 % (flag, flag, self.kr))
        res = self._push(self.daily, ["status.json"], "status z", recompute=[sys.executable, "-c", sneak])
        self.assertEqual(res, "pushed", self.logs)
        self.assertIn("푸시 밀림 1/5 — 다시 당겨서 시도", self.logs)
        self.assertEqual(json.loads(self._remote_file("kr_sector.json"))["d"], 5)
        self.assertEqual(json.loads(self._remote_file("status.json"))["d"], 4)

    def test_no_change(self):
        self.assertEqual(self._push(self.daily, ["status.json"], "status x"), "no change")

    def test_plain_push_without_race(self):
        write(os.path.join(self.daily, "status.json"), '{"d": 9}\n')
        self.assertEqual(self._push(self.daily, ["status.json"], "status y"), "pushed")
        self.assertEqual(json.loads(self._remote_file("status.json"))["d"], 9)


if __name__ == "__main__":
    unittest.main()
