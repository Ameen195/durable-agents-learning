"""Dry run: run every demo from scratch and check the expected results.

Usage (PowerShell, venv active, in the project folder):
    python dry_run.py

It copies the demo files into a temporary folder and runs everything there, so
your real checkpoints, outbox files and Git history are never touched. The
temporary folder is deleted at the end.

Not covered: day5b_postgres.py (needs a Postgres server).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ["durable_demo.py", "day3_demo.py", "day4_demo.py", "day5_demo.py", "day6_dbos_demo.py"]

results: list[tuple[str, bool]] = []


def run(tmp: str, script: str, *args: str, crash: bool = False, extra_env: dict | None = None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.pop("CRASH", None)
    env.pop("THREAD", None)
    env.pop("WF", None)
    if crash:
        env["CRASH"] = "1"
    if extra_env:
        env.update(extra_env)
    p = subprocess.run(
        [sys.executable, script, *args], cwd=tmp, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def check(name: str, condition: bool, detail: str = "") -> None:
    results.append((name, condition))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}")
    if not condition and detail:
        print("        output was:", detail.strip().replace("\n", "\n        ")[:600])


def day2(tmp: str) -> None:
    print("Day 2: persistent vs durable")
    rc, out = run(tmp, "durable_demo.py", "memory", "run", crash=True)
    check("memory run crashes at think", rc != 0 and "CRASH" in out, out)
    rc, out = run(tmp, "durable_demo.py", "memory", "resume")
    check("memory resume finds NOTHING (state lost)", "NOTHING (state lost)" in out, out)
    rc, out = run(tmp, "durable_demo.py", "sqlite", "run", crash=True)
    check("sqlite run crashes at think", rc != 0 and "CRASH" in out, out)
    rc, out = run(tmp, "durable_demo.py", "sqlite", "resume")
    check("sqlite resume finishes", "FINISHED" in out and "'report'" in out, out)
    check("sqlite resume does NOT re-run fetch", "fetch done" not in out, out)


def day3(tmp: str) -> None:
    print("Day 3: tools that run twice")
    for kind, expected in (("naive", "2"), ("safe", "1")):
        run(tmp, "day3_demo.py", kind, "run", crash=True)
        rc, out = run(tmp, "day3_demo.py", kind, "resume")
        check(f"{kind}: emails in outbox = {expected}", f"EMAILS IN OUTBOX: {expected}" in out, out)


def day4(tmp: str) -> None:
    print("Day 4: human approval")
    rc, out = run(tmp, "day4_demo.py", "start")
    check("start pauses for a human", "PAUSED" in out and "ACTIONS IN OUTBOX: 0" in out, out)
    rc, out = run(tmp, "day4_demo.py", "approve")
    check("approve runs the action once", "action executed" in out and "ACTIONS IN OUTBOX: 1" in out, out)
    rc, out = run(tmp, "day4_demo.py", "approve")
    check("second approve does nothing", "Nothing is waiting" in out and "ACTIONS IN OUTBOX: 1" in out, out)
    run(tmp, "day4_demo.py", "start", extra_env={"THREAD": "approval-2"})
    rc, out = run(tmp, "day4_demo.py", "reject", extra_env={"THREAD": "approval-2"})
    check("reject does not run the action", "ACTIONS IN OUTBOX: 1" in out and "action executed" not in out, out)


def day5(tmp: str) -> None:
    print("Day 5: audit trail")
    run(tmp, "day5_demo.py", "start")
    run(tmp, "day5_demo.py", "approve")
    rc, out = run(tmp, "day5_demo.py", "audit")
    check("audit shows 5 checkpoints", "(5 checkpoints)" in out, out)
    check("audit records who approved", "approve by ameen" in out, out)


def day6(tmp: str) -> None:
    print("Day 6: DBOS")
    rc, out = run(tmp, "day6_dbos_demo.py", "nothing")
    if "No module named 'dbos'" in out:
        print("  SKIP  dbos is not installed (pip install dbos)")
        return
    rc, out = run(tmp, "day6_dbos_demo.py", "crash-run", crash=True)
    check("crash-run dies inside step 3", rc != 0 and "CRASH" in out, out)
    rc, out = run(tmp, "day6_dbos_demo.py", "resume")
    check("resume finishes the workflow", "report done" in out and "FINISHED" in out, out)
    check("resume does NOT re-run steps 1 and 2", "fetch done" not in out and "think done" not in out, out)
    run(tmp, "day6_dbos_demo.py", "start")
    rc, out = run(tmp, "day6_dbos_demo.py", "approve")
    check("approve runs the action once", "action executed" in out and "ACTIONS IN OUTBOX: 1" in out, out)
    rc, out = run(tmp, "day6_dbos_demo.py", "approve")
    check("second approve does nothing", "Nothing is waiting" in out, out)


def patch_db_path(src: str, new_path: str) -> str:
    """Replace the DB_PATH line. A function is used as the replacement so that
    backslashes in Windows paths are kept as they are (re.sub would otherwise
    treat them as escape codes)."""
    new_line = f"DB_PATH = {new_path!r}"
    return re.sub(r"^DB_PATH = .*$", lambda _m: new_line, src, flags=re.M)


def main() -> int:
    missing = [f for f in FILES if not os.path.exists(os.path.join(HERE, f))]
    if missing:
        print("Missing files in this folder:", ", ".join(missing))
        print("Run dry_run.py from your project folder.")
        return 2
    with tempfile.TemporaryDirectory(prefix="dryrun_") as tmp:
        for f in FILES:
            shutil.copy(os.path.join(HERE, f), os.path.join(tmp, f))
        # durable_demo.py has a hard-coded D:\ path. Point the temporary copy at the temp folder.
        path = os.path.join(tmp, "durable_demo.py")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        src = patch_db_path(src, os.path.join(tmp, "checkpoints.sqlite"))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(src)
        print(f"Running in a temporary folder: {tmp}\n")
        for step in (day2, day3, day4, day5, day6):
            step(tmp)
            print()
    failed = [name for name, ok in results if not ok]
    print(f"{len(results) - len(failed)} of {len(results)} checks passed.")
    if failed:
        print("Failed:", "; ".join(failed))
        return 1
    print("All good. Your demos behave as described.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
