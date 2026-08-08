# state_machine

A fluent, checkpointed state machine for sequencing plain Python functions.

## Core ideas

- A step is a plain function: `def step(ctx: T) -> T`, where `T` is a frozen
  dataclass you define.
- Steps are wired together with predicate edges, evaluated in declaration order.
- Every run is checkpointed to SQLite before each step executes, so a crashed
  or failed run can be resumed from any step with its original context.
- Cycles are allowed (e.g. retry loops); a `max_steps` ceiling guards against
  infinite loops.

## Usage

```python
import dataclasses
from dataclasses import dataclass
from typing import Optional

from state_machine import StateMachine


@dataclass(frozen=True)
class AccountContext:
    account_id: int
    balance: int = 0
    result: Optional[str] = None


machine = StateMachine(context_type=AccountContext, db_path="./workflow.db", max_steps=1000)


@machine.step("check_balance")
def check_balance(ctx):
    return dataclasses.replace(ctx, balance=fetch_balance(ctx.account_id))


@machine.step("approve")
def approve(ctx):
    return dataclasses.replace(ctx, result="approved")


@machine.step("deny")
def deny(ctx):
    return dataclasses.replace(ctx, result="denied")


check_balance.to(approve, when=lambda ctx: ctx.balance > 0)
check_balance.otherwise(deny)

run_id = machine.run(AccountContext(account_id=7))
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

# or patch fields before retrying
machine.resume(run_id, start_at="check_balance", context_overrides={"account_id": 8})
```

`resume` re-executes the named step using the context exactly as it was
checkpointed right before that step last ran (reconstructed as a fresh
`context_type` instance), optionally patched via `dataclasses.replace(ctx,
**context_overrides)`.

### Context

Context is a **frozen dataclass** you define and pass to `StateMachine(context_type=...)`.
Steps don't mutate it — they return a new instance via `dataclasses.replace(ctx, ...)`.
Fields must be flat, JSON-serializable types (`str`, `int`, `float`, `bool`,
`None`, `list`, `dict`) since the context is persisted to SQLite as a
checkpoint before every step runs; nested dataclasses and enums as fields
aren't supported. `StateMachine` validates at construction that `context_type`
is a dataclass, and at `run()`/`resume()`/after every step that the value in
hand is actually an instance of it — a step that forgets `dataclasses.replace`
and returns something else fails immediately with a clear `TypeError` rather
than corrupting a checkpoint.

## Development

```bash
pip install -e .
pip install pytest
pytest
```
