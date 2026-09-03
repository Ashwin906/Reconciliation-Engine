"""
The defect catalogue that powers "Break it yourself".

Every entry is DERIVED BY INTROSPECTION from gen/defects.py's actual
injector functions (`inspect.signature` over `_REGISTRY`), not written out
by hand. That matters: a hand-maintained list of "what parameters does
`fee_overcharge` take" would drift the moment an injector's signature
changed, and the UI would offer a control that silently did nothing or
crash on a missing argument. Here, adding a parameter to an injector makes
it appear in the UI, and removing one makes it disappear, with no edit to
this file.

What this module DOES own by hand is presentation metadata -- a
merchant-readable label, a sane slider range, and whether the defect costs
real money. None of that is derivable from a function signature, and none
of it affects behaviour: if a hint is missing, the control still renders
from the introspected default.
"""

from __future__ import annotations

import inspect
from typing import Any

from gen.defects import _REGISTRY

# Parameters every injector takes structurally but that are not user
# choices -- they come from the harness, not from a control on screen.
_NOT_USER_PARAMS = {"ds", "rng"}


class _Hint:
    """Presentation-only metadata for one parameter. Ranges are demo
    guard-rails, not validation: the backend clamps independently (see
    sim/journey.py) so a hand-crafted request cannot use these to get
    somewhere the API would otherwise refuse."""

    def __init__(self, label: str, minimum: float, maximum: float, step: float = 1, unit: str = ""):
        self.label = label
        self.minimum = minimum
        self.maximum = maximum
        self.step = step
        self.unit = unit

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "min": self.minimum,
            "max": self.maximum,
            "step": self.step,
            "unit": self.unit,
        }


_PARAM_HINTS: dict[str, _Hint] = {
    "n": _Hint("How many", 1, 8, 1, ""),
    "extra_bps": _Hint("Overcharge above rate card", 5, 200, 5, "bps"),
    "days": _Hint("Over the last", 1, 60, 1, "days"),
    "reserve_bps": _Hint("Reserve withheld", 100, 2000, 100, "bps"),
    "method": _Hint("Payment method", 0, 0, 1, ""),
}

# Choice parameters -- a free number field would be wrong here. Kept in
# step with the rate card api/main.py actually configures.
_PARAM_CHOICES: dict[str, list[str]] = {
    "method": ["card", "upi", "amex"],
}

# Merchant-readable framing for each defect kind. A kind with no entry
# still works -- it falls back to its own slug -- so a new injector is
# usable here the moment it is registered, before anyone writes copy.
_DEFECT_LABELS: dict[str, tuple[str, str]] = {
    "fee_overcharge": (
        "Overcharged fees",
        "Charges above your rate card. Reconciles perfectly against the bank -- only a rate-card comparison catches it.",
    ),
    "unbooked_chargeback": (
        "Customer chargeback",
        "A disputed payment pulled back, plus a fee, with nothing telling you it happened.",
    ),
    "missing_settlement": (
        "Settlement never arrived",
        "Razorpay says it paid. The bank has no such credit.",
    ),
    "short_credit": (
        "Bank credited less",
        "The settlement said one number, the bank credited a smaller one.",
    ),
    "utr_change": (
        "Reference number rewritten",
        "The bank quoted a different UTR than Razorpay issued. Exact matching fails; the fallback tiers should recover it.",
    ),
    "narration_truncation": (
        "Bank narration truncated",
        "The bank clipped the description. Identity noise, not a money problem.",
    ),
    "duplicate_order_id": (
        "Duplicate order ID",
        "Your own checkout retried and created the same order twice. No money lost.",
    ),
    "cross_period_refund": (
        "Refund landed in another period",
        "A refund issued in one settlement period, deducted in the next.",
    ),
    "reserve_hold": (
        "Rolling reserve withheld",
        "Part of the settlement held back as a risk reserve. Looks identical to money going missing.",
    ),
    "negative_settlement": (
        "Negative settlement",
        "Refunds exceeded collections, so the settlement is a debit rather than a credit.",
    ),
    "offline_payment": (
        "Payment outside Razorpay",
        "A bank credit with no matching Razorpay payment -- cash, a direct transfer, an unknown source.",
    ),
    "holiday_shift": (
        "Settlement shifted by a holiday",
        "The credit landed on a different date than the settlement report claims.",
    ),
    "late_capture": (
        "Late capture",
        "A payment authorised in one window but captured well after it.",
    ),
}

# Defects that move real money, versus ones that are identity/data noise.
# Used by the UI to set expectations honestly BEFORE a run -- a viewer who
# breaks something with no financial impact should be told that is the
# point, not left thinking the system missed it.
_COSTS_MONEY = {
    "fee_overcharge",
    "unbooked_chargeback",
    "missing_settlement",
    "short_credit",
    "cross_period_refund",
    "reserve_hold",
    "negative_settlement",
    "offline_payment",
}


def _param_spec(name: str, parameter: inspect.Parameter) -> dict:
    has_default = parameter.default is not inspect.Parameter.empty
    hint = _PARAM_HINTS.get(name)
    spec: dict[str, Any] = {
        "name": name,
        "required": not has_default,
        "default": parameter.default if has_default else _fallback_default(name),
        "choices": _PARAM_CHOICES.get(name),
    }
    spec.update(hint.as_dict() if hint else {"label": name, "min": 1, "max": 10, "step": 1, "unit": ""})
    return spec


def _fallback_default(name: str):
    """A required parameter has no default of its own, but a UI control
    still has to start somewhere. Prefer the hint's minimum over a
    hardcoded 1 so a parameter like extra_bps starts somewhere sensible."""
    hint = _PARAM_HINTS.get(name)
    if _PARAM_CHOICES.get(name):
        return _PARAM_CHOICES[name][0]
    return hint.minimum if hint else 1


def defect_catalog() -> list[dict]:
    """Every registered defect injector, with its real parameters."""
    out: list[dict] = []
    for kind, fn in sorted(_REGISTRY.items()):
        signature = inspect.signature(fn)
        params = [
            _param_spec(name, parameter)
            for name, parameter in signature.parameters.items()
            if name not in _NOT_USER_PARAMS
        ]
        label, description = _DEFECT_LABELS.get(kind, (kind.replace("_", " "), ""))
        out.append(
            {
                "kind": kind,
                "label": label,
                "description": description,
                "costs_money": kind in _COSTS_MONEY,
                "params": params,
            }
        )
    return out


def coerce_params(kind: str, raw: dict) -> dict:
    """Validates and coerces user-supplied params against the injector's
    REAL signature.

    Rejects unknown parameter names outright rather than silently dropping
    them: a request asking for a control that does not exist is a
    misunderstanding worth surfacing, and silently ignoring it would let a
    viewer believe they had changed something they had not.

    Raises ValueError, which the API layer turns into a 422.
    """
    fn = _REGISTRY.get(kind)
    if fn is None:
        raise ValueError(f"unknown defect kind: {kind}")

    signature = inspect.signature(fn)
    allowed = {n for n in signature.parameters if n not in _NOT_USER_PARAMS}

    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"{kind} has no parameter(s) {sorted(unknown)} -- allowed: {sorted(allowed)}")

    coerced: dict[str, Any] = {}
    for name, value in raw.items():
        parameter = signature.parameters[name]
        coerced[name] = _coerce_one(kind, name, value, parameter)

    # Fill required params the caller omitted, so a partial request still
    # runs rather than failing deep inside the injector with a TypeError.
    for name, parameter in signature.parameters.items():
        if name in _NOT_USER_PARAMS or name in coerced:
            continue
        if parameter.default is inspect.Parameter.empty:
            coerced[name] = _fallback_default(name)

    return coerced


def _coerce_one(kind: str, name: str, value, parameter: inspect.Parameter):
    choices = _PARAM_CHOICES.get(name)
    if choices is not None:
        if value not in choices:
            raise ValueError(f"{kind}.{name} must be one of {choices}, got {value!r}")
        return value

    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{kind}.{name} must be a whole number, got {value!r}")

    # Clamped server-side, independently of the UI's slider bounds -- the
    # UI's range is a convenience, this is the actual limit. Unbounded
    # values here would let one request generate an arbitrarily large
    # dataset and hang the process.
    hint = _PARAM_HINTS.get(name)
    if hint is not None:
        number = max(int(hint.minimum), min(int(hint.maximum), number))
    return number
