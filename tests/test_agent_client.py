import json

import pytest

from agent.client import GroqClient, SchemaValidationError

SIMPLE_SCHEMA = {
    "type": "object",
    "required": ["hypothesis"],
    "properties": {"hypothesis": {"type": "string", "enum": ["a", "b"]}},
}


class FakeLLMTransport:
    """Records calls, returns pre-scripted raw text responses in order --
    mirrors tests/test_ingest_razorpay.py's FakeTransport pattern."""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[tuple[str, str, str, float]] = []

    def complete(self, system, user, model, temperature, schema=None):
        self.calls.append((system, user, model, temperature))
        return self.responses.pop(0)


def _client(transport, **kwargs):
    return GroqClient(transport=transport, sleep_fn=lambda _: None, **kwargs)


class TestCompleteJsonSuccessPath:
    def test_valid_response_first_try(self):
        transport = FakeLLMTransport([json.dumps({"hypothesis": "a"})])
        client = _client(transport)
        result = client.complete_json("sys", "user", SIMPLE_SCHEMA)
        assert result == {"hypothesis": "a"}
        assert len(transport.calls) == 1

    def test_temperature_and_model_passed_through(self):
        transport = FakeLLMTransport([json.dumps({"hypothesis": "a"})])
        client = _client(transport, model="test-model")
        client.complete_json("sys", "user", SIMPLE_SCHEMA, temperature=0.0)
        _, _, model, temperature = transport.calls[0]
        assert model == "test-model"
        assert temperature == 0.0


class TestCompleteJsonRetries:
    def test_retries_on_invalid_json_then_succeeds(self):
        transport = FakeLLMTransport(["not json at all", json.dumps({"hypothesis": "b"})])
        client = _client(transport)
        result = client.complete_json("sys", "user", SIMPLE_SCHEMA)
        assert result == {"hypothesis": "b"}
        assert len(transport.calls) == 2

    def test_retries_on_schema_violation_then_succeeds(self):
        transport = FakeLLMTransport(
            [json.dumps({"hypothesis": "not_in_enum"}), json.dumps({"hypothesis": "a"})]
        )
        client = _client(transport)
        result = client.complete_json("sys", "user", SIMPLE_SCHEMA)
        assert result == {"hypothesis": "a"}

    def test_raises_after_exhausting_retries(self):
        transport = FakeLLMTransport(["garbage"] * 10)
        client = _client(transport, max_retries=2)
        with pytest.raises(SchemaValidationError):
            client.complete_json("sys", "user", SIMPLE_SCHEMA)
        assert len(transport.calls) == 3  # initial + 2 retries

    def test_missing_required_field_is_rejected(self):
        transport = FakeLLMTransport([json.dumps({}), json.dumps({"hypothesis": "a"})])
        client = _client(transport)
        result = client.complete_json("sys", "user", SIMPLE_SCHEMA)
        assert result == {"hypothesis": "a"}
