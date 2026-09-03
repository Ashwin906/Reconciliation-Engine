"""
Webhook signature verification. Non-negotiable per DOMAIN_KNOWLEDGE.md
section 15: skipping this means anyone on the internet can POST a fake
settlement.processed event to our endpoint and have it treated as real
money movement.
"""

from __future__ import annotations

import hmac
import hashlib


def verify_razorpay_signature(raw_body: bytes, header_signature: str, secret: str) -> bool:
    """Recomputes the HMAC-SHA256 of the RAW request body using the
    webhook secret, and compares against the signature Razorpay sent in
    the X-Razorpay-Signature header. Uses hmac.compare_digest, NEVER a
    plain ==, which would leak timing information about how many
    leading bytes matched and make the signature guessable byte-by-byte
    over enough requests.

    Takes raw_body as bytes, not a parsed dict -- the signature is
    computed over the EXACT bytes Razorpay sent. Re-serializing a parsed
    JSON object before hashing it is a real, easy-to-make mistake: it
    can silently produce a different byte sequence (key order, spacing,
    unicode escaping) than what was actually signed, making every
    signature fail even for genuine requests. The caller must capture
    the raw body BEFORE any JSON parsing.
    """
    if not header_signature:
        return False
    expected = hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header_signature)
