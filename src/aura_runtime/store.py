"""Local append-only evidence storage."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

from aura_runtime.models import AgentEvent, Finding


class SQLiteEventStore:
    def __init__(self, path: str | Path = ".aura/aura.db") -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    sequence INTEGER,
                    timestamp TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_run_order
                    ON events(run_id, sequence, timestamp, event_id);

                CREATE TABLE IF NOT EXISTS findings (
                    finding_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    policy_id TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_findings_run
                    ON findings(run_id, created_at, finding_id);
                """
            )

    def append_event(self, event: AgentEvent) -> None:
        self.initialize()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO events(event_id, run_id, sequence, timestamp, kind, payload)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    str(event.event_id),
                    event.run_id,
                    event.sequence,
                    event.timestamp.isoformat(),
                    event.kind.value,
                    event.model_dump_json(),
                ),
            )

    def append_finding(self, finding: Finding) -> None:
        self.initialize()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO findings(
                       finding_id, run_id, policy_id, severity, created_at, payload
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    str(finding.finding_id),
                    finding.run_id,
                    finding.policy_id,
                    finding.severity.value,
                    finding.created_at.isoformat(),
                    finding.model_dump_json(),
                ),
            )

    def events(self, run_id: str) -> list[AgentEvent]:
        self.initialize()
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT payload FROM events WHERE run_id = ?
                   ORDER BY sequence IS NULL, sequence, timestamp, event_id""",
                (run_id,),
            ).fetchall()
        return [AgentEvent.model_validate_json(row["payload"]) for row in rows]

    def findings(self, run_id: str) -> list[Finding]:
        self.initialize()
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT payload FROM findings WHERE run_id = ?
                   ORDER BY created_at, finding_id""",
                (run_id,),
            ).fetchall()
        return [Finding.model_validate_json(row["payload"]) for row in rows]

    def run_ids(self) -> Iterator[str]:
        self.initialize()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT run_id FROM events ORDER BY run_id"
            ).fetchall()
        yield from (str(row["run_id"]) for row in rows)

    def has_event(self, event_id: UUID) -> bool:
        self.initialize()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM events WHERE event_id = ?", (str(event_id),)
            ).fetchone()
        return row is not None
