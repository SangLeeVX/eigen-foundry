#!/usr/bin/env python3
"""Validate committed Forge state with the Python standard library."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CHECKPOINTS = ROOT / "forge" / "state" / "checkpoints.json"
WORK_ITEMS = ROOT / "forge" / "work-items"

PHASE_STATES = {"PENDING", "IN_PROGRESS", "BLOCKED", "COMPLETED"}
CRITERION_STATES = {"PENDING", "BLOCKED", "VERIFIED"}
WORK_STATES = {
    "DRAFT",
    "READY",
    "CLAIMED",
    "IMPLEMENTED",
    "VALIDATED",
    "REVIEWED",
    "MERGE_APPROVED",
    "DONE",
    "BLOCKED",
    "FAILED",
    "SUPERSEDED",
}


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path.relative_to(ROOT)}: invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path.relative_to(ROOT)}: top level must be an object")
    return value


def require(mapping: dict[str, Any], names: tuple[str, ...], context: str) -> None:
    missing = [name for name in names if name not in mapping]
    if missing:
        raise ValueError(f"{context}: missing {', '.join(missing)}")


def validate_checkpoints() -> set[str]:
    doc = load_json(CHECKPOINTS)
    require(doc, ("schema_version", "project", "canonical_repository", "phases"), "checkpoints")
    if not isinstance(doc["phases"], list) or not doc["phases"]:
        raise ValueError("checkpoints: phases must be a non-empty array")

    phase_ids: set[str] = set()
    checkpoint_ids: set[str] = set()
    for phase in doc["phases"]:
        if not isinstance(phase, dict):
            raise ValueError("checkpoints: each phase must be an object")
        require(phase, ("phase_id", "checkpoint_id", "status", "exit_criteria"), "phase")
        phase_id = phase["phase_id"]
        if phase_id in phase_ids:
            raise ValueError(f"checkpoints: duplicate phase_id {phase_id}")
        phase_ids.add(phase_id)
        checkpoint_ids.add(phase["checkpoint_id"])
        if phase["status"] not in PHASE_STATES:
            raise ValueError(f"{phase_id}: invalid status {phase['status']}")
        criteria = phase["exit_criteria"]
        if not isinstance(criteria, list) or not criteria:
            raise ValueError(f"{phase_id}: exit_criteria must be non-empty")
        criterion_ids: set[str] = set()
        for criterion in criteria:
            require(criterion, ("criterion_id", "description", "status", "evidence"), phase_id)
            criterion_id = criterion["criterion_id"]
            if criterion_id in criterion_ids:
                raise ValueError(f"{phase_id}: duplicate criterion_id {criterion_id}")
            criterion_ids.add(criterion_id)
            if criterion["status"] not in CRITERION_STATES:
                raise ValueError(f"{criterion_id}: invalid status {criterion['status']}")
            if not isinstance(criterion["evidence"], list):
                raise ValueError(f"{criterion_id}: evidence must be an array")
            if criterion["status"] == "VERIFIED" and not criterion["evidence"]:
                raise ValueError(f"{criterion_id}: VERIFIED requires durable evidence")
        if phase["status"] == "COMPLETED" and any(
            item["status"] != "VERIFIED" for item in criteria
        ):
            raise ValueError(f"{phase_id}: COMPLETED requires every criterion VERIFIED")
    return checkpoint_ids


def validate_work_item(path: Path, checkpoint_ids: set[str]) -> None:
    item = load_json(path)
    require(
        item,
        (
            "schema_version",
            "work_item_id",
            "phase_id",
            "title",
            "status",
            "objective",
            "allowed_paths",
            "forbidden_actions",
            "acceptance_criteria",
            "validation_commands",
            "risk_class",
            "retry_policy",
            "review",
            "checkpoint_id",
        ),
        path.name,
    )
    if not re.fullmatch(r"FWI-P[0-9]+-[0-9]{3}", item["work_item_id"]):
        raise ValueError(f"{path.name}: invalid work_item_id")
    if item["status"] not in WORK_STATES:
        raise ValueError(f"{path.name}: invalid status {item['status']}")
    if item["checkpoint_id"] not in checkpoint_ids:
        raise ValueError(f"{path.name}: unknown checkpoint_id {item['checkpoint_id']}")
    if not item["allowed_paths"] or not item["acceptance_criteria"]:
        raise ValueError(f"{path.name}: paths and acceptance criteria must be non-empty")
    if item["review"].get("author_may_approve") is not False:
        raise ValueError(f"{path.name}: author_may_approve must be false")
    if item["review"].get("independent_required") is not True:
        raise ValueError(f"{path.name}: independent review must be required")
    attempts = item["retry_policy"].get("max_attempts")
    if not isinstance(attempts, int) or not 0 <= attempts <= 5:
        raise ValueError(f"{path.name}: max_attempts must be between 0 and 5")
    if item["status"] in {"VALIDATED", "REVIEWED", "MERGE_APPROVED", "DONE"}:
        for criterion in item["acceptance_criteria"]:
            if criterion.get("status") != "VERIFIED" or not criterion.get("evidence"):
                raise ValueError(f"{path.name}: validated states require criterion evidence")


def main() -> int:
    errors: list[str] = []
    try:
        checkpoint_ids = validate_checkpoints()
    except ValueError as exc:
        errors.append(str(exc))
        checkpoint_ids = set()

    for schema in sorted((ROOT / "forge" / "contracts").glob("*.json")):
        try:
            load_json(schema)
        except ValueError as exc:
            errors.append(str(exc))

    for path in sorted(WORK_ITEMS.glob("*.json")):
        try:
            validate_work_item(path, checkpoint_ids)
        except ValueError as exc:
            errors.append(str(exc))

    if not list(WORK_ITEMS.glob("*.json")):
        errors.append("forge/work-items: at least one work item is required")

    if errors:
        print("Forge contract validation failed:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print("Forge contract validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
