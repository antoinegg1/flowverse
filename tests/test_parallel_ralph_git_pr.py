from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from _parallel_flame_chase.core.models import LaneBrief, LaneReport, MissionSpec
from _parallel_flame_chase.orchestration import state as runtime_state
from _parallel_ralph_git_pr.runtime import RalphPlan2, RalphPlan4, execute
from parallel_ralph_git_pr_2way import Agents as Agents2
from parallel_ralph_git_pr_2way import Config as Config2
from parallel_ralph_git_pr_4way import Agents as Agents4
from parallel_ralph_git_pr_4way import Config as Config4


class Session:
    def __init__(self, agent: Agent, cwd: Path) -> None:
        self.agent = agent
        self.cwd = cwd

    def __call__(self, prompt: str, *, suppress: bool, schema: type[Any]) -> Any:
        self.agent.prompts.append((self.cwd, prompt, schema))
        if schema in {RalphPlan2, RalphPlan4}:
            count = 2 if schema is RalphPlan2 else 4
            return schema(
                lanes=[
                    LaneBrief(
                        lane=f"lane-{number}",  # type: ignore[arg-type]
                        mission=MissionSpec(
                            title=f"Approach {number}",
                            objective=f"Test approach {number}",
                            success_criteria=["Produce evidence"],
                            approach_class=f"class-{number}",
                            information_question="Does it improve the score?",
                        ),
                    )
                    for number in range(1, count + 1)
                ]
            )
        if schema is LaneReport:
            return LaneReport(
                status="progress",
                summary="Made measured progress.",
                next_step="Continue in a fresh Ralph round.",
            )
        raise AssertionError(schema)

    def close(self) -> None:
        pass


class Agent:
    def __init__(self) -> None:
        self.prompts: list[tuple[Path, str, type[Any]]] = []

    def new(self, cwd: str | Path | None = None) -> Session:
        return Session(self, Path(cwd or ".").resolve())


@pytest.mark.parametrize("lane_count", [2, 4])
def test_parallel_ralph_runs_one_fixed_model_per_fresh_lane(
    tmp_path: Path, monkeypatch: Any, lane_count: int
) -> None:
    source = tmp_path / f"source-{lane_count}"
    source.mkdir()
    (source / "candidate.py").write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.chdir(source)
    monkeypatch.setattr(runtime_state, "home", lambda: tmp_path / "humanize-home")
    models = [Agent() for _ in range(lane_count + 1)]
    selected_agents = Agents2(*models) if lane_count == 2 else Agents4(*models)
    selected_config = (
        Config2(rest_seconds=0.05) if lane_count == 2 else Config4(rest_seconds=0.05)
    )
    state: dict[str, Any] = {}
    execute(
        selected_agents,
        "Improve candidate.py.",
        selected_config,
        state,
        _sleep=lambda _: time.sleep(0.002),
        _max_turns=lane_count,
    )

    root = Path(state["run_root"])
    assert set(state["lanes"]) == {
        f"lane-{number}" for number in range(1, lane_count + 1)
    }
    for number, model in enumerate(models[1:], start=1):
        assert model.prompts
        cwd, prompt, _schema = model.prompts[0]
        assert cwd == root / "private" / f"lane-{number}"
        assert "This lane is a Ralph loop" in prompt
        assert "prior same-lane report" in prompt
    assert state["global_knowledge_enabled"] is False
    assert state["git_pr_enabled"] is True
