# Parallel Flame Chase Git/PR

`parallel_flame_chase_git_pr` retains Report Share and adds two independently switchable
mechanisms: a receipt-fast-path PR protocol and a compact run-local knowledge digest. The flags are
frozen when a run is created, giving a complete 2x2 experiment:

| `git_pr_enabled` | `global_knowledge_enabled` | Behavior |
| --- | --- | --- |
| false | false | Existing Report Share behavior |
| true | false | Isolated Git branches and score-prioritized receipt integration |
| false | true | Existing workspaces plus a compact shared-best digest |
| true | true | Receipt integration plus the compact digest |

The eight ordered agents are `orchestrateor`, `knowledge_reviewer`, and A/B partners for lanes 1,
2, and 3. With Git enabled, every lane owns one clone and has equal PR rights. The runtime owns a
bare central repository and a separate integration clone. A lane may keep many drafts but only one
ready/reviewing PR. Ready heads are frozen, while a newer queued candidate from the same lane
supersedes its older one. The runtime validates the exact official evaluator command and immutable
receipt artifacts, chooses the lowest-cycle candidate, and publishes its exact tested tree only
when it improves main. The server hook accepts main only when the commit has two exact parents, an
allowed-path diff, the tested head's exact tree, a successful head-bound receipt, and the matching
`PFC-PR` trailer. No model review or staging re-evaluation runs on this fast path.

`pfc evaluate -- <command>` records rather than interprets the evaluator. It requires a clean
commit/tree before and after the command, stores stdout/stderr outside Git, and preserves an
immutable receipt. Code and light files belong in Git; large artifacts can use `pfc artifact put`.

SQLite/WAL is live truth, JSONL is the audit stream, and JSON/Markdown are disposable views.
Evaluator-backed shared-best and merged results create compact ExperienceCards automatically.
Only the newest 12 stay in the hot digest and at most four are injected into a lane prompt. There
is no always-on knowledge-review model; failures and routine progress remain in Report Share's
archive. System reports broadcast merges and digest updates while routing rejection and invalid
receipts only to the affected lane.

```console
hmz exec -f ./flows/parallel_flame_chase_git_pr \
  -a codex/gpt-5.6-sol:max -a claude/claude-opus-5:max \
  -a codex/gpt-5.6-sol:max -a claude/claude-opus-5:max \
  -a codex/gpt-5.6-sol:max -a claude/claude-opus-5:max \
  -a codex/gpt-5.6-sol:max -a claude/claude-opus-5:max \
  -c ./flows/parallel_flame_chase_git_pr/examples/git-pr.yaml \
  "$(cat TASK.md)"
```

Runs are resumable. Runtime state retains the central refs, receipts, artifacts, report archives,
ledger, and compact knowledge index. The
original source is assumed not to change outside the flow while it holds the source lock.
