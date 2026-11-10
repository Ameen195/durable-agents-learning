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

`day3_demo.py` is a 3-node graph (`prepare -> send_email -> report`) where `send_email` has a side effect. With `CRASH=1` it dies right after sending, before the checkpoint is saved.

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

## Day 4: Human approval with interrupt and resume

`day4_demo.py` is a 3-node graph (`draft -> approval -> execute`). The `approval` node calls `interrupt()`, which saves the run to SQLite and stops the program. A later, separate program run resumes it with `Command(resume=...)`.

```powershell
python day4_demo.py start     # pauses, program exits
python day4_demo.py status    # shows it is waiting for a human
python day4_demo.py approve   # new program run: resumes and executes the action
python day4_demo.py approve   # "Nothing is waiting for approval"
```

- The human can answer from a different program run (or days later) because the pause lives in the SQLite checkpoint, found again by the same `thread_id`.
- On resume the `approval` node runs again from its top, so code before `interrupt()` must be free of side effects.
- The `execute` step reuses the Day 3 idempotency key, so the action happens once.
- Run `$env:THREAD = "approval-2"` first to start an independent job, for example to test the reject path.

## Day 5: The audit trail

`day5_demo.py` reuses the Day 4 graph (`draft -> approval -> execute`) and adds a way to **read back** everything the run did. LangGraph saves a checkpoint every time a step finishes, so the checkpoint history is already an audit log.

```powershell
python day5_demo.py start     # draft, then pause for approval
python day5_demo.py audit     # timeline so far: 3 checkpoints, PAUSED at approval
python day5_demo.py approve   # separate run: resume as "ameen" and finish
python day5_demo.py audit     # full timeline: 5 checkpoints, "Decision: approve by ameen"
python day5_demo.py raw       # the raw rows in the SQLite checkpoints table
```

- `graph.get_state_history(config)` returns every checkpoint for a `thread_id`, newest first. The demo reverses it so it reads oldest first.
- Each checkpoint stores the full state, a timestamp, and which step runs next. The step that just finished is the previous checkpoint's "next".
- The human's answer is `{"decision": ..., "by": ...}`, so the trail records **who** approved, not just that someone did.
- Each row in the `checkpoints` table points to its parent, so the history is a chain you can replay or inspect later.
- Run `$env:THREAD = "other-name"` to audit a different run.
