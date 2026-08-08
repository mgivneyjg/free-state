# state_machine

A fluent, checkpointed state machine for sequencing plain Python functions.

## Core ideas

- A step is a plain function: `def step(ctx: dict) -> dict`.
- Steps are wired together with predicate edges, evaluated in declaration order.
- Every run is checkpointed to SQLite before each step executes, so a crashed
  or failed run can be resumed from any step with its original context.
- Cycles are allowed (e.g. retry loops); a `max_steps` ceiling guards against
  infinite loops.

## Usage

```python
from state_machine import StateMachine

machine = StateMachine(db_path="./workflow.db", max_steps=1000)


@machine.step("check_balance")
def check_balance(ctx):
    ctx["balance"] = fetch_balance(ctx["account_id"])
    return ctx


@machine.step("approve")
def approve(ctx):
    ctx["result"] = "approved"
    return ctx


@machine.step("deny")
def deny(ctx):
    ctx["result"] = "denied"
    return ctx


check_balance.to(approve, when=lambda ctx: ctx["balance"] > 0)
check_balance.otherwise(deny)

run_id = machine.run({"account_id": 7})
```

The first step registered on a machine is its implicit entry point. Any step
with no outgoing edges is terminal.

### Branching

Chain `.to(step, when=predicate)` for conditional edges and finish with
`.otherwise(step)` for the required fallback. Edges are evaluated in the
order they were declared; the first matching predicate wins. If nothing
matches and there's no `.otherwise()`, the engine raises `NoMatchingEdgeError`.

### Recovering a failed run

```python
machine.list_runs(status="failed")
# -> [{"run_id": "...", "status": "failed", "current_step": "check_balance", "updated_at": "..."}]

machine.resume(run_id, start_at="check_balance")

# or patch the context before retrying
machine.resume(run_id, start_at="check_balance", context_overrides={"account_id": 8})
```

`resume` re-executes the named step using the context exactly as it was
checkpointed right before that step last ran, optionally patched with
`context_overrides`.

### Context

Context is a plain `dict` and must be JSON-serializable, since it's persisted
to SQLite as a checkpoint before every step runs. Pass IDs, not live objects.

## Development

```bash
pip install -e .
pip install pytest
pytest
```
