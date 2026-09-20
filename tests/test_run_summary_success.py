#!/usr/bin/env python
"""
Contract tests for how a run's overall ``success`` is decided.

This is the logic that decides whether a consumer's run command prints "Success
... solved" / exits 0, and what the ``.summary`` records. It accreted across several
ad-hoc solvers and has produced two opposite bugs:

  * false success -- a solver that runs cleanly but finds NO solution (a planner
    that returns no plan, or all horizons UNSAT) was reported as solved, because
    ``success`` was ``all(phase.success)`` and an exit-0-but-no-solution
    subprocess counts as a successful phase; and
  * false failure -- a best-effort post-processing phase (visualisation / .sol
    export) failing dragged an otherwise-solved run to ``success=False``.

These are pure-Python tests on :meth:`RunSummary.finalize` (no solver binaries),
so the contract is pinned cheaply and any new solver that plugs into the same
summary machinery is covered automatically:

  * a run succeeds when it produced a DEFINITIVE ANSWER -- a solution, or a
    sound proof that none exists. Nothing else counts;
  * clean subprocess exits never imply a solution. This is the false-success bug
    above, and its sharpest form is a horizon scan that runs out of time: every
    solver stopped itself tidily at the limit it was given, so every phase
    exited 0, and the run learned nothing;
  * a planner's proven-unsat IS a success even though it exits non-zero -- but
    only for a model that can prove it (``proves_unsat``), never for a bounded
    CP model whose "no solution" is about the bound;
  * only REQUIRED phases decide success; visualisation/export never do;
  * an anytime run that banked a solution before timing out still succeeds.
"""

import pytest

from solver_support.run_summary import RunSummary, PhaseResult


def _phase(name, success, **stats):
    """A PhaseResult with just the fields the success logic looks at."""
    return PhaseResult(name=name, success=success, solver_stats=dict(stats))


def _summary(*phases):
    s = RunSummary(instance="t")
    for p in phases:
        s.add_phase(p)
    return s


# --- the core verdict: final_status wins over clean subprocess exits ----------

def test_solved_run_is_success():
    s = _summary(_phase("translation", True),
                 _phase("solving", True, solution_found=True))
    s.finalize("done")
    assert s.success is True
    assert s.outcome == "solved"


def test_no_solution_but_clean_exit_is_not_success():
    """The false-success bug: every phase exited 0 yet no solution was produced.

    The orchestrator returns no solution file and finalizes "failed"; a clean
    subprocess exit must not override that into a reported success.
    """
    s = _summary(_phase("translation", True), _phase("solving", True))
    s.finalize("failed")
    assert s.success is False


def test_launch_failed_is_not_success():
    s = _summary(_phase("translation", False))
    s.finalize("launch-failed")
    assert s.success is False


def test_required_phase_failure_is_not_success():
    s = _summary(_phase("translation", True), _phase("solving", False))
    s.finalize("done")
    assert s.success is False


def test_no_phases_is_not_success():
    s = _summary()
    s.finalize("done")
    assert s.success is False


# --- optional phases never decide success (the false-failure bug) -------------

@pytest.mark.parametrize("optional_name", ["visualisation", "export"])
def test_optional_phase_failure_does_not_fail_a_solved_run(optional_name):
    s = _summary(
        _phase("translation", True),
        _phase("solving", True, solution_found=True),
        _phase(optional_name, False),  # best-effort post-processing failed
    )
    s.finalize("done")
    assert s.success is True, (
        f"a failing {optional_name} phase must not fail an otherwise-solved run"
    )


def test_optional_phase_alone_is_not_success():
    """Required phases must actually exist; a lone optional phase isn't success."""
    s = _summary(_phase("visualisation", True))
    s.finalize("done")
    assert s.success is False


# --- anytime / timeout nuance is preserved ------------------------------------

def test_timeout_with_banked_solution_is_success():
    s = _summary(
        _phase("translation", True),
        _phase("solving-horizon-7", False),  # the horizon that timed out
        _phase("final-timeout-solution", True, timeout_solution=True),
    )
    s.finalize("timeout")
    assert s.success is True


def test_timeout_without_solution_is_not_success():
    s = _summary(
        _phase("translation", True),
        _phase("solving-horizon-7", False),  # timed out, nothing banked
    )
    s.finalize("timeout")
    assert s.success is False


# --- a custom status is judged on the answer, like any other ------------------

def test_custom_status_with_a_solution_is_success():
    # Mirrors test_solver_runner.test_finalize_summary_success ("completed").
    s = _summary(_phase("solving", True, solution_found=True))
    s.finalize("completed")
    assert s.success is True


def test_custom_status_without_a_solution_is_not_success():
    """A status the core does not know buys no free pass.

    Previously any label outside the declared failure list computed success
    from subprocess exit codes, so a consumer's own "completed" reported a
    solved run on the strength of a tidy exit alone.
    """
    s = _summary(_phase("solving", True))
    s.finalize("completed")
    assert s.success is False
    assert s.outcome == "unknown"


# --- proven unsat is an answer, and needs the model to be able to give it ------

def test_planner_proven_unsat_is_success():
    """Exits non-zero, finalizes 'failed', and has answered the question."""
    s = _summary(_phase("translation", True), _phase("solving", False))
    s.finalize("failed", proves_unsat=True)
    assert s.outcome == "unsat"
    assert s.success is True


def test_bounded_model_finding_nothing_is_never_unsat():
    """The same status from a model that cannot prove it is an error, not a verdict."""
    s = _summary(_phase("translation", True), _phase("solving", False))
    s.finalize("failed", proves_unsat=False)
    assert s.outcome == "error"
    assert s.success is False


def test_horizon_limit_is_not_unsat_even_for_a_complete_model():
    """Running out of horizons decides nothing, whatever the model can prove.

    Refuting horizons 1..N says nothing about N+1, so 'hlim' must never be
    promoted to a proof by the capability flag.
    """
    s = _summary(_phase("solving-horizon-100", True))
    s.finalize("hlim", proves_unsat=True)
    assert s.outcome == "unknown"
    assert s.success is False
