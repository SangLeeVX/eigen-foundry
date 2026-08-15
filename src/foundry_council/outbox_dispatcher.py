from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Protocol

from .postgres_ledger import PostgresLedger

logger = logging.getLogger("foundry_council.outbox_dispatcher")

# A handler receives the parsed dispatch envelope and returns normally on
# success; it raises to signal a (retryable or fatal) delivery failure.
DispatchHandler = Callable[[dict[str, Any]], None]


class PollController(Protocol):
    """Allows tests to stop the poll loop deterministically."""

    def should_continue(self) -> bool: ...


def _default_payload_loader(payload_json: str) -> dict[str, Any]:
    return json.loads(payload_json)


class OutboxDispatcher:
    """Transactional outbox consumer.

    Drains PENDING rows (claim -> DISPATCHED), invokes a handler per envelope,
    and on handler failure marks the row FAILED with the error recorded. Rows
    dispatch at-most-once *per drain*; requeue_outbox moves FAILED rows back to
    PENDING for a bounded retry (see poll()).

    A handler is any callable accepting a dict envelope::

        {
            "outbox_id": ...,
            "aggregate_type": ...,
            "aggregate_id": ...,
            "event_id": ...,
            "payload": <parsed payload_json>,
        }
    """

    def __init__(
        self,
        ledger: PostgresLedger,
        handler: DispatchHandler,
        *,
        max_attempts: int = 5,
        payload_loader: Callable[[str], dict[str, Any]] | None = None,
        batch_size: int = 100,
    ) -> None:
        self.ledger = ledger
        self.handler = handler
        self.max_attempts = max_attempts
        self.payload_loader = payload_loader or _default_payload_loader
        self.batch_size = batch_size

    def dispatch_once(self, limit: int | None = None) -> int:
        """Drain and dispatch one batch. Returns the number of rows successfully
        dispatched (FAILED rows are not counted as success)."""
        batch = self.ledger.drain_outbox(limit=limit or self.batch_size)
        delivered = 0
        for row in batch:
            envelope = {
                "outbox_id": row["outbox_id"],
                "aggregate_type": row["aggregate_type"],
                "aggregate_id": row["aggregate_id"],
                "event_id": row["event_id"],
                "payload": self.payload_loader(row["payload_json"]),
            }
            try:
                self.handler(envelope)
            except Exception as exc:  # noqa: BLE001 - delivery boundary
                logger.warning(
                    "outbox delivery failed for %s: %s", row["outbox_id"], exc
                )
                self.ledger.mark_outbox_failed(row["outbox_id"], str(exc))
                self._maybe_requeue(row["outbox_id"])
                continue
            delivered += 1
        return delivered

    def _maybe_requeue(self, outbox_id: str) -> None:
        # If attempts are under the cap, requeue for another poll round.
        rows = self.ledger.get_outbox_by_id(outbox_id)
        if rows and rows[0]["attempts"] < self.max_attempts:
            self.ledger.requeue_outbox(outbox_id)

    def poll(
        self,
        *,
        interval_seconds: float = 1.0,
        controller: PollController | None = None,
        max_rounds: int | None = None,
    ) -> int:
        """Run dispatch loops until the controller says stop (or max_rounds)."""
        total = 0
        rounds = 0
        while True:
            rounds += 1
            total += self.dispatch_once()
            if controller is not None and not controller.should_continue():
                break
            if max_rounds is not None and rounds >= max_rounds:
                break
            time.sleep(interval_seconds)
        return total
