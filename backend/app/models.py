"""Pydantic data models for the Atlas pipeline.

These are the contracts between Planner ↔ Executor ↔ Synthesizer ↔ Renderer.
If a model serializes, the brief renders.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

# ── Module catalog ─────────────────────────────────────────────────

ModuleName = Literal[
    "trueprice",
    "signal",
    "filing",
    "altdata",
    "visual",
    "exposure",
    "investor",
]

MODULE_NAMES: tuple[ModuleName, ...] = (
    "trueprice",
    "signal",
    "filing",
    "altdata",
    "visual",
    "exposure",
    "investor",
)

Intent = Literal["competitive", "financial", "security", "mixed"]
Severity = Literal["info", "notable", "high", "critical"]
ModuleStatus = Literal["success", "partial", "failed"]


# ── Input ──────────────────────────────────────────────────────────


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: f"q_{uuid4().hex[:10]}")
    text: str
    user_context: dict[str, Any] | None = None


# ── Plan ───────────────────────────────────────────────────────────


class ModuleInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    module: ModuleName
    params: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=3, ge=1, le=5)
    rationale: str = ""


class ResearchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    intent: Intent
    modules_to_invoke: list[ModuleInvocation]
    reasoning: str = ""


# ── Module output ──────────────────────────────────────────────────


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    title: str
    accessed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    via: str  # Bright Data tool that retrieved it (e.g. "web_unlocker")


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str
    evidence: list[str] = Field(default_factory=list)
    severity: Severity = "notable"


class ModuleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    module: ModuleName
    status: ModuleStatus = "success"
    findings: list[Finding] = Field(default_factory=list)
    raw_data: dict[str, Any] = Field(default_factory=dict)
    sources: list[Source] = Field(default_factory=list)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    duration_ms: int = 0
    error: str | None = None


# ── Brief output ───────────────────────────────────────────────────


class BriefSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    module: ModuleName
    title: str
    summary: str
    findings: list[Finding] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    confidence: float = 0.7
    # Module-specific structured payload (e.g. TruePrice's regions table)
    # that the brief renderer can lay out alongside the findings. Free-form
    # by design — each module owns the keys it sets here.
    data: dict[str, Any] = Field(default_factory=dict)


class Brief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: f"brief_{uuid4().hex[:10]}")
    question: Question
    plan: ResearchPlan
    subject: str = "Unknown"
    executive_summary: str = ""
    key_findings: list[Finding] = Field(default_factory=list)
    sections: list[BriefSection] = Field(default_factory=list)
    confidence_score: float = Field(default=0.7, ge=0.0, le=1.0)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    mode: Literal["mock", "live"] = "mock"
