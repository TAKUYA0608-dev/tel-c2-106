"""AgentCore Platform v1.0 — Cat 3 sample (read-only reference, delete before release)"""

# Cat 3 — AutonomousBaseGraph, LLM think-act-observe loop
#
# When to use Cat 3:
#   The template runs an autonomous think→act→observe loop with self-directed
#   termination (review-criteria §1-1, §2-1).
#   Examples: ResearchAgent, AutonomousCodeReviewAgent
#
# ── Pipeline ──────────────────────────────────────────────────────────────────
#
#   START → initialize → think ⇄ act → finalize → END
#
#   All edges are wired by AutonomousBaseGraph.add_edges() — do NOT override it.
#
#   Loop routing (_route_after_think):
#     tool_calls present   → act   (execute tool, then back to think)
#     status == SUCCESS    → finalize
#     iterations >= max    → finalize
#     status == ERROR      → finalize
#
# ── Node roles ────────────────────────────────────────────────────────────────
#
#   initialize — sets schema_version, session_id, caller_trust_level, cost_usd=0.0
#   think      — calls LLM with build_think_prompt(); emits tool_calls or final answer
#   act        — executes tool_calls; records results back into state
#   finalize   — FinalizeNode: builds response_metadata + total_time_ms (teardown).
#                The default does NOT call assemble_output() — see the note on
#                assemble_output() below and CASE 2 in register_nodes().
#
# ── Required abstract methods ─────────────────────────────────────────────────
#
#   max_iterations (property)
#     Iteration ceiling for the think-act loop.
#     Must return a positive int ≤ 1000 (framework hard cap).
#     Enforced by _validate_config() at compile() time.
#     Pattern: return int(self.config.get("max_iterations", self.DEFAULT_MAX_ITERATIONS))
#
#   get_tools() → list
#     Return the list of tool callables available to ThinkNode.
#     Tools are bound to the LLM via llm.bind_tools(tools) at register_nodes().
#     Tool names must match hitl.tool_policies keys if HITL is enabled.
#
#   build_think_prompt(state) → str
#     Build the prompt string fed to ThinkNode on each iteration.
#     Include: task description, working memory, prior tool results.
#     Keep prompts deterministic — avoid time-dependent content.
#
#   assemble_output(state) → dict
#     Shape the final output dict from terminal state.
#     NOT called by the default FinalizeNode — on_finalize() is an opt-in hook
#     returning {}, so this shaping is skipped unless you wire a custom
#     FinalizeNode (CASE 2) whose on_finalize() returns assemble_output(state).
#     Those fields land in State, where AutonomousBaseGraph.get_output()
#     surfaces state["final_output"].
#     Cost is automatically surfaced by AutonomousBaseGraph._enrich_output();
#     you do not need to include cost_usd here.
#
# ── config.yaml requirements ──────────────────────────────────────────────────
#
#   budget_usd: 2.50       # REQUIRED — hard cost ceiling per invocation (USD)
#                          # Use null to opt-out (cost tracked externally).
#                          # Absent budget_usd raises ConfigError at compile().
#   llm: <BaseLLM>         # REQUIRED — BaseLLM instance with .complete() method
#   max_iterations: 20     # OPTIONAL — overrides DEFAULT_MAX_ITERATIONS (default 50)
#
# ── register_nodes() — two cases ─────────────────────────────────────────────
#
#   CASE 1 (most templates): omit register_nodes() entirely.
#     AutonomousBaseGraph wires all four slots automatically:
#       initialize → AutonomousInitializeNode
#       think      → ThinkNode(self, llm)       # llm = _build_llm_with_tools()
#       act        → ToolActNode(tools, ...)
#       finalize   → FinalizeNode
#
#   CASE 2: override one or more fixed-pipeline slots.
#     def register_nodes(self) -> None:
#         super().register_nodes()              # wires all four slots first
#         self._nodes["think"] = CustomThinkNode(self, llm)   # replace think only
#         # self._nodes["act"]  = CustomToolActNode(...)       # replace act only
#
# ── Rules ─────────────────────────────────────────────────────────────────────
#
#   ✅ Inherit AutonomousBaseGraph (review-criteria §2-1 — mandatory for Cat 3)
#   ✅ Implement all four abstract methods: max_iterations, get_tools(),
#      build_think_prompt(), assemble_output()
#   ✅ Declare budget_usd in config.yaml (absent → ConfigError at compile)
#   ✅ Declare llm in config.yaml (absent → ConfigError at compile)
#   ❌ Do NOT override add_edges()
#   ❌ Do NOT inherit AgentBaseGraph for Cat 3 — it is a fixed pipeline and
#      cannot satisfy the autonomous loop requirement (review-criteria §2-1)

from framework.graph.autonomous_base_graph import AutonomousBaseGraph
from src.tools.your_tools import YOUR_TOOL_REGISTRY

_MAX_ITERATIONS: int = 20


class GraphCat3Sample(AutonomousBaseGraph):
    """Cat 3 autonomous loop graph sample.

    Fixed pipeline: START → initialize → think ⇄ act → finalize → END

    Replace TelecomAlarmCorrelationQueueAgent with the template identifier (e.g. "ind_c3_001").
    Replace GraphCat3Sample with your agent class name.
    Replace YOUR_TOOL_REGISTRY with your domain tool registry.
    """

    # ── Required: name ────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        """Unique identifier for this agent.

        Replace TelecomAlarmCorrelationQueueAgent with the template identifier,
        e.g. "ind_c3_001" or "cmn_c3_research".
        """
        return "TelecomAlarmCorrelationQueueAgent"

    # ── Required: iteration ceiling ───────────────────────────────────────────

    @property
    def max_iterations(self) -> int:
        """Return the iteration ceiling for the think-act loop.

        Reads from config.yaml `max_iterations`; falls back to _MAX_ITERATIONS.
        Must be a positive int ≤ 1000 (framework hard cap).
        """
        return int(self.config.get("max_iterations", _MAX_ITERATIONS))

    # ── Required: abstract methods ────────────────────────────────────────────

    def get_tools(self) -> list:
        """Return tools available to the LLM on each think iteration.

        Tools are bound at register_nodes() via llm.bind_tools(tools).
        If hitl.enabled: true, tool names here must match hitl.tool_policies keys.
        """
        return list(YOUR_TOOL_REGISTRY.values())

    def build_think_prompt(self, state) -> str:
        """Build the prompt fed to ThinkNode on each iteration.

        Include task, working memory, and prior tool results so the LLM
        has full context. Keep prompts deterministic — avoid datetime.now() etc.
        """
        return (
            f"Task: {state.get('user_input')}\n"
            f"Memory: {state.get('working_memory', {})}\n"
            f"Prior results: {state.get('tool_results', [])}\n"
            "What is your next action?"
        )

    def assemble_output(self, state) -> dict:
        """Shape the final output dict from terminal state.

        NOT called by the default FinalizeNode. To run it, wire a custom
        FinalizeNode whose on_finalize() returns assemble_output(state) — see
        CASE 2 in register_nodes() below. Otherwise this shaping is skipped.
        cost_usd is automatically surfaced by _enrich_output() — do not include it here.
        """
        return {
            "final_output": state.get("final_output"),
            "artifacts": state.get("artifacts", []),
            "thoughts": state.get("thoughts", []),
            "iterations": state.get("iterations", 0),
        }

    # ── Optional: register_nodes() ────────────────────────────────────────────
    #
    # CASE 1 (most templates): omit this method entirely.
    #   AutonomousBaseGraph.register_nodes() wires initialize/think/act/finalize.
    #   Note: the default FinalizeNode does NOT call assemble_output(). If your
    #   template relies on assemble_output() to shape the response, use CASE 2.
    #
    # CASE 2: override to replace one or more slots.
    #   def register_nodes(self) -> None:
    #       super().register_nodes()
    #       # self._nodes["think"] = CustomThinkNode(self, self._build_llm_with_tools())
    #       self._nodes["finalize"] = ReportFinalizeNode(self)   # runs assemble_output()
    #
    #   To run assemble_output() at finalize time, wire a custom FinalizeNode
    #   whose on_finalize() returns it. FinalizeNode.execute() merges on_finalize()'s
    #   return into State, so the fields assemble_output() produces (e.g.
    #   final_output) land in State — where get_output() reads state["final_output"]:
    #
    #   from framework.nodes.defaults.finalize_node import FinalizeNode
    #
    #   class ReportFinalizeNode(FinalizeNode):
    #       def __init__(self, agent):
    #           self._agent = agent
    #       def on_finalize(self, state) -> dict:
    #           return self._agent.assemble_output(state)
