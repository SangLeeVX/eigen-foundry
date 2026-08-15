#!/usr/bin/env python3
"""Validate committed Forge milestone and work-item state with the standard library."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CHECKPOINTS = ROOT / "forge" / "state" / "checkpoints.json"
WORK_ITEMS = ROOT / "forge" / "work-items"
CHECKPOINT_SCHEMA = ROOT / "forge" / "contracts" / "checkpoints.schema.json"
WORK_ITEM_SCHEMA = ROOT / "forge" / "contracts" / "work-item.schema.json"

MILESTONE_STATES = {"NOT_STARTED", "PARTIAL", "IN_PROGRESS", "BLOCKED", "COMPLETED"}
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
MANDATE_ROLES = {
    "BUILD_MANDATE_OWNER",
    "ENGINEERING_AUTHORITY",
    "FOUNDRY_GOVERNANCE_SAFETY_AUTHORITY",
}
EXPECTED_PLAN_SHA256 = (
    "sha256:91956a8194ed22697c8c013115d766f097143e6ed376da8b87cd504163ee7e45"
)
EXPECTED_APPROVED_REPOSITORY_HEAD = "ae1e6acebfde703312b882302c4137bfd06faa9e"
EXPECTED_AUTHORING_RUN_ID = "foundry-codex-20260814-master-plan"
EXPECTED_APPROVED_AT = "2026-08-14T10:53:39Z"
EXPECTED_APPROVAL_EVIDENCE = (
    "https://github.com/SangLeeVX/eigen-foundry/issues/17#issuecomment-5292491688"
)
EXPECTED_APPROVAL_IDS = {
    "BUILD_MANDATE_OWNER": "APR-BUILD-EIGEN-FOUNDRY-003-MANDATE",
    "ENGINEERING_AUTHORITY": "APR-BUILD-EIGEN-FOUNDRY-003-ENGINEERING",
    "FOUNDRY_GOVERNANCE_SAFETY_AUTHORITY": "APR-BUILD-EIGEN-FOUNDRY-003-GOVERNANCE",
}
EXPECTED_APPROVAL_CONDITIONS = (
    "Invalidates on a material change to the bound plan or reviewed repository head."
)
EXPECTED_REVIEW_ID = "github-pr-review:4936490163"
EXPECTED_REVIEWER_RUN_ID = "/root/master_plan_review"
EXPECTED_REVIEW_EVIDENCE = (
    "https://github.com/SangLeeVX/eigen-foundry/pull/18#pullrequestreview-4936490163"
)
EXPECTED_LEGACY_SCHEMA_SHA256 = (
    "sha256:a88c4abce157a16a676d1ce3be0738900f59fdbaea62c80b78a994f7f90048aa"
)
EXPECTED_LEGACY_STATE_SHA256 = (
    "sha256:996d5d73344e63535f5a8d77b42c10046d7dac1a56251a1916b518414359ddf6"
)
EXPECTED_LEGACY_SCHEMA_PATH = "forge/contracts/legacy/checkpoints-v1.schema.json"
EXPECTED_LEGACY_STATE_PATH = "forge/state/legacy/checkpoints-v1.0.0.json"
EXPECTED_MIGRATION_ID = "MIGRATE-P-CHECKPOINTS-TO-M-v2"
EXPECTED_MIGRATION_RUN_ID = "foundry-codex-20260814-m0-checkpoint-migration"
EXPECTED_MIGRATION_CREATED_AT = "2026-08-14T10:55:49Z"
EXPECTED_PROJECT = "Eigen Foundry"
EXPECTED_CANONICAL_REPOSITORY = "SangLeeVX/eigen-foundry"
EXPECTED_MANDATE_DOCUMENT_PATH = "FOUNDRY_MASTER_BUILD_PLAN.md"
EXPECTED_BLUEPRINT_LIBRARY_PATH = (
    "/Drug Foundry w eigenfield/EIGEN_FOUNDRY_EXECUTION_BLUEPRINT_v0.2.md"
)
EXPECTED_CHECKPOINT_SCHEMA_SHA256 = (
    "sha256:57d4ffd64a6ddce66113f1810180fb47130425c5cbe790621e00169469152552"
)
EXPECTED_WORK_ITEM_SCHEMA_SHA256 = (
    "sha256:bd2d526fd1e0c88d8b175483c4e5bbaae66f34633ea431fa9cf45bfd6c048ec6"
)
EXPECTED_MILESTONE_SPEC_SHA256 = (
    "sha256:7fef941b7ca145afb239b46147109d0a1f7a5cf8c98bf8f038a806cde91994ee"
)
EXPECTED_MILESTONES = tuple(f"M{index}" for index in range(10))
EXPECTED_DEPENDENCIES = {
    milestone: ([] if index == 0 else [f"M{index - 1}"])
    for index, milestone in enumerate(EXPECTED_MILESTONES)
}
EXPECTED_MAXIMUM_CLAIMS = {
    "M0": "NO_PRODUCT_CLAIM",
    "M1": "TECHNICAL_FOUNDATION",
    "M2": "TECHNICAL_FOUNDATION",
    "M3": "SYNTHETIC_CONCLAVE_HARNESS",
    "M4": "WORKING_CONCLAVE",
    "M5": "WORKING_FOUNDRY_MVP",
    "M6": "WORKING_FOUNDRY_MVP",
    "M7": "WORKING_FOUNDRY_MVP",
    "M8": "WORKING_FOUNDRY_MVP",
    "M9": "PRODUCTION_QUALIFIED_FOUNDRY",
}
EXPECTED_CROSSWALK = {
    "P0": ("LEGACY_ENGINEERING_PHASE", ["M1"]),
    "P1": ("LEGACY_ENGINEERING_PHASE", ["M1"]),
    "P2": ("LEGACY_ENGINEERING_PHASE", ["M1"]),
    "P3": ("LEGACY_ENGINEERING_PHASE", ["M2"]),
    "P4": ("LEGACY_ENGINEERING_PHASE", ["M3", "M4"]),
    "P5": ("LEGACY_ENGINEERING_PHASE", ["M6"]),
    "P6": ("LEGACY_ENGINEERING_PHASE", ["M5"]),
    "P7": ("LEGACY_ENGINEERING_PHASE", ["M7", "M9"]),
    "B0": ("LEGACY_BUILD_FOUNDATION", ["M1"]),
    "B0.5": ("LEGACY_BUILD_FOUNDATION", ["M1"]),
    "B1": ("LEGACY_BUILD_FOUNDATION", ["M1"]),
    "B2": ("LEGACY_BUILD_FOUNDATION", ["M1"]),
    "LEGACY_BUILD_F1": ("LEGACY_BUILD_INCREMENT", ["M2"]),
    "LEGACY_BUILD_F2": ("LEGACY_BUILD_INCREMENT", ["M3"]),
    "LEGACY_BUILD_F3": ("LEGACY_BUILD_INCREMENT", ["M4"]),
    "LEGACY_BUILD_F4": ("LEGACY_BUILD_INCREMENT", ["M6"]),
    "LEGACY_BUILD_F5": ("LEGACY_BUILD_INCREMENT", ["M6"]),
    "LEGACY_BUILD_F6": ("LEGACY_BUILD_INCREMENT", ["M6"]),
    "LEGACY_BUILD_F7": ("LEGACY_BUILD_INCREMENT", ["M5"]),
    "LEGACY_BUILD_F8": ("LEGACY_BUILD_INCREMENT", ["M5", "M7"]),
    "LEGACY_BUILD_F9": ("LEGACY_BUILD_INCREMENT", ["M7"]),
    "LEGACY_BUILD_F10": ("LEGACY_BUILD_INCREMENT", ["M7"]),
}
EXPECTED_PROGRAM_GATE_COVERAGE = {
    "PROGRAM_GATE_F0_F2": "M6",
    "PROGRAM_GATE_F3_F8": "M7",
    "PROGRAM_GATE_F9_F12": "M8",
}
EXPECTED_BLUEPRINT_SHA256 = (
    "sha256:78f386f1132a57824a879c77ffce611b7bf2726619e7a68a32b576fac6f03b9c"
)
EXPECTED_BLOCKERS = {
    "BLK-M1-KEY-ROTATION": {
        "milestone_id": "M1",
        "criterion_ids": ["M1-C5"],
        "type": "SECURITY",
        "description": (
            "Credentials previously exposed in chat must be revoked, rotated, and "
            "installed by an authorized human before live integration."
        ),
        "owner": "authorized credential owner",
    },
    "BLK-M1-REPO-POLICY": {
        "milestone_id": "M1",
        "criterion_ids": ["M1-C3"],
        "type": "AUTHORITY",
        "description": (
            "Private repository visibility, branch protection, required checks, and "
            "merge policy require repository-admin verification."
        ),
        "owner": "repository administrator",
    },
}
EXPECTED_PENDING_CRITERION_STATES = {
    "M0-C1": "VERIFIED",
    "M0-C2": "VERIFIED",
    "M1-C5": "BLOCKED",
}
EXPECTED_M0_EVIDENCE = {
    "M0-C1": [
        {
            "evidence_id": "EVD-M0-C1-HUMAN-APPROVAL",
            "type": "GITHUB_ISSUE_COMMENT",
            "locator": EXPECTED_APPROVAL_EVIDENCE,
            "immutable_revision": "issuecomment-5292491688",
            "bound_criterion_id": "M0-C1",
            "result": "APPROVED",
        }
    ],
    "M0-C2": [
        {
            "evidence_id": "EVD-M0-C2-INDEPENDENT-REVIEW",
            "type": "GITHUB_PR_REVIEW",
            "locator": EXPECTED_REVIEW_EVIDENCE,
            "immutable_revision": (
                "pullrequestreview-4936490163@"
                "ae1e6acebfde703312b882302c4137bfd06faa9e"
            ),
            "bound_criterion_id": "M0-C2",
            "result": "PASS",
        }
    ],
}
ALLOWED_EVIDENCE_TYPES = {
    "GITHUB_ISSUE_COMMENT",
    "GITHUB_PR_REVIEW",
    "GITHUB_MERGE_COMMIT",
    "GITHUB_ACTION_RUN",
    "SECURITY_REPORT",
    "SIGNED_APPROVAL",
    "RELEASE_EVIDENCE",
}
ROOT_KEYS = {
    "schema_version",
    "project",
    "canonical_repository",
    "classification",
    "updated_at",
    "active_build_mandate",
    "migration",
    "milestones",
    "blockers",
}


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        try:
            relative = path.relative_to(ROOT)
        except ValueError:
            relative = path
        raise ValueError(f"{relative}: invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path}: top level must be an object")
    return value


def require(mapping: dict[str, Any], names: tuple[str, ...], context: str) -> None:
    missing = [name for name in names if name not in mapping]
    if missing:
        raise ValueError(f"{context}: missing {', '.join(missing)}")


def reject_extras(mapping: dict[str, Any], allowed: set[str], context: str) -> None:
    extras = sorted(set(mapping) - allowed)
    if extras:
        raise ValueError(f"{context}: unexpected fields {', '.join(extras)}")


def parse_datetime(value: Any, context: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{context}: expected RFC3339 date-time")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{context}: invalid RFC3339 date-time") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{context}: date-time requires timezone")
    return parsed


def file_sha256(path: Path) -> str:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"{path}: required bound artifact is unavailable") from exc
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def load_bound_schema(path: Path, expected_sha256: str) -> dict[str, Any]:
    if file_sha256(path) != expected_sha256:
        raise ValueError(f"{path.name}: approved schema digest was substituted")
    return load_json(path)


def schema_type_matches(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    raise ValueError(f"schema: unsupported type keyword {expected}")


def validate_schema_instance(value: Any, schema: dict[str, Any], context: str) -> None:
    """Apply the JSON-Schema subset used by the committed Forge contracts."""

    expected_type = schema.get("type")
    if expected_type is not None:
        candidates = expected_type if isinstance(expected_type, list) else [expected_type]
        if not all(isinstance(item, str) for item in candidates) or not any(
            schema_type_matches(value, item) for item in candidates
        ):
            raise ValueError(f"{context}: schema type constraint failed")

    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{context}: schema const constraint failed")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{context}: schema enum constraint failed")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise ValueError(f"{context}: schema minLength constraint failed")
        pattern = schema.get("pattern")
        if pattern is not None and re.search(pattern, value) is None:
            raise ValueError(f"{context}: schema pattern constraint failed")
        if schema.get("format") == "date-time":
            parse_datetime(value, context)

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise ValueError(f"{context}: schema minItems constraint failed")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ValueError(f"{context}: schema maxItems constraint failed")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                validate_schema_instance(item, item_schema, f"{context}[{index}]")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            raise ValueError(f"{context}: schema missing {', '.join(missing)}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = sorted(set(value) - set(properties))
            if extras:
                raise ValueError(f"{context}: schema unexpected fields {', '.join(extras)}")
        for name, child_schema in properties.items():
            if name in value:
                validate_schema_instance(value[name], child_schema, f"{context}.{name}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"{context}: schema minimum constraint failed")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"{context}: schema maximum constraint failed")


def safe_repository_path(root: Path, value: Any, context: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context}: path must be non-empty")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{context}: path must stay inside the repository")
    return root / relative


def validate_active_mandate(mandate: dict[str, Any], root: Path) -> None:
    required = (
        "mandate_id",
        "version",
        "document_path",
        "plan_sha256",
        "approved_repository_head",
        "authoring_run_id",
        "status",
        "approved_at",
        "approvals",
        "independent_review",
    )
    require(mandate, required, "active_build_mandate")
    reject_extras(mandate, set(required), "active_build_mandate")
    if mandate["mandate_id"] != "BUILD-EIGEN-FOUNDRY-003" or mandate["version"] != "1.0":
        raise ValueError("active_build_mandate: unexpected mandate identity")
    if mandate["document_path"] != EXPECTED_MANDATE_DOCUMENT_PATH:
        raise ValueError("active_build_mandate: approved document path was substituted")
    if mandate["plan_sha256"] != EXPECTED_PLAN_SHA256:
        raise ValueError("active_build_mandate: approved plan digest was substituted")
    if mandate["approved_repository_head"] != EXPECTED_APPROVED_REPOSITORY_HEAD:
        raise ValueError("active_build_mandate: approved repository head was substituted")
    if mandate["authoring_run_id"] != EXPECTED_AUTHORING_RUN_ID:
        raise ValueError("active_build_mandate: authoring run identity was substituted")
    if mandate["approved_at"] != EXPECTED_APPROVED_AT:
        raise ValueError("active_build_mandate: approval timestamp was substituted")
    if mandate["status"] not in {"APPROVED_PENDING_ACTIVATION", "ACTIVE", "SUPERSEDED"}:
        raise ValueError("active_build_mandate: invalid status")
    if not re.fullmatch(r"[0-9a-f]{40}", mandate["approved_repository_head"]):
        raise ValueError("active_build_mandate: invalid approved repository head")
    parse_datetime(mandate["approved_at"], "active_build_mandate.approved_at")
    plan_path = safe_repository_path(root, mandate["document_path"], "active_build_mandate.document_path")
    if file_sha256(plan_path) != mandate["plan_sha256"]:
        raise ValueError("active_build_mandate: plan digest does not match repository bytes")

    approvals = mandate["approvals"]
    if not isinstance(approvals, list) or len(approvals) != 3:
        raise ValueError("active_build_mandate: exactly three functional approvals are required")
    roles: set[str] = set()
    approval_ids: set[str] = set()
    for approval in approvals:
        if not isinstance(approval, dict):
            raise ValueError("active_build_mandate: approval must be an object")
        required_approval = (
            "approval_id",
            "approver",
            "actor_ref",
            "actor_kind",
            "role",
            "decision",
            "approved_at",
            "expires_at",
            "conditions",
            "bound_plan_sha256",
            "bound_repository_head",
            "evidence",
        )
        require(approval, required_approval, "active_build_mandate.approval")
        reject_extras(approval, set(required_approval), "active_build_mandate.approval")
        if approval["approval_id"] in approval_ids:
            raise ValueError("active_build_mandate: duplicate approval_id")
        approval_ids.add(approval["approval_id"])
        role = approval["role"]
        if role not in MANDATE_ROLES:
            raise ValueError("active_build_mandate: unrecognized functional approval role")
        roles.add(role)
        if approval["actor_kind"] != "HUMAN" or approval["decision"] != "APPROVED":
            raise ValueError("active_build_mandate: approvals must be human APPROVED decisions")
        if approval["approval_id"] != EXPECTED_APPROVAL_IDS[role]:
            raise ValueError("active_build_mandate: functional approval identity was substituted")
        if approval["approver"] != "Sang H. Lee" or approval["actor_ref"] != "github:SangLeeVX":
            raise ValueError("active_build_mandate: approver identity was substituted")
        if approval["approved_at"] != EXPECTED_APPROVED_AT:
            raise ValueError("active_build_mandate: functional approval timestamp was substituted")
        if approval["bound_plan_sha256"] != mandate["plan_sha256"]:
            raise ValueError("active_build_mandate: approval is bound to the wrong plan digest")
        if approval["bound_repository_head"] != mandate["approved_repository_head"]:
            raise ValueError("active_build_mandate: approval is bound to the wrong repository head")
        parse_datetime(approval["approved_at"], f"{approval['approval_id']}.approved_at")
        expires_at = approval["expires_at"]
        if expires_at is not None:
            if parse_datetime(expires_at, f"{approval['approval_id']}.expires_at") <= datetime.now(
                timezone.utc
            ):
                raise ValueError("active_build_mandate: an approval is expired")
            raise ValueError("active_build_mandate: approval expiry was substituted")
        if approval["conditions"] != EXPECTED_APPROVAL_CONDITIONS:
            raise ValueError("active_build_mandate: approval conditions were substituted")
        evidence = approval["evidence"]
        if evidence != EXPECTED_APPROVAL_EVIDENCE:
            raise ValueError("active_build_mandate: approval evidence locator was substituted")
    if roles != MANDATE_ROLES:
        raise ValueError("active_build_mandate: required functional approval roles are incomplete")

    review = mandate["independent_review"]
    if not isinstance(review, dict):
        raise ValueError("active_build_mandate: independent_review must be an object")
    review_fields = {
        "review_id",
        "reviewer_run_id",
        "authoring_run_id",
        "verdict",
        "reviewed_repository_head",
        "evidence",
    }
    require(review, tuple(review_fields), "active_build_mandate.independent_review")
    reject_extras(review, review_fields, "active_build_mandate.independent_review")
    if review["verdict"] != "PASS":
        raise ValueError("active_build_mandate: independent review must pass")
    if review["review_id"] != EXPECTED_REVIEW_ID:
        raise ValueError("active_build_mandate: independent review identity was substituted")
    if review["reviewer_run_id"] != EXPECTED_REVIEWER_RUN_ID:
        raise ValueError("active_build_mandate: reviewer run identity was substituted")
    if review["authoring_run_id"] != mandate["authoring_run_id"]:
        raise ValueError("active_build_mandate: review authoring run mismatch")
    if review["reviewer_run_id"] == review["authoring_run_id"]:
        raise ValueError("active_build_mandate: authoring run cannot review itself")
    if review["reviewed_repository_head"] != mandate["approved_repository_head"]:
        raise ValueError("active_build_mandate: review is bound to the wrong repository head")
    if review["evidence"] != EXPECTED_REVIEW_EVIDENCE:
        raise ValueError("active_build_mandate: independent review locator was substituted")


def legacy_criterion_ids(legacy_state: dict[str, Any]) -> dict[str, list[str]]:
    phases = legacy_state.get("phases")
    if not isinstance(phases, list):
        raise ValueError("migration source: archived state has no phases")
    result: dict[str, list[str]] = {}
    for phase in phases:
        phase_id = phase.get("phase_id")
        criteria = phase.get("exit_criteria")
        if not isinstance(phase_id, str) or not isinstance(criteria, list):
            raise ValueError("migration source: malformed legacy phase")
        result[phase_id] = [item.get("criterion_id") for item in criteria]
    if list(result) != [f"P{index}" for index in range(8)]:
        raise ValueError("migration source: archived phases must be exactly P0-P7")
    if any(not all(isinstance(item, str) for item in items) for items in result.values()):
        raise ValueError("migration source: malformed legacy criterion")
    return result


def validate_migration(migration: dict[str, Any], root: Path) -> None:
    required = {
        "migration_id",
        "created_at",
        "actor_run_id",
        "status",
        "status_inheritance",
        "therapeutic_program_state_changes",
        "source",
        "blueprint",
        "crosswalk",
        "program_gate_coverage",
    }
    require(migration, tuple(required), "migration")
    reject_extras(migration, required, "migration")
    parse_datetime(migration["created_at"], "migration.created_at")
    if migration["created_at"] != EXPECTED_MIGRATION_CREATED_AT:
        raise ValueError("migration: creation timestamp was substituted")
    if migration["migration_id"] != EXPECTED_MIGRATION_ID:
        raise ValueError("migration: migration identity was substituted")
    if migration["actor_run_id"] != EXPECTED_MIGRATION_RUN_ID:
        raise ValueError("migration: actor run identity was substituted")
    if migration["status"] not in {"PENDING_PROTECTED_MERGE", "ACTIVE", "SUPERSEDED"}:
        raise ValueError("migration: invalid status")
    if migration["status_inheritance"] is not False:
        raise ValueError("migration: legacy status inheritance is forbidden")
    if migration["therapeutic_program_state_changes"] is not False:
        raise ValueError("migration: software migration cannot change therapeutic Program state")

    source = migration["source"]
    source_fields = {
        "schema_version",
        "schema_sha256",
        "state_sha256",
        "archived_schema_path",
        "archived_state_path",
    }
    require(source, tuple(source_fields), "migration.source")
    reject_extras(source, source_fields, "migration.source")
    if source["schema_version"] != "1.0.0":
        raise ValueError("migration.source: unsupported source schema")
    if source["schema_sha256"] != EXPECTED_LEGACY_SCHEMA_SHA256:
        raise ValueError("migration.source: approved legacy schema digest was substituted")
    if source["state_sha256"] != EXPECTED_LEGACY_STATE_SHA256:
        raise ValueError("migration.source: approved legacy state digest was substituted")
    if source["archived_schema_path"] != EXPECTED_LEGACY_SCHEMA_PATH:
        raise ValueError("migration.source: archived schema path was substituted")
    if source["archived_state_path"] != EXPECTED_LEGACY_STATE_PATH:
        raise ValueError("migration.source: archived state path was substituted")
    archive_schema = safe_repository_path(root, source["archived_schema_path"], "migration.source")
    archive_state = safe_repository_path(root, source["archived_state_path"], "migration.source")
    if file_sha256(archive_schema) != source["schema_sha256"]:
        raise ValueError("migration.source: archived schema digest mismatch")
    if file_sha256(archive_state) != source["state_sha256"]:
        raise ValueError("migration.source: archived state digest mismatch")
    legacy_criteria = legacy_criterion_ids(load_json(archive_state))

    blueprint = migration["blueprint"]
    blueprint_fields = {"document_id", "version", "library_path", "sha256"}
    require(blueprint, tuple(blueprint_fields), "migration.blueprint")
    reject_extras(blueprint, blueprint_fields, "migration.blueprint")
    if (
        blueprint["document_id"] != "BUILD-EIGEN-FOUNDRY-002"
        or blueprint["version"] != "0.2"
        or blueprint["sha256"] != EXPECTED_BLUEPRINT_SHA256
        or blueprint["library_path"] != EXPECTED_BLUEPRINT_LIBRARY_PATH
    ):
        raise ValueError("migration.blueprint: identity, digest, or locator mismatch")

    crosswalk = migration["crosswalk"]
    if not isinstance(crosswalk, list):
        raise ValueError("migration.crosswalk: expected an array")
    by_source: dict[str, dict[str, Any]] = {}
    for entry in crosswalk:
        if not isinstance(entry, dict):
            raise ValueError("migration.crosswalk: entry must be an object")
        fields = {
            "source_id",
            "namespace",
            "target_milestones",
            "treatment",
            "status_inherited",
            "criterion_routes",
        }
        require(entry, tuple(fields), "migration.crosswalk")
        reject_extras(entry, fields, "migration.crosswalk")
        source_id = entry["source_id"]
        if not isinstance(source_id, str):
            raise ValueError("migration.crosswalk: source_id must be a string")
        if source_id in by_source:
            raise ValueError(f"migration.crosswalk: duplicate source {source_id}")
        if re.fullmatch(r"F[0-9]+", str(source_id)):
            raise ValueError("migration.crosswalk: naked F identifiers are forbidden")
        by_source[source_id] = entry
    if set(by_source) != set(EXPECTED_CROSSWALK):
        missing = sorted(set(EXPECTED_CROSSWALK) - set(by_source))
        extra = sorted(set(by_source) - set(EXPECTED_CROSSWALK))
        raise ValueError(f"migration.crosswalk: incomplete inventory; missing={missing}, extra={extra}")

    routed_legacy_criteria: list[str] = []
    for source_id, (namespace, targets) in EXPECTED_CROSSWALK.items():
        entry = by_source[source_id]
        if entry["namespace"] != namespace or entry["target_milestones"] != targets:
            raise ValueError(f"migration.crosswalk: incorrect mapping for {source_id}")
        expected_treatment = "SPLIT" if len(targets) > 1 else "CARRIED"
        if entry["treatment"] != expected_treatment:
            raise ValueError(f"migration.crosswalk: incorrect treatment for {source_id}")
        if entry["status_inherited"] is not False:
            raise ValueError(f"migration.crosswalk: {source_id} inherits legacy status")
        routes = entry["criterion_routes"]
        if not isinstance(routes, list):
            raise ValueError(f"migration.crosswalk: criterion_routes must be an array for {source_id}")
        for route in routes:
            if not isinstance(route, dict):
                raise ValueError(f"migration.crosswalk: malformed criterion route for {source_id}")
            route_fields = {"source_criterion_id", "target_milestones"}
            require(route, tuple(route_fields), "migration.crosswalk.criterion_route")
            reject_extras(route, route_fields, "migration.crosswalk.criterion_route")
            if not isinstance(route["target_milestones"], list):
                raise ValueError(f"migration.crosswalk: criterion targets must be an array for {source_id}")
        if source_id.startswith("P"):
            expected_ids = legacy_criteria[source_id]
            route_ids = [route.get("source_criterion_id") for route in routes]
            if route_ids != expected_ids:
                raise ValueError(f"migration.crosswalk: criterion loss or duplication for {source_id}")
            for route in routes:
                if not set(route.get("target_milestones", [])).issubset(set(targets)):
                    raise ValueError(f"migration.crosswalk: criterion escapes targets for {source_id}")
                routed_legacy_criteria.append(route["source_criterion_id"])
            if source_id == "P4" and routes[0]["target_milestones"] != ["M3"]:
                raise ValueError("migration.crosswalk: P4 mock criterion cannot satisfy M4")
            for route in routes:
                expected_route = ["M3"] if route["source_criterion_id"] == "P4-C1" else targets
                if route["target_milestones"] != expected_route:
                    raise ValueError(
                        "migration.crosswalk: exact legacy criterion route was changed"
                    )
        elif routes:
            raise ValueError(f"migration.crosswalk: {source_id} has invented legacy criteria")
    archived_criterion_inventory = [
        criterion_id for phase in legacy_criteria.values() for criterion_id in phase
    ]
    if sorted(routed_legacy_criteria) != sorted(archived_criterion_inventory):
        raise ValueError("migration.crosswalk: every legacy criterion must appear exactly once")

    coverage = migration["program_gate_coverage"]
    if not isinstance(coverage, list):
        raise ValueError("migration.program_gate_coverage: expected an array")
    coverage_map: dict[str, str] = {}
    for item in coverage:
        if not isinstance(item, dict):
            raise ValueError("migration.program_gate_coverage: entry must be an object")
        coverage_fields = {"source_id", "target_milestone", "changes_program_state"}
        require(item, tuple(coverage_fields), "migration.program_gate_coverage")
        reject_extras(item, coverage_fields, "migration.program_gate_coverage")
        if item.get("changes_program_state") is not False:
            raise ValueError("migration.program_gate_coverage: cannot change Program state")
        source_id = item.get("source_id")
        if source_id in coverage_map:
            raise ValueError("migration.program_gate_coverage: duplicate source")
        coverage_map[source_id] = item.get("target_milestone")
    if coverage_map != EXPECTED_PROGRAM_GATE_COVERAGE:
        raise ValueError("migration.program_gate_coverage: incomplete or incorrect F0-F12 coverage")


def validate_evidence(evidence: Any, criterion_id: str) -> None:
    if not isinstance(evidence, dict):
        raise ValueError(f"{criterion_id}: evidence must be an object")
    required = {
        "evidence_id",
        "type",
        "locator",
        "immutable_revision",
        "bound_criterion_id",
        "result",
    }
    require(evidence, tuple(required), criterion_id)
    reject_extras(evidence, required, criterion_id)
    if not isinstance(evidence["evidence_id"], str) or not evidence["evidence_id"].strip():
        raise ValueError(f"{criterion_id}: evidence_id is required")
    if not isinstance(evidence["type"], str) or evidence["type"] not in ALLOWED_EVIDENCE_TYPES:
        raise ValueError(f"{criterion_id}: unsupported evidence type")
    locator = evidence["locator"]
    if (
        not isinstance(locator, str)
        or not locator.strip()
        or locator.startswith(("local:", "chat:", "draft-pr:"))
    ):
        raise ValueError(f"{criterion_id}: evidence is not durable")
    revision = evidence["immutable_revision"]
    github_patterns = {
        "GITHUB_ISSUE_COMMENT": (
            r"https://github\.com/SangLeeVX/eigen-foundry/issues/[0-9]+#issuecomment-(?P<id>[0-9]+)",
            r"issuecomment-(?P<id>[0-9]+)",
        ),
        "GITHUB_PR_REVIEW": (
            r"https://github\.com/SangLeeVX/eigen-foundry/pull/[0-9]+#pullrequestreview-(?P<id>[0-9]+)",
            r"pullrequestreview-(?P<id>[0-9]+)@[0-9a-f]{40}",
        ),
        "GITHUB_MERGE_COMMIT": (
            r"https://github\.com/SangLeeVX/eigen-foundry/commit/(?P<id>[0-9a-f]{40})",
            r"(?P<id>[0-9a-f]{40})",
        ),
        "GITHUB_ACTION_RUN": (
            r"https://github\.com/SangLeeVX/eigen-foundry/actions/runs/(?P<id>[0-9]+)",
            r"run-(?P<id>[0-9]+)@[0-9a-f]{40}",
        ),
    }
    if evidence["type"] in github_patterns:
        locator_pattern, revision_pattern = github_patterns[evidence["type"]]
        locator_match = re.fullmatch(locator_pattern, locator)
        revision_match = (
            re.fullmatch(revision_pattern, revision) if isinstance(revision, str) else None
        )
        if (
            locator_match is None
            or revision_match is None
            or locator_match.group("id") != revision_match.group("id")
        ):
            raise ValueError(f"{criterion_id}: GitHub evidence is not exact-revision addressable")
    elif (
        not locator.startswith("https://github.com/SangLeeVX/eigen-foundry/")
        or not isinstance(revision, str)
        or re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}|[0-9a-f]{40}", revision) is None
    ):
        raise ValueError(f"{criterion_id}: evidence is not exact-revision addressable")
    if evidence["bound_criterion_id"] != criterion_id:
        raise ValueError(f"{criterion_id}: evidence is bound to another criterion")
    if evidence["result"] not in {"PASS", "APPROVED", "VERIFIED"}:
        raise ValueError(f"{criterion_id}: invalid evidence result")


def validate_milestones(
    milestones: Any,
) -> tuple[dict[str, dict[str, Any]], dict[str, str], set[str]]:
    if not isinstance(milestones, list):
        raise ValueError("checkpoints: milestones must be an array")
    ids = [item.get("milestone_id") for item in milestones if isinstance(item, dict)]
    if ids != list(EXPECTED_MILESTONES):
        raise ValueError("checkpoints: milestones must be exactly ordered M0-M9")
    specification = [
        {
            "milestone_id": item.get("milestone_id"),
            "name": item.get("name"),
            "maximum_claim_on_completion": item.get("maximum_claim_on_completion"),
            "criteria": [
                {
                    "criterion_id": criterion.get("criterion_id"),
                    "description": criterion.get("description"),
                }
                for criterion in item.get("exit_criteria", [])
                if isinstance(criterion, dict)
            ],
        }
        for item in milestones
        if isinstance(item, dict)
    ]
    specification_sha256 = "sha256:" + hashlib.sha256(
        json.dumps(specification, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if specification_sha256 != EXPECTED_MILESTONE_SPEC_SHA256:
        raise ValueError("checkpoints: approved milestone criterion specification changed")
    milestone_map: dict[str, dict[str, Any]] = {}
    criterion_owner: dict[str, str] = {}
    checkpoint_ids: set[str] = set()
    milestone_fields = {
        "milestone_id",
        "checkpoint_id",
        "name",
        "status",
        "dependencies",
        "maximum_claim_on_completion",
        "exit_criteria",
    }
    for milestone in milestones:
        milestone_id = milestone["milestone_id"]
        require(milestone, tuple(milestone_fields), milestone_id)
        reject_extras(milestone, milestone_fields, milestone_id)
        if milestone["checkpoint_id"] != f"CP-{milestone_id}":
            raise ValueError(f"{milestone_id}: checkpoint_id mismatch")
        if milestone["checkpoint_id"] in checkpoint_ids:
            raise ValueError(f"{milestone_id}: duplicate checkpoint_id")
        checkpoint_ids.add(milestone["checkpoint_id"])
        if milestone["status"] not in MILESTONE_STATES:
            raise ValueError(f"{milestone_id}: invalid milestone status")
        if not isinstance(milestone["name"], str) or not milestone["name"].strip():
            raise ValueError(f"{milestone_id}: name is required")
        if not isinstance(milestone["dependencies"], list):
            raise ValueError(f"{milestone_id}: dependencies must be an array")
        if milestone["dependencies"] != EXPECTED_DEPENDENCIES[milestone_id]:
            raise ValueError(f"{milestone_id}: dependencies do not match approved graph")
        if milestone["maximum_claim_on_completion"] != EXPECTED_MAXIMUM_CLAIMS[milestone_id]:
            raise ValueError(f"{milestone_id}: capability claim exceeds approved label")
        criteria = milestone["exit_criteria"]
        if not isinstance(criteria, list) or not criteria:
            raise ValueError(f"{milestone_id}: exit_criteria must be non-empty")
        local_ids: set[str] = set()
        for criterion in criteria:
            if not isinstance(criterion, dict):
                raise ValueError(f"{milestone_id}: criterion must be an object")
            fields = {"criterion_id", "description", "status", "evidence"}
            require(criterion, tuple(fields), milestone_id)
            reject_extras(criterion, fields, milestone_id)
            criterion_id = criterion.get("criterion_id")
            if not isinstance(criterion_id, str) or not re.fullmatch(
                rf"{milestone_id}-C[0-9]+", criterion_id
            ):
                raise ValueError(f"{milestone_id}: malformed criterion_id")
            if criterion_id in local_ids or criterion_id in criterion_owner:
                raise ValueError(f"{milestone_id}: duplicate criterion_id {criterion_id}")
            local_ids.add(criterion_id)
            criterion_owner[criterion_id] = milestone_id
            if not isinstance(criterion["description"], str) or not criterion["description"].strip():
                raise ValueError(f"{criterion_id}: description is required")
            if criterion.get("status") not in CRITERION_STATES:
                raise ValueError(f"{criterion_id}: invalid criterion status")
            evidence = criterion.get("evidence")
            if not isinstance(evidence, list):
                raise ValueError(f"{criterion_id}: evidence must be an array")
            for item in evidence:
                validate_evidence(item, criterion_id)
            if criterion["status"] == "VERIFIED" and not evidence:
                raise ValueError(f"{criterion_id}: VERIFIED requires durable evidence")
        milestone_map[milestone_id] = milestone
    return milestone_map, criterion_owner, checkpoint_ids


def validate_blockers(
    blockers: Any,
    milestone_map: dict[str, dict[str, Any]],
    criterion_owner: dict[str, str],
) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(blockers, list):
        raise ValueError("checkpoints: blockers must be an array")
    by_milestone = {milestone: [] for milestone in milestone_map}
    blocker_ids: set[str] = set()
    fields = {
        "blocker_id",
        "milestone_id",
        "criterion_ids",
        "type",
        "description",
        "owner",
        "status",
        "evidence",
    }
    for blocker in blockers:
        if not isinstance(blocker, dict):
            raise ValueError("checkpoints: blocker must be an object")
        require(blocker, tuple(fields), "blocker")
        reject_extras(blocker, fields, "blocker")
        blocker_id = blocker.get("blocker_id")
        if blocker_id in blocker_ids:
            raise ValueError("checkpoints: duplicate blocker_id")
        blocker_ids.add(blocker_id)
        milestone_id = blocker.get("milestone_id")
        if milestone_id not in milestone_map:
            raise ValueError(f"{blocker_id}: unknown milestone_id")
        criterion_ids = blocker.get("criterion_ids")
        if not isinstance(criterion_ids, list) or not criterion_ids:
            raise ValueError(f"{blocker_id}: criterion_ids must be non-empty")
        if any(criterion_owner.get(item) != milestone_id for item in criterion_ids):
            raise ValueError(f"{blocker_id}: blocker references absent or foreign criterion")
        if blocker.get("status") not in {"OPEN", "RESOLVED"}:
            raise ValueError(f"{blocker_id}: invalid blocker status")
        if blocker.get("type") not in {"AUTHORITY", "SECURITY", "DEPENDENCY", "TECHNICAL"}:
            raise ValueError(f"{blocker_id}: invalid blocker type")
        if not isinstance(blocker.get("description"), str) or not blocker["description"].strip():
            raise ValueError(f"{blocker_id}: description is required")
        if not isinstance(blocker.get("owner"), str) or not blocker["owner"].strip():
            raise ValueError(f"{blocker_id}: owner is required")
        if not isinstance(blocker.get("evidence"), list):
            raise ValueError(f"{blocker_id}: evidence must be an array")
        evidence_criteria: set[str] = set()
        for evidence in blocker["evidence"]:
            bound_id = evidence.get("bound_criterion_id") if isinstance(evidence, dict) else ""
            if bound_id not in criterion_ids:
                raise ValueError(f"{blocker_id}: evidence binds an unrelated criterion")
            validate_evidence(evidence, bound_id)
            evidence_criteria.add(bound_id)
        if blocker["status"] == "RESOLVED" and not blocker.get("evidence"):
            raise ValueError(f"{blocker_id}: resolved blocker requires durable evidence")
        if blocker["status"] == "RESOLVED" and evidence_criteria != set(criterion_ids):
            raise ValueError(f"{blocker_id}: resolved blocker lacks criterion-bound evidence")
        by_milestone[milestone_id].append(blocker)
    if set(blocker_ids) != set(EXPECTED_BLOCKERS):
        raise ValueError("checkpoints: required blocker inventory changed")
    for blocker in blockers:
        expected = EXPECTED_BLOCKERS[blocker["blocker_id"]]
        for field, value in expected.items():
            if blocker[field] != value:
                raise ValueError(
                    f"{blocker['blocker_id']}: approved blocker binding changed"
                )
    return by_milestone


def validate_completion(
    milestone_map: dict[str, dict[str, Any]],
    blockers_by_milestone: dict[str, list[dict[str, Any]]],
    migration_status: str,
) -> None:
    if migration_status == "PENDING_PROTECTED_MERGE" and any(
        milestone["status"] == "COMPLETED" for milestone in milestone_map.values()
    ):
        raise ValueError("checkpoints: migration PR cannot complete a milestone")
    for milestone_id, milestone in milestone_map.items():
        if milestone["status"] != "COMPLETED":
            continue
        if any(item["status"] != "VERIFIED" for item in milestone["exit_criteria"]):
            raise ValueError(f"{milestone_id}: COMPLETED requires every criterion VERIFIED")
        if any(
            milestone_map[dependency]["status"] != "COMPLETED"
            for dependency in milestone["dependencies"]
        ):
            raise ValueError(f"{milestone_id}: COMPLETED requires completed dependencies")
        if any(
            blocker["status"] == "OPEN" for blocker in blockers_by_milestone[milestone_id]
        ):
            raise ValueError(f"{milestone_id}: COMPLETED is blocked by an open blocker")


def validate_checkpoints(
    document: dict[str, Any] | None = None,
    root: Path = ROOT,
) -> set[str]:
    doc = load_json(root / "forge" / "state" / "checkpoints.json") if document is None else document
    if not isinstance(doc, dict):
        raise ValueError("checkpoints: top level must be an object")
    schema = load_bound_schema(CHECKPOINT_SCHEMA, EXPECTED_CHECKPOINT_SCHEMA_SHA256)
    validate_schema_instance(doc, schema, "checkpoints")
    require(doc, tuple(ROOT_KEYS), "checkpoints")
    reject_extras(doc, ROOT_KEYS, "checkpoints")
    if doc["schema_version"] != "2.0.0":
        raise ValueError("checkpoints: schema_version must be 2.0.0")
    if doc["classification"] != "NONCANONICAL_ENGINEERING_STATE":
        raise ValueError("checkpoints: invalid classification")
    if doc["project"] != EXPECTED_PROJECT:
        raise ValueError("checkpoints: project authority was substituted")
    if doc["canonical_repository"] != EXPECTED_CANONICAL_REPOSITORY:
        raise ValueError("checkpoints: canonical repository was substituted")
    parse_datetime(doc["updated_at"], "checkpoints.updated_at")
    validate_active_mandate(doc["active_build_mandate"], root)
    validate_migration(doc["migration"], root)
    milestone_map, criterion_owner, checkpoint_ids = validate_milestones(doc["milestones"])
    blockers = validate_blockers(doc["blockers"], milestone_map, criterion_owner)
    validate_completion(milestone_map, blockers, doc["migration"]["status"])
    for criterion in doc["milestones"][0]["exit_criteria"]:
        criterion_id = criterion["criterion_id"]
        if criterion_id in EXPECTED_M0_EVIDENCE and criterion["evidence"] != EXPECTED_M0_EVIDENCE[
            criterion_id
        ]:
            raise ValueError("checkpoints: mandate evidence binding changed")
    mandate_status = doc["active_build_mandate"]["status"]
    migration_status = doc["migration"]["status"]
    if migration_status == "PENDING_PROTECTED_MERGE" and mandate_status != "APPROVED_PENDING_ACTIVATION":
        raise ValueError("checkpoints: pending migration requires pending-activation mandate")
    if migration_status == "ACTIVE" and mandate_status != "ACTIVE":
        raise ValueError("checkpoints: active migration requires active mandate")
    if "SUPERSEDED" in {migration_status, mandate_status}:
        raise ValueError("checkpoints: supersession requires a successor-evidence schema")
    if migration_status == "PENDING_PROTECTED_MERGE":
        expected_statuses = ["IN_PROGRESS", "PARTIAL"] + ["NOT_STARTED"] * 8
        actual_statuses = [item["status"] for item in doc["milestones"]]
        if actual_statuses != expected_statuses:
            raise ValueError("checkpoints: pending migration milestone statuses changed")
        for milestone in doc["milestones"]:
            for criterion in milestone["exit_criteria"]:
                criterion_id = criterion["criterion_id"]
                expected_status = EXPECTED_PENDING_CRITERION_STATES.get(
                    criterion_id, "PENDING"
                )
                if criterion["status"] != expected_status:
                    raise ValueError("checkpoints: pending migration criterion status changed")
                if criterion_id not in EXPECTED_M0_EVIDENCE and criterion["evidence"]:
                    raise ValueError("checkpoints: pending migration introduced premature evidence")
        if any(
            blocker["status"] != "OPEN" or blocker["evidence"]
            for blocker in doc["blockers"]
        ):
            raise ValueError("checkpoints: pending migration falsely resolved a required blocker")
    if migration_status == "ACTIVE":
        if doc["milestones"][0]["status"] != "COMPLETED":
            raise ValueError("checkpoints: activation requires completed M0 merge evidence")
        m0_criteria = {
            item["criterion_id"]: item for item in doc["milestones"][0]["exit_criteria"]
        }
        plan_merge_evidence = m0_criteria["M0-C3"]["evidence"]
        if len(plan_merge_evidence) != 1 or plan_merge_evidence[0]["type"] != "GITHUB_MERGE_COMMIT":
            raise ValueError("checkpoints: M0-C3 requires the approved plan merge commit")
        migration_evidence = m0_criteria["M0-C4"]["evidence"]
        evidence_by_type = {item["type"]: item for item in migration_evidence}
        if len(migration_evidence) != 2 or set(evidence_by_type) != {
            "GITHUB_MERGE_COMMIT",
            "GITHUB_ACTION_RUN",
        }:
            raise ValueError("checkpoints: M0-C4 requires migration merge and exact-head CI")
        migration_merge_sha = evidence_by_type["GITHUB_MERGE_COMMIT"]["immutable_revision"]
        if evidence_by_type["GITHUB_ACTION_RUN"]["immutable_revision"].split("@")[-1] != migration_merge_sha:
            raise ValueError("checkpoints: M0-C4 CI is not bound to the migration merge")
        for milestone in doc["milestones"][1:]:
            if milestone["status"] in {"PARTIAL", "IN_PROGRESS", "BLOCKED", "COMPLETED"}:
                if any(
                    next(item for item in doc["milestones"] if item["milestone_id"] == dependency)[
                        "status"
                    ]
                    != "COMPLETED"
                    for dependency in milestone["dependencies"]
                ):
                    raise ValueError(
                        f"{milestone['milestone_id']}: work cannot start before dependencies complete"
                    )
    return checkpoint_ids


def validate_work_item(path: Path, checkpoint_ids: set[str]) -> str:
    item = load_json(path)
    schema = load_bound_schema(WORK_ITEM_SCHEMA, EXPECTED_WORK_ITEM_SCHEMA_SHA256)
    validate_schema_instance(item, schema, path.name)
    required = {
        "schema_version",
        "work_item_id",
        "milestone_id",
        "title",
        "status",
        "source",
        "objective",
        "dependencies",
        "allowed_paths",
        "forbidden_actions",
        "acceptance_criteria",
        "validation_commands",
        "risk_class",
        "retry_policy",
        "claim",
        "review",
        "protected_actions",
        "checkpoint_id",
    }
    optional = {"legacy_phase_id"}
    require(item, tuple(required), path.name)
    reject_extras(item, required | optional, path.name)
    if item["schema_version"] != "2.0.0":
        raise ValueError(f"{path.name}: schema_version must be 2.0.0")
    if not re.fullmatch(r"FWI-(M[0-9]|P[0-9]+)-[0-9]{3}", item["work_item_id"]):
        raise ValueError(f"{path.name}: invalid work_item_id")
    if path.stem != item["work_item_id"]:
        raise ValueError(f"{path.name}: filename must equal work_item_id")
    legacy_id = item["work_item_id"].startswith("FWI-P")
    if legacy_id and not re.fullmatch(r"P[0-7]", str(item.get("legacy_phase_id"))):
        raise ValueError(f"{path.name}: legacy work item requires legacy_phase_id")
    if not legacy_id and "legacy_phase_id" in item:
        raise ValueError(f"{path.name}: new work item cannot claim a legacy phase")
    if not re.fullmatch(r"M[0-9]", item["milestone_id"]):
        raise ValueError(f"{path.name}: invalid milestone_id")
    if item["checkpoint_id"] != f"CP-{item['milestone_id']}":
        raise ValueError(f"{path.name}: checkpoint does not match milestone")
    if item["checkpoint_id"] not in checkpoint_ids:
        raise ValueError(f"{path.name}: unknown checkpoint_id {item['checkpoint_id']}")
    id_scope = item["work_item_id"].split("-")[1]
    if not legacy_id and id_scope != item["milestone_id"]:
        raise ValueError(f"{path.name}: work_item_id milestone does not match milestone_id")
    if legacy_id:
        if id_scope != item["legacy_phase_id"]:
            raise ValueError(f"{path.name}: legacy work_item_id does not match legacy_phase_id")
        allowed_legacy_targets = {
            "P0": {"M1"},
            "P1": {"M1"},
            "P2": {"M1"},
            "P3": {"M2"},
            "P4": {"M3", "M4"},
            "P5": {"M6"},
            "P6": {"M5"},
            "P7": {"M7", "M9"},
        }
        if item["milestone_id"] not in allowed_legacy_targets[item["legacy_phase_id"]]:
            raise ValueError(f"{path.name}: legacy work item targets an invalid milestone")
    if item["status"] not in WORK_STATES:
        raise ValueError(f"{path.name}: invalid status {item['status']}")
    if not item["allowed_paths"] or not item["acceptance_criteria"]:
        raise ValueError(f"{path.name}: paths and acceptance criteria must be non-empty")
    if item["review"].get("author_may_approve") is not False:
        raise ValueError(f"{path.name}: author_may_approve must be false")
    if item["review"].get("independent_required") is not True:
        raise ValueError(f"{path.name}: independent review must be required")
    attempts = item["retry_policy"].get("max_attempts")
    if not isinstance(attempts, int) or not 0 <= attempts <= 5:
        raise ValueError(f"{path.name}: max_attempts must be between 0 and 5")
    if item["retry_policy"].get("idempotent_only") is not True:
        raise ValueError(f"{path.name}: retries must be idempotent only")
    claim = item.get("claim")
    if item["status"] not in {"DRAFT", "READY"} and claim is None:
        raise ValueError(f"{path.name}: active work state requires claimed identity")
    if claim is not None:
        claimed_at = parse_datetime(claim.get("claimed_at"), f"{path.name}.claim.claimed_at")
        expires_at = parse_datetime(claim.get("expires_at"), f"{path.name}.claim.expires_at")
        if expires_at <= claimed_at:
            raise ValueError(f"{path.name}: claim expiry must follow claim time")
    criterion_ids: set[str] = set()
    for criterion in item["acceptance_criteria"]:
        if not isinstance(criterion, dict):
            raise ValueError(f"{path.name}: acceptance criterion must be an object")
        require(
            criterion,
            ("criterion_id", "description", "status", "evidence"),
            f"{path.name}.acceptance_criteria",
        )
        criterion_id = criterion.get("criterion_id")
        if not isinstance(criterion_id, str) or re.fullmatch(
            rf"{re.escape(item['work_item_id'])}-A[0-9]+", criterion_id
        ) is None:
            raise ValueError(f"{path.name}: acceptance criterion_id does not match work item")
        if criterion_id in criterion_ids:
            raise ValueError(f"{path.name}: duplicate acceptance criterion")
        criterion_ids.add(criterion_id)
        if criterion.get("status") not in CRITERION_STATES:
            raise ValueError(f"{path.name}: invalid acceptance status")
        evidence = criterion.get("evidence")
        if not isinstance(evidence, list):
            raise ValueError(f"{path.name}: acceptance evidence must be an array")
        for locator in evidence:
            if locator.startswith(("local:", "chat:", "draft-pr:")):
                raise ValueError(f"{path.name}: acceptance evidence is not durable")
            if not (
                locator.startswith("https://github.com/SangLeeVX/eigen-foundry/")
                or re.fullmatch(r"sha256:[0-9a-f]{64}", locator)
            ):
                raise ValueError(f"{path.name}: acceptance evidence is not addressable")
        if criterion["status"] == "VERIFIED" and not evidence:
            raise ValueError(f"{path.name}: verified acceptance requires evidence")
    if item["status"] in {"VALIDATED", "REVIEWED", "MERGE_APPROVED", "DONE"}:
        for criterion in item["acceptance_criteria"]:
            if criterion.get("status") != "VERIFIED" or not criterion.get("evidence"):
                raise ValueError(f"{path.name}: validated states require criterion evidence")
    if item["status"] in {"REVIEWED", "MERGE_APPROVED", "DONE"} and not item["review"][
        "review_evidence"
    ]:
        raise ValueError(f"{path.name}: reviewed states require independent review evidence")
    for locator in item["review"]["review_evidence"]:
        if not locator.startswith("https://github.com/SangLeeVX/eigen-foundry/"):
            raise ValueError(f"{path.name}: review evidence is not addressable")
    return item["work_item_id"]


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
    for schema in sorted((ROOT / "forge" / "contracts" / "legacy").glob("*.json")):
        try:
            load_json(schema)
        except ValueError as exc:
            errors.append(str(exc))

    work_items = sorted(WORK_ITEMS.glob("*.json"))
    work_item_ids: set[str] = set()
    for path in work_items:
        try:
            work_item_id = validate_work_item(path, checkpoint_ids)
            if work_item_id in work_item_ids:
                raise ValueError(f"{path.name}: duplicate work_item_id")
            work_item_ids.add(work_item_id)
        except ValueError as exc:
            errors.append(str(exc))
    if not work_items:
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
