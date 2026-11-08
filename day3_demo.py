"""Day 3: the double-run problem and the idempotency fix.

Usage:
    python day3_demo.py <naive|safe> <run|resume>

The graph is: prepare -> send_email -> report.
`send_email` has a SIDE EFFECT (it appends a line to day3_outbox_<mode>.txt, our fake
"email provider"). When CRASH=1 the program dies right AFTER the email is
sent but BEFORE LangGraph can save the checkpoint for that step.
"""
import os
import sqlite3
import sys
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "day3_checkpoints.sqlite")
# One outbox file per mode, so the two experiments never mix.
OUTBOX = os.path.join(HERE, f"day3_outbox_{sys.argv[1]}.txt")


class State(TypedDict):
    log: list[str]


def prepare(state: State) -> dict:
    print("[1/3] prepare done", flush=True)
    return {"log": state["log"] + ["prepare"]}


def send_naive(message: str, key: str) -> None:
    """Sends every time it is called. No memory of what was already sent."""
    with open(OUTBOX, "a", encoding="utf-8") as f:
        f.write(f"{message}\n")


def send_once(message: str, key: str) -> None:
    """Idempotent: the idempotency key makes a repeat call do nothing."""
    if os.path.exists(OUTBOX):
        with open(OUTBOX, encoding="utf-8") as f:
            if any(line.startswith(key + "|") for line in f):
                print(f"   (key {key} already sent, skipping)", flush=True)
                return
    with open(OUTBOX, "a", encoding="utf-8") as f:
        f.write(f"{key}|{message}\n")


def make_send_node(send):
    def send_email(state: State, config: RunnableConfig) -> dict:
        # Same key on every retry of this step of this run.
        key = f"{config['configurable']['thread_id']}:send_email"
        send("Your report is ready", key)
        print("[2/3] send_email step finished", flush=True)
        if os.environ.get("CRASH") == "1":
            print("      CRASH! after sending, before the checkpoint was saved", flush=True)
            raise SystemExit(1)
        return {"log": state["log"] + ["send_email"]}

    return send_email


def report(state: State) -> dict:
    print("[3/3] report done")
    return {"log": state["log"] + ["report"]}


def build(saver, send):
    b = StateGraph(State)
    b.add_node("prepare", prepare)
    b.add_node("send_email", make_send_node(send))
    b.add_node("report", report)
    b.add_edge(START, "prepare")
    b.add_edge("prepare", "send_email")
    b.add_edge("send_email", "report")
    b.add_edge("report", END)
    return b.compile(checkpointer=saver)


mode_kind, mode = sys.argv[1], sys.argv[2]
send = send_naive if mode_kind == "naive" else send_once
saver = SqliteSaver(sqlite3.connect(DB_PATH, check_same_thread=False))
graph = build(saver, send)
config = {"configurable": {"thread_id": f"job-{mode_kind}"}}

if mode == "run":
    graph.invoke({"log": []}, config)
    print("FINISHED:", graph.get_state(config).values)
else:
    snap = graph.get_state(config)
    print("Found saved state:", snap.values or "NOTHING", "| next:", snap.next)
    if snap.next:
        graph.invoke(None, config)
        print("FINISHED:", graph.get_state(config).values)

count = 0
if os.path.exists(OUTBOX):
    with open(OUTBOX, encoding="utf-8") as f:
        count = sum(1 for _ in f)
print(f"EMAILS IN OUTBOX: {count}")
