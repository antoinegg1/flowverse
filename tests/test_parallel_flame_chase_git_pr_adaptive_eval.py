from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from _parallel_flame_chase.core.api import GitPRAgents
from _parallel_flame_chase.core.models import (
    InitialPlan,
    LaneBrief,
    LaneReport,
    MissionSpec,
)
from _parallel_flame_chase.orchestration import state as runtime_state
from parallel_flame_chase_git_pr_adaptive_eval import Config
from parallel_flame_chase_git_pr_adaptive_eval.adaptive_gate_runner import (
    _gate_for_submission,
)
from parallel_flame_chase_git_pr_adaptive_eval.gate_registry import (
    AdaptiveEvalPaths,
    activate_gate,
    current_gate,
    initialize_registry,
    validate_gate_commit,
)
from parallel_flame_chase_git_pr_adaptive_eval.models import (
    GateBuildResult,
    GateManifest,
    GateReceipt,
    MetricSpec,
)
from parallel_flame_chase_git_pr_adaptive_eval.runtime import AdaptiveEvalGitPRRuntime
from pydantic import ValidationError


def run_git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def run_command(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def gate_manifest(*, direction: str = "maximize") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "task_type": "synthetic regression",
        "official_metric": {"name": "quality", "direction": direction},
        "proxy_metric": {"name": "paired_quality", "direction": direction},
        "alignment": {
            "status": "aligned",
            "evidence": ["same target and paired data"],
            "blind_spots": ["hidden distribution unavailable"],
        },
        "entrypoint": "evaluate.py",
        "validity_command": [sys.executable, "-c", "raise SystemExit(0)"],
        "self_test_command": [sys.executable, "evaluate.py", "--self-test"],
        "minimum_improvement": 0.0,
        "timeout_seconds": 30,
    }


EVALUATOR = """from __future__ import annotations
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--self-test", action="store_true")
parser.add_argument("--candidate-root")
parser.add_argument("--incumbent-root")
parser.add_argument("--context")
args = parser.parse_args()
if args.self_test:
    raise SystemExit(0)
candidate = float(Path(args.candidate_root, "score.txt").read_text())
incumbent = float(Path(args.incumbent_root, "score.txt").read_text())
result = {
    "schema_version": 1,
    "metric": "paired_quality",
    "direction": "maximize",
    "candidate_score": candidate,
    "incumbent_score": incumbent,
    "validity_passed": True,
    "checks": [{"name": "paired-input", "passed": True, "detail": "same fixture"}],
    "summary": "paired synthetic comparison",
}
print("PFC_EVAL_RESULT: " + json.dumps(result, sort_keys=True))
"""

PLAN = InitialPlan(
    lanes=[
        LaneBrief(
            lane=f"lane-{number}",  # type: ignore[arg-type]
            mission=MissionSpec(
                title=f"Approach {number}",
                objective=f"Test approach {number}",
                success_criteria=["Produce a measured candidate"],
                approach_class=f"class-{number}",
                information_question=f"Does approach {number} generalize?",
            ),
        )
        for number in range(1, 4)
    ]
)


class FakeSession:
    def __init__(self, agent: FakeAgent, cwd: Path) -> None:
        self.agent = agent
        self.cwd = cwd

    def __call__(self, prompt: str, *, suppress: bool, schema: type[Any]) -> Any:
        self.agent.prompts.append((self.cwd, prompt, schema))
        if schema is InitialPlan:
            return PLAN
        if schema is GateBuildResult:
            return GateBuildResult(
                status="deferred",
                summary="The synthetic task does not expose enough protocol detail.",
                task_type="synthetic",
                official_metric=MetricSpec(name="quality", direction="maximize"),
            )
        if schema is LaneReport:
            return LaneReport(
                status="progress",
                summary="The lane started without waiting for gate construction.",
                next_step="Run a bounded experiment.",
            )
        raise AssertionError(schema)

    def close(self) -> None:
        pass


class FakeAgent:
    def __init__(self) -> None:
        self.prompts: list[tuple[Path, str, type[Any]]] = []

    def new(self, cwd: str | Path | None = None) -> FakeSession:
        return FakeSession(self, Path(cwd or ".").resolve())


def fake_agents() -> GitPRAgents:
    return GitPRAgents(*(FakeAgent() for _ in range(7)))  # type: ignore[arg-type]


def make_gate_repository(tmp_path: Path) -> tuple[Path, Path, str, str]:
    seed = tmp_path / "seed"
    central = tmp_path / "repository.git"
    run_root = tmp_path / "run"
    seed.mkdir()
    run_git(seed, "init", "-b", "main")
    run_git(seed, "config", "user.name", "Gate Test")
    run_git(seed, "config", "user.email", "gate@example.invalid")
    (seed / "score.txt").write_text("1\n", encoding="utf-8")
    run_git(seed, "add", "score.txt")
    run_git(seed, "commit", "-m", "baseline")
    baseline = run_git(seed, "rev-parse", "HEAD")
    run_git(seed, "init", "--bare", "--initial-branch=main", str(central))
    run_git(seed, "remote", "add", "origin", str(central))
    run_git(seed, "push", "origin", "main")

    bundle = seed / ".pfc" / "adaptive-eval"
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_text(
        json.dumps(gate_manifest(), indent=2), encoding="utf-8"
    )
    (bundle / "evaluate.py").write_text(EVALUATOR, encoding="utf-8")
    run_git(seed, "add", "-f", ".pfc/adaptive-eval")
    run_git(seed, "commit", "-m", "feat: adaptive proxy gate")
    gate_commit = run_git(seed, "rev-parse", "HEAD")
    run_git(seed, "push", "origin", "HEAD:refs/heads/pfc/eval-gate")
    return central, run_root, baseline, gate_commit


def test_manifest_rejects_proxy_direction_mismatch() -> None:
    value = gate_manifest()
    value["proxy_metric"]["direction"] = "minimize"
    with pytest.raises(ValidationError, match="directions must match"):
        GateManifest.model_validate(value)


def test_gate_applies_only_to_prs_opened_after_activation(tmp_path: Path) -> None:
    database = tmp_path / "coordination.sqlite"
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE pull_requests(id TEXT, created_at TEXT)")
        connection.execute(
            "INSERT INTO pull_requests VALUES(?, ?)",
            ("PR000001", "2026-01-01T00:00:00Z"),
        )
        connection.execute(
            "INSERT INTO pull_requests VALUES(?, ?)",
            ("PR000002", "2026-01-01T00:02:00Z"),
        )
        connection.commit()
    finally:
        connection.close()
    commit = "a" * 40
    registry = {
        "current": commit,
        "history": [{"commit": commit, "activated_at": "2026-01-01T00:01:00Z"}],
    }
    assert _gate_for_submission(registry, database, "PR000001") is None
    assert _gate_for_submission(registry, database, "PR000002") == commit


def test_gate_commit_is_validated_and_activated(tmp_path: Path) -> None:
    central, run_root, _baseline, gate_commit = make_gate_repository(tmp_path)
    paths = AdaptiveEvalPaths(run_root)
    initialize_registry(
        paths,
        central=central,
        baseline_command=[sys.executable, "-c", "raise SystemExit(0)"],
        objective="Improve the synthetic quality score.",
    )
    manifest = validate_gate_commit(central, gate_commit)
    assert manifest.task_type == "synthetic regression"
    activated = activate_gate(
        paths,
        repository=central,
        commit_sha=gate_commit,
        reason="paired metric and data protocol match",
    )
    assert activated["commit"] == gate_commit
    assert current_gate(paths) == gate_commit


def test_runner_compares_candidate_to_current_main_through_committed_gate(
    tmp_path: Path,
) -> None:
    central, run_root, baseline, gate_commit = make_gate_repository(tmp_path)
    paths = AdaptiveEvalPaths(run_root)
    initialize_registry(
        paths,
        central=central,
        baseline_command=[sys.executable, "-c", "raise SystemExit(0)"],
        objective="Improve the synthetic quality score.",
    )
    activate_gate(
        paths,
        repository=central,
        commit_sha=gate_commit,
        reason="test activation",
    )
    runner_source = (
        Path(__file__).parents[1]
        / "flows"
        / "parallel_flame_chase_git_pr_adaptive_eval"
        / "adaptive_gate_runner.py"
    )
    runner = run_root / "shared" / "bin" / "pfc-evaluate"
    runner.parent.mkdir(parents=True)
    shutil.copy2(runner_source, runner)
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "score.txt").write_text("2\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(runner)],
        cwd=candidate,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    line = next(
        item
        for item in result.stdout.splitlines()
        if item.startswith("PFC_GATE_RECEIPT:")
    )
    receipt = GateReceipt.model_validate_json(line.split(":", 1)[1])
    assert receipt.decision == "accept"
    assert receipt.gate_commit == gate_commit
    assert receipt.incumbent_sha == baseline
    assert receipt.improvement == 1.0


def test_runner_rejects_regression_even_when_gate_script_runs(tmp_path: Path) -> None:
    central, run_root, _baseline, gate_commit = make_gate_repository(tmp_path)
    paths = AdaptiveEvalPaths(run_root)
    initialize_registry(
        paths,
        central=central,
        baseline_command=[sys.executable, "-c", "raise SystemExit(0)"],
        objective="Improve the synthetic quality score.",
    )
    activate_gate(
        paths,
        repository=central,
        commit_sha=gate_commit,
        reason="test activation",
    )
    runner_source = (
        Path(__file__).parents[1]
        / "flows"
        / "parallel_flame_chase_git_pr_adaptive_eval"
        / "adaptive_gate_runner.py"
    )
    runner = run_root / "shared" / "bin" / "pfc-evaluate"
    runner.parent.mkdir(parents=True)
    shutil.copy2(runner_source, runner)
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "score.txt").write_text("0\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(runner)],
        cwd=candidate,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 3
    line = next(
        item
        for item in result.stdout.splitlines()
        if item.startswith("PFC_GATE_RECEIPT:")
    )
    receipt = GateReceipt.model_validate_json(line.split(":", 1)[1])
    assert receipt.decision == "reject"
    assert receipt.improvement == -1.0


def test_lanes_can_start_while_coordinator_builds_gate(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "TASK.md").write_text(
        "You MUST only modify `candidate.py`.\nYou MUST run `python evaluator.py`.\n",
        encoding="utf-8",
    )
    (source / "candidate.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "evaluator.py").write_text("print('CYCLES: 1')\n", encoding="utf-8")
    monkeypatch.chdir(source)
    monkeypatch.setattr(runtime_state, "home", lambda: tmp_path / "humanize-home")
    chosen = fake_agents()
    runtime = AdaptiveEvalGitPRRuntime(
        chosen,
        "Improve candidate.py. You MUST run `python evaluator.py`.",
        Config(rest_seconds=0.05),
        {},
    )
    try:
        runtime.prepare()
        assert runtime._start_gate_build() is True
        for lane_runtime in runtime.lanes.values():
            runtime._schedule_lane(lane_runtime)
        assert runtime.coordinator_work.future is not None
        runtime.coordinator_work.future.result(timeout=5)
        assert runtime._collect_coordinator_work() is True
        assert runtime.control["adaptive_eval"]["build_status"] == "deferred"
        command = runtime.store.meta("trusted_evaluator_command")
        assert Path(command[1]).name == "pfc-evaluate"
        for lane_agent in (
            chosen.lane_1_actor_a,
            chosen.lane_2_actor_a,
            chosen.lane_3_actor_a,
        ):
            assert lane_agent.prompts  # type: ignore[attr-defined]
            assert "open the draft before evaluating" in lane_agent.prompts[0][1]  # type: ignore[attr-defined]
    finally:
        runtime._close_sessions()
        runtime.executor.shutdown(wait=True, cancel_futures=True)


def test_audited_receipt_is_merged_automatically(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "TASK.md").write_text(
        "You MUST only modify `candidate.py`.\nYou MUST run `python evaluator.py`.\n",
        encoding="utf-8",
    )
    (source / "candidate.py").write_text("VALUE = 2\n", encoding="utf-8")
    (source / "evaluator.py").write_text(
        "from candidate import VALUE\nprint(f'CYCLES: {VALUE}')\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(source)
    monkeypatch.setattr(runtime_state, "home", lambda: tmp_path / "humanize-home")
    runtime = AdaptiveEvalGitPRRuntime(
        fake_agents(),
        "Improve candidate.py. You MUST run `python evaluator.py`.",
        Config(rest_seconds=0.05),
        {},
    )
    try:
        runtime.prepare()
        lane = runtime.git_paths.lane("lane-1")
        run_git(lane, "switch", "-c", "lane-1/improve")
        (lane / "candidate.py").write_text("VALUE = 1\n", encoding="utf-8")
        run_git(lane, "add", "candidate.py")
        run_git(lane, "commit", "-m", "improve candidate")
        run_git(lane, "push", "-u", "origin", "HEAD")
        cli = str(runtime.git_paths.bin / "pfc")
        opened = run_command(
            lane,
            cli,
            "pr",
            "open",
            "--draft",
            "--title",
            "Improve candidate",
            "--hypothesis",
            "Lower is better in the baseline evaluator",
        )
        pr_id = json.loads(opened.stdout)["id"]
        lane_two_listing = run_command(
            runtime.git_paths.lane("lane-2"), cli, "pr", "list"
        )
        assert json.loads(lane_two_listing.stdout) == []
        evaluated = run_command(
            lane,
            cli,
            "evaluate",
            "--pr",
            pr_id,
            "--",
            sys.executable,
            str(runtime._runner_path()),
        )
        receipt_id = json.loads(evaluated.stdout.splitlines()[-1])["receipt_id"]
        run_command(
            lane,
            cli,
            "pr",
            "ready",
            pr_id,
            "--receipt",
            receipt_id,
        )
        assert runtime._scan_receipts() is True
        adaptive = runtime.control["adaptive_eval"]
        adaptive["audit_queue"].clear()
        adaptive["audits"][receipt_id] = {
            "status": "accept",
            "summary": "baseline receipt is structurally aligned",
            "evidence": [],
        }
        assert runtime._process_fast_path() is True
        assert runtime._observe_main() is True
        assert runtime.store.pr(pr_id)["status"] == "merged"
        assert (source / "candidate.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    finally:
        runtime._close_sessions()
        runtime.executor.shutdown(wait=True, cancel_futures=True)
