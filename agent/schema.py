"""
The model's output contract. DECISIONS.md D-10: hypothesis is an enum,
not free text -- the model classifies into causes we already understand
and can verify; it cannot invent a plausible-sounding category that maps
to no real mechanism. A novel cause the enum doesn't cover surfaces as
NoHypothesis (agent/investigate.py) and lands in exceptions.md, which is
where a genuinely unknown cause belongs -- not a confident guess.
"""

from __future__ import annotations

# One entry per exception-taxonomy kind this system can currently reason
# about and act on -- deliberately the same vocabulary as gen/defects.py's
# PlantedDefect.kind and detect/rules.py's Finding.rule, so a hypothesis
# the model returns is immediately checkable against ground truth in eval
# and immediately actionable by gate/policy.py (a later phase).
HYPOTHESIS_KINDS = (
    "unbooked_chargeback",
    "missing_settlement",
    "amount_short_unexplained",
    "reserve_hold",
    "negative_settlement",
    "duplicate_settlement",
    "cross_period_refund",
    "unknown_credit",
    "fee_overcharge",
    "other_unexplained",   # the honest escape hatch -- NOT a free-text field,
                            # just an enum member meaning "none of the above
                            # fit, do not force a specific wrong answer"
)

ADJUSTMENT_KINDS = ("refund", "dispute", "dispute_fee", "fee_correction", "reserve_release", "other")

HYPOTHESIS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["hypothesis", "evidence_ids", "proposed_adjustments", "confidence"],
    "properties": {
        "hypothesis": {"type": "string", "enum": list(HYPOTHESIS_KINDS)},
        "evidence_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
        "proposed_adjustments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "ref", "paise"],
                "properties": {
                    "kind": {"type": "string", "enum": list(ADJUSTMENT_KINDS)},
                    "ref": {"type": "string"},
                    "paise": {"type": "integer"},
                },
            },
        },
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        # A short free-text observation, ALWAYS advisory and never parsed
        # or acted on -- it exists so a novel failure mode that doesn't fit
        # any enum member lands in human review carrying the model's actual
        # observation ("bank credit arrived 3 days late, no matching
        # dispute") instead of a bare, information-free NoHypothesis.
        # Deliberately NOT an escape hatch around the enum (D-10): the
        # hypothesis field stays strictly enumerated, this only annotates
        # it. Capped short so it can't become a smuggled reasoning channel.
        "note": {"type": "string", "maxLength": 280},
    },
}


def strict_variant(schema: dict) -> dict:
    """Groq/OpenAI strict structured-output mode rejects a schema with any
    optional property -- every declared property must appear in
    `required`. Our own validation schema deliberately keeps 'note'
    optional (a response without one is perfectly valid), so the strict
    API variant is derived here rather than tightening HYPOTHESIS_SCHEMA
    itself.

    Constraining the model harder than we validate is the safe direction:
    jsonschema.validate() in GroqClient.complete_json stays the authority
    (D-07, the model has no authority), and it accepts a strict response
    trivially. The reverse -- validating harder than we constrain -- would
    just mean rejecting responses we asked for.
    """
    out = dict(schema)
    properties = schema.get("properties", {})
    out["required"] = list(properties.keys())
    return out
