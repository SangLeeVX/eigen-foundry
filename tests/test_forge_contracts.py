from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from forge.validate_contracts import (
    CHECKPOINTS,
    EXPECTED_BLUEPRINT_SHA256,
    EXPECTED_CROSSWALK,
    EXPECTED_MILESTONES,
    EXPECTED_PROGRAM_GATE_COVERAGE,
    ROOT,
    WORK_ITEMS,
    file_sha256,
    validate_checkpoints,
    validate_evidence,
    validate_work_item,
)


def load(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def durable_evidence(criterion_id: str, suffix: str = "test"):
    return {
        "evidence_id": f"EVD-{criterion_id}-{suffix}",
        "type": "GITHUB_ACTION_RUN",
        "locator": "https://github.com/SangLeeVX/eigen-foundry/actions/runs/1",
        "immutable_revision": f"run-1@{'a' * 40}",
        "bound_criterion_id": criterion_id,
        "result": "PASS",
    }


def merge_evidence(criterion_id: str, revision: str, suffix: str = "test"):
    return {
        "evidence_id": f"EVD-{criterion_id}-{suffix}",
        "type": "GITHUB_MERGE_COMMIT",
        "locator": f"https://github.com/SangLeeVX/eigen-foundry/commit/{revision}",
        "immutable_revision": revision,
        "bound_criterion_id": criterion_id,
        "result": "VERIFIED",
    }


def materialize_bound_artifacts(root: Path) -> None:
    for relative in (
        "FOUNDRY_MASTER_BUILD_PLAN.md",
        "forge/contracts/legacy/checkpoints-v1.schema.json",
        "forge/state/legacy/checkpoints-v1.0.0.json",
    ):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / relative).read_bytes())


def activate(document):
    document["migration"]["status"] = "ACTIVE"
    document["active_build_mandate"]["status"] = "ACTIVE"


def verify_milestone(document, milestone_id: str):
    milestone = next(
        item for item in document["milestones"] if item["milestone_id"] == milestone_id
    )
    for criterion in milestone["exit_criteria"]:
        criterion["status"] = "VERIFIED"
        if not criterion["evidence"]:
            if criterion["criterion_id"] == "M0-C3":
                criterion["evidence"] = [
                    merge_evidence(criterion["criterion_id"], "b" * 40, "plan-merge")
                ]
            elif criterion["criterion_id"] == "M0-C4":
                criterion["evidence"] = [
                    merge_evidence(criterion["criterion_id"], "a" * 40, "migration-merge"),
                    durable_evidence(criterion["criterion_id"], "migration-ci"),
                ]
            else:
                criterion["evidence"] = [durable_evidence(criterion["criterion_id"])]
    milestone["status"] = "COMPLETED"


class ForgeCheckpointMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = load("forge/state/checkpoints.json")
        cls.checkpoint_ids = validate_checkpoints(cls.document, ROOT)

    def assert_invalid(self, document, pattern: str) -> None:
        with self.assertRaisesRegex(ValueError, pattern):
            validate_checkpoints(document, ROOT)

    def _blocked_pair(self):
        """Return (blocked_id, dep_id): the first NOT_STARTED milestone (the one
        whose direct dependency is complete, so it may complete) and that direct
        dependency. Tests regress `dep_id` to make `blocked_id`'s dependency
        genuinely incomplete, so the fail-closed guards hold even at the terminal
        milestone (no successor exists)."""
        ids = [m["milestone_id"] for m in self.document["milestones"]]
        # The first NOT_STARTED milestone is the next one that may complete.
        first_not_started = next(
            (m["milestone_id"] for m in self.document["milestones"]
             if m["status"] == "NOT_STARTED"),
            ids[-1],
        )
        # Its direct dependency (ids are ordered M0..M9 -> dependency is previous).
        idx = ids.index(first_not_started)
        dep_id = ids[idx - 1] if idx > 0 else first_not_started
        return first_not_started, dep_id

    def test_committed_checkpoint_document_validates(self) -> None:
        self.assertEqual(
            self.checkpoint_ids,
            {f"CP-M{index}" for index in range(10)},
        )

    def test_all_committed_work_items_reference_milestones(self) -> None:
        paths = sorted(WORK_ITEMS.glob("*.json"))
        self.assertTrue(paths)
        for path in paths:
            validate_work_item(path, self.checkpoint_ids)

    def test_approved_plan_and_archives_match_bound_digests(self) -> None:
        mandate = self.document["active_build_mandate"]
        migration = self.document["migration"]
        self.assertEqual(
            file_sha256(ROOT / mandate["document_path"]),
            mandate["plan_sha256"],
        )
        self.assertEqual(
            file_sha256(ROOT / migration["source"]["archived_schema_path"]),
            migration["source"]["schema_sha256"],
        )
        self.assertEqual(
            file_sha256(ROOT / migration["source"]["archived_state_path"]),
            migration["source"]["state_sha256"],
        )
        self.assertEqual(migration["blueprint"]["sha256"], EXPECTED_BLUEPRINT_SHA256)

    def test_milestone_inventory_is_exact_and_ordered(self) -> None:
        self.assertEqual(
            [item["milestone_id"] for item in self.document["milestones"]],
            list(EXPECTED_MILESTONES),
        )
        for mutation in ("missing", "duplicate", "reordered", "extra"):
            with self.subTest(mutation=mutation):
                malformed = copy.deepcopy(self.document)
                if mutation == "missing":
                    malformed["milestones"].pop()
                elif mutation == "duplicate":
                    malformed["milestones"][1] = copy.deepcopy(malformed["milestones"][0])
                elif mutation == "reordered":
                    malformed["milestones"][5], malformed["milestones"][6] = (
                        malformed["milestones"][6],
                        malformed["milestones"][5],
                    )
                else:
                    extra = copy.deepcopy(malformed["milestones"][-1])
                    extra["milestone_id"] = "M10"
                    malformed["milestones"].append(extra)
                self.assert_invalid(malformed, "minItems|maxItems|exactly ordered M0-M9")

    def test_approved_dependency_order_is_enforced(self) -> None:
        malformed = copy.deepcopy(self.document)
        malformed["milestones"][5]["dependencies"] = ["M6"]
        self.assert_invalid(malformed, "dependencies do not match approved graph")

    def test_crosswalk_inventory_and_namespaces_are_exact(self) -> None:
        crosswalk = self.document["migration"]["crosswalk"]
        self.assertEqual({item["source_id"] for item in crosswalk}, set(EXPECTED_CROSSWALK))
        malformed = copy.deepcopy(self.document)
        malformed["migration"]["crosswalk"].pop()
        self.assert_invalid(malformed, "minItems|incomplete inventory")
        malformed = copy.deepcopy(self.document)
        malformed["migration"]["crosswalk"][-1]["source_id"] = "F10"
        self.assert_invalid(malformed, "naked F identifiers")

    def test_p4_mock_criterion_cannot_satisfy_live_conclave(self) -> None:
        malformed = copy.deepcopy(self.document)
        p4 = next(
            item for item in malformed["migration"]["crosswalk"] if item["source_id"] == "P4"
        )
        p4["criterion_routes"][0]["target_milestones"] = ["M4"]
        self.assert_invalid(malformed, "P4 mock criterion cannot satisfy M4")

    def test_legacy_status_inheritance_is_rejected(self) -> None:
        malformed = copy.deepcopy(self.document)
        malformed["migration"]["status_inheritance"] = True
        self.assert_invalid(malformed, "const constraint|status inheritance is forbidden")
        malformed = copy.deepcopy(self.document)
        malformed["migration"]["crosswalk"][0]["status_inherited"] = True
        self.assert_invalid(malformed, "const constraint|inherits legacy status")

    def test_program_gate_coverage_is_separate_and_complete(self) -> None:
        actual = {
            item["source_id"]: item["target_milestone"]
            for item in self.document["migration"]["program_gate_coverage"]
        }
        self.assertEqual(actual, EXPECTED_PROGRAM_GATE_COVERAGE)
        malformed = copy.deepcopy(self.document)
        malformed["migration"]["therapeutic_program_state_changes"] = True
        self.assert_invalid(malformed, "const constraint|cannot change therapeutic Program state")
        malformed = copy.deepcopy(self.document)
        malformed["migration"]["program_gate_coverage"][0][
            "changes_program_state"
        ] = True
        self.assert_invalid(malformed, "const constraint|cannot change Program state")

    def test_migration_pull_request_cannot_complete_any_milestone(self) -> None:
        # Post-activation: migration and mandate are ACTIVE and M0 is COMPLETED.
        # The validator requires ACTIVE migration to keep M0 COMPLETED with bound
        # merge evidence, so regressing M0 fails closed.
        # M0 and M1 are COMPLETED; regressing M0 fails closed against M1's
        # completion dependency guard (M1 is COMPLETED and depends on M0).
        malformed = copy.deepcopy(self.document)
        malformed["milestones"][0]["status"] = "IN_PROGRESS"
        self.assert_invalid(malformed, "M1: COMPLETED requires completed dependencies")
        malformed = copy.deepcopy(self.document)
        malformed["milestones"][0]["exit_criteria"][2]["evidence"] = []
        self.assert_invalid(malformed, "M0-C3: VERIFIED requires durable evidence")

    def test_verified_criterion_requires_bound_durable_evidence(self) -> None:
        malformed = copy.deepcopy(self.document)
        criterion = malformed["milestones"][3]["exit_criteria"][0]
        criterion["status"] = "VERIFIED"
        criterion["evidence"] = []
        self.assert_invalid(malformed, "VERIFIED requires durable evidence")
        malformed = copy.deepcopy(self.document)
        criterion = malformed["milestones"][3]["exit_criteria"][0]
        criterion["evidence"] = [durable_evidence("M9-C1")]
        self.assert_invalid(malformed, "evidence is bound to another criterion")
        malformed = copy.deepcopy(self.document)
        criterion = malformed["milestones"][3]["exit_criteria"][0]
        evidence = durable_evidence(criterion["criterion_id"])
        evidence["locator"] = "local:tests-passed"
        criterion["evidence"] = [evidence]
        self.assert_invalid(malformed, "evidence is not durable")

    def test_github_evidence_locator_and_revision_identity_must_match(self) -> None:
        fixtures = (
            {
                "type": "GITHUB_ISSUE_COMMENT",
                "locator": "https://github.com/SangLeeVX/eigen-foundry/issues/1#issuecomment-1",
                "immutable_revision": "issuecomment-2",
            },
            {
                "type": "GITHUB_PR_REVIEW",
                "locator": "https://github.com/SangLeeVX/eigen-foundry/pull/1#pullrequestreview-1",
                "immutable_revision": f"pullrequestreview-2@{'a' * 40}",
            },
            {
                "type": "GITHUB_MERGE_COMMIT",
                "locator": f"https://github.com/SangLeeVX/eigen-foundry/commit/{'a' * 40}",
                "immutable_revision": "b" * 40,
            },
            {
                "type": "GITHUB_ACTION_RUN",
                "locator": "https://github.com/SangLeeVX/eigen-foundry/actions/runs/1",
                "immutable_revision": f"run-2@{'a' * 40}",
            },
        )
        for index, fixture in enumerate(fixtures):
            with self.subTest(evidence_type=fixture["type"]):
                evidence = {
                    "evidence_id": f"EVD-M2-C1-mismatch-{index}",
                    "bound_criterion_id": "M2-C1",
                    "result": "PASS",
                    **fixture,
                }
                with self.assertRaisesRegex(ValueError, "not exact-revision addressable"):
                    validate_evidence(evidence, "M2-C1")

    def test_completed_milestone_requires_verified_criteria(self) -> None:
        # Baseline is post-activation: M0 COMPLETED, migration ACTIVE.
        # A COMPLETED milestone must have every exit criterion VERIFIED.
        malformed = copy.deepcopy(self.document)
        malformed["milestones"][1]["status"] = "COMPLETED"  # M1
        for criterion in malformed["milestones"][1]["exit_criteria"]:
            criterion["status"] = "PENDING"
        self.assert_invalid(malformed, "every criterion VERIFIED")

    def test_completed_milestone_requires_completed_dependencies(self) -> None:
        # The first NOT_STARTED milestone may complete only once its direct
        # dependency is COMPLETED. Regress the dependency so the dependent cannot
        # complete — fail-closed on the incomplete dependency.
        blocked, dep_id = self._blocked_pair()
        malformed = copy.deepcopy(self.document)
        verify_milestone(malformed, blocked)
        for m in malformed["milestones"]:
            if m["milestone_id"] == dep_id:
                m["status"] = "IN_PROGRESS"  # break the dependency
        self.assert_invalid(malformed, "requires completed dependencies")

    def test_open_blocker_prevents_completion(self) -> None:
        # Live M1 blockers (KEY-ROTATION, REPO-POLICY) are now RESOLVED, so re-open
        # an expected blocker in the fixture to exercise the open-blocker guard
        # without violating the required blocker inventory.
        malformed = copy.deepcopy(self.document)
        activate(malformed)
        verify_milestone(malformed, "M0")
        verify_milestone(malformed, "M1")
        for blocker in malformed["blockers"]:
            if blocker["blocker_id"] == "BLK-M1-REPO-POLICY":
                blocker["status"] = "OPEN"
                blocker["evidence"] = []
        self.assert_invalid(malformed, "blocked by an open blocker")

    def test_wrong_plan_or_source_digest_fails_closed(self) -> None:
        malformed = copy.deepcopy(self.document)
        malformed["active_build_mandate"]["plan_sha256"] = f"sha256:{'0' * 64}"
        self.assert_invalid(malformed, "approved plan digest was substituted|plan digest")
        malformed = copy.deepcopy(self.document)
        malformed["migration"]["source"]["state_sha256"] = f"sha256:{'0' * 64}"
        self.assert_invalid(malformed, "approved legacy state digest was substituted|archived state digest")
        malformed = copy.deepcopy(self.document)
        malformed["migration"]["blueprint"]["sha256"] = f"sha256:{'0' * 64}"
        self.assert_invalid(malformed, "identity.*digest.*mismatch")

    def test_approval_roles_decisions_expiry_and_bindings_fail_closed(self) -> None:
        malformed = copy.deepcopy(self.document)
        malformed["active_build_mandate"]["approvals"].pop()
        self.assert_invalid(malformed, "minItems|exactly three functional approvals")
        malformed = copy.deepcopy(self.document)
        malformed["active_build_mandate"]["approvals"][0]["decision"] = "REJECTED"
        self.assert_invalid(malformed, "const constraint|human APPROVED decisions")
        malformed = copy.deepcopy(self.document)
        malformed["active_build_mandate"]["approvals"][0][
            "expires_at"
        ] = "2000-01-01T00:00:00Z"
        self.assert_invalid(malformed, "approval is expired")
        malformed = copy.deepcopy(self.document)
        malformed["active_build_mandate"]["approvals"][0][
            "bound_repository_head"
        ] = "0" * 40
        self.assert_invalid(malformed, "wrong repository head")

    def test_independent_review_must_be_distinct_and_exact_head(self) -> None:
        malformed = copy.deepcopy(self.document)
        review = malformed["active_build_mandate"]["independent_review"]
        review["reviewer_run_id"] = review["authoring_run_id"]
        self.assert_invalid(malformed, "reviewer run identity was substituted|cannot review itself")
        malformed = copy.deepcopy(self.document)
        malformed["active_build_mandate"]["independent_review"][
            "reviewed_repository_head"
        ] = "0" * 40
        self.assert_invalid(malformed, "wrong repository head")

    def test_blocker_must_reference_a_real_milestone_criterion(self) -> None:
        malformed = copy.deepcopy(self.document)
        malformed["blockers"][0]["milestone_id"] = "M9"
        self.assert_invalid(malformed, "foreign criterion")
        malformed = copy.deepcopy(self.document)
        malformed["blockers"][0]["criterion_ids"] = ["M1-C99"]
        self.assert_invalid(malformed, "absent or foreign criterion")

    def test_legacy_work_item_id_is_stable_but_checkpoint_is_migrated(self) -> None:
        item = load("forge/work-items/FWI-P0-001.json")
        self.assertEqual(item["work_item_id"], "FWI-P0-001")
        self.assertEqual(item["legacy_phase_id"], "P0")
        self.assertEqual(item["milestone_id"], "M1")
        self.assertEqual(item["checkpoint_id"], "CP-M1")
        self.assertEqual(item["status"], "READY")
        self.assertIsNone(item["claim"])

    def test_unknown_or_dangling_work_item_checkpoint_fails(self) -> None:
        item = load("forge/work-items/FWI-M0-011.json")
        item["checkpoint_id"] = "CP-M9"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / f"{item['work_item_id']}.json"
            path.write_text(json.dumps(item), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "checkpoint does not match milestone"):
                validate_work_item(path, self.checkpoint_ids)

    def test_new_work_item_cannot_claim_legacy_phase(self) -> None:
        item = load("forge/work-items/FWI-M0-011.json")
        item["legacy_phase_id"] = "P0"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / f"{item['work_item_id']}.json"
            path.write_text(json.dumps(item), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cannot claim a legacy phase"):
                validate_work_item(path, self.checkpoint_ids)

    def test_coordinated_plan_and_head_substitution_fails_closed(self) -> None:
        malformed = copy.deepcopy(self.document)
        forged_head = "0" * 40
        malformed["active_build_mandate"]["approved_repository_head"] = forged_head
        for approval in malformed["active_build_mandate"]["approvals"]:
            approval["bound_repository_head"] = forged_head
        malformed["active_build_mandate"]["independent_review"][
            "reviewed_repository_head"
        ] = forged_head
        self.assert_invalid(malformed, "approved repository head was substituted")

        with tempfile.TemporaryDirectory() as directory:
            contract_root = Path(directory)
            materialize_bound_artifacts(contract_root)
            plan = contract_root / "FOUNDRY_MASTER_BUILD_PLAN.md"
            plan.write_bytes(plan.read_bytes() + b"\nforged plan\n")
            forged_digest = file_sha256(plan)
            malformed = copy.deepcopy(self.document)
            malformed["active_build_mandate"]["plan_sha256"] = forged_digest
            for approval in malformed["active_build_mandate"]["approvals"]:
                approval["bound_plan_sha256"] = forged_digest
            with self.assertRaisesRegex(ValueError, "approved plan digest was substituted"):
                validate_checkpoints(malformed, contract_root)

    def test_coordinated_archive_substitution_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            contract_root = Path(directory)
            materialize_bound_artifacts(contract_root)
            archive = contract_root / "forge/state/legacy/checkpoints-v1.0.0.json"
            archive.write_bytes(archive.read_bytes() + b"\n")
            malformed = copy.deepcopy(self.document)
            malformed["migration"]["source"]["state_sha256"] = file_sha256(archive)
            with self.assertRaisesRegex(
                ValueError, "approved legacy state digest was substituted"
            ):
                validate_checkpoints(malformed, contract_root)

    def test_authority_and_evidence_locators_are_pinned(self) -> None:
        for field, value, pattern in (
            ("project", "Attacker Foundry", "project authority was substituted"),
            ("canonical_repository", "attacker/other", "canonical repository was substituted"),
        ):
            malformed = copy.deepcopy(self.document)
            malformed[field] = value
            self.assert_invalid(malformed, pattern)
        malformed = copy.deepcopy(self.document)
        malformed["active_build_mandate"]["document_path"] = "./FOUNDRY_MASTER_BUILD_PLAN.md"
        self.assert_invalid(malformed, "approved document path was substituted")
        malformed = copy.deepcopy(self.document)
        malformed["migration"]["blueprint"]["library_path"] = "/forged/blueprint.md"
        self.assert_invalid(malformed, "locator mismatch")
        malformed = copy.deepcopy(self.document)
        for approval in malformed["active_build_mandate"]["approvals"]:
            approval["evidence"] = "https://github.com/SangLeeVX/eigen-foundry/issues/999"
        self.assert_invalid(malformed, "approval evidence locator was substituted")

    def test_milestone_criteria_routes_and_blockers_cannot_disappear(self) -> None:
        malformed = copy.deepcopy(self.document)
        malformed["milestones"][0]["exit_criteria"] = malformed["milestones"][0][
            "exit_criteria"
        ][:2]
        self.assert_invalid(malformed, "milestone criterion specification changed")

        for source_id, targets in (("P0", []), ("P7", ["M7"])):
            malformed = copy.deepcopy(self.document)
            entry = next(
                item
                for item in malformed["migration"]["crosswalk"]
                if item["source_id"] == source_id
            )
            entry["criterion_routes"][0]["target_milestones"] = targets
            self.assert_invalid(malformed, "minItems|exact legacy criterion route was changed")

        malformed = copy.deepcopy(self.document)
        malformed["blockers"] = []
        self.assert_invalid(malformed, "required blocker inventory changed")
        malformed = copy.deepcopy(self.document)
        malformed["blockers"][0]["status"] = "RESOLVED"
        malformed["blockers"][0]["evidence"] = ["x"]
        self.assert_invalid(malformed, "schema type constraint|durable evidence")
        malformed = copy.deepcopy(self.document)
        blocker = malformed["blockers"][0]
        blocker["status"] = "RESOLVED"
        blocker["evidence"] = []
        self.assert_invalid(malformed, "resolved blocker requires durable evidence")

    def test_pending_and_active_statuses_require_merge_evidence(self) -> None:
        # The first NOT_STARTED milestone cannot start before its direct dependency
        # has completed; regress the dependency so starting it fails closed.
        blocked, dep_id = self._blocked_pair()
        malformed = copy.deepcopy(self.document)
        for m in malformed["milestones"]:
            if m["milestone_id"] == blocked:
                m["status"] = "IN_PROGRESS"  # trying to start before dep completes
            if m["milestone_id"] == dep_id:
                m["status"] = "NOT_STARTED"  # dependency not yet complete
        self.assert_invalid(malformed, "work cannot start before dependencies complete")
        # The mandate evidence binding must be preserved even in ACTIVE state.
        malformed = copy.deepcopy(self.document)
        malformed["milestones"][0]["exit_criteria"][0]["status"] = "VERIFIED"
        malformed["milestones"][0]["exit_criteria"][0]["evidence"] = []
        self.assert_invalid(malformed, "M0-C1: VERIFIED requires durable evidence")
        # ACTIVE migration requires M0 to remain COMPLETED; regressing M0 fails
        # closed against M1's completion dependency guard (M1 is COMPLETED, deps on M0).
        malformed = copy.deepcopy(self.document)
        malformed["milestones"][0]["status"] = "IN_PROGRESS"
        self.assert_invalid(malformed, "M1: COMPLETED requires completed dependencies")
        malformed = copy.deepcopy(self.document)
        malformed["milestones"][0]["exit_criteria"][3]["evidence"] = malformed[
            "milestones"
        ][0]["exit_criteria"][3]["evidence"][:1]
        self.assert_invalid(malformed, "M0-C4 requires migration merge and exact-head CI")

    def test_work_item_schema_and_authority_laundering_fail_closed(self) -> None:
        item = load("forge/work-items/FWI-M0-011.json")
        item["status"] = "VALIDATED"
        item["claim"].pop("run_id")
        item["claim"].pop("actor")
        item["source"]["unexpected_authority"] = "attacker"
        item["review"]["unexpected_approval"] = True
        for criterion in item["acceptance_criteria"]:
            criterion["status"] = "VERIFIED"
            criterion["evidence"] = [1]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / f"{item['work_item_id']}.json"
            path.write_text(json.dumps(item), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "schema"):
                validate_work_item(path, self.checkpoint_ids)

        item = load("forge/work-items/FWI-M0-011.json")
        item["milestone_id"] = "M9"
        item["checkpoint_id"] = "CP-M9"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / f"{item['work_item_id']}.json"
            path.write_text(json.dumps(item), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "work_item_id milestone"):
                validate_work_item(path, self.checkpoint_ids)

        item = load("forge/work-items/FWI-M0-011.json")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "forged-name.json"
            path.write_text(json.dumps(item), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "filename must equal"):
                validate_work_item(path, self.checkpoint_ids)

    def test_no_legacy_phase_array_remains_canonical(self) -> None:
        self.assertNotIn("phases", self.document)
        self.assertNotIn("phase_id", json.dumps(self.document["milestones"]))


if __name__ == "__main__":
    unittest.main()
