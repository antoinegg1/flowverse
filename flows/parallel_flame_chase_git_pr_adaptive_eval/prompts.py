"""Lane, gate-construction, and per-submission audit instructions."""

from __future__ import annotations

import json
import shlex


def document(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def lane_protocol(
    *,
    lane: str,
    run_root: str,
    cli: str,
    evaluator_command: list[str],
    allowed_paths: list[str],
) -> str:
    """Explain the adaptive receipt/PR protocol to one independent lane."""
    prefix = f"{shlex.quote(cli)} --run-root {shlex.quote(run_root)} --lane {lane}"
    evaluator = " ".join(shlex.quote(item) for item in evaluator_command)
    return f"""This lane owns exactly this writable Git clone. Start each experiment from the
latest `origin/main` on `{lane}/<experiment>`. You may submit freely; no lane is a privileged
integrator. Commit and push the exact branch, then open the draft before evaluating so the gate
receipt is bound to the submission:

  {prefix} pr open --draft --title "..." --hypothesis "..."
  {prefix} evaluate --pr PRxxxxxx -- {evaluator}
  {prefix} pr ready PRxxxxxx --receipt R...

The stable command dispatches to whichever coordinator-authored gate was active when the PR was
opened. A PR opened before the first gate commit uses the task's baseline evaluator. A successful
receipt is still audited by the coordinator for evaluator/real-objective alignment, leakage,
overfitting, and wrong-PR behavior; passing audits merge automatically as the exact tested tree.
If main or the gate changes, rebase and obtain a fresh receipt. Do not rewrite a ready PR head.

Gate failures and PR rejections are private feedback: only this lane and the coordinator receive
them. Use the diagnostic to repair the candidate; do not broadcast failed details as shared
knowledge. The frozen task path patterns are {document(allowed_paths)}. `.git`, `.flowbench`, and
`.pfc` are protected from lane PRs. Never edit or push the coordinator gate ref."""


def git_planning_prompt(
    *, objective: str, workspace_map: dict[str, object], skill: str
) -> str:
    """Dispatch lanes first; gate construction is a separate concurrent session."""
    return f"""You are the planning coordinator for an adaptive-gate Git/PR Flame Chase.

Read the repository and mounted `{skill}` skill. In this turn only dispatch exactly three
materially different, falsifiable experiment lanes. Do not edit files or build the gate yet: the
runtime starts a separate gate-construction session immediately after dispatch, concurrently with
the lanes. All lanes have isolated clones and equal submission rights.

Objective:
{objective}

Workspace map:
{document(workspace_map)}

Return only the structured InitialPlan requested by the runtime.
"""


def gate_build_prompt(
    *,
    objective: str,
    workspace_map: dict[str, object],
    skill: str,
    baseline_command: list[str],
    gate_ref: str,
) -> str:
    """Ask the coordinator to build and commit the first task-specific gate."""
    return f"""You are the coordinator's post-dispatch gate-construction session.

Read the mounted `{skill}` skill completely, including its gate contract reference. The three
lanes have already been dispatched and must not wait for you. Inspect the task, data protocol,
official metric definition, baseline evaluator, and likely leakage/overfitting modes. Design the
smallest useful PR proxy evaluator for this task type. It must compare candidate versus current
main, preserve the official metric direction and validation protocol, include robustness/leakage
checks, and emit the exact structured result in the skill contract.

Write only `.pfc/adaptive-eval/**` in this planning clone. Run its self-test. Commit it as a
one-parent commit and push `HEAD:{gate_ref}`. For the first gate, branch from `origin/main`; for a
later repair, branch from `origin/pfc/eval-gate`. Return `published` only with the full pushed
commit SHA. If evidence cannot justify semantic alignment, return `deferred` and do not publish a
pretend-aligned gate. Hidden evaluator scores are unavailable online: never claim measured hidden
correlation.

Objective:
{objective}

Baseline evaluator command used until publication:
{document(baseline_command)}

Workspace map:
{document(workspace_map)}

Return only GateBuildResult.
"""


def gate_review_prompt(
    *,
    objective: str,
    workspace_map: dict[str, object],
    skill: str,
    receipt: dict[str, object],
    gate_receipt: dict[str, object] | None,
    pr: dict[str, object] | None,
    current_gate: str | None,
    artifact_excerpt: dict[str, str],
    gate_ref: str,
) -> str:
    """Audit one submission without asking the coordinator to integrate it."""
    return f"""You are the coordinator auditing exactly one submitted evaluator receipt.

Read the mounted `{skill}` skill and inspect the candidate diff when a PR is present. Decide two
things independently: (1) whether the candidate is valid and avoids obvious leakage, hard-coding,
wrong-artifact, or proxy gaming; and (2) whether the active gate remains semantically aligned with
the real task definition, official metric direction, and data split/group/time protocol.

If the gate is aligned, do not edit it. Return `aligned_accept` only for a successful gate receipt
and a sound PR; otherwise return `aligned_reject`. If the gate itself is misaligned or has become
gameable, repair `.pfc/adaptive-eval/**`, run its self-test, commit linearly on the existing gate
ref, push `HEAD:{gate_ref}`, and return `repair_published`. The runtime will reject the current PR
and require a new receipt under the replacement gate; never retroactively bless it. Use
`insufficient` if alignment cannot honestly be determined, which also prevents merge.

Do not merge, publish main, alter lane branches, or communicate with other lanes. Rejection and
failed-eval details are visible only to the submitting lane and this coordinator audit trail.
Never use posthoc hidden scores to tune a live gate. If hidden results later become available,
they may be used only for an explicitly separated offline alignment audit.

Objective:
{objective}

Current active gate: {current_gate or "none (baseline evaluator)"}

Receipt record:
{document(receipt)}

Normalized gate result:
{document(gate_receipt)}

PR record:
{document(pr)}

Bounded stdout/stderr excerpts:
{document(artifact_excerpt)}

Workspace map:
{document(workspace_map)}

Return only GateReviewResult for receipt `{receipt["id"]}`.
"""


__all__ = [
    "document",
    "gate_build_prompt",
    "gate_review_prompt",
    "git_planning_prompt",
    "lane_protocol",
]
