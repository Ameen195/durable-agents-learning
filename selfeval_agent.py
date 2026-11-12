"""SelfEvaluate recap agent: reads code, edits it (with human approval),
runs tests, and checks its own work. LangGraph + Groq.

Usage (PowerShell, inside D:\\Projects\\durable-agents with venv active):
    $env:GROQ_API_KEY = "gsk_..."
    python selfeval_agent.py reset      # restore the buggy demo files
    python selfeval_agent.py run        # run the agent
Optional env vars: GROQ_MODEL, SELFEVAL_DB=sqlite (survive restarts), THREAD.
"""
import os
import subprocess
import sys
from pathlib import Path
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command, interrupt

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE / "selfeval_workspace"
DB_PATH = HERE / "selfeval_checkpoints.db"
MAX_STEPS = 12  # hard stop so a confused model cannot loop forever

BUGGY_FILES = {
    "calculator.py": (
        "def add(a, b):\n    return a + b\n\n\n"
        "def divide(a, b):\n    return a * b  # BUG: should divide\n"
    ),
    "test_calculator.py": (
        "from calculator import add, divide\n\n\n"
        "def test_add():\n    assert add(2, 3) == 5\n\n\n"
        "def test_divide():\n    assert divide(10, 2) == 5\n"
    ),
}
TASK = "The tests in this workspace are failing. Find the bug, fix it, and prove the fix works."
SYSTEM = (
    "You are a careful coding agent working in a small workspace. "
    "Use tools to read files, then fix bugs with write_file (a human must approve each write). "
    "After every edit, call run_tests to check your own work. "
    "Only give a final answer once run_tests reports that tests pass. "
    "If a write is rejected, do not retry the same write; explain and stop."
)


def reset_workspace() -> None:
    WORKSPACE.mkdir(exist_ok=True)
    for name, text in BUGGY_FILES.items():
        (WORKSPACE / name).write_text(text, encoding="utf-8")


def safe_path(name: str) -> Path:
    """Resolve a file name and refuse anything outside the workspace."""
    p = (WORKSPACE / name).resolve()
    if WORKSPACE.resolve() not in p.parents:
        raise ValueError(f"path escapes workspace: {name}")
    return p


@tool
def list_files() -> str:
    """List files in the workspace."""
    return "\n".join(sorted(p.name for p in WORKSPACE.iterdir() if p.is_file()))


@tool
def read_file(path: str) -> str:
    """Read a text file from the workspace."""
    return safe_path(path).read_text(encoding="utf-8")


@tool
def write_file(path: str, content: str) -> str:
    """Overwrite a workspace file with new content (needs human approval)."""
    safe_path(path).write_text(content, encoding="utf-8")
    return f"wrote {path}"


@tool
def run_tests() -> str:
    """Run pytest in the workspace and return its output."""
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=WORKSPACE, capture_output=True, text=True, timeout=60,
    )
    return (r.stdout + r.stderr)[-1500:] or "no output"


TOOLS = [list_files, read_file, write_file, run_tests]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}


class State(MessagesState):
    approved: bool
    steps: int


def build_graph(llm_with_tools, checkpointer):
    def agent(state: State):
        reply = llm_with_tools.invoke([SystemMessage(SYSTEM)] + state["messages"])
        return {"messages": [reply], "steps": state.get("steps", 0) + 1}

    def route(state: State) -> Literal["approval", "tools", "__end__"]:
        calls = state["messages"][-1].tool_calls
        if not calls or state.get("steps", 0) >= MAX_STEPS:
            return END
        return "approval" if any(c["name"] == "write_file" for c in calls) else "tools"

    def approval(state: State):
        # No side effects here: this node re-runs from the top on resume.
        writes = [c for c in state["messages"][-1].tool_calls if c["name"] == "write_file"]
        answer = interrupt({"question": "Approve these writes?", "writes": writes})
        return {"approved": answer == "approve"}

    def tools(state: State):
        out = []
        for call in state["messages"][-1].tool_calls:
            if call["name"] == "write_file" and not state.get("approved"):
                out.append(ToolMessage("REJECTED by human. Do not retry.", tool_call_id=call["id"]))
                continue
            try:
                result = TOOLS_BY_NAME[call["name"]].invoke(call["args"])
            except Exception as e:  # tell the model; let it recover
                result = f"ERROR: {e}"
            out.append(ToolMessage(str(result), tool_call_id=call["id"]))
        return {"messages": out, "approved": False}

    g = StateGraph(State)
    g.add_node("agent", agent)
    g.add_node("approval", approval)
    g.add_node("tools", tools)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", route)
    g.add_edge("approval", "tools")
    g.add_edge("tools", "agent")
    return g.compile(checkpointer=checkpointer)


def drive(app, config, first_input, ask=input):
    """Run until done, asking the human whenever the graph pauses."""
    payload = first_input
    while True:
        for ev in app.stream(payload, config, stream_mode="updates"):
            for node, upd in ev.items():
                if node == "__interrupt__":
                    continue
                for m in (upd or {}).get("messages", []):
                    show(m)
        snap = app.get_state(config)
        if not snap.next:
            return snap.values
        req = snap.tasks[0].interrupts[0].value
        for w in req["writes"]:
            print(f"\n  WRITE REQUEST -> {w['args'].get('path')}\n{w['args'].get('content')}")
        ans = ask("Approve? [y/N] ").strip().lower()
        payload = Command(resume="approve" if ans == "y" else "reject")


def show(m):
    kind = m.type
    if kind == "ai" and m.tool_calls:
        for c in m.tool_calls:
            print(f"[agent] -> {c['name']}({list(c['args'])})")
    elif kind == "ai":
        print(f"[agent] {m.content}")
    elif kind == "tool":
        print(f"[tool ] {m.content[:300]}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "reset" or not WORKSPACE.exists():
        reset_workspace()
        print("Workspace reset to the buggy version.")
        if cmd == "reset":
            return
    if not os.environ.get("GROQ_API_KEY"):
        sys.exit('Set your key first:  $env:GROQ_API_KEY = "gsk_..."')
    if cmd == "models":  # ask Groq which models THIS key can use
        from groq import Groq
        for m in sorted(Groq().models.list().data, key=lambda m: m.id):
            print(m.id)
        return
    from langchain_groq import ChatGroq
    llm = ChatGroq(model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"), temperature=0)
    thread = os.environ.get("THREAD", "selfeval-1")
    config = {"configurable": {"thread_id": thread}, "recursion_limit": 40}
    first = {"messages": [HumanMessage(TASK)], "steps": 0}
    if os.environ.get("SELFEVAL_DB") == "sqlite":
        from langgraph.checkpoint.sqlite import SqliteSaver
        with SqliteSaver.from_conn_string(str(DB_PATH)) as saver:
            app = build_graph(llm.bind_tools(TOOLS), saver)
            resuming = bool(app.get_state(config).next)
            drive(app, config, None if resuming else first)
    else:
        app = build_graph(llm.bind_tools(TOOLS), InMemorySaver())
        drive(app, config, first)
    print("\nDone.")


if __name__ == "__main__":
    main()
