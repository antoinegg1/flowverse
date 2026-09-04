"""Parallel Flame Chase Git/PR with token efficiency and main monitoring."""

from __future__ import annotations

from typing import Any, Literal

from _parallel_flame_chase.core.api import BaseConfig, GitPRAgents
from hmz.flows import flow
from parallel_flame_chase_git_pr.runtime import GitPRRuntime

Agents = GitPRAgents


class Config(BaseConfig):
    """Freeze the combined token-efficient plus monitor treatment."""

    git_pr_enabled: Literal[True] = True
    global_knowledge_enabled: Literal[False] = False
    experiment_memory_enabled: Literal[False] = False
    token_efficient_enabled: Literal[True] = True
    main_update_monitor_enabled: Literal[True] = True


class Runtime(GitPRRuntime):
    """Identify this variant independently in resumable state."""

    mode_name = "git-pr-token-efficient-main-monitor"


@flow(resumable=True)
def run(
    agents: Agents,
    task: str,
    config: Config | None = None,
    state: dict[str, Any] | None = None,
) -> None:
    Runtime(agents, task, config or Config(), state).run()


__all__ = ["Agents", "Config", "Runtime", "run"]
