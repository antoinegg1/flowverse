"""Parallel fresh-session Ralph loops over the Git/PR Report Share runtime."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from _parallel_flame_chase.core.models import LaneBrief, LaneName, StrictModel
from _parallel_flame_chase.core.utils import close_safely
from hmz.flows import Stopped
from parallel_flame_chase_git_pr.runtime import GitPRRuntime
from pydantic import Field, model_validator

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Callable


class RalphPlan2(StrictModel):
    """Exactly two independent initial Ralph-loop missions."""

    lanes: list[LaneBrief] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def exact_lanes(self) -> RalphPlan2:
        if {brief.lane for brief in self.lanes} != {"lane-1", "lane-2"}:
            raise ValueError("plan must contain lane-1 and lane-2 once")
        return self


class RalphPlan4(StrictModel):
    """Exactly four independent initial Ralph-loop missions."""

    lanes: list[LaneBrief] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def exact_lanes(self) -> RalphPlan4:
        expected = {"lane-1", "lane-2", "lane-3", "lane-4"}
        if {brief.lane for brief in self.lanes} != expected:
            raise ValueError("plan must contain lane-1 through lane-4 once")
        return self


class RalphGitPRRuntime(GitPRRuntime):
    """Run one model per lane in repeated fresh sessions with durable handoff."""

    mode_name = "parallel-ralph-git-pr"
    skill_name = "parallel-flame-chase-git-pr"
    session_protocol = (
        "This lane is a Ralph loop: every round is a fresh session of the same worker model. "
        "Continue from durable Git state and the prior same-lane report, not chat memory."
    )
    executor_workers = 4

    def __init__(
        self,
        agents: Any,
        task: str,
        config: Any,
        state: dict[str, Any] | None,
        *,
        clock: Callable[[], dt.datetime] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        max_turns: int | None = None,
    ) -> None:
        lane_count = int(config.lane_count)
        if lane_count not in {2, 4}:
            raise ValueError("Parallel Ralph supports exactly two or four lanes")
        self.lane_names = cast(
            "tuple[LaneName, ...]",
            tuple(f"lane-{number}" for number in range(1, lane_count + 1)),
        )
        self.mode_name = f"parallel-ralph-git-pr-{lane_count}way"
        super().__init__(
            agents,
            task,
            config,
            state,
            clock=clock,
            sleeper=sleeper,
            max_turns=max_turns,
        )

    @property
    def _plan_schema(self) -> type[RalphPlan2 | RalphPlan4]:
        return RalphPlan2 if len(self.lane_names) == 2 else RalphPlan4

    def _validate_plan(self, value: object) -> RalphPlan2 | RalphPlan4:
        return self._plan_schema.model_validate(value)

    def _plan(self, objective: str, cwd: Path | None = None) -> RalphPlan2 | RalphPlan4:
        count = len(self.lane_names)
        prompt = f"""You are the planning orchestrateor for a {count}-lane Parallel Ralph loop.

Read the repository and plan only. Split the objective into exactly {count} materially different,
falsifiable missions named {", ".join(self.lane_names)}. Each lane repeatedly starts a fresh
session of one fixed worker model, retains durable Git state and its prior report, has an isolated
clone, and can submit receipt-backed PRs. All lanes are equal; deterministic runtime integration
publishes only a measured improvement. Favor complementary information gain. Do not edit files.

Objective:
{objective}

Workspace map:
{json.dumps(self._workspace_map(), ensure_ascii=False, indent=2)}

Return only the structured plan requested by the runtime.
"""
        failures: list[str] = []
        for attempt in range(1, 4):
            session = self.agents.coordinator.new(cwd=cwd or self.paths.planning)
            try:
                result = session(prompt, suppress=False, schema=self._plan_schema)
            except Stopped:
                raise
            except Exception as why:  # noqa: BLE001 - providers fail in open-ended ways
                failures.append(
                    f"attempt {attempt}: {type(why).__name__}: {why}"[:1000]
                )
                result = None
            finally:
                close_safely(session)
            if result is not None:
                return result
            if len(failures) < attempt:
                failures.append(f"attempt {attempt}: no structured plan")
        raise RuntimeError(f"initial Parallel Ralph plan failed: {failures}")

    def _prepare_lanes(self) -> None:
        for lane in self.lane_names:
            actor = getattr(self.agents, f"lane_{lane.removeprefix('lane-')}_actor")
            lane_state = cast("dict[str, Any]", self.control["lanes"][lane])
            self.lanes[lane] = self._make_lane_runtime(
                lane=lane,
                actors=(actor, actor),
                workspace=self.git_paths.lane(lane),
                actor_at=int(lane_state.get("next_actor", 0)) % 2,
            )

    def _actor_index(self, runtime: Any, durable: dict[str, Any]) -> int:
        return 0

    def _next_actor(self, runtime: Any) -> int:
        return 0

    def _actor_role(self, lane: LaneName, actor_index: int) -> str:
        return f"{lane}-ralph-worker"


def execute(
    agents: Any,
    task: str,
    config: Any,
    state: dict[str, Any] | None,
    *,
    _clock: Callable[[], dt.datetime] | None = None,
    _sleep: Callable[[float], None] = time.sleep,
    _max_turns: int | None = None,
) -> None:
    """Execute one fixed-topology Parallel Ralph experiment."""
    RalphGitPRRuntime(
        agents,
        task,
        config,
        state,
        clock=_clock,
        sleeper=_sleep,
        max_turns=_max_turns,
    ).run()


__all__ = ["RalphGitPRRuntime", "RalphPlan2", "RalphPlan4", "execute"]
