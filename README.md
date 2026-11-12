# durable-agents-learning

Six small, tested demos of how to make AI agents durable: they survive crashes, never repeat an action, wait for human approval, and leave an audit trail. Built in Python with [LangGraph](https://langchain-ai.github.io/langgraph/), SQLite and [DBOS](https://docs.dbos.dev/).

The key idea: checkpoints give you at-least-once execution, and idempotency keys turn that into an exactly-once effect.

| Day | File | What it shows |
| --- | --- | --- |
| 1 | `hello_graph.py` | Checkpoints: state is saved after every step |
| 2 | `durable_demo.py` | Persistent vs durable: crash and resume with SQLite |
| 3 | `day3_demo.py` | A tool that runs twice, fixed with an idempotency key |
| 4 | `day4_demo.py` | Human approval with `interrupt()` and resume |
| 5 | `day5_demo.py` | An audit trail read from the checkpoint history |
| 5b | `day5b_postgres.py` | The same agent on PostgreSQL (tested against PostgreSQL 16) |
| 6 | `day6_dbos_demo.py` | The same ideas with the DBOS durable-execution library |

Quick start (PowerShell):

```powershell
pip install -r requirements.txt
pip install dbos
python dry_run.py    # runs the demos from scratch and checks every result
```

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

## Day 5b: Same audit trail, stored in PostgreSQL

`day5b_postgres.py` is the Day 5 agent with one real change: the checkpoints go to a PostgreSQL server instead of a SQLite file. The graph, `interrupt()` and `Command(resume=...)` are unchanged.

```powershell
pip install langgraph-checkpoint-postgres "psycopg[binary,pool]"
docker run --name pg-agent -e POSTGRES_PASSWORD=dev -p 5432:5432 -d postgres:16

python day5b_postgres.py start     # pauses for approval
python day5b_postgres.py audit     # timeline, read from PostgreSQL
python day5b_postgres.py approve   # separate run: resume and finish
python day5b_postgres.py audit     # full timeline
python day5b_postgres.py tables    # the tables LangGraph created
python day5b_postgres.py raw       # raw rows in the checkpoints table
```

- `PostgresSaver.from_conn_string(DSN)` replaces `SqliteSaver(...)`, and `saver.setup()` creates the tables on first use.
- LangGraph creates four tables: `checkpoints`, `checkpoint_blobs`, `checkpoint_writes` and `checkpoint_migrations`.
- The connection string comes from `DATABASE_URL` and defaults to the Docker command above. The password `dev` is for local practice only. Never commit real credentials.
- Postgres is a separate server, so several programs or machines can share one history, and it can be backed up and secured like any database.
- Stop and restart the container with `docker stop pg-agent` and `docker start pg-agent`. The data stays in the container until you remove it.

## Day 6: A durable-execution engine (DBOS), no Docker

`day6_dbos_demo.py` repeats the crash and approval ideas with [DBOS](https://docs.dbos.dev/), a library that makes ordinary Python functions durable. You mark units of work with `@DBOS.step()` and the function that calls them with `@DBOS.workflow()`. DBOS records each finished step in SQLite and resumes unfinished workflows when the program starts again.

```powershell
pip install dbos

# Part 1: crash and automatic recovery
$env:CRASH = "1"
python day6_dbos_demo.py crash-run   # dies inside step 3
Remove-Item Env:CRASH
python day6_dbos_demo.py resume      # steps 1 and 2 are NOT re-run

# Part 2: human approval that survives a restart
python day6_dbos_demo.py start       # draft, then waits; program exits
python day6_dbos_demo.py status      # saved workflow and its recorded steps
python day6_dbos_demo.py approve     # new run: recovers, approves, finishes
python day6_dbos_demo.py approve     # "Nothing is waiting"
```

LangGraph and DBOS compared:

| | LangGraph (Days 1-5) | DBOS (Day 6) |
|---|---|---|
| You write | A graph of nodes and edges | Plain Python functions with decorators |
| Saved at | Each finished node (checkpoint) | Each finished step |
| Pause for a human | `interrupt()` and `Command(resume=...)` | `DBOS.recv()` and `DBOS.send()` |
| Resume | Same `thread_id`, `invoke(None, ...)` | Automatic on program start, by workflow ID |

- The `workflow` function body runs again on recovery, but finished `step` results are replayed from the record, so steps are not repeated.
- Steps are still at-least-once. A step interrupted mid-way re-runs, so side effects keep the Day 3 idempotency key.
- The same rule from Day 4 applies: do not put side effects in the workflow body outside a step.

## Verify everything: the dry run

`dry_run.py` runs the Day 2 to Day 6 demos from scratch and checks each expected result (for example: the in-memory run loses its state, the safe version sends one email, an approved run executes the action once, and a DBOS crash resumes without repeating finished steps).

```powershell
python dry_run.py
```

- It copies the demo files into a temporary folder, so your own checkpoints, outbox files and Git history are never touched. The folder is deleted at the end.
- A healthy run ends with `18 of 18 checks passed.` The Day 6 checks are skipped if `dbos` is not installed.
- `day5b_postgres.py` is not covered because it needs a running Postgres server.

## SelfEvaluate recap agent (Groq)

A small tool-using LangGraph agent: it reads code, edits it only after a human approves (`interrupt`), runs the tests, and keeps going until its own check passes.

```powershell
pip install langchain-groq pytest
$env:GROQ_API_KEY = "gsk_..."      # never commit your key
python selfeval_agent.py reset     # restore the buggy demo files
python selfeval_agent.py run       # approve or reject each write with y/n
python -m pytest test_selfeval.py  # offline tests, no API key needed
```

Set `GROQ_MODEL` to change the model, and `SELFEVAL_DB=sqlite` to keep checkpoints on disk so a stopped run can resume.
