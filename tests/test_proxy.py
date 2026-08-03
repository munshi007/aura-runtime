import json
import subprocess
import sys
from pathlib import Path

from aura_runtime.flight import verify_protocol_chain
from aura_runtime.store import SQLiteEventStore


def test_stdio_proxy_round_trip(tmp_path: Path) -> None:
    db = tmp_path / "aura.db"
    upstream = "import sys; [(sys.stdout.write(line), sys.stdout.flush()) for line in sys.stdin]"
    message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "aura_runtime.cli",
            "proxy",
            "--db",
            str(db),
            "--run-id",
            "proxy-run",
            "--",
            sys.executable,
            "-c",
            upstream,
        ],
        input=json.dumps(message) + "\n",
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == message
    records = SQLiteEventStore(db).protocol_records("proxy-run")
    assert len(records) == 2
    assert verify_protocol_chain(records)
