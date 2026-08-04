"""Local append-only evidence storage."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

from aura_runtime.models import AgentEvent, Finding, ProtocolRecord, ToolManifestSnapshot


class SQLiteEventStore:
    def __init__(self, path: str | Path = ".aura/aura.db", *, read_only: bool = False) -> None:
        self.path = Path(path)
        self.read_only = read_only

    def connect(self) -> sqlite3.Connection:
        if self.read_only:
            connection = sqlite3.connect(f"file:{self.path.resolve()}?mode=ro", uri=True)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        if not self.read_only:
            connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        if self.read_only:
            return
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

                CREATE TABLE IF NOT EXISTS protocol_records (
                    record_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    content_hash TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL,
                    UNIQUE(run_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS idx_protocol_records_run
                    ON protocol_records(run_id, sequence);

                CREATE TABLE IF NOT EXISTS tool_manifests (
                    snapshot_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tool_manifests_run
                    ON tool_manifests(run_id, timestamp, snapshot_id);
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

    def all_events(self) -> list[AgentEvent]:
        """Return every event in deterministic cross-run order."""
        self.initialize()
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT payload FROM events
                   ORDER BY timestamp, run_id, sequence IS NULL, sequence, event_id"""
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
                """SELECT run_id FROM events
                   UNION SELECT run_id FROM findings
                   UNION SELECT run_id FROM protocol_records
                   UNION SELECT run_id FROM tool_manifests
                   ORDER BY run_id"""
            ).fetchall()
        yield from (str(row["run_id"]) for row in rows)

    def has_event(self, event_id: UUID) -> bool:
        self.initialize()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM events WHERE event_id = ?", (str(event_id),)
            ).fetchone()
        return row is not None

    def append_protocol_record(self, record: ProtocolRecord) -> None:
        self.initialize()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO protocol_records(
                       record_id, run_id, sequence, content_hash, payload
                   ) VALUES (?, ?, ?, ?, ?)""",
                (
                    str(record.record_id),
                    record.run_id,
                    record.sequence,
                    record.content_hash,
                    record.model_dump_json(),
                ),
            )

    def protocol_records(self, run_id: str) -> list[ProtocolRecord]:
        self.initialize()
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT payload FROM protocol_records WHERE run_id = ?
                   ORDER BY sequence""",
                (run_id,),
            ).fetchall()
        return [ProtocolRecord.model_validate_json(row["payload"]) for row in rows]

    def last_protocol_hash(self, run_id: str) -> str:
        self.initialize()
        with self.connect() as connection:
            row = connection.execute(
                """SELECT content_hash FROM protocol_records WHERE run_id = ?
                   ORDER BY sequence DESC LIMIT 1""",
                (run_id,),
            ).fetchone()
        return str(row["content_hash"]) if row else ""

    def append_manifest(self, snapshot: ToolManifestSnapshot) -> None:
        self.initialize()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO tool_manifests(
                       snapshot_id, run_id, request_id, timestamp, content_hash, payload
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    str(snapshot.snapshot_id),
                    snapshot.run_id,
                    snapshot.request_id,
                    snapshot.timestamp.isoformat(),
                    snapshot.content_hash,
                    snapshot.model_dump_json(),
                ),
            )

    def manifests(self, run_id: str) -> list[ToolManifestSnapshot]:
        self.initialize()
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT payload FROM tool_manifests WHERE run_id = ?
                   ORDER BY rowid""",
                (run_id,),
            ).fetchall()
        return [ToolManifestSnapshot.model_validate_json(row["payload"]) for row in rows]
