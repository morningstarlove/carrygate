#!/usr/bin/env python3
"""결과 파일 커밋·푸시 — 여러 워크플로가 같은 파일을 거의 동시에 쓸 때 생기는 충돌을 스스로 푼다.

왜 필요한가 (2026-11-03 사고):
  daily(carrygate.yml) 와 한국주식(kr_breakout.yml) 이 둘 다 verdict.json·verdict_history.jsonl 을 쓴다.
  daily 가 예약보다 6시간 늦게 돌아 한국주식과 겹쳤고, 먼저 올라간 쪽과 `git pull --rebase` 가 충돌해
  그날 결과 43개 파일이 통째로 저장되지 못했다. 같은 일은 reversal_4h 와 daily(reversal_*) 사이에도 날 수 있다.

푸는 방법 (사람 판단 없이 기계적으로):
  - 기록 파일(*.jsonl): 양쪽 줄을 합친다. 같은 날짜(reversal_bars 는 같은 봉) 줄은 지금 올리는 쪽으로 바꾸고
    날짜순으로 정렬한다. 어느 쪽 기록도 버리지 않는다.
  - 결과 파일(*.json 등): 지금 올리는 쪽(방금 계산한 값)을 쓴다.
  - --verdict: 합친 뒤 verdict.py 를 다시 돌려 판정을 최신 입력 전체로 새로 계산한다.
  - 푸시가 밀리면(그 사이 누가 또 올림) 다시 당겨서 최대 5번 시도한다. 끝내 못 올리면 종료코드 1(빨간불).

사용:
  python push_results.py -m "status 2026-11-03" --verdict status.json history.jsonl ...
  없는 경로는 건너뛴다(live.json 처럼 조건부로 생기는 파일).
"""
import argparse, json, os, random, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ATTEMPTS = 5


def git(*args, check=True):
    r = subprocess.run(("git",) + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, encoding="utf-8")
    if check and r.returncode != 0:
        raise RuntimeError("git %s 실패: %s" % (" ".join(args), (r.stderr or r.stdout).strip()))
    return r


def row_key(line):
    """기록 한 줄의 정체: reversal_bars 는 봉 시각, 나머지는 날짜. 둘 다 없으면 줄 전체."""
    try:
        d = json.loads(line)
    except ValueError:
        return ("raw", line)
    if isinstance(d, dict):
        if "bar" in d:
            return ("bar", str(d["bar"]))
        if "date" in d:
            return ("date", str(d["date"]))
    return ("raw", line)


def merge_jsonl(upstream_text, mine_text):
    """먼저 올라간 쪽 줄을 기준으로 내 줄을 덮어쓰거나 붙인다. 모든 줄에 날짜·봉 키가 있으면 그 순서로 정렬한다."""
    rows, index = [], {}
    for text, overwrite in ((upstream_text, False), (mine_text, True)):
        for line in (text or "").splitlines():
            if not line.strip():
                continue
            k = row_key(line)
            if k[0] != "raw" and k in index:
                if overwrite:
                    rows[index[k]] = line
                continue
            if k[0] == "raw" and k in index:
                continue   # 똑같은 줄은 한 번만
            index[k] = len(rows)
            rows.append(line)
    if rows and all(row_key(l)[0] != "raw" for l in rows):
        rows.sort(key=lambda l: row_key(l)[1])
    return "\n".join(rows) + "\n" if rows else ""


def unmerged_files():
    out = git("diff", "--name-only", "--diff-filter=U").stdout
    return [l for l in out.splitlines() if l.strip()]


def stage_text(stage, path):
    r = git("show", ":%d:%s" % (stage, path), check=False)
    return r.stdout if r.returncode == 0 else None


def resolve_conflicts(log=print):
    """rebase 가 멈춘 상태에서 충돌 파일을 모두 푼다. 리베이스 중 stage 2 = 먼저 올라간 쪽, stage 3 = 지금 올리는 커밋."""
    for path in unmerged_files():
        upstream, mine = stage_text(2, path), stage_text(3, path)
        if mine is None:
            git("rm", "-q", "--", path)
            log("  충돌 해결(내 쪽 삭제 유지): %s" % path)
            continue
        if path.endswith(".jsonl"):
            merged = merge_jsonl(upstream, mine)
            how = "기록 합침"
        else:
            merged = mine
            how = "방금 계산한 값 사용"
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(merged)
        git("add", "--", path)
        log("  충돌 해결(%s): %s" % (how, path))


def rebase_onto_remote(branch, log=print):
    """원격을 당겨 내 커밋을 그 위로 옮긴다. 충돌은 resolve_conflicts 로 풀고 계속한다."""
    git("fetch", "-q", "origin", "+refs/heads/%s:refs/remotes/origin/%s" % (branch, branch))
    r = git("rebase", "origin/" + branch, check=False)
    for _ in range(20):
        if r.returncode == 0:
            return True
        if not unmerged_files():
            log("rebase 실패(충돌 아님): %s" % (r.stderr or r.stdout).strip())
            git("rebase", "--abort", check=False)
            return False
        resolve_conflicts(log)
        env = dict(os.environ, GIT_EDITOR="true")
        r = subprocess.run(["git", "rebase", "--continue"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, encoding="utf-8", env=env)
    git("rebase", "--abort", check=False)
    return False


def amend_with(paths, recompute, log=print):
    """합친 입력으로 판정을 다시 계산해 같은 커밋에 넣는다."""
    r = subprocess.run(recompute, cwd=os.getcwd())
    if r.returncode != 0:
        log("판정 재계산 실패(종료코드 %d) — 합친 그대로 올린다" % r.returncode)
        return
    for p in paths:
        if os.path.exists(p):
            git("add", "-A", "--", p)
    if git("diff", "--cached", "--quiet", check=False).returncode != 0:
        git("commit", "-q", "--amend", "--no-edit")
        log("판정을 최신 입력으로 다시 계산해 커밋에 넣었다")


def push_results(paths, message, branch, recompute=None, recompute_paths=(), attempts=ATTEMPTS,
                 sleep=time.sleep, log=print):
    """있는 경로만 커밋하고 올린다. 반환: "no change" | "pushed" | "failed"."""
    for p in paths:
        if os.path.exists(p):
            git("add", "-A", "--", p)
        else:
            log("없음(건너뜀): %s" % p)
    if git("diff", "--cached", "--quiet", check=False).returncode == 0:
        log("no change")
        return "no change"
    git("commit", "-q", "-m", message)
    for i in range(1, attempts + 1):
        if not rebase_onto_remote(branch, log):
            log("당겨오기 실패 %d/%d" % (i, attempts))
        else:
            if recompute:
                amend_with(recompute_paths, recompute, log)
            if git("push", "origin", "HEAD:" + branch, check=False).returncode == 0:
                log("올림: %s (%d번째 시도)" % (message, i))
                return "pushed"
            log("푸시 밀림 %d/%d — 다시 당겨서 시도" % (i, attempts))
        sleep(i * 5 + random.uniform(0, 5))
    log("결과를 올리지 못했다 (%d번 시도)" % attempts)
    return "failed"


def main(argv=None):
    ap = argparse.ArgumentParser(description="결과 파일 커밋·푸시 (충돌 자동 해결)")
    ap.add_argument("-m", "--message", required=True)
    ap.add_argument("--branch", default=os.environ.get("GITHUB_REF_NAME") or "main")
    ap.add_argument("--verdict", action="store_true", help="합친 뒤 verdict.py 로 판정을 다시 계산")
    ap.add_argument("paths", nargs="+")
    a = ap.parse_args(argv)
    recompute = [sys.executable, os.path.join(HERE, "verdict.py")] if a.verdict else None
    res = push_results(a.paths, a.message, a.branch, recompute=recompute,
                       recompute_paths=("verdict.json", "verdict_history.jsonl"))
    return 1 if res == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
