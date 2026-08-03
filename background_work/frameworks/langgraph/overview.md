# LangGraph Fits Checkpointed Agent and Human-Interrupt Workflows

> **Who this is for**: Python engineers building graph-shaped LLM/agent workflows whose state, interruptions, and resume points must survive process loss.

Before reading this, compare checkpointed graphs with service workflows in **[State-Machine Design](../../03_state_machine_design.md)**.

---

## 1. Agent state needs a durable cursor, not a sleeping request

An agent researches, proposes an action, waits overnight for a human, and resumes with the full message/tool context. Keeping the request or worker alive cannot survive deployment. Re-running from the start repeats model and tool calls and may produce a different path.

LangGraph models execution as state, nodes, and edges. A **checkpointer** saves state at super-step boundaries under a stable `thread_id`; an **interrupt** persists the wait and resumes when a `Command` supplies input. The official persistence guide describes checkpoints, pending writes, replay, and thread identity: [Persistence](https://docs.langchain.com/oss/python/langgraph/persistence). Checked 2026-08-03.

```text
thread_id=editorial-run-42
  checkpoint: research complete
  checkpoint: draft proposed
  interrupt:  waiting for reviewer
  resume:     approved
  checkpoint: generation next
```

> **The near-miss**: a checkpoint is not a cache of a function return. Resuming or time-traveling can re-execute nodes after a checkpoint, including LLM, API, and interrupt calls.

---

## 2. The minimal graph persists and resumes one approval

This example uses an in-memory checkpointer only to make the mechanism runnable. Production requires a durable checkpointer.

```python
from typing_extensions import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class EditorialState(TypedDict, total=False):
    draft: str
    reviewer_id: str
    approved: bool
    artifact: str


def prepare_draft(state: EditorialState) -> EditorialState:
    return {"draft": "verified research draft"}


def review(state: EditorialState) -> EditorialState:
    decision = interrupt({"draft": state["draft"], "question": "Approve?"})
    return {
        "approved": bool(decision["approved"]),
        "reviewer_id": str(decision["reviewer_id"]),
    }


def generate(state: EditorialState) -> EditorialState:
    if not state["approved"]:
        return {"artifact": "rejected"}
    return {"artifact": f"final:{state['draft']}"}


builder = StateGraph(EditorialState)
builder.add_node("prepare_draft", prepare_draft)
builder.add_node("review", review)
builder.add_node("generate", generate)
builder.add_edge(START, "prepare_draft")
builder.add_edge("prepare_draft", "review")
builder.add_edge("review", "generate")
builder.add_edge("generate", END)
graph = builder.compile(checkpointer=InMemorySaver())


if __name__ == "__main__":
    config = {"configurable": {"thread_id": "editorial-run-42"}}
    paused = graph.invoke({}, config)
    assert "__interrupt__" in paused
    completed = graph.invoke(
        Command(resume={"approved": True, "reviewer_id": "editor-7"}),
        config,
    )
    assert completed["artifact"] == "final:verified research draft"
    print(completed)
```

Success is a first invocation containing `__interrupt__` and a second invocation producing the artifact under the same `thread_id`. Using a different ID starts a different thread and does not resume the checkpoint.

> **Key insight**: The checkpoint makes graph state durable; it does not make node effects exactly once. Stable thread identity recovers control flow, while operation identity recovers external effects.

---

## 3. Production durability needs an external checkpointer

`InMemorySaver` disappears with the process and is suitable only for tests. Use a supported database-backed checkpointer or the managed Agent Server persistence path, configure retention/encryption, and test restart against the actual store.

The persisted contract includes:

- Stable `thread_id` mapped to the business workflow/run.
- Serializable, bounded state; large artifacts live behind immutable references.
- Checkpoint and pending-write retention sized for active threads.
- Access control for message, prompt, tool, and human-review data.
- Search/operational index for interrupted, busy, and failed threads.
- Backup, restore, schema migration, and deletion behavior.

The checkpointer saves state at super-step boundaries; successful node writes within a failed parallel super-step can be retained as pending writes. Recovery granularity therefore follows node and super-step design, not arbitrary Python lines.

---

## 4. Interrupts restart the node from its beginning

The official interrupt guide states that resuming restarts the node containing `interrupt()` from the beginning. Code before the interrupt executes again: [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts). Checked 2026-08-03.

```text
node begins
  → code before interrupt executes
  → interrupt persists wait
  → resume restarts node
  → code before interrupt executes again
  → resume value becomes interrupt() result
```

Keep pre-interrupt work pure or idempotent. Do not send an email, charge, or mutate an external system before `interrupt()` unless the operation uses a stable key and a durable local/provider record. Do not reorder multiple interrupts in the same node; resume values are matched by their execution order.

⚠️ Wrapping `interrupt()` in a broad `try/except` can catch the runtime's control-flow exception and prevent the graph from pausing correctly.

---

## 5. Time travel re-executes later nodes

Replaying from a prior checkpoint skips nodes before it and re-executes nodes after it. The official time-travel guide explicitly includes LLM calls, API requests, and interrupts among the re-executed work: [Time travel](https://docs.langchain.com/oss/python/langgraph/use-time-travel). Checked 2026-08-03.

Design tool/provider nodes like background activities:

```text
operation_key = thread_id + logical_node_purpose + definition_version
request_hash  = canonical tool input
local record  = INTENT → SUCCEEDED
checkpoint    = stores result reference, not the only evidence of the effect
```

For non-repeatable exploration, fork under a new business operation scope. Reusing the original operation key deliberately returns the original effect rather than creating an alternative one.

---

## 6. Graph changes are compatibility changes for saved threads

LangGraph applies current deployed graph code to persisted threads. Its current compatibility guidance warns that removing/renaming a node can break a thread paused at that node, and that renamed or incompatibly changed state fields can break old checkpoints: [Backward compatibility](https://docs.langchain.com/oss/python/langgraph/backward-compatibility). Checked 2026-08-03.

Use an add-then-remove migration:

- Add new state keys as optional with defaults for old checkpoints.
- Keep old node names as routing shims through a drain window.
- Dual-read/dual-write renamed state temporarily.
- Inspect `get_state()` and `get_state_history()` for representative in-flight threads before deployment.
- Record a graph/business definition version in state when old and new behavior must differ.

⚠️ A graph that resumes technically can still violate business compatibility if the new conditional edge interprets old state differently.

---

## 7. Choose LangGraph for graph state, not generic queues

Use LangGraph when LLM/tool loops, branching graph state, streaming, human interrupts, checkpoint inspection, and alternative-path replay are central. Use a database-backed state machine for a small service lifecycle whose source of truth is relational business state. Use a Temporal-class engine when general service-workflow timers, signals, activity retries, and long-lived deterministic orchestration dominate rather than agent state.

Do not use a checkpointer as the only record of a payment, email, or domain entity. Do not use LangGraph for one independent job merely to obtain retries.

**How you know it is working**: stop the runtime at an interrupt and mid-graph, restart it against the durable store, resume the same thread, and assert one effect per operation key. Monitor checkpoint-write failures, interrupted/error thread age, state size, replayed node count, and graph-version compatibility.

---

**Next**: [Part 9: Decision Guide](../../09_decision_guide.md)
