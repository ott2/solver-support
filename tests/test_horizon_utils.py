#!/usr/bin/env python3
"""Unit tests for horizon_utils module."""

import pytest
from pathlib import Path
import tempfile
import logging
from solver_support.horizon_utils import (
    create_horizon_param_file, horizon_search, reconcile_horizon_bounds)
from solver_support.file_management import SolutionFileNamer
from solver_support.run_summary import RunSummary
from solver_support.global_state import global_state


class TestReconcileHorizonBounds:
    """The one rule both the CLI and horizon_search resolve bounds by.

    Keeping this in a single function is what stops the two layers from drifting
    (they once disagreed on the start-above-cap case).
    """

    def test_normal_bounds_unchanged(self):
        assert reconcile_horizon_bounds(1, 100) == (1, 100)
        assert reconcile_horizon_bounds(3, 20) == (3, 20)

    def test_start_above_stop_raises_cap_to_start(self):
        # The start wins and the cap is raised to meet it - never an empty range.
        assert reconcile_horizon_bounds(5, 3) == (5, 5)

    def test_min_horizon_floors_the_start(self):
        # Conjure/Essence models pass min_horizon=2.
        assert reconcile_horizon_bounds(1, 100, min_horizon=2) == (2, 100)

    def test_min_horizon_floor_also_raises_cap(self):
        # A tiny cap below the floor is lifted to the floored start.
        assert reconcile_horizon_bounds(1, 1, min_horizon=2) == (2, 2)

    def test_start_at_stop_is_a_single_horizon(self):
        assert reconcile_horizon_bounds(7, 7) == (7, 7)


class TestCreateHorizonParamFile:
    """Tests for create_horizon_param_file function."""
    
    def test_normal_horizon_param_file(self, tmp_path, caplog):
        """Test creating horizon param file without continue-scan constraints."""
        # Create test parameter file
        param_file = tmp_path / "test.param"
        param_file.write_text("$ Test parameter file\nletting foo be 1\nletting bar be 2\n")
        
        logger = logging.getLogger(__name__)
        
        # Create horizon param file
        horizon_param = create_horizon_param_file(
            param_file, tmp_path, 10, logger
        )
        
        # Verify content
        content = horizon_param.read_text()
        # Base file should have original content
        assert "$ Test parameter file" in content
        assert "letting foo be 1" in content
        assert "letting bar be 2" in content
        # Should not have horizon constraint yet (added by caller)
        assert "letting horizon be" not in content
        assert "such that obj >" not in content
        # Check file naming (without runner, uses simple format)
        assert horizon_param.name == "test-10.param"
        
    def test_horizon_param_file_name_format(self, tmp_path):
        """Test the naming convention for horizon param files."""
        param_file = tmp_path / "model.param"
        param_file.write_text("letting x be 5\n")
        
        logger = logging.getLogger(__name__)
        
        # Test various horizon values
        for horizon in [5, 20, 100]:
            horizon_param = create_horizon_param_file(
                param_file, tmp_path, horizon, logger
            )
            # Without runner, uses simple format with zero-padded horizon
            expected_name = f"model-{horizon:02d}.param"
            assert horizon_param.name == expected_name
            # Horizon constraint is added by caller, not by create_horizon_param_file
            assert f"letting horizon be {horizon}" not in horizon_param.read_text()
    
    def test_param_file_content_preservation(self, tmp_path):
        """Test that original parameter file content is preserved."""
        original_content = """$ Original comments
letting width be 5
letting height be 7
letting blocks be relation (
    (1, 2, "R"),
    (3, 4, "G")
)
"""
        param_file = tmp_path / "original.param"
        param_file.write_text(original_content)
        
        logger = logging.getLogger(__name__)
        
        horizon_param = create_horizon_param_file(
            param_file, tmp_path, 15, logger
        )
        
        content = horizon_param.read_text()
        # Check original content is preserved
        assert "$ Original comments" in content
        assert "letting width be 5" in content
        assert "letting height be 7" in content
        assert "letting blocks be relation" in content
        # Horizon constraint is NOT added by create_horizon_param_file
        assert "letting horizon be 15" not in content
    
    def test_output_directory_creation(self, tmp_path):
        """Test that horizon param files are created in the specified directory."""
        param_file = tmp_path / "input" / "test.param"
        param_file.parent.mkdir()
        param_file.write_text("letting x be 1\n")
        
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        
        logger = logging.getLogger(__name__)
        
        horizon_param = create_horizon_param_file(
            param_file, output_dir, 10, logger
        )
        
        # Verify file is created in output directory
        assert horizon_param.parent == output_dir
        assert horizon_param.exists()


class _StubRunner:
    """Minimal runner exposing the .summary attribute horizon_search needs."""

    def __init__(self, summary):
        self.summary = summary


class TestHorizonSearchInterruptionSalvage:
    """Regression tests for salvaging a found solution on interruption.

    A continue-scan run keeps a best solution from an earlier horizon on disk
    while it scans further. When the harness SIGTERMs the run (e.g. an -O2
    Savile Row solve that overran its per-horizon timelimit), horizon_search
    must not discard that solution: it should be promoted to the main solution
    and its score recorded, exactly as the graceful timeout path does.
    """

    @pytest.fixture(autouse=True)
    def _reset_interrupt(self):
        """Keep the shared global_state interrupt counter from leaking out."""
        saved = global_state.interrupt_count
        global_state.interrupt_count = 0
        yield
        global_state.interrupt_count = saved

    def test_interruption_promotes_best_solution_and_records_score(
        self, tmp_path, monkeypatch, fixture_registry, wired_timeout_score
    ):
        # Salvaging a score needs both seams wired: the registry says this model
        # keeps its objective in the solution file, and the consumer's extractor
        # reads it (a stand-in here -- 300 for any salvaged file that exists).
        monkeypatch.chdir(tmp_path)
        logger = logging.getLogger(__name__)

        summary = RunSummary(
            instance="inst", model="scored", solver="kissat",
            timestamp="20260101000000", output_dir=str(tmp_path),
        )
        runner = _StubRunner(summary)

        param_file = tmp_path / "inst.param"
        param_file.write_text("letting foo be 1\n")

        def callback(n, horizon_param_file):
            # Mimic continue-scan: stash a best solution for this horizon, keep
            # scanning (return None), then have the harness interrupt us so the
            # next horizon boundary takes the interruption branch.
            best = Path(SolutionFileNamer.generate_solution_filename(
                "inst", "kissat", "scored", "20260101000000",
                horizon=n, suffix=".best.solution"))
            best.write_text("a solution the consumer's extractor would read\n")
            global_state.interrupt_count = 2  # forced termination, as SIGTERM does
            return None

        result = horizon_search(runner, param_file, startat=1,
                                horizon_callback=callback, logger=logger)

        # The earlier horizon's solution is salvaged into a main solution file...
        assert result is not None
        assert result.exists()
        # ...and its score is recorded rather than silently dropped.
        score_phase = summary.get_phase("final-timeout-solution")
        assert score_phase is not None
        assert score_phase.solver_stats.get("score") == 300  # what the hook reported
        assert summary.score == 300

    def test_interruption_without_solution_returns_none(self, tmp_path, monkeypatch):
        """No best solution in hand -> nothing to salvage, still returns None."""
        monkeypatch.chdir(tmp_path)
        logger = logging.getLogger(__name__)

        summary = RunSummary(
            instance="inst", model="scored", solver="kissat",
            timestamp="20260101000000", output_dir=str(tmp_path),
        )
        runner = _StubRunner(summary)

        param_file = tmp_path / "inst.param"
        param_file.write_text("letting foo be 1\n")

        def callback(n, horizon_param_file):
            global_state.interrupt_count = 2
            return None  # no .best.solution written -> nothing to salvage

        result = horizon_search(runner, param_file, startat=1,
                                horizon_callback=callback, logger=logger)

        assert result is None
        assert summary.get_phase("final-timeout-solution") is None


class TestHorizonSearchExhaustion:
    """Reaching the horizon cap without deciding flags 'horizon limit reached'.

    A horizon-scanning CP model that runs through every horizon without finding
    a solution (and without timing out or being interrupted) has not proven
    unsatisfiability - it has merely run out of horizons. horizon_search must
    flag this on the runner so the orchestrator can record final_status 'hlim'
    rather than conflating it with a genuine 'failed'.
    """

    @pytest.fixture(autouse=True)
    def _reset_interrupt(self):
        saved = global_state.interrupt_count
        global_state.interrupt_count = 0
        yield
        global_state.interrupt_count = saved

    def test_exhausting_horizons_sets_flag(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        logger = logging.getLogger(__name__)

        summary = RunSummary(
            instance="inst", model="demo", solver="kissat",
            timestamp="20260101000000", output_dir=str(tmp_path),
        )
        runner = _StubRunner(summary)

        param_file = tmp_path / "inst.param"
        param_file.write_text("letting foo be 1\n")

        # Never solves, never times out, never interrupted: the loop runs to the
        # cap and falls through. Start near the cap to keep the test quick.
        calls = []

        def callback(n, horizon_param_file):
            calls.append(n)
            return None

        result = horizon_search(runner, param_file, startat=99,
                                horizon_callback=callback, logger=logger)

        assert result is None
        assert calls == [99, 100]  # scanned through to the cap
        assert getattr(runner, 'horizon_limit_reached', False) is True

    def test_solution_found_does_not_set_flag(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        logger = logging.getLogger(__name__)

        summary = RunSummary(
            instance="inst", model="demo", solver="kissat",
            timestamp="20260101000000", output_dir=str(tmp_path),
        )
        runner = _StubRunner(summary)

        param_file = tmp_path / "inst.param"
        param_file.write_text("letting foo be 1\n")

        solution = tmp_path / "inst.solution"
        solution.write_text("letting x be 1\n")

        def callback(n, horizon_param_file):
            return solution  # solves immediately at the first horizon

        result = horizon_search(runner, param_file, startat=99,
                                horizon_callback=callback, logger=logger)

        assert result == solution
        assert getattr(runner, 'horizon_limit_reached', False) is False


class TestHorizonSearchStopat:
    """`stopat` overrides the default upper limit of the horizon scan.

    The scan normally runs up to horizon 100; `--stopat N` (threaded down to
    horizon_search as `stopat`) lets a caller lower the cap to fail fast on easy
    instances or raise it past 100 for deep, hard instances.
    """

    @pytest.fixture(autouse=True)
    def _reset_interrupt(self):
        saved = global_state.interrupt_count
        global_state.interrupt_count = 0
        yield
        global_state.interrupt_count = saved

    def _run(self, tmp_path, startat, stopat, min_horizon=1):
        summary = RunSummary(
            instance="inst", model="demo", solver="kissat",
            timestamp="20260101000000", output_dir=str(tmp_path),
        )
        runner = _StubRunner(summary)

        param_file = tmp_path / "inst.param"
        param_file.write_text("letting foo be 1\n")

        calls = []

        def callback(n, horizon_param_file):
            calls.append(n)
            return None  # never solves, so the loop runs to the cap

        result = horizon_search(runner, param_file, startat=startat,
                                horizon_callback=callback,
                                logger=logging.getLogger(__name__),
                                min_horizon=min_horizon, stopat=stopat)
        return result, calls, runner

    def test_stopat_lowers_the_cap(self, tmp_path, monkeypatch):
        """A low stopat ends the scan early and still flags hlim."""
        monkeypatch.chdir(tmp_path)
        result, calls, runner = self._run(tmp_path, startat=1, stopat=3)
        assert result is None
        assert calls == [1, 2, 3]  # stopped at the overridden cap, not 100
        assert getattr(runner, 'horizon_limit_reached', False) is True

    def test_stopat_above_100(self, tmp_path, monkeypatch):
        """A stopat above the historical 100 default extends the scan."""
        monkeypatch.chdir(tmp_path)
        result, calls, runner = self._run(tmp_path, startat=99, stopat=102)
        assert result is None
        assert calls == [99, 100, 101, 102]

    def test_stopat_below_startat_still_tries_start(self, tmp_path, monkeypatch):
        """A contradictory stopat < startat is clamped up so the start is tried."""
        monkeypatch.chdir(tmp_path)
        result, calls, runner = self._run(tmp_path, startat=5, stopat=3)
        assert calls == [5]  # effective_stop = max(stopat, effective_start)

    def test_stopat_default_matches_min_horizon_floor(self, tmp_path, monkeypatch):
        """A Conjure-style min_horizon=2 with a tiny stopat still tries horizon 2."""
        monkeypatch.chdir(tmp_path)
        result, calls, runner = self._run(tmp_path, startat=1, stopat=1, min_horizon=2)
        assert calls == [2]  # floored start 2, cap clamped to 2
