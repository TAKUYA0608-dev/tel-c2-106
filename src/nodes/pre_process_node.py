"""TEL-C2-106 — pre_process node: EvidenceIngest (S-1/S-2 validation + field-level input hygiene).

Accepts a structured JSON alarm-correlation request (an ``alarms[]`` array of approved, de-identified alarm
events, plus optional ``topology`` / ``maintenance_windows`` / ``correlation_policy`` / ``scope`` /
``period``) or NL text, normalizes it (NFKC / strip control / length cap), enforces S-1/S-2, and extracts
the analysis slots. The agent is read-only: it never queries live telemetry and never mutates the export.

Degraded contract (SDK 1.0.0): injection markers / oversize / empty never set ``status=ERROR``. They return
``status=SUCCESS + error_code`` (``INJECTION_REJECTED`` / ``INPUT_TOO_LONG`` / ``INPUT_REJECTED``) and
**discard the offending body** so ``main``/``post_process`` still run (disclaimer + S-3 + S-4). The
``@final`` framework hook is not invoked by the local stub framework, so ``execute()`` re-checks the same S-2
conditions itself.

Field-level input hygiene (S-2 SensitiveDataDetectAndMinimise): every string written into
``validated_input`` is passed through ``_hygiene()`` (redacts credential / My-Number / email / phone); the
identifier keys ``event_id`` / ``element`` / ``ne_id`` (and topology / maintenance element refs) are
**unconditionally tokenised** to opaque surrogates (``evt:<sha8>`` / ``ne:<sha8>`` — no syntactic
passthrough); ``alarm_type`` is constrained to a safe enum token; ``source`` is resolved to a privacy-
tokenised citation only if it names an authorized system of record (else dropped so S-3 blocks); the alarm
free-text is length-capped + hygiened (retained for bounded interpretation, never copied to output).
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import PLATFORM_MASK_TOKEN, resolve_provenance, safe_element, safe_event_id
from src.utils.audit import emit_trace_event

_MAX_INPUT = 400_000  # alarm exports carry many events → larger cap than a chat prompt
_MAX_FREETEXT = 2_000  # per-alarm free-text is length-capped before interpretation
_INJECTION_MARKERS = (
    "ignore previous",
    "ignore all previous",
    "disregard the above",
    "system prompt",
    "you are now",
    "###system",
    "<|im_start|>",
)
_REJECT_CODES = frozenset({"INJECTION_REJECTED", "INPUT_TOO_LONG"})
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# ── input hygiene: redact secrets a caller may inadvertently include before persisting to State ──
_CREDENTIAL = re.compile(r"\b(sk-[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{12,}|eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,})\b")
_MY_NUMBER = re.compile(r"\b\d{12}\b")  # Japanese My-Number / 個人番号
_EMAIL = re.compile(r"\b[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){1,4}\b")
_PHONE = re.compile(r"(?<![\d.])(?:(?:\+81[-\s]?\d{1,4}|0\d{1,4})[-\s]?\d{1,4}[-\s]?\d{3,4})(?![\d.])")
_REDACTED = "[REDACTED]"
# PII display fields dropped entirely — alarms are keyed by opaque event_id / element, not by operator name.
_PII_DROP_FIELDS = frozenset(
    {
        "operator_name",
        "engineer_name",
        "contact_name",
        "reported_by",
        "assignee",
        "contact_email",
        "contact_phone",
        "email",
        "phone",
    }
)
# Event identifier keys → evt:<sha8>; element/NE identifier keys → ne:<sha8>. Unconditional tokenize (no
# syntactic passthrough) so a PII / free-text identifier can never leak into State, a citation, or output.
_EVENT_ID_FIELDS = frozenset({"event_id", "id"})
_ELEMENT_ID_FIELDS = frozenset({"element", "ne_id", "ne", "node", "element_a", "element_b"})


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def _looks_structured(text: str) -> bool:
    """True if the input parses as a structured alarm request (dict with ``alarms`` list, or a bare list).

    Approved alarm exports may legitimately carry injection-like text inside an alarm's free-text field
     — that is **data**, contained at the evidence_interpret layer (routed to needs-review)
    and neutralised at S-3, not a caller-prompt injection. So the S-2 caller-boundary injection reject
    applies only to a non-structured (NL / bare-string) caller prompt.
    """
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return False
    return isinstance(obj, list) or (isinstance(obj, dict) and isinstance(obj.get("alarms"), list))


def _masked_elements(obj: Any) -> list[str]:
    """Surrogates of element identifiers that arrived containing the platform's mask token.

    The platform's S-2 pass (final, runs before this node) masks e-mail / phone values and any run of two
    or more Title-Case words, so two different elements such as "Shinjuku Hub" and "Osaka Core" both reach
    this node as "[MASKED]" and hash to the same ``ne:`` surrogate. Recorded here (surrogates only, the
    same ones ``_hygiene_obj`` writes) so the groups that join on them can be marked unverified.
    """
    found: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in _ELEMENT_ID_FIELDS and isinstance(v, str) and PLATFORM_MASK_TOKEN in v:
                found.add(safe_element(v.strip()))
            else:
                found.update(_masked_elements(v))
    elif isinstance(obj, list):
        for v in obj:
            found.update(_masked_elements(v))
    return sorted(found)


def _hygiene(text: str) -> str:
    """Redact credential / My-Number / email / phone patterns from a free-text value."""
    out = _CREDENTIAL.sub(_REDACTED, text)
    out = _MY_NUMBER.sub(_REDACTED, out)
    out = _EMAIL.sub(_REDACTED, out)
    out = _PHONE.sub(_REDACTED, out)
    return out


def _hygiene_obj(obj: Any, freetext_keys: frozenset[str] = frozenset({"text", "description"})) -> Any:
    """Recursively drop PII display fields, tokenize identifiers, resolve provenance, cap free-text, and
    redact secrets in every string value."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            key = k.lower()
            if key in _PII_DROP_FIELDS:
                continue
            if key in _EVENT_ID_FIELDS:
                text = str(v).strip() if v is not None else ""
                out[k] = safe_event_id(text) if text else None
                continue
            if key in _ELEMENT_ID_FIELDS:
                text = str(v).strip() if v is not None else ""
                out[k] = safe_element(text) if text else None
                continue
            if key == "source":
                # Provenance → grounded citation only if it resolves to an authorized system of record
                # (privacy-tokenized); unverifiable / free-text source → None → S-3 blocks (needs_review).
                out[k] = resolve_provenance(v)
                continue
            if key in freetext_keys and isinstance(v, str):
                out[k] = _hygiene(v)[:_MAX_FREETEXT]
                continue
            out[k] = _hygiene_obj(v, freetext_keys)
        return out
    if isinstance(obj, list):
        return [_hygiene_obj(v, freetext_keys) for v in obj]
    if isinstance(obj, str):
        return _hygiene(obj)
    return obj


class PreProcessNode(FunctionNode):
    """Validate the alarm-correlation request, hygiene it, and extract its alarm / topology / policy slots."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _reject_code(self, raw: str) -> str | None:
        if len(raw) > _MAX_INPUT:
            return "INPUT_TOO_LONG"
        norm = _nfkc(raw)
        # Injection markers inside a structured alarm export are DATA (contained at evidence_interpret +
        # neutralised at S-3), not a caller-prompt injection. Hard-reject injection only on a
        # non-structured (NL / bare-string) caller prompt (layer 1 of the 3-layer defence).
        if not _looks_structured(norm) and any(marker in norm.lower() for marker in _INJECTION_MARKERS):
            return "INJECTION_REJECTED"
        return None

    def _extra_security_gate_input(self, state: dict[str, Any]) -> dict[str, Any]:
        """S-2 caller-boundary check: size cap + prompt-injection markers on the raw caller prompt.

        SDK 1.0.0 contract: MUST NOT raise, and MUST NOT set status=ERROR (that would short-circuit the
        pipeline past post_process). A rejection is surfaced as a degraded `SUCCESS + error_code`; the
        offending body is discarded by execute().
        """
        raw = state.get("user_input", "") or ""
        code = self._reject_code(raw)
        if code:
            out = dict(state)
            out["error_code"] = code
            return out
        return dict(state)

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        raw = state.get("user_input", "") or ""
        input_context = state.get("input_context", {})  # read-only [C1]
        enriched = json.dumps(
            {"source": "TelecomAlarmCorrelationQueueAgent", "channel": input_context.get("channel", "unknown")},
            ensure_ascii=False,
        )

        # Degrade on rejection: the S-2 hook may already have set error_code (real SDK); re-detect here
        # because the local stub framework does not invoke the hook. Discard the offending body entirely.
        prior = state.get("error_code")
        code = prior if prior in _REJECT_CODES else self._reject_code(raw)
        if code:
            emit_trace_event("evidence_ingest.rejected", {"reason": code}, state)
            return {
                "validated_input": "{}",
                "input_format": "rejected",
                "enriched_context": enriched,
                "user_input": "",
                "error_code": code,
                "status": AgentStatus.SUCCESS.value,
            }

        if not raw.strip():
            emit_trace_event("evidence_ingest.rejected", {"reason": "empty_input"}, state)
            return {
                "validated_input": "{}",
                "input_format": "empty",
                "enriched_context": enriched,
                "error_code": "INPUT_REJECTED",
                "status": AgentStatus.SUCCESS.value,
            }

        slots, fmt = self._parse(_CONTROL.sub("", _nfkc(raw)))
        emit_trace_event(
            "evidence_ingest.validated",
            {
                "input_format": fmt,
                "alarm_count": len(slots["alarms"]),
                "topology_edges": len(slots["topology"]),
                "maintenance_windows": len(slots["maintenance_windows"]),
                "masked_elements": len(slots.get("masked_elements", [])),
            },
            state,
        )
        return {
            "validated_input": json.dumps(slots, ensure_ascii=False),
            "input_format": fmt,
            "enriched_context": enriched,
            "status": AgentStatus.SUCCESS.value,
        }

    def _parse(self, text: str) -> tuple[dict[str, Any], str]:
        try:
            obj = json.loads(text)
        except (ValueError, TypeError):
            return self._empty_slots(), "text"
        if isinstance(obj, dict):
            alarms = obj.get("alarms")
            alarms = alarms if isinstance(alarms, list) else []
            topology = obj.get("topology") if isinstance(obj.get("topology"), list) else []
            maintenance = obj.get("maintenance_windows") if isinstance(obj.get("maintenance_windows"), list) else []
            policy = obj.get("correlation_policy") if isinstance(obj.get("correlation_policy"), dict) else {}
            return {
                "alarms": _hygiene_obj(alarms),
                "topology": _hygiene_obj(topology),
                "maintenance_windows": _hygiene_obj(maintenance),
                "correlation_policy": _hygiene_obj(policy),
                "scope": _hygiene_obj(obj.get("scope")),
                "period": _hygiene_obj(obj.get("period")),
                "masked_elements": _masked_elements([alarms, topology, maintenance]),
            }, "json"
        if isinstance(obj, list):  # bare alarms array
            slots = self._empty_slots()
            slots["alarms"] = _hygiene_obj(obj)
            slots["masked_elements"] = _masked_elements(obj)
            return slots, "json"
        return self._empty_slots(), "text"

    @staticmethod
    def _empty_slots() -> dict[str, Any]:
        return {
            "alarms": [],
            "topology": [],
            "maintenance_windows": [],
            "correlation_policy": {},
            "scope": None,
            "period": None,
        }
