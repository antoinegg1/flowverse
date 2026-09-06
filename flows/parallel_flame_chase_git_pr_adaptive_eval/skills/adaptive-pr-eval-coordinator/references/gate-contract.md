# Committed gate contract

The dedicated ref is `refs/heads/pfc/eval-gate`. Every update is a one-parent linear commit whose
diff is limited to `.pfc/adaptive-eval/**`. Each update must change `manifest.json`.

## Required bundle

`.pfc/adaptive-eval/manifest.json` follows this shape:

```json
{
  "schema_version": 1,
  "task_type": "tabular binary classification",
  "official_metric": {"name": "roc_auc", "direction": "maximize"},
  "proxy_metric": {"name": "repeated_group_roc_auc", "direction": "maximize"},
  "alignment": {
    "status": "aligned",
    "evidence": ["official metric and target verified from task files"],
    "blind_spots": ["hidden distribution is not observable online"]
  },
  "entrypoint": "evaluate.py",
  "validity_command": ["python", "mle_submit.py", "validate", "solution/submission.csv"],
  "self_test_command": ["python", "evaluate.py", "--self-test"],
  "minimum_improvement": 0.0001,
  "timeout_seconds": 600
}
```

Paths in the manifest are relative and may not escape the bundle. The official and proxy metric
directions must match. The validity command runs in the candidate checkout before the proxy.

## CI invocation

For every `pfc pr submit`, the runtime freezes the exact pushed head, checks it out from the
run-local central Git repository into an isolated CI workspace, and invokes:

```text
python evaluate.py \
  --candidate-root <candidate checkout> \
  --incumbent-root <temporary snapshot of current main> \
  --context <runtime JSON>
```

The entrypoint must not modify either checkout. It evaluates both sides symmetrically and prints
exactly one result line:

```text
PFC_EVAL_RESULT: {"schema_version":1,"metric":"repeated_group_roc_auc","direction":"maximize","candidate_score":0.812,"incumbent_score":0.806,"validity_passed":true,"checks":[{"name":"group-disjoint","passed":true,"detail":"0 entity overlap"},{"name":"seed-stability","passed":true,"detail":"improves on 4/5 seeds"}],"summary":"candidate improves the paired proxy without detected leakage"}
```

Scores must be finite. `checks` must be nonempty. The runtime—not the entrypoint—computes the
signed improvement and accepts only when validity and every check pass and improvement is strictly
greater than `minimum_improvement`.

## Self-test requirements

The self-test must cover result parsing, metric direction, a known improvement, a known regression,
and at least one task-specific leakage/robustness failure. Keep fixtures synthetic and small.

## Repair rule

When the current gate is aligned, do not commit a cosmetic rewrite. Repair only for concrete
misalignment, structural failure, or a demonstrated gaming route. A repair invalidates the current
CI attempt: publish the new linear gate commit, return the same PR to `draft`, and require a fresh
submission and receipt after the author has modified or rebased it.
