"""Versioned input contracts. Monetary calculations use integer micro-USD."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Metric(Contract):
    name: str = Field(min_length=1, max_length=160)
    unit: str = Field(min_length=1)
    direction: Literal["minimize", "maximize"] = "minimize"
    role: Literal["objective", "constraint", "info"] = "objective"
    operator: Literal["lte", "gte", "eq"] | None = None
    value: float | None = None
    minimum_improvement: float = Field(default=0.02, ge=0, le=1)
    max_cv: float = Field(default=0.15, gt=0, le=1)
    min_repetitions: int = Field(default=3, ge=2, le=50)

    @model_validator(mode="after")
    def constraint(self):
        if self.role == "constraint" and (self.operator is None or self.value is None):
            raise ValueError("Constraints require operator and value")
        return self


class Protocol(Contract):
    schema_version: Literal[1] = 1
    command: list[str] = Field(min_length=1)
    metrics: list[Metric] = Field(min_length=1)
    repetitions: int = Field(default=3, ge=2, le=50)
    confirmation_repetitions: int = Field(default=3, ge=2, le=50)
    timeout_seconds: float = Field(default=60, gt=0, le=3600)
    warmup_command: list[str] = Field(default_factory=list)
    diagnostic_command: list[str] = Field(default_factory=list)
    protected_paths: list[str] = Field(default_factory=list)
    environment: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def metrics_valid(self):
        if len({m.name for m in self.metrics}) != len(self.metrics):
            raise ValueError("Metric names must be unique")
        if not any(m.role == "objective" for m in self.metrics):
            raise ValueError("At least one objective metric is required")
        if any(
            m.min_repetitions > min(self.repetitions, self.confirmation_repetitions)
            for m in self.metrics
        ):
            raise ValueError("Repetition counts cannot be below a metric's minimum")
        for path in self.protected_paths:
            if (
                path.startswith("/")
                or any(p in {".", ".."} for p in path.rstrip("/").split("/"))
                or not path
            ):
                raise ValueError("Protected paths must be relative repository paths")
        return self


class Limits(Contract):
    max_parallel_tasks: int = Field(default=2, ge=1, le=4)
    max_experiments: int = Field(default=8, ge=1, le=1000)
    max_active_seconds: int = Field(default=7200, ge=1, le=86400)
    max_task_seconds: int = Field(default=900, ge=1, le=3600)
    max_preparation_attempts: int = Field(default=3, ge=1, le=10)


class CampaignCreate(Contract):
    schema_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=120)
    repository: str = Field(min_length=1)
    base_ref: str = "HEAD"
    goal: str = Field(min_length=1, max_length=4000)
    budget_usd: Decimal = Field(default=Decimal("100"), gt=0, le=Decimal("100000"))
    protected_fraction: Decimal = Field(default=Decimal("0.2"), ge=0, lt=1)
    protocol: Protocol | None = None
    runner: dict = Field(
        default_factory=lambda: {"kind": "docker", "image": "eclipse-temurin:25-jdk"}
    )
    agent: dict = Field(
        default_factory=lambda: {"kind": "manual", "enforcement": "strict"}
    )
    limits: Limits = Field(default_factory=Limits)

    @field_validator("budget_usd")
    @classmethod
    def whole_micros(cls, value):
        if value * 1_000_000 != (value * 1_000_000).to_integral_value():
            raise ValueError("Budget supports at most six fractional USD digits")
        return value

    @model_validator(mode="after")
    def no_credentials(self):
        forbidden = {"api_key", "token", "password", "secret", "authorization"}

        def check(obj):
            if isinstance(obj, dict):
                if forbidden & {str(k).lower() for k in obj}:
                    raise ValueError(
                        "Use environment credentials, never stored campaign secrets"
                    )
                for v in obj.values():
                    check(v)
            if isinstance(obj, list):
                for v in obj:
                    check(v)

        check(self.agent)
        check(self.runner)
        kind = self.runner.get("kind", self.runner.get("mode", "docker"))
        if kind not in {"docker", "host"}:
            raise ValueError("Runner kind must be docker or host")
        if "mode" in self.runner and self.runner["mode"] != kind:
            raise ValueError("Conflicting runner kind/mode")
        self.runner["kind"] = kind
        from .scenario import services

        scenario = services(self.runner.get("services", []))
        if scenario and kind != "docker":
            raise ValueError("Scenario services require the isolated Docker runner")
        if scenario:
            self.runner["services"] = [s.model_dump() for s in scenario]
        if kind == "host" and self.runner.get("trusted") is not True:
            raise ValueError("Host runner requires explicit trusted=true")
        if self.agent.get("kind", "manual") not in {"manual", "claude", "claude_code"}:
            raise ValueError("Agent kind must be manual, claude or claude_code")
        if "excluded_paths" in self.agent:
            from .runner import source_exclusions

            if self.agent.get("kind") != "claude_code":
                raise ValueError(
                    "Source exclusions require the native Claude Code agent"
                )
            self.agent["excluded_paths"] = source_exclusions(
                self.agent["excluded_paths"]
            )
        if self.agent.get("enforcement", "strict") not in {"strict", "estimated"}:
            raise ValueError("Enforcement must be strict or estimated")
        return self


class ExperimentCreate(Contract):
    hypothesis: str = Field(min_length=1, max_length=8000)
    strategy: str = Field(default="user-proposed", max_length=200)
    patch: str = Field(min_length=1, max_length=500000)
    parent_id: str | None = None
    expected_effect: str = ""
    origin: Literal["manual", "external_worker"] = "manual"


class Approval(Contract):
    protocol_hash: str
    authority: Literal["operator", "delegated_agent"] = "operator"
    rationale: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def delegation(self):
        if self.authority == "delegated_agent" and not self.rationale.strip():
            raise ValueError(
                "Delegated approval requires the operator's scope of authorization"
            )
        return self


class ProtocolUpdate(Contract):
    protocol: Protocol


class PublishRequest(Contract):
    candidate_id: str
    review_hash: str
    remote_repository: str
    base_branch: str
    head_branch: str
