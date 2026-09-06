---
name: adaptive-pr-eval-coordinator
description: Coordinate independent Git/PR lanes while designing, auditing, and repairing a task-specific proxy evaluation gate committed on a dedicated control ref.
---

# Adaptive PR Eval Coordinator

Use the role in the runtime prompt as authority. The planning role dispatches three distinct lanes
only. Lanes begin immediately and never wait for gate construction. The later coordinator role
owns gate design and receipt audits; lanes must not modify `.pfc/adaptive-eval/**` or the gate ref.

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
`refs/heads/pfc/eval-gate` ref. The runtime validates and activates the exact commit. The stable
lane command uses the baseline evaluator until activation; only PRs opened after activation use
the new gate. Do not block lane dispatch while constructing it.

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
self-test, make a new linear gate commit, and return that commit. The current PR must be rejected;
submit a new PR and evaluate it under the replacement. Never reuse an old receipt. If evidence is
insufficient, block merge without inventing certainty.

Failed evaluation and rejected-PR details are private to the authoring lane and coordinator audit
trail. Do not publish them to all lanes or turn them into shared-success knowledge.

## Alignment claim boundary

In blind evaluation, “aligned” means structurally aligned with the authoritative task definition,
metric, validity rules, and data-generating protocol. It does not mean empirically correlated with
unseen hidden scores. If hidden results later become available, analyze proxy/hidden rank agreement
offline with untouched historical receipts; never tune the live gate on those outcomes.

This flow does not authorize deployment, release, competition submission, purchase, messaging, or
other external actions.
