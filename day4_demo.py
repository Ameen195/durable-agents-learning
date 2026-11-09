"""Day 4: pause for a human approval, survive a restart, then continue.

Usage (each command is a SEPARATE program run, like days apart):
    python day4_demo.py start      # runs until it needs approval, then the program exits
    python day4_demo.py status     # shows what the saved run is waiting for
    python day4_demo.py approve    # new program run: resumes and finishes the job
    python day4_demo.py reject     # new program run: resumes and cancels the job

Graph: draft -> approval (PAUSES HERE) -> execute (sends the "refund email")
Set THREAD=some-name to run a fresh, independent job (default: approval-1).
"""
import os
import sqlite3
import sys
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "day4_checkpoints.sqlite")
OUTBOX = os.path.join(HERE, "day4_outbox.txt")
THREAD = os.environ.get("THREAD", "approval-1")


class State(TypedDict):
    draft: str
    decision: str
    log: list[str]


def draft(state: State) -> dict:
    print("[1/3] draft written", flush=True)
    return {"draft": "Refund of Rs 5,000 to customer #42", "log": state["log"] + ["draft"]}


def approval(state: State) -> dict:
    # WARNING: on resume this whole node runs again from the top.
    # So everything BEFORE interrupt() must be safe to repeat (no side effects!).
    print("[2/3] approval node started", flush=True)
    answer = interrupt({"question": "Approve this action?", "draft": state["draft"]})
    # The line below only runs after a human resumes the run.
    print(f"[2/3] human answered: {answer}", flush=True)
    return {"decision": answer, "log": state["log"] + [f"approval:{answer}"]}


def send_once(message: str, key: str) -> None:
    """Idempotent send from Day 3: the key makes repeats do nothing."""
    if os.path.exists(OUTBOX):
        with open(OUTBOX, encoding="utf-8") as f:
            if any(line.startswith(key + "|") for line in f):
                print(f"   (key {key} already sent, skipping)", flush=True)
                return
    with open(OUTBOX, "a", encoding="utf-8") as f:
        f.write(f"{key}|{message}\n")


def execute(state: State, config: RunnableConfig) -> dict:
    key = f"{config['configurable']['thread_id']}:execute"
    send_once(state["draft"], key)
    print("[3/3] action executed", flush=True)
    return {"log": state["log"] + ["execute"]}


def route(state: State) -> str:
    return "execute" if state["decision"] == "approve" else END


def build(saver):
    b = StateGraph(State)
    b.add_node("draft", draft)
    b.add_node("approval", approval)
    b.add_node("execute", execute)
    b.add_edge(START, "draft")
    b.add_edge("draft", "approval")
    b.add_conditional_edges("approval", route, {"execute": "execute", END: END})
    b.add_edge("execute", END)
    return b.compile(checkpointer=saver)


def show_outbox() -> None:
    n = 0
    if os.path.exists(OUTBOX):
        with open(OUTBOX, encoding="utf-8") as f:
            n = sum(1 for _ in f)
    print(f"ACTIONS IN OUTBOX: {n}")


command = sys.argv[1]
graph = build(SqliteSaver(sqlite3.connect(DB_PATH, check_same_thread=False)))
config = {"configurable": {"thread_id": THREAD}}
snap = graph.get_state(config)

if command == "start":
    result = graph.invoke({"draft": "", "decision": "", "log": []}, config)
    pending = result.get("__interrupt__")
    if pending:
        print("PAUSED. Waiting for a human:", pending[0].value)
        print("You can close this program now. Run 'approve' or 'reject' whenever you like.")
elif command == "status":
    print("Saved state:", snap.values or "NOTHING", "| next:", snap.next)
    for task in snap.tasks:
        for it in task.interrupts:
            print("Waiting for a human:", it.value)
elif command in ("approve", "reject"):
    if not snap.next:
        print("Nothing is waiting for approval on this thread.")
    else:
        graph.invoke(Command(resume=command), config)
        print("FINISHED:", graph.get_state(config).values)
else:
    print("Unknown command. Use: start | status | approve | reject")

show_outbox()
