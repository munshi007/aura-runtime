"""Aura Runtime command-line interface."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

import typer

from aura_runtime.adapters.otel import events_from_otlp_json
from aura_runtime.conformance import analyze_protocol_records
from aura_runtime.contract import TraceContract, check_contract
from aura_runtime.flight import EnforcementMode, MCPFlightRecorder, verify_protocol_chain
from aura_runtime.integrations.goose import (
    connect_goose as connect_goose_config,
)
from aura_runtime.integrations.goose import (
    disconnect_goose as disconnect_goose_config,
)
from aura_runtime.integrations.goose import (
    doctor_goose as doctor_goose_config,
)
from aura_runtime.integrations.goose import goose_config_path
from aura_runtime.models import AgentEvent
from aura_runtime.object_contract import ObjectContract, ObjectContractMonitor
from aura_runtime.object_process import compare_object_behavior, discover_object_behavior
from aura_runtime.ocel_export import events_to_ocel_json
from aura_runtime.otlp_export import protocol_records_to_otlp_json
from aura_runtime.policy import AuraSpec
from aura_runtime.proxy import run_stdio_proxy
from aura_runtime.replay import compare_manifests, compare_runs, replay_run
from aura_runtime.store import SQLiteEventStore
from aura_runtime.verifier import OnlineLTLfMonitor, OnlineTemporalMonitor, RuntimeVerifier

app = typer.Typer(help="Deterministic runtime verification for AI agents.", no_args_is_help=True)
contract_app = typer.Typer(
    help="Check agent behavior against trace contracts.", no_args_is_help=True
)
connect_app = typer.Typer(help="Enroll agent runtimes behind Aura.", no_args_is_help=True)
disconnect_app = typer.Typer(help="Remove Aura runtime enrollment.", no_args_is_help=True)
doctor_app = typer.Typer(help="Diagnose agent runtime enrollment.", no_args_is_help=True)
manifests_app = typer.Typer(help="Compare captured MCP tool manifests.", no_args_is_help=True)
objects_app = typer.Typer(
    help="Discover and compare object-centric behavior.", no_args_is_help=True
)
object_contract_app = typer.Typer(help="Create and check object contracts.", no_args_is_help=True)
objects_app.add_typer(object_contract_app, name="contract")
app.add_typer(contract_app, name="contract")
app.add_typer(connect_app, name="connect")
app.add_typer(disconnect_app, name="disconnect")
app.add_typer(doctor_app, name="doctor")
app.add_typer(manifests_app, name="manifests")
app.add_typer(objects_app, name="objects")
DB_OPTION = Annotated[Path, typer.Option("--db", help="Path to the Aura SQLite database.")]


def _emit_json(value: object, output: Path | None = None) -> None:
    content = json.dumps(value, indent=2)
    if output is None:
        typer.echo(content)
    else:
        output.write_text(f"{content}\n", encoding="utf-8")


def _as_cli_error(error: ValueError) -> typer.BadParameter:
    return typer.BadParameter(str(error))


def _goose_path(config: Path | None) -> Path:
    try:
        return config or goose_config_path()
    except ValueError as error:
        raise _as_cli_error(error) from error


@connect_app.command("goose")
def connect_goose_command(
    config: Annotated[Path | None, typer.Option("--config", help="Goose config.yaml path.")] = None,
    policy: Annotated[
        Path | None, typer.Option("--policy", exists=True, readable=True)
    ] = None,
    db: Annotated[Path | None, typer.Option("--db", help="Shared Aura evidence database.")] = None,
    mode: Annotated[EnforcementMode, typer.Option("--mode")] = EnforcementMode.OBSERVE,
    aura_command: Annotated[str, typer.Option("--aura-command")] = "aura",
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Reversibly wrap every configured Goose stdio extension with Aura."""
    try:
        report = connect_goose_config(
            _goose_path(config),
            policy=policy,
            db=db,
            mode=mode,
            aura_command=aura_command,
            dry_run=dry_run,
        )
    except ValueError as error:
        raise _as_cli_error(error) from error
    _emit_json(report.model_dump(mode="json"))


@doctor_app.command("goose")
def doctor_goose_command(
    config: Annotated[Path | None, typer.Option("--config", help="Goose config.yaml path.")] = None,
) -> None:
    """Check configuration, wrapper integrity, and Aura executable availability."""
    try:
        report = doctor_goose_config(_goose_path(config))
    except ValueError as error:
        raise _as_cli_error(error) from error
    _emit_json(report.model_dump(mode="json"))
    if not report.healthy:
        raise typer.Exit(code=2)


@disconnect_app.command("goose")
def disconnect_goose_command(
    config: Annotated[Path | None, typer.Option("--config", help="Goose config.yaml path.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Restore enrolled Goose extensions without overwriting later edits."""
    try:
        report = disconnect_goose_config(_goose_path(config), dry_run=dry_run)
    except ValueError as error:
        raise _as_cli_error(error) from error
    _emit_json(report.model_dump(mode="json"))


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
    spec = AuraSpec.from_yaml(policy)
    events = [spec.bind_objects(event) for event in events]
    verifier = RuntimeVerifier(spec)
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
    typer.echo(f"Ingested {len(events)} Aura events from OTLP spans")


@app.command("export-otlp")
def export_otlp(
    run_id: Annotated[str, typer.Argument()],
    db: DB_OPTION = Path(".aura/aura.db"),
    output: Annotated[Path | None, typer.Option("--output")] = None,
    include_content: Annotated[
        bool,
        typer.Option(
            "--include-content",
            help="Opt in to exporting tool arguments and results.",
        ),
    ] = False,
) -> None:
    """Export MCP causal evidence as an OTLP/HTTP JSON trace payload."""
    try:
        payload = protocol_records_to_otlp_json(
            SQLiteEventStore(db).protocol_records(run_id),
            include_content=include_content,
        )
    except ValueError as error:
        raise _as_cli_error(error) from error
    _emit_json(payload, output)


@app.command("export-ocel")
def export_ocel(
    db: DB_OPTION = Path(".aura/aura.db"),
    output: Annotated[Path | None, typer.Option("--output")] = None,
    run_ids: Annotated[
        list[str] | None,
        typer.Option("--run", help="Run ID to include; repeat to select multiple runs."),
    ] = None,
    include_identifiers: Annotated[
        bool,
        typer.Option(
            "--include-identifiers",
            help="Opt in to exporting original run and business-object identifiers.",
        ),
    ] = False,
) -> None:
    """Export structural agent evidence as privacy-safe OCEL 2.0 JSON."""
    store = SQLiteEventStore(db)
    events = (
        store.all_events()
        if run_ids is None
        else [event for run_id in dict.fromkeys(run_ids) for event in store.events(run_id)]
    )
    try:
        payload = events_to_ocel_json(events, include_identifiers=include_identifiers)
    except ValueError as error:
        raise _as_cli_error(error) from error
    _emit_json(payload, output)


def _selected_events(store: SQLiteEventStore, run_ids: list[str]) -> list[AgentEvent]:
    return [event for run_id in dict.fromkeys(run_ids) for event in store.events(run_id)]


@objects_app.command("discover")
def discover_objects(
    run_ids: Annotated[
        list[str], typer.Option("--run", help="Run ID to include; repeat for a cohort.")
    ],
    db: DB_OPTION = Path(".aura/aura.db"),
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    """Discover a content-free object-centric behavioral profile."""
    try:
        profile = discover_object_behavior(_selected_events(SQLiteEventStore(db), run_ids))
    except ValueError as error:
        raise _as_cli_error(error) from error
    _emit_json(profile.model_dump(mode="json"), output)


@objects_app.command("compare")
def compare_objects(
    baseline_runs: Annotated[
        list[str], typer.Option("--baseline-run", help="Trusted run; repeat for a cohort.")
    ],
    candidate_runs: Annotated[
        list[str], typer.Option("--candidate-run", help="Candidate run; repeat for a cohort.")
    ],
    db: DB_OPTION = Path(".aura/aura.db"),
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    """Fail on structural object-lifecycle or interaction drift."""
    store = SQLiteEventStore(db)
    try:
        baseline = discover_object_behavior(_selected_events(store, baseline_runs))
        candidate = discover_object_behavior(_selected_events(store, candidate_runs))
    except ValueError as error:
        raise _as_cli_error(error) from error
    report = compare_object_behavior(baseline, candidate)
    _emit_json(report.model_dump(mode="json"), output)
    if report.verdict == "fail":
        raise typer.Exit(code=2)


@object_contract_app.command("create")
def create_object_contract(
    run_ids: Annotated[
        list[str], typer.Option("--baseline-run", help="Trusted run; repeat for a cohort.")
    ],
    db: DB_OPTION = Path(".aura/aura.db"),
    output: Annotated[Path, typer.Option("--output")] = Path("aura-object-contract.json"),
    effect: Annotated[
        Literal["deny", "require_approval"], typer.Option("--effect")
    ] = "deny",
) -> None:
    """Compile trusted object behavior into a content-addressed contract."""
    try:
        profile = discover_object_behavior(_selected_events(SQLiteEventStore(db), run_ids))
        contract = ObjectContract.from_profile(profile, effect=effect)
    except ValueError as error:
        raise _as_cli_error(error) from error
    _emit_json(contract.model_dump(mode="json"), output)


def _monitor_object_events(
    contract: ObjectContract,
    events: list[AgentEvent],
    *,
    final: bool,
) -> ObjectContractMonitor:
    monitor = ObjectContractMonitor(contract)
    for event in events:
        if event.objects:
            monitor.observe(event, commit=True)
    if final:
        monitor.finalize()
    return monitor


@object_contract_app.command("check")
def check_object_contract(
    contract_path: Annotated[Path, typer.Argument(exists=True, readable=True)],
    run_ids: Annotated[
        list[str], typer.Option("--run", help="Candidate run; repeat for a cohort.")
    ],
    db: DB_OPTION = Path(".aura/aura.db"),
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    """Replay completed runs against an object contract and fail on violations."""
    try:
        contract = ObjectContract.from_json(contract_path)
        monitor = _monitor_object_events(
            contract,
            _selected_events(SQLiteEventStore(db), run_ids),
            final=True,
        )
    except ValueError as error:
        raise _as_cli_error(error) from error
    report = monitor.report()
    _emit_json(report.model_dump(mode="json"), output)
    if report.violation_count:
        raise typer.Exit(code=2)


@objects_app.command("state")
def object_state(
    run_id: Annotated[str, typer.Argument()],
    contract_path: Annotated[
        Path, typer.Option("--contract", exists=True, readable=True)
    ],
    db: DB_OPTION = Path(".aura/aura.db"),
) -> None:
    """Inspect accepted object state for a captured run prefix."""
    try:
        monitor = _monitor_object_events(
            ObjectContract.from_json(contract_path),
            SQLiteEventStore(db).events(run_id),
            final=False,
        )
    except ValueError as error:
        raise _as_cli_error(error) from error
    _emit_json(monitor.report().model_dump(mode="json"))


@app.command()
def report(
    run_id: Annotated[str, typer.Argument()],
    db: DB_OPTION = Path(".aura/aura.db"),
) -> None:
    store = SQLiteEventStore(db)
    findings = store.findings(run_id)
    protocol_records = store.protocol_records(run_id)
    output = {
        "run_id": run_id,
        "event_count": len(store.events(run_id)),
        "finding_count": len(findings),
        "protocol_record_count": len(protocol_records),
        "manifest_count": len(store.manifests(run_id)),
        "transcript_integrity": verify_protocol_chain(protocol_records),
        "findings": [finding.model_dump(mode="json") for finding in findings],
    }
    _emit_json(output)


@app.command("conformance")
def conformance_command(
    run_id: Annotated[str, typer.Argument()],
    db: DB_OPTION = Path(".aura/aura.db"),
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    """Reconstruct MCP causality and check protocol lifecycle invariants."""
    try:
        report = analyze_protocol_records(SQLiteEventStore(db).protocol_records(run_id))
    except ValueError as error:
        raise _as_cli_error(error) from error
    _emit_json(report.model_dump(mode="json"), output)
    if report.verdict == "fail":
        raise typer.Exit(code=2)


@app.command("temporal-state")
def temporal_state_command(
    run_id: Annotated[str, typer.Argument()],
    policy: Annotated[Path, typer.Option("--policy", exists=True, readable=True)],
    db: DB_OPTION = Path(".aura/aura.db"),
    final: Annotated[
        bool,
        typer.Option("--final", help="Declare the captured prefix to be a complete trace."),
    ] = False,
) -> None:
    """Inspect pending, satisfied, and violated bounded future obligations."""
    events = SQLiteEventStore(db).events(run_id)
    if not events:
        raise typer.BadParameter(f"run {run_id!r} has no captured events")
    monitor = OnlineTemporalMonitor(AuraSpec.from_yaml(policy))
    for event in events:
        monitor.observe(event)
    if final:
        monitor.finalize()
    report = monitor.report()
    _emit_json(report.model_dump(mode="json"))
    if report.findings:
        raise typer.Exit(code=2)


@app.command("ltlf-state")
def ltlf_state_command(
    run_id: Annotated[str, typer.Argument()],
    policy: Annotated[Path, typer.Option("--policy", exists=True, readable=True)],
    db: DB_OPTION = Path(".aura/aura.db"),
    final: Annotated[
        bool,
        typer.Option("--final", help="Declare the captured prefix to be a complete trace."),
    ] = False,
) -> None:
    """Inspect exact four-valued LTLf state for a captured run."""
    events = SQLiteEventStore(db).events(run_id)
    if not events:
        raise typer.BadParameter(f"run {run_id!r} has no captured events")
    monitor = OnlineLTLfMonitor(AuraSpec.from_yaml(policy))
    for event in events:
        monitor.observe(event)
    if final:
        monitor.finalize()
    report = monitor.report()
    _emit_json(report.model_dump(mode="json"))
    if report.findings:
        raise typer.Exit(code=2)


@app.command("shield-action")
def shield_action_command(
    run_id: Annotated[str, typer.Argument()],
    policy: Annotated[Path, typer.Option("--policy", exists=True, readable=True)],
    event: Annotated[Path, typer.Option("--event", exists=True, readable=True)],
    db: DB_OPTION = Path(".aura/aura.db"),
) -> None:
    """Preview one canonical event and return deterministic safe alternatives."""
    events = SQLiteEventStore(db).events(run_id)
    monitor = OnlineLTLfMonitor(AuraSpec.from_yaml(policy))
    for captured in events:
        monitor.observe(captured)
    proposed = AgentEvent.model_validate_json(event.read_text(encoding="utf-8"))
    if proposed.run_id != run_id:
        raise typer.BadParameter("proposed event run_id must match the captured run")
    report = monitor.preview(proposed)
    _emit_json(report.model_dump(mode="json"))
    if not report.safe:
        raise typer.Exit(code=2)


@app.command("strategy-check")
def strategy_check_command(
    policy: Annotated[Path, typer.Option("--policy", exists=True, readable=True)],
    run_id: Annotated[str | None, typer.Option("--run")] = None,
    db: DB_OPTION = Path(".aura/aura.db"),
) -> None:
    """Check whether LTLf policies admit winning controller strategies."""
    monitor = OnlineLTLfMonitor(AuraSpec.from_yaml(policy))
    if run_id is not None:
        events = SQLiteEventStore(db).events(run_id)
        if not events:
            raise typer.BadParameter(f"run {run_id!r} has no captured events")
        for event in events:
            monitor.observe(event)
    report = monitor.strategy_report()
    _emit_json(report.model_dump(mode="json"))
    if not report.all_realizable:
        raise typer.Exit(code=2)


@app.command("replay")
def replay_command(
    run_id: Annotated[str, typer.Argument()],
    policy: Annotated[Path, typer.Option("--policy", exists=True, readable=True)],
    db: DB_OPTION = Path(".aura/aura.db"),
    output: Annotated[Path | None, typer.Option("--output")] = None,
    fail_on_new: Annotated[bool, typer.Option("--fail-on-new")] = False,
) -> None:
    """Re-evaluate captured evidence without executing any tool."""
    try:
        report = replay_run(SQLiteEventStore(db), run_id, AuraSpec.from_yaml(policy))
    except ValueError as error:
        raise _as_cli_error(error) from error
    _emit_json(report.model_dump(mode="json"), output)
    if fail_on_new and report.introduced:
        raise typer.Exit(code=2)


@app.command("diff")
def diff_runs(
    left_run_id: Annotated[str, typer.Argument()],
    right_run_id: Annotated[str, typer.Argument()],
    db: DB_OPTION = Path(".aura/aura.db"),
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    """Locate the first behavioral divergence between two captured runs."""
    try:
        report = compare_runs(SQLiteEventStore(db), left_run_id, right_run_id)
    except ValueError as error:
        raise _as_cli_error(error) from error
    _emit_json(report.model_dump(mode="json"), output)


@manifests_app.command("diff")
def diff_manifests(
    left_run_id: Annotated[str, typer.Argument()],
    right_run_id: Annotated[str, typer.Argument()],
    db: DB_OPTION = Path(".aura/aura.db"),
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    """Compare the latest tool-manifest snapshots for two runs."""
    try:
        report = compare_manifests(SQLiteEventStore(db), left_run_id, right_run_id)
    except ValueError as error:
        raise _as_cli_error(error) from error
    _emit_json(report.model_dump(mode="json"), output)


@contract_app.command("check")
def contract_check(
    contract_path: Annotated[Path, typer.Argument(exists=True, readable=True)],
    candidate_run: Annotated[str, typer.Option("--candidate-run")],
    db: DB_OPTION = Path(".aura/aura.db"),
    json_output: Annotated[Path | None, typer.Option("--json-output")] = None,
    markdown_output: Annotated[Path | None, typer.Option("--markdown-output")] = None,
) -> None:
    """Check a candidate run against a committed behavioral baseline."""
    try:
        report = check_contract(
            TraceContract.from_yaml(contract_path),
            SQLiteEventStore(db),
            candidate_run,
        )
    except ValueError as error:
        raise _as_cli_error(error) from error

    _emit_json(report.model_dump(mode="json"), json_output)
    if markdown_output is not None:
        markdown_output.write_text(f"{report.to_markdown()}\n", encoding="utf-8")
    if report.verdict == "fail":
        raise typer.Exit(code=2)


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def proxy(
    context: typer.Context,
    policy: Annotated[Path | None, typer.Option("--policy", exists=True, readable=True)] = None,
    object_contract: Annotated[
        Path | None, typer.Option("--object-contract", exists=True, readable=True)
    ] = None,
    mode: Annotated[EnforcementMode, typer.Option("--mode")] = EnforcementMode.OBSERVE,
    db: DB_OPTION = Path(".aura/aura.db"),
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
) -> None:
    """Record and optionally gate an upstream MCP stdio server."""
    command = list(context.args)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise typer.BadParameter("pass an upstream command after --")
    if mode == EnforcementMode.ENFORCE and policy is None and object_contract is None:
        raise typer.BadParameter("--mode enforce requires --policy or --object-contract")
    if object_contract is not None and policy is None:
        raise typer.BadParameter("--object-contract requires --policy for object bindings")

    store = SQLiteEventStore(db)
    spec = AuraSpec.from_yaml(policy) if policy else None
    if object_contract is not None and spec is not None and not spec.object_bindings:
        raise typer.BadParameter("--object-contract requires AuraSpec object_bindings")
    recorder = MCPFlightRecorder(
        run_id=run_id or f"mcp-{uuid4()}",
        store=store,
        spec=spec,
        object_contract=ObjectContract.from_json(object_contract) if object_contract else None,
        mode=mode,
    )
    exit_code = asyncio.run(run_stdio_proxy(command, recorder))
    if exit_code:
        raise typer.Exit(exit_code)


if __name__ == "__main__":
    app()
