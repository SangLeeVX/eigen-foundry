"""M8 — F12 registration/externalization/lifecycle/termination workflows.

F12 is the terminal development lifecycle gate: a program may be
registered/nominated, externalized (licensed/acquired/partnered), or
terminated — per the governed disposition, never implying a real therapeutic
outcome (dry run only).

Each workflow is deterministic, content-addressed, and emits a traceable packet.
Termination/externalization never imply real registration, licensing, or
therapeutic advancement.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from .models import Disposition, ProgramRecord, utc_now


class F12Outcome(StrEnum):
    REGISTRATION = "REGISTRATION"
    EXTERNALIZE_LICENSE = "EXTERNALIZE_LICENSE"
    EXTERNALIZE_ACQUIRE = "EXTERNALIZE_ACQUIRE"
    EXTERNALIZE_PARTNER = "EXTERNALIZE_PARTNER"
    TERMINATE = "TERMINATE"
    SPINOUT = "SPINOUT"


def _canon(data: dict[str, Any]) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
    )


@dataclass
class TerminationWorkflow:
    """Deterministic F12 disposition workflow (dry run; never a real outcome)."""

    # Disposition -> F12 outcome mapping.
    OUTCOME_BY_DISPOSITION: dict = field(
        default_factory=lambda: {
            Disposition.LICENSE_OR_ACQUIRE: F12Outcome.EXTERNALIZE_LICENSE,
            Disposition.PARTNER: F12Outcome.EXTERNALIZE_PARTNER,
            Disposition.SPINOUT: F12Outcome.SPINOUT,
            Disposition.TERMINATE: F12Outcome.TERMINATE,
        }
    )

    def execute(
        self,
        *,
        workflow_id: str,
        program: ProgramRecord,
        disposition: Disposition,
        terms_ref,
    ) -> dict[str, Any]:
        """Record the F12 disposition workflow and emit a traceable packet."""
        outcome = self.OUTCOME_BY_DISPOSITION.get(disposition)
        if outcome is None:
            # REGISTRATION is the natural F12 completion (not a disposition action).
            outcome = F12Outcome.REGISTRATION
        packet = {
            "workflow_id": workflow_id,
            "program_id": program.program_id,
            "final_stage": program.stage.value,
            "disposition": disposition.value,
            "outcome": outcome.value,
            "terms_ref": terms_ref.model_dump(mode="json") if terms_ref is not None else None,
            "registered_at": utc_now().isoformat(),
        }
        return {
            "workflow_id": workflow_id,
            "program_id": program.program_id,
            "disposition": disposition.value,
            "outcome": outcome.value,
            "packet_digest": _canon(packet),
            "harness_only": True,
            "real_therapeutic_advance": False,
        }
