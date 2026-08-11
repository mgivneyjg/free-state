from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path

import pytest

from free_state import MaxStepsExceededError, NoMatchingEdgeError, StateMachine, Step


@dataclass(frozen=True)
class ApprovalContext:
    balance: int
    checked: bool = False
    result: str | None = None


def build_approval_machine(
    db_path: Path,
) -> tuple[
    StateMachine[ApprovalContext],
    Step[ApprovalContext],
    Step[ApprovalContext],
    Step[ApprovalContext],
]:
    machine = StateMachine(context_type=ApprovalContext, db_path=str(db_path))

    @machine.step("check_balance")
    def check_balance(ctx: ApprovalContext) -> ApprovalContext:
        return dataclasses.replace(ctx, checked=True)

    @machine.step("approve")
    def approve(ctx: ApprovalContext) -> ApprovalContext:
        return dataclasses.replace(ctx, result="approved")

    @machine.step("deny")
    def deny(ctx: ApprovalContext) -> ApprovalContext:
        return dataclasses.replace(ctx, result="denied")

    _ = check_balance.to(approve, when=lambda ctx: ctx.balance > 0)
    _ = check_balance.otherwise(deny)

    return machine, check_balance, approve, deny


def test_linear_run_reaches_terminal_step(tmp_path: Path) -> None:
    machine, *_ = build_approval_machine(tmp_path / "wf.db")
    run_id = machine.run(ApprovalContext(balance=10))

    runs = machine.list_runs()
    assert len(runs) == 1
    assert runs[0]["run_id"] == run_id
    assert runs[0]["status"] == "completed"
    assert runs[0]["current_step"] == "approve"


def test_default_edge_is_used_when_no_predicate_matches(tmp_path: Path) -> None:
    machine, *_ = build_approval_machine(tmp_path / "wf.db")
    _ = machine.run(ApprovalContext(balance=-5))

    runs = machine.list_runs()
    assert runs[0]["current_step"] == "deny"


def test_context_type_must_be_a_dataclass(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        # dict is deliberately not a dataclass; this exercises the runtime
        # guard that catches callers who don't type-check their code.
        StateMachine(context_type=dict, db_path=str(tmp_path / "wf.db"))  # pyright: ignore[reportArgumentType, reportUnusedCallResult]


def test_run_rejects_context_of_wrong_type(tmp_path: Path) -> None:
    @dataclass(frozen=True)
    class Other:
        y: int = 0

    machine = StateMachine(context_type=ApprovalContext, db_path=str(tmp_path / "wf.db"))

    @machine.step("start")
    def start(ctx: ApprovalContext) -> ApprovalContext:  # pyright: ignore[reportUnusedFunction]
        return ctx

    with pytest.raises(TypeError):
        # Other is deliberately the wrong context type for this machine;
        # this exercises the runtime guard, not the type checker.
        _ = machine.run(Other(y=1))  # pyright: ignore[reportArgumentType]


def test_step_returning_wrong_type_raises_and_marks_run_failed(tmp_path: Path) -> None:
    @dataclass(frozen=True)
    class Ctx:
        x: int = 0

    machine = StateMachine(context_type=Ctx, db_path=str(tmp_path / "wf.db"))

    @machine.step("forgot_replace")
    def forgot_replace(_ctx: Ctx) -> Ctx:  # pyright: ignore[reportUnusedFunction]
        # Deliberately returns the wrong type (should have used
        # dataclasses.replace(ctx, x=1)) to exercise the runtime check.
        return {"x": 1}  # pyright: ignore[reportReturnType]

    with pytest.raises(TypeError):
        _ = machine.run(Ctx())

    runs = machine.list_runs()
    assert runs[0]["status"] == "failed"
    assert runs[0]["current_step"] == "forgot_replace"


def test_step_without_default_raises_and_marks_run_failed(tmp_path: Path) -> None:
    @dataclass(frozen=True)
    class Ctx:
        flag: bool = False

    machine = StateMachine(context_type=Ctx, db_path=str(tmp_path / "wf.db"))

    @machine.step("a")
    def a(ctx: Ctx) -> Ctx:
        return ctx

    @machine.step("b")
    def b(ctx: Ctx) -> Ctx:
        return ctx

    _ = a.to(b, when=lambda ctx: False)

    with pytest.raises(NoMatchingEdgeError):
        _ = machine.run(Ctx())

    runs = machine.list_runs()
    assert runs[0]["status"] == "failed"
    assert runs[0]["current_step"] == "a"


def test_exception_in_step_marks_run_failed_and_reraises(tmp_path: Path) -> None:
    @dataclass(frozen=True)
    class Ctx:
        pass

    machine = StateMachine(context_type=Ctx, db_path=str(tmp_path / "wf.db"))

    @machine.step("boom")
    def boom(_ctx: Ctx) -> Ctx:  # pyright: ignore[reportUnusedFunction]
        raise ValueError("kaboom")

    with pytest.raises(ValueError):
        _ = machine.run(Ctx())

    runs = machine.list_runs()
    assert runs[0]["status"] == "failed"
    assert runs[0]["current_step"] == "boom"


def test_resume_reruns_failed_step_with_checkpointed_context(tmp_path: Path) -> None:
    @dataclass(frozen=True)
    class Ctx:
        seed: int
        done: bool = False

    machine = StateMachine(context_type=Ctx, db_path=str(tmp_path / "wf.db"))
    attempts = {"count": 0}

    @machine.step("flaky")
    def flaky(ctx: Ctx) -> Ctx:  # pyright: ignore[reportUnusedFunction]
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise ValueError("first attempt fails")
        return dataclasses.replace(ctx, done=True)

    with pytest.raises(ValueError):
        _ = machine.run(Ctx(seed=1))

    run_id = machine.list_runs()[0]["run_id"]
    assert isinstance(run_id, str)
    _ = machine.resume(run_id, start_at="flaky")

    runs = machine.list_runs()
    assert runs[0]["status"] == "completed"
    assert attempts["count"] == 2


def test_resume_context_overrides_are_applied(tmp_path: Path) -> None:
    @dataclass(frozen=True)
    class Ctx:
        api_key: str | None
        called_with: str | None = None

    machine = StateMachine(context_type=Ctx, db_path=str(tmp_path / "wf.db"))

    @machine.step("needs_key")
    def needs_key(ctx: Ctx) -> Ctx:  # pyright: ignore[reportUnusedFunction]
        if not ctx.api_key:
            raise ValueError("missing api_key")
        return dataclasses.replace(ctx, called_with=ctx.api_key)

    with pytest.raises(ValueError):
        _ = machine.run(Ctx(api_key=None))

    run_id = machine.list_runs()[0]["run_id"]
    assert isinstance(run_id, str)
    _ = machine.resume(run_id, start_at="needs_key", context_overrides={"api_key": "sk-real"})

    runs = machine.list_runs()
    assert runs[0]["status"] == "completed"


def test_resume_unknown_run_raises(tmp_path: Path) -> None:
    @dataclass(frozen=True)
    class Ctx:
        pass

    machine = StateMachine(context_type=Ctx, db_path=str(tmp_path / "wf.db"))

    @machine.step("only")
    def only(ctx: Ctx) -> Ctx:  # pyright: ignore[reportUnusedFunction]
        return ctx

    with pytest.raises(Exception):
        _ = machine.resume("does-not-exist", start_at="only")


def test_cycle_within_max_steps_completes(tmp_path: Path) -> None:
    @dataclass(frozen=True)
    class Ctx:
        count: int = 0

    machine = StateMachine(context_type=Ctx, db_path=str(tmp_path / "wf.db"), max_steps=50)

    @machine.step("loop")
    def loop(ctx: Ctx) -> Ctx:
        return dataclasses.replace(ctx, count=ctx.count + 1)

    @machine.step("done")
    def done(ctx: Ctx) -> Ctx:
        return ctx

    _ = loop.to(done, when=lambda ctx: ctx.count >= 5)
    _ = loop.otherwise(loop)

    _ = machine.run(Ctx())

    runs = machine.list_runs()
    assert runs[0]["current_step"] == "done"
    assert runs[0]["status"] == "completed"


def test_cycle_exceeding_max_steps_raises(tmp_path: Path) -> None:
    @dataclass(frozen=True)
    class Ctx:
        pass

    machine = StateMachine(context_type=Ctx, db_path=str(tmp_path / "wf.db"), max_steps=10)

    @machine.step("loop")
    def loop(ctx: Ctx) -> Ctx:
        return ctx

    _ = loop.otherwise(loop)

    with pytest.raises(MaxStepsExceededError):
        _ = machine.run(Ctx())


def test_non_json_serializable_context_raises_clear_error(tmp_path: Path) -> None:
    @dataclass(frozen=True)
    class Ctx:
        bad: object

    machine = StateMachine(context_type=Ctx, db_path=str(tmp_path / "wf.db"))

    @machine.step("start")
    def start(ctx: Ctx) -> Ctx:  # pyright: ignore[reportUnusedFunction]
        return ctx

    with pytest.raises(TypeError):
        _ = machine.run(Ctx(bad=object()))


def test_list_runs_filters_by_status(tmp_path: Path) -> None:
    @dataclass(frozen=True)
    class Ctx:
        pass

    machine = StateMachine(context_type=Ctx, db_path=str(tmp_path / "wf.db"))

    @machine.step("ok")
    def ok(ctx: Ctx) -> Ctx:  # pyright: ignore[reportUnusedFunction]
        return ctx

    _ = machine.run(Ctx())

    machine2 = StateMachine(context_type=Ctx, db_path=str(tmp_path / "wf2.db"))

    @machine2.step("bad")
    def bad(_ctx: Ctx) -> Ctx:  # pyright: ignore[reportUnusedFunction]
        raise ValueError("nope")

    with pytest.raises(ValueError):
        _ = machine2.run(Ctx())

    failed = machine2.list_runs(status="failed")
    completed = machine2.list_runs(status="completed")
    assert len(failed) == 1
    assert len(completed) == 0


def test_duplicate_step_name_raises(tmp_path: Path) -> None:
    @dataclass(frozen=True)
    class Ctx:
        pass

    machine = StateMachine(context_type=Ctx, db_path=str(tmp_path / "wf.db"))

    @machine.step("dup")
    def dup(ctx: Ctx) -> Ctx:  # pyright: ignore[reportUnusedFunction]
        return ctx

    with pytest.raises(ValueError):
        @machine.step("dup")
        def dup2(ctx: Ctx) -> Ctx:  # pyright: ignore[reportUnusedFunction]
            return ctx
