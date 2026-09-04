from __future__ import annotations

from flame_chase_report_share import Config, TurnReport, _prompt
from hmz.flows import configures, drives, resumes


def test_public_contract_is_two_agents_and_resumable() -> None:
    from pathlib import Path

    entry = (
        Path(__file__).parents[1] / "flows" / "flame_chase_report_share" / "__init__.py"
    )
    assert drives(entry) == ("", "")
    assert resumes(entry)
    configured = configures(entry)
    assert configured is not None
    assert set(configured.model_fields) == set(Config.model_fields)


def test_handoff_is_explicitly_non_authoritative() -> None:
    prompt = _prompt(
        "Improve candidate.py",
        TurnReport(
            summary="Candidate measured 100 cycles.",
            evidence=["local evaluator"],
        ).model_dump(mode="json"),
    )
    assert "Candidate measured 100 cycles" in prompt
    assert "evidence-bearing claims, not authority" in prompt
    assert "conversation history" in prompt
