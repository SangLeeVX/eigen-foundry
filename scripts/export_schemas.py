from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from foundry_council.models import (
    Approval,
    ApprovalRequest,
    ArbitrationResult,
    AuditEvent,
    CaseOpinion,
    Challenge,
    ChallengeResolution,
    ChallengeResponse,
    CouncilSession,
    CouncilSessionView,
    FinalCaseAssessment,
    GatePacket,
    GatePacketInputs,
    GateDecision,
    ProgramRecord,
    RedTeamReport,
)


class FoundryCouncilSchemaBundle(BaseModel):
    program_record: ProgramRecord | None = None
    council_session: CouncilSession | None = None
    council_session_view: CouncilSessionView | None = None
    case_opinion: CaseOpinion | None = None
    challenge: Challenge | None = None
    challenge_response: ChallengeResponse | None = None
    challenge_resolution: ChallengeResolution | None = None
    red_team_report: RedTeamReport | None = None
    final_case_assessment: FinalCaseAssessment | None = None
    arbitration_result: ArbitrationResult | None = None
    gate_packet: GatePacket | None = None
    gate_packet_inputs: GatePacketInputs | None = None
    gate_decision: GateDecision | None = None
    approval_request: ApprovalRequest | None = None
    approval: Approval | None = None
    audit_event: AuditEvent | None = None


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    output = project_root / "schemas" / "foundry-council.schema.json"
    output.write_text(
        json.dumps(FoundryCouncilSchemaBundle.model_json_schema(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(output)


if __name__ == "__main__":
    main()
