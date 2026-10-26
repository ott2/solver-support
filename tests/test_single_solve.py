"""
A model with no horizon is tailored and solved once (issue #10).

Its one solve is a phase named ``solving``, carrying the ``.info`` verdict, so
a complete model's refutation finalizes as ``unsat`` under ``proves-unsat``,
exactly as a planner's does. A horizon model still scans, within the bounds
its registry entry gives.
"""

from pathlib import Path
from unittest.mock import Mock

import pytest

from solver_support import solver_horizon
from solver_support.run_summary import PhaseResult, RunSummary
from solver_support.solver_horizon import ConjureSolver, SavileRowSolver
from solver_support.solver_runner import SolverExecutableError, SolverResult

UNSAT_INFO = "SavileRowTotalTime:0.4\nSolverTotalTime:0.1\nSolverSatisfiable:0\nSolverTimeOut:0\n"
SAT_INFO = "SavileRowTotalTime:0.4\nSolverTotalTime:0.1\nSolverSatisfiable:1\nSolverTimeOut:0\n"
LIMIT_INFO = "SavileRowTotalTime:0.4\nSolverTotalTime:60\nSolverTimeOut:1\n"


def _runner(tmp_path, model, solver, outcomes):
    """A runner whose run_phase records a phase and plays the toolchain:
    ``outcomes`` maps a phase name to (returncode, files to write, extra
    SolverResult kwargs)."""
    summary = RunSummary(instance='inst', model=model, solver=solver,
                         timestamp='ts', output_dir=str(tmp_path))
    runner = Mock(summary=summary, timeout=60)
    calls = []

    def run_phase(name, cmd, expected_outputs=None, **kw):
        calls.append(name)
        returncode, files, extra = outcomes.get(name, (0, {}, {}))
        for suffix, text in files.items():
            Path(_target(cmd, suffix)).write_text(text)
        result = SolverResult(command=cmd, returncode=returncode, stdout='',
                              stderr='', duration=1.0, **extra)
        summary.add_phase(PhaseResult(
            name=name, command=cmd, returncode=returncode,
            success=result.success, timed_out=result.timed_out,
            launch_failed=result.launch_failed))
        return result

    runner.run_phase.side_effect = run_phase
    return runner, calls


def _target(cmd, suffix):
    """The file Savile Row (or Conjure) writes beside its parameter file."""
    param = next(a for a in cmd if a.endswith(('.param', '.eprime-param')))
    return param + suffix


@pytest.fixture
def cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'inst.param').write_text('letting k be 3\n')
    return tmp_path


class TestSavileRowOnce:

    def _solve(self, cwd, outcomes, model='oneshot'):
        runner, calls = _runner(cwd, model, 'srsat', outcomes)
        solver = SavileRowSolver(runner)
        result = solver.solve(cwd / 'inst.param', model_path='m.eprime')
        return result, runner.summary, calls

    def test_a_refutation_is_unsat(self, fixture_registry, cwd):
        result, summary, calls = self._solve(cwd, {
            'solving': (0, {'.info': UNSAT_INFO}, {})})
        assert result is None
        assert calls == ['solving']
        assert summary.get_phase('solving').solver_stats['verdict'] == 'unsat'
        summary.finalize('failed', proves_unsat=True)
        assert (summary.outcome, summary.stop_reason) == ('unsat', 'complete')
        assert summary.success

    def test_without_proves_unsat_it_is_unknown(self, fixture_registry, cwd):
        _, summary, _ = self._solve(cwd, {'solving': (0, {'.info': UNSAT_INFO}, {})})
        summary.finalize('failed', proves_unsat=False)
        assert summary.outcome == 'unknown'

    def test_a_solution(self, fixture_registry, cwd):
        result, summary, calls = self._solve(cwd, {'solving': (0, {
            '.info': SAT_INFO, '.solution': 'letting x be 1\n'}, {})})
        assert result is not None and result.read_text() == 'letting x be 1\n'
        assert summary.get_phase('solving').solver_stats['verdict'] == 'sat'

    def test_the_solvers_own_limit_is_not_a_proof(self, fixture_registry, cwd):
        _, summary, _ = self._solve(cwd, {'solving': (0, {'.info': LIMIT_INFO}, {})})
        summary.finalize('failed', proves_unsat=True)
        assert summary.outcome == 'unknown'

    def test_the_instance_is_copied_not_rewritten(self, fixture_registry, cwd):
        self._solve(cwd, {'solving': (0, {'.info': UNSAT_INFO}, {})})
        assert (cwd / 'inst.param').read_text() == 'letting k be 3\n'
        assert (cwd / 'inst-srsat-oneshot-ts.param').read_text() == 'letting k be 3\n'

    def test_a_timeout_raises(self, fixture_registry, cwd):
        with pytest.raises(TimeoutError):
            self._solve(cwd, {'solving': (-15, {}, {'timed_out': True})})

    def test_a_launch_failure_raises(self, fixture_registry, cwd):
        with pytest.raises(SolverExecutableError):
            self._solve(cwd, {'solving': (127, {}, {'launch_failed': True})})

    def test_an_empty_list_opts_out_of_the_solvers_horizon(self, fixture_registry, cwd):
        runner, calls = _runner(cwd, 'minionoptout', 'srminion', {
            'solving': (0, {'.info': UNSAT_INFO}, {})})
        SavileRowSolver(runner).solve(cwd / 'inst.param', model_path='m.eprime')
        assert calls == ['solving']


class TestConjureOnce:

    def test_phases_and_verdict(self, fixture_registry, cwd):
        (cwd / 'conjure-output').mkdir()
        (cwd / 'conjure-output' / 'model000001.eprime').write_text('')
        runner, calls = _runner(cwd, 'oneshotessence', 'conjure', {
            'solving': (0, {'.info': UNSAT_INFO}, {}),
        })
        runner.run_phase.side_effect = _with_eprime_param(runner.run_phase.side_effect)
        result = ConjureSolver(runner, solver_name='srsat').solve(cwd / 'inst.param',
                                             model_path=str(cwd / 'm.essence'))
        assert result is None
        assert calls == ['modelling', 'translate-param', 'solving']
        summary = runner.summary
        summary.finalize('failed', proves_unsat=True)
        assert (summary.outcome, summary.outcome_reason) == ('unsat', '')

    def test_a_failed_translate_param_is_a_setup_error(self, fixture_registry, cwd):
        (cwd / 'm.eprime').write_text('')        # newer than the essence: reused
        (cwd / 'm.essence').write_text('')
        import os
        os.utime(cwd / 'm.essence', (0, 0))
        runner, calls = _runner(cwd, 'oneshotessence', 'conjure', {
            'translate-param': (1, {}, {})})
        ConjureSolver(runner, solver_name='srsat').solve(
            cwd / 'inst.param', model_path=str(cwd / 'm.essence'))
        assert calls == ['translate-param']
        runner.summary.finalize('failed', proves_unsat=True)
        assert (runner.summary.outcome, runner.summary.outcome_reason) == (
            'error', 'setup')


def _with_eprime_param(play):
    """Conjure's translate-parameter writes the .eprime-param Savile Row reads."""
    def run_phase(name, cmd, expected_outputs=None, **kw):
        if name == 'translate-param':
            for out in expected_outputs or []:
                Path(out).write_text('')
        return play(name, cmd, expected_outputs=expected_outputs, **kw)
    return run_phase


class TestScanBounds:
    """A horizon model still scans: from max(startat, horizon-start) to the
    caller's stopat, else its horizon-stop, else 100."""

    def _bounds(self, cwd, monkeypatch, cls, model, **kwargs):
        seen = {}

        def fake_search(runner, param_file, startat, callback, logger,
                        min_horizon=1, stopat=100, **kw):
            seen.update(startat=startat, min_horizon=min_horizon, stopat=stopat)
            return None
        monkeypatch.setattr(solver_horizon, 'horizon_search', fake_search)
        monkeypatch.setattr(cls, '_do_conjure_modelling',
                            lambda self, path: Path('m.eprime'), raising=False)
        runner, _ = _runner(cwd, model, 'srsat', {})
        cls(runner).solve(cwd / 'inst.param', model_path='m', **kwargs)
        return seen

    def test_the_global_default(self, fixture_registry, cwd, monkeypatch):
        assert self._bounds(cwd, monkeypatch, SavileRowSolver, 'stepped') == {
            'startat': 1, 'min_horizon': 1, 'stopat': 100}

    def test_the_models_own_bounds(self, fixture_registry, cwd, monkeypatch):
        assert self._bounds(cwd, monkeypatch, ConjureSolver, 'bounded') == {
            'startat': 2, 'min_horizon': 2, 'stopat': 12}

    def test_the_caller_may_narrow_but_not_go_below_the_floor(
            self, fixture_registry, cwd, monkeypatch):
        assert self._bounds(cwd, monkeypatch, ConjureSolver, 'bounded',
                            startat=1, stopat=5) == {
            'startat': 1, 'min_horizon': 2, 'stopat': 5}

    def test_conjure_has_no_built_in_floor(self, fixture_registry, cwd, monkeypatch):
        """The old hardcoded min_horizon=2 is the model's horizon-start now."""
        assert self._bounds(cwd, monkeypatch, ConjureSolver, 'stepped')[
            'min_horizon'] == 1
