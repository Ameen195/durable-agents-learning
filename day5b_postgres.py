"""Day 5b: the same audit-trail agent, but checkpoints live in PostgreSQL.

Compared with Day 5, the ONLY real change is the saver:
    SqliteSaver(sqlite3.connect("file.sqlite"))   ->   PostgresSaver.from_conn_string(DSN)
The graph, nodes, interrupt() and Command(resume=...) are identical.

Why Postgres? SQLite is a file next to your code. Postgres is a separate server:
many programs (or machines) can share it, it is backed up and secured like any
database, and it is what production systems normally use.

Start Postgres first (PowerShell, Docker Desktop must be running):
    docker run --name pg-agent -e POSTGRES_PASSWORD=dev -p 5432:5432 -d postgres:16

Usage (each command is a separate program run):
    python day5b_postgres.py start      # draft -> pause for approval
    python day5b_postgres.py approve    # resume and finish (human = "ameen")
    python day5b_postgres.py audit      # print the timeline of the run
    python day5b_postgres.py tables     # show the tables LangGraph created
    python day5b_postgres.py raw        # peek at the raw checkpoint rows

Optional environment variables:
    DATABASE_URL  connection string (default matches the docker command above)
    THREAD        run name (default: pg-audit-1)
"""
import os
import sys
from typing import TypedDict

import psycopg
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

HERE = os.path.dirname(os.path.abspath(__file__))
OUTBOX = os.path.join(HERE, "day5b_outbox.txt")
DSN = os.environ.get("DATABASE_URL", "postgresql://postgres:dev@localhost:5432/postgres")
THREAD = os.environ.get("THREAD", "pg-audit-1")


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
    history = list(graph.get_state_history(config))[::-1]  # newest first -> oldest first
    if not history:
        print(f"No saved run found for thread '{config['configurable']['thread_id']}'.")
        return
    print(f"AUDIT TRAIL for thread '{config['configurable']['thread_id']}' "
          f"({len(history)} checkpoints, stored in PostgreSQL)\n")
    print(f"{'#':<3} {'saved at (UTC)':<20} {'event':<32} {'next step':<12} log so far")
    print("-" * 100)
    for i, snap in enumerate(history):
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


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    config = {"configurable": {"thread_id": THREAD}}

    # from_conn_string is a context manager: it opens the connection and closes it for us.
    with PostgresSaver.from_conn_string(DSN) as saver:
        saver.setup()  # creates the checkpoint tables if missing; safe to run every time
        graph = build(saver)

        if command == "start":
            result = graph.invoke(
                {"draft": "", "decision": "", "approved_by": "", "log": []}, config
            )
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
        elif command in ("tables", "raw"):
            with psycopg.connect(DSN) as conn:
                if command == "tables":
                    rows = conn.execute(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public' AND table_name LIKE 'checkpoint%' "
                        "ORDER BY table_name"
                    ).fetchall()
                    print("Tables LangGraph created in Postgres:")
                    for (name,) in rows:
                        print(" ", name)
                else:
                    rows = conn.execute(
                        "SELECT checkpoint_id, parent_checkpoint_id FROM checkpoints "
                        "WHERE thread_id = %s ORDER BY checkpoint_id",
                        (THREAD,),
                    ).fetchall()
                    print(f"{len(rows)} rows in the 'checkpoints' table for thread '{THREAD}':")
                    for cid, parent in rows:
                        print(" ", cid[:13] + "...", "<- parent", (parent or "none")[:13])
        else:
            print("Unknown command. Use: start | approve | audit | tables | raw")


if __name__ == "__main__":
    try:
        main()
    except psycopg.OperationalError as exc:
        print("Could not connect to PostgreSQL.")
        print("  1. Is Docker Desktop running?  Check:  docker ps")
        print("  2. Is the container started?   docker start pg-agent")
        print(f"  Details: {str(exc).strip().splitlines()[0]}")
        sys.exit(1)
