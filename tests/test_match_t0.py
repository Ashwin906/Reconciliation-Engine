from gen.build import generate_clean
from gen.defects import DefectSpec, apply_defects
from gen.scenario import default_spec
from match.t0_key import group_payments_by_settlement, link_orders_to_payments


class TestGroupPaymentsBySettlement:
    def test_every_captured_payment_lands_in_exactly_one_group(self):
        ds = generate_clean(default_spec(seed=30))
        groups = group_payments_by_settlement(ds.payments)

        all_grouped = [p.payment_id for members in groups.values() for p in members]
        captured_ids = [p.payment_id for p in ds.payments if p.status == "captured"]

        assert sorted(all_grouped) == sorted(captured_ids)
        assert len(all_grouped) == len(set(all_grouped))  # no payment appears twice

    def test_failed_payments_excluded(self):
        ds = generate_clean(default_spec(seed=30))
        groups = group_payments_by_settlement(ds.payments)
        grouped_ids = {p.payment_id for members in groups.values() for p in members}
        failed_ids = {p.payment_id for p in ds.payments if p.status == "failed"}
        assert grouped_ids.isdisjoint(failed_ids)


class TestLinkOrdersToPayments:
    def test_every_order_maps_to_its_payment_in_clean_data(self):
        ds = generate_clean(default_spec(seed=31))
        mapping, orphan_orders, orphan_payments = link_orders_to_payments(ds.orders, ds.payments)

        assert orphan_orders == []
        assert orphan_payments == []
        assert len(mapping) == len(ds.orders)

    def test_duplicate_order_id_surfaces_as_orphan(self):
        ds = generate_clean(default_spec(seed=32))
        _, truth = apply_defects(ds, [DefectSpec("duplicate_order_id", {"n": 2})], seed=1)
        assert len(truth.defects) > 0

        mapping, orphan_orders, orphan_payments = link_orders_to_payments(ds.orders, ds.payments)

        # each duplicated order_id now has TWO Order records but only one
        # payment -- the second Order for that id has no payment of its own
        duplicated_ids = {d.subject_id for d in truth.defects}
        orphan_order_ids = {o.order_id for o in orphan_orders}
        assert duplicated_ids <= orphan_order_ids
