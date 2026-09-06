"""Parallel Flame Chase Git/PR with a coordinator-authored adaptive eval gate."""

from __future__ import annotations

from typing import Any, Literal

from _parallel_flame_chase.core.api import BaseConfig, GitPRAgents
from hmz.flows import flow

from parallel_flame_chase_git_pr_adaptive_eval.runtime import execute

Agents = GitPRAgents


class Config(BaseConfig):
    """Freeze the adaptive-gate workflow's isolation settings."""

    git_pr_enabled: Literal[True] = True
    global_knowledge_enabled: Literal[False] = False
    experiment_memory_enabled: Literal[False] = False
    token_efficient_enabled: Literal[False] = False
    main_update_monitor_enabled: Literal[False] = False


@flow(resumable=True)
def run(
    agents: Agents,
    task: str,
    config: Config | None = None,
    state: dict[str, Any] | None = None,
) -> None:
    """Run independent lanes with versioned gate audits and automatic integration."""
    execute(agents, task, config or Config(), state)


__all__ = ["Agents", "Config", "run"]
