---
name: adaptive-pr-eval-coordinator
description: Coordinate independent Git/PR lanes while designing, auditing, and repairing a task-specific CI proxy-evaluation gate committed on a dedicated control ref.
---

# Adaptive PR Eval Coordinator

Use the role in the runtime prompt as authority. The planning role dispatches three distinct lanes
only. Lanes begin immediately and never wait for gate construction. The later coordinator role
owns CI gate design and receipt audits; lanes must not modify `.pfc/adaptive-eval/**` or the gate
ref.

Before designing or reviewing a gate, read [the gate contract](references/gate-contract.md).

## Coordinator: construct the first gate

Identify the real deliverable, official validity requirements, metric name and direction, and the
data protocol that determines generalization. Classify the task before choosing the proxy:

- Tabular/classification/regression: honor groups, time order, stratification, target semantics,
  and official preprocessing; use repeated or nested validation when cheap enough.
- Ranking/recommendation: preserve query/user grouping and ranking cutoff; never split individual
  rows across train/validation when entities leak.
- Forecasting: use strictly forward temporal validation and realistic horizons.
- Vision/audio/text: split by source/entity, detect duplicates, and use the official metric rather
  than generic accuracy when they differ.
- Optimization/programming: use deterministic validity checks, adversarial cases, invariant/property
  tests, and the official resource or quality objective.

Prefer a cheap proxy that preserves candidate ordering over an elaborate surrogate. Compare both
candidate and current main under the same code, seeds, folds, inputs, and budget. Require a real
improvement margin plus at least one task-relevant leakage/robustness check. Treat format validation
as necessary but never as evidence of model quality.

Write the bundle under `.pfc/adaptive-eval/`, self-test it, commit it, and push the dedicated
`refs/heads/pfc/eval-gate` ref. The runtime validates and activates the exact commit. This is a CI
check in the existing run-local Git/PR mechanism: after `pfc pr submit`, the runtime checks out the
frozen pushed revision from the central bare repository and invokes the stable evaluator itself.
Lanes do not run the qualifying check and cannot mark their own PR ready. A submission attempt
uses the gate active at its submission time; attempts before first activation use the baseline
evaluator. Do not block lane dispatch while constructing the gate.

## Coordinator: audit every submission

Review the immutable receipt and, when present, the frozen PR diff. Check separately:

1. Candidate integrity: correct output, no target/test leakage, no hard-coded answers, no metric
   spoofing, no evaluator tampering, and no candidate-only evaluation path.
2. Gate alignment: same optimization direction and materially equivalent population, split,
   horizon/grouping, preprocessing, weighting, and validity semantics as the real evaluator.
3. Robustness: the conclusion survives the gate's declared seeds/folds/perturbations and is not a
   one-split or one-seed accident.

If aligned, do not change the gate. Return the aligned accept/reject result and let the runtime
perform deterministic automatic integration. If misaligned or gameable, repair the gate, run its
self-test, make a new linear gate commit, and return that commit. The current PR returns to draft;
its author modifies or rebases the same branch, pushes it, and resubmits the same PR under the
replacement gate. Never reuse an old receipt. If evidence is insufficient, return the PR to its
author without inventing certainty.

CI and review states freeze the registered branch head. A failed check or audit sends private
evidence only to the author lane and coordinator, then unlocks the PR as `draft`. Do not tell a
lane to open a replacement PR solely because a gate attempt failed.

Failed evaluation and rejected-PR details are private to the authoring lane and coordinator audit
trail. Do not publish them to all lanes or turn them into shared-success knowledge.

## Alignment claim boundary

In blind evaluation, “aligned” means structurally aligned with the authoritative task definition,
metric, validity rules, and data-generating protocol. It does not mean empirically correlated with
unseen hidden scores. If hidden results later become available, analyze proxy/hidden rank agreement
offline with untouched historical receipts; never tune the live gate on those outcomes.

This flow does not authorize deployment, release, competition submission, purchase, messaging, or
other external actions.
