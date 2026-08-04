from pathlib import Path

import pytest
from ruamel.yaml import YAML
from typer.testing import CliRunner

from aura_runtime.cli import app
from aura_runtime.flight import EnforcementMode
from aura_runtime.integrations.goose import (
    connect_goose,
    disconnect_goose,
    doctor_goose,
    goose_config_path,
)


def write_config(path: Path) -> str:
    content = """# preserve this operator comment
active_provider: test
extensions:
  memory:
    type: stdio
    name: Memory
    cmd: npx
    args: [-y, '@modelcontextprotocol/server-memory']
    envs:
      MEMORY_SCOPE: do-not-copy-this-value
    timeout: 300
    enabled: true
  developer:
    type: builtin
    name: Developer
    enabled: true
  web:
    type: streamable_http
    name: Web
    uri: https://example.test/mcp
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return content


def load_yaml(path: Path) -> dict:
    yaml = YAML(typ="safe")
    with path.open("r", encoding="utf-8") as stream:
        return yaml.load(stream)


def test_goose_config_path_uses_documented_platform_locations(tmp_path: Path) -> None:
    assert goose_config_path(environ={}, platform="linux", home=tmp_path) == (
        tmp_path / ".config" / "goose" / "config.yaml"
    )
    assert goose_config_path(
        environ={"XDG_CONFIG_HOME": "/xdg"}, platform="darwin", home=tmp_path
    ) == Path("/xdg/goose/config.yaml")
    assert goose_config_path(
        environ={"APPDATA": "C:/Users/Ada/AppData/Roaming"}, platform="win32"
    ) == Path("C:/Users/Ada/AppData/Roaming/Block/goose/config/config.yaml")
    assert goose_config_path(
        environ={"GOOSE_PATH_ROOT": "/isolated"}, platform="linux", home=tmp_path
    ) == Path("/isolated/config/config.yaml")


def test_connect_is_previewable_reversible_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("aura_runtime.integrations.goose.shutil.which", lambda _: "/bin/aura")
    config_path = tmp_path / "config.yaml"
    original_text = write_config(config_path)
    db = tmp_path / "evidence" / "goose.db"
    policy = tmp_path / "policy.yaml"
    policy.write_text("version: '0.1'\npolicies: []\n", encoding="utf-8")

    preview = connect_goose(
        config_path,
        db=db,
        policy=policy,
        mode=EnforcementMode.OBSERVE,
        dry_run=True,
    )
    assert preview.status == "would_connect"
    assert preview.wrapped_extensions == ["memory"]
    assert config_path.read_text(encoding="utf-8") == original_text

    connected = connect_goose(config_path, db=db, policy=policy)
    assert connected.status == "connected"
    assert connected.backup_path is not None
    assert connected.backup_path.read_text(encoding="utf-8") == original_text
    assert "# preserve this operator comment" in config_path.read_text(encoding="utf-8")

    config = load_yaml(config_path)
    memory = config["extensions"]["memory"]
    assert memory["cmd"] == "aura"
    assert memory["args"] == [
        "proxy",
        "--db",
        str(db.resolve()),
        "--mode",
        "observe",
        "--policy",
        str(policy.resolve()),
        "--",
        "npx",
        "-y",
        "@modelcontextprotocol/server-memory",
    ]
    assert memory["envs"] == {"MEMORY_SCOPE": "do-not-copy-this-value"}
    state_text = connected.state_path.read_text(encoding="utf-8")
    assert "do-not-copy-this-value" not in state_text
    assert config["extensions"]["developer"]["type"] == "builtin"
    assert config["extensions"]["web"]["type"] == "streamable_http"

    backups_before = list(tmp_path.glob("config.yaml.aura-connect-backup-*"))
    repeated = connect_goose(config_path, db=db, policy=policy)
    assert repeated.status == "already_connected"
    assert list(tmp_path.glob("config.yaml.aura-connect-backup-*")) == backups_before
    assert doctor_goose(config_path).status == "healthy"

    # Unrelated changes made after enrollment must survive command restoration.
    yaml = YAML()
    with config_path.open("r", encoding="utf-8") as stream:
        edited = yaml.load(stream)
    edited["active_provider"] = "changed-after-connect"
    edited["extensions"]["memory"]["timeout"] = 999
    with config_path.open("w", encoding="utf-8") as stream:
        yaml.dump(edited, stream)

    disconnected = disconnect_goose(config_path)
    assert disconnected.status == "disconnected"
    assert disconnected.archived_state_path is not None
    assert disconnected.archived_state_path.exists()
    restored = load_yaml(config_path)
    assert restored["active_provider"] == "changed-after-connect"
    assert restored["extensions"]["memory"]["timeout"] == 999
    assert restored["extensions"]["memory"]["cmd"] == "npx"
    assert restored["extensions"]["memory"]["args"] == [
        "-y",
        "@modelcontextprotocol/server-memory",
    ]


def test_disconnect_refuses_to_clobber_extension_drift(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    write_config(config_path)
    connect_goose(config_path, db=tmp_path / "goose.db")

    yaml = YAML()
    with config_path.open("r", encoding="utf-8") as stream:
        config = yaml.load(stream)
    config["extensions"]["memory"]["args"].append("--modified-after-connect")
    with config_path.open("w", encoding="utf-8") as stream:
        yaml.dump(config, stream)

    report = doctor_goose(config_path)
    assert report.status == "unhealthy"
    assert report.drifted_extensions == ["memory"]
    with pytest.raises(ValueError, match="refusing to overwrite modified"):
        disconnect_goose(config_path)


def test_cli_can_preview_goose_enrollment(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    original = write_config(config_path)

    result = CliRunner().invoke(
        app,
        [
            "connect",
            "goose",
            "--config",
            str(config_path),
            "--db",
            str(tmp_path / "goose.db"),
            "--aura-command",
            "python",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"status": "would_connect"' in result.output
    assert '"memory"' in result.output
    assert config_path.read_text(encoding="utf-8") == original


def test_connect_refuses_an_unrunnable_or_incomplete_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.yaml"
    original = write_config(config_path)

    with pytest.raises(ValueError, match="requires a policy"):
        connect_goose(config_path, mode=EnforcementMode.ENFORCE)

    monkeypatch.setattr("aura_runtime.integrations.goose.shutil.which", lambda _: None)
    with pytest.raises(ValueError, match="not available"):
        connect_goose(config_path)
    assert config_path.read_text(encoding="utf-8") == original
    assert not (tmp_path / "aura-runtime-goose.json").exists()
