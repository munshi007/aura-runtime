"""Aura Runtime command-line interface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from aura_runtime.adapters.otel import events_from_otlp_json
from aura_runtime.models import AgentEvent
from aura_runtime.policy import AuraSpec
from aura_runtime.store import SQLiteEventStore
from aura_runtime.verifier import RuntimeVerifier

app = typer.Typer(help="Deterministic runtime verification for AI agents.", no_args_is_help=True)
DB_OPTION = Annotated[Path, typer.Option("--db", help="Path to the Aura SQLite database.")]


@app.command("init")
def initialize(db: DB_OPTION = Path(".aura/aura.db")) -> None:
    store = SQLiteEventStore(db)
    store.initialize()
    typer.echo(f"Initialized Aura evidence store at {db}")


@app.command()
def check(
    events_path: Annotated[Path, typer.Argument(exists=True, readable=True)],
    policy: Annotated[Path, typer.Option("--policy", exists=True, readable=True)],
    db: DB_OPTION = Path(".aura/aura.db"),
) -> None:
    """Ingest JSONL events and evaluate them against an AuraSpec file."""
    events = [
        AgentEvent.model_validate_json(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not events:
        raise typer.BadParameter("events file is empty")

    store = SQLiteEventStore(db)
    verifier = RuntimeVerifier(AuraSpec.from_yaml(policy))
    findings = verifier.verify(events)
    for event in events:
        store.append_event(event)
    for finding in findings:
        store.append_finding(finding)
    typer.echo(f"Ingested {len(events)} events; emitted {len(findings)} findings")
    if findings:
        raise typer.Exit(code=2)


@app.command("ingest-otlp")
def ingest_otlp(
    payload_path: Annotated[Path, typer.Argument(exists=True, readable=True)],
    db: DB_OPTION = Path(".aura/aura.db"),
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
) -> None:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    events = events_from_otlp_json(payload, run_id=run_id)
    store = SQLiteEventStore(db)
    for event in events:
        store.append_event(event)
    typer.echo(f"Ingested {len(events)} OTLP spans")


@app.command()
def report(
    run_id: Annotated[str, typer.Argument()],
    db: DB_OPTION = Path(".aura/aura.db"),
) -> None:
    store = SQLiteEventStore(db)
    findings = store.findings(run_id)
    output = {
        "run_id": run_id,
        "event_count": len(store.events(run_id)),
        "finding_count": len(findings),
        "findings": [finding.model_dump(mode="json") for finding in findings],
    }
    typer.echo(json.dumps(output, indent=2))


if __name__ == "__main__":
    app()
