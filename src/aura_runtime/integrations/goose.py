"""Reversible enrollment of Goose stdio extensions behind Aura's MCP proxy."""

from __future__ import annotations

import copy
import json
import os
import shutil
import stat
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from ruamel.yaml import YAML

from aura_runtime.flight import EnforcementMode

STATE_VERSION = "1"
STATE_NAME = "aura-runtime-goose.json"


class GooseConnectionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["1"] = STATE_VERSION
    config_path: Path
    backup_path: Path
    connected_at: datetime
    aura_command: str
    original_commands: dict[str, dict[str, Any]]
    expected_commands: dict[str, dict[str, Any]]


class GooseIntegrationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: Literal["connect", "disconnect", "doctor"]
    status: str
    config_path: Path
    state_path: Path
    dry_run: bool = False
    healthy: bool | None = None
    eligible_extensions: list[str] = Field(default_factory=list)
    wrapped_extensions: list[str] = Field(default_factory=list)
    skipped_extensions: list[str] = Field(default_factory=list)
    drifted_extensions: list[str] = Field(default_factory=list)
    orphaned_wrappers: list[str] = Field(default_factory=list)
    backup_path: Path | None = None
    archived_state_path: Path | None = None
    aura_command_available: bool | None = None


def goose_config_path(
    *,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> Path:
    """Return Goose's documented primary configuration path."""
    env = environ if environ is not None else os.environ
    system = platform if platform is not None else sys.platform
    root_override = env.get("GOOSE_PATH_ROOT")
    if root_override:
        return Path(root_override).expanduser() / "config" / "config.yaml"
    if system == "win32":
        appdata = env.get("APPDATA")
        if not appdata:
            raise ValueError("APPDATA is required to locate Goose config on Windows")
        return Path(appdata) / "Block" / "goose" / "config" / "config.yaml"
    user_home = home if home is not None else Path.home()
    xdg_config = env.get("XDG_CONFIG_HOME")
    config_root = Path(xdg_config).expanduser() if xdg_config else user_home / ".config"
    return config_root / "goose" / "config.yaml"


def default_aura_db_path(
    *, environ: Mapping[str, str] | None = None, platform: str | None = None
) -> Path:
    env = environ if environ is not None else os.environ
    system = platform if platform is not None else sys.platform
    explicit = env.get("AURA_DATA_DIR")
    if explicit:
        return Path(explicit).expanduser() / "goose.db"
    if system == "win32":
        local_appdata = env.get("LOCALAPPDATA") or env.get("APPDATA")
        if not local_appdata:
            raise ValueError("LOCALAPPDATA or APPDATA is required for Aura data on Windows")
        return Path(local_appdata) / "Aura Runtime" / "goose.db"
    data_root = Path(env.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return data_root.expanduser() / "aura-runtime" / "goose.db"


def _yaml() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def _load_config(path: Path) -> tuple[YAML, Any]:
    if not path.is_file():
        raise ValueError(f"Goose config not found at {path}")
    yaml = _yaml()
    with path.open("r", encoding="utf-8") as stream:
        config = yaml.load(stream)
    if not isinstance(config, Mapping):
        raise ValueError("Goose config must contain a YAML mapping")
    extensions = config.get("extensions", {})
    if extensions is None:
        config["extensions"] = {}
    elif not isinstance(extensions, Mapping):
        raise ValueError("Goose config 'extensions' must be a mapping")
    return yaml, config


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain(item) for item in value]
    return value


def _atomic_yaml_write(path: Path, yaml: YAML, config: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            yaml.dump(config, stream)
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")


def _backup(path: Path, label: str) -> Path:
    backup = path.with_name(f"{path.name}.aura-{label}-{_timestamp()}")
    shutil.copy2(path, backup)
    return backup


def _state_path(config_path: Path) -> Path:
    return config_path.with_name(STATE_NAME)


def _load_state(path: Path) -> GooseConnectionState:
    try:
        return GooseConnectionState.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid Aura Goose state at {path}: {error}") from error


def _is_stdio(extension: Any) -> bool:
    return isinstance(extension, Mapping) and extension.get("type") == "stdio"


def _command_view(extension: Any) -> dict[str, Any] | None:
    """Return only the fields Aura owns, excluding extension environment values."""
    if not isinstance(extension, Mapping):
        return None
    return {
        "cmd": _plain(extension.get("cmd")),
        "args": _plain(extension.get("args", [])),
    }


def _looks_wrapped(extension: Any) -> bool:
    if not isinstance(extension, Mapping):
        return False
    args = extension.get("args", [])
    return (
        isinstance(args, Sequence)
        and not isinstance(args, str)
        and len(args) >= 2
        and args[0] == "proxy"
        and "--" in args
    )


def _wrapper(
    original: dict[str, Any],
    *,
    aura_command: str,
    db: Path,
    mode: EnforcementMode,
    policy: Path | None,
) -> dict[str, Any]:
    command = original.get("cmd")
    arguments = original.get("args", [])
    if not isinstance(command, str) or not command:
        raise ValueError("stdio extension has no executable 'cmd'")
    if not isinstance(arguments, list) or not all(isinstance(arg, str) for arg in arguments):
        raise ValueError("stdio extension 'args' must be a list of strings")

    proxy_args = ["proxy", "--db", str(db), "--mode", mode.value]
    if policy is not None:
        proxy_args.extend(["--policy", str(policy)])
    proxy_args.extend(["--", command, *arguments])
    wrapped = copy.deepcopy(original)
    wrapped["cmd"] = aura_command
    wrapped["args"] = proxy_args
    return wrapped


def connect_goose(
    config_path: Path,
    *,
    policy: Path | None = None,
    db: Path | None = None,
    mode: EnforcementMode = EnforcementMode.OBSERVE,
    aura_command: str = "aura",
    dry_run: bool = False,
) -> GooseIntegrationReport:
    config_path = config_path.expanduser().resolve()
    policy = policy.expanduser().resolve() if policy else None
    db = (db or default_aura_db_path()).expanduser().resolve()
    if mode == EnforcementMode.ENFORCE and policy is None:
        raise ValueError("mode 'enforce' requires a policy")
    if policy is not None and not policy.is_file():
        raise ValueError(f"Aura policy not found at {policy}")
    state_path = _state_path(config_path)
    yaml, config = _load_config(config_path)
    extensions = config["extensions"]

    if state_path.exists():
        state = _load_state(state_path)
        drifted = [
            name
            for name, expected in state.expected_commands.items()
            if _command_view(extensions.get(name)) != expected
        ]
        if drifted:
            names = ", ".join(sorted(drifted))
            raise ValueError(f"existing Aura Goose connection has drifted extensions: {names}")
        return GooseIntegrationReport(
            operation="connect",
            status="already_connected",
            config_path=config_path,
            state_path=state_path,
            dry_run=dry_run,
            healthy=shutil.which(state.aura_command) is not None,
            eligible_extensions=sorted(state.original_commands),
            wrapped_extensions=sorted(state.expected_commands),
            backup_path=state.backup_path,
            aura_command_available=shutil.which(state.aura_command) is not None,
        )

    original_commands: dict[str, dict[str, Any]] = {}
    expected_commands: dict[str, dict[str, Any]] = {}
    skipped: list[str] = []
    orphaned: list[str] = []
    for name, extension in extensions.items():
        if not _is_stdio(extension):
            skipped.append(str(name))
            continue
        if _looks_wrapped(extension):
            orphaned.append(str(name))
            continue
        original = _plain(extension)
        try:
            wrapped = _wrapper(
                original,
                aura_command=aura_command,
                db=db,
                mode=mode,
                policy=policy,
            )
        except ValueError as error:
            raise ValueError(f"cannot wrap Goose extension {name!r}: {error}") from error
        original_commands[str(name)] = _command_view(original) or {}
        expected_commands[str(name)] = _command_view(wrapped) or {}

    if orphaned:
        names = ", ".join(sorted(orphaned))
        raise ValueError(
            f"found Aura-like wrappers without enrollment state: {names}; restore them first"
        )
    eligible = sorted(original_commands)
    if not eligible:
        return GooseIntegrationReport(
            operation="connect",
            status="no_stdio_extensions",
            config_path=config_path,
            state_path=state_path,
            dry_run=dry_run,
            healthy=False,
            skipped_extensions=sorted(skipped),
            aura_command_available=shutil.which(aura_command) is not None,
        )
    if dry_run:
        return GooseIntegrationReport(
            operation="connect",
            status="would_connect",
            config_path=config_path,
            state_path=state_path,
            dry_run=True,
            healthy=None,
            eligible_extensions=eligible,
            wrapped_extensions=eligible,
            skipped_extensions=sorted(skipped),
            aura_command_available=shutil.which(aura_command) is not None,
        )
    if shutil.which(aura_command) is None:
        raise ValueError(
            f"Aura command {aura_command!r} is not available; install Aura or use "
            "--aura-command"
        )

    backup = _backup(config_path, "connect-backup")
    for name, wrapped in expected_commands.items():
        extensions[name]["cmd"] = wrapped["cmd"]
        extensions[name]["args"] = wrapped["args"]
    _atomic_yaml_write(config_path, yaml, config)
    state = GooseConnectionState(
        config_path=config_path,
        backup_path=backup,
        connected_at=datetime.now(UTC),
        aura_command=aura_command,
        original_commands=original_commands,
        expected_commands=expected_commands,
    )
    _atomic_json_write(state_path, state.model_dump(mode="json"))
    return GooseIntegrationReport(
        operation="connect",
        status="connected",
        config_path=config_path,
        state_path=state_path,
        healthy=shutil.which(aura_command) is not None,
        eligible_extensions=eligible,
        wrapped_extensions=eligible,
        skipped_extensions=sorted(skipped),
        backup_path=backup,
        aura_command_available=shutil.which(aura_command) is not None,
    )


def doctor_goose(config_path: Path) -> GooseIntegrationReport:
    config_path = config_path.expanduser().resolve()
    state_path = _state_path(config_path)
    if not config_path.is_file():
        return GooseIntegrationReport(
            operation="doctor",
            status="config_not_found",
            config_path=config_path,
            state_path=state_path,
            healthy=False,
        )
    _, config = _load_config(config_path)
    extensions = config["extensions"]
    eligible = sorted(str(name) for name, value in extensions.items() if _is_stdio(value))
    if not state_path.exists():
        orphaned = sorted(
            str(name) for name, value in extensions.items() if _looks_wrapped(value)
        )
        return GooseIntegrationReport(
            operation="doctor",
            status="not_connected" if not orphaned else "orphaned_wrappers",
            config_path=config_path,
            state_path=state_path,
            healthy=False,
            eligible_extensions=eligible,
            orphaned_wrappers=orphaned,
        )

    state = _load_state(state_path)
    drifted = sorted(
        name
        for name, expected in state.expected_commands.items()
        if _command_view(extensions.get(name)) != expected
    )
    available = shutil.which(state.aura_command) is not None
    healthy = not drifted and available
    return GooseIntegrationReport(
        operation="doctor",
        status="healthy" if healthy else "unhealthy",
        config_path=config_path,
        state_path=state_path,
        healthy=healthy,
        eligible_extensions=eligible,
        wrapped_extensions=sorted(state.expected_commands),
        drifted_extensions=drifted,
        backup_path=state.backup_path,
        aura_command_available=available,
    )


def disconnect_goose(config_path: Path, *, dry_run: bool = False) -> GooseIntegrationReport:
    config_path = config_path.expanduser().resolve()
    state_path = _state_path(config_path)
    if not state_path.is_file():
        return GooseIntegrationReport(
            operation="disconnect",
            status="not_connected",
            config_path=config_path,
            state_path=state_path,
            dry_run=dry_run,
            healthy=False,
        )
    state = _load_state(state_path)
    yaml, config = _load_config(config_path)
    extensions = config["extensions"]
    drifted = sorted(
        name
        for name, expected in state.expected_commands.items()
        if _command_view(extensions.get(name))
        not in (expected, state.original_commands[name])
    )
    if drifted:
        names = ", ".join(drifted)
        raise ValueError(f"refusing to overwrite modified Goose extensions: {names}")
    if dry_run:
        return GooseIntegrationReport(
            operation="disconnect",
            status="would_disconnect",
            config_path=config_path,
            state_path=state_path,
            dry_run=True,
            healthy=None,
            wrapped_extensions=sorted(state.expected_commands),
        )

    backup = _backup(config_path, "disconnect-backup")
    for name, original in state.original_commands.items():
        extensions[name]["cmd"] = original["cmd"]
        extensions[name]["args"] = original["args"]
    _atomic_yaml_write(config_path, yaml, config)
    archived_state = state_path.with_name(
        f"{state_path.stem}.disconnected-{_timestamp()}{state_path.suffix}"
    )
    os.replace(state_path, archived_state)
    return GooseIntegrationReport(
        operation="disconnect",
        status="disconnected",
        config_path=config_path,
        state_path=state_path,
        healthy=False,
        wrapped_extensions=sorted(state.expected_commands),
        backup_path=backup,
        archived_state_path=archived_state,
    )
