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

## Day 2: Persistent vs durable

`durable_demo.py` is a 3-node graph (`fetch -> think -> report`). `think` stops the program when the environment variable `CRASH` is `1`.

Run these in PowerShell from the project folder (venv activated):

```powershell
# 1. In-memory: crash, then try to resume
$env:CRASH = "1"
python durable_demo.py memory run      # dies at think
Remove-Item Env:CRASH                  # clear the flag, or resume will crash again
python durable_demo.py memory resume   # "NOTHING (state lost)"

# 2. SQLite: crash, then resume
$env:CRASH = "1"
python durable_demo.py sqlite run      # dies at think
Remove-Item Env:CRASH
python durable_demo.py sqlite resume   # resumes at think, fetch is NOT re-run
```

The only change between the two runs is one line: `InMemorySaver()` -> `SqliteSaver(sqlite3.connect(DB_PATH))`. Checkpoints go to `checkpoints.sqlite` on D:. Resuming uses `graph.invoke(None, config)` with the same `thread_id`.

Notes:

- To run the SQLite demo again from scratch, delete `checkpoints.sqlite` or change the `thread_id`. A finished thread has nothing left to resume.
- On resume, LangGraph re-runs the node that was interrupted (here `think`) from its start, not from the middle. Anything with a side effect inside that node can run twice. That is the problem Day 3 addresses.

## Day 3: Tools that run twice

`day3_demo.py` is a 3-node graph (`prepare -> send_email -> send_email -> report`) where `send_email` has a side effect. With `CRASH=1` it dies right after sending, before the checkpoint is saved.

```powershell
$env:CRASH = "1"
python day3_demo.py naive run
Remove-Item Env:CRASH
python day3_demo.py naive resume   # EMAILS IN OUTBOX: 2  (the bug)

$env:CRASH = "1"
python day3_demo.py safe run
Remove-Item Env:CRASH
python day3_demo.py safe resume    # EMAILS IN OUTBOX: 1  (the fix)
```

Checkpoints give at-least-once execution: an interrupted step re-runs from its start. The fix is an idempotency key (`<thread_id>:send_email`) that the receiver uses to ignore repeats, which gives an exactly-once effect.
