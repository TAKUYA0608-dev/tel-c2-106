"""TEL-C2-106 — inner domain workflow graph (Cat 2).

Instantiated by AlarmCorrelationWorkflowGraphNode.get_subgraph() in graph.py. Linear topology with per-node
skip guards (the portable Cat 2 form; conditional edges don't propagate across the subgraph boundary):

    START → alarm_ingest → correlation_group → evidence_interpret → noc_review_gate → END

On rejected / 0-alarm input, alarm_ingest sets alarm_count=0 (+error_code); correlation_group and
noc_review_gate no-op and evidence_interpret emits the out-of-scope safe answer — no fabricated correlation.
"""

from __future__ import annotations
from typing import Any

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState

from src.nodes.alarm_ingest_node import AlarmIngestNode
from src.nodes.correlation_group_node import CorrelationGroupNode
from src.nodes.evidence_interpret_node import EvidenceInterpretNode
from src.nodes.noc_review_gate_node import NocReviewGateNode
from src.schemas.state import State


class AlarmCorrelationWorkflow(BaseGraph):
    """Inner graph: alarm_ingest → correlation_group → evidence_interpret → noc_review_gate."""

    @property
    def name(self) -> str:
        return "AlarmCorrelationWorkflow"

    @property
    def state_schema(self) -> type:
        return State

    def _validate_config(self) -> None:
        pass

    def register_nodes(self) -> None:
        # No super() — BaseGraph.register_nodes() is abstract.
        self._nodes["alarm_ingest"] = AlarmIngestNode()
        self._nodes["correlation_group"] = CorrelationGroupNode()
        self._nodes["evidence_interpret"] = EvidenceInterpretNode()
        self._nodes["noc_review_gate"] = NocReviewGateNode()

    def add_edges(self) -> None:
        # Static linear backbone; the 0-alarm / rejected skip is handled by per-node guards.
        self._sg.add_edge(START, "alarm_ingest")
        self._sg.add_edge("alarm_ingest", "correlation_group")
        self._sg.add_edge("correlation_group", "evidence_interpret")
        self._sg.add_edge("evidence_interpret", "noc_review_gate")
        self._sg.add_edge("noc_review_gate", END)

    def route(self, state: AgentState) -> str:
        """Required by the BaseGraph ABC. Linear topology → not wired to a conditional edge."""
        if state.get("error_code") or state.get("alarm_count", 0) == 0:
            return "evidence_interpret"
        return "correlation_group"

    def get_output(self, state: AgentState) -> dict[str, Any]:
        return {
            "output": state.get("result"),
            "status": state.get("status"),
            "alarm_count": state.get("alarm_count", 0),
            "group_count": state.get("group_count", 0),
            "noc_review_required": state.get("noc_review_required", False),
            "error_code": state.get("error_code"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
