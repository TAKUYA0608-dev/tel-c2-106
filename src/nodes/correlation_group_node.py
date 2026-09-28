"""TEL-C2-106 — inner workflow step 2: correlation_group.

Deterministic (Tool-split, ``shared/tools/correlation`` reference): bundle the normalized alarms into
candidate correlation groups by policy rule — topology-adjacency join + time-window proximity threshold +
alarm-type co-occurrence (union-find over the correlatable pairs). Single-alarm clusters are not groups.
Sets ``group_count``. Skips (no-op) on rejected / 0-alarm input after emitting a skip audit event.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import AlarmCorrelationService
from src.utils.audit import emit_trace_event


class CorrelationGroupNode(FunctionNode):
    """Deterministically bundle correlated alarms into candidate groups (topology + time-window + type)."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("error_code") or state.get("alarm_count", 0) == 0:
            emit_trace_event("correlation_group.skip", {"reason": state.get("error_code") or "no_alarms"}, state)
            return {}

        signals = json.loads(state.get("normalized_alarms") or "[]")
        slots = json.loads(state.get("validated_input") or "{}")
        topology = slots.get("topology") if isinstance(slots.get("topology"), list) else []
        policy = AlarmCorrelationService.resolve_policy(slots.get("correlation_policy"))

        groups = AlarmCorrelationService.group(signals, topology, policy)
        # Strip the internal `_members` payload before persisting to State (the member signals are rejoined
        # from `normalized_alarms` by event_id in evidence_interpret — no raw payload duplicated in State).
        stored = [{k: v for k, v in g.items() if k != "_members"} for g in groups]
        emit_trace_event(
            "correlation_group.complete",
            {"alarms": len(signals), "groups": len(groups), "grouped_alarms": sum(len(g["_members"]) for g in groups)},
            state,
        )
        return {
            "candidate_groups": json.dumps(stored, ensure_ascii=False),
            "group_count": len(groups),
            "status": AgentStatus.SUCCESS.value,
        }
