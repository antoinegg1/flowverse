"""Four-lane Parallel Ralph with Git/PR and Report Share."""

from __future__ import annotations

from typing import Annotated, Any, Literal, NamedTuple

from _parallel_flame_chase.core.api import BaseConfig
from _parallel_ralph_git_pr.runtime import execute
from hmz.flows import Agent, AgentDefaults, flow

NoGoals = Annotated[Agent, AgentDefaults(goals=False)]


class Agents(NamedTuple):
    """One planner and one fixed worker model for each Ralph lane."""

    orchestrateor: NoGoals
    lane_1_actor: NoGoals
    lane_2_actor: NoGoals
    lane_3_actor: NoGoals
    lane_4_actor: NoGoals

    @property
    def coordinator(self) -> NoGoals:
        return self.orchestrateor


class Config(BaseConfig):
    """Freeze the four-lane topology and collaboration mechanisms."""

    lane_count: Literal[4] = 4
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
    execute(agents, task, config or Config(), state)


__all__ = ["Agents", "Config", "run"]
