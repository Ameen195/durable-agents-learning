# durable-agents-learning

Learning journal for building durable AI agents with [LangGraph](https://langchain-ai.github.io/langgraph/).

## Day 1: Checkpoint basics

`hello_graph.py` is a two-step graph that shows how LangGraph saves progress.

- **State**: a `TypedDict` with one `text` field that every step reads and writes.
- **Nodes**: `step_one` and `step_two` each append to `text`.
- **Edges**: `START -> step_one -> step_two -> END`.
- **Checkpointer**: the graph is compiled with `InMemorySaver`, which saves state after each step.
- **Thread ID**: `thread_id="demo-1"` names the run, so its checkpoints can be found again.
- **History**: `graph.get_state_history(config)` prints every saved checkpoint.

### Run it

```bash
pip install -r requirements.txt
python hello_graph.py
```

### Next

`InMemorySaver` is lost when the process exits. Next step is a persistent checkpointer (e.g. SQLite) so a run can resume after a crash.
