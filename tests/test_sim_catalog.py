import inspect

import pytest

from gen.defects import _REGISTRY
from sim.catalog import coerce_params, defect_catalog


class TestCatalogIsDerivedFromRealSignatures:
    def test_every_registered_injector_appears(self):
        kinds = {e["kind"] for e in defect_catalog()}
        assert kinds == set(_REGISTRY)

    def test_params_match_the_actual_function_signature(self):
        """The whole point of introspecting rather than hand-listing: a UI
        control that does not correspond to a real parameter would either
        do nothing or crash the injector."""
        for entry in defect_catalog():
            signature = inspect.signature(_REGISTRY[entry["kind"]])
            expected = {n for n in signature.parameters if n not in {"ds", "rng"}}
            assert {p["name"] for p in entry["params"]} == expected, entry["kind"]

    def test_fee_overcharge_exposes_its_three_real_params_not_n(self):
        entry = next(e for e in defect_catalog() if e["kind"] == "fee_overcharge")
        assert {p["name"] for p in entry["params"]} == {"method", "extra_bps", "days"}

    def test_optional_param_reports_its_real_default(self):
        entry = next(e for e in defect_catalog() if e["kind"] == "reserve_hold")
        reserve = next(p for p in entry["params"] if p["name"] == "reserve_bps")
        assert reserve["required"] is False
        assert reserve["default"] == 500


class TestCoerceParams:
    def test_accepts_valid_params(self):
        assert coerce_params("fee_overcharge", {"method": "card", "extra_bps": 50, "days": 30}) == {
            "method": "card",
            "extra_bps": 50,
            "days": 30,
        }

    def test_fills_omitted_required_params_so_a_partial_request_still_runs(self):
        assert coerce_params("unbooked_chargeback", {}) == {"n": 1}

    def test_rejects_unknown_param_rather_than_silently_dropping_it(self):
        """Silently ignoring it would let a viewer believe they changed
        something they did not."""
        with pytest.raises(ValueError, match="no parameter"):
            coerce_params("unbooked_chargeback", {"nope": 1})

    def test_rejects_unknown_kind(self):
        with pytest.raises(ValueError, match="unknown defect kind"):
            coerce_params("not_a_defect", {})

    def test_rejects_invalid_choice(self):
        with pytest.raises(ValueError, match="must be one of"):
            coerce_params("fee_overcharge", {"method": "bitcoin"})

    def test_rejects_non_numeric(self):
        with pytest.raises(ValueError, match="whole number"):
            coerce_params("unbooked_chargeback", {"n": "lots"})

    def test_clamps_server_side_independently_of_the_ui(self):
        """An unbounded n would let one request generate an arbitrarily
        large dataset -- the slider's range is a convenience, this is the
        actual limit."""
        assert coerce_params("unbooked_chargeback", {"n": 10_000})["n"] == 8
        assert coerce_params("unbooked_chargeback", {"n": -5})["n"] == 1
