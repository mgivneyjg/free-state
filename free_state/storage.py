from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import cast, final


@final
class Storage:
    def __init__(self, db_path: str) -> None:
        self.db_path: str = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn, conn:
            _ = conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    current_step TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            _ = conn.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    step_name TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            _ = conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_checkpoints_lookup
                ON checkpoints (run_id, step_name, id)
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_run(self, run_id: str, start_step: str) -> None:
        with closing(self._connect()) as conn, conn:
            _ = conn.execute(
                "INSERT INTO runs (run_id, status, current_step, updated_at) VALUES (?, ?, ?, ?)",
                (run_id, "running", start_step, self._now()),
            )

    def update_run_status(self, run_id: str, status: str, current_step: str) -> None:
        with closing(self._connect()) as conn, conn:
            _ = conn.execute(
                "UPDATE runs SET status = ?, current_step = ?, updated_at = ? WHERE run_id = ?",
                (status, current_step, self._now(), run_id),
            )

    def save_checkpoint(self, run_id: str, step_name: str, context: dict[str, object]) -> None:
        try:
            context_json = json.dumps(context)
        except TypeError as exc:
            raise TypeError(
                f"context for step '{step_name}' is not JSON-serializable: {exc}"
            ) from exc
        with closing(self._connect()) as conn, conn:
            _ = conn.execute(
                "INSERT INTO checkpoints (run_id, step_name, context_json, created_at) VALUES (?, ?, ?, ?)",
                (run_id, step_name, context_json, self._now()),
            )

    def load_latest_checkpoint(self, run_id: str, step_name: str) -> dict[str, object] | None:
        with closing(self._connect()) as conn:
            row = cast(
                "sqlite3.Row | None",
                conn.execute(
                    """
                    SELECT context_json FROM checkpoints
                    WHERE run_id = ? AND step_name = ? ORDER BY id DESC LIMIT 1
                    """,
                    (run_id, step_name),
                ).fetchone(),
            )
        if row is None:
            return None
        context_json = cast(str, row["context_json"])
        return cast("dict[str, object]", json.loads(context_json))

    def list_runs(self, status: str | None = None) -> list[dict[str, object]]:
        query = "SELECT run_id, status, current_step, updated_at FROM runs"
        params: tuple[str, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY updated_at DESC"
        with closing(self._connect()) as conn:
            rows = cast("list[sqlite3.Row]", conn.execute(query, params).fetchall())
        return [dict(row) for row in rows]
