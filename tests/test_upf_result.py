"""
Tests for the UPF result file (issue #8): ``read_upf_result``,
``SolverStatsExtractor.upf_result_stats`` and ``UpfPlanSolver(result_file=True)``.

The CLI reports facts; the verdict is judged from the engine's own log. A UPF
run streams its engine's ordinary log, so the engine logs here are the REAL
direct-run fixtures (tests/fixtures/fd, tests/fixtures/enhsp).
"""

import json
import re
from pathlib import Path
from unittest.mock import Mock

import pytest

from solver_support.run_summary import PhaseResult, RunSummary
from solver_support.solver_runner import SolverResult
from solver_support.solver_stats import (
    SAVILEROW_VERDICTS, UPF_RESULT_SCHEMA, SolverStatsExtractor, read_upf_result)
from solver_support.solver_upfplan import UpfPlanSolver

FIXTURES = Path(__file__).parent / 'fixtures'


def _read(rel):
    path = FIXTURES / rel
    return path.read_text() if path.exists() else ''


def _file(**fields):
    doc = {'schema': UPF_RESULT_SCHEMA, 'finished': True, 'plan_found': False,
           'times': {'read': 0.004, 'compile': 0.215, 'engine': 0.09}}
    doc.update(fields)
    return doc


def _stats(doc, stderr, returncode=1, **kwargs):
    return SolverStatsExtractor.upf_result_stats(
        doc, returncode, '', stderr, **kwargs)


def _verdict(stats):
    return {k: v for k, v in stats.items() if k.startswith('verdict')}


class TestFastDownwardViaUpf:

    def test_the_issue_8_run_is_a_proof(self):
        """Blind A* ran out of states: UPF says UNSOLVABLE_INCOMPLETELY for every
        exit 12, but the engine's log says exhausted."""
        doc = _file(engine='fast-downward',
                    engine_status='UNSOLVABLE_INCOMPLETELY')
        stats = _stats(doc, _read('fd/fd-exhaust.stdout'))
        assert _verdict(stats) == {'verdict': 'unsat', 'verdict_reason': 'exhausted'}
        assert stats['solution_found'] is False
        assert stats['up_compile_time'] == pytest.approx(0.215)

    def test_the_allowlist_applies(self):
        """The same exhaustion under lmcut: what UPF's fast-downward-opt runs,
        and calls UNSOLVABLE_PROVEN."""
        log = re.sub(r"--search '[^']*'", "--search 'astar(lmcut())'",
                     _read('fd/fd-exhaust.stdout'))
        doc = _file(engine='fast-downward-opt', engine_status='UNSOLVABLE_PROVEN')
        assert _verdict(_stats(doc, log)) == {
            'verdict': 'unknown', 'verdict_reason': 'unverified-search'}

    def test_a_reported_exit_code_outranks_the_log(self):
        doc = _file(engine='fast-downward', engine_returncode=23)
        assert _verdict(_stats(doc, _read('fd/fd-exhaust.stdout'))) == {
            'verdict': 'unknown', 'verdict_reason': 'search-limit'}

    def test_an_unsupported_task(self):
        doc = _file(engine='fast-downward-opt')
        assert _verdict(_stats(doc, _read('fd/fd-lmcut-ax.stdout'))) == {
            'verdict': 'error', 'verdict_reason': 'unsupported'}

    def test_symk_reads_as_fast_downward(self):
        doc = _file(engine='symk')
        assert _verdict(_stats(doc, _read('fd/symk-exhaust.stdout'))) == {
            'verdict': 'unsat', 'verdict_reason': 'exhausted'}

    def test_no_exit_code_anywhere_gives_no_verdict(self):
        doc = _file(engine='fast-downward')
        assert 'verdict' not in _stats(doc, 'some engine chatter\n')

    def test_a_plan(self):
        doc = _file(engine='fast-downward', plan_found=True, steps=4, score=7)
        stats = _stats(doc, _read('fd/fd-sat.stdout'), returncode=0)
        assert stats['verdict'] == 'sat'
        assert (stats['solution_found'], stats['plan_length'], stats['score']) == (
            True, 4, 7)


class TestEnhspViaUpf:

    @pytest.mark.parametrize('fixture, expected', [
        ('enhsp-exhaust', {'verdict': 'unsat', 'verdict_reason': 'exhausted'}),
        # UPF calls this UNSOLVABLE_PROVEN: it exited 0 without a plan
        ('enhsp-nofile', {'verdict': 'error', 'verdict_reason': 'crash'}),
        ('enhsp-syntax', {'verdict': 'error', 'verdict_reason': 'input-error'}),
        ('enhsp-oom', {'verdict': 'unknown', 'verdict_reason': 'memory'}),
    ])
    def test_real_logs(self, fixture, expected):
        stderr = _read(f'enhsp/{fixture}.stdout') + _read(f'enhsp/{fixture}.stderr')
        doc = _file(engine='enhsp', engine_status='UNSOLVABLE_PROVEN')
        assert _verdict(_stats(doc, stderr)) == expected

    def test_the_engine_command_is_read(self):
        """Helpful-action pruning is in the argv, not the log."""
        doc = _file(engine='enhsp',
                    engine_command=['java', '-jar', 'enhsp.jar', '-planner', 'sat-hmrph'])
        assert _verdict(_stats(doc, _read('enhsp/enhsp-ha.stdout'))) == {
            'verdict': 'unknown', 'verdict_reason': 'incomplete'}


class TestHowTheRunEnded:

    @pytest.mark.parametrize('flag', ['timed_out', 'interrupted'])
    def test_killed(self, flag):
        doc = _file(engine='fast-downward', finished=False, plan_found=None)
        assert _verdict(_stats(doc, '', **{flag: True})) == {'verdict': 'killed'}

    def test_unfinished_and_not_killed_is_a_crash(self):
        doc = _file(engine='fast-downward', finished=False, plan_found=None)
        assert _verdict(_stats(doc, _read('fd/fd-exhaust.stdout'))) == {
            'verdict': 'error', 'verdict_reason': 'crash'}

    def test_no_file_is_an_error(self):
        assert _verdict(_stats(None, '')) == {
            'verdict': 'error', 'verdict_reason': 'no-result'}

    def test_an_engine_without_a_reader_gets_no_verdict(self):
        doc = _file(engine='tamer')
        assert 'verdict' not in _stats(doc, _read('fd/fd-exhaust.stdout'))

    def test_a_killed_runs_last_layer_is_bounded_by_the_engine_time(self):
        """The final cost layer ends at the phase duration less UPF's read and
        compile time and FD's translate time (0.004s in this log)."""
        log = _read('fd/fd-exhaust.stdout')
        log = log[:log.index('Completely explored')]
        doc = _file(engine='fast-downward', finished=False, plan_found=None,
                    times={'read': 1.0, 'compile': 2.0})
        stats = _stats(doc, log, timed_out=True, duration=13.0)
        direct = SolverStatsExtractor.extract_search_cost_layers(
            log, end_hint=13.0 - 1.0 - 2.0 - 0.004)
        assert stats['cost_layers'] == direct['cost_layers']
        assert stats['cost_layers'][-1][1] == pytest.approx(9.996 - 0.001831)

    def test_every_verdict_is_in_the_vocabulary(self):
        for doc in (None, _file(engine='fast-downward'), _file(finished=False)):
            v = _stats(doc, _read('fd/fd-exhaust.stdout')).get('verdict')
            assert v is None or v in SAVILEROW_VERDICTS


class TestReadUpfResult:

    def test_round_trip(self, tmp_path):
        path = tmp_path / 'r.json'
        path.write_text(json.dumps(_file(engine='symk')))
        assert read_upf_result(path)['engine'] == 'symk'

    @pytest.mark.parametrize('content', ['', '{not json', '[]',
                                         json.dumps({'schema': 99, 'finished': True})])
    def test_unreadable_or_foreign(self, tmp_path, content):
        path = tmp_path / 'r.json'
        path.write_text(content)
        assert read_upf_result(path) is None

    def test_missing(self, tmp_path):
        assert read_upf_result(tmp_path / 'absent.json') is None


class TestUpfPlanSolverResultFile:
    """The solver passes --result only when told the CLI supports it, and the
    run's outcome then follows the file."""

    @pytest.fixture(autouse=True)
    def _setup(self, fixture_registry, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self.summary = RunSummary(instance='i', model='upf', solver='upffd',
                                  timestamp='t')
        self.runner = Mock()
        self.runner.logger = Mock()
        self.runner.summary = self.summary
        self.param = tmp_path / 'i.upf'
        self.param.write_text('problem')

    def _run(self, result_file, doc, returncode=1, stdout='', stderr='', **kw):
        def run_phase(name, cmd, expected_outputs=None):
            if '--result' in cmd and doc is not None:
                Path(cmd[cmd.index('--result') + 1]).write_text(json.dumps(doc))
            self.summary.add_phase(PhaseResult(name=name, command=cmd,
                                               returncode=returncode,
                                               success=returncode == 0))
            return SolverResult(command=cmd, returncode=returncode, stdout=stdout,
                                stderr=stderr, duration=1.0, **kw)
        self.runner.run_phase.side_effect = run_phase
        solver = UpfPlanSolver(self.runner, solver_name='upffd', build_mode='upf',
                               upfplan_command='my-upfplan', result_file=result_file)
        plan = solver.solve(self.param)
        return plan, self.runner.run_phase.call_args[0][1]

    def test_off_by_default_passes_nothing(self):
        plan, cmd = self._run(None, None)
        assert '--result' not in cmd
        self.summary.finalize('failed', proves_unsat=True)
        assert (self.summary.outcome, self.summary.outcome_reason) == (
            'unknown', 'no-verdict')

    def test_a_proof_reaches_the_outcome(self):
        doc = _file(engine='fast-downward')
        plan, cmd = self._run(True, doc, stderr=_read('fd/fd-exhaust.stdout'))
        assert plan is None and '--result' in cmd
        self.summary.finalize('failed', proves_unsat=True)
        assert (self.summary.outcome, self.summary.outcome_reason) == (
            'unsat', 'exhausted')
        phase = self.summary.get_phase('solving')
        assert any(f.endswith('.result.json') for f in phase.output_files)

    def test_the_file_decides_a_plan_despite_the_exit_code(self):
        doc = _file(engine='fast-downward', plan_found=True, steps=2)
        plan, _ = self._run(True, doc, returncode=3, stdout='(a)\n(b)\n',
                            stderr=_read('fd/fd-sat.stdout'))
        assert plan is not None and plan.read_text() == '(a)\n(b)\n'
        self.summary.finalize('done', proves_unsat=True)
        assert self.summary.outcome == 'solved'

    def test_a_cli_that_wrote_nothing(self):
        plan, _ = self._run(True, None, returncode=2)
        self.summary.finalize('failed', proves_unsat=True)
        assert (self.summary.outcome, self.summary.outcome_reason) == (
            'error', 'no-result')

    def test_a_stale_file_is_removed_first(self, tmp_path):
        solver = UpfPlanSolver(self.runner, solver_name='upffd', build_mode='upf',
                               upfplan_command='my-upfplan', result_file=True)
        stale = solver._generate_solution_file_path(suffix='.result.json')
        stale.write_text(json.dumps(_file(engine='fast-downward', plan_found=True)))
        self._run(True, None, returncode=2)
        assert self.summary.get_phase('solving').solver_stats['verdict'] == 'error'

    def test_the_class_attribute_opts_in(self):
        class Mine(UpfPlanSolver):
            UPFPLAN_RESULT_FILE = True
        assert Mine(self.runner, upfplan_command='x').result_file is True
        assert Mine(self.runner, upfplan_command='x', result_file=False).result_file is False
