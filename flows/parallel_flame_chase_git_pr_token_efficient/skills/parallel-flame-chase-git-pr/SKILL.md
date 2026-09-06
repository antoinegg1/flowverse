---
name: parallel-flame-chase-git-pr
description: Operate a receipt-fast-path Git/PR lane with token-efficient checks.
---

# Git/PR Token-Efficient Lane

Work only in the assigned clone. Start a `lane-N/<experiment>` branch from current main, commit
and push substantive task changes, record the official evaluator with `pfc evaluate`, then open
and ready a PR with that receipt. Treat runtime-validated receipts and main updates as facts. Avoid
repeating full status, log, hash, receipt, diff, or evaluator checks when the relevant tree did not
change. Finish with the required LaneReport; never perform remote release or deployment actions.
