"""Persistent vs durable demo.  Usage: python durable_demo.py <memory|sqlite> <run|resume>"""
import os
import sqlite3
import sys
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

DB_PATH = r"D:\Projects\durable-agents\checkpoints.sqlite"


class State(TypedDict):
    log: list[str]


def fetch(state: State) -> dict:
    print("[1/3] fetch done", flush=True)
    return {"log": state["log"] + ["fetch"]}


def think(state: State) -> dict:
    if os.environ.get("CRASH") == "1":
        print("[2/3] think ... CRASH! program stopped halfway", flush=True)
        raise SystemExit(1)  # like Ctrl+C: process ends, in-memory data is gone
    print("[2/3] think done")
    return {"log": state["log"] + ["think"]}


def report(state: State) -> dict:
    print("[3/3] report done")
    return {"log": state["log"] + ["report"]}


def build(saver):
    b = StateGraph(State)
    for name, fn in [("fetch", fetch), ("think", think), ("report", report)]:
        b.add_node(name, fn)
    b.add_edge(START, "fetch")
    b.add_edge("fetch", "think")
    b.add_edge("think", "report")
    b.add_edge("report", END)
    return b.compile(checkpointer=saver)


kind, mode = sys.argv[1], sys.argv[2]
# THE ONE LINE: swap this and the run survives a restart
saver = InMemorySaver() if kind == "memory" else SqliteSaver(sqlite3.connect(DB_PATH, check_same_thread=False))
graph = build(saver)
config = {"configurable": {"thread_id": "job-1"}}

if mode == "run":
    graph.invoke({"log": []}, config)
    print("FINISHED:", graph.get_state(config).values)
else:
    snap = graph.get_state(config)
    print("Found saved state:", snap.values or "NOTHING (state lost)", "| next:", snap.next)
    if snap.next:
        graph.invoke(None, config)  # None = continue from last checkpoint
        print("FINISHED:", graph.get_state(config).values)
