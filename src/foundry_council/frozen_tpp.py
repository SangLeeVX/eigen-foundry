"""M7 — frozen Target Product Profile (TPP) anchor.

A TPP is the frozen target profile that anchors the entire F0–F8 preclinical dry
run. Both the existing-asset/rescue and de novo routes complete their governed
dry runs AGAINST THE SAME frozen TPP; no gate may rewrite it during the run.

The TPP is immutable and content-addressed (SnapshotRef + digest), third-party
readable, and independent of any program.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .models import SnapshotRef, StableId, utc_now


def _cannoness(data: dict[str, Any]) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
    )


@dataclass(frozen=True)
class FrozenTPP:
    """Immutable target product profile anchoring a preclinical dry run."""

    tpp_id: StableId
    indication: str
    patient_segment: str
    primary_endpoint: str
    target_product_label: str
    key_specs: dict[str, Any]
    version: int = 1
    created_at: datetime = field(default_factory=utc_now)

    @property
    def digest(self) -> str:
        return _cannoness(
            {
                "tpp_id": self.tpp_id,
                "indication": self.indication,
                "patient_segment": self.patient_segment,
                "primary_endpoint": self.primary_endpoint,
                "target_product_label": self.target_product_label,
                "key_specs": self.key_specs,
                "version": self.version,
            }
        )

    def snapshot_ref(self) -> SnapshotRef:
        return SnapshotRef(
            object_id=self.tpp_id,
            version=self.version,
            digest=self.digest,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tpp_id": self.tpp_id,
            "indication": self.indication,
            "patient_segment": self.patient_segment,
            "primary_endpoint": self.primary_endpoint,
            "target_product_label": self.target_product_label,
            "key_specs": self.key_specs,
            "version": self.version,
            "digest": self.digest,
        }


def make_frozen_tpp(*, tpp_id: str = "TPP-CRC-001", seed: int = 7) -> FrozenTPP:
    """Build a deterministic frozen TPP for the M7 dry run."""
    return FrozenTPP(
        tpp_id=tpp_id,
        indication=f"Community colorectal cancer (CRC) indication set (seed {seed})",
        patient_segment=f"Adult CRC cohort (seed {seed})",
        primary_endpoint="Confirmed objective response (synthetic dry-run endpoint)",
        target_product_label="Synthetic nominated candidate for third-party review (dry run)",
        key_specs={
            "minimum_response_rate": 0.25,
            "maximum_serious_ae_rate": 0.10,
            "dosing": "QD oral (synthetic)",
            "route_of_administration": "oral",
        },
    )
