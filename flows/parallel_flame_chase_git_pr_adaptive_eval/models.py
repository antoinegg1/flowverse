"""Structured coordinator results and adaptive evaluation contracts."""

from __future__ import annotations

from typing import Literal

from _parallel_flame_chase.core.models import ReportItem, StrictModel
from pydantic import Field, model_validator


class MetricSpec(StrictModel):
    """Official or proxy metric semantics."""

    name: str = Field(min_length=1, max_length=200)
    direction: Literal["minimize", "maximize"]


class AlignmentSpec(StrictModel):
    """Why a proxy is expected to preserve the real evaluator's ordering."""

    status: Literal["aligned"]
    evidence: list[str] = Field(min_length=1, max_length=20)
    blind_spots: list[str] = Field(default_factory=list, max_length=20)


class GateManifest(StrictModel):
    """Versioned contract committed by the coordinator."""

    schema_version: Literal[1] = 1
    task_type: str = Field(min_length=1, max_length=200)
    official_metric: MetricSpec
    proxy_metric: MetricSpec
    alignment: AlignmentSpec
    entrypoint: str = Field(pattern=r"^[A-Za-z0-9_.\-/]+$")
    validity_command: list[str] = Field(min_length=1, max_length=50)
    self_test_command: list[str] = Field(min_length=1, max_length=50)
    minimum_improvement: float = Field(default=0.0, ge=0.0)
    timeout_seconds: int = Field(default=600, ge=1, le=3600)

    @model_validator(mode="after")
    def proxy_direction_matches_official(self) -> GateManifest:
        if self.proxy_metric.direction != self.official_metric.direction:
            raise ValueError("proxy and official metric directions must match")
        return self


class CheckResult(StrictModel):
    """One required validity, robustness, or leakage check."""

    name: str = Field(min_length=1, max_length=200)
    passed: bool
    detail: str = Field(min_length=1, max_length=2000)


class GateEvaluation(StrictModel):
    """Output emitted by a committed proxy evaluator."""

    schema_version: Literal[1] = 1
    metric: str = Field(min_length=1, max_length=200)
    direction: Literal["minimize", "maximize"]
    candidate_score: float
    incumbent_score: float
    validity_passed: bool
    checks: list[CheckResult] = Field(min_length=1, max_length=50)
    summary: str = Field(min_length=1, max_length=4000)


class GateReceipt(StrictModel):
    """Runtime-normalized receipt printed by the immutable gate runner."""

    schema_version: Literal[1] = 1
    mode: Literal["baseline", "adaptive"]
    decision: Literal["accept", "reject", "invalid"]
    gate_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    incumbent_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    metric: str | None = Field(default=None, max_length=200)
    direction: Literal["minimize", "maximize"] | None = None
    candidate_score: float | None = None
    incumbent_score: float | None = None
    improvement: float | None = None
    alignment_status: Literal["baseline", "aligned", "misaligned", "insufficient"]
    checks: list[CheckResult] = Field(default_factory=list, max_length=50)
    reason: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def mode_has_matching_commit(self) -> GateReceipt:
        if self.mode == "adaptive" and self.gate_commit is None:
            raise ValueError("adaptive receipt requires gate_commit")
        if self.mode == "baseline" and self.gate_commit is not None:
            raise ValueError("baseline receipt cannot carry gate_commit")
        return self


class GateBuildResult(StrictModel):
    """Coordinator outcome after constructing or repairing a gate."""

    status: Literal["published", "deferred"]
    summary: str = Field(min_length=1, max_length=4000)
    gate_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    task_type: str = Field(min_length=1, max_length=200)
    official_metric: MetricSpec
    alignment_evidence: list[ReportItem] = Field(default_factory=list, max_length=30)
    tests: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def published_result_has_commit(self) -> GateBuildResult:
        if self.status == "published" and self.gate_commit is None:
            raise ValueError("published gate requires gate_commit")
        if self.status == "deferred" and self.gate_commit is not None:
            raise ValueError("deferred gate cannot carry gate_commit")
        return self


class GateReviewResult(StrictModel):
    """Coordinator's mandatory alignment and overfitting audit for one receipt."""

    receipt_id: str = Field(min_length=1, max_length=100)
    pr_id: str | None = Field(default=None, max_length=100)
    verdict: Literal[
        "aligned_accept",
        "aligned_reject",
        "repair_published",
        "insufficient",
    ]
    alignment_status: Literal["aligned", "misaligned", "insufficient"]
    summary: str = Field(min_length=1, max_length=4000)
    evidence: list[ReportItem] = Field(default_factory=list, max_length=30)
    replacement_gate_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")

    @model_validator(mode="after")
    def repair_has_replacement(self) -> GateReviewResult:
        repaired = self.verdict == "repair_published"
        if repaired != (self.replacement_gate_commit is not None):
            raise ValueError("only repair_published carries a replacement gate commit")
        if repaired and self.alignment_status != "misaligned":
            raise ValueError("gate repair requires a misalignment finding")
        if self.verdict.startswith("aligned_") and self.alignment_status != "aligned":
            raise ValueError("aligned verdict requires aligned status")
        return self


__all__ = [
    "AlignmentSpec",
    "CheckResult",
    "GateBuildResult",
    "GateEvaluation",
    "GateManifest",
    "GateReceipt",
    "GateReviewResult",
    "MetricSpec",
]
