"""AgentCore Platform v1.0 — DomainWorkflowGraph sample (read-only reference, delete before release)"""

# src/graph/domain_workflow_graph_sample.py
#
# This file is the INNER graph for a Cat 2 template.
# It is instantiated by DomainWorkflowGraphNode.get_subgraph() in graph.py.
#
# ── Directory rule ────────────────────────────────────────────────────────────
#
#   ✅ src/graph/domain_workflow_graph.py   (only accepted path)
#
# ── Parent class ──────────────────────────────────────────────────────────────
#
#   Choose based on your domain workflow's topology:
#
#   Inherit BaseGraph when:
#     The inner graph uses fully custom node names and topology
#     (no pre_process/main/post_process slots).
#     BaseGraph._build_graph() iterates self._nodes and calls add_edges()
#     with no forced backbone.
#     You must implement all 7 abstract methods:
#       name, state_schema, register_nodes, add_edges, route,
#       get_output, _validate_config
#
#   Inherit AgentBaseGraph when:
#     The inner graph follows the standard 5-node pipeline
#     (initialize → pre_process → main → post_process → finalize).
#     AgentBaseGraph provides register_nodes(), add_edges(), route(), and
#     get_output() out of the box — override only what your domain needs.
#     Note: AgentBaseGraph.compile() asserts pre_process/main/post_process
#     are registered; all three slots must be filled.
#
#   This sample uses BaseGraph (fully custom topology).
#
# ── How this inner graph is called ───────────────────────────────────────────
#
#   GraphNode.execute() in graph.py calls:
#     subgraph.invoke(user_input, session_id=ctx.session_id, ctx=ctx)
#   The framework injects correlation_id, session_id, caller_trust_level,
#   and hitl_allowed automatically via InvocationContext.from_state().
#   This graph's get_output() shapes the sub_result dict that is then passed
#   to DomainWorkflowGraphNode.merge_output() in graph.py.
#
# ── Pipeline ──────────────────────────────────────────────────────────────────
#
#   This sample uses a linear topology:
#     START → step_a → step_b → step_c → END
#
#   For conditional branching:
#     self._sg.add_conditional_edges("step_b", self.route)
#   then implement route() with meaningful status-based logic.
#
# ── What to implement (BaseGraph ABC) ────────────────────────────────────────
#
#   name              — unique agent identifier string
#   state_schema      — TypedDict subclass (AgentState or custom State)
#   _validate_config  — validate config keys before compile(); pass if none required
#   register_nodes    — assign domain node instances to self._nodes[<name>]
#                       No super() call — BaseGraph.register_nodes() is abstract.
#                       Do NOT register initialize/finalize; those are outer concerns.
#   add_edges         — wire self._sg edges; every node in self._nodes must be reachable
#   route             — required by ABC; implement even for linear graphs (never called
#                       unless add_conditional_edges() references it)
#   get_output        — shape the sub_result dict returned to the outer merge_output()
#
# ── Rules ─────────────────────────────────────────────────────────────────────
#
#   ✅ Place at src/graph/domain_workflow_graph.py
#   ✅ Implement all 7 BaseGraph abstract methods
#   ✅ register_nodes() does NOT call super() (abstract in BaseGraph)
#   ✅ get_output() is designed together with outer merge_output()
#   ✅ Each node's execute() returns only changed state keys
#   ❌ Do NOT register initialize / finalize (outer backbone concern)

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from src.nodes.step_a_node import StepANode
from src.nodes.step_b_node import StepBNode
from src.nodes.step_c_node import StepCNode
from src.schemas.state import State


class DomainWorkflowGraph(BaseGraph):
    """Inner graph for Cat 2 multi-step domain workflow.

    Inherits BaseGraph directly for a fully custom node topology.
    Called by DomainWorkflowGraphNode.get_subgraph() in graph.py.

    Pipeline (linear example):
        START → step_a → step_b → step_c → END

    Replace step_a/step_b/step_c with your domain node names.
    For conditional branching use add_conditional_edges() in add_edges()
    and implement route() with meaningful logic.
    """

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        """Unique identifier for this inner graph.

        Replace TelecomAlarmCorrelationQueueWorkflow with a descriptive name,
        e.g. "invoice_extraction_workflow".
        """
        return "TelecomAlarmCorrelationQueueWorkflow"

    @property
    def state_schema(self) -> type:
        return State

    # ── Config validation ─────────────────────────────────────────────────────

    def _validate_config(self) -> None:
        """Validate inner graph config before compilation.

        No mandatory config for a generic inner graph.
        Add ConfigError raises here if your domain workflow requires
        specific config keys (e.g. retrieval endpoint, model name).
        """
        pass

    # ── Node registration ─────────────────────────────────────────────────────

    def register_nodes(self) -> None:
        """Register all domain nodes.

        No super() call — BaseGraph.register_nodes() is abstract.
        Do NOT register initialize or finalize here; those are outer backbone
        concerns handled by AgentBaseGraph in graph.py.
        Every key registered here must be referenced in add_edges().
        """
        self._nodes["step_a"] = StepANode()
        self._nodes["step_b"] = StepBNode()
        self._nodes["step_c"] = StepCNode()

    # ── Edge wiring ───────────────────────────────────────────────────────────

    def add_edges(self) -> None:
        """Wire the custom domain topology.

        Linear example: step_a → step_b → step_c → END.
        For conditional branching:
            self._sg.add_conditional_edges("step_b", self.route)
        and update route() with status-based branching logic.
        Every node registered in register_nodes() must be reachable from START.
        """
        self._sg.add_edge(START, "step_a")
        self._sg.add_edge("step_a", "step_b")
        self._sg.add_edge("step_b", "step_c")
        self._sg.add_edge("step_c", END)

    # ── Routing ───────────────────────────────────────────────────────────────

    def route(self, state: AgentState) -> str:
        """Conditional routing. Required by BaseGraph ABC.

        For a purely linear topology this method is never called, but it must
        be implemented because BaseGraph declares it @abstractmethod.
        Add meaningful logic here when add_conditional_edges() is used.
        """
        return END if state.get("status") == AgentStatus.ERROR.value else "step_c"

    # ── Output shape ──────────────────────────────────────────────────────────

    def get_output(self, state: AgentState) -> dict:
        """Shape the output dict returned to the outer GraphNode as sub_result.

        This dict is received by DomainWorkflowGraphNode.merge_output() in graph.py
        as the `sub_result` argument. Design both methods together:
            sub_result   = self.get_output(final_state)       # here
            parent_delta = outer_node.merge_output(state, sub_result)  # in graph.py

        Return only the fields the outer merge_output() needs.
        """
        return {
            "output": state.get("formatted_output") or state.get("result"),
            "status": state.get("status"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
