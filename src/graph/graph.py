"""TEL-C2-106 — outer graph (Cat 2).

AgentBaseGraph 5-node backbone; domain complexity in the `main` slot via
AlarmCorrelationWorkflowGraphNode (a GraphNode wrapping the inner AlarmCorrelationWorkflow).

    START → initialize → pre_process → main(GraphNode) → post_process → finalize → END
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, cast

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState

from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State

if TYPE_CHECKING:
    from src.graph.domain_workflow_graph import AlarmCorrelationWorkflow


class AlarmCorrelationWorkflowGraphNode(GraphNode):
    """`main` slot — wraps the inner AlarmCorrelationWorkflow (composition criterion #9)."""

    error_strategy: ClassVar[str] = "propagate"
    propagate_hitl: ClassVar[bool] = False
    _subgraph: ClassVar[AlarmCorrelationWorkflow | None] = (
        None  # class-level cache (SDK how-to compose-agents-graphnode.md) — not mutable node-instance state (CoE §9)
    )

    def get_subgraph(self) -> AlarmCorrelationWorkflow:
        # Cache the inner-workflow instance (BaseGraph.invoke() _ensure_compiled is idempotent →
        # skips per-request DAG compile).
        if self._subgraph is None:
            from src.graph.domain_workflow_graph import AlarmCorrelationWorkflow

            AlarmCorrelationWorkflowGraphNode._subgraph = AlarmCorrelationWorkflow(config=self._parent_config())
        return cast("AlarmCorrelationWorkflow", self._subgraph)

    def extract_input(self, state: AgentState) -> str:
        return cast(str, state.get("validated_input", state.get("user_input", "")))

    def merge_output(self, state: AgentState, sub_result: dict[str, Any]) -> dict[str, Any]:
        # error_code is OUTER-first: a pre-stage rejection (INJECTION_REJECTED / INPUT_TOO_LONG) must
        # survive to the terminal S-4 audit. The inner workflow runs on the discarded body and would
        # otherwise overwrite it with NO_ALARMS (extract_input passes only validated_input, so the inner
        # state never sees the outer error_code).
        return {
            "result": sub_result.get("output"),
            "alarm_count": sub_result.get("alarm_count", state.get("alarm_count", 0)),
            "group_count": sub_result.get("group_count", state.get("group_count", 0)),
            "noc_review_required": sub_result.get("noc_review_required", state.get("noc_review_required", False)),
            "error_code": state.get("error_code") or sub_result.get("error_code"),
            "status": sub_result.get("status"),
        }

    def _parent_config(self) -> dict[str, Any]:
        return {}


class Graph(AgentBaseGraph):
    """Outer Cat 2 graph for TEL-C2-106."""

    @property
    def name(self) -> str:
        return "TelecomAlarmCorrelationQueueAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()  # injects InitializeNode + FinalizeNode
        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = AlarmCorrelationWorkflowGraphNode()
        self._nodes["post_process"] = PostProcessNode()

    def get_output(self, state: dict[str, Any]) -> dict[str, Any]:
        """Framework default, plus the guarantee that a success is never empty.

        The Marketplace runner rejects a successful invocation whose output is
        missing — verified on a deployed Pod — and a degraded run
        (SUCCESS + error_code) produces no artefact for the framework default
        to surface. Report the degradation instead: this states what happened,
        it does not invent an answer.

        Only on SUCCESS. A request refused by the framework's S-2 gate (status
        ERROR) must keep publishing nothing — answering a hostile input with a
        notice would undo the refusal, and the runner treats a non-success
        invocation as a failure regardless, so there is nothing to rescue.
        """
        out: dict[str, Any] = super().get_output(state)
        if not out.get("output") and str(state.get("status", "")).lower().endswith("success"):
            code = state.get("error_code") or "NO_CONTENT"
            out["output"] = (
                "This request could not be completed "
                f"(error_code={code}). No content was produced; "
                "see error_code and error_log for the degradation cause."
            )
        return out


# server.py / AgentRegistry expect a module-level alias for the agent class.
TelecomAlarmCorrelationQueueAgent = Graph
