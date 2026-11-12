"""Offline tests: a scripted fake model replaces Groq."""
import selfeval_agent as s
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

FIX = "def add(a, b):\n    return a + b\n\n\ndef divide(a, b):\n    return a / b\n"


class Fake:
    def __init__(self, script): self.script, self.i = script, 0
    def invoke(self, _):
        m = self.script[min(self.i, len(self.script) - 1)]; self.i += 1
        return m.model_copy(update={"id": None})


def call(name, cid, **args): return AIMessage("", tool_calls=[{"name": name, "args": args, "id": cid}])


def run(script, answers, tmp_path, monkeypatch):
    monkeypatch.setattr(s, "WORKSPACE", tmp_path / "ws"); s.reset_workspace()
    app = s.build_graph(Fake(script), InMemorySaver())
    cfg = {"configurable": {"thread_id": "t"}, "recursion_limit": 40}
    it = iter(answers)
    out = s.drive(app, cfg, {"messages": [HumanMessage("go")], "steps": 0}, ask=lambda _: next(it))
    return out, (tmp_path / "ws" / "calculator.py").read_text()


HAPPY = [call("run_tests", "1"), call("write_file", "2", path="calculator.py", content=FIX),
         call("run_tests", "3"), AIMessage("fixed")]


def test_approve_fixes_and_tests_pass(tmp_path, monkeypatch):
    out, code = run(HAPPY, ["y"], tmp_path, monkeypatch)
    assert "a / b" in code and "passed" in out["messages"][-2].content


def test_reject_leaves_file_untouched(tmp_path, monkeypatch):
    script = HAPPY[:2] + [AIMessage("stopped")]
    out, code = run(script, ["n"], tmp_path, monkeypatch)
    assert "a * b" in code and "REJECTED" in out["messages"][-2].content


def test_path_escape_blocked(tmp_path, monkeypatch):
    script = [call("read_file", "1", path="../secret.txt"), AIMessage("ok")]
    out, _ = run(script, [], tmp_path, monkeypatch)
    assert "escapes workspace" in out["messages"][-2].content


def test_step_cap_stops_loop(tmp_path, monkeypatch):
    out, _ = run([call("list_files", "1")], [], tmp_path, monkeypatch)
    assert out["steps"] == s.MAX_STEPS
