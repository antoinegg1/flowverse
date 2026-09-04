"""Parallel Flame Chase Git/PR with natural main-update steering."""

from __future__ import annotations

from typing import Any, Literal

from _parallel_flame_chase.core.api import BaseConfig, GitPRAgents
from hmz.flows import flow
from parallel_flame_chase_git_pr.runtime import GitPRRuntime

Agents = GitPRAgents


class Config(BaseConfig):
    """Freeze the main-monitor ablation and its controls."""

    git_pr_enabled: Literal[True] = True
    global_knowledge_enabled: Literal[False] = False
    experiment_memory_enabled: Literal[False] = False
    token_efficient_enabled: Literal[False] = False
    main_update_monitor_enabled: Literal[True] = True


class Runtime(GitPRRuntime):
    """Identify this variant independently in resumable state."""

    mode_name = "git-pr-main-monitor"


@flow(resumable=True)
def run(
    agents: Agents,
    task: str,
    config: Config | None = None,
    state: dict[str, Any] | None = None,
) -> None:
    Runtime(agents, task, config or Config(), state).run()


__all__ = ["Agents", "Config", "Runtime", "run"]
