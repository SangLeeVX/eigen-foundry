from __future__ import annotations

from typing import Any


class FoundryError(Exception):
    """Base class for typed service failures."""

    code = "INTERNAL_ERROR"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class NotFound(FoundryError):
    code = "NOT_FOUND"


class StateConflict(FoundryError):
    code = "STATE_CONFLICT"


class ApprovalRequired(FoundryError):
    code = "APPROVAL_REQUIRED"


class ValidationFailure(FoundryError):
    code = "VALIDATION_ERROR"


class DuplicateEvent(FoundryError):
    code = "DUPLICATE_EVENT"


class IdempotencyKeyReused(FoundryError):
    code = "IDEMPOTENCY_KEY_REUSED"


class Forbidden(FoundryError):
    code = "FORBIDDEN"


class PolicyConfigurationRequired(FoundryError):
    code = "POLICY_CONFIGURATION_REQUIRED"
