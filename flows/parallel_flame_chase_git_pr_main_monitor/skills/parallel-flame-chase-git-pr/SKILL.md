---
name: parallel-flame-chase-git-pr
description: Operate a receipt-fast-path Git/PR lane with live main-update steering.
---

# Git/PR Main-Monitor Lane

Work only in the assigned clone. Start a `lane-N/<experiment>` branch, commit and push substantive
task changes, record the official evaluator with `pfc evaluate`, then open and ready a PR with its
receipt. A Main Update Monitor event is trusted runtime context: decide whether to rebase, continue,
or combine, without replying to the event separately. Preserve the decision in the LaneReport.
Never perform remote release or deployment actions.
