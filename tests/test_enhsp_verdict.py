"""
Tests for ``SolverStatsExtractor.enhsp_verdict`` (issue #6).

ENHSP exits 0 for a plan, for a proof, and for a run that never loaded its
input, so the verdict is read from the log. As with Fast Downward, the cases run
against REAL planner output (tests/fixtures/enhsp, see its README).
"""

from pathlib import Path

import pytest

from solver_support.solver_stats import SAVILEROW_VERDICTS, SolverStatsExtractor

FIXTURES = Path(__file__).parent / 'fixtures' / 'enhsp'


def _read(name, stream):
    path = FIXTURES / f'{name}.{stream}'
    return path.read_text() if path.exists() else ''


def _enhsp(*extra, planner='opt-blind'):
    return ['enhsp', '-o', 'd.pddl', '-f', 'p.pddl', '-planner', planner,
            '-sp', 'plan', *extra]


def _verdict(name, returncode, command, **kwargs):
    return SolverStatsExtractor.enhsp_verdict(
        returncode, _read(name, 'stdout'), _read(name, 'stderr'), command,
        **kwargs)


@pytest.mark.parametrize('fixture, code, command, expected', [
    ('enhsp-sat', 0, _enhsp(), {'verdict': 'sat'}),
    ('enhsp-exhaust', 0, _enhsp(),
     {'verdict': 'unsat', 'verdict_reason': 'exhausted'}),
    # the same `Problem unsolvable`, from a search that pruned with helpful actions
    ('enhsp-ha', 0, _enhsp(planner='sat-hmrph'),
     {'verdict': 'unknown', 'verdict_reason': 'incomplete'}),
    # and from a cost bound below the plan's (sat.pddl is solvable)
    ('enhsp-dl', 0, _enhsp('-dl', '1'),
     {'verdict': 'unknown', 'verdict_reason': 'bounded'}),
    ('enhsp-syntax', 255, _enhsp(),
     {'verdict': 'error', 'verdict_reason': 'input-error'}),
    # exit 0 and `Unsolvable Problem`, after a FileNotFoundException on stderr
    ('enhsp-nofile', 0, _enhsp(),
     {'verdict': 'error', 'verdict_reason': 'crash'}),
    ('enhsp-oom', 1, _enhsp(),
     {'verdict': 'unknown', 'verdict_reason': 'memory'}),
])
def test_real_logs(fixture, code, command, expected):
    assert _verdict(fixture, code, command) == expected


def test_the_issue_6_hazard_is_not_a_proof():
    """A missing domain file exits 0 with an `unsolvable` line on stdout. That
    must never read as unsat, whatever the stderr check decides."""
    assert _verdict('enhsp-nofile', 0, _enhsp())['verdict'] != 'unsat'


def test_refused_without_an_exception_proves_nothing():
    """`Unsolvable Problem` alone: grounding returned null, which may be a sound
    reachability refutation, but the log does not say which."""
    stdout = 'Domain parsed\nProblem parsed\nGrounding..\nUnsolvable Problem\n'
    assert SolverStatsExtractor.enhsp_verdict(0, stdout, '', _enhsp()) == {
        'verdict': 'unknown', 'verdict_reason': 'grounding'}


def test_a_swallowed_search_exception_is_a_crash():
    """ENHSP.planning() logs the exception at SEVERE and returns: no verdict
    line, exit 0. No input here makes it throw, so this stderr is modelled on
    enhsp-nofile.stderr."""
    stdout = _read('enhsp-exhaust', 'stdout').split('Problem unsolvable')[0]
    stderr = ('Oct 22, 2026 11:00:26 PM planners.ENHSP planning\n'
              'SEVERE: null\njava.lang.NullPointerException\n')
    assert SolverStatsExtractor.enhsp_verdict(0, stdout, stderr, _enhsp()) == {
        'verdict': 'error', 'verdict_reason': 'crash'}


@pytest.mark.parametrize('extra, planner, expected', [
    (('-ha', 'true'), None, 'incomplete'),
    (('-ht', 'true'), None, 'incomplete'),
    # no -planner: ENHSP's defaults are hadd and gbfs, which can prune
    (('-ha', 'false'), None, 'unverified-search'),
    # a preset overrides -ha (ENHSP.setPlanner resets it)
    (('-ha', 'true'), 'opt-blind', None),
    (('-anytime',), 'sat-hmrp', 'anytime'),
    (('-autoanytime',), None, 'anytime'),
])
def test_configuration_flags(extra, planner, expected):
    command = ['enhsp', '-o', 'd.pddl', '-f', 'p.pddl', *extra]
    if planner:
        command += ['-planner', planner]
    stats = _verdict('enhsp-exhaust', 0, command)
    if expected is None:
        assert stats == {'verdict': 'unsat', 'verdict_reason': 'exhausted'}
    else:
        assert stats == {'verdict': 'unknown', 'verdict_reason': expected}


@pytest.mark.parametrize('flag', ['timed_out', 'interrupted'])
def test_killed(flag):
    assert _verdict('enhsp-exhaust', -15, _enhsp(), **{flag: True}) == {
        'verdict': 'killed'}


def test_unexplained_exits():
    assert SolverStatsExtractor.enhsp_verdict(0, '', '', _enhsp()) == {
        'verdict': 'error', 'verdict_reason': 'unexplained'}
    assert SolverStatsExtractor.enhsp_verdict(-9, '', '', _enhsp()) == {
        'verdict': 'error', 'verdict_reason': 'crash'}


def test_vocabulary_is_shared_with_savilerow():
    names = ['enhsp-sat', 'enhsp-exhaust', 'enhsp-ha', 'enhsp-dl',
             'enhsp-syntax', 'enhsp-nofile', 'enhsp-oom']
    for name in names:
        assert _verdict(name, 0, _enhsp())['verdict'] in SAVILEROW_VERDICTS


@pytest.mark.parametrize('extra, expected', [
    # blind under the WAStar class drops only duplicates, whatever -wh and
    # whichever of wastar / gbfs; AIBR preprocessing must be off
    (('-h', 'blind', '-s', 'WAStar', '-dap'), 'exhausted'),
    (('-h', 'blind', '-s', 'wastar', '-wh', '5', '-dap'), 'exhausted'),
    (('-h', 'blind', '-dap'), 'exhausted'),                 # -s defaults to gbfs
    (('-h', 'blind', '-s', 'WAStar'), 'unverified-search'),  # AIBR preprocessing on
    (('-h', 'blind', '-s', 'lazygbfs', '-dap'), 'unverified-search'),
    (('-h', 'blind', '-s', 'ehs', '-dap'), 'unverified-search'),
    (('-h', 'hrmax', '-s', 'WAStar', '-dap'), 'unverified-search'),  # UPF enhsp-opt
    ((), 'unverified-search'),                                # hadd, gbfs
])
def test_only_a_verified_search_proves(extra, expected):
    command = ['enhsp', '-o', 'd.pddl', '-f', 'p.pddl', *extra]
    verdict = 'unsat' if expected == 'exhausted' else 'unknown'
    assert _verdict('enhsp-exhaust', 0, command) == {
        'verdict': verdict, 'verdict_reason': expected}


@pytest.mark.parametrize('planner, expected', [
    ('opt-blind', 'exhausted'),          # turns AIBR preprocessing off itself
    ('opt-hrmax', 'unverified-search'),
    ('sat-hadd', 'unverified-search'),
    ('no-such-preset', 'unverified-search'),   # ENHSP falls back to gbfs + hadd
])
def test_presets(planner, expected):
    verdict = 'unsat' if expected == 'exhausted' else 'unknown'
    assert _verdict('enhsp-exhaust', 0, _enhsp(planner=planner)) == {
        'verdict': verdict, 'verdict_reason': expected}
