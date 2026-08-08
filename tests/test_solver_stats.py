#!/usr/bin/env python3
"""
Unit tests for solver statistics extractors.

Tests the SolverStatsExtractor class and related functions for parsing
solver output and extracting structured metrics.
"""

import pytest
from solver_support.solver_stats import SolverStatsExtractor, detect_solver_from_command

# The captured sample outputs below were produced by a consumer that reports its
# score scaled by 100, so they print e.g. 1700 for a score of 17. The core parser
# is scale-blind -- it returns whatever number the solver printed -- and the
# factor is kept here only so each expectation reads against its sample.
MULT = 100


class TestSavileRowStatsExtractor:
    """Test statistics extraction from Savile Row output."""
    
    def test_savilerow_basic_solution(self):
        """Test extracting stats from successful Savile Row run."""
        stdout = """
WARNING: interval 7..6 is out of order. Rewriting to empty interval.
Created output SAT file results-tmp/test-model-01.param.dimacs
No solution found.
Created information file results-tmp/test-model-01.param.info
        0.14 real         0.08 user         0.03 sys
No solution found
WARNING: interval 7..6 is out of order. Rewriting to empty interval.
Created output SAT file results-tmp/test-model-07.param.dimacs
Created solution file results-tmp/test-model-07.param.solution
Created information file results-tmp/test-model-07.param.info
        0.56 real         0.49 user         0.05 sys
"""
        stderr = ""
        
        stats = SolverStatsExtractor.extract_stats('savilerow', stdout, stderr, duration=0.56, horizon_number=7)
        
        assert stats['horizon_number'] == 7
        assert stats['final_horizon'] == 7
        assert stats['solution_found'] is True
        assert stats['real_time'] == 0.56  # From internal duration
        assert stats['used_sat_solver'] is True
        assert stats['interval_warnings'] == 2
    
    def test_savilerow_no_solution(self):
        """Test extracting stats when no solution is found."""
        stdout = """
WARNING: interval 1..0 is out of order. Rewriting to empty interval.
Created output SAT file results-tmp/test-model-01.param.dimacs
No solution found.
Created information file results-tmp/test-model-01.param.info
        0.15 real         0.09 user         0.03 sys
No solution found
"""
        stderr = ""
        
        stats = SolverStatsExtractor.extract_stats('savilerow', stdout, stderr, duration=0.15, horizon_number=1)
        
        assert stats['horizon_number'] == 1
        assert 'final_horizon' not in stats
        assert stats['solution_found'] is False
        assert stats['real_time'] == 0.15
        assert stats['used_sat_solver'] is True
        assert stats['interval_warnings'] == 1
    
    def test_savilerow_single_attempt_success(self):
        """Test extracting stats from single successful attempt."""
        stdout = """
Created output SAT file results-tmp/test-model-01.param.dimacs
Created solution file results-tmp/test-model-01.param.solution
Created information file results-tmp/test-model-01.param.info
        1.25 real         1.10 user         0.08 sys
"""
        stderr = ""
        
        stats = SolverStatsExtractor.extract_stats('savilerow', stdout, stderr, duration=1.25, horizon_number=1)
        
        assert stats['horizon_number'] == 1
        assert stats['final_horizon'] == 1
        assert stats['solution_found'] is True
        assert stats['real_time'] == 1.25
    
    def test_savilerow_timeout(self):
        """Test detecting timeout in Savile Row output."""
        stdout = "Process timed out after 1200 seconds"
        stderr = "TIMEOUT: Solver exceeded time limit"
        
        stats = SolverStatsExtractor.extract_savilerow_stats(stdout, stderr)
        
        assert stats['timed_out'] is True
        assert stats['solution_found'] is False
    
    def test_savilerow_no_timing(self):
        """Test handling Savile Row output without timing information."""
        stdout = """
Created output SAT file results-tmp/test-model-01.param.dimacs
Created solution file results-tmp/test-model-01.param.solution
"""
        stderr = ""
        
        stats = SolverStatsExtractor.extract_savilerow_stats(stdout, stderr)
        
        # horizon_number not available without explicit parameter
        assert stats['final_horizon'] == 1
        assert stats['solution_found'] is True
        assert 'real_time' not in stats


class TestSavileRowInfoTimes:
    """Test the tailoring/solver split parsed from Savile Row .info files."""

    def _write_info(self, tmp_path, body):
        p = tmp_path / "run.param.info"
        p.write_text(body)
        return p

    def test_both_times_parsed(self, tmp_path):
        # Realistic .info: labelled Key:Value lines; only the two totals matter.
        info = self._write_info(
            tmp_path,
            "SolverMemOut:0\n"
            "SolverTotalTime:21.72\n"
            "SavileRowClauseOut:0\n"
            "SavileRowTotalTime:9.779\n"
            "SolverNodes:655835\n")
        times = SolverStatsExtractor.extract_savilerow_info_times(info)
        assert times == {'savilerow_time': 9.779, 'solver_time': 21.72}

    def test_missing_file_returns_empty(self, tmp_path):
        # Hard timeout / cleaned-up run: no file -> empty, best-effort.
        assert SolverStatsExtractor.extract_savilerow_info_times(
            tmp_path / "nope.info") == {}

    def test_partial_only_present_keys(self, tmp_path):
        info = self._write_info(tmp_path, "SavileRowTotalTime:1.5\n")
        assert SolverStatsExtractor.extract_savilerow_info_times(info) == {
            'savilerow_time': 1.5}

    def test_unparseable_value_skipped(self, tmp_path):
        info = self._write_info(
            tmp_path, "SavileRowTotalTime:NA\nSolverTotalTime:2.0\n")
        assert SolverStatsExtractor.extract_savilerow_info_times(info) == {
            'solver_time': 2.0}

    def test_times_are_floats_even_when_the_file_has_integers(self, tmp_path):
        """The two totals feed a float timing model, whatever the file says."""
        info = self._write_info(
            tmp_path, "SavileRowTotalTime:9\nSolverTotalTime:21\n")
        times = SolverStatsExtractor.extract_savilerow_info_times(info)
        assert times == {'savilerow_time': 9.0, 'solver_time': 21.0}
        assert all(isinstance(v, float) for v in times.values())


class TestSavileRowInfo:
    """The full ``.info`` view: every Key:Value pair, keys verbatim.

    The two totals are what the core's timing model needs; a consumer's run
    report may want the encoding size and search counters that sit in the same
    file, so this returns the lot rather than a curated subset.
    """

    def _write_info(self, tmp_path, body):
        p = tmp_path / "run.param.info"
        p.write_text(body)
        return p

    def test_all_keys_returned_with_values_coerced(self, tmp_path):
        info = self._write_info(
            tmp_path,
            "SolverMemOut:0\n"
            "SolverTotalTime:21.72\n"
            "SATVars:12345\n"
            "SATClauses:678901\n"
            "SolverNodes:655835\n"
            "SolverSatisfiable:1\n"
            "SavileRowTotalTime:9.779\n")

        assert SolverStatsExtractor.extract_savilerow_info(info) == {
            'SolverMemOut': 0,
            'SolverTotalTime': 21.72,
            'SATVars': 12345,
            'SATClauses': 678901,
            'SolverNodes': 655835,
            'SolverSatisfiable': 1,
            'SavileRowTotalTime': 9.779,
        }

    def test_non_numeric_value_kept_as_text(self, tmp_path):
        """Coercion is best-effort: an unparseable value is not dropped here."""
        info = self._write_info(tmp_path, "SolverStatus:NA\n")
        assert SolverStatsExtractor.extract_savilerow_info(info) == {
            'SolverStatus': 'NA'}

    def test_lines_without_a_separator_are_skipped(self, tmp_path):
        info = self._write_info(tmp_path, "garbage line\nSATVars:7\n\n")
        assert SolverStatsExtractor.extract_savilerow_info(info) == {'SATVars': 7}

    def test_missing_file_returns_empty(self, tmp_path):
        # Same best-effort contract as the times wrapper: a hard timeout can
        # kill Savile Row before it writes the file.
        assert SolverStatsExtractor.extract_savilerow_info(
            tmp_path / "nope.info") == {}


_FD_LAYER_OUTPUT = """\
Done! [0.160s CPU, 0.143s wall-clock]
[t=0.005230s, 435307568 KB] f = 0, 1 evaluated, 0 expanded
[t=0.005249s, 435307568 KB] f = 1, 7 evaluated, 1 expanded
[t=0.005322s, 435307568 KB] f = 2, 52 evaluated, 31 expanded
[t=0.005979s, 435307568 KB] f = 3, 486 evaluated, 301 expanded
[t=0.006697s, 435307568 KB] Solution found!
[t=0.006703s, 435307568 KB] Actual search time: 0.001467s
Plan length: 20 step(s).
"""

_SYMK_LAYER_OUTPUT = """\
[t=0.243621s, 435575792 KB] BOUND: 0 < 2147483647 [0/1 plans], reconstruction time: 0.0s
[t=0.243696s, 435575792 KB] BOUND: 1 < 2147483647 [0/1 plans], dir: FW
[t=0.243986s, 435575792 KB] BOUND: 2 < 2147483647 [0/1 plans], dir: FW
[t=0.245523s, 435575792 KB] BOUND: 3 < 2147483647 [0/1 plans], dir: FW
[t=0.249882s, 435575792 KB] Actual search time: 0.006265s
[t=0.249886s, 435575792 KB] Best plan:
[t=0.249886s, 435575792 KB] Plan length: 20 step(s).
[t=0.249886s, 435575792 KB] Plan cost: 3
"""


class TestSearchCostLayers:
    """Test the FD (f = N) / SymK (BOUND: N) cost-layer progression parser."""

    def test_fd_f_layers(self):
        r = SolverStatsExtractor.extract_search_cost_layers(_FD_LAYER_OUTPUT)
        # Setup = time to reach bound 1; layer 0 is the trivial initial layer.
        assert r['search_setup_time'] == pytest.approx(0.005249)
        cl = r['cost_layers']
        assert [n for n, _, _ in cl] == [1, 2, 3]
        # Durations are the gaps to the next bound (end-of-search for the last).
        assert cl[0][1] == pytest.approx(0.000073)
        assert cl[2][1] == pytest.approx(0.006697 - 0.005979)
        # Only the final bound carries the solution.
        assert [found for _, _, found in cl] == [False, False, True]

    def test_symk_bound_layers(self):
        r = SolverStatsExtractor.extract_search_cost_layers(_SYMK_LAYER_OUTPUT)
        assert r['search_setup_time'] == pytest.approx(0.243696)
        cl = r['cost_layers']
        assert [n for n, _, _ in cl] == [1, 2, 3]
        assert cl[2][1] == pytest.approx(0.249882 - 0.245523)
        assert cl[-1][2] is True

    def test_no_layers_returns_empty(self):
        assert SolverStatsExtractor.extract_search_cost_layers("no layers here") == {}

    def test_timeout_no_end_marker_still_yields_layers(self):
        # Cut off after f = 2 (no Solution found! / Actual search time:): the
        # layers are still returned, none marked solved, and the final layer is
        # bounded by the last timestamp seen (so 0 without an end hint).
        out = ("[t=0.10s, 1 KB] f = 0, 1 evaluated, 0 expanded\n"
               "[t=0.20s, 1 KB] f = 1, 5 evaluated, 1 expanded\n"
               "[t=0.50s, 1 KB] f = 2, 40 evaluated, 12 expanded\n"
               "caught signal 15 -- exiting\n")
        r = SolverStatsExtractor.extract_search_cost_layers(out)
        cl = r['cost_layers']
        assert [n for n, _, _ in cl] == [1, 2]
        assert all(found is False for _, _, found in cl)
        assert cl[-1][1] == pytest.approx(0.0)

    def test_timeout_end_hint_extends_final_layer(self):
        # The kill-time hint extends the final, blown-up layer from its last
        # printed timestamp (0.50) to the real kill time (9.5).
        out = ("[t=0.20s, 1 KB] f = 1, 5 evaluated, 1 expanded\n"
               "[t=0.50s, 1 KB] f = 2, 40 evaluated, 12 expanded\n")
        r = SolverStatsExtractor.extract_search_cost_layers(out, end_hint=9.5)
        cl = r['cost_layers']
        assert cl[-1][0] == 2
        assert cl[-1][1] == pytest.approx(9.0)
        assert cl[-1][2] is False

    def test_timeout_end_hint_threaded_via_duration(self):
        # The FD extractor turns the phase duration into the end hint
        # (duration - translate_time), so a timed-out final layer is bounded.
        out = ("Done! [0.1s CPU, 0.10s wall-clock]\n"
               "[t=0.20s, 1 KB] f = 1, 5 evaluated, 1 expanded\n"
               "[t=0.50s, 1 KB] f = 2, 40 evaluated, 12 expanded\n")
        stats = SolverStatsExtractor.extract_fast_downward_stats(out, "", duration=9.6)
        assert stats['cost_layers'][-1][1] == pytest.approx(9.0)

    def test_fd_extractor_merges_layers_and_translate(self):
        stats = SolverStatsExtractor.extract_fast_downward_stats(_FD_LAYER_OUTPUT, "")
        assert stats['translate_time'] == pytest.approx(0.143)
        assert 'cost_layers' in stats and 'search_setup_time' in stats
        assert stats['solution_found'] is True

    def test_symk_routes_to_fd_extractor(self):
        # SymK shares FD's output; both the command detector and the name
        # dispatch must send it to the FD extractor (not the generic one).
        assert detect_solver_from_command(
            ['symk', 'dom.pddl', 'p.pddl', '--search', 'sym_fw()']) == 'fast-downward'
        stats = SolverStatsExtractor.extract_stats('symk', _SYMK_LAYER_OUTPUT, "")
        assert 'cost_layers' in stats

    def test_setup_time_uses_first_bound_not_literally_one(self):
        # UPF-compiled models have score costs, so bounds do not start at 1;
        # search_setup_time must be the first tracked bound's timestamp.
        out = ("[t=0.05s, 1 KB] f = 0, 1 evaluated, 0 expanded\n"
               "[t=4.69s, 1 KB] f = 16, 100 evaluated, 50 expanded\n"
               "[t=4.73s, 1 KB] f = 19, 200 evaluated, 90 expanded\n"
               "[t=4.80s, 1 KB] Solution found!\n"
               "Plan length: 20 step(s).\n")
        r = SolverStatsExtractor.extract_search_cost_layers(out)
        assert r['search_setup_time'] == pytest.approx(4.69)
        assert [n for n, _, _ in r['cost_layers']] == [16, 19]


class TestUpfEngineLogParsing:
    """UPF surfaces the engine's search log on stderr; extract_upf_stats must
    recover the cost-layers/translate time from there without disturbing its
    plan/score parsing (which reads stdout)."""

    def test_cost_layers_from_stderr_alongside_plan_on_stdout(self, wired_stdout_score):
        stdout = "Steps: 20\nScore: 17\n"          # UPF's own plan/score output
        stderr = _FD_LAYER_OUTPUT                   # the engine's f = N log
        stats = SolverStatsExtractor.extract_upf_stats(stdout, stderr)
        # Plan/score parsing (stdout) is unaffected.
        assert stats['solution_found'] is True
        assert stats['score'] == 17 * MULT
        # Engine cost-layers/translate recovered from stderr.
        assert 'cost_layers' in stats
        assert stats['translate_time'] == pytest.approx(0.143)

    def test_no_engine_log_leaves_no_cost_layers(self):
        # Empty engine log (no FD/SymK/ENHSP markers) -> plan/score only.
        stats = SolverStatsExtractor.extract_upf_stats("Steps: 12\nScore: 5\n", "")
        assert stats['solution_found'] is True
        assert 'cost_layers' not in stats

    def test_up_compile_time_from_stderr(self):
        # The UPF CLI reports the UP model-compilation time on stderr.
        stderr = _FD_LAYER_OUTPUT + "\nUP compilation time: 0.118s\n"
        stats = SolverStatsExtractor.extract_upf_stats("Steps: 20\nScore: 17\n", stderr)
        assert stats['up_compile_time'] == pytest.approx(0.118)

    def test_no_up_compile_time_when_absent(self):
        # --domain route (PDDL read directly) has no UP compilation.
        stats = SolverStatsExtractor.extract_upf_stats("Steps: 12\n", _FD_LAYER_OUTPUT)
        assert 'up_compile_time' not in stats

    def test_enhsp_engine_log_recovered_alongside_plan(self, wired_stdout_score):
        # upfenhsp: ENHSP's engine log (grounding + f(n) cost-layers) on stderr,
        # UPF's own plan/score on stdout. Both must survive.
        stdout = "Steps: 72\nScore: 17\n"
        stats = SolverStatsExtractor.extract_upf_stats(stdout, _ENHSP_OPT_OUTPUT)
        assert stats['solution_found'] is True
        assert stats['score'] == 17 * MULT
        assert stats['grounding_time'] == pytest.approx(7.921)
        assert [n for n, _, _ in stats['cost_layers']] == [1, 2, 3, 4]


# ENHSP optimising output (opt-blind / opt-hrmax): a timestamped "f(n) = N (Time:
# T)" cost-bound progression - the direct analogue of Fast Downward's f-layers.
# Blind search omits the "H1 Setup Time" line. The final Expanded/Evaluated totals
# are line-anchored (distinct from the per-line "(Expanded Nodes: N" counters).
_ENHSP_OPT_OUTPUT = """\
Grounding..
Grounding Time: 7921
h(I):0.0
f(n) = 1.0 (Expanded Nodes: 2, Evaluated States: 7, Time: 0.004) Frontier Size: 5
f(n) = 2.0 (Expanded Nodes: 32, Evaluated States: 58, Time: 0.015) Frontier Size: 26
f(n) = 3.0 (Expanded Nodes: 158, Evaluated States: 269, Time: 0.045) Frontier Size: 111
f(n) = 4.0 (Expanded Nodes: 426, Evaluated States: 771, Time: 0.118) Frontier Size: 345
Problem Solved

Found Plan:
0.0: (move_block loc_4_5 loc_3_5 left blue)
Plan-Length:15
Metric (Search):4.0
Planning Time (msec): 123
Heuristic Time (msec): 1
Search Time (msec): 122
Expanded Nodes:441
States Evaluated:795
"""

# ENHSP satisficing output (sat-hmrp / sat-hadd): improving-h " g(n)= " milestones
# that carry NO timestamps, so they yield aggregate times but no cost-layers.
_ENHSP_SAT_OUTPUT = """\
Grounding..
Grounding Time: 7813
H1 Setup Time (msec): 87
h(I):6.0
 g(n)= 1.0 h(n)=3.0
 g(n)= 2.0 h(n)=1.0
 g(n)= 3.0 h(n)=0.0
Problem Solved

Found Plan:
0.0: (move_block loc_4_5 loc_3_5 left blue)
Plan-Length:15
Metric (Search):4.0
Planning Time (msec): 302
Heuristic Time (msec): 288
Search Time (msec): 301
Expanded Nodes:25
States Evaluated:49
"""


class TestEnhspStatsExtractor:
    """Direct-ENHSP stats: aggregate times (grounding dominant), and cost-layers
    only under an optimising search."""

    def test_optimising_cost_layers(self):
        stats = SolverStatsExtractor.extract_enhsp_stats(_ENHSP_OPT_OUTPUT, "")
        cl = stats['cost_layers']
        assert [n for n, _, _ in cl] == [1, 2, 3, 4]
        # search_setup_time is the first f(n) timestamp; layer durations are gaps.
        assert stats['search_setup_time'] == pytest.approx(0.004)
        assert cl[0][1] == pytest.approx(0.015 - 0.004)
        assert cl[2][1] == pytest.approx(0.118 - 0.045)
        # The deepest bound ends at ENHSP's reported Search Time (122 ms), not the
        # last printed f(n) timestamp (0.118), and carries the solution.
        assert cl[3][1] == pytest.approx(0.122 - 0.118)
        assert [found for _, _, found in cl] == [False, False, False, True]

    def test_optimising_aggregate_and_outcome(self):
        stats = SolverStatsExtractor.extract_enhsp_stats(_ENHSP_OPT_OUTPUT, "")
        # Milliseconds -> seconds.
        assert stats['grounding_time'] == pytest.approx(7.921)
        assert stats['search_time'] == pytest.approx(0.122)
        assert stats['heuristic_time'] == pytest.approx(0.001)
        # Blind search prints no H1 Setup line.
        assert 'h1_setup_time' not in stats
        # Final line-anchored totals, not the per-f(n)-line counters.
        assert stats['expanded_states'] == 441
        assert stats['evaluated_states'] == 795
        assert stats['solution_found'] is True
        assert stats['plan_length'] == 15
        assert stats['plan_cost'] == pytest.approx(4.0)

    def test_satisficing_has_times_but_no_layers(self):
        # g(n)= milestones have no timestamps -> aggregate times only, no curve.
        stats = SolverStatsExtractor.extract_enhsp_stats(_ENHSP_SAT_OUTPUT, "")
        assert stats['grounding_time'] == pytest.approx(7.813)
        assert stats['h1_setup_time'] == pytest.approx(0.087)
        assert 'cost_layers' not in stats
        assert 'search_setup_time' not in stats
        assert stats['solution_found'] is True

    def test_timeout_bounds_final_layer_via_duration(self):
        # Cut off after f = 2 (no Problem Solved / no Search Time line): layers
        # still returned, none solved, final layer bounded at the search-relative
        # kill time = duration - grounding.
        out = ("Grounding Time: 500\n"
               "f(n) = 1.0 (Expanded Nodes: 2, Evaluated States: 7, Time: 0.10) Frontier Size: 5\n"
               "f(n) = 2.0 (Expanded Nodes: 32, Evaluated States: 58, Time: 0.50) Frontier Size: 26\n")
        stats = SolverStatsExtractor.extract_enhsp_stats(out, "", duration=10.0)
        cl = stats['cost_layers']
        assert [n for n, _, _ in cl] == [1, 2]
        assert all(found is False for _, _, found in cl)
        # end_hint = 10.0 - 0.5 grounding = 9.5; final layer 9.5 - 0.50 = 9.0.
        assert cl[-1][1] == pytest.approx(9.0)
        assert 'solution_found' not in stats

    def test_dispatch_routes_enhsp(self):
        # Command detection and the name dispatch both send a direct ENHSP run to
        # the ENHSP extractor (not the generic one, which loses everything).
        assert detect_solver_from_command(
            ['enhsp', '-o', 'dom.pddl', '-f', 'p.pddl', '-planner', 'opt-blind']) == 'enhsp'
        stats = SolverStatsExtractor.extract_stats('enhsp', _ENHSP_OPT_OUTPUT, "", duration=9.4)
        assert 'cost_layers' in stats and stats['grounding_time'] == pytest.approx(7.921)


class TestFastDownwardStatsExtractor:
    """Test statistics extraction from Fast Downward output."""
    
    def test_fast_downward_successful_search(self):
        """Test extracting stats from successful Fast Downward run."""
        stdout = """
INFO     Running search (release).
[t=0.027425s, 34136572 KB] Solution found!
[t=0.027425s, 34136572 KB] Actual search time: 0.002613s
move_block loc_2_5 loc_3_5 right pink (1)
fall_block loc_3_5 loc_3_4 pink (0)
[t=0.027425s, 34136572 KB] Plan length: 20 step(s).
[t=0.027425s, 34136572 KB] Plan cost: 4
[t=0.027425s, 34136572 KB] Expanded 713 state(s).
[t=0.027425s, 34136572 KB] Reopened 0 state(s).
[t=0.027425s, 34136572 KB] Evaluated 931 state(s).
[t=0.027425s, 34136572 KB] Generated 1265 state(s).
[t=0.027425s, 34136572 KB] Search time: 0.002670s
[t=0.027425s, 34136572 KB] Total time: 0.027425s
Solution found.
Peak memory: 34136572 KB
INFO     Planner time: 0.37s
        0.44 real         0.33 user         0.06 sys
"""
        stderr = ""
        
        stats = SolverStatsExtractor.extract_stats('fast-downward', stdout, stderr, duration=0.44)
        
        assert stats['expanded_states'] == 713
        assert stats['evaluated_states'] == 931
        assert stats['generated_states'] == 1265
        assert stats['reopened_states'] == 0
        assert stats['plan_length'] == 20
        assert stats['plan_cost'] == 4
        assert stats['peak_memory_kb'] == 34136572
        assert stats['search_time'] == 0.002670
        assert stats['total_time'] == 0.027425
        assert stats['planner_time'] == 0.37
        assert stats['solution_found'] is True
        assert stats['real_time'] == 0.44
    
    def test_fast_downward_no_solution(self):
        """Test extracting stats when Fast Downward finds no solution."""
        stdout = """
INFO     Running search (release).
[t=0.027425s, 34136572 KB] Completely explored state space -- no solution!
[t=0.027425s, 34136572 KB] Expanded 1000 state(s).
[t=0.027425s, 34136572 KB] Evaluated 1500 state(s).
[t=0.027425s, 34136572 KB] Generated 2000 state(s).
[t=0.027425s, 34136572 KB] Search time: 0.025000s
[t=0.027425s, 34136572 KB] Total time: 0.030000s
No solution found.
Peak memory: 25000000 KB
        1.50 real         1.20 user         0.15 sys
"""
        stderr = ""
        
        stats = SolverStatsExtractor.extract_stats('fast-downward', stdout, stderr, duration=1.50)
        
        assert stats['expanded_states'] == 1000
        assert stats['evaluated_states'] == 1500
        assert stats['generated_states'] == 2000
        assert stats['peak_memory_kb'] == 25000000
        assert stats['search_time'] == 0.025000
        assert stats['total_time'] == 0.030000
        assert stats['solution_found'] is False
        assert stats['real_time'] == 1.50
    
    def test_fast_downward_translator_stats(self):
        """Test extracting translator statistics."""
        stdout = """
Translator variables: 62
Translator derived variables: 29
Translator facts: 124
Translator operators: 59
Translator axioms: 125
[t=0.027425s, 34136572 KB] Solution found!
[t=0.027425s, 34136572 KB] Plan length: 5 step(s).
"""
        stderr = ""
        
        stats = SolverStatsExtractor.extract_fast_downward_stats(stdout, stderr)
        
        assert stats['translator_variables'] == 62
        assert stats['translator_operators'] == 59
        assert stats['plan_length'] == 5
        assert stats['solution_found'] is True
    
    def test_fast_downward_minimal_output(self):
        """Test handling minimal Fast Downward output."""
        stdout = "Solution found."
        stderr = ""
        
        stats = SolverStatsExtractor.extract_fast_downward_stats(stdout, stderr)
        
        assert stats['solution_found'] is True


class TestConjureStatsExtractor:
    """Test statistics extraction from Conjure output."""
    
    def test_conjure_solution_found(self):
        """Test extracting stats when Conjure finds solution."""
        stdout = """
Conjure version 2.5.0
SATISFIABLE
Solution found.
        2.15 real         1.95 user         0.12 sys
"""
        stderr = ""
        
        stats = SolverStatsExtractor.extract_stats('conjure', stdout, stderr, duration=2.15)
        
        assert stats['solution_found'] is True
        assert stats['real_time'] == 2.15
    
    def test_conjure_no_solution(self):
        """Test extracting stats when Conjure finds no solution."""
        stdout = """
Conjure version 2.5.0
UNSATISFIABLE
No solution found.
        1.50 real         1.20 user         0.08 sys
"""
        stderr = ""
        
        stats = SolverStatsExtractor.extract_stats('conjure', stdout, stderr, duration=1.50)
        
        assert stats['solution_found'] is False
        assert stats['real_time'] == 1.50


class TestUPFStatsExtractor:
    """Test statistics extraction from UPF output."""
    
    def test_upf_solution_found(self):
        """Test extracting stats when UPF finds solution."""
        stdout = """
UPF Solver running...
SOLVED
Solution found.
Plan length: 8
        3.25 real         2.85 user         0.20 sys
"""
        stderr = ""
        
        stats = SolverStatsExtractor.extract_stats('upf', stdout, stderr, duration=3.25)
        
        assert stats['solution_found'] is True
        assert stats['plan_length'] == 8
        assert stats['real_time'] == 3.25
    
    def test_upf_no_solution(self):
        """Test extracting stats when UPF finds no solution."""
        stdout = """
UPF Solver running...
UNSOLVABLE
No solution found.
        1.75 real         1.45 user         0.10 sys
"""
        stderr = ""
        
        stats = SolverStatsExtractor.extract_stats('upf', stdout, stderr, duration=1.75)

        assert stats['solution_found'] is False
        assert stats['real_time'] == 1.75

    def test_upfplan_build_route_plan_found(self, wired_stdout_score):
        """UPF CLI constructed-build output: Steps: + Score: summary
        lines (a found plan) set solution_found, plan_length and the score."""
        stdout = (
            "action_score: 1\n"
            "action_score: 0\n"
            "Steps: 6\n"
            "Actions: [move_block(1, 2, -1), match_blocks]\n"
            "Score: 1\n"
        )
        stats = SolverStatsExtractor.extract_upf_stats(stdout, "")
        assert stats['solution_found'] is True
        assert stats['plan_length'] == 6
        assert stats['score'] == 1 * MULT

    def test_upfplan_domain_route_plan_found(self):
        """UPF CLI PDDL-domain output: (action ...) lines + Steps:, no
        Score line - solution_found and plan_length set, score absent."""
        stdout = (
            "(move_block loc_2_5 loc_3_5 right pink)\n"
            "(fall_block loc_3_5 loc_3_4 pink)\n"
            "Steps: 2\n"
        )
        stats = SolverStatsExtractor.extract_upf_stats(stdout, "")
        assert stats['solution_found'] is True
        assert stats['plan_length'] == 2
        assert 'score' not in stats

    def test_upfplan_no_plan(self):
        """A no-plan run prints "No plan found ..." to stderr with empty stdout;
        solution_found must be False (not left unset)."""
        stats = SolverStatsExtractor.extract_upf_stats(
            "", "No plan found (status: UNSOLVABLE_PROVEN)")
        assert stats['solution_found'] is False

    def test_upfplan_action_score_not_read_as_total_score(self):
        """per-action 'action_score:' lines must not be mistaken for the plan's
        total 'Score:' (the score anchor requires the line to start with Score:)."""
        stdout = "action_score: 5\nSteps: 1\n"
        stats = SolverStatsExtractor.extract_upf_stats(stdout, "")
        assert stats['solution_found'] is True
        assert 'score' not in stats


class TestGenericStatsExtractor:
    """Test generic statistics extraction."""
    
    def test_generic_with_timing(self):
        """Test generic extraction with timing information."""
        stdout = """
Some solver output
        1.23 real         1.00 user         0.15 sys
"""
        stderr = ""
        
        stats = SolverStatsExtractor.extract_stats('unknown', stdout, stderr, duration=1.23)
        
        assert stats['real_time'] == 1.23
    
    def test_generic_solution_indicators(self):
        """Test generic solution detection."""
        test_cases = [
            ("Solution found by custom solver", True),
            ("SATISFIABLE result obtained", True),
            ("Problem solved successfully", True),
            ("Created solution file output.sol", True),
            ("Plan found with 5 steps", True),
            ("No solution exists for this problem", False),
            ("UNSATISFIABLE problem detected", False),
            ("Problem is unsolvable", False),
            ("Infeasible constraint set", False),
        ]
        
        for stdout, expected in test_cases:
            stats = SolverStatsExtractor.extract_generic_stats(stdout, "")
            assert stats.get('solution_found') == expected, f"Failed for: {stdout}"
    
    def test_generic_stderr_indicators(self):
        """Test generic solution detection in stderr."""
        stdout = "Running solver..."
        stderr = "Solution found in stderr"
        
        stats = SolverStatsExtractor.extract_generic_stats(stdout, stderr)
        assert stats['solution_found'] is True
    
    def test_generic_no_indicators(self):
        """Test generic extraction with no clear indicators."""
        stdout = "Solver running... processing... done."
        stderr = ""
        
        stats = SolverStatsExtractor.extract_generic_stats(stdout, stderr)
        
        # Should not have solution_found key if no indicators found
        assert 'solution_found' not in stats


class TestSolverStatsExtractor:
    """Test the main SolverStatsExtractor.extract_stats method."""
    
    def test_extract_stats_savilerow(self):
        """Test solver detection and extraction for Savile Row."""
        stdout = """
Created solution file test-01.param.solution
        1.25 real         1.10 user         0.08 sys
"""
        stderr = ""
        
        # Test various Savile Row solver names
        for solver in ['savilerow-native', 'SavileRow', 'SAVILEROW']:
            stats = SolverStatsExtractor.extract_stats(solver, stdout, stderr, duration=1.25)
            assert stats['solution_found'] is True
            assert stats['final_horizon'] == 1
            assert 'real_time' in stats
    
    def test_extract_stats_fast_downward(self):
        """Test solver detection and extraction for Fast Downward."""
        stdout = """
Solution found.
Plan length: 5 step(s).
Expanded 100 state(s).
        0.50 real         0.45 user         0.02 sys
"""
        stderr = ""
        
        # Test various Fast Downward solver names
        for solver in ['fd', 'fast-downward', 'downward', 'Fast-Downward']:
            stats = SolverStatsExtractor.extract_stats(solver, stdout, stderr)
            assert stats['solution_found'] is True
            assert stats['plan_length'] == 5
            assert stats['expanded_states'] == 100
    
    def test_extract_stats_conjure(self):
        """Test solver detection and extraction for Conjure."""
        stdout = """
SATISFIABLE
        1.50 real         1.20 user         0.08 sys
"""
        stderr = ""
        
        # Test various Conjure solver names
        for solver in ['conjure', 'Conjure', 'essence']:
            stats = SolverStatsExtractor.extract_stats(solver, stdout, stderr, duration=1.50)
            assert stats['solution_found'] is True
            assert 'real_time' in stats
    
    def test_extract_stats_upf(self):
        """Test solver detection and extraction for UPF."""
        stdout = """
SOLVED
Plan length: 3
        2.00 real         1.80 user         0.10 sys
"""
        stderr = ""
        
        # Test various UPF solver names
        for solver in ['upf', 'unified-planning', 'UPF']:
            stats = SolverStatsExtractor.extract_stats(solver, stdout, stderr)
            assert stats['solution_found'] is True
            assert stats['plan_length'] == 3

    def test_extract_stats_upfplan_end_to_end(self, wired_stdout_score):
        """The upffd command detects as 'upfplan' and routes to the UPF extractor
        (not fast-downward), so a solved plan sets solution_found and score - the
        end-to-end guard for the misrouted-stats bug."""
        command = ['my-upfplan', '--build', 'upf', '-s', 'fast-downward', 'x.upf']
        stdout = "Steps: 6\nScore: 17\n"
        solver = detect_solver_from_command(command)
        assert solver == 'upfplan'
        stats = SolverStatsExtractor.extract_stats(solver, stdout, "", duration=1.0)
        assert stats['solution_found'] is True
        assert stats['plan_length'] == 6
        assert stats['score'] == 17 * MULT

    def test_extract_stats_unknown_solver(self):
        """Test fallback to generic extraction for unknown solvers."""
        stdout = """
Custom solver result: optimal solution found
"""
        stderr = ""
        
        stats = SolverStatsExtractor.extract_stats('unknown-solver', stdout, stderr, duration=1.75)
        
        # Should use generic extraction
        assert stats['solution_found'] is True
        assert stats['real_time'] == 1.75
    
    def test_extract_stats_empty_solver(self):
        """Test handling empty solver name."""
        stdout = "solution found"
        stderr = ""
        
        stats = SolverStatsExtractor.extract_stats('', stdout, stderr, duration=1.00)
        
        # Should use generic extraction
        assert stats['solution_found'] is True
        assert stats['real_time'] == 1.00


class TestDetectSolverFromCommand:
    """Test solver detection from command line."""
    
    def test_detect_savilerow(self):
        """Test detecting Savile Row from command."""
        commands = [
            ['savilerow-native', '-run-solver', 'model.eprime'],
            ['java', '-jar', 'savilerow.jar', 'model.eprime'],
            ['/usr/local/bin/savilerow', 'model.eprime']
        ]
        
        for cmd in commands:
            solver = detect_solver_from_command(cmd)
            assert solver == 'savilerow'
    
    def test_detect_fast_downward(self):
        """Test detecting Fast Downward from command."""
        commands = [
            ['fd', 'domain.pddl', 'problem.pddl'],
            ['fast-downward', '--search', 'astar(blind())'],
            ['/usr/bin/downward', '--help']
        ]
        
        for cmd in commands:
            solver = detect_solver_from_command(cmd)
            assert solver == 'fast-downward'
    
    def test_detect_conjure(self):
        """Test detecting Conjure from command."""
        commands = [
            ['conjure', 'solve', 'model.essence'],
            ['/usr/local/bin/conjure', '--help']
        ]
        
        for cmd in commands:
            solver = detect_solver_from_command(cmd)
            assert solver == 'conjure'
    
    def test_detect_upf(self):
        """Test detecting UPF from command."""
        commands = [
            ['python3', 'upf_solver.py', 'problem.pddl'],
            ['upf-planner', 'domain.pddl', 'problem.pddl']
        ]
        
        for cmd in commands:
            solver = detect_solver_from_command(cmd)
            assert solver == 'upf'
    
    def test_detect_upfplan(self):
        """A consumer's UPF CLI is detected as its own 'upfplan' type, NOT as
        fast-downward - even though its command embeds '-s fast-downward' (or an
        engine name). Regression: that misroute sent a solved upffd run to the
        fast-downward extractor, which does not understand upfplan's plan format,
        so it reported solution_found=False / score=None despite a real plan."""
        commands = [
            ['my-upfplan', '--build', 'upf', '-s', 'fast-downward', 'x.upf'],
            ['my-upfplan', '--domain', 'd.pddl', '--engine', 'enhsp', 'p.upf'],
        ]
        for cmd in commands:
            assert detect_solver_from_command(cmd) == 'upfplan'

    def test_detect_unknown_solver(self):
        """Test detecting unknown solver from command."""
        commands = [
            ['custom-solver', 'input.txt'],
            ['python', 'my_solver.py'],
            ['./unknown_binary']
        ]
        
        expected = ['custom-solver', 'python', './unknown_binary']
        
        for cmd, exp in zip(commands, expected):
            solver = detect_solver_from_command(cmd)
            assert solver == exp
    
    def test_detect_empty_command(self):
        """Test detecting solver from empty command."""
        solver = detect_solver_from_command([])
        assert solver == 'unknown'
    
    def test_detect_case_insensitive(self):
        """Test case insensitive detection."""
        commands = [
            ['SAVILEROW', 'model.eprime'],
            ['FD', 'domain.pddl', 'problem.pddl'],
            ['CONJURE', 'model.essence']
        ]
        
        expected = ['savilerow', 'fast-downward', 'conjure']
        
        for cmd, exp in zip(commands, expected):
            solver = detect_solver_from_command(cmd)
            assert solver == exp


class TestStatsExtractorEdgeCases:
    """Test edge cases and error handling for statistics extractors."""
    
    def test_empty_output(self):
        """Test handling empty stdout and stderr."""
        for method in [
            SolverStatsExtractor.extract_savilerow_stats,
            SolverStatsExtractor.extract_fast_downward_stats,
            SolverStatsExtractor.extract_conjure_stats,
            SolverStatsExtractor.extract_upf_stats,
            SolverStatsExtractor.extract_generic_stats
        ]:
            stats = method("", "")
            assert isinstance(stats, dict)
    
    def test_malformed_timing(self):
        """Test handling malformed timing information."""
        stdout = """
        invalid real         bad user         wrong sys
        1.25 real
        user 0.50 sys 0.05
"""
        stderr = ""
        
        stats = SolverStatsExtractor.extract_generic_stats(stdout, stderr)
        
        # Should not have timing keys if parsing fails
        assert 'real_time' not in stats
        assert 'user_time' not in stats
        assert 'sys_time' not in stats
    
    def test_multiple_timing_entries(self):
        """Test handling multiple timing entries (should use last one)."""
        stdout = """
First command:
        1.00 real         0.90 user         0.05 sys
Second command:
        2.50 real         2.30 user         0.10 sys
"""
        stderr = ""
        
        stats = SolverStatsExtractor.extract_stats('savilerow', stdout, stderr, duration=2.50)
        
        # Should use the internal duration
        assert stats['real_time'] == 2.50
    
    def test_very_large_numbers(self):
        """Test handling very large numbers in output."""
        stdout = """
[t=0.027425s, 999999999 KB] Expanded 9999999999 state(s).
[t=0.027425s, 999999999 KB] Generated 9999999999 state(s).
Plan length: 99999 step(s).
Peak memory: 999999999 KB
"""
        stderr = ""
        
        stats = SolverStatsExtractor.extract_fast_downward_stats(stdout, stderr)
        
        assert stats['expanded_states'] == 9999999999
        assert stats['generated_states'] == 9999999999
        assert stats['plan_length'] == 99999
        assert stats['peak_memory_kb'] == 999999999