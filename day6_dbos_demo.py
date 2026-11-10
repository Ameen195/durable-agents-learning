"""Day 6: the same ideas with a durable-execution engine (DBOS), no Docker needed.

LangGraph (Days 1-5): you build a graph and give it a checkpointer.
DBOS (today): you write ordinary Python functions and add two decorators.
    @DBOS.step()      a unit of work whose result is saved when it finishes
    @DBOS.workflow()  the function that calls steps; DBOS resumes it after a crash

DBOS stores its records in SQLite here (a file next to this script).

Usage (each command is a separate program run):

  Part 1: crash and automatic recovery
    $env:CRASH = "1"; python day6_dbos_demo.py crash-run      # dies inside step 3
    Remove-Item Env:CRASH
    python day6_dbos_demo.py resume                           # steps 1 and 2 are NOT re-run

  Part 2: human approval that survives a restart
    python day6_dbos_demo.py start      # draft, then waits for approval; program exits
    python day6_dbos_demo.py status     # shows the saved workflow and its steps
    python day6_dbos_demo.py approve    # new run: recovers the workflow, approves, finishes

Set WF=some-name to use a different workflow ID (a fresh run).
"""
import os
import sys
import time

from dbos import DBOS, SetWorkflowID

HERE = os.path.dirname(os.path.abspath(__file__))
DB_URL = "sqlite:///" + os.path.join(HERE, "day6_dbos.sqlite").replace("\\", "/")
OUTBOX = os.path.join(HERE, "day6_outbox.txt")
WF_ID = os.environ.get("WF", "refund-1")

DBOS(config={"name": "day6-demo", "system_database_url": DB_URL, "run_admin_server": False})


# ---------- Part 1: crash and recover ----------

@DBOS.step()
def fetch() -> str:
    print("[1/3] fetch done", flush=True)
    return "data"


@DBOS.step()
def think(data: str) -> str:
    print("[2/3] think done", flush=True)
    return data + "+thought"


@DBOS.step()
def report(thought: str) -> str:
    if os.environ.get("CRASH") == "1":
        print("[3/3] report ... CRASH! program stopped halfway", flush=True)
        os._exit(1)  # hard stop, like pulling the plug
    print("[3/3] report done", flush=True)
    return thought + "+reported"


@DBOS.workflow()
def pipeline() -> str:
    return report(think(fetch()))


# ---------- Part 2: human approval ----------

@DBOS.step()
def draft() -> str:
    print("[1/3] draft written", flush=True)
    return "Refund of Rs 5,000 to customer #42"


@DBOS.step()
def execute(text: str) -> None:
    """Idempotent send (Day 3): the key makes repeats do nothing."""
    key = f"{DBOS.workflow_id}:execute"
    if os.path.exists(OUTBOX):
        with open(OUTBOX, encoding="utf-8") as f:
            if any(line.startswith(key + "|") for line in f):
                print("   (already sent, skipping)", flush=True)
                return
    with open(OUTBOX, "a", encoding="utf-8") as f:
        f.write(f"{key}|{text}\n")
    print("[3/3] action executed", flush=True)


@DBOS.workflow()
def refund() -> dict:
    text = draft()
    print("[2/3] waiting for a human decision ...", flush=True)
    # recv() is durable: the wait is recorded, so it survives a restart.
    decision = DBOS.recv("approval", timeout_seconds=3600)
    if decision == "approve":
        execute(text)
    return {"draft": text, "decision": decision}


def show_outbox() -> None:
    n = 0
    if os.path.exists(OUTBOX):
        with open(OUTBOX, encoding="utf-8") as f:
            n = sum(1 for _ in f)
    print(f"ACTIONS IN OUTBOX: {n}")


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    DBOS.launch()  # on launch, DBOS automatically recovers unfinished workflows
    try:
        if command == "crash-run":
            with SetWorkflowID("pipeline-1"):
                print("FINISHED:", pipeline())
        elif command == "resume":
            handle = DBOS.retrieve_workflow("pipeline-1")
            print("Workflow status before waiting:", handle.get_status().status)
            print("FINISHED:", handle.get_result())
        elif command == "start":
            with SetWorkflowID(WF_ID):
                DBOS.start_workflow(refund)
            time.sleep(1.5)  # let it reach the waiting point
            print("PAUSED. You can close this program now. Run 'approve' whenever you like.")
            os._exit(0)  # simulate the program going away while waiting
        elif command == "status":
            handle = DBOS.retrieve_workflow(WF_ID)
            print("Workflow:", WF_ID, "| status:", handle.get_status().status)
            for s in DBOS.list_workflow_steps(WF_ID):
                print("  step", s["function_id"], s["function_name"])
        elif command == "approve":
            handle = DBOS.retrieve_workflow(WF_ID)
            if handle.get_status().status not in ("PENDING", "ENQUEUED"):
                print("Nothing is waiting for approval on this workflow.")
            else:
                DBOS.send(WF_ID, "approve", "approval")
                print("FINISHED:", handle.get_result())
        else:
            print("Unknown command. Use: crash-run | resume | start | status | approve")
        show_outbox()
    finally:
        DBOS.destroy()


if __name__ == "__main__":
    main()
