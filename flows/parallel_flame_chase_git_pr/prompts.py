"""Self-contained lane, PR-review, and knowledge-review instructions."""

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
    git_pr_enabled: bool,
    global_knowledge_enabled: bool,
    allowed_paths: list[str],
    knowledge_digest: list[dict[str, object]],
) -> str:
    """Explain the additive protocol without changing LaneReport."""
    prefix = f"{shlex.quote(cli)} --run-root {shlex.quote(run_root)} --lane {lane}"
    sections: list[str] = []
    if git_pr_enabled:
        sections.append(
            f"""This lane owns exactly this writable Git clone. Code/lightweight results belong
in Git; large evaluator outputs belong in the external object store. Start each new experiment
from the latest `origin/main` on a branch named `{lane}/<experiment>`. A newer ready candidate
supersedes your older queued candidate. Never rewrite a ready PR head. Commit, push the exact
branch, and use:

  {prefix} evaluate -- <official task evaluator command>
  {prefix} pr open --draft --title "..." --hypothesis "..."
  {prefix} pr ready PRxxxxxx --receipt R...

`evaluate` is a recorder, not an evaluator: it preserves the external command's result and only
a successful receipt from the exact official command, bound to a clean pushed head/tree, qualifies
a PR. The runtime prioritizes the lowest `CYCLES` value and publishes that exact tested tree only
when it improves official main; there is no second model review or second evaluator run. Git
metadata mentioned in LaneReport is informational; the PR registry and receipts are authoritative.
The frozen allowed path patterns are {document(allowed_paths)}; `.git`, `.flowbench`, and `.pfc`
are always protected."""
        )
    if global_knowledge_enabled:
        sections.append(
            f"""The global knowledge mechanism is a compact run-local digest, not conversational
memory. It retains at most 12 evaluator-backed shared-best cards and injects at most four recent
cards per turn. Query it with `{prefix} knowledge search "<terms>"` only when useful; inspect a
relevant entry with `knowledge get <ID>` and cite any E... ID you rely on. Ordinary progress and
failed ideas remain in the Report Share archive and do not trigger a separate reviewer.

Current compact digest:
{document(knowledge_digest)}"""
        )
    if not sections:
        return "This factorial cell uses the unchanged Report Share protocol."
    return "\n\n".join(sections)


def git_planning_prompt(
    *, objective: str, workspace_map: dict[str, object], skill: str
) -> str:
    """Plan diverse experiments when every lane has equal PR capability."""
    return f"""You are the planning orchestrateor for a Git/PR parallel Flame Chase.

Read the repository and mounted `{skill}` skill. Plan only: do not edit files or execute remote
actions. Split the objective into exactly three materially different, falsifiable experiment
lanes. All three lanes have isolated clones, equal ability to test, and equal ability to propose
one frozen ready PR at a time. There is no privileged integration lane: later integration is a
deterministic receipt-verified best-score fast path. Favor complementary information gain over
cosmetic variants, and make every lane independently useful. This is the only planning turn.

Objective:
{objective}

Workspace map:
{document(workspace_map)}

Return only the structured InitialPlan requested by the runtime.
"""


def pr_review_prompt(
    *,
    objective: str,
    pr: dict[str, object],
    prior_main_sha: str,
    cli: str,
    allowed_paths: list[str],
    ledger: list[dict[str, object]],
) -> str:
    """Give a trusted coordinator one exact FIFO PR and native Git protocol."""
    pr_id = pr["id"]
    return f"""You are the trusted orchestrateor reviewing the single active FIFO PR `{pr_id}`.
This is a fresh review session in a run-owned review worktree. You have final scientific judgment:
merge only if the candidate is a real improvement for the objective, but the runtime deliberately
does not impose a numeric score gate. Inspect code and evidence, reproduce proportionate checks,
and repair the integration yourself when useful. You may iterate without a fixed repair limit.
Do not perform deployment, release, competition submission, purchase, messaging, or other remote
actions.

Objective:
{objective}

Frozen PR record:
{document(pr)}

Current authoritative main: {prior_main_sha}
Allowed task paths: {document(allowed_paths)}
Historical official ledger (later reviews may reuse sound historical-main evidence):
{document(ledger)}

Use native Git. Fetch origin, reset/switch a local `review/{pr_id}` branch to `origin/main`, then
merge the exact frozen head `{pr["head_sha"]}` with `--no-ff --no-commit`. Resolve conflicts and
make any integration repairs in that merge tree. The final commit must have exactly two parents:
first `{prior_main_sha}`, second `{pr["head_sha"]}`. Put this trailer on the merge commit exactly:

PFC-PR: {pr_id}

The first review should lazily evaluate both the historical main and candidate when that is needed
to establish improvement; later reviews may reuse valid ledger evidence. Every qualifying staging
evaluation must be recorded on a clean commit with:

  {shlex.quote(cli)} evaluate --pr {pr_id} -- <official task evaluator command>

The recorder is not the evaluator. A failed receipt does not qualify. If you repair after an
evaluation, amend the same merge commit and record a fresh receipt for the new commit. Once a
successful staging receipt exists and your judgment is positive, push `HEAD:main`; server-side
protection verifies FIFO identity, both parents, frozen head, allowed paths, trailer, and receipt.
If the proposal should not merge, explicitly run:

  {shlex.quote(cli)} pr reject {pr_id} --reason "<evidence-backed reason>"

Return only PRReviewResult. `merged` must name the actually pushed main commit and its receipt IDs;
`rejected` must follow the explicit reject command. Use `continue` only when durable review work
remains and neither transition was completed.
"""


def knowledge_review_prompt(
    *,
    objective: str,
    reports: list[dict[str, object]],
    visible_knowledge: dict[str, object],
    pending_experiences: list[dict[str, object]],
) -> str:
    """Ask an independent reviewer to accumulate only durable cognition."""
    report_ids = [item["report_id"] for item in reports]
    return f"""You are the independent knowledge reviewer for a long-horizon MLE run. Do not edit
the repository. Review the exact immutable batch below and return only KnowledgeReview.

Objective:
{objective}

Exact report IDs (return this same list, in this order):
{document(report_ids)}

New reports:
{document(reports)}

Currently visible verified facts and accepted success experiences:
{document(visible_knowledge)}

Success skeletons awaiting enrichment:
{document(pending_experiences)}

The fact graph is for atomic, important conclusions that prevent repeated exploration—not a diary.
Add a fact only with an explicit proof, evidence references, dependency IDs, and narrow scope. A
claim from an unmerged branch is allowed only when its scope makes that limitation unmistakable.
Name known contradictions so both claims are quarantined. Mark facts stale when their scope/evidence
no longer applies; revoke facts only when invalidity is established (dependents are revoked by the
runtime). Do not turn failures, dead ends, ordinary progress events, guesses, or coordinator
decisions into facts. Success ExperienceCards are separate: enrich only listed accepted skeletons
with method, why it worked, and limitations. It is valid to produce no facts or updates.
"""


__all__ = [
    "git_planning_prompt",
    "knowledge_review_prompt",
    "lane_protocol",
    "pr_review_prompt",
]
