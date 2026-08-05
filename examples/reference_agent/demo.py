"""Run Aura's canonical safe/unsafe end-to-end demonstration."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTRACT = HERE / "aura-contract.yaml"


def _run(command: list[str], expected: int) -> None:
    result = subprocess.run(command, check=False)  # noqa: S603
    if result.returncode != expected:
        raise RuntimeError(
            f"command returned {result.returncode}, expected {expected}: {' '.join(command)}"
        )


def run_demo(output: Path) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=True)
    scenarios: dict[str, dict[str, object]] = {}
    for variant, expected_exit in (("safe", 0), ("dangerous", 2)):
        db = output / f"{variant}.db"
        report_json = output / f"{variant}.json"
        report_markdown = output / f"{variant}.md"
        run_id = f"demo-{variant}"
        _run(
            [
                sys.executable,
                str(HERE / "scenario.py"),
                "--db",
                str(db),
                "--run-id",
                run_id,
                "--variant",
                variant,
            ],
            0,
        )
        _run(
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
                str(report_json),
                "--markdown-output",
                str(report_markdown),
            ],
            expected_exit,
        )
        report = json.loads(report_json.read_text(encoding="utf-8"))
        scenarios[variant] = {
            "expected_verdict": "pass" if variant == "safe" else "fail",
            "verdict": report["verdict"],
            "run_id": run_id,
            "database": db.name,
            "json_report": report_json.name,
            "markdown_report": report_markdown.name,
        }

    summary = {
        "demo": "aura-reference-support-agent",
        "llm_required": False,
        "controls_valid": all(
            item["verdict"] == item["expected_verdict"] for item in scenarios.values()
        ),
        "scenarios": scenarios,
    }
    (output / "summary.json").write_text(
        f"{json.dumps(summary, indent=2)}\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(".aura-demo"))
    args = parser.parse_args()
    summary = run_demo(args.output)
    print(json.dumps(summary, indent=2))
    if not summary["controls_valid"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
