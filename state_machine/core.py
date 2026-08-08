from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional, Type

from .exceptions import MaxStepsExceededError, NoMatchingEdgeError, UnknownRunError
from .storage import Storage

Predicate = Callable[[Any], bool]
StepFn = Callable[[Any], Any]


@dataclass
class _Edge:
    target: "Step"
    when: Predicate


class Step:
    def __init__(self, name: str, fn: StepFn):
        self.name = name
        self.fn = fn
        self._edges: list[_Edge] = []
        self._default: Optional["Step"] = None

    def to(self, target: "Step", when: Predicate) -> "Step":
        self._edges.append(_Edge(target=target, when=when))
        return self

    def otherwise(self, target: "Step") -> "Step":
        self._default = target
        return self

    def resolve_next(self, context: Any) -> Optional["Step"]:
        if not self._edges and self._default is None:
            return None
        for edge in self._edges:
            if edge.when(context):
                return edge.target
        if self._default is not None:
            return self._default
        raise NoMatchingEdgeError(
            f"step '{self.name}': no edge predicate matched and no .otherwise() default was defined"
        )

    def __call__(self, context: Any) -> Any:
        return self.fn(context)

    def __repr__(self) -> str:
        return f"Step({self.name!r})"


class StateMachine:
    def __init__(
        self,
        context_type: Type[Any],
        db_path: str = "./.state_machine.db",
        max_steps: int = 1000,
    ):
        if not dataclasses.is_dataclass(context_type):
            raise TypeError(
                f"context_type must be a dataclass type, got {context_type!r}"
            )
        self.context_type = context_type
        self.max_steps = max_steps
        self.steps: dict[str, Step] = {}
        self._start: Optional[str] = None
        self._storage = Storage(db_path)

    def step(self, name: str):
        if name in self.steps:
            raise ValueError(f"step '{name}' is already registered")

        def decorator(fn: StepFn) -> Step:
            step_obj = Step(name, fn)
            self.steps[name] = step_obj
            if self._start is None:
                self._start = name
            return step_obj

        return decorator

    def run(self, context: Any) -> str:
        if self._start is None:
            raise RuntimeError("no steps have been registered on this machine")
        self._validate_context(context)
        run_id = str(uuid.uuid4())
        self._storage.create_run(run_id, self._start)
        self._execute(run_id, self._start, context)
        return run_id

    def resume(
        self,
        run_id: str,
        start_at: str,
        context_overrides: Optional[dict] = None,
    ) -> str:
        if start_at not in self.steps:
            raise KeyError(f"unknown step '{start_at}'")
        raw = self._storage.load_latest_checkpoint(run_id, start_at)
        if raw is None:
            raise UnknownRunError(
                f"no checkpoint found for run '{run_id}' at step '{start_at}'"
            )
        context = self.context_type(**raw)
        if context_overrides:
            context = dataclasses.replace(context, **context_overrides)
        self._validate_context(context)
        self._storage.update_run_status(run_id, "running", start_at)
        self._execute(run_id, start_at, context)
        return run_id

    def list_runs(self, status: Optional[str] = None) -> list:
        return self._storage.list_runs(status)

    def _validate_context(self, context: Any) -> None:
        if not isinstance(context, self.context_type):
            raise TypeError(
                f"expected context of type {self.context_type.__name__}, "
                f"got {type(context).__name__}"
            )

    def _execute(self, run_id: str, start_step: str, context: Any) -> Any:
        current = self.steps[start_step]
        ctx = context
        steps_taken = 0

        while True:
            steps_taken += 1
            if steps_taken > self.max_steps:
                self._storage.update_run_status(run_id, "failed", current.name)
                raise MaxStepsExceededError(
                    f"run '{run_id}' exceeded max_steps={self.max_steps} at step "
                    f"'{current.name}' (possible infinite loop)"
                )

            self._storage.save_checkpoint(run_id, current.name, dataclasses.asdict(ctx))
            self._storage.update_run_status(run_id, "running", current.name)

            try:
                ctx = current.fn(ctx)
                self._validate_context(ctx)
                next_step = current.resolve_next(ctx)
            except Exception:
                self._storage.update_run_status(run_id, "failed", current.name)
                raise

            if next_step is None:
                self._storage.update_run_status(run_id, "completed", current.name)
                return ctx

            current = next_step
