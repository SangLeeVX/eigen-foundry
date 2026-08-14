from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from forge.schema_validation import SchemaValidationError, validate_instance


ROOT = Path(__file__).resolve().parents[1]


def load(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


class ForgeSchemaValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.work_schema = load("forge/contracts/work-item.schema.json")
        cls.checkpoint_schema = load("forge/contracts/checkpoints.schema.json")
        cls.work_item = load("forge/work-items/FWI-P0-001.json")
        cls.checkpoints = load("forge/state/checkpoints.json")

    def test_committed_instances_match_their_schemas(self) -> None:
        validate_instance(self.work_item, self.work_schema, "work_item")
        validate_instance(self.checkpoints, self.checkpoint_schema, "checkpoints")

    def test_missing_required_field_fails(self) -> None:
        malformed = copy.deepcopy(self.work_item)
        del malformed["objective"]
        with self.assertRaisesRegex(SchemaValidationError, "missing required properties: objective"):
            validate_instance(malformed, self.work_schema, "work_item")

    def test_invalid_enum_fails(self) -> None:
        malformed = copy.deepcopy(self.work_item)
        malformed["status"] = "MAGICALLY_DONE"
        with self.assertRaisesRegex(SchemaValidationError, "value is not one of"):
            validate_instance(malformed, self.work_schema, "work_item")

    def test_unexpected_property_fails(self) -> None:
        malformed = copy.deepcopy(self.checkpoints)
        malformed["trust_me"] = True
        with self.assertRaisesRegex(SchemaValidationError, "unexpected properties: trust_me"):
            validate_instance(malformed, self.checkpoint_schema, "checkpoints")

    def test_unknown_schema_keyword_fails_closed(self) -> None:
        with self.assertRaisesRegex(SchemaValidationError, "unsupported keywords"):
            validate_instance("value", {"type": "string", "mystery": True})


if __name__ == "__main__":
    unittest.main()
