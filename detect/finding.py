"""
Finding: the output shape every detector in this package produces.
DOMAIN_KNOWLEDGE.md section 11 -- this list of detectors IS the product's
value, not a side feature.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str          # 'P0' | 'P1' | 'P2' | 'P3'
    impact_paise: int
    subjects: tuple[str, ...]
    evidence: dict
    confidence: float
    explanation: str
