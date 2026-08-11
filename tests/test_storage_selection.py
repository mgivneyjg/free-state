import pytest

from free_state.storage import PostgresStorage, SQLiteStorage, create_storage


def test_create_storage_defaults_to_sqlite_for_file_path(tmp_path):
    storage = create_storage(str(tmp_path / "wf.db"))
    assert isinstance(storage, SQLiteStorage)


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://user:pass@localhost/db",
        "postgres://user:pass@localhost/db",
    ],
)
def test_create_storage_picks_postgres_for_connection_url(monkeypatch, dsn):
    pytest.importorskip("psycopg")
    monkeypatch.setattr(PostgresStorage, "_init_db", lambda self: None)
    storage = create_storage(dsn)
    assert isinstance(storage, PostgresStorage)
    assert storage.dsn == dsn


def test_postgres_storage_requires_psycopg_when_missing(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "psycopg":
            raise ImportError("no module named psycopg")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ImportError, match="pip install free-state\\[postgres\\]"):
        PostgresStorage("postgresql://user:pass@localhost/db")


def test_state_machine_accepts_injected_storage(tmp_path):
    from dataclasses import dataclass

    from free_state import StateMachine

    @dataclass(frozen=True)
    class Ctx:
        pass

    injected = SQLiteStorage(str(tmp_path / "wf.db"))
    machine = StateMachine(context_type=Ctx, storage=injected)
    assert machine._storage is injected

    @machine.step("only")
    def only(ctx):
        return ctx

    machine.run(Ctx())
    assert injected.list_runs()[0]["status"] == "completed"
