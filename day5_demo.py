"""Day 5: the audit trail. Every step of a run is recorded, and we can read it back.

Usage (each command is a separate program run):
    python day5_demo.py start      # draft -> pause for approval
    python day5_demo.py approve    # resume and finish (human = "ameen")
    python day5_demo.py audit      # print the full timeline of the run
    python day5_demo.py raw        # peek at the raw checkpoint rows in SQLite

Set THREAD=some-name to audit a different run (default: audit-1).

Same graph as Day 4: draft -> approval (PAUSE) -> execute.
New today: we READ the checkpoint history as an audit log, and the
approval step also records WHO approved.
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
DB_PATH = os.path.join(HERE, "day5_checkpoints.sqlite")
OUTBOX = os.path.join(HERE, "day5_outbox.txt")
THREAD = os.environ.get("THREAD", "audit-1")


class State(TypedDict):
    draft: str
    decision: str
    approved_by: str
    log: list[str]


def draft(state: State) -> dict:
    return {"draft": "Refund of Rs 5,000 to customer #42", "log": state["log"] + ["draft"]}


def approval(state: State) -> dict:
    # Everything before interrupt() re-runs on resume, so keep it side-effect free.
    answer = interrupt({"question": "Approve this action?", "draft": state["draft"]})
    # The human's answer carries their name, so the audit trail shows who decided.
    return {
        "decision": answer["decision"],
        "approved_by": answer["by"],
        "log": state["log"] + [f"approval:{answer['decision']}"],
    }


def send_once(message: str, key: str) -> None:
    """Idempotent send from Day 3."""
    if os.path.exists(OUTBOX):
        with open(OUTBOX, encoding="utf-8") as f:
            if any(line.startswith(key + "|") for line in f):
                return
    with open(OUTBOX, "a", encoding="utf-8") as f:
        f.write(f"{key}|{message}\n")


def execute(state: State, config: RunnableConfig) -> dict:
    send_once(state["draft"], f"{config['configurable']['thread_id']}:execute")
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


def print_audit(graph, config) -> None:
    """Turn the checkpoint history into a readable, oldest-first timeline."""
    history = list(graph.get_state_history(config))[::-1]  # API returns newest first
    if not history:
        print(f"No saved run found for thread '{config['configurable']['thread_id']}'.")
        return
    print(f"AUDIT TRAIL for thread '{config['configurable']['thread_id']}' "
          f"({len(history)} checkpoints)\n")
    print(f"{'#':<3} {'saved at (UTC)':<20} {'event':<32} {'next step':<12} log so far")
    print("-" * 100)
    for i, snap in enumerate(history):
        # A checkpoint is saved when a step FINISHES, so the step that just
        # finished is whatever the previous checkpoint said was "next".
        if i == 0:
            event = "run started"
        elif i == 1:
            event = "input saved"
        else:
            event = "finished: " + ",".join(history[i - 1].next)
        if any(t.interrupts for t in snap.tasks):
            event += " -> PAUSED"
        ts = (snap.created_at or "")[:19].replace("T", " ")
        nxt = ",".join(snap.next) or "-"
        print(f"{i:<3} {ts:<20} {event:<32} {nxt:<12} {snap.values.get('log', [])}")
    final = history[-1].values
    if final.get("approved_by"):
        print(f"\nDecision: {final['decision']} by {final['approved_by']}")


command = sys.argv[1]
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
graph = build(SqliteSaver(conn))
config = {"configurable": {"thread_id": THREAD}}

if command == "start":
    result = graph.invoke({"draft": "", "decision": "", "approved_by": "", "log": []}, config)
    if result.get("__interrupt__"):
        print("PAUSED. Run 'approve' when ready.")
elif command == "approve":
    if graph.get_state(config).next:
        graph.invoke(Command(resume={"decision": "approve", "by": "ameen"}), config)
        print("FINISHED.")
    else:
        print("Nothing is waiting for approval on this thread.")
elif command == "audit":
    print_audit(graph, config)
elif command == "raw":
    rows = conn.execute(
        "SELECT thread_id, checkpoint_id, parent_checkpoint_id FROM checkpoints "
        "WHERE thread_id = ? ORDER BY checkpoint_id", (THREAD,)
    ).fetchall()
    print(f"{len(rows)} rows in the 'checkpoints' table for thread '{THREAD}':")
    for r in rows:
        print(" ", r[1][:13] + "...", "<- parent", (r[2] or "none")[:13])
else:
    print("Unknown command. Use: start | approve | audit | raw")
