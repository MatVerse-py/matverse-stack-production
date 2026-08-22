from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from .config import SGSI_AGENT_NAME, SGSI_AGENT_ORCID


class AlgorithmBody(BaseModel):
    psi: float = Field(default=0.5, ge=0.0, le=1.0, description="Coherence (Ψ)")
    theta: float = Field(default=0.5, ge=0.0, le=1.0, description="Embodiment (Θ)")
    cvar: float = Field(default=0.0, ge=0.0, le=1.0, description="Conditional Value at Risk (CVaR)")
    pole: float = Field(default=0.5, ge=0.0, le=1.0, description="Proof of Liveness (PoLE)")
    omega: float = Field(default=0.5, ge=0.0, le=1.0, description="Overall Fitness (Ω)")

    def calculate_omega(self) -> float:
        # Ω = 0.4Ψ + 0.3Θ + 0.2(1-CVaR) + 0.1PoLE
        self.omega = (0.4 * self.psi) + (0.3 * self.theta) + (0.2 * (1 - self.cvar)) + (0.1 * self.pole)
        return self.omega


class GovernanceBody(BaseModel):
    decision: Literal["PASS", "ESCALATE", "BLOCK", "FAIL_CLOSED"] = "PASS"
    institutional_score: float = Field(default=0.5, ge=0.0, le=1.0)
    embodied_score: float = Field(default=0.5, ge=0.0, le=1.0)
    closure_ready: bool = False
    fail_closed_active: bool = False

    def make_decision(self, algorithm_metrics: AlgorithmBody, telemetry: Dict[str, Any]) -> Literal["PASS", "ESCALATE", "BLOCK", "FAIL_CLOSED"]:
        # Invariante de Fail-Closed: Se anomalia crítica detectada na telemetria
        if telemetry.get("critical_anomaly", False) or algorithm_metrics.cvar > 0.15:
            self.decision = "FAIL_CLOSED"
            self.fail_closed_active = True
            return self.decision

        if algorithm_metrics.cvar > 0.05: # Hard veto
            self.decision = "BLOCK"
        elif algorithm_metrics.psi >= 0.85 and algorithm_metrics.omega >= 0.85:
            self.decision = "PASS"
        else:
            self.decision = "ESCALATE"
        return self.decision

    def check_closure_ready(self, algorithm_metrics: AlgorithmBody, replay_ok: bool, receipt_ok: bool, publication_ok: bool) -> bool:
        if self.fail_closed_active:
            self.closure_ready = False
            return False
            
        self.closure_ready = (
            algorithm_metrics.psi >= 0.85
            and algorithm_metrics.cvar <= 0.05
            and algorithm_metrics.omega >= 0.85
            and replay_ok
            and receipt_ok
            and publication_ok
        )
        return self.closure_ready


class SkillBody(BaseModel):
    skill_name: str = "default_skill"
    mutation_applied: bool = False
    new_mnb_id: Optional[str] = None


class AgentBody(BaseModel):
    agent_name: str = SGSI_AGENT_NAME
    orcid: Optional[str] = SGSI_AGENT_ORCID
    timestamp: float = Field(default_factory=time.time)


class SGSI(BaseModel):
    agent: AgentBody = Field(default_factory=AgentBody)
    metrics: AlgorithmBody = Field(default_factory=AlgorithmBody)
    governance: GovernanceBody = Field(default_factory=GovernanceBody)
    skill: SkillBody = Field(default_factory=SkillBody)
    context: Dict[str, Any] = {}
    raw_input: Any = None
    processed_output: Any = None
    event_type: str = "process_event"
    losses: List[float] = []
    latency_ms: int = 0
    replay_ok: bool = False
    receipt_ok: bool = False
    publication_ok: bool = False

    def process(self, raw_input: Dict[str, Any]) -> Dict[str, Any]:
        self.raw_input = raw_input
        self.event_type = raw_input.get("type", "process_event")
        self.context = raw_input.get("context", {})
        self.losses = raw_input.get("losses", [])
        self.latency_ms = raw_input.get("latency_ms", 0)
        self.replay_ok = raw_input.get("replay_ok", False)
        self.receipt_ok = raw_input.get("receipt_ok", False)
        self.publication_ok = raw_input.get("publication_ok", False)

        # Telemetria Local (Simulada a partir do input e estado)
        telemetry = {
            "timestamp": time.time(),
            "latency_ms": self.latency_ms,
            "critical_anomaly": self.latency_ms > 5000 or raw_input.get("force_fail", False),
            "system_load": 0.45 # Placeholder
        }

        # Update metrics based on input
        if self.losses:
            sorted_losses = sorted(self.losses, reverse=True)
            cvar_threshold_index = math.ceil(len(sorted_losses) * 0.05) - 1
            if cvar_threshold_index >= 0 and cvar_threshold_index < len(sorted_losses):
                self.metrics.cvar = sum(sorted_losses[:cvar_threshold_index+1]) / (cvar_threshold_index + 1)
            else:
                self.metrics.cvar = 0.0
        else:
            self.metrics.cvar = 0.0

        self.metrics.psi = raw_input.get("psi", 0.87)
        self.metrics.theta = raw_input.get("theta", 0.5)
        self.metrics.pole = raw_input.get("pole", 0.5)

        self.metrics.calculate_omega()
        
        # Ω-Gate: Decisão de Governança com Telemetria e Fail-Closed
        self.governance.make_decision(self.metrics, telemetry)
        self.governance.check_closure_ready(self.metrics, self.replay_ok, self.receipt_ok, self.publication_ok)

        self.processed_output = {
            "agent": self.agent.model_dump(),
            "metrics": self.metrics.model_dump(),
            "governance": self.governance.model_dump(),
            "skill": self.skill.model_dump(),
            "telemetry": telemetry,
            "context": self.context,
            "event_type": self.event_type,
            "losses": self.losses,
            "latency_ms": self.latency_ms,
            "replay_ok": self.replay_ok,
            "receipt_ok": self.receipt_ok,
            "publication_ok": self.publication_ok,
        }
        return self.processed_output

        self.processed_output = {
            "agent": self.agent.model_dump(),
            "metrics": self.metrics.model_dump(),
            "governance": self.governance.model_dump(),
            "skill": self.skill.model_dump(),
            "context": self.context,
            "event_type": self.event_type,
            "losses": self.losses,
            "latency_ms": self.latency_ms,
            "replay_ok": self.replay_ok,
            "receipt_ok": self.receipt_ok,
            "publication_ok": self.publication_ok,
        }
        return self.processed_output
