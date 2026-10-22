"""
Which parameters a horizon scan sets to the horizon.

Each horizon N reaches the model as `letting <name> be N` appended to the
instance. The names used to be hardcoded, `noSteps` for Savile Row and both
`maxMoves` and `horizon` for Conjure. Conjure rejects a `letting` for a
parameter the model does not declare, so every Essence model had to declare
both. They are now the registry's `horizon-params`, with the historical names
kept as the fallback for configs written before the key existed.
"""

import logging
from pathlib import Path
from unittest.mock import Mock

import pytest

from solver_support.horizon_utils import (
    LEGACY_HORIZON_PARAMS, create_horizon_param_file)
from solver_support.solver_horizon import ConjureSolver, SavileRowSolver

log = logging.getLogger(__name__)


class TestRegistry:

    def test_model_names_its_own(self, fixture_registry):
        # a single name may be a plain string
        assert fixture_registry.get_horizon_params('stepped', ('noSteps',)) == ['steps']

    def test_model_inherits_its_solvers(self, fixture_registry):
        assert fixture_registry.get_horizon_params('minionstepped', ('noSteps',)) == ['t']

    def test_neither_falls_back_to_the_routes_default(self, fixture_registry):
        assert fixture_registry.get_horizon_params(
            'scored', ('maxMoves', 'horizon')) == ['maxMoves', 'horizon']

    def test_unknown_model_gets_the_default(self, fixture_registry):
        assert fixture_registry.get_horizon_params('nosuch', ('noSteps',)) == ['noSteps']


class TestInstanceRewrite:
    """The instance's own value for a horizon parameter is dropped, since the
    scan appends one per horizon and two lettings of a name is an error."""

    def _rewrite(self, tmp_path, text, names=None):
        param = tmp_path / 'p.param'
        param.write_text(text)
        out = create_horizon_param_file(param, tmp_path, 3, log, horizon_params=names)
        return out.read_text()

    def test_drops_only_the_named_lettings(self, tmp_path):
        out = self._rewrite(tmp_path, 'letting steps be 9\nletting size be 4\n', ['steps'])
        assert 'steps' not in out
        assert 'letting size be 4' in out

    def test_a_line_that_merely_mentions_a_name_is_kept(self, tmp_path):
        """The old substring test dropped these too."""
        text = ('$ horizon below is the instance author\'s\n'
                'letting horizonLimit be 20\n'
                'letting horizon be 5\n')
        out = self._rewrite(tmp_path, text, ['horizon'])
        assert "$ horizon below" in out
        assert 'letting horizonLimit be 20' in out
        assert 'letting horizon be 5' not in out

    def test_default_drops_the_legacy_names(self, tmp_path):
        text = ''.join(f'letting {n} be 1\n' for n in LEGACY_HORIZON_PARAMS) + 'letting k be 2\n'
        out = self._rewrite(tmp_path, text)
        assert all(n not in out for n in LEGACY_HORIZON_PARAMS)
        assert 'letting k be 2' in out


class TestSolverWritesTheConfiguredNames:

    def _solver(self, cls, model, tmp_path):
        runner = Mock()
        runner.summary.model = model
        return cls(runner)

    @pytest.mark.parametrize('cls, model, expected', [
        (SavileRowSolver, 'stepped', ['steps']),
        (SavileRowSolver, 'scored', ['noSteps']),             # no key: legacy
        (ConjureSolver, 'stepped', ['steps']),
        (ConjureSolver, 'scored', ['maxMoves', 'horizon']),   # no key: legacy
    ])
    def test_names(self, fixture_registry, tmp_path, cls, model, expected):
        solver = self._solver(cls, model, tmp_path)
        names = solver._horizon_params()
        assert names == expected
        param = tmp_path / 'h.param'
        param.write_text('')
        assert solver._write_horizon(param, 7, names)
        assert param.read_text() == ''.join(f'letting {n} be 7\n' for n in expected)


class TestBackstop:
    """`backstop=True` sizes each horizon's kill above its in-band limits."""

    def test_off_by_default_keeps_the_runners_timeout(self):
        solver = SavileRowSolver(Mock())
        assert solver._horizon_watchdog(60) is None

    def test_on_is_the_savilerow_backstop_of_the_horizon_budget(self):
        from solver_support import savilerow_backstop
        solver = SavileRowSolver(Mock())
        solver._backstop = True
        assert solver._horizon_watchdog(60) == savilerow_backstop(60)
        assert solver._horizon_watchdog(None) is None   # no budget, nothing to size
