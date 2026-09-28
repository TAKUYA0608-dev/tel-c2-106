"""AgentCore Platform v1.0 — Cat 2 sample (read-only reference, delete before release)"""

# Cat 2 — AgentBaseGraph + GraphNode, multi-node domain workflow
#
# When to use Cat 2:
#   The template orchestrates multiple steps to accomplish a specific
#   job-to-be-done (review-criteria §1-1).
#   Examples: InvoiceTriageAgent, ContractGapDetectionAgent
#
# ── Architecture ──────────────────────────────────────────────────────────────
#
#   Cat 2 uses two layers:
#
#   Outer graph (this file — graph.py):
#     Inherits AgentBaseGraph. The fixed 5-node backbone is identical to Cat 1.
#     Domain complexity is hidden inside the `main` slot via a GraphNode subclass.
#     Do NOT override add_edges() on the outer graph.
#
#   Inner graph (src/graph/domain_workflow_graph.py):
#     Inherits BaseGraph or AgentBaseGraph depending on topology needs.
#     Implements the multi-step domain logic with custom nodes and edges.
#     Called by DomainWorkflowGraphNode.get_subgraph().
#     See src/examples/domain_workflow_graph_sample.py for a full inner graph example.
#
# ── Pipeline ──────────────────────────────────────────────────────────────────
#
#   Outer backbone (fixed — same as Cat 1):
#     START → initialize → pre_process → main → {route} → post_process → finalize → END
#                                             ↓ (RETRY, max 3)
#                                          pre_process
#
#   Inside `main` (DomainWorkflowGraphNode.execute()):
#     GraphNode calls inner_graph.invoke(user_input, ctx=ctx).
#     The inner graph runs its own topology and returns sub_result.
#     merge_output() maps sub_result fields back into the outer state.
#
# ── Directory layout ──────────────────────────────────────────────────────────
#
#   src/graph/graph.py                    ← outer graph (this file)
#   src/graph/domain_workflow_graph.py    ← inner graph (multi-node topology)
#                                         ✅ src/graph/ is the ONLY accepted path
#                                         ❌ src/subagents/ is NOT accepted
#
# ── GraphNode contract ────────────────────────────────────────────────────────
#
#   get_subgraph()    — instantiate and return the inner graph; called per execute()
#   extract_input()   — pull the string user_input to pass to inner graph.invoke()
#   merge_output()    — map sub_result fields into the outer state delta dict;
#                       return ONLY changed keys, never the full state.
#                       Design together with inner graph's get_output().
#   error_strategy    — "propagate" (default): re-raise inner errors as SubgraphError
#                       "handle": call on_subgraph_error() for graceful degradation
#   propagate_hitl    — False (default): HITL interrupts stay inside the inner graph
#                       True: surface inner HITL interrupt to the outer graph caller
#
# ── What to implement ─────────────────────────────────────────────────────────
#
#   1. Subclass GraphNode → override get_subgraph(), extract_input(), merge_output().
#   2. Subclass AgentBaseGraph → override register_nodes(); assign your GraphNode
#      subclass to self._nodes["main"].
#   3. Call super().register_nodes() in the outer graph.
#   4. Implement the inner graph in src/graph/domain_workflow_graph.py.
#   5. Do NOT override add_edges() on the outer graph.
#
# ── Rules ─────────────────────────────────────────────────────────────────────
#
#   ✅ Outer graph inherits AgentBaseGraph
#   ✅ Call super().register_nodes() in outer graph
#   ✅ Assign a GraphNode subclass to the `main` slot
#   ✅ Inner graph lives at src/graph/domain_workflow_graph.py
#   ✅ merge_output() returns only changed state keys
#   ❌ Do NOT override add_edges() on the outer graph
#   ❌ Do NOT place the inner graph under src/subagents/

from typing import ClassVar

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from src.nodes.pre_process_node import PreProcessNode
from src.nodes.post_process_node import PostProcessNode
from src.schemas.state import State


class DomainWorkflowGraphNode(GraphNode):
    """Wraps the inner domain workflow graph; assigned to the `main` slot.

    Replace DomainWorkflowGraphNode with a name that reflects your domain,
    e.g. InvoiceTriageGraphNode or ContractGapGraphNode.
    """

    # "propagate": re-raise inner graph exceptions as SubgraphError (default — fail fast).
    # "handle": call on_subgraph_error() instead — use for graceful degradation.
    error_strategy: ClassVar[str] = "propagate"

    # False: HITL interrupts are handled inside the inner graph only (default).
    # True:  surface inner HITL interrupt to the outer graph caller.
    #        Requires hitl.enabled: true in the outer graph config as well.
    propagate_hitl: ClassVar[bool] = False

    def get_subgraph(self):
        """Instantiate and return the inner domain workflow graph.

        Called on every execute() — if construction is expensive, cache in __init__:
            def __init__(self):
                super().__init__()                        # always call super
                self._subgraph = DomainWorkflowGraph()
            def get_subgraph(self):
                return self._subgraph

        The inner graph must inherit BaseGraph (or AgentBaseGraph).
        It receives InvocationContext (correlation_id, session_id,
        caller_trust_level, hitl_allowed) automatically from the framework.
        """
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        return DomainWorkflowGraph(config=self._parent_config())

    def extract_input(self, state: AgentState) -> str:
        """Return the string user_input to pass into inner_graph.invoke().

        Prefer validated_input (set by pre_process) over raw user_input.
        """
        return state.get("validated_input", state.get("user_input", ""))

    def merge_output(self, state: AgentState, sub_result: dict) -> dict:
        """Map inner graph sub_result fields back into the outer state.

        sub_result is the dict returned by DomainWorkflowGraph.get_output().
        Return ONLY the keys this node changes — never return the full state.
        Design this method together with inner graph's get_output() so the
        field names and types are consistent.
        """
        return {
            "result": sub_result.get("output"),
            "status": sub_result.get("status"),
        }

    def _parent_config(self) -> dict:
        """Pass relevant config keys down to the inner graph (optional).

        Override to forward specific config keys the inner graph needs,
        e.g. llm client, retrieval config, or feature flags.
        """
        return {}


class GraphCat2Sample(AgentBaseGraph):
    """Cat 2 outer graph sample.

    Domain logic is encapsulated in DomainWorkflowGraphNode (`main` slot).
    Backbone: initialize → pre_process → main → post_process → finalize (fixed).

    Replace TelecomAlarmCorrelationQueueAgent with the template identifier (e.g. "ind_c2_001").
    Replace GraphCat2Sample with your agent class name.
    """

    @property
    def name(self) -> str:
        return "TelecomAlarmCorrelationQueueAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()  # fills: initialize, finalize (required)
        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = DomainWorkflowGraphNode()
        self._nodes["post_process"] = PostProcessNode()

    # add_edges() is NOT overridden — backbone wiring belongs to the framework.
