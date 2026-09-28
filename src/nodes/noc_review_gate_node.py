"""TEL-C2-106 — inner workflow step 4: noc_review_gate (HumanApprovalGate).

Deterministic NOC review gate. It does **not** accept/reject any correlation group and never assigns an
owner — every candidate group is a candidate that requires an authorized NOC human's review. It sets
``noc_review_required=True`` + ``review_status="pending_noc_review"`` and records the material groups
(needs-review / any candidate group) into the queue for the NOC. Skips (no-op) on the rejected / 0-group
safe-answer branch (no review gate needed) after emitting a skip audit event.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.utils.audit import emit_trace_event


class NocReviewGateNode(FunctionNode):
    """Flag every candidate correlation group for authorized NOC review; set noc_review_required."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        report = json.loads(state.get("result") or "{}")
        if (
            state.get("error_code")
            or state.get("group_count", 0) == 0
            or report.get("status_kind") != "alarm_correlation_evidence_queue"
        ):
            emit_trace_event("noc_review_gate.skip", {"reason": state.get("error_code") or "no_groups"}, state)
            return {"noc_review_required": False, "review_status": "not_required", "status": AgentStatus.SUCCESS.value}

        material: list[dict[str, Any]] = []
        for group in report.get("correlation_groups", []):
            material.append(
                {
                    "group_id": group["group_id"],
                    "correlation_rule_id": group["correlation_rule_id"],
                    "uncertainty_label": group["uncertainty_label"],
                    "alarm_count": group["alarm_count"],
                    "reason": "Candidate correlation group — requires authorized NOC review before any "
                    "operational action (accept/reject/priority is a NOC human's decision)",
                }
            )

        required = bool(material)
        review = {
            "required": required,
            "status": "pending_noc_review" if required else "not_required",
            "note": "Correlation groups are candidates only. Accept/reject, prioritisation and any field "
            "work are an authorized NOC human's decision. This agent produces read-only evidence.",
            "material_groups": material,
        }
        report["noc_review"] = review
        emit_trace_event(
            "noc_review_gate.complete", {"review_required": required, "material_group_count": len(material)}, state
        )
        return {
            "result": json.dumps(report, ensure_ascii=False),
            "noc_review_required": required,
            "review_status": review["status"],
            "status": AgentStatus.SUCCESS.value,
        }
