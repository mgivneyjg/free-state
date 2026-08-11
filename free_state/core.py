from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, TypeVar, cast, final

from .exceptions import MaxStepsExceededError, NoMatchingEdgeError, UnknownRunError
from .storage import Storage

if TYPE_CHECKING:
    from _typeshed import DataclassInstance

    C = TypeVar("C", bound=DataclassInstance)
else:
    C = TypeVar("C")

Predicate = Callable[[C], bool]
StepFn = Callable[[C], C]


@dataclass
class _Edge(Generic[C]):
    target: "Step[C]"
    when: Predicate[C]


@final
class Step(Generic[C]):
    def __init__(self, name: str, fn: StepFn[C]) -> None:
        self.name: str = name
        self.fn: StepFn[C] = fn
        self._edges: list[_Edge[C]] = []
        self._default: Step[C] | None = None

    def to(self, target: Step[C], when: Predicate[C]) -> Step[C]:
        self._edges.append(_Edge(target=target, when=when))
        return self

    def otherwise(self, target: Step[C]) -> Step[C]:
        self._default = target
        return self

    def resolve_next(self, context: C) -> Step[C] | None:
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

    def __call__(self, context: C) -> C:
        return self.fn(context)

    # __repr__ is always an override of object.__repr__; adding a real
    # @override here would require typing_extensions on Python < 3.12 just
    # for this one dunder method, which isn't worth the new dependency.
    def __repr__(self) -> str:  # pyright: ignore[reportImplicitOverride]
        return f"Step({self.name!r})"


@final
class StateMachine(Generic[C]):
    def __init__(
        self,
        context_type: type[C],
        db_path: str = "./.free_state.db",
        max_steps: int = 1000,
    ) -> None:
        # C is bound to DataclassInstance, so a statically well-typed caller
        # always satisfies this; the check stays as a runtime guard for
        # callers that pass an arbitrary type without type-checking it.
        if not dataclasses.is_dataclass(context_type):
            raise TypeError(  # pyright: ignore[reportUnreachable]
                f"context_type must be a dataclass type, got {context_type!r}"
            )
        self.context_type: type[C] = context_type
        self.max_steps: int = max_steps
        self.steps: dict[str, Step[C]] = {}
        self._start: str | None = None
        self._storage: Storage = Storage(db_path)

    def step(self, name: str) -> Callable[[StepFn[C]], Step[C]]:
        if name in self.steps:
            raise ValueError(f"step '{name}' is already registered")

        def decorator(fn: StepFn[C]) -> Step[C]:
            step_obj = Step(name, fn)
            self.steps[name] = step_obj
            if self._start is None:
                self._start = name
            return step_obj

        return decorator

    def run(self, context: C) -> str:
        if self._start is None:
            raise RuntimeError("no steps have been registered on this machine")
        self._validate_context(context)
        run_id = str(uuid.uuid4())
        self._storage.create_run(run_id, self._start)
        _ = self._execute(run_id, self._start, context)
        return run_id

    def resume(
        self,
        run_id: str,
        start_at: str,
        context_overrides: dict[str, object] | None = None,
    ) -> str:
        if start_at not in self.steps:
            raise KeyError(f"unknown step '{start_at}'")
        raw = self._storage.load_latest_checkpoint(run_id, start_at)
        if raw is None:
            raise UnknownRunError(
                f"no checkpoint found for run '{run_id}' at step '{start_at}'"
            )
        # DataclassInstance has no statically-known __init__ signature, so the
        # constructor call itself has to go through a loosely-typed callable.
        construct = cast(Callable[..., C], self.context_type)
        context = construct(**raw)
        if context_overrides:
            context = dataclasses.replace(context, **context_overrides)
        self._validate_context(context)
        self._storage.update_run_status(run_id, "running", start_at)
        _ = self._execute(run_id, start_at, context)
        return run_id

    def list_runs(self, status: str | None = None) -> list[dict[str, object]]:
        return self._storage.list_runs(status)

    def _validate_context(self, context: C) -> None:
        if not isinstance(context, self.context_type):
            raise TypeError(
                f"expected context of type {self.context_type.__name__}, "
                + f"got {type(context).__name__}"
            )

    def _execute(self, run_id: str, start_step: str, context: C) -> C:
        current = self.steps[start_step]
        ctx = context
        steps_taken = 0

        while True:
            steps_taken += 1
            if steps_taken > self.max_steps:
                self._storage.update_run_status(run_id, "failed", current.name)
                raise MaxStepsExceededError(
                    f"run '{run_id}' exceeded max_steps={self.max_steps} at step "
                    + f"'{current.name}' (possible infinite loop)"
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
