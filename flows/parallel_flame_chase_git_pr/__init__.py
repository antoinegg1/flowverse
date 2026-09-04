"""Parallel Flame Chase with factorial Git/PR and global-knowledge controls."""

from __future__ import annotations

from typing import Any

from _parallel_flame_chase.core.api import BaseConfig, GitPRAgents
from hmz.flows import flow
from pydantic import Field

from parallel_flame_chase_git_pr.runtime import execute

Agents = GitPRAgents


class Config(BaseConfig):
    """Freeze the independently switchable collaboration mechanisms for one run."""

    git_pr_enabled: bool = Field(
        default=True,
        description="Use isolated clones and score-prioritized receipt-fast-path PRs.",
    )
    global_knowledge_enabled: bool = Field(
        default=True,
        description="Retain a compact run-local digest of evaluator-backed successes.",
    )
    experiment_memory_enabled: bool = Field(
        default=False,
        description="Use a deterministic intent-indexed run-local experiment ledger.",
    )
    token_efficient_enabled: bool = Field(
        default=False,
        description="Remove repeated model-side checks already enforced by the runtime.",
    )
    main_update_monitor_enabled: bool = Field(
        default=False,
        description="Steer compact main-update notices into active lane turns.",
    )


@flow(resumable=True)
def run(
    agents: Agents,
    task: str,
    config: Config | None = None,
    state: dict[str, Any] | None = None,
) -> None:
    """Run one frozen factorial cell with Report Share retained in every cell."""
    execute(agents, task, config or Config(), state)


__all__ = ["Agents", "Config", "run"]
