# Command API contract

The Python service is the executable domain boundary. It assumes a trusted internal `CommandContext`; it is not a network security boundary. A production HTTP layer must supply authenticated identity without accepting authority from request bodies.

## Public routes

| HTTP route | Service command | Authorized caller |
|---|---|---|
| `POST /v1/programs` | `create_program_draft` | Drafter |
| `GET /v1/programs/{id}/snapshot` | Ledger read facade | Scoped reader |
| `POST /v1/council-sessions` | `create_session` | Assigned Commander |
| `GET /v1/council-sessions/{id}` | `get_session_view` | Assigned participant or auditor |
| `POST /v1/council-sessions/{id}:freeze-evidence` | `freeze_evidence` | Evidence Steward |
| `POST /v1/council-sessions/{id}:start-blind` | `start_blind_round` | Commander |
| `POST /v1/council-sessions/{id}/opinions` | `submit_blind_opinion` | Assigned Case Captain |
| `POST /v1/council-sessions/{id}:reveal` | `reveal_claims` | Commander |
| `POST /v1/council-sessions/{id}:open-challenges` | `open_challenges` | Commander |
| `POST /v1/council-sessions/{id}/challenges` | `add_challenge` | Case Captain or Red Team |
| `POST /v1/council-sessions/{id}:close-challenges` | `close_challenges` | Commander |
| `POST /v1/council-sessions/{id}/responses` | `add_response` | Challenged Claim Owner |
| `POST /v1/council-sessions/{id}/resolutions` | `resolve_challenge` | Independent Reviewer |
| `POST /v1/council-sessions/{id}:start-red-team` | `start_red_team` | Commander |
| `POST /v1/council-sessions/{id}/red-team-report` | `submit_red_team` | Red Team |
| `POST /v1/council-sessions/{id}:open-final-cases` | `open_final_cases` | Commander |
| `POST /v1/council-sessions/{id}/final-cases` | `submit_final_case` | Assigned Case Captain |
| `POST /v1/council-sessions/{id}:lock-gate-packet-inputs` | `submit_gate_packet_inputs` | Commander |
| `POST /internal/v1/council-sessions/{id}:arbitrate` | `arbitrate` | Policy service |
| `POST /v1/council-sessions/{id}:request-approval` | `request_approval` | Commander |
| `POST /v1/approval-requests/{id}/decisions` | `record_approval` | Assigned Human |
| `POST /internal/v1/gate-decisions:commit` | `commit_gate_decision` | Commit service |
| `GET /v1/programs/{id}/events` | Ledger read facade | Scoped reader or auditor |

Do not expose generic `PATCH /programs/{id}`, a direct stage setter, arbitrary event append, SQL, or an agent-callable commit route.

## Required headers

- `Authorization`: OIDC access token from the control plane.
- `Idempotency-Key`: required for every command.
- `If-Match`: aggregate state version for every mutation.
- `X-Request-ID`: tracing only; it grants no authority.

Identity, functional roles, Program scope, MFA state, and conflicts come from server-side identity claims and assignments. Request bodies cannot elevate them.

## Error envelope

```json
{
  "type": "https://foundry.eigenbio.ai/errors/state-conflict",
  "status": 409,
  "code": "STATE_CONFLICT",
  "detail": "Program changed after review was frozen.",
  "request_id": "req_opaque",
  "retryable": false,
  "meta": {
    "expected_version": 17,
    "actual_version": 19
  }
}
```

The core already emits stable error codes. The HTTP adapter must map them without returning stack traces, secrets, inaccessible-object existence, or restricted evidence.

## Blind-review read policy

`submit_blind_opinion` returns only `CommandReceipt`. `get_session_view` omits peer opinions and claims before reveal. The HTTP adapter must never expose `SQLiteLedger.get_session` to agents.

## Idempotency policy

Production must scope keys to authenticated principal, route, aggregate, and client key. A repeated key with the same canonical command must return the original result; changed content returns `IDEMPOTENCY_KEY_REUSED`.

The local kernel guarantees no duplicate persisted event or commit and detects tested changed-body key reuse. Exact original-response replay is complete for gate commit only; the HTTP layer must finish it for every command.
