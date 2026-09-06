"""Git/PR runtime with a coordinator-built, versioned proxy-evaluation gate."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from _parallel_flame_chase.core.models import InitialPlan, LaneName
from _parallel_flame_chase.core.utils import close_safely, json_copy
from _parallel_flame_chase.persistence.workspace import RunPaths, initialize_paths
from hmz.flows import Stopped
from parallel_flame_chase_git_pr.runtime import GitPRRuntime as BaseGitPRRuntime

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Callable
    from concurrent.futures import Future

    from hmz.flows import Session

from .gate_registry import (
    GATE_REF,
    AdaptiveEvalPaths,
    activate_gate,
    current_gate,
    initialize_registry,
)
from .models import GateBuildResult, GateReceipt, GateReviewResult
from .prompts import (
    gate_build_prompt,
    gate_review_prompt,
    git_planning_prompt,
    lane_protocol,
)
from .repository import (
    GitRunPaths,
    create_fast_path_merge,
    discover_allowed_paths,
    discover_evaluator_command,
    initialize_shadow_repository,
    validate_changed_paths,
    write_branch_protection_context,
)
from .storage import CoordinationStore

GATE_RECEIPT_PATTERN = re.compile(r"(?m)^PFC_GATE_RECEIPT:\s*(\{.*\})\s*$")
FINAL_RESULT_REPAIR_ATTEMPT = 2


@dataclass(slots=True)
class CoordinatorWork:
    """One gate build or receipt audit using the single coordinator slot."""

    kind: Literal["build", "review"] | None = None
    future: Future[GateBuildResult | GateReviewResult | None] | None = None
    session: Session | None = None
    receipt_id: str | None = None
    gate_ref_before: str | None = None


@dataclass(frozen=True, slots=True)
class CIExecution:
    """Captured result from one isolated run-local CI process."""

    returncode: int
    stdout: str
    stderr: str


@dataclass(slots=True)
class CIWork:
    """The single serialized CI job, bound to one frozen PR revision."""

    future: Future[CIExecution] | None = None
    pr: dict[str, object] | None = None
    workspace: Path | None = None


def _run_ci_command(command: list[str], workspace: Path) -> CIExecution:
    result = subprocess.run(
        command,
        cwd=workspace,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    return CIExecution(result.returncode, result.stdout, result.stderr)


def _run_structured(
    session: Session,
    prompt: str,
    schema: type[GateBuildResult | GateReviewResult],
) -> GateBuildResult | GateReviewResult | None:
    """Repair only the result shape after coordinator work has completed."""
    current = prompt
    for attempt in range(3):
        try:
            result = session(current, suppress=False, schema=schema)
        except Stopped:
            raise
        except ValueError as why:
            if attempt == FINAL_RESULT_REPAIR_ATTEMPT:
                raise
            current = f"""Your completed result failed schema validation: {why}

Do not repeat Git actions or edit files. Return only a corrected {schema.__name__} describing the
work already completed. Do not invent a commit that was not pushed.
"""
            continue
        if result is not None:
            return result
        current = f"Return only {schema.__name__} for the work you just completed."
    return None


class AdaptiveEvalGitPRRuntime(BaseGitPRRuntime):
    """Automatically integrate only candidates that pass a reviewed adaptive gate."""

    mode_name = "git-pr-adaptive-eval"
    skill_name = "adaptive-pr-eval-coordinator"
    orchestrator_role_name = "coordinator"
    executor_workers = 5
    planning_cadence = (
        "Dispatch lanes once; gate construction and per-receipt audits use fresh coordinator "
        "sessions while lanes continue independently."
    )

    def __init__(
        self,
        agents: Any,
        task: str,
        config: Any,
        state: dict[str, Any] | None,
        *,
        clock: Callable[[], dt.datetime] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        max_turns: int | None = None,
    ) -> None:
        super().__init__(
            agents,
            task,
            config,
            state,
            clock=clock,
            sleeper=sleeper,
            max_turns=max_turns,
        )
        self.adaptive_paths: AdaptiveEvalPaths
        self.coordinator_work = CoordinatorWork()
        self.ci_work = CIWork()

    def _new_mode_control(self) -> dict[str, object]:
        control = super()._new_mode_control()
        control["adaptive_eval"] = {
            "build_status": "not-started",
            "build_attempts": 0,
            "active_gate": None,
            "audit_queue": [],
            "audits": {},
            "ci": {"completed": 0, "passed": 0, "failed": 0},
        }
        return control

    def _validate_mode_control(self) -> None:
        super()._validate_mode_control()
        adaptive = self.control.get("adaptive_eval")
        if not isinstance(adaptive, dict):
            raise TypeError("resumable adaptive-eval control is malformed")
        if adaptive.get("build_status") not in {
            "not-started",
            "building",
            "published",
            "deferred",
            "failed",
        }:
            raise ValueError("invalid adaptive gate build status")
        if not isinstance(adaptive.get("audit_queue"), list) or not isinstance(
            adaptive.get("audits"), dict
        ):
            raise TypeError("resumable adaptive audit state is malformed")
        ci = adaptive.setdefault("ci", {"completed": 0, "passed": 0, "failed": 0})
        if not isinstance(ci, dict) or any(
            not isinstance(ci.get(field), int)
            or isinstance(ci.get(field), bool)
            or int(ci[field]) < 0
            for field in ("completed", "passed", "failed")
        ):
            raise TypeError("resumable adaptive CI state is malformed")

    def _source_files(self) -> tuple[Path, Path, Path]:
        directory = Path(__file__).resolve().parent
        return (
            directory / "agent_cli.py",
            directory / "storage.py",
            directory / "pre_receive.py",
        )

    def _runner_source(self) -> Path:
        return Path(__file__).resolve().parent / "adaptive_gate_runner.py"

    def _runner_path(self) -> Path:
        return self.git_paths.bin / "pfc-evaluate"

    def _create_run(self, objective: str) -> None:
        self.control = self._new_control(objective)
        self.paths = RunPaths(Path(cast("str", self.control["run_root"])), self.source)
        self.paths.root.mkdir(parents=True, exist_ok=False)
        initialize_paths(self.paths, make_snapshots=False, lanes=self.lane_names)
        self.git_paths = GitRunPaths(self.paths.root)
        cli_source, storage_source, hook_source = self._source_files()
        baseline = initialize_shadow_repository(
            self.git_paths,
            self.source,
            cli_source=cli_source,
            storage_source=storage_source,
            hook_source=hook_source,
            lanes=cast("tuple[str, ...]", self.lane_names),
        )
        shutil.copy2(self._runner_source(), self._runner_path())
        self._runner_path().chmod(0o755)
        baseline_command = discover_evaluator_command(self.source, objective)
        self.adaptive_paths = AdaptiveEvalPaths(self.paths.root)
        initialize_registry(
            self.adaptive_paths,
            central=self.git_paths.central,
            baseline_command=baseline_command,
            objective=objective,
        )
        self.store = CoordinationStore(self.git_paths.database, self.git_paths.events)
        self.store.initialize(
            run_id=cast("str", self.control["run_id"]),
            git_pr_enabled=True,
            global_knowledge_enabled=False,
            experiment_memory_enabled=False,
            lanes=cast("tuple[str, ...]", self.lane_names),
            allowed_paths=discover_allowed_paths(self.source, objective),
            trusted_evaluator_command=[sys.executable, str(self._runner_path())],
        )
        cast("dict[str, Any]", self.control["git_pr"])["observed_main_sha"] = baseline
        write_branch_protection_context(self.git_paths, self.store)

    def _initialize_mode_paths(self) -> None:
        super()._initialize_mode_paths()
        # The base initializer installs its module-local store class. Reattach this
        # workflow's compatible extension so CI transitions and migrations are present.
        self.store = CoordinationStore(self.git_paths.database, self.git_paths.events)
        self.store.initialize(
            run_id=cast("str", self.control["run_id"]),
            git_pr_enabled=True,
            global_knowledge_enabled=False,
            experiment_memory_enabled=False,
            lanes=cast("tuple[str, ...]", self.lane_names),
            allowed_paths=cast("list[str]", self.store.meta("allowed_paths")),
            trusted_evaluator_command=cast(
                "list[str]", self.store.meta("trusted_evaluator_command")
            ),
        )
        self.adaptive_paths = AdaptiveEvalPaths(self.paths.root)
        if not self._runner_path().exists():
            raise RuntimeError("resumable adaptive gate runner is missing")
        adaptive = cast("dict[str, Any]", self.control["adaptive_eval"])
        self.store.requeue_running_ci()
        if adaptive.get("build_status") == "building":
            adaptive["build_status"] = "not-started"
        queue = cast("list[str]", adaptive["audit_queue"])
        for receipt_id, audit in cast("dict[str, Any]", adaptive["audits"]).items():
            if isinstance(audit, dict) and audit.get("status") == "reviewing":
                audit["status"] = "pending"
                if receipt_id not in queue:
                    queue.append(receipt_id)
        active = current_gate(self.adaptive_paths)
        adaptive["active_gate"] = active
        for pr in self.store.prs():
            receipt_id = pr.get("last_ci_receipt_id")
            if isinstance(receipt_id, str):
                self._queue_audit(receipt_id)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _validate_mode_layout(self) -> None:
        super()._validate_mode_layout()
        self.adaptive_paths = AdaptiveEvalPaths(self.paths.root)
        for path in (
            self._runner_path(),
            self.adaptive_paths.registry,
            self.adaptive_paths.runtime_config,
            self.adaptive_paths.coordinator_inbox,
        ):
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise RuntimeError(f"adaptive evaluation file was replaced: {path}")
        if self._sha256(self._runner_path()) != self._sha256(self._runner_source()):
            raise RuntimeError(
                "run-local adaptive gate runner differs from workflow source"
            )

    def _workspace_map(self) -> dict[str, object]:
        mapping = super()._workspace_map()
        mapping["adaptive_eval"] = {
            "stable_evaluator_command": [sys.executable, str(self._runner_path())],
            "dedicated_ref": GATE_REF,
            "registry": str(self.adaptive_paths.registry),
            "active_gate": (
                current_gate(self.adaptive_paths)
                if self.adaptive_paths.registry.exists()
                else None
            ),
            "publication": "coordinator commit; applies to later CI submissions",
            "ci": "runtime-owned isolated checkout of each frozen submitted revision",
            "merge": "automatic after CI receipt integrity and coordinator audit",
            "failure_visibility": "author lane plus coordinator only",
        }
        git_pr = cast("dict[str, object]", mapping["git_pr"])
        git_pr.update(
            review_order="FIFO after gate audit",
            review_mode="adaptive-gate-plus-coordinator-alignment-audit",
        )
        return mapping

    def _plan(self, objective: str, cwd: Path | None = None) -> InitialPlan:
        prompt = git_planning_prompt(
            objective=objective,
            workspace_map=self._workspace_map(),
            skill=self.skill_name,
        )
        failures: list[str] = []
        for attempt in range(1, 4):
            session = self.agents.coordinator.new(cwd=cwd or self.paths.planning)
            try:
                result = session(prompt, suppress=False, schema=InitialPlan)
            except Stopped:
                raise
            except Exception as why:  # noqa: BLE001
                failures.append(
                    f"attempt {attempt}: {type(why).__name__}: {why}"[:1000]
                )
                result = None
            finally:
                close_safely(session)
            if result is not None:
                return result
            if len(failures) < attempt:
                failures.append(f"attempt {attempt}: no structured plan")
        raise RuntimeError(f"initial adaptive Git/PR plan failed: {failures}")

    def _lane_instructions(self, lane: LaneName) -> str:
        return lane_protocol(
            lane=lane,
            run_root=str(self.paths.root),
            cli=str(self.git_paths.bin / "pfc"),
            evaluator_command=cast(
                "list[str]", self.store.meta("trusted_evaluator_command")
            ),
            allowed_paths=self._allowed_paths(),
        )

    def _lane_ownership(self, lane: LaneName) -> str | None:
        return (
            f"You are {lane}, an independent PR author. Work only in your assigned clone. "
            "The runtime integrates accepted PRs automatically; the coordinator alone owns "
            "the adaptive gate control ref."
        )

    def _gate_ref_sha(self) -> str | None:
        result = subprocess.run(
            ["git", "--git-dir", str(self.git_paths.central), "rev-parse", GATE_REF],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else None

    def _start_gate_build(self) -> bool:
        adaptive = cast("dict[str, Any]", self.control["adaptive_eval"])
        attempts = int(adaptive.get("build_attempts", 0))
        if (
            self.coordinator_work.future is not None
            or adaptive.get("build_status") not in {"not-started", "failed"}
            or attempts >= 3
        ):
            return False
        runtime = json.loads(
            self.adaptive_paths.runtime_config.read_text(encoding="utf-8")
        )
        prompt = gate_build_prompt(
            objective=cast("str", self.control["objective"]),
            workspace_map=self._workspace_map(),
            skill=self.skill_name,
            baseline_command=cast("list[str]", runtime["baseline_command"]),
            gate_ref=GATE_REF,
        )
        session = self.agents.coordinator.new(cwd=self.git_paths.planning)
        adaptive["build_status"] = "building"
        adaptive["build_attempts"] = attempts + 1
        self.coordinator_work = CoordinatorWork(
            kind="build",
            future=self.executor.submit(
                _run_structured, session, prompt, GateBuildResult
            ),
            session=session,
            gate_ref_before=self._gate_ref_sha(),
        )
        self.store.record_telemetry(
            "adaptive_gate_build_started", {"attempt": attempts + 1}
        )
        return True

    def _receipt_artifacts(self, receipt: dict[str, object]) -> tuple[Path, Path]:
        evaluation_root = self.git_paths.evaluation_artifacts.resolve()
        artifacts: list[Path] = []
        for path_field, hash_field in (
            ("stdout_path", "stdout_sha256"),
            ("stderr_path", "stderr_sha256"),
        ):
            path = Path(cast("str", receipt[path_field]))
            resolved = path.resolve(strict=True)
            if (
                not resolved.is_relative_to(evaluation_root)
                or path.is_symlink()
                or not path.is_file()
                or self._sha256(path) != receipt[hash_field]
            ):
                raise ValueError("receipt artifact integrity check failed")
            artifacts.append(path)
        return artifacts[0], artifacts[1]

    def _gate_receipt(
        self, receipt: dict[str, object], *, require_head: str | None = None
    ) -> GateReceipt:
        expected_command = cast(
            "list[str]", self.store.meta("trusted_evaluator_command")
        )
        if json.loads(cast("str", receipt["command_json"])) != expected_command:
            raise ValueError(
                "receipt did not use the frozen adaptive evaluator command"
            )
        if receipt["kind"] != "provisional":
            raise ValueError("adaptive auto-merge requires a provisional lane receipt")
        if require_head is not None and receipt["commit_sha"] != require_head:
            raise ValueError("receipt is not bound to the frozen PR head")
        if require_head is not None:
            tree = cast(
                "subprocess.CompletedProcess[str]",
                subprocess.run(
                    ["git", "rev-parse", f"{require_head}^{{tree}}"],
                    cwd=self.git_paths.central,
                    capture_output=True,
                    text=True,
                    check=True,
                ),
            ).stdout.strip()
            if receipt["tree_sha"] != tree:
                raise ValueError("receipt tree does not match the frozen PR head")
        stdout_path, _stderr_path = self._receipt_artifacts(receipt)
        output = stdout_path.read_text(encoding="utf-8", errors="replace")
        matches = GATE_RECEIPT_PATTERN.findall(output)
        if len(matches) != 1:
            raise ValueError("evaluator output must contain one PFC_GATE_RECEIPT line")
        return GateReceipt.model_validate_json(matches[0])

    def _queue_audit(self, receipt_id: str) -> bool:
        adaptive = cast("dict[str, Any]", self.control["adaptive_eval"])
        audits = cast("dict[str, Any]", adaptive["audits"])
        queue = cast("list[str]", adaptive["audit_queue"])
        if receipt_id in audits or receipt_id in queue:
            return False
        queue.append(receipt_id)
        return True

    def _artifact_excerpt(self, receipt: dict[str, object]) -> dict[str, str]:
        try:
            stdout_path, stderr_path = self._receipt_artifacts(receipt)
            return {
                "stdout": stdout_path.read_text(encoding="utf-8", errors="replace")[
                    -6000:
                ],
                "stderr": stderr_path.read_text(encoding="utf-8", errors="replace")[
                    -6000:
                ],
            }
        except (KeyError, OSError, ValueError) as why:
            return {"integrity_error": f"{type(why).__name__}: {why}"}

    def _start_audit(self) -> bool:
        adaptive = cast("dict[str, Any]", self.control["adaptive_eval"])
        queue = cast("list[str]", adaptive["audit_queue"])
        if self.coordinator_work.future is not None or not queue:
            return False
        receipt_id = queue.pop(0)
        receipt = self.store.receipt(receipt_id)
        pr: dict[str, object] | None = None
        if receipt.get("pr_id"):
            try:
                pr = self.store.pr(cast("str", receipt["pr_id"]))
            except KeyError:
                pr = None
        try:
            normalized = self._gate_receipt(receipt).model_dump(mode="json")
        except (KeyError, OSError, ValueError) as why:
            normalized = {
                "decision": "invalid",
                "alignment_status": "misaligned",
                "reason": f"{type(why).__name__}: {why}",
            }
        prompt = gate_review_prompt(
            objective=cast("str", self.control["objective"]),
            workspace_map=self._workspace_map(),
            skill=self.skill_name,
            receipt=receipt,
            gate_receipt=normalized,
            pr=pr,
            current_gate=current_gate(self.adaptive_paths),
            artifact_excerpt=self._artifact_excerpt(receipt),
            gate_ref=GATE_REF,
        )
        session = self.agents.coordinator.new(cwd=self.git_paths.planning)
        self.coordinator_work = CoordinatorWork(
            kind="review",
            future=self.executor.submit(
                _run_structured, session, prompt, GateReviewResult
            ),
            session=session,
            receipt_id=receipt_id,
            gate_ref_before=self._gate_ref_sha(),
        )
        cast("dict[str, Any]", adaptive["audits"])[receipt_id] = {"status": "reviewing"}
        self.store.record_telemetry(
            "adaptive_receipt_audit_started",
            {"receipt_id": receipt_id, "pr_id": receipt.get("pr_id")},
            lane=cast("str", receipt["lane"]),
        )
        return True

    def _append_coordinator_inbox(self, payload: dict[str, object]) -> None:
        with self.adaptive_paths.coordinator_inbox.open(
            "a", encoding="utf-8"
        ) as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    def _collect_coordinator_work(self) -> bool:
        work = self.coordinator_work
        if work.future is None or not work.future.done():
            return False
        result: GateBuildResult | GateReviewResult | None = None
        error: str | None = None
        try:
            result = work.future.result()
        except Stopped:
            raise
        except Exception as why:  # noqa: BLE001
            error = f"{type(why).__name__}: {why}"[:2000]
        close_safely(work.session)
        self.coordinator_work = CoordinatorWork()
        adaptive = cast("dict[str, Any]", self.control["adaptive_eval"])

        if work.kind == "build":
            if not isinstance(result, GateBuildResult):
                adaptive["build_status"] = "failed"
                self.store.record_telemetry(
                    "adaptive_gate_build_failed", {"error": error or "missing result"}
                )
                return True
            if result.status == "deferred":
                adaptive["build_status"] = "deferred"
                self.store.record_telemetry(
                    "adaptive_gate_build_deferred", {"summary": result.summary}
                )
                return True
            try:
                record = activate_gate(
                    self.adaptive_paths,
                    repository=self.git_paths.central,
                    commit_sha=cast("str", result.gate_commit),
                    reason=result.summary,
                )
            except (OSError, ValueError, subprocess.SubprocessError) as why:
                adaptive["build_status"] = "failed"
                self.store.record_telemetry(
                    "adaptive_gate_build_failed",
                    {"error": f"gate activation rejected: {why}"[:2000]},
                )
                return True
            adaptive["build_status"] = "published"
            adaptive["active_gate"] = result.gate_commit
            self.store.record_telemetry("adaptive_gate_activated", record)
            return True

        receipt_id = cast("str", work.receipt_id)
        receipt = self.store.receipt(receipt_id)
        audits = cast("dict[str, Any]", adaptive["audits"])
        if not isinstance(result, GateReviewResult) or result.receipt_id != receipt_id:
            summary = error or "coordinator returned no matching audit"
            audits[receipt_id] = {
                "status": "insufficient",
                "summary": summary,
            }
            self._append_coordinator_inbox(
                {
                    "receipt_id": receipt_id,
                    "status": "insufficient",
                    "error": error or "missing/mismatched result",
                }
            )
            self._emit_system(
                targets=(cast("LaneName", receipt["lane"]),),
                kind="private_adaptive_gate_feedback",
                summary=summary,
                payload={
                    "receipt_id": receipt_id,
                    "pr_id": receipt.get("pr_id"),
                    "status": "insufficient",
                },
            )
            return True

        audit = result.model_dump(mode="json")
        audit["status"] = {
            "aligned_accept": "accept",
            "aligned_reject": "reject",
            "repair_published": "repair",
            "insufficient": "insufficient",
        }[result.verdict]
        if result.verdict == "repair_published":
            try:
                record = activate_gate(
                    self.adaptive_paths,
                    repository=self.git_paths.central,
                    commit_sha=cast("str", result.replacement_gate_commit),
                    reason=result.summary,
                )
            except (OSError, ValueError, subprocess.SubprocessError) as why:
                audit["status"] = "insufficient"
                audit["summary"] = f"replacement gate failed activation: {why}"[:2000]
            else:
                adaptive["active_gate"] = result.replacement_gate_commit
                adaptive["build_status"] = "published"
                self.store.record_telemetry("adaptive_gate_repaired", record)
        elif self._gate_ref_sha() != work.gate_ref_before:
            self.store.record_telemetry(
                "unaligned_gate_ref_change_ignored",
                {
                    "receipt_id": receipt_id,
                    "verdict": result.verdict,
                    "active_gate_unchanged": adaptive.get("active_gate"),
                },
            )
        audits[receipt_id] = audit
        inbox = {
            "receipt_id": receipt_id,
            "pr_id": receipt.get("pr_id"),
            "lane": receipt["lane"],
            "verdict": result.verdict,
            "alignment_status": result.alignment_status,
            "summary": audit["summary"],
        }
        self._append_coordinator_inbox(inbox)
        self.store.record_telemetry(
            "adaptive_receipt_audit_completed",
            inbox,
            lane=cast("str", receipt["lane"]),
        )
        if audit["status"] != "accept":
            self._emit_system(
                targets=(cast("LaneName", receipt["lane"]),),
                kind="private_adaptive_gate_feedback",
                summary=cast("str", audit["summary"]),
                payload={
                    "receipt_id": receipt_id,
                    "pr_id": receipt.get("pr_id"),
                    "status": audit["status"],
                    "replacement_gate": result.replacement_gate_commit,
                },
            )
        return True

    def _scan_receipts(self) -> bool:
        """Advance the receipt cursor and recover audits for CI-authoritative receipts."""
        git_state = cast("dict[str, Any]", self.control["git_pr"])
        cursor = int(git_state.get("receipt_cursor", 0))
        receipts, end = self.store.receipts_after(cursor)
        changed = False
        for receipt in receipts:
            if (
                receipt["kind"] != "provisional"
                or receipt["lane"] not in self.lane_names
            ):
                continue
            receipt_id = cast("str", receipt["id"])
            try:
                pr = self.store.pr(cast("str", receipt["pr_id"]))
            except (KeyError, TypeError):
                continue
            if pr.get("last_ci_receipt_id") != receipt_id:
                continue
            changed = self._queue_audit(receipt_id) or changed
        git_state["receipt_cursor"] = end
        return changed or end != cursor

    def _receipt_gate_is_eligible(
        self, pr: dict[str, object], normalized: GateReceipt
    ) -> None:
        active = current_gate(self.adaptive_paths)
        if normalized.mode == "adaptive":
            if normalized.gate_commit != active:
                raise ValueError(
                    "gate changed; re-evaluate this PR under the current gate"
                )
            return
        if active is None:
            return
        registry = json.loads(self.adaptive_paths.registry.read_text(encoding="utf-8"))
        history = cast("list[dict[str, object]]", registry["history"])
        first_activation = cast("str", history[0]["activated_at"])
        submitted_at = cast("str", pr.get("ci_submitted_at") or pr["created_at"])
        if submitted_at >= first_activation:
            raise ValueError(
                "this CI attempt was submitted after gate activation and needs a gated receipt"
            )

    def _ci_workspace(self, pr: dict[str, object]) -> Path:
        root = self.git_paths.shared / "ci-workspaces"
        root.mkdir(parents=True, exist_ok=True)
        workspace = root / f"{pr['id']}-attempt-{pr['ci_attempt']}"
        if workspace.is_symlink():
            raise RuntimeError(f"refusing linked CI workspace: {workspace}")
        if workspace.exists():
            shutil.rmtree(workspace)
        subprocess.run(
            [
                "git",
                "clone",
                "--quiet",
                "--no-hardlinks",
                str(self.git_paths.central),
                str(workspace),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "checkout", "--quiet", "--detach", cast("str", pr["head_sha"])],
            cwd=workspace,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=True,
        )
        return workspace

    @staticmethod
    def _ci_receipt_id(output: str) -> str | None:
        for line in reversed(output.splitlines()):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and isinstance(value.get("receipt_id"), str):
                return cast("str", value["receipt_id"])
        return None

    def _private_ci_failure(
        self, pr: dict[str, object], reason: str, *, receipt_id: str | None
    ) -> None:
        payload = {
            "pr_id": pr["id"],
            "head_sha": pr["head_sha"],
            "ci_attempt": pr["ci_attempt"],
            "receipt_id": receipt_id,
            "status": "draft",
        }
        self._emit_system(
            targets=(cast("LaneName", pr["lane"]),),
            kind="private_ci_failed",
            summary=(
                f"CI returned {pr['id']} to draft: {reason[:1800]} "
                "Modify the same branch, push it, and resubmit the same PR."
            ),
            payload=payload,
        )
        self._append_coordinator_inbox(
            {
                **payload,
                "lane": pr["lane"],
                "status": "ci-failed-awaiting-audit" if receipt_id else "ci-failed",
                "reason": reason[:2000],
            }
        )

    def _start_ci(self) -> bool:
        if self.ci_work.future is not None:
            return False
        pr = self.store.claim_next_ci()
        if pr is None:
            return False
        try:
            workspace = self._ci_workspace(pr)
        except (OSError, RuntimeError, subprocess.SubprocessError) as why:
            reason = f"CI workspace setup failed: {type(why).__name__}: {why}"
            self.store.complete_ci(
                pr_id=cast("str", pr["id"]),
                passed=False,
                receipt_id=None,
                reason=reason,
            )
            ci = cast(
                "dict[str, int]",
                cast("dict[str, Any]", self.control["adaptive_eval"])["ci"],
            )
            ci["completed"] += 1
            ci["failed"] += 1
            self._private_ci_failure(pr, reason, receipt_id=None)
            return True
        command = [
            str(self.git_paths.bin / "pfc"),
            "--run-root",
            str(self.paths.root),
            "--lane",
            cast("str", pr["lane"]),
            "evaluate",
            "--pr",
            cast("str", pr["id"]),
            "--",
            *cast("list[str]", self.store.meta("trusted_evaluator_command")),
        ]
        self.ci_work = CIWork(
            future=self.executor.submit(_run_ci_command, command, workspace),
            pr=pr,
            workspace=workspace,
        )
        self.store.record_telemetry(
            "adaptive_ci_worker_started",
            {
                "pr_id": pr["id"],
                "head_sha": pr["head_sha"],
                "attempt": pr["ci_attempt"],
            },
            lane=cast("str", pr["lane"]),
        )
        return True

    def _collect_ci(self) -> bool:
        work = self.ci_work
        if work.future is None or not work.future.done():
            return False
        pr = cast("dict[str, object]", work.pr)
        execution: CIExecution | None = None
        worker_error: str | None = None
        try:
            execution = work.future.result()
        except Exception as why:  # noqa: BLE001 - return infrastructure failures to lane
            worker_error = f"{type(why).__name__}: {why}"[:2000]
        self.ci_work = CIWork()

        receipt_id = self._ci_receipt_id(execution.stdout) if execution else None
        normalized: GateReceipt | None = None
        reason = worker_error or "CI did not produce a receipt."
        passed = False
        if receipt_id is not None:
            try:
                receipt = self.store.receipt(receipt_id)
                normalized = self._gate_receipt(
                    receipt, require_head=cast("str", pr["head_sha"])
                )
                observed_main = cast(
                    "str",
                    cast("dict[str, Any]", self.control["git_pr"])["observed_main_sha"],
                )
                if execution is None or execution.returncode != 0:
                    raise ValueError(normalized.reason)
                if normalized.decision != "accept":
                    raise ValueError(normalized.reason)
                if normalized.incumbent_sha != observed_main:
                    raise ValueError("main changed during CI; rebase and resubmit")
                if pr["base_sha"] != observed_main:
                    raise ValueError(
                        "PR is not based on current main; rebase and resubmit"
                    )
                self._receipt_gate_is_eligible(pr, normalized)
            except (KeyError, OSError, ValueError) as why:
                reason = str(why)
            else:
                passed = True
                reason = normalized.reason
        elif execution is not None:
            reason = (
                f"CI command exited {execution.returncode} without a receipt. "
                f"{execution.stderr[-1200:]}"
            ).strip()

        try:
            self.store.complete_ci(
                pr_id=cast("str", pr["id"]),
                passed=passed,
                receipt_id=receipt_id,
                reason=reason,
            )
        except ValueError as why:
            passed = False
            reason = f"CI receipt binding failed: {why}"
            receipt_id = None
            self.store.complete_ci(
                pr_id=cast("str", pr["id"]),
                passed=False,
                receipt_id=None,
                reason=reason,
            )

        ci = cast(
            "dict[str, int]",
            cast("dict[str, Any]", self.control["adaptive_eval"])["ci"],
        )
        ci["completed"] += 1
        ci["passed" if passed else "failed"] += 1
        if receipt_id is not None:
            self._queue_audit(receipt_id)
        self.store.record_telemetry(
            "adaptive_ci_worker_completed",
            {
                "pr_id": pr["id"],
                "head_sha": pr["head_sha"],
                "attempt": pr["ci_attempt"],
                "passed": passed,
                "receipt_id": receipt_id,
                "reason": reason[:2000],
            },
            lane=cast("str", pr["lane"]),
        )
        if not passed:
            self._private_ci_failure(pr, reason, receipt_id=receipt_id)
        if work.workspace is not None:
            shutil.rmtree(work.workspace, ignore_errors=True)
        return True

    def _reject_fast_path(self, pr: dict[str, object], reason: str) -> None:
        returned = self.store.return_pr_to_lane(
            pr_id=cast("str", pr["id"]), reason=reason
        )
        self._emit_system(
            targets=(cast("LaneName", returned["lane"]),),
            kind="private_pr_returned_by_gate",
            summary=(
                f"{reason} The PR is draft again: modify the same branch, push, "
                "and resubmit the same PR to CI."
            ),
            payload={"pr_id": returned["id"], "status": "draft"},
        )

    def _process_fast_path(self) -> bool:
        """FIFO automatic merge after exact receipt and coordinator audit acceptance."""
        active = self.store.active_review()
        if active is None:
            ready = self.store.prs(status="ready")
            if not ready:
                return False
            candidate = ready[0]
        else:
            candidate = active
        receipt_id = cast("str", candidate["provisional_receipt_id"])
        receipt = self.store.receipt(receipt_id)
        try:
            normalized = self._gate_receipt(
                receipt, require_head=cast("str", candidate["head_sha"])
            )
            if int(receipt["exit_code"]) != 0 or normalized.decision != "accept":
                raise ValueError(f"gate did not accept candidate: {normalized.reason}")
            if normalized.incumbent_sha != cast(
                "str",
                cast("dict[str, Any]", self.control["git_pr"])["observed_main_sha"],
            ):
                raise ValueError(
                    "main changed after evaluation; rebase and re-evaluate"
                )
            self._receipt_gate_is_eligible(candidate, normalized)
        except (KeyError, OSError, ValueError) as why:
            selected = active or self.store.activate_pr(cast("str", candidate["id"]))
            if selected is not None:
                self._reject_fast_path(
                    selected, f"Adaptive gate rejected the PR: {why}"
                )
            return True

        audits = cast(
            "dict[str, Any]",
            cast("dict[str, Any]", self.control["adaptive_eval"])["audits"],
        )
        audit = audits.get(receipt_id)
        if not isinstance(audit, dict) or audit.get("status") == "reviewing":
            self._queue_audit(receipt_id)
            return False
        if audit.get("status") != "accept":
            selected = active or self.store.activate_pr(cast("str", candidate["id"]))
            if selected is not None:
                self._reject_fast_path(
                    selected,
                    f"Coordinator gate audit blocked the PR: {audit.get('summary', 'insufficient evidence')}",
                )
            return True

        selected = active or self.store.activate_pr(cast("str", candidate["id"]))
        if selected is None:
            return False
        git_state = cast("dict[str, Any]", self.control["git_pr"])
        prior = cast("str", git_state["observed_main_sha"])
        validate_changed_paths(
            self.git_paths.central,
            prior,
            cast("str", selected["head_sha"]),
            self._allowed_paths(),
        )
        comparison: dict[str, object] = {
            "pr_id": selected["id"],
            "review_mode": "adaptive_gate_auto_merge",
            "score": normalized.candidate_score,
            "prior_score": normalized.incumbent_score,
            "metric": normalized.metric,
            "direction": normalized.direction,
            "improvement": normalized.improvement,
            "gate_commit": normalized.gate_commit,
            "receipt_id": receipt_id,
            "summary": audit.get("summary", normalized.reason),
            "evidence": [receipt_id, *audit.get("evidence", [])],
        }
        try:
            merge_sha = create_fast_path_merge(
                self.git_paths,
                pr_id=cast("str", selected["id"]),
                prior_sha=prior,
                head_sha=cast("str", selected["head_sha"]),
            )
        except (RuntimeError, subprocess.CalledProcessError) as why:
            self._reject_fast_path(
                selected, f"Deterministic integration failed: {str(why)[:1000]}"
            )
            return True
        git_state["pending_comparison"] = comparison
        self.store.record_telemetry(
            "adaptive_pr_auto_merged",
            {
                "pr_id": selected["id"],
                "merge_sha": merge_sha,
                "receipt_id": receipt_id,
                "gate_commit": normalized.gate_commit,
            },
            lane=cast("str", selected["lane"]),
        )
        return True

    def _manifest_fields(self) -> dict[str, object]:
        fields = super()._manifest_fields()
        fields["adaptive_eval"] = {
            **json_copy(self.control.get("adaptive_eval", {})),
            "registry": str(self.adaptive_paths.registry),
            "coordinator_inbox": str(self.adaptive_paths.coordinator_inbox),
        }
        return fields

    def _control_cycle(self) -> None:
        changed = self._collect_coordinator_work()
        changed = self._collect_ci() or changed
        changed = self._scan_receipts() or changed
        changed = self._observe_main() or changed
        changed = self._process_fast_path() or changed
        changed = self._observe_main() or changed
        changed = self._start_gate_build() or changed
        changed = self._start_audit() or changed
        changed = self._start_ci() or changed
        if changed:
            self._persist()

    def _close_sessions(self) -> None:
        super()._close_sessions()
        close_safely(self.coordinator_work.session)
        if self.ci_work.future is not None:
            self.ci_work.future.cancel()


def execute(
    agents: Any,
    task: str,
    config: Any,
    state: dict[str, Any] | None,
    *,
    _clock: Callable[[], dt.datetime] | None = None,
    _sleep: Callable[[float], None] = time.sleep,
    _max_turns: int | None = None,
) -> None:
    """Execute one resumable adaptive-gate Git/PR run."""
    AdaptiveEvalGitPRRuntime(
        agents,
        task,
        config,
        state,
        clock=_clock,
        sleeper=_sleep,
        max_turns=_max_turns,
    ).run()


__all__ = ["AdaptiveEvalGitPRRuntime", "execute"]
