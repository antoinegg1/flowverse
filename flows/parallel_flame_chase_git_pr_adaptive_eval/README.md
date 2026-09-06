# Parallel Flame Chase Git/PR Adaptive Eval

This is a new workflow copied from `parallel_flame_chase_git_pr`; the measured fixed workflow is
unchanged. It preserves three independent lanes, immutable evaluator receipts, protected main,
and exact-tree automatic merges, but replaces the fixed lowest-`CYCLES` policy with a
coordinator-authored, task-specific PR proxy gate.

## Lifecycle

1. The coordinator dispatches exactly three experiment lanes.
2. All lanes start immediately and may open PRs independently. A lane pushes a commit, opens one
   draft PR, and submits it with `pfc pr submit PRxxxxxx`.
3. In parallel, a fresh coordinator session classifies the task, builds
   `.pfc/adaptive-eval/**`, self-tests it, and pushes a one-parent commit to
   `refs/heads/pfc/eval-gate`.
4. The existing local Git/PR runtime treats the gate as CI. It freezes each submitted head, clones
   that exact commit from the central bare repository into an isolated workspace, and runs the
   stable evaluator. A CI attempt uses the gate active when that attempt was submitted; before the
   first activation it uses the task's baseline evaluator.
5. Every CI receipt gets a fresh coordinator audit for wrong-artifact behavior, leakage,
   overfitting, and gate/real-task alignment. An aligned gate is left untouched. A misaligned gate
   is repaired in a new linear commit, and the same PR is returned to its author for modification
   or rebasing and resubmission.
6. A successful, current-main receipt plus `aligned_accept` audit triggers deterministic automatic
   merge. No model performs integration.

The PR lifecycle is `draft → ci_pending → ci_running → ready → reviewing → merged`. A failed CI,
stale-main result, stale-gate result, or rejected audit transitions back to `draft`; this unlocks
the lane branch so its author can push a correction and resubmit the same PR. Branch protection
rejects head changes while CI/review is active and rejects every direct or unevaluated main update.

Failed evaluator and rejected-PR details go only to the author lane's system-report inbox and the
coordinator audit inbox. Successful merges may still be announced normally. In blind tasks,
alignment means agreement with the authoritative metric, validity rules, and data protocol; it
does not claim observed agreement with hidden scores.

The stable run-local `pfc-evaluate` command is frozen in the coordination database, while gate
revisions are immutable Git commits recorded in `shared/adaptive-eval/registry.json`. Therefore a
lane cannot swap evaluators or mark itself ready, an old receipt cannot silently adopt a newer
gate, and a repair never retroactively approves the PR attempt that exposed the flaw.

```console
hmz exec -f ./flows/parallel_flame_chase_git_pr_adaptive_eval \
  -a codex/gpt-5.6-sol:max \
  -a codex/gpt-5.6-sol:max -a claude/claude-opus-5:max \
  -a claude/claude-opus-5:max -a codex/gpt-5.6-sol:max \
  -a claude/claude-opus-5:max -a codex/gpt-5.6-sol:max \
  -c ./flows/parallel_flame_chase_git_pr_adaptive_eval/examples/adaptive-eval.yaml \
  "$(cat TASK.md)"
```
