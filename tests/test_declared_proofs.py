"""
A registry tune that declares ``proves-unsat: true`` vouches for its own search:
an exhaustion the built-in allowlist does not cover becomes ``unsat`` /
``exhausted-declared``, for exactly that search and nothing the log contradicts.
"""

import re
from pathlib import Path

import pytest

from solver_support.solver_stats import SolverStatsExtractor, UPF_RESULT_SCHEMA

FIXTURES = Path(__file__).parent / 'fixtures'


def _log(rel):
    return (FIXTURES / rel).read_text()


def _with_search(search, log=None):
    log = log if log is not None else _log('fd/fd-exhaust.stdout')
    return re.sub(r"--search '[^']*'", lambda _: f"--search '{search}'", log)


def _fd(stdout, declared, **kw):
    return SolverStatsExtractor.fd_verdict(12, stdout, None,
                                           declared_search=declared, **kw)


class TestRegistry:

    def test_an_explicit_true_vouches(self, fixture_registry):
        preset = fixture_registry.get_declaring_tune('fd', 'lmcut', 'search')
        assert preset['search'] == 'astar(lmcut())'

    @pytest.mark.parametrize('tune', ['blind', 'ff', None, 'no-such-tune'])
    def test_absent_or_false_never_vouches(self, fixture_registry, tune):
        # blind: no key; ff: false; None and unknown: default_tune (blind)
        assert fixture_registry.get_declaring_tune('fd', tune, 'search') is None

    def test_an_unknown_solver(self, fixture_registry):
        assert fixture_registry.get_declaring_tune('nope', 'lmcut', 'search') is None

    def test_vouching_does_not_grant_a_model_the_claim(self, fixture_registry):
        """get_proves_unsat is still model AND tune."""
        assert fixture_registry.get_proves_unsat('eprime', tune='lmcut',
                                                 solver_name='fd') is False


class TestFastDownward:

    def test_the_declared_search_is_a_proof(self):
        assert _fd(_with_search('astar(lmcut())'), 'astar(lmcut())') == {
            'verdict': 'unsat', 'verdict_reason': 'exhausted-declared'}

    def test_whitespace_does_not_matter(self):
        assert _fd(_with_search('astar( lmcut() )'), 'astar(lmcut())')[
            'verdict_reason'] == 'exhausted-declared'

    def test_without_a_declaration_it_is_unverified(self):
        assert _fd(_with_search('astar(lmcut())'), None)[
            'verdict_reason'] == 'unverified-search'

    def test_a_different_search_ran(self):
        """--options can replace the tune's search; the log shows what ran."""
        assert _fd(_with_search('astar(ipdb())'), 'astar(lmcut())')[
            'verdict_reason'] == 'unverified-search'

    def test_a_predefinition_means_something_else_ran(self):
        log = _log('fd/fd-exhaust.stdout').replace(
            "--search 'astar(blind())'",
            "--evaluator 'h=lmcut()' --search 'astar(lmcut())'")
        assert _fd(log, 'astar(lmcut())')['verdict_reason'] == 'unverified-search'

    def test_the_allowlist_still_says_exhausted(self):
        assert _fd(_log('fd/fd-exhaust.stdout'), 'astar(blind())')[
            'verdict_reason'] == 'exhausted'

    @pytest.mark.parametrize('search, reason', [
        ('astar(lmcut(), bound=3)', 'bounded'),
        ('lazy_greedy([cg()])', 'unsafe-pruning'),
    ])
    def test_it_never_overrides_what_the_log_shows(self, search, reason):
        assert _fd(_with_search(search), search)['verdict_reason'] == reason

    def test_unsafe_on_axioms_is_still_unsafe(self):
        log = _with_search('lazy_greedy([ff()])', _log('fd/fd-ff-ax.stdout'))
        assert _fd(log, 'lazy_greedy([ff()])')['verdict_reason'] == 'unsafe-pruning'

    def test_a_limit_or_a_kill_is_not_overridden(self):
        assert _fd(_log('fd/fd-maxtime.stdout'), 'astar(blind(), max_time=0)')[
            'verdict_reason'] == 'search-limit'
        assert _fd(_with_search('astar(lmcut())'), 'astar(lmcut())',
                   timed_out=True) == {'verdict': 'killed'}

    def test_without_the_log_line_the_argv_is_compared(self):
        stdout = '[t=0.01s, 1 KB] Completely explored state space -- no solution!\n'
        cmd = ['fd', 'd.pddl', 'p.pddl', '--search', 'astar(lmcut())']
        assert SolverStatsExtractor.fd_verdict(
            12, stdout, cmd, declared_search='astar(lmcut())')[
            'verdict_reason'] == 'exhausted-declared'


class TestSymk:
    """Issue #9: SymK's ending (its last BOUND reaching infinity) is judged like
    Fast Downward's exhaustion line, and no SymK search is on the allowlist."""

    @pytest.mark.parametrize('search', [
        'sym_fw()', 'sym_bw()', 'sym_bd()', 'sym_fw(mutex_type=MUTEX_NOT)'])
    def test_no_symk_search_is_checked(self, search):
        log = _with_search(search, _log('fd/symk-exhaust.stdout'))
        assert _fd(log, None) == {
            'verdict': 'unknown', 'verdict_reason': 'unverified-search'}

    def test_the_declared_search_is_a_proof(self):
        log = _with_search('sym_fw()', _log('fd/symk-exhaust.stdout'))
        assert _fd(log, 'sym_fw()') == {
            'verdict': 'unsat', 'verdict_reason': 'exhausted-declared'}

    def test_a_different_search_ran(self):
        log = _with_search('sym_bw()', _log('fd/symk-exhaust.stdout'))
        assert _fd(log, 'sym_fw()')['verdict_reason'] == 'unverified-search'

    def test_a_bound_is_never_overridden(self):
        assert _fd(_log('fd/symk-bound.stdout'), 'sym_bd(bound=2)') == {
            'verdict': 'unknown', 'verdict_reason': 'bounded'}

    def test_a_bound_in_the_search_is_never_overridden(self):
        """A bound= the log's BOUND lines do not reflect still bounds the proof."""
        log = _with_search('sym_bd(bound=2)', _log('fd/symk-exhaust.stdout'))
        assert _fd(log, 'sym_bd(bound=2)')['verdict_reason'] == 'bounded'

    def test_via_upf_the_engine_vouches(self):
        doc = {'schema': UPF_RESULT_SCHEMA, 'finished': True,
               'plan_found': False, 'engine': 'symk'}
        log = _log('fd/symk-exhaust.stdout')
        vouched = SolverStatsExtractor.upf_result_stats(
            doc, 1, '', log, declared_engine='symk')
        other = SolverStatsExtractor.upf_result_stats(
            doc, 1, '', log, declared_engine='symk-opt')
        assert vouched['verdict_reason'] == 'exhausted-declared'
        assert other['verdict_reason'] == 'unverified-search'


class TestEnhsp:

    CMD = ['enhsp', '-o', 'd.pddl', '-f', 'p.pddl', '-planner', 'opt-hrmax',
           '-sp', 'plan']

    def _verdict(self, command, declared):
        return SolverStatsExtractor.enhsp_verdict(
            0, _log('enhsp/enhsp-exhaust.stdout'), '', command,
            declared_command=declared)

    def test_the_declared_command_is_a_proof(self):
        assert self._verdict(self.CMD, list(self.CMD)) == {
            'verdict': 'unsat', 'verdict_reason': 'exhausted-declared'}

    def test_extra_options_break_the_match(self):
        assert self._verdict(self.CMD + ['-h', 'hadd'], self.CMD)[
            'verdict_reason'] == 'unverified-search'

    def test_helpful_actions_are_never_overridden(self):
        cmd = self.CMD[:-4] + ['-planner', 'sat-hmrph', '-sp', 'plan']
        assert self._verdict(cmd, cmd)['verdict_reason'] == 'incomplete'


class TestUpf:

    def _doc(self, engine, **kw):
        return {'schema': UPF_RESULT_SCHEMA, 'finished': True, 'plan_found': False,
                'engine': engine, **kw}

    def test_fast_downward_search(self):
        stats = SolverStatsExtractor.upf_result_stats(
            self._doc('fast-downward'), 1, '', _with_search('astar(lmcut())'),
            declared_search='astar(lmcut())', declared_engine='fast-downward')
        assert stats['verdict_reason'] == 'exhausted-declared'

    def test_enhsp_engine(self):
        cmd = ['java', '-jar', 'enhsp.jar', '-s', 'WAStar', '-h', 'hrmax']
        doc = self._doc('enhsp-opt', engine_command=cmd)
        log = _log('enhsp/enhsp-exhaust.stdout')
        vouched = SolverStatsExtractor.upf_result_stats(
            doc, 1, '', log, declared_engine='enhsp-opt')
        other = SolverStatsExtractor.upf_result_stats(
            doc, 1, '', log, declared_engine='enhsp')
        assert vouched['verdict_reason'] == 'exhausted-declared'
        assert other['verdict_reason'] == 'unverified-search'


class TestSolvers:

    def test_fd_solver_passes_the_tune_search(self, fixture_registry, tmp_path,
                                              monkeypatch):
        from unittest.mock import Mock
        from solver_support.global_state import global_state
        from solver_support.run_summary import PhaseResult, RunSummary
        from solver_support.solver_fd import FastDownwardSolver
        from solver_support.solver_runner import SolverResult

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(global_state, 'tune', 'lmcut', raising=False)
        summary = RunSummary(instance='i', model='pddl', solver='fd', timestamp='t')
        runner = Mock(summary=summary, timeout=60)

        def run_phase(name, cmd, **kw):
            summary.add_phase(PhaseResult(name=name, command=cmd, returncode=12))
            return SolverResult(command=cmd, returncode=12,
                                stdout=_with_search('astar(lmcut())'),
                                stderr='', duration=1.0)
        runner.run_phase.side_effect = run_phase
        FastDownwardSolver(runner).solve(tmp_path / 'p.pddl', model_path='d.pddl')
        summary.finalize('failed', proves_unsat=True)
        assert summary.get_phase('solving').solver_stats['verdict_reason'] == \
            'exhausted-declared'
        assert (summary.outcome, summary.outcome_reason) == (
            'unsat', 'exhausted-declared')
