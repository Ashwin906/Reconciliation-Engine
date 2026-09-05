from evalh.cli import run_eval


class TestRunEval:
    def test_writes_both_output_files(self, tmp_path):
        out_dir = tmp_path / "results"
        run_eval(seed=42, out_dir=out_dir)

        metrics_file = out_dir / "metrics.md"
        exceptions_file = out_dir / "exceptions.md"
        assert metrics_file.exists()
        assert exceptions_file.exists()

        metrics_content = metrics_file.read_text(encoding="utf-8")
        assert "BATCH" in metrics_content
        assert "GROUND TRUTH" in metrics_content

    def test_deterministic_given_same_seed(self, tmp_path):
        """Not a full byte-for-byte file comparison: metrics.md's BATCH
        line includes real wall-clock duration_s, which is SUPPOSED to
        vary between runs -- that's correct behaviour, not a
        reproducibility gap. (A first version of this test asserted
        full file equality and intermittently failed across two
        real process runs for exactly that reason -- not a bug in
        run_eval, a bug in the test's own expectation.) Compares every
        line except the one containing the timing measurement."""
        out1 = tmp_path / "run1"
        out2 = tmp_path / "run2"
        run_eval(seed=7, out_dir=out1)
        run_eval(seed=7, out_dir=out2)

        def _strip_timing(text: str) -> str:
            return "\n".join(
                line for line in text.splitlines() if not line.startswith("BATCH")
            )

        m1 = (out1 / "metrics.md").read_text()
        m2 = (out2 / "metrics.md").read_text()
        assert _strip_timing(m1) == _strip_timing(m2)

        assert (out1 / "exceptions.md").read_text() == (out2 / "exceptions.md").read_text()
