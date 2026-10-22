"""
Tests for ``SolverStatsExtractor.fd_verdict`` (issue #4).

Fast Downward reports most outcomes through its exit code, but exit 12 is
ambiguous by construction and only the log separates a proof from a time limit
from a search that gave up. The 12 cases therefore run against REAL planner
logs (tests/fixtures/fd, see its README), not hand-written ones.
"""

from pathlib import Path

import pytest

from solver_support.solver_stats import (
    FD_ANSWER_RETURNCODES, SAVILEROW_VERDICTS, SolverStatsExtractor)

FIXTURES = Path(__file__).parent / 'fixtures' / 'fd'


def _log(name):
    return (FIXTURES / f'{name}.stdout').read_text()


def _fd(search):
    return ['fd', '--search-time-limit', '60', 'dom.pddl', 'p.pddl',
            '--search', search]


def _verdict(returncode, stdout='', command=None, **kwargs):
    return SolverStatsExtractor.fd_verdict(returncode, stdout, command, **kwargs)


class TestUnsolvedExit12:
    """Exit 12 read off the log, one real run per way a search can end."""

    @pytest.mark.parametrize('fixture, search, expected', [
        # blind A* ran out of states on a relaxed-reachable unsolvable task
        ('fd-exhaust', 'astar(blind())',
         {'verdict': 'unsat', 'verdict_reason': 'exhausted'}),
        # the translator saw it and handed the search a trivially false goal
        ('fd-transunsat', 'astar(blind())',
         {'verdict': 'unsat', 'verdict_reason': 'exhausted'}),
        # the same exhaustion line, but only plans up to cost 2 were refuted
        ('fd-bound', 'astar(blind(), bound=2)',
         {'verdict': 'unknown', 'verdict_reason': 'bounded'}),
        ('fd-maxtime', 'astar(blind(), max_time=0)',
         {'verdict': 'unknown', 'verdict_reason': 'search-limit'}),
        # EHC is incomplete: it gives up, it does not refute
        ('fd-ehc', 'ehc(ff())',
         {'verdict': 'unknown', 'verdict_reason': 'incomplete'}),
        # SymK prints no exhaustion line; its bound sweep reaching infinity is one
        ('symk-exhaust', 'sym_bd()',
         {'verdict': 'unsat', 'verdict_reason': 'exhausted'}),
        ('symk-bound', 'sym_bd(bound=2)',
         {'verdict': 'unknown', 'verdict_reason': 'bounded'}),
    ])
    def test_real_logs(self, fixture, search, expected):
        assert _verdict(12, _log(fixture), _fd(search)) == expected

    def test_bound_is_read_from_the_search_string(self):
        """The log alone cannot see a bound: drop it from the argv and the same
        output reads as a proof."""
        assert _verdict(12, _log('fd-bound'), _fd('astar(blind())')) == {
            'verdict': 'unsat', 'verdict_reason': 'exhausted'}

    def test_portfolio_12_is_never_unsat(self):
        """A portfolio's 12 can mask its components' time-outs, and its
        exhaustion line may come from a component that pruned unsafely."""
        cmd = ['fd', '--alias', 'seq-sat-fdss-2018', 'd.pddl', 'p.pddl']
        assert _verdict(12, _log('fd-exhaust'), cmd, anytime=True) == {
            'verdict': 'unknown', 'verdict_reason': 'portfolio'}

    @pytest.mark.parametrize('line', [
        # Fast Downward 26.6, src/search/search_algorithm.cc: a complete search
        # exits 11 or 13 there, so a 12 that ran out is one FD judged unsafe
        '[t=0.01s, 1 KB] Search terminated without finding a plan!',
        '',
    ])
    def test_neither_line_is_incomplete(self, line):
        assert _verdict(12, line, _fd('astar(ff())')) == {
            'verdict': 'unknown', 'verdict_reason': 'incomplete'}

    def test_time_limit_outranks_exhaustion(self):
        both = _log('fd-maxtime') + _log('fd-exhaust')
        assert _verdict(12, both, _fd('astar(blind())'))['verdict'] == 'unknown'


class TestExitCodes:
    """Every code but 12 says what happened by itself."""

    @pytest.mark.parametrize('code, expected', [
        (0, {'verdict': 'sat'}),
        (1, {'verdict': 'sat'}),
        (2, {'verdict': 'sat'}),
        (3, {'verdict': 'sat'}),
        (10, {'verdict': 'unsat'}),
        (11, {'verdict': 'unsat'}),
        (13, {'verdict': 'unknown', 'verdict_reason': 'bounded'}),
        (20, {'verdict': 'unknown', 'verdict_reason': 'translate-memory'}),
        (21, {'verdict': 'unknown', 'verdict_reason': 'translate-limit'}),
        (22, {'verdict': 'unknown', 'verdict_reason': 'memory'}),
        (23, {'verdict': 'unknown', 'verdict_reason': 'search-limit'}),
        (24, {'verdict': 'unknown', 'verdict_reason': 'memory'}),
        (30, {'verdict': 'error', 'verdict_reason': 'crash'}),
        (31, {'verdict': 'error', 'verdict_reason': 'input-error'}),
        (32, {'verdict': 'error', 'verdict_reason': 'crash'}),
        (33, {'verdict': 'error', 'verdict_reason': 'input-error'}),
        (34, {'verdict': 'error', 'verdict_reason': 'unsupported'}),
        (35, {'verdict': 'error', 'verdict_reason': 'crash'}),
        (36, {'verdict': 'error', 'verdict_reason': 'input-error'}),
        (37, {'verdict': 'error', 'verdict_reason': 'unsupported'}),
        (-11, {'verdict': 'error', 'verdict_reason': 'crash'}),
        (99, {'verdict': 'error', 'verdict_reason': 'unexplained'}),
    ])
    def test_table(self, code, expected):
        assert _verdict(code) == expected

    def test_real_lmcut_on_axioms(self):
        assert _verdict(34, _log('fd-lmcut-ax'), _fd('astar(lmcut())')) == {
            'verdict': 'error', 'verdict_reason': 'unsupported'}

    def test_real_plan(self):
        assert _verdict(0, _log('fd-sat'), _fd('astar(blind())')) == {
            'verdict': 'sat'}

    def test_portfolio_34_is_a_member_bailing_out(self):
        assert _verdict(34, '', None, anytime=True) == {
            'verdict': 'unknown', 'verdict_reason': 'portfolio'}

    @pytest.mark.parametrize('flag', ['timed_out', 'interrupted'])
    def test_killed_whatever_the_code(self, flag):
        """The runner killed it: whatever code it died with, the planner never
        wrote its own account of how the search ended."""
        assert _verdict(-1, _log('fd-exhaust'), None, **{flag: True}) == {
            'verdict': 'killed'}


def test_vocabulary_is_shared_with_savilerow():
    codes = list(range(-1, 40))
    verdicts = {_verdict(c)['verdict'] for c in codes}
    verdicts.add(_verdict(0, timed_out=True)['verdict'])
    assert verdicts <= set(SAVILEROW_VERDICTS)


def test_answer_codes_are_exactly_the_non_errors():
    for code in range(0, 40):
        answered = _verdict(code, '', _fd('astar(blind())'))['verdict'] != 'error'
        assert (code in FD_ANSWER_RETURNCODES) == answered, code
