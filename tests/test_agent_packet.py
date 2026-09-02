from agent.packet import PacketConfig, build_packet
from gen.build import generate_clean
from gen.defects import DefectSpec, apply_defects
from gen.scenario import default_spec
from match.types import Unmatched


class TestBuildPacket:
    def test_includes_correct_member_payments_refunds_disputes(self):
        ds = generate_clean(default_spec(seed=400))
        ds, truth = apply_defects(ds, [DefectSpec("unbooked_chargeback", {"n": 1})], seed=1)
        settlement_id = truth.defects[0].subject_id

        unmatched = Unmatched(
            kind="settlement", id=settlement_id, reason="amount_mismatch", detail={}
        )
        packet = build_packet(unmatched, ds, PacketConfig())

        settlement = next(s for s in ds.settlements if s.settlement_id == settlement_id)
        expected_member_ids = {
            p.payment_id for p in ds.payments if p.settlement_id == settlement_id
        }
        packet_member_ids = {p["payment_id"] for p in packet["member_payments"]}
        assert packet_member_ids == expected_member_ids

        # the injected dispute must appear -- it's the whole point of the packet
        packet_dispute_ids = {d["dispute_id"] for d in packet["disputes_on_members"]}
        expected_dispute = next(
            d for d in ds.disputes if d.payment_id in expected_member_ids
        )
        assert expected_dispute.dispute_id in packet_dispute_ids

    def test_excludes_disputes_on_unrelated_payments(self):
        """Precision, not just recall -- a dispute on a payment NOT
        belonging to this settlement must not leak into the packet."""
        ds = generate_clean(default_spec(seed=401))
        target_settlement = ds.settlements[0]
        other_settlement_payment = next(
            p for p in ds.payments
            if p.status == "captured" and p.settlement_id != target_settlement.settlement_id
        )
        from gen.models import Dispute
        from datetime import datetime

        ds.disputes.append(
            Dispute("disp_unrelated", other_settlement_payment.payment_id, 10000, 5000, "raised",
                    datetime(2026, 2, 3, 10, 0))
        )

        unmatched = Unmatched(
            kind="settlement", id=target_settlement.settlement_id, reason="x", detail={}
        )
        packet = build_packet(unmatched, ds, PacketConfig())
        packet_dispute_ids = {d["dispute_id"] for d in packet["disputes_on_members"]}
        assert "disp_unrelated" not in packet_dispute_ids

    def test_unknown_settlement_id_produces_empty_but_valid_packet(self):
        ds = generate_clean(default_spec(seed=402))
        unmatched = Unmatched(kind="settlement", id="setl_does_not_exist", reason="x", detail={})
        packet = build_packet(unmatched, ds, PacketConfig())
        assert packet["settlement"] is None
        assert packet["member_payments"] == []

    def test_nearby_bank_lines_respects_window(self):
        ds = generate_clean(default_spec(seed=403))
        settlement = ds.settlements[0]
        unmatched = Unmatched(
            kind="settlement", id=settlement.settlement_id, reason="x", detail={}
        )
        packet = build_packet(unmatched, ds, PacketConfig(window_days=7))

        for line in packet["nearby_bank_lines"]:
            from datetime import date

            line_date = date.fromisoformat(line["txn_date"])
            settlement_date = settlement.created_at.date()
            assert abs((line_date - settlement_date).days) <= 7

    def test_rate_card_included_when_configured(self):
        ds = generate_clean(default_spec(seed=404))
        settlement = ds.settlements[0]
        unmatched = Unmatched(kind="settlement", id=settlement.settlement_id, reason="x", detail={})
        cfg = PacketConfig(rate_card_bps={"upi": 0, "card": 200})
        packet = build_packet(unmatched, ds, cfg)
        assert packet["rate_card_bps"] == {"upi": 0, "card": 200}
