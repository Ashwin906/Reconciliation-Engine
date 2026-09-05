"""
Shared result types for every matching tier.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.proof import Proof


@dataclass(frozen=True)
class EntityRef:
    kind: str        # 'settlement' | 'payment' | 'bank_line' | 'order'
    id: str


@dataclass(frozen=True)
class MatchCandidate:
    tier: str                  # 'T0' | 'T1' | 'T2' | 'T3' | 'T4'
    left: EntityRef
    right: EntityRef
    confidence: float
    proof: Proof | None = None


@dataclass(frozen=True)
class Unmatched:
    kind: str
    id: str
    reason: str
    detail: dict = field(default_factory=dict)
