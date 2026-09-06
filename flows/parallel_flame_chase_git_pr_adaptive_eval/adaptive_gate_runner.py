#!/usr/bin/env python3
"""Stable run-local command that dispatches to the active committed eval gate.

This file intentionally uses only the Python standard library because it is copied into
``shared/bin`` and executed inside arbitrary task environments.
"""

from __future__ import annotations

import io
import json
import math
import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

GATE_PREFIX = ".pfc/adaptive-eval"
RESULT_PREFIX = "PFC_EVAL_RESULT:"
RECEIPT_PREFIX = "PFC_GATE_RECEIPT:"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_extract(archive: bytes, destination: Path) -> Path:
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        members = bundle.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or member.issym()
                or member.islnk()
            ):
                raise ValueError("unsafe path in committed gate bundle")
        bundle.extractall(destination, members=members, filter="data")
    return destination / GATE_PREFIX


def _archive(repository: Path, revision: str, destination: Path) -> Path:
    result = subprocess.run(
        ["git", "--git-dir", str(repository), "archive", "--format=tar", revision],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
    )
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
        members = archive.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or member.issym()
                or member.islnk()
            ):
                raise ValueError("unsafe path in incumbent tree")
        archive.extractall(destination, members=members, filter="data")
    return destination


def _gate_bundle(repository: Path, commit: str, destination: Path) -> Path:
    result = subprocess.run(
        [
            "git",
            "--git-dir",
            str(repository),
            "archive",
            "--format=tar",
            commit,
            GATE_PREFIX,
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
    )
    return _safe_extract(result.stdout, destination)


def _main_sha(repository: Path) -> str:
    return subprocess.run(
        ["git", "--git-dir", str(repository), "rev-parse", "refs/heads/main"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _gate_for_submission(
    registry: dict[str, Any], database: Path, pr_id: str | None
) -> str | None:
    """Bind a PR attempt to the newest gate active when it entered CI."""
    if not pr_id:
        current = registry.get("current")
        return current if isinstance(current, str) else None
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT COALESCE(ci_submitted_at, created_at) "
            "FROM pull_requests WHERE id=?",
            (pr_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError(f"unknown PR identity: {pr_id}")
    submitted_at = str(row[0])
    eligible = [
        item
        for item in registry.get("history", [])
        if isinstance(item, dict)
        and isinstance(item.get("commit"), str)
        and isinstance(item.get("activated_at"), str)
        and item["activated_at"] <= submitted_at
    ]
    return str(eligible[-1]["commit"]) if eligible else None


def _print_receipt(receipt: dict[str, Any]) -> int:
    print(f"{RECEIPT_PREFIX} {json.dumps(receipt, ensure_ascii=False, sort_keys=True)}")
    return 0 if receipt["decision"] == "accept" else 3


def _baseline(command: list[str], *, candidate: Path, incumbent_sha: str) -> int:
    if not command:
        return _print_receipt(
            {
                "schema_version": 1,
                "mode": "baseline",
                "decision": "invalid",
                "gate_commit": None,
                "incumbent_sha": incumbent_sha,
                "metric": None,
                "direction": None,
                "candidate_score": None,
                "incumbent_score": None,
                "improvement": None,
                "alignment_status": "insufficient",
                "checks": [],
                "reason": "No baseline evaluator command was discoverable.",
            }
        )
    result = subprocess.run(
        command,
        cwd=candidate,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr[-4000:], file=sys.stderr)
    cycles: int | None = None
    for line in result.stdout.splitlines():
        label, separator, raw = line.partition(":")
        if separator and label.strip().upper() == "CYCLES" and raw.strip().isdigit():
            cycles = int(raw.strip())
    return _print_receipt(
        {
            "schema_version": 1,
            "mode": "baseline",
            "decision": "accept" if result.returncode == 0 else "reject",
            "gate_commit": None,
            "incumbent_sha": incumbent_sha,
            "metric": "CYCLES" if cycles is not None else None,
            "direction": "minimize" if cycles is not None else None,
            "candidate_score": cycles,
            "incumbent_score": None,
            "improvement": None,
            "alignment_status": "baseline",
            "checks": [],
            "reason": (
                "Baseline evaluator passed before an adaptive gate was published."
                if result.returncode == 0
                else f"Baseline evaluator exited {result.returncode}."
            ),
        }
    )


def _extract_result(stdout: str) -> dict[str, Any]:
    matches = [
        line.removeprefix(RESULT_PREFIX).strip()
        for line in stdout.splitlines()
        if line.startswith(RESULT_PREFIX)
    ]
    if len(matches) != 1:
        raise ValueError("gate must print exactly one PFC_EVAL_RESULT JSON line")
    value = json.loads(matches[0])
    if not isinstance(value, dict):
        raise TypeError("gate result must be a JSON object")
    return value


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _adaptive(
    *,
    repository: Path,
    gate_commit: str,
    candidate: Path,
    incumbent_sha: str,
    baseline_command: list[str],
    objective: str,
) -> int:
    with tempfile.TemporaryDirectory(prefix="pfc-adaptive-eval-") as raw:
        temporary = Path(raw)
        incumbent = _archive(repository, incumbent_sha, temporary / "incumbent")
        bundle = _gate_bundle(repository, gate_commit, temporary / "gate")
        manifest = _load(bundle / "manifest.json")
        validity_command = list(manifest["validity_command"])
        validity = subprocess.run(
            validity_command,
            cwd=candidate,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=int(manifest.get("timeout_seconds", 600)),
        )
        if validity.returncode != 0:
            if validity.stderr:
                print(validity.stderr[-4000:], file=sys.stderr)
            return _print_receipt(
                {
                    "schema_version": 1,
                    "mode": "adaptive",
                    "decision": "reject",
                    "gate_commit": gate_commit,
                    "incumbent_sha": incumbent_sha,
                    "metric": manifest["proxy_metric"]["name"],
                    "direction": manifest["proxy_metric"]["direction"],
                    "candidate_score": None,
                    "incumbent_score": None,
                    "improvement": None,
                    "alignment_status": "aligned",
                    "checks": [
                        {
                            "name": "official-validity",
                            "passed": False,
                            "detail": f"validity command exited {validity.returncode}",
                        }
                    ],
                    "reason": "Candidate failed the gate's official validity command.",
                }
            )

        context = temporary / "context.json"
        context.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "objective": objective,
                    "pr_id": os.environ.get("PFC_EVALUATION_PR"),
                    "lane": os.environ.get("PFC_EVALUATION_LANE"),
                    "candidate_commit": os.environ.get("PFC_EVALUATION_COMMIT"),
                    "incumbent_sha": incumbent_sha,
                    "gate_commit": gate_commit,
                    "baseline_command": baseline_command,
                    "official_metric": manifest["official_metric"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        entrypoint = bundle / manifest["entrypoint"]
        command = [
            sys.executable,
            str(entrypoint),
            "--candidate-root",
            str(candidate),
            "--incumbent-root",
            str(incumbent),
            "--context",
            str(context),
        ]
        result = subprocess.run(
            command,
            cwd=bundle,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=int(manifest.get("timeout_seconds", 600)),
        )
        if result.stderr:
            print(result.stderr[-4000:], file=sys.stderr)
        if result.returncode != 0:
            raise ValueError(f"gate entrypoint exited {result.returncode}")
        measured = _extract_result(result.stdout)

    proxy = manifest["proxy_metric"]
    if (
        measured.get("metric") != proxy["name"]
        or measured.get("direction") != proxy["direction"]
    ):
        raise ValueError("gate result metric does not match committed manifest")
    candidate_score = _finite_number(measured.get("candidate_score"), "candidate_score")
    incumbent_score = _finite_number(measured.get("incumbent_score"), "incumbent_score")
    checks = measured.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ValueError("gate must return at least one robustness/leakage check")
    checks_passed = all(
        isinstance(item, dict) and item.get("passed") is True for item in checks
    )
    validity_passed = measured.get("validity_passed") is True
    improvement = (
        candidate_score - incumbent_score
        if proxy["direction"] == "maximize"
        else incumbent_score - candidate_score
    )
    threshold = float(manifest.get("minimum_improvement", 0.0))
    accepted = validity_passed and checks_passed and improvement > threshold
    receipt = {
        "schema_version": 1,
        "mode": "adaptive",
        "decision": "accept" if accepted else "reject",
        "gate_commit": gate_commit,
        "incumbent_sha": incumbent_sha,
        "metric": proxy["name"],
        "direction": proxy["direction"],
        "candidate_score": candidate_score,
        "incumbent_score": incumbent_score,
        "improvement": improvement,
        "alignment_status": "aligned",
        "checks": checks,
        "reason": str(measured.get("summary") or "Adaptive gate completed."),
    }
    return _print_receipt(receipt)


def main() -> int:
    run_root = Path(__file__).resolve().parents[2]
    directory = run_root / "shared" / "adaptive-eval"
    runtime = _load(directory / "runtime.json")
    registry = _load(directory / "registry.json")
    repository = Path(runtime["central"])
    incumbent_sha = _main_sha(repository)
    gate_commit = _gate_for_submission(
        registry,
        run_root / "shared" / "coordination.sqlite",
        os.environ.get("PFC_EVALUATION_PR"),
    )
    if gate_commit is None:
        return _baseline(
            list(runtime.get("baseline_command", [])),
            candidate=Path.cwd(),
            incumbent_sha=incumbent_sha,
        )
    try:
        return _adaptive(
            repository=repository,
            gate_commit=gate_commit,
            candidate=Path.cwd(),
            incumbent_sha=incumbent_sha,
            baseline_command=list(runtime.get("baseline_command", [])),
            objective=str(runtime.get("objective", "")),
        )
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        subprocess.SubprocessError,
    ) as why:
        return _print_receipt(
            {
                "schema_version": 1,
                "mode": "adaptive",
                "decision": "invalid",
                "gate_commit": gate_commit,
                "incumbent_sha": incumbent_sha,
                "metric": None,
                "direction": None,
                "candidate_score": None,
                "incumbent_score": None,
                "improvement": None,
                "alignment_status": "misaligned",
                "checks": [],
                "reason": f"Adaptive gate failed structurally: {type(why).__name__}: {why}",
            }
        )


if __name__ == "__main__":
    raise SystemExit(main())
