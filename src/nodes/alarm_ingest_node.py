"""TEL-C2-106 — inner workflow step 1: alarm_ingest.

Deterministic ingest + normalization of the supplied approved alarm export / topology / maintenance windows
/ correlation policy: validate required fields (event_id / ts / element / alarm_type), parse timestamps to
epoch, apply the bounded window, and classify each alarm to a policy rule vocabulary (allow-list). Sets
``alarm_count``. **0 valid alarms (rejected input, non-JSON text, or all rows missing event_id / ts) routes
to the out-of-scope safe answer** — the agent never fabricates correlation evidence for data it did not
receive.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import AlarmCorrelationService
from src.utils.audit import emit_trace_event


class AlarmIngestNode(FunctionNode):
    """Ingest + normalize the supplied alarm export and classify each alarm to a policy rule."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        # Alarms arrive already validated + provenance-resolved by pre_process (S-1): each `source` is a
        # grounded citation `src:<sha8>` or None (a forged surrogate was dropped at S-1). We do not re-run
        # provenance here — normalize trusts that single upstream resolution.
        slots = json.loads(state.get("validated_input") or state.get("user_input") or "{}")
        if not isinstance(slots, dict):
            slots = {}
        canonical = json.dumps(slots, ensure_ascii=False)
        alarms = slots.get("alarms") if isinstance(slots.get("alarms"), list) else []
        policy = AlarmCorrelationService.resolve_policy(slots.get("correlation_policy"))

        if state.get("error_code") or not alarms:
            emit_trace_event("alarm_ingest.skip", {"reason": state.get("error_code") or "no_alarms"}, state)
            return {
                "validated_input": canonical,
                "normalized_alarms": "[]",
                "alarm_count": 0,
                "error_code": state.get("error_code") or "NO_ALARMS",
                "status": AgentStatus.SUCCESS.value,
            }

        normalized = AlarmCorrelationService.normalize(alarms, policy)
        if not normalized:
            emit_trace_event("alarm_ingest.skip", {"reason": "all_malformed"}, state)
            return {
                "validated_input": canonical,
                "normalized_alarms": "[]",
                "alarm_count": 0,
                "error_code": "NO_ALARMS",
                "status": AgentStatus.SUCCESS.value,
            }

        rule_distribution: dict[str, int] = {}
        for s in normalized:
            key = s["rule_id"] or "unmatched"
            rule_distribution[key] = rule_distribution.get(key, 0) + 1
        emit_trace_event(
            "alarm_ingest.complete",
            {"supplied": len(alarms), "ingested": len(normalized), "rule_distribution": rule_distribution},
            state,
        )
        return {
            "validated_input": canonical,
            "normalized_alarms": json.dumps(normalized, ensure_ascii=False),
            "alarm_count": len(normalized),
            "status": AgentStatus.SUCCESS.value,
        }
