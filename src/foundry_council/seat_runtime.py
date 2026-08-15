"""M4 — Working Conclave seat runtime.

Binds a ParticipantAssignment (a council seat) to a versioned model identity and
executes that seat under three M4 invariants that introduce NO new authority over
the existing council kernel:

  1. Distinct run identity — every seat uses its assigned run_id/model_version/
     prompt_version; a seat cannot impersonate another seat or reuse another run.
  2. Bounded tool envelope — a seat may only call tools within its declared
     envelope; anything outside fails closed.
  3. Structured-output validation — a seat's output must conform to the expected
     schema contract before it is accepted; malformed output fails closed.

The runtime never changes formal Program state: it only produces candidate
structured outputs (claims, opinions, challenges, case determinations) that the
governed council service subsequently validates, challenges, and commits through
the existing restricted commit path.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Protocol

from .models import (
    ActorKind,
    CaseStatus,
    CaseType,
    ParticipantAssignment,
    StableId,
)
from .service import CommandContext


class SeatAlreadyBound(Exception):
    """The seat is already bound to a run identity."""


class SeatNotBound(Exception):
    """No run identity is bound to this seat."""


class ToolOutsideEnvelope(Exception):
    """The seat attempted to call a tool outside its declared envelope."""


class MalformedSeatOutput(Exception):
    """The seat produced output that failed structured-output validation."""


# Tool categories a council seat may invoke (bounded, read-only/proposal only;
# none of these can change formal Program state).
TOOL_READ_EVIDENCE = "read_evidence"
TOOL_READ_CLAIMS = "read_claims"
TOOL_PROPOSE_CLAIM = "propose_claim"
TOOL_PROPOSE_OPINION = "propose_opinion"
TOOL_PROPOSE_CHALLENGE = "propose_challenge"
TOOL_PROPOSE_CASE = "propose_case"
TOOL_READ_PROGRAM = "read_program"

# Envelope per standing role. Propose-tools are proposal-only (never commit).
ROLE_ENVELOPES: dict[str, frozenset[str]] = {
    "foundry_commander": frozenset(
        {TOOL_READ_EVIDENCE, TOOL_READ_CLAIMS, TOOL_READ_PROGRAM}
    ),
    "evidence_steward": frozenset({TOOL_READ_EVIDENCE, TOOL_READ_PROGRAM}),
    "program_architect": frozenset(
        {TOOL_READ_EVIDENCE, TOOL_READ_CLAIMS, TOOL_PROPOSE_CLAIM, TOOL_READ_PROGRAM}
    ),
    "independent_red_team": frozenset(
        {TOOL_READ_EVIDENCE, TOOL_READ_CLAIMS, TOOL_PROPOSE_CHALLENGE, TOOL_READ_PROGRAM}
    ),
    "independent_reviewer": frozenset(
        {TOOL_READ_EVIDENCE, TOOL_READ_CLAIMS, TOOL_PROPOSE_CHALLENGE, TOOL_READ_PROGRAM}
    ),
    "policy_arbiter": frozenset({TOOL_READ_EVIDENCE, TOOL_READ_CLAIMS, TOOL_READ_PROGRAM}),
    "case_captain": frozenset(
        {
            TOOL_READ_EVIDENCE,
            TOOL_READ_CLAIMS,
            TOOL_PROPOSE_CLAIM,
            TOOL_PROPOSE_OPINION,
            TOOL_PROPOSE_CASE,
            TOOL_READ_PROGRAM,
        }
    ),
}


def _canonical(*, assignment: ParticipantAssignment, seed: int) -> str:
    material = "|".join(
        [
            assignment.assignment_id,
            assignment.actor_id,
            assignment.actor_kind.value,
            assignment.role,
            assignment.run_id or "",
            assignment.model_version or "",
            assignment.prompt_version or "",
            str(seed),
        ]
    )
    return hashlib.sha256(material.encode()).hexdigest()


@dataclass(frozen=True)
class SeatOutput:
    """A validated, schema-conformant structured output from a seat."""

    kind: str  # e.g. "claim" | "opinion" | "challenge" | "final_case"
    run_id: str | None
    model_version: str | None
    content: dict[str, Any]


@dataclass
class BoundSeat:
    """A ParticipantAssignment bound to a versioned model identity + envelope."""

    assignment: ParticipantAssignment
    envelope: frozenset[str]
    run_digest: str
    _bound: bool = True

    def bind_digest(self, seed: int) -> str:
        """Deterministic run identity digest for verifiable attribution."""
        return _canonical(assignment=self.assignment, seed=seed)


class LiteralSeatModel(Protocol):
    """A callable that turns a prompt+context into a raw model response string."""

    def run(self, prompt: str, context: dict[str, Any]) -> str: ...


class DeterministicSeatModel:
    """Deterministic mock seat model for the synthetic/working Conclave harness.

    Produces a canonical JSON structured output derived from the context (data,
    not model authority). Used to validate the runtime + envelope + output
    contract end-to-end without a live model.
    """

    def __init__(self, response_template: dict[str, Any]) -> None:
        self._template = response_template

    def run(self, prompt: str, context: dict[str, Any]) -> str:
        import json

        return json.dumps(self._template, sort_keys=True)


class SeatRuntime:
    """Binds a council seat to a versioned model identity and executes it."""

    def __init__(self, seat: BoundSeat, model: LiteralSeatModel) -> None:
        self.seat = seat
        self.model = model

    def call_tool(self, tool: str) -> None:
        """Validate that a tool is inside the seat's bounded envelope."""
        if tool not in self.seat.envelope:
            raise ToolOutsideEnvelope(
                f"seat role '{self.seat.assignment.role}' has no tool '{tool}' in its envelope"
            )

    def produce(
        self,
        prompt: str,
        context: dict[str, Any],
        *,
        expected_kind: str,
        required_fields: tuple[str, ...],
        seed: int = 0,
    ) -> SeatOutput:
        """Run the model and validate its structured output against the contract.

        Fails closed (MalformedSeatOutput) if the output is not a JSON object or
        misses required fields. Never escalates authority.
        """
        raw = self.model.run(prompt, context)
        try:
            import json

            content = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise MalformedSeatOutput("seat output is not valid JSON") from exc
        if not isinstance(content, dict):
            raise MalformedSeatOutput("seat output must be a JSON object")
        missing = [f for f in required_fields if f not in content]
        if missing:
            raise MalformedSeatOutput(
                f"seat output missing required fields: {missing}"
            )
        return SeatOutput(
            kind=expected_kind,
            run_id=self.seat.assignment.run_id,
            model_version=self.seat.assignment.model_version,
            content=content,
        )

    def command(
        self,
        *,
        expected_version: int | None,
        actor_kind: ActorKind | None = None,
        roles: frozenset[str] = frozenset(),
    ) -> CommandContext:
        """Build the CommandContext for this seat, stamped with its run identity.

        The returned context asserts only the seat's own actor/roles — never
        another role or any authority beyond the assignment.
        """
        assignment = self.seat.assignment
        return CommandContext(
            actor_id=assignment.actor_id,
            actor_kind=actor_kind or assignment.actor_kind,
            idempotency_key=f"{assignment.run_id or assignment.assignment_id}:{self.seat.run_digest}",
            expected_version=expected_version,
            reason=f"Working Conclave seat run {assignment.run_id or assignment.assignment_id} (model {assignment.model_version or 'n/a'})",
            principal_roles=frozenset({assignment.role}),
        )


def bind_seat(assignment: ParticipantAssignment, *, seed: int = 0) -> BoundSeat:
    """Bind a participant assignment to a seat with its role envelope + run digest."""
    envelope = ROLE_ENVELOPES.get(assignment.role, frozenset())
    if not envelope:
        # Unknown roles get no tools (fail-closed); only declared roles act.
        envelope = frozenset()
    return BoundSeat(
        assignment=assignment,
        envelope=envelope,
        run_digest=_canonical(assignment=assignment, seed=seed),
    )
