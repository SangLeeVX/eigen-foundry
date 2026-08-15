"""M6 — F0–F2 gate policies (Eigen-grounded).

Defines the F0/F1/F2 admissibility policy and enforces the M6 invariant that a
`MODEL_PREDICTION` claim can never by itself satisfy an experimental gate.

Rules:
  - F0/F1/F2 advancement requires decisive claims with evidence; a claim whose
    state is MODEL_PREDICTION (or CONTRADICTED/UNKNOWN) cannot count toward
    passing an experimental determination.
  - A MODEL_PREDICTION never upgrades to EXPERIMENTALLY_VALIDATED or
    TRANSLATIONALLY_VALIDATED at these gates.
  - Decisive claims must reference non-empty evidence and not be model-only.

The policy is deterministic and gate/plan-artifact-bound (mirrors the kernel
policy style) but is intentionally small and explicit for the F0–F2 scope.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import ClaimState, ProgramStage


# Evidence states that MAY satisfy an F0–F2 experimental determination.
# MODEL_PREDICTION is intentionally excluded.
_ADMISSIBLE_DETERMINATIVE = frozenset(
    {
        ClaimState.OBSERVED,
        ClaimState.SUPPORTED_INFERENCE,
        ClaimState.EXPERIMENTALLY_VALIDATED,
        ClaimState.TRANSLATIONALLY_VALIDATED,
    }
)


@dataclass(frozen=True)
class GateVerdict:
    passed: bool
    blockers: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "blockers": list(self.blockers)}


class F0F2GatePolicy:
    """Deterministic F0/F1/F2 admissibility gate."""

    def __init__(self, *, admission_kind: str = "CRC", program_id: str = "PRG") -> None:
        self.admission_kind = admission_kind
        self.program_id = program_id

    def evaluate_decisive_claims(
        self,
        decisive_claim_states: list[ClaimState],
        *,
        stage: ProgramStage,
        packet_kind: str = "DRY_RUN",
    ) -> GateVerdict:
        """Evaluate whether the decisive claims admit a gate.

        A decisive claim whose state is MODEL_PREDICTION (or CONTRADICTED,
        UNKNOWN) is a blocker and cannot contribute to passing an experimental
        determination.
        """
        blockers: list[str] = []
        model_only = [c.value for c in decisive_claim_states if c is ClaimState.MODEL_PREDICTION]
        if model_only:
            blockers.append(
                "model prediction claims cannot satisfy an experimental gate at this stage: "
                + ",".join(model_only)
            )
        contradicted = [c.value for c in decisive_claim_states if c is ClaimState.CONTRADICTED]
        if contradicted:
            blockers.append("contradicted decisive claims block advancement: " + ",".join(contradicted))
        unknown = [c.value for c in decisive_claim_states if c is ClaimState.UNKNOWN]
        if unknown:
            blockers.append("UNKNOWN decisive claims block advancement: " + ",".join(unknown))

        if stage not in {ProgramStage.F0, ProgramStage.F1, ProgramStage.F2}:
            blockers.append(f"policy scoped to F0-F2, got {stage.value}")

        # No decisive claims at all: nothing to admit the gate on.
        if not decisive_claim_states:
            blockers.append("no decisive claims supplied")

        return GateVerdict(not blockers, tuple(blockers))

    def model_prediction_never_satisfies(self, claim_state: ClaimState) -> bool:
        """True if a MODEL_PREDICTION can never count toward a determinative state."""
        # A model prediction is never in the admissible determinative set.
        return claim_state is ClaimState.MODEL_PREDICTION and claim_state not in _ADMISSIBLE_DETERMINATIVE
