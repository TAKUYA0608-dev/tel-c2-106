"""AgentCore Platform v1.0 — Cat 1 sample (read-only reference, delete before release)"""

# Cat 1 — AgentBaseGraph, standard 3-slot fixed pipeline
#
# When to use Cat 1:
#   The template delivers a single technical capability that is generic and
#   use-case-agnostic (review-criteria §1-1).
#   Examples: TextSummarizer, EmbeddingGenerator, LanguageDetector
#
# ── Pipeline ──────────────────────────────────────────────────────────────────
#
#   START → initialize → pre_process → main → {route} → post_process → finalize → END
#                                           ↓ (RETRY, max 3)
#                                        pre_process
#
#   All edges are wired by AgentBaseGraph.add_edges() — do NOT override it.
#
# ── Node roles ────────────────────────────────────────────────────────────────
#
#   initialize   — injected by super().register_nodes(); sets schema_version,
#                  session_id, caller_trust_level. Do not re-register unless
#                  you subclass InitializeNode and override on_initialize().
#   pre_process  — input validation, intent classification, context enrichment.
#                  Sets validated_input and enriched_context in state.
#   main         — core capability logic (LLM call, retrieval, transform, etc.).
#                  Sets result and status (SUCCESS / RETRY / ERROR).
#   post_process — output formatting, PII scrubbing, response shaping.
#                  Sets formatted_output in state.
#   finalize     — injected by super().register_nodes(); builds response_metadata
#                  and total_time_ms. Do not re-register unless you subclass
#                  FinalizeNode and override on_finalize().
#
# ── Routing ───────────────────────────────────────────────────────────────────
#
#   AgentBaseGraph.route() reads state["status"]:
#     SUCCESS  → post_process
#     RETRY    → pre_process  (up to max_retry times; default 3)
#     ERROR / others → finalize
#   Your main node controls routing by writing AgentStatus.<X>.value (a string).
#
# ── What to implement ─────────────────────────────────────────────────────────
#
#   1. Subclass AgentBaseGraph.
#   2. Override register_nodes():
#      a. Call super().register_nodes() first (injects initialize + finalize).
#      b. Assign PreProcessNode / MainNode / PostProcessNode to the three slots.
#   3. Each node must implement execute(self, state: AgentState) -> dict.
#      Return only the state keys your node changes — never the full state.
#   4. Do NOT override add_edges() or route().
#
# ── Rules ─────────────────────────────────────────────────────────────────────
#
#   ✅ Inherit AgentBaseGraph
#   ✅ Call super().register_nodes() — injects initialize + finalize
#   ✅ Fill all three slots: pre_process / main / post_process
#   ✅ Each node's execute() returns a partial state dict (changed keys only)
#   ❌ Do NOT override add_edges()
#   ❌ Do NOT override route() unless custom routing logic is explicitly required
#   ❌ Do NOT put business logic in graph.py — nodes own execution logic

from framework.graph.agent_base_graph import AgentBaseGraph
from src.nodes.pre_process_node import PreProcessNode
from src.nodes.main_node import MainNode
from src.nodes.post_process_node import PostProcessNode
from src.schemas.state import State


class GraphCat1Sample(AgentBaseGraph):
    """Cat 1 fixed-pipeline graph sample.

    Replace TelecomAlarmCorrelationQueueAgent with the template identifier (e.g. "cmn_c1_001").
    Replace State with your template's AgentState subclass if needed.
    Replace the three node classes with your domain implementations.
    """

    @property
    def name(self) -> str:
        return "TelecomAlarmCorrelationQueueAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()  # injects InitializeNode + FinalizeNode

        # Fill all three domain slots — all are required.
        # Leaving any slot as None will raise MissingNodeError at compile().
        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = MainNode()
        self._nodes["post_process"] = PostProcessNode()
