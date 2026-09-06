# Parallel Flame Chase Git/PR Adaptive Eval

This is a new workflow copied from `parallel_flame_chase_git_pr`; the measured fixed workflow is
unchanged. It preserves three independent lanes, immutable evaluator receipts, protected main,
and exact-tree automatic merges, but replaces the fixed lowest-`CYCLES` policy with a
coordinator-authored, task-specific PR proxy gate.

## Lifecycle

1. The coordinator dispatches exactly three experiment lanes.
2. All lanes start immediately and may open PRs independently. While no gate is active, the stable
   evaluator wrapper delegates to the task's baseline evaluator.
3. In parallel, a fresh coordinator session classifies the task, builds
   `.pfc/adaptive-eval/**`, self-tests it, and pushes a one-parent commit to
   `refs/heads/pfc/eval-gate`.
4. The runtime validates that commit and activates it. PRs opened afterward compare the candidate
   and current main through that exact commit; older PRs retain their original evaluator binding.
5. Every lane receipt gets a fresh coordinator audit for wrong-artifact behavior, leakage,
   overfitting, and gate/real-task alignment. An aligned gate is left untouched. A misaligned gate
   is repaired in a new linear commit, and the candidate must be submitted as a new PR and
   evaluated again.
6. A successful, current-main receipt plus `aligned_accept` audit triggers deterministic automatic
   merge. No model performs integration.

Failed evaluator and rejected-PR details go only to the author lane's system-report inbox and the
coordinator audit inbox. Successful merges may still be announced normally. In blind tasks,
alignment means agreement with the authoritative metric, validity rules, and data protocol; it
does not claim observed agreement with hidden scores.

The stable run-local `pfc-evaluate` command is frozen in the coordination database, while gate
revisions are immutable Git commits recorded in `shared/adaptive-eval/registry.json`. Therefore a
lane cannot swap evaluators, an old receipt cannot silently adopt a newer gate, and a repair never
retroactively approves the PR that exposed the flaw.

```console
hmz exec -f ./flows/parallel_flame_chase_git_pr_adaptive_eval \
  -a codex/gpt-5.6-sol:max \
  -a codex/gpt-5.6-sol:max -a claude/claude-opus-5:max \
  -a claude/claude-opus-5:max -a codex/gpt-5.6-sol:max \
  -a claude/claude-opus-5:max -a codex/gpt-5.6-sol:max \
  -c ./flows/parallel_flame_chase_git_pr_adaptive_eval/examples/adaptive-eval.yaml \
  "$(cat TASK.md)"
```
