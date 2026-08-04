"""Run a Goose trace-contract experiment and evaluate the captured evidence."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
CONTRACT = ROOT / "examples" / "reference_agent" / "aura-contract.yaml"


def default_output_dir(scenario: str) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return ROOT / ".aura" / "goose" / f"{timestamp}-{os.getpid()}-{scenario}"


def run_command(command: list[str], *, env: dict[str, str] | None = None) -> int:
    return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Goose through Aura and check the resulting trace contract."
    )
    parser.add_argument("scenario", choices=("safe", "dangerous"))
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    goose = shutil.which("goose")
    if goose is None:
        parser.error("goose is not installed; see https://goose-docs.ai/docs/quickstart/")

    output_dir = (args.output_dir or default_output_dir(args.scenario)).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    db = output_dir / "aura.db"
    run_id = f"goose-{args.scenario}"
    env = os.environ.copy()
    env.update(
        {
            "AURA_GOOSE_DB": str(db),
            "AURA_GOOSE_RUN_ID": run_id,
            "AURA_GOOSE_MODE": "observe",
        }
    )

    recipe = HERE / f"{args.scenario}.yaml"
    print(f"Running Goose recipe: {recipe.relative_to(ROOT)}")
    print(f"Aura evidence directory: {output_dir}")
    goose_result = run_command([goose, "run", "--recipe", str(recipe)], env=env)
    if goose_result != 0:
        raise SystemExit(goose_result)

    report_path = output_dir / "contract-report.json"
    summary_path = output_dir / "contract-report.md"
    contract_result = run_command(
        [
            sys.executable,
            "-m",
            "aura_runtime.cli",
            "contract",
            "check",
            str(CONTRACT),
            "--db",
            str(db),
            "--candidate-run",
            run_id,
            "--json-output",
            str(report_path),
            "--markdown-output",
            str(summary_path),
        ]
    )

    expected = 0 if args.scenario == "safe" else 2
    print(summary_path.read_text(encoding="utf-8"))
    if contract_result != expected:
        print(
            f"Expected contract exit {expected} for {args.scenario!r}, got {contract_result}.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    print(f"Experiment behaved as expected; evidence retained at {output_dir}")


if __name__ == "__main__":
    main()
