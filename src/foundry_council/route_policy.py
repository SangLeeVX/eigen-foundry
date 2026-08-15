"""M7 — route policy and F3–F8 preclinical gate policy.

Defines the M7 route families and the F3–F8 admissibility gate policy.

Routes:
  - Rescue / existing-asset routes (advance through F6A): EXISTING_ASSET,
    REPOSITIONING, ASSET_RESCUE, TARGET_RESCUE, TRIAL_RESCUE, INBOUND_DILIGENCE.
  - De novo routes (advance through F6B): KNOWN_TARGET_NEW_CANDIDATE,
    NOVEL_TARGET_DE_NOVO.
  - Combination route (advance through F6C): COMBINATION.

F3–F8 gate policy: like F0–F2, a MODEL_PREDICTION decisive claim can never
satisfy a preclinical experimental gate; OBSERVED / SUPPORTED_INFERENCE /
EXPERIMENTALLY_VALIDATED evidence is admissible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import ClaimState, ProgramStage, Route


# Which F6 sub-stage each route advances through.
ROUTE_F6_STAGE: dict[Route, ProgramStage] = {
    Route.EXISTING_ASSET: ProgramStage.F6A,
    Route.REPOSITIONING: ProgramStage.F6A,
    Route.ASSET_RESCUE: ProgramStage.F6A,
    Route.TARGET_RESCUE: ProgramStage.F6A,
    Route.TRIAL_RESCUE: ProgramStage.F6A,
    Route.INBOUND_DILIGENCE: ProgramStage.F6A,
    Route.KNOWN_TARGET_NEW_CANDIDATE: ProgramStage.F6B,
    Route.NOVEL_TARGET_DE_NOVO: ProgramStage.F6B,
    Route.COMBINATION: ProgramStage.F6C,
}

RESCUE_ROUTES = frozenset(
    {
        Route.EXISTING_ASSET,
        Route.REPOSITIONING,
        Route.ASSET_RESCUE,
        Route.TARGET_RESCUE,
        Route.TRIAL_RESCUE,
        Route.INBOUND_DILIGENCE,
    }
)
DE_NOVO_ROUTES = frozenset(
    {Route.KNOWN_TARGET_NEW_CANDIDATE, Route.NOVEL_TARGET_DE_NOVO}
)
COMBINATION_ROUTES = frozenset({Route.COMBINATION})


def f6_stage_for(route: Route) -> ProgramStage:
    if route not in ROUTE_F6_STAGE:
        raise ValueError(f"no F6 stage mapped for route {route.value}")
    return ROUTE_F6_STAGE[route]


def full_stage_sequence(route: Route) -> tuple[ProgramStage, ...]:
    """The governed F0→F8 stage sequence for a route, with NO gate skips."""
    f6 = f6_stage_for(route)
    return (
        ProgramStage.F0, ProgramStage.F1, ProgramStage.F2, ProgramStage.F3,
        ProgramStage.F4, ProgramStage.F5, f6, ProgramStage.F7, ProgramStage.F8,
    )


def full_lifecycle_sequence(route: Route) -> tuple[ProgramStage, ...]:
    """The governed F0→F12 development-lifecycle sequence for a route."""
    return full_stage_sequence(route) + (
        ProgramStage.F9,
        ProgramStage.F10,
        ProgramStage.F11,
        ProgramStage.F12,
    )


# Evidence states admissible to satisfy a preclinical (F3-F8) experimental gate.
# MODEL_PREDICTION is deliberately excluded.
_PRECLINICAL_ADMISSIBLE = frozenset(
    {
        ClaimState.OBSERVED,
        ClaimState.SUPPORTED_INFERENCE,
        ClaimState.EXPERIMENTALLY_VALIDATED,
        ClaimState.TRANSLATIONALLY_VALIDATED,
    }
)


@dataclass(frozen=True)
class PreclinicalGateVerdict:
    passed: bool
    blockers: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "blockers": list(self.blockers)}


class PreclinicalGatePolicy:
    """Deterministic F3-F8 admissibility gate (model output never passes)."""

    def __init__(self, *, route: Route, tpp_digest: str) -> None:
        self.route = route
        self.tpp_digest = tpp_digest

    def evaluate(self, decisive_claim_states: list[ClaimState], *, stage: ProgramStage) -> PreclinicalGateVerdict:
        blockers: list[str] = []
        for state in decisive_claim_states:
            if state is ClaimState.MODEL_PREDICTION and state not in _PRECLINICAL_ADMISSIBLE:
                blockers.append(
                    "model prediction claim cannot satisfy a preclinical experimental gate"
                )
            elif state in {ClaimState.CONTRADICTED, ClaimState.UNKNOWN}:
                blockers.append(f"{state.value} decisive claim blocks advancement")
        if not decisive_claim_states:
            blockers.append("no decisive claims supplied")
        if stage == ProgramStage.F6A and self.route not in RESCUE_ROUTES:
            blockers.append(f"route {self.route.value} does not advance through F6A")
        if stage == ProgramStage.F6B and self.route not in DE_NOVO_ROUTES:
            blockers.append(f"route {self.route.value} does not advance through F6B")
        if stage == ProgramStage.F6C and self.route not in COMBINATION_ROUTES:
            blockers.append(f"route {self.route.value} does not advance through F6C")
        # F9-F12 are development-lifecycle gates (regulatory/clinical); they are
        # route-agnostic and always model-output-excluding.
        return PreclinicalGateVerdict(not blockers, tuple(blockers))
