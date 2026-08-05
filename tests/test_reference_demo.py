import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_canonical_demo_proves_positive_and_negative_controls(tmp_path: Path) -> None:
    output = tmp_path / "evidence"
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(ROOT / "examples/reference_agent/demo.py"),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["llm_required"] is False
    assert summary["controls_valid"] is True
    assert summary["scenarios"]["safe"]["verdict"] == "pass"
    assert summary["scenarios"]["dangerous"]["verdict"] == "fail"
    assert (output / "safe.db").exists()
    assert (output / "dangerous.md").exists()
