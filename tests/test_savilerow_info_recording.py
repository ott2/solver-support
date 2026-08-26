"""What ``_record_savilerow_times`` keeps from each horizon's ``.info`` file.

Savile Row writes a ``<param>.info`` per horizon. Two of its fields are renamed
into the core's normalised vocabulary (``savilerow_time`` / ``solver_time``); the
rest used to be parsed and dropped, so a consumer wanting the encoding size or
the search-effort counters had to re-parse the files itself -- duplicating the
parser, and depending on the run's output directory rather than its summary, so
it stopped working once those files were cleaned up.

The whole file is now kept verbatim under ``savilerow_info``, nested rather than
flattened. Nesting is the point of several tests below: ``solver_stats`` is the
core's cross-solver vocabulary, while these are one solver's own keys in its own
spelling, and the backends do not agree on which of them exist.
"""

from types import SimpleNamespace

import pytest

from solver_support.run_summary import PhaseResult
from solver_support.solver_horizon import SavileRowSolver

# A real Minion .info, which carries three keys no other backend emits.
MINION_INFO = """SavileRowClauseOut:0
SavileRowTotalTime:0.484
SolverNodes:1
SolverSatisfiable:1
SolverSetupTime:0.001
SolverSolutionsFound:1
SolverSolveTime:0.000123
SolverTimeOut:0
SolverTotalTime:0.000123
"""

# A real OR-Tools .info: no SolverNodes, but SolverFailures and a wall time.
ORTOOLS_INFO = """SavileRowClauseOut:0
SavileRowTotalTime:0.51
SolverFailures:0
SolverSatisfiable:1
SolverTimeOut:0
SolverTotalTime:0.002
SolverTotalWallTime:0.003
"""


@pytest.fixture
def solver(tmp_path, monkeypatch):
    """A SavileRowSolver whose summary already holds a horizon-3 phase."""
    monkeypatch.chdir(tmp_path)
    phase = PhaseResult(name="solving-horizon-3", duration=1.0)
    runner = SimpleNamespace(
        summary=SimpleNamespace(phases=[phase]),
        logger=None,
    )
    return SavileRowSolver(runner), phase


def write_info(tmp_path, text, stem="inst.param"):
    (tmp_path / f"{stem}.info").write_text(text)
    return tmp_path / stem


class TestVerbatimInfo:

    def test_whole_file_is_kept(self, solver, tmp_path):
        sr, phase = solver
        sr._record_savilerow_times(write_info(tmp_path, MINION_INFO), 3)
        info = phase.solver_stats["savilerow_info"]
        assert info["SolverNodes"] == 1
        assert info["SolverSatisfiable"] == 1
        assert info["SolverSolutionsFound"] == 1

    def test_renamed_times_still_recorded(self, solver, tmp_path):
        # The existing contract must not move: these two are what the phase model
        # reads, and they stay top-level and snake_case.
        sr, phase = solver
        sr._record_savilerow_times(write_info(tmp_path, MINION_INFO), 3)
        assert phase.solver_stats["savilerow_time"] == 0.484
        assert phase.solver_stats["solver_time"] == 0.000123

    def test_verbatim_keys_are_nested_not_flattened(self, solver, tmp_path):
        # Savile Row's CamelCase must not land beside the core's snake_case.
        sr, phase = solver
        sr._record_savilerow_times(write_info(tmp_path, MINION_INFO), 3)
        assert "SolverNodes" not in phase.solver_stats
        assert set(phase.solver_stats) == {"savilerow_time", "solver_time",
                                           "savilerow_info"}

    def test_keys_are_verbatim_and_coerced(self, solver, tmp_path):
        sr, phase = solver
        sr._record_savilerow_times(write_info(tmp_path, ORTOOLS_INFO), 3)
        info = phase.solver_stats["savilerow_info"]
        assert info["SolverTotalWallTime"] == 0.003   # float
        assert info["SolverTimeOut"] == 0             # int, not "0"

    def test_backends_may_disagree_on_which_keys_exist(self, solver, tmp_path):
        # Which is why this is verbatim rather than an allow-list: Minion emits
        # SolverNodes and no wall time, OR-Tools the reverse.
        sr, phase = solver
        sr._record_savilerow_times(write_info(tmp_path, ORTOOLS_INFO), 3)
        info = phase.solver_stats["savilerow_info"]
        assert "SolverNodes" not in info
        assert "SolverFailures" in info and "SolverTotalWallTime" in info


class TestBestEffort:

    def test_missing_info_file_records_nothing(self, solver, tmp_path):
        # A hard timeout can kill Savile Row before it writes the file.
        sr, phase = solver
        sr._record_savilerow_times(tmp_path / "absent.param", 3)
        assert phase.solver_stats == {}

    def test_only_the_matching_horizon_phase_is_touched(self, solver, tmp_path):
        # Guarded so a mismatched horizon cannot scribble on an unrelated phase.
        sr, phase = solver
        sr._record_savilerow_times(write_info(tmp_path, MINION_INFO), 7)
        assert phase.solver_stats == {}

    def test_info_without_the_two_times_is_still_kept(self, solver, tmp_path):
        # The times are absent if Savile Row was interrupted mid-write; whatever
        # did land is still worth keeping.
        sr, phase = solver
        sr._record_savilerow_times(
            write_info(tmp_path, "SolverSatisfiable:0\nSolverTimeOut:1\n"), 3)
        assert phase.solver_stats["savilerow_info"] == {"SolverSatisfiable": 0,
                                                        "SolverTimeOut": 1}
