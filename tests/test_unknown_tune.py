"""
An unknown tune is an error, not a run of the default tune (issue #13).

The fallback built the default tune's argv while the run was recorded under
the name asked for, so a misspelt cell in a tune grid silently duplicated the
default. A known tune that lacks the backend's key still falls back: that is a
configuration choice (a regime-only preset, say), not a typo.
"""

from unittest.mock import Mock

import pytest

from solver_support.global_state import global_state
from solver_support.model_registry import ModelRegistryError
from solver_support.solver_fd import FastDownwardSolver
from solver_support.solver_horizon import SavileRowSolver
from solver_support.solver_upfplan import UpfPlanSolver


class TestBuilders:

    def test_savilerow(self, fixture_registry):
        with pytest.raises(ModelRegistryError, match="Unknown tune 'exactt'.*exact"):
            fixture_registry.build_savilerow_command(
                'm.eprime', 'p.param', solver_name='srsat', tune='exactt')
        assert '-S0' in fixture_registry.build_savilerow_command(
            'm.eprime', 'p.param', solver_name='srsat', tune='exact')

    def test_fast_downward(self, fixture_registry):
        with pytest.raises(ModelRegistryError, match='Unknown tune'):
            fixture_registry.build_fd_command('d.pddl', 'p.pddl', tune='lmkut')

    def test_upf_lookups(self, fixture_registry):
        with pytest.raises(ModelRegistryError, match='Unknown tune'):
            fixture_registry.get_upf_engine('upffd', 'optimel')
        with pytest.raises(ModelRegistryError, match='Unknown tune'):
            fixture_registry.get_upf_fd_search('upffd', 'optimel')

    def test_a_tune_on_a_solver_with_none(self, fixture_registry):
        with pytest.raises(ModelRegistryError, match='known: none'):
            fixture_registry.build_savilerow_command(
                'm.eprime', 'p.param', solver_name='kissat', tune='default')

    def test_no_tune_is_the_default(self, fixture_registry):
        cmd = fixture_registry.build_fd_command('d.pddl', 'p.pddl', tune=None)
        assert cmd[-1] == 'astar(blind())'

    def test_a_known_tune_without_the_key_still_falls_back(self, fixture_registry):
        """`soup` is an alias preset with no `search`: a choice, not a typo."""
        cmd = fixture_registry.build_fd_command('d.pddl', 'p.pddl', tune='soup')
        assert cmd[-1] == 'astar(blind())'

    def test_the_query_helpers_stay_lenient(self, fixture_registry):
        assert fixture_registry.get_tune_regime('fd', 'lmkut') is None
        assert fixture_registry.get_declaring_tune('fd', 'lmkut', 'search') is None


def _runner(model, solver):
    runner = Mock()
    runner.summary.model = model
    runner.summary.solver = solver
    return runner


class TestSolvers:
    """The solver-level resolution rejects it too, so no backend can swallow
    the builder's error and run its default under the name asked for."""

    def test_an_explicit_tune(self, fixture_registry, monkeypatch, tmp_path):
        monkeypatch.setattr(global_state, 'tune', 'lmkut', raising=False)
        runner = _runner('pddl', 'fd')
        with pytest.raises(ModelRegistryError, match='Unknown tune'):
            FastDownwardSolver(runner).solve(tmp_path / 'p.pddl', model_path='d.pddl')
        runner.run_phase.assert_not_called()

    def test_upf_does_not_swallow_it(self, fixture_registry, monkeypatch, tmp_path):
        monkeypatch.setattr(global_state, 'tune', 'optimel', raising=False)
        runner = _runner('upf', 'upffd')
        solver = UpfPlanSolver(runner, solver_name='upffd', upfplan_command='upfplan')
        with pytest.raises(ModelRegistryError, match='Unknown tune'):
            solver.solve(tmp_path / 'p.pddl', model_path='d.pddl')
        runner.run_phase.assert_not_called()

    def test_a_known_explicit_tune(self, fixture_registry, monkeypatch):
        monkeypatch.setattr(global_state, 'tune', 'exact', raising=False)
        solver = SavileRowSolver(_runner('stepped', 'srsat'))
        assert solver._effective_tune() == 'exact'

    def test_a_pinned_tune_under_its_own_solver(self, fixture_registry, monkeypatch):
        monkeypatch.setattr(global_state, 'tune', None, raising=False)
        assert FastDownwardSolver(_runner('pddlff', 'fd'))._effective_tune() == 'ff'

    def test_a_misspelt_pin_under_its_own_solver_raises(
            self, fixture_registry, monkeypatch):
        monkeypatch.setattr(global_state, 'tune', None, raising=False)
        fixture_registry.register_model(
            'pinbad', {'path': 'd.pddl', 'solver': 'fd', 'tune': 'fff'})
        with pytest.raises(ModelRegistryError, match="Unknown tune 'fff'"):
            FastDownwardSolver(_runner('pinbad', 'fd'))._effective_tune()

    def test_a_pin_another_solver_lacks_gives_its_default(
            self, fixture_registry, monkeypatch):
        """The pin is the model's preference for its own solver."""
        monkeypatch.setattr(global_state, 'tune', None, raising=False)
        solver = SavileRowSolver(_runner('pddlff', 'srsat'), solver_name='srsat')
        assert solver._effective_tune() is None
