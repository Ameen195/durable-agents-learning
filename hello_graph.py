from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver


# 1. State: the "notebook" every step can read and write
class State(TypedDict):
    text: str


# 2. Nodes: each is a plain function that takes state and returns changes
def step_one(state: State) -> dict:
    print("Running step one")
    return {"text": state["text"] + " -> step one done"}


def step_two(state: State) -> dict:
    print("Running step two")
    return {"text": state["text"] + " -> step two done"}


# 3. Wire the steps in order: START -> step_one -> step_two -> END
builder = StateGraph(State)
builder.add_node("step_one", step_one)
builder.add_node("step_two", step_two)
builder.add_edge(START, "step_one")
builder.add_edge("step_one", "step_two")
builder.add_edge("step_two", END)

# 4. Compile with a checkpointer (the "save system")
graph = builder.compile(checkpointer=InMemorySaver())

# 5. Run it. The thread_id is the name tag for this run.
config = {"configurable": {"thread_id": "demo-1"}}
result = graph.invoke({"text": "start"}, config)
print("Result:", result["text"])

# 6. Look at the saved checkpoints (the "logbook")
print("\nSaved checkpoints:")
for snap in graph.get_state_history(config):
    print(" -", snap.next, snap.values)