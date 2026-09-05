import pytest

from ingest.razorpay import RazorpayAPIError, RazorpayClient


class FakeTransport:
    """Records every call and returns pre-scripted (status, body) pairs
    in order -- lets tests drive retry/backoff without a real network."""

    def __init__(self, responses: list[tuple[int, dict]]):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params, auth):
        self.calls.append((url, dict(params)))
        return self.responses.pop(0)


def _client(transport, **kwargs):
    return RazorpayClient(
        "key_id",
        "key_secret",
        transport=transport,
        sleep_fn=lambda _: None,  # no real sleeping in tests
        **kwargs,
    )


class TestSuccessPath:
    def test_fetch_payments_normalises_amounts_and_ids(self):
        transport = FakeTransport(
            [
                (
                    200,
                    {
                        "items": [
                            {
                                "id": "pay_ABC",
                                "order_id": "order_XYZ",
                                "amount": 100000,
                                "method": "upi",
                                "fee": 0,
                                "tax": 0,
                                "status": "captured",
                                "created_at": 1738483200,
                                "settlement_id": "setl_1",
                            }
                        ]
                    },
                )
            ]
        )
        client = _client(transport)
        result = client.fetch_payments(since=1738400000, until=1738500000)

        assert len(result) == 1
        assert result[0]["payment_id"] == "pay_ABC"
        assert result[0]["amount"] == 100000
        assert result[0]["settlement_id"] == "setl_1"

    def test_query_params_use_real_from_not_from_underscore(self):
        # the actual bug caught while building this: `from` is a Python
        # keyword, easy to accidentally send as "from_" in the real query
        transport = FakeTransport([(200, {"items": []})])
        client = _client(transport)
        client.fetch_payments(since=111, until=222)

        _, params = transport.calls[0]
        assert params == {"from": 111, "to": 222}
        assert "from_" not in params


class TestRetryBehaviour:
    def test_retries_on_429_then_succeeds(self):
        transport = FakeTransport(
            [
                (429, {}),
                (200, {"items": []}),
            ]
        )
        client = _client(transport)
        result = client.fetch_payments(since=1, until=2)
        assert result == []
        assert len(transport.calls) == 2

    def test_retries_on_5xx_then_succeeds(self):
        transport = FakeTransport(
            [
                (503, {}),
                (502, {}),
                (200, {"items": []}),
            ]
        )
        client = _client(transport)
        client.fetch_payments(since=1, until=2)
        assert len(transport.calls) == 3

    def test_gives_up_after_max_retries(self):
        transport = FakeTransport([(500, {})] * 10)
        client = _client(transport, max_retries=2)
        with pytest.raises(RazorpayAPIError):
            client.fetch_payments(since=1, until=2)
        # initial attempt + 2 retries = 3 calls total
        assert len(transport.calls) == 3

    def test_does_not_retry_on_non_retryable_4xx(self):
        transport = FakeTransport([(401, {"error": "bad auth"}), (200, {"items": []})])
        client = _client(transport)
        with pytest.raises(RazorpayAPIError) as exc_info:
            client.fetch_payments(since=1, until=2)
        assert exc_info.value.status_code == 401
        # must NOT have consumed the second scripted response -- proves
        # it failed immediately rather than retrying a non-retryable error
        assert len(transport.calls) == 1


class TestNormalisation:
    def test_settlement_normalisation(self):
        transport = FakeTransport(
            [
                (
                    200,
                    {
                        "items": [
                            {
                                "id": "setl_1",
                                "amount": 441740,
                                "utr": "UTR001",
                                "created_at": 1738483200,
                            }
                        ]
                    },
                )
            ]
        )
        client = _client(transport)
        result = client.fetch_settlements(since=1, until=2)
        assert result[0]["settlement_id"] == "setl_1"
        assert result[0]["amount"] == 441740
        assert result[0]["utr"] == "UTR001"
        assert result[0]["created_at"].tzinfo is not None

    def test_dispute_fee_normalisation_not_the_gateway_dispute_id_bug(self):
        # regression test for a real bug: the first version of
        # _normalise_dispute computed fee as
        # `raw.get("gateway_dispute_id", 0) and raw.get("fee", 0)`,
        # nonsense left over from editing. This proves fee comes from
        # the actual "fee" field regardless of gateway_dispute_id's value.
        transport = FakeTransport(
            [
                (
                    200,
                    {
                        "items": [
                            {
                                "id": "disp_1",
                                "payment_id": "pay_1",
                                "amount": 40000,
                                "fee": 10000,
                                "gateway_dispute_id": "gw_123",
                                "status": "raised",
                                "created_at": 1738483200,
                            }
                        ]
                    },
                )
            ]
        )
        client = _client(transport)
        result = client.fetch_disputes(since=1, until=2)
        assert result[0]["fee"] == 10000
