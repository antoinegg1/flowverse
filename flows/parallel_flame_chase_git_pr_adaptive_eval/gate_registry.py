"""Validate and activate coordinator-authored proxy-evaluation commits."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast

from _parallel_flame_chase.core.utils import atomic_json, now

from .models import GateManifest
from .repository import git

GATE_PREFIX = ".pfc/adaptive-eval"
MANIFEST_PATH = f"{GATE_PREFIX}/manifest.json"
GATE_REF = "refs/heads/pfc/eval-gate"


@dataclass(frozen=True, slots=True)
class AdaptiveEvalPaths:
    """Run-owned adaptive-evaluation state."""

    root: Path

    @property
    def directory(self) -> Path:
        return self.root / "shared" / "adaptive-eval"

    @property
    def registry(self) -> Path:
        return self.directory / "registry.json"

    @property
    def runtime_config(self) -> Path:
        return self.directory / "runtime.json"

    @property
    def coordinator_inbox(self) -> Path:
        return self.directory / "coordinator-inbox.jsonl"


def _commit_parents(repository: Path, commit_sha: str) -> list[str]:
    result = cast(
        "subprocess.CompletedProcess[str]",
        git("rev-list", "--parents", "-n", "1", commit_sha, cwd=repository),
    )
    fields = result.stdout.strip().split()
    return fields[1:]


def _gate_paths(repository: Path, parent: str, commit_sha: str) -> list[str]:
    result = cast(
        "subprocess.CompletedProcess[str]",
        git(
            "diff",
            "--name-only",
            "-z",
            parent,
            commit_sha,
            cwd=repository,
        ),
    )
    return [item for item in result.stdout.split("\0") if item]


def _safe_bundle_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe gate bundle path: {value}")
    return path


def read_manifest(repository: Path, commit_sha: str) -> GateManifest:
    """Read and validate the manifest from one immutable Git commit."""
    result = cast(
        "subprocess.CompletedProcess[str]",
        git("show", f"{commit_sha}:{MANIFEST_PATH}", cwd=repository),
    )
    return GateManifest.model_validate_json(result.stdout)


def _extract_bundle(repository: Path, commit_sha: str, destination: Path) -> Path:
    archive = cast(
        "subprocess.CompletedProcess[bytes]",
        git(
            "archive",
            "--format=tar",
            commit_sha,
            GATE_PREFIX,
            cwd=repository,
            text=False,
        ),
    )
    with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as bundle:
        members = bundle.getmembers()
        for member in members:
            _safe_bundle_path(member.name)
            if member.issym() or member.islnk():
                raise ValueError("gate bundle may not contain links")
        bundle.extractall(destination, members=members, filter="data")
    return destination / GATE_PREFIX


def validate_gate_commit(repository: Path, commit_sha: str) -> GateManifest:
    """Validate scope, manifest, entrypoint, and self-test of a gate commit."""
    if len(commit_sha) != 40 or any(
        character not in "0123456789abcdef" for character in commit_sha
    ):
        raise ValueError("gate commit must be a full lowercase SHA-1")
    git("cat-file", "-e", f"{commit_sha}^{{commit}}", cwd=repository)
    parents = _commit_parents(repository, commit_sha)
    if len(parents) != 1:
        raise ValueError("gate update must be a one-parent commit")
    changed = _gate_paths(repository, parents[0], commit_sha)
    if not changed or any(not path.startswith(f"{GATE_PREFIX}/") for path in changed):
        raise ValueError("gate commit may change only .pfc/adaptive-eval/**")
    if MANIFEST_PATH not in changed:
        raise ValueError("gate commit must update its manifest")

    manifest = read_manifest(repository, commit_sha)
    entrypoint = _safe_bundle_path(manifest.entrypoint)
    if entrypoint.parts[0] == ".pfc":
        raise ValueError(
            "entrypoint is relative to the gate bundle, not repository root"
        )
    with tempfile.TemporaryDirectory(prefix="pfc-gate-validate-") as raw:
        bundle = _extract_bundle(repository, commit_sha, Path(raw))
        entrypoint_path = bundle / entrypoint
        if not entrypoint_path.is_file() or entrypoint_path.is_symlink():
            raise ValueError("gate entrypoint is missing or unsafe")
        command = list(manifest.self_test_command)
        if command[0] in {"python", "python3"}:
            command[0] = sys.executable
        result = subprocess.run(
            command,
            cwd=bundle,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=manifest.timeout_seconds,
        )
        if result.returncode != 0:
            diagnostic = (result.stderr or result.stdout)[-2000:]
            raise ValueError(f"gate self-test failed: {diagnostic}")
    return manifest


def initialize_registry(
    paths: AdaptiveEvalPaths,
    *,
    central: Path,
    baseline_command: list[str],
    objective: str,
) -> None:
    """Create immutable runtime context and an empty gate history."""
    paths.directory.mkdir(parents=True, exist_ok=True)
    if not paths.runtime_config.exists():
        atomic_json(
            paths.runtime_config,
            {
                "version": 1,
                "central": str(central),
                "baseline_command": baseline_command,
                "objective": objective,
            },
        )
    if not paths.registry.exists():
        atomic_json(paths.registry, {"version": 1, "current": None, "history": []})
    paths.coordinator_inbox.touch(exist_ok=True)


def activate_gate(
    paths: AdaptiveEvalPaths,
    *,
    repository: Path,
    commit_sha: str,
    reason: str,
) -> dict[str, object]:
    """Append an aligned, validated gate commit to the activation history."""
    pushed = cast(
        "subprocess.CompletedProcess[str]",
        git("rev-parse", GATE_REF, cwd=repository),
    ).stdout.strip()
    if pushed != commit_sha:
        raise ValueError("gate commit is not the pushed dedicated gate ref tip")
    manifest = validate_gate_commit(repository, commit_sha)
    registry = json.loads(paths.registry.read_text(encoding="utf-8"))
    history = cast("list[dict[str, object]]", registry["history"])
    if any(item.get("commit") == commit_sha for item in history):
        registry["current"] = commit_sha
        atomic_json(paths.registry, registry)
        return cast(
            "dict[str, object]",
            next(item for item in history if item.get("commit") == commit_sha),
        )
    record: dict[str, object] = {
        "commit": commit_sha,
        "activated_at": now(),
        "reason": reason,
        "task_type": manifest.task_type,
        "official_metric": manifest.official_metric.model_dump(mode="json"),
        "proxy_metric": manifest.proxy_metric.model_dump(mode="json"),
        "alignment": manifest.alignment.model_dump(mode="json"),
    }
    history.append(record)
    registry["current"] = commit_sha
    atomic_json(paths.registry, registry)
    return record


def current_gate(paths: AdaptiveEvalPaths) -> str | None:
    """Return the active gate commit, if construction has completed."""
    registry = json.loads(paths.registry.read_text(encoding="utf-8"))
    current = registry.get("current")
    return current if isinstance(current, str) else None


__all__ = [
    "GATE_PREFIX",
    "GATE_REF",
    "MANIFEST_PATH",
    "AdaptiveEvalPaths",
    "activate_gate",
    "current_gate",
    "initialize_registry",
    "read_manifest",
    "validate_gate_commit",
]
