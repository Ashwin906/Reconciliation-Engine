from pathlib import Path

import pytest

from ingest.bank.column_mapper import ColumnMap, apply_mapping, learn_mapping
from ingest.bank.generic_csv import GenericCsvAdapter

SCRATCH = Path(__file__).parent / "_scratch"
SCRATCH.mkdir(exist_ok=True)


def _write_csv(name: str, content: str) -> Path:
    path = SCRATCH / name
    path.write_text(content, encoding="utf-8")
    return path


class TestGenericCsvAdapterSniff:
    def test_high_confidence_on_standard_headers(self):
        adapter = GenericCsvAdapter()
        headers = ["Date", "Narration", "Chq/Ref No", "Debit", "Credit", "Balance"]
        assert adapter.sniff(headers) == 1.0

    def test_zero_confidence_missing_date(self):
        adapter = GenericCsvAdapter()
        headers = ["Narration", "Debit", "Credit"]
        assert adapter.sniff(headers) == 0.0

    def test_zero_confidence_missing_both_debit_and_credit(self):
        adapter = GenericCsvAdapter()
        headers = ["Date", "Narration", "Balance"]
        assert adapter.sniff(headers) == 0.0

    def test_partial_confidence_missing_optional_fields(self):
        adapter = GenericCsvAdapter()
        headers = ["Date", "Narration", "Credit"]  # no ref_no, debit, balance
        confidence = adapter.sniff(headers)
        assert 0.0 < confidence < 1.0


class TestGenericCsvAdapterParse:
    def test_parses_standard_format(self):
        csv_content = (
            "Date,Narration,Chq/Ref No,Debit,Credit,Balance\n"
            "02/09/2026,NEFT CR RAZORPAY SOFTWARE,UTR001,,95679.28,195679.28\n"
            "03/09/2026,ATM WDL,,5000.00,,190679.28\n"
        )
        path = _write_csv("standard.csv", csv_content)
        adapter = GenericCsvAdapter()
        lines = adapter.parse(path)

        assert len(lines) == 2
        assert lines[0].credit == 9567928
        assert lines[0].debit == 0
        assert lines[0].ref_no == "UTR001"
        assert lines[0].narration == "NEFT CR RAZORPAY SOFTWARE"
        assert lines[1].debit == 500000
        assert lines[1].credit == 0

    def test_raises_on_missing_required_columns(self):
        csv_content = "Foo,Bar\n1,2\n"
        path = _write_csv("bad.csv", csv_content)
        adapter = GenericCsvAdapter()
        with pytest.raises(ValueError, match="cannot parse"):
            adapter.parse(path)

    def test_handles_missing_optional_ref_no(self):
        csv_content = "Date,Narration,Debit,Credit\n02/09/2026,Some txn,,1000.00\n"
        path = _write_csv("no_ref.csv", csv_content)
        adapter = GenericCsvAdapter()
        lines = adapter.parse(path)
        assert lines[0].ref_no is None
        assert lines[0].credit == 100000


class TestColumnMap:
    def test_missing_required_field_raises(self):
        with pytest.raises(ValueError, match="missing required"):
            ColumnMap(fields={"narration": "Description"})  # no txn_date

    def test_unknown_field_raises(self):
        with pytest.raises(ValueError, match="unknown field"):
            ColumnMap(fields={"txn_date": "Date", "narration": "Desc", "bogus_field": "X"})

    def test_valid_map_constructs(self):
        cm = ColumnMap(fields={"txn_date": "Date", "narration": "Desc"})
        assert cm.fields["txn_date"] == "Date"


class FakeLLMClient:
    def __init__(self, response: dict[str, str]):
        self.response = response
        self.calls = []

    def map_columns(self, headers, sample_rows):
        self.calls.append((headers, sample_rows))
        return self.response


class TestLearnMapping:
    def test_valid_response_produces_column_map(self):
        headers = ["Txn Dt", "Particulars", "Amt Cr", "Amt Dr"]
        client = FakeLLMClient(
            {"txn_date": "Txn Dt", "narration": "Particulars", "credit": "Amt Cr", "debit": "Amt Dr"}
        )
        cm = learn_mapping(headers, sample_rows=[], client=client)
        assert cm.fields["txn_date"] == "Txn Dt"
        assert len(client.calls) == 1  # called exactly once -- D-19's whole point

    def test_rejects_invented_field_name(self):
        headers = ["Date", "Desc"]
        client = FakeLLMClient({"txn_date": "Date", "narration": "Desc", "made_up_field": "Date"})
        with pytest.raises(ValueError, match="invented unknown field"):
            learn_mapping(headers, sample_rows=[], client=client)

    def test_rejects_hallucinated_header_not_in_file(self):
        headers = ["Date", "Desc"]
        client = FakeLLMClient({"txn_date": "Date", "narration": "A Header That Does Not Exist"})
        with pytest.raises(ValueError, match="not one of the actual file headers"):
            learn_mapping(headers, sample_rows=[], client=client)

    def test_no_client_configured_raises_clearly(self):
        with pytest.raises(RuntimeError, match="No LLM client configured"):
            learn_mapping(["Date", "Desc"], sample_rows=[])


class TestApplyMapping:
    def test_applies_learned_mapping_to_rows(self):
        cm = ColumnMap(
            fields={"txn_date": "Txn Dt", "narration": "Particulars", "credit": "Amt Cr"}
        )
        rows = [
            {"Txn Dt": "02/09/2026", "Particulars": "  Salary  Credit  ", "Amt Cr": "50000.00"}
        ]
        lines = apply_mapping(rows, cm)
        assert len(lines) == 1
        assert lines[0].credit == 5000000
        assert lines[0].narration == "SALARY CREDIT"
        assert lines[0].debit == 0
        assert lines[0].ref_no is None


class TestSignedAmountFormat:
    """Banks that export ONE amount column carrying direction by sign,
    instead of a debit/credit pair -- previously an unsupported KNOWN GAP
    in ColumnMap. Downstream code only ever sees debit/credit, so the
    split has to happen here."""

    def test_negative_signed_amount_becomes_a_debit(self):
        cm = ColumnMap(
            fields={"txn_date": "Date", "narration": "Desc", "signed_amount": "Amount"}
        )
        rows = [{"Date": "02/09/2026", "Desc": "UPI payout", "Amount": "-1234.56"}]
        lines = apply_mapping(rows, cm)
        assert lines[0].debit == 123456
        assert lines[0].credit == 0

    def test_positive_signed_amount_becomes_a_credit(self):
        cm = ColumnMap(
            fields={"txn_date": "Date", "narration": "Desc", "signed_amount": "Amount"}
        )
        rows = [{"Date": "02/09/2026", "Desc": "Settlement", "Amount": "5000.00"}]
        lines = apply_mapping(rows, cm)
        assert lines[0].credit == 500000
        assert lines[0].debit == 0

    def test_accounting_style_parenthesised_negative_becomes_a_debit(self):
        cm = ColumnMap(
            fields={"txn_date": "Date", "narration": "Desc", "signed_amount": "Amount"}
        )
        rows = [{"Date": "02/09/2026", "Desc": "Chargeback", "Amount": "(750.00)"}]
        lines = apply_mapping(rows, cm)
        assert lines[0].debit == 75000
        assert lines[0].credit == 0

    def test_blank_signed_amount_is_zero_not_a_crash(self):
        cm = ColumnMap(
            fields={"txn_date": "Date", "narration": "Desc", "signed_amount": "Amount"}
        )
        rows = [{"Date": "02/09/2026", "Desc": "Memo line", "Amount": "   "}]
        lines = apply_mapping(rows, cm)
        assert lines[0].debit == 0
        assert lines[0].credit == 0

    def test_both_conventions_at_once_is_rejected(self):
        """Ambiguous by construction -- which wins if they disagree?
        Rejected up front rather than resolved by a silent precedence rule."""
        with pytest.raises(ValueError, match="both"):
            ColumnMap(
                fields={
                    "txn_date": "Date",
                    "narration": "Desc",
                    "signed_amount": "Amount",
                    "credit": "Cr",
                }
            )
