"""
Groq client for T4 residue investigation. See DECISIONS.md D-08: because
the model has NO AUTHORITY (every output is re-derived by verify/, and
hallucinated evidence is rejected before any arithmetic runs --
agent/investigate.py), model quality is not on the correctness path. It
only affects how much of the residue gets a plausible explanation, not
whether anything is trusted. That's what makes a small, fast, cheap
model like Groq's a genuine architectural fit here, not a compromise.

Same edge-injection pattern as ingest/razorpay.py's Transport, for the
same reason: push the untestable dependency (a real network call, and
here there is no GROQ_API_KEY configured in this environment at all) to
the boundary, keep retry/validation logic in the middle fully
unit-testable against a FakeLLMTransport.
"""

from __future__ import annotations

import json
import os
import time
from typing import Protocol

import jsonschema
import requests

from agent.schema import strict_variant

GROQ_BASE = "https://api.groq.com/openai/v1"

# llama-3.1-8b-instant was decommissioned by Groq (confirmed live against
# GET /openai/v1/models on 2026-09-01) -- openai/gpt-oss-20b is the
# current small/fast/cheap replacement in the same tier, matching the
# original "no authority, so model quality doesn't need to be top-tier"
# rationale above. [VERIFY] again before relying on this default -- Groq's
# lineup changes; GROQ_MODEL env var overrides it without a code change.
DEFAULT_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")


class SchemaValidationError(Exception):
    pass


class LLMTransport(Protocol):
    def complete(self, system: str, user: str, model: str, temperature: float, schema: dict) -> str:
        """Returns the raw text content of the model's response (expected
        to be a JSON string conforming to `schema`). Must not raise on a
        malformed response -- returning bogus text and letting the caller's
        JSON-parse/schema-validate step fail is the transport's contract,
        same as ingest.razorpay.Transport returning (status, body) rather
        than raising on a non-2xx status.

        `schema` is passed through so a real transport can request
        API-level constrained decoding (Groq/OpenAI structured outputs)
        instead of relying solely on GroqClient.complete_json's own
        after-the-fact jsonschema.validate() to catch a malformed
        response -- that check stays regardless (defense in depth, D-07:
        the model has no authority), this just reduces how often it needs
        to fire."""
        ...


class RequestsLLMTransport:
    """Production transport, backed by `requests` against Groq's
    OpenAI-compatible chat completions endpoint. [UNVERIFIED end-to-end]
    -- no GROQ_API_KEY is configured in this environment, so this class
    has not been run against a live endpoint. Only the retry/validation
    logic wrapped around it (GroqClient.complete_json) has been tested,
    via FakeLLMTransport -- identical situation to
    ingest.razorpay.RequestsTransport."""

    def __init__(self, api_key: str | None = None, timeout_s: float = 15.0):
        self.api_key = api_key or os.environ.get("GROQ_API_KEY")
        self.timeout_s = timeout_s

    def complete(self, system: str, user: str, model: str, temperature: float, schema: dict) -> str:
        if not self.api_key:
            raise RuntimeError(
                "RequestsLLMTransport has no API key -- set GROQ_API_KEY or pass api_key= explicitly."
            )
        # Strict, API-level constrained decoding (Groq/OpenAI structured
        # outputs), not just "please return JSON" (response_format
        # type=json_object). Caught live: without this, gpt-oss-20b
        # returned syntactically valid JSON that used values outside the
        # hypothesis enum (e.g. "tax", "fee_correction") -- valid JSON,
        # invalid per OUR schema, correctly rejected by
        # GroqClient.complete_json's jsonschema.validate() but only after
        # burning 3 retries and never succeeding. strict:true makes the
        # model incapable of emitting an out-of-enum value in the first
        # place; jsonschema.validate() stays as the real authority
        # (D-07/D-12: the model's output is never trusted un-checked),
        # this just means it usually has nothing to reject.
        payload = {
            "model": model,
            "temperature": temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "hypothesis",
                    "strict": True,
                    "schema": strict_variant(schema),
                },
            },
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        # gpt-oss models spend a large, invisible chunk of the per-minute
        # token budget on hidden chain-of-thought before ever producing
        # the JSON we asked for -- caught live: a free-tier key's 8000
        # TPM cap was exhausted by ONE investigate() call. This
        # classification task needs no deep reasoning, so cap it low
        # rather than switching models. Harmless no-op on models that
        # don't recognize the field (OpenAI-compatible APIs ignore
        # unknown params).
        if "gpt-oss" in model:
            payload["reasoning_effort"] = "low"

        response = requests.post(
            f"{GROQ_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=self.timeout_s,
        )
        response.raise_for_status()
        body = response.json()
        return body["choices"][0]["message"]["content"]


class GroqClient:
    def __init__(
        self,
        transport: LLMTransport | None = None,
        model: str = DEFAULT_MODEL,
        max_retries: int = 2,
        sleep_fn=time.sleep,
    ):
        self.transport = transport or RequestsLLMTransport()
        self.model = model
        self.max_retries = max_retries
        self._sleep = sleep_fn

    def complete_json(
        self, system: str, user: str, schema: dict, temperature: float = 0.0
    ) -> dict:
        """Calls the transport, parses the response as JSON, validates
        against `schema`, and retries (re-calling the transport, not just
        re-parsing the same bad response) on either a JSON parse failure
        or a schema violation -- up to max_retries. Raises
        SchemaValidationError with the last failure's detail after
        retries are exhausted, rather than returning a partially-invalid
        dict a caller might trust by accident.

        temperature=0.0 by default -- DECISIONS.md D-09: not a
        determinism guarantee (sampling at zero still isn't guaranteed
        identical across serving stacks), but it narrows the variance
        band, and the model id is recorded by the caller (ledger, a
        later phase) so a replay discrepancy is explainable rather than
        mysterious.
        """
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            raw_text = self.transport.complete(system, user, self.model, temperature, schema)
            try:
                parsed = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                last_error = exc
                continue

            try:
                jsonschema.validate(parsed, schema)
            except jsonschema.ValidationError as exc:
                last_error = exc
                continue

            return parsed

        raise SchemaValidationError(
            f"model output failed to parse/validate after {self.max_retries + 1} attempt(s): {last_error}"
        )
