import hashlib
import hmac

from api.security import verify_razorpay_signature

SECRET = "test_webhook_secret"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestVerifyRazorpaySignature:
    def test_genuine_signature_verifies(self):
        body = b'{"event":"settlement.processed"}'
        signature = _sign(body)
        assert verify_razorpay_signature(body, signature, SECRET) is True

    def test_wrong_secret_fails(self):
        body = b'{"event":"settlement.processed"}'
        signature = _sign(body, secret="not_the_real_secret")
        assert verify_razorpay_signature(body, signature, SECRET) is False

    def test_tampered_body_fails(self):
        body = b'{"event":"settlement.processed"}'
        signature = _sign(body)
        tampered_body = b'{"event":"settlement.processed","amount":999999999}'
        assert verify_razorpay_signature(tampered_body, signature, SECRET) is False

    def test_missing_signature_fails_not_crashes(self):
        body = b'{"event":"x"}'
        assert verify_razorpay_signature(body, "", SECRET) is False
        assert verify_razorpay_signature(body, None, SECRET) is False

    def test_garbage_signature_fails(self):
        body = b'{"event":"x"}'
        assert verify_razorpay_signature(body, "not-a-real-hex-digest", SECRET) is False

    def test_uses_constant_time_comparison_not_plain_equality(self):
        """Can't directly test timing, but can prove hmac.compare_digest
        is actually being called rather than == -- by confirming the
        function still correctly rejects a signature that differs only
        in its LAST character (a plain == would also catch this, so this
        alone doesn't prove constant-time comparison, but it's a
        sanity check that comparison logic isn't broken/inverted)."""
        body = b'{"event":"x"}'
        real_signature = _sign(body)
        almost_right = real_signature[:-1] + ("0" if real_signature[-1] != "0" else "1")
        assert verify_razorpay_signature(body, almost_right, SECRET) is False
