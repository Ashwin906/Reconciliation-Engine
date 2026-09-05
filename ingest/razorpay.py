"""
Razorpay API client. Fetches payments, settlements, refunds, disputes,
and settlement recon reports.

The HTTP layer is injected (Transport protocol) rather than hardcoded to
`requests` inline in every method -- this is what makes retry/backoff
logic testable without a network call or a live Razorpay account: tests
inject a FakeTransport that fails on cue, production wires a real
RequestsTransport. Same reasoning as core.money's boundary rejections and
verify/'s independence (D-02) -- push the untestable dependency to the
edge, keep the logic in the middle pure and inspectable.
"""

from __future__ import annotations

import time
from typing import Protocol

import requests

from ingest.normalise import normalise_amount, normalise_date, normalise_ts

RAZORPAY_BASE = "https://api.razorpay.com/v1"


class RazorpayAPIError(Exception):
    """Raised after retries are exhausted, or on a non-retryable status."""

    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"Razorpay API error {status_code}: {body[:200]}")


class Transport(Protocol):
    def get(self, url: str, params: dict, auth: tuple[str, str]) -> tuple[int, dict]:
        """Returns (status_code, parsed_json_body). Must not raise on a
        non-2xx status -- the caller decides retry/raise policy, the
        transport's only job is to make the request and hand back what
        came back."""
        ...


class RequestsTransport:
    """Production transport, backed by the `requests` library.
    [UNVERIFIED end-to-end] -- exercised in tests only via FakeTransport;
    this class itself has not been run against a live Razorpay endpoint
    (no test-mode keys configured in this environment). The retry/backoff
    logic it's wrapped in HAS been fully tested against FakeTransport."""

    def __init__(self, timeout_s: float = 10.0):
        self.timeout_s = timeout_s

    def get(self, url: str, params: dict, auth: tuple[str, str]) -> tuple[int, dict]:
        response = requests.get(url, params=params, auth=auth, timeout=self.timeout_s)
        try:
            body = response.json()
        except ValueError:
            body = {}
        return response.status_code, body


_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class RazorpayClient:
    def __init__(
        self,
        key_id: str,
        key_secret: str,
        transport: Transport | None = None,
        max_retries: int = 3,
        backoff_base_s: float = 0.5,
        sleep_fn=time.sleep,
    ):
        self.auth = (key_id, key_secret)
        self.transport = transport or RequestsTransport()
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self._sleep = sleep_fn

    def _get(self, path: str, params: dict | None = None) -> dict:
        """GET with retry + exponential backoff on 429/5xx. Raises
        RazorpayAPIError immediately on a non-retryable status (4xx other
        than 429) -- retrying a 400 or 401 wastes time and hides a real
        integration bug behind a delay.

        Takes params as an explicit dict, not **kwargs -- Razorpay's API
        expects a query parameter literally named `from`, which is a
        Python keyword and cannot be a kwarg name. An explicit dict lets
        callers write params={"from": since, "to": until} directly rather
        than needing a from_ -> from translation step that would be easy
        to forget at one call site and silently send the wrong query key.
        """
        url = f"{RAZORPAY_BASE}{path}"
        attempt = 0
        while True:
            status, body = self.transport.get(url, params or {}, self.auth)
            if 200 <= status < 300:
                return body
            if status not in _RETRYABLE_STATUSES or attempt >= self.max_retries:
                raise RazorpayAPIError(status, str(body))
            self._sleep(self.backoff_base_s * (2**attempt))
            attempt += 1

    def fetch_payments(self, since: int, until: int) -> list[dict]:
        """Returns payments with amounts/timestamps normalised. `since`/
        `until` are Unix epoch seconds, matching Razorpay's own convention."""
        body = self._get("/payments", {"from": since, "to": until})
        return [self._normalise_payment(p) for p in body.get("items", [])]

    def fetch_settlements(self, since: int, until: int) -> list[dict]:
        body = self._get("/settlements", {"from": since, "to": until})
        return [self._normalise_settlement(s) for s in body.get("items", [])]

    def fetch_settlement_recon(self, settlement_id: str) -> list[dict]:
        body = self._get(
            "/settlements/recon/combined", {"settlement_id": settlement_id}
        )
        return body.get("items", [])

    def fetch_refunds(self, since: int, until: int) -> list[dict]:
        body = self._get("/refunds", {"from": since, "to": until})
        return [self._normalise_refund(r) for r in body.get("items", [])]

    def fetch_disputes(self, since: int, until: int) -> list[dict]:
        body = self._get("/disputes", {"from": since, "to": until})
        return [self._normalise_dispute(d) for d in body.get("items", [])]

    @staticmethod
    def _normalise_payment(raw: dict) -> dict:
        return {
            "payment_id": raw["id"],
            "order_id": raw.get("order_id"),
            "amount": normalise_amount(raw["amount"]),
            "method": raw.get("method", "unknown"),
            "fee": normalise_amount(raw.get("fee", 0)),
            "tax": normalise_amount(raw.get("tax", 0)),
            "status": raw["status"],
            "captured_at": normalise_ts(raw["created_at"]) if raw.get("created_at") else None,
            "settlement_id": raw.get("settlement_id"),
        }

    @staticmethod
    def _normalise_settlement(raw: dict) -> dict:
        return {
            "settlement_id": raw["id"],
            "amount": normalise_amount(raw["amount"]),
            "utr": raw.get("utr"),
            "created_at": normalise_ts(raw["created_at"]),
        }

    @staticmethod
    def _normalise_refund(raw: dict) -> dict:
        return {
            "refund_id": raw["id"],
            "payment_id": raw["payment_id"],
            "amount": normalise_amount(raw["amount"]),
            "created_at": normalise_ts(raw["created_at"]),
        }

    @staticmethod
    def _normalise_dispute(raw: dict) -> dict:
        return {
            "dispute_id": raw["id"],
            "payment_id": raw["payment_id"],
            "amount": normalise_amount(raw["amount"]),
            "fee": normalise_amount(raw.get("fee", 0)),
            "status": raw["status"],
            "raised_at": normalise_ts(raw["created_at"]),
        }
