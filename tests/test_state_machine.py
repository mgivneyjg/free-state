import pytest

from state_machine import MaxStepsExceededError, NoMatchingEdgeError, StateMachine


def build_approval_machine(db_path):
    machine = StateMachine(db_path=str(db_path))

    @machine.step("check_balance")
    def check_balance(ctx):
        ctx["checked"] = True
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

    return machine, check_balance, approve, deny


def test_linear_run_reaches_terminal_step(tmp_path):
    machine, *_ = build_approval_machine(tmp_path / "wf.db")
    run_id = machine.run({"balance": 10})

    runs = machine.list_runs()
    assert len(runs) == 1
    assert runs[0]["run_id"] == run_id
    assert runs[0]["status"] == "completed"
    assert runs[0]["current_step"] == "approve"


def test_default_edge_is_used_when_no_predicate_matches(tmp_path):
    machine, *_ = build_approval_machine(tmp_path / "wf.db")
    machine.run({"balance": -5})

    runs = machine.list_runs()
    assert runs[0]["current_step"] == "deny"


def test_step_without_default_raises_and_marks_run_failed(tmp_path):
    machine = StateMachine(db_path=str(tmp_path / "wf.db"))

    @machine.step("a")
    def a(ctx):
        return ctx

    @machine.step("b")
    def b(ctx):
        return ctx

    a.to(b, when=lambda ctx: False)

    with pytest.raises(NoMatchingEdgeError):
        machine.run({})

    runs = machine.list_runs()
    assert runs[0]["status"] == "failed"
    assert runs[0]["current_step"] == "a"


def test_exception_in_step_marks_run_failed_and_reraises(tmp_path):
    machine = StateMachine(db_path=str(tmp_path / "wf.db"))

    @machine.step("boom")
    def boom(ctx):
        raise ValueError("kaboom")

    with pytest.raises(ValueError):
        machine.run({})

    runs = machine.list_runs()
    assert runs[0]["status"] == "failed"
    assert runs[0]["current_step"] == "boom"


def test_resume_reruns_failed_step_with_checkpointed_context(tmp_path):
    machine = StateMachine(db_path=str(tmp_path / "wf.db"))
    attempts = {"count": 0}

    @machine.step("flaky")
    def flaky(ctx):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise ValueError("first attempt fails")
        ctx["done"] = True
        return ctx

    with pytest.raises(ValueError):
        machine.run({"seed": 1})

    run_id = machine.list_runs()[0]["run_id"]
    machine.resume(run_id, start_at="flaky")

    runs = machine.list_runs()
    assert runs[0]["status"] == "completed"
    assert attempts["count"] == 2


def test_resume_context_overrides_are_applied(tmp_path):
    machine = StateMachine(db_path=str(tmp_path / "wf.db"))

    @machine.step("needs_key")
    def needs_key(ctx):
        if not ctx.get("api_key"):
            raise ValueError("missing api_key")
        ctx["called_with"] = ctx["api_key"]
        return ctx

    with pytest.raises(ValueError):
        machine.run({"api_key": None})

    run_id = machine.list_runs()[0]["run_id"]
    machine.resume(run_id, start_at="needs_key", context_overrides={"api_key": "sk-real"})

    runs = machine.list_runs()
    assert runs[0]["status"] == "completed"


def test_resume_unknown_run_raises(tmp_path):
    machine = StateMachine(db_path=str(tmp_path / "wf.db"))

    @machine.step("only")
    def only(ctx):
        return ctx

    with pytest.raises(Exception):
        machine.resume("does-not-exist", start_at="only")


def test_cycle_within_max_steps_completes(tmp_path):
    machine = StateMachine(db_path=str(tmp_path / "wf.db"), max_steps=50)

    @machine.step("loop")
    def loop(ctx):
        ctx["count"] = ctx.get("count", 0) + 1
        return ctx

    @machine.step("done")
    def done(ctx):
        return ctx

    loop.to(done, when=lambda ctx: ctx["count"] >= 5)
    loop.otherwise(loop)

    machine.run({})

    runs = machine.list_runs()
    assert runs[0]["current_step"] == "done"
    assert runs[0]["status"] == "completed"


def test_cycle_exceeding_max_steps_raises(tmp_path):
    machine = StateMachine(db_path=str(tmp_path / "wf.db"), max_steps=10)

    @machine.step("loop")
    def loop(ctx):
        return ctx

    loop.otherwise(loop)

    with pytest.raises(MaxStepsExceededError):
        machine.run({})


def test_non_json_serializable_context_raises_clear_error(tmp_path):
    machine = StateMachine(db_path=str(tmp_path / "wf.db"))

    @machine.step("start")
    def start(ctx):
        return ctx

    with pytest.raises(TypeError):
        machine.run({"bad": object()})


def test_list_runs_filters_by_status(tmp_path):
    machine = StateMachine(db_path=str(tmp_path / "wf.db"))

    @machine.step("ok")
    def ok(ctx):
        return ctx

    machine.run({})

    machine2 = StateMachine(db_path=str(tmp_path / "wf2.db"))

    @machine2.step("bad")
    def bad(ctx):
        raise ValueError("nope")

    with pytest.raises(ValueError):
        machine2.run({})

    failed = machine2.list_runs(status="failed")
    completed = machine2.list_runs(status="completed")
    assert len(failed) == 1
    assert len(completed) == 0


def test_duplicate_step_name_raises(tmp_path):
    machine = StateMachine(db_path=str(tmp_path / "wf.db"))

    @machine.step("dup")
    def dup(ctx):
        return ctx

    with pytest.raises(ValueError):
        @machine.step("dup")
        def dup2(ctx):
            return ctx
