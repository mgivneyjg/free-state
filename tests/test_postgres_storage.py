"""Integration tests against a real Postgres instance.

Skipped unless both the optional 'psycopg' dependency is installed and
FREE_STATE_TEST_PG_DSN is set to a reachable Postgres connection string, e.g.:

    createdb free_state_test
    FREE_STATE_TEST_PG_DSN=postgresql://localhost/free_state_test pytest tests/test_postgres_storage.py
"""

import dataclasses
import os
import uuid
from dataclasses import dataclass

import pytest

psycopg = pytest.importorskip("psycopg")

from free_state import StateMachine
from free_state.storage import PostgresStorage, create_storage

PG_DSN = os.environ.get("FREE_STATE_TEST_PG_DSN")

pytestmark = pytest.mark.skipif(
    not PG_DSN,
    reason="set FREE_STATE_TEST_PG_DSN to a reachable Postgres connection string to run these tests",
)


@pytest.fixture
def storage():
    store = PostgresStorage(PG_DSN)
    yield store
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("DROP TABLE IF EXISTS checkpoints")
        conn.execute("DROP TABLE IF EXISTS runs")


def test_create_storage_dispatches_to_postgres():
    assert isinstance(create_storage(PG_DSN), PostgresStorage)


def test_create_run_update_status_and_list_runs(storage):
    run_id = str(uuid.uuid4())
    storage.create_run(run_id, "start")

    runs = storage.list_runs()
    assert any(r["run_id"] == run_id and r["status"] == "running" for r in runs)

    storage.update_run_status(run_id, "completed", "finish")
    completed = storage.list_runs(status="completed")
    assert any(r["run_id"] == run_id and r["current_step"] == "finish" for r in completed)


def test_save_and_load_latest_checkpoint(storage):
    run_id = str(uuid.uuid4())
    storage.create_run(run_id, "step_a")
    storage.save_checkpoint(run_id, "step_a", {"x": 1})
    storage.save_checkpoint(run_id, "step_a", {"x": 2})

    assert storage.load_latest_checkpoint(run_id, "step_a") == {"x": 2}
    assert storage.load_latest_checkpoint(run_id, "step_b") is None


def test_non_json_serializable_context_raises(storage):
    run_id = str(uuid.uuid4())
    storage.create_run(run_id, "step_a")
    with pytest.raises(TypeError):
        storage.save_checkpoint(run_id, "step_a", {"bad": object()})


def test_state_machine_runs_end_to_end_on_postgres():
    @dataclass(frozen=True)
    class Ctx:
        value: int = 0

    machine = StateMachine(context_type=Ctx, db_path=PG_DSN)

    @machine.step("increment")
    def increment(ctx):
        return dataclasses.replace(ctx, value=ctx.value + 1)

    run_id = machine.run(Ctx())
    runs = machine.list_runs()
    assert any(r["run_id"] == run_id and r["status"] == "completed" for r in runs)

    with psycopg.connect(PG_DSN) as conn:
        conn.execute("DROP TABLE IF EXISTS checkpoints")
        conn.execute("DROP TABLE IF EXISTS runs")
