"""
Solver statistics extractors for parsing solver output and extracting metrics.

This module provides functions to extract structured statistics from solver output,
including timing information, search statistics, and solver-specific metrics.
"""

import re
from typing import Dict, Any, Optional, List
from pathlib import Path
from .objective import reported_score_from_solution, reported_score_from_stdout


# Fast Downward stdout metrics that are a plain "search -> capture group 1 -> cast":
# stats-key -> (regex, caster). Metrics with extra side effects (plan_length also
# sets solution_found; the solution_found text fallback) stay explicit in
# extract_fast_downward_stats rather than living in this table.
_FD_STAT_PATTERNS = {
    'expanded_states':      (r'Expanded (\d+) state\(s\)', int),
    'evaluated_states':     (r'Evaluated (\d+) state\(s\)', int),
    'generated_states':     (r'Generated (\d+) state\(s\)', int),
    'reopened_states':      (r'Reopened (\d+) state\(s\)', int),
    'plan_cost':            (r'Plan cost: (\d+)', int),
    'peak_memory_kb':       (r'Peak memory: (\d+) KB', int),
    'search_time':          (r'Search time: (\d+\.\d+)s', float),
    'total_time':           (r'Total time: (\d+\.\d+)s', float),
    'planner_time':         (r'Planner time: (\d+\.\d+)s', float),
    'translator_variables': (r'Translator variables: (\d+)', int),
    'translator_operators': (r'Translator operators: (\d+)', int),
}


# Cost-layer progression lines from the Fast Downward / SymK search. Both sweep
# increasing cost bounds and print a timestamped line as each bound N is reached:
#   FD (A*):  "[t=0.0055s, 4353 KB] f = 3, 201 evaluated, 131 expanded"
#   SymK:     "[t=0.2445s, 4355 KB] BOUND: 3 < 2147483647 [0/1 plans], dir: FW, ..."
# The final layer ends at the search-end line, "Solution found!" (FD) or
# "Actual search time:" (SymK).
_COST_LAYER_RE = re.compile(
    r'\[t=([\d.]+)s,[^\]]*\]\s*(?:f = (\d+),|BOUND: (\d+) <)')
_SEARCH_END_RE = re.compile(
    r'\[t=([\d.]+)s,[^\]]*\]\s*(?:Solution found!|Actual search time:)')
# Any progress timestamp, used to bound the last (incomplete) layer of a search
# that was cut off before printing a clean end line (a timeout / external kill).
_ANY_TIMESTAMP_RE = re.compile(r'\[t=([\d.]+)s,')
# A plan was actually found (so the deepest bound reached carries the solution);
# absent on a timeout, and on a completed-but-unsolved search.
_SOLUTION_FOUND_RE = re.compile(r'Solution found|Plan length: \d+ step|Best plan:')
# Fast Downward / SymK translator (grounding) wall-clock: "Done! [0.16s CPU,
# 0.143s wall-clock]". The bracketed form is the translator summary; the plain
# "... done!" progress lines don't match.
_TRANSLATE_TIME_RE = re.compile(r'Done! \[[\d.]+s CPU, ([\d.]+)s wall-clock\]')
# UPF's own model-transform (compilation) time, printed by puzznic-upfplan before
# the engine runs - the analogue of Savile Row tailoring / Conjure refinement.
_UP_COMPILE_TIME_RE = re.compile(r'UP compilation time: ([\d.]+)s')

# ENHSP prints its timing/search summary in a different shape from Fast Downward's.
# Aggregate times are in MILLISECONDS (note "Grounding Time:" alone carries no
# "(msec)" suffix); ``grounding_time`` is ENHSP's lifted-to-grounded step, the
# preprocessing analogue of FD's translate time and the dominant cost on hard
# instances. ``H1 Setup Time`` appears only for heuristic configs - a blind search
# omits it. All folded into seconds on capture.
_ENHSP_SEARCH_MS_RE = re.compile(r'Search Time \(msec\):\s*(\d+)')
_ENHSP_TIME_PATTERNS = {
    'grounding_time': re.compile(r'Grounding Time:\s*(\d+)'),
    'h1_setup_time':  re.compile(r'H1 Setup Time \(msec\):\s*(\d+)'),
    'search_time':    _ENHSP_SEARCH_MS_RE,
    'heuristic_time': re.compile(r'Heuristic Time \(msec\):\s*(\d+)'),
    'planning_time':  re.compile(r'Planning Time \(msec\):\s*(\d+)'),
}
# The final summary totals (line-anchored so they don't match the per-progress-line
# "(Expanded Nodes: N, Evaluated States: N" that appears mid-f(n)-line).
_ENHSP_COUNT_PATTERNS = {
    'expanded_states':  re.compile(r'^Expanded Nodes:\s*(\d+)', re.MULTILINE),
    'evaluated_states': re.compile(r'^States Evaluated:\s*(\d+)', re.MULTILINE),
}
# One optimising-search cost-bound line, e.g.
#   "f(n) = 4.0 (Expanded Nodes: 426, Evaluated States: 771, Time: 0.118) Frontier Size: 345"
# the f-value (an integer cost printed as a float) and its search-relative
# timestamp in seconds - the ENHSP analogue of FD's "[t=Ts] f = N". A satisficing
# search instead prints " g(n)= G h(n)= H" milestones with NO timestamps, which
# this deliberately does not match (so satisficing runs yield no cost-layers).
_ENHSP_LAYER_RE = re.compile(r'f\(n\)\s*=\s*([\d.]+)\s*\([^)]*Time:\s*([\d.]+)\)')
# A plan was found (the deepest bound carries the solution); ENHSP prints both on
# success, neither on a timeout / unsolvable.
_ENHSP_SOLVED_RE = re.compile(r'Problem Solved|Found Plan:')


class SolverStatsExtractor:
    """Extract structured statistics from solver output."""

    @staticmethod
    def extract_search_cost_layers(stdout: str,
                                   end_hint: Optional[float] = None) -> Dict[str, Any]:
        """Recover the per-cost-layer progression of an A* / symbolic search.

        Fast Downward (A*) and SymK (symbolic uniform-cost) sweep increasing
        cost bounds, printing a timestamped line as each bound N is reached.
        Bounds ``1..plan_cost`` are the search's own notion of "horizon" - the
        analogue of a Savile Row horizon scan - so recover them as a per-layer
        timing curve. Timestamps are relative to the search process, so the
        parts partition its wall-clock exactly: ``search_setup_time`` (before
        bound 1) plus the layer durations sum to the search-end time.

        A search cut off before printing a clean end line (a timeout / external
        kill) still yields its layers: the final, incomplete layer is bounded by
        the latest progress timestamp seen - or by ``end_hint`` (a search-relative
        kill time, typically the phase duration minus the translate time) when the
        caller supplies it, since the search keeps working the deepest bound after
        its last printed line. No bound is marked as bearing a solution.

        Returns (keys omitted when the solver prints no cost-layer lines at all):
          - ``cost_layers``: list of ``(N, duration, found_solution)`` for
            N >= 1, duration the wall-clock spent at bound N (gap to the next
            bound, or to the search end for the last), found_solution marking
            the final, solution-bearing bound (never set on a timed-out run).
          - ``search_setup_time``: wall-clock before bound 1 is reached (reading
            the task, building successor generators / symbolic machinery) - the
            search-side preprocessing that dominates symbolic search on large
            tasks.
        """
        by_n = {}  # bound N -> timestamp (last occurrence wins)
        for m in _COST_LAYER_RE.finditer(stdout):
            t = float(m.group(1))
            n = int(m.group(2) if m.group(2) is not None else m.group(3))
            by_n[n] = t
        if not by_n:
            return {}

        # Bound the final layer. A clean end line (Solution found! / Actual
        # search time:) means the search finished; without one it was cut off
        # (timeout / external kill), so fall back to the latest progress
        # timestamp seen - the last layer is then incomplete, and no bound
        # carries a solution.
        end_match = _SEARCH_END_RE.search(stdout)
        if end_match is not None:
            end_t = float(end_match.group(1))
        else:
            # Timed out: the last progress timestamp, extended to the caller's
            # search-relative kill time (end_hint) when available - the search
            # keeps working the deepest bound after its last printed line, so
            # without this the final, blown-up layer collapses to ~0.
            last_seen = max(float(t) for t in _ANY_TIMESTAMP_RE.findall(stdout))
            end_t = max(last_seen, end_hint) if end_hint is not None else last_seen
        solved = _SOLUTION_FOUND_RE.search(stdout) is not None

        return SolverStatsExtractor._assemble_cost_layers(by_n, end_t, solved)

    @staticmethod
    def _assemble_cost_layers(by_n: Dict[int, float], end_t: float,
                              solved: bool) -> Dict[str, Any]:
        """Build ``cost_layers`` + ``search_setup_time`` from a ``{bound: timestamp}`` map.

        Shared by the Fast Downward / SymK (``[t=Ts] f = N``) and ENHSP
        (``f(n) = N (... Time: T)``) parsers, which differ only in how they read
        the per-bound timestamps and the search-end time; the layering is
        identical, so it lives here:

          - ``search_setup_time`` is the timestamp of the first tracked bound
            (>= 1) - reaching it ends the search-side setup (task load,
            successor/symbolic machinery, the trivial bound-0 layer) and starts
            the first real horizon. Bound 1 for FD A* / SymK / ENHSP on a moves
            metric; the first compiled-model cost bound for the UPF score models,
            whose bounds do not start at 1.
          - each layer's duration is the gap to the next bound (or to ``end_t``
            for the deepest), and only the deepest bound of a solved search bears
            the solution (a timed-out run's deepest bound was reached but not
            cleared). ``by_n`` empty -> ``{}``.
        """
        if not by_n:
            return {}
        ordered = sorted(by_n.items())  # [(N, T), ...] by N
        max_n = ordered[-1][0]

        result: Dict[str, Any] = {}
        real_bounds = [n for n in by_n if n >= 1]
        if real_bounds:
            result['search_setup_time'] = by_n[min(real_bounds)]

        cost_layers = []
        for idx, (n, t) in enumerate(ordered):
            if n < 1:
                continue
            next_t = ordered[idx + 1][1] if idx + 1 < len(ordered) else end_t
            cost_layers.append((n, max(0.0, next_t - t), solved and n == max_n))
        if cost_layers:
            result['cost_layers'] = cost_layers
        return result

    @staticmethod
    def extract_enhsp_search_layers(text: str,
                                    end_hint: Optional[float] = None) -> Dict[str, Any]:
        """Recover ENHSP's optimising-search cost-bound progression as cost_layers.

        Under an optimising search (opt-blind / opt-hrmax, direct or via UPF)
        ENHSP prints ``f(n) = N (... Time: T) Frontier Size: F`` as each new
        f-bound N is reached, T the search-relative time in seconds - the same
        shape as Fast Downward's cost-layers, fed through the shared assembler.
        The sweep ends at ENHSP's reported ``Search Time (msec)`` (the exact
        post-grounding search wall-clock, tighter than the last printed f(n)
        timestamp); on a timeout that line is absent, so fall back to the last
        f(n) timestamp, extended to ``end_hint`` (the phase duration less
        grounding) when supplied - exactly as the FD parser bounds a cut-off
        final layer. Returns ``{}`` for a satisficing run, whose ``g(n)=``
        milestones carry no timestamps and so no layers.
        """
        by_n: Dict[int, float] = {}
        for m in _ENHSP_LAYER_RE.finditer(text):
            # f is an integer cost printed as a float ("4.0"); last occurrence
            # of a bound wins, matching the FD parser.
            by_n[int(float(m.group(1)))] = float(m.group(2))
        if not by_n:
            return {}

        end_match = _ENHSP_SEARCH_MS_RE.search(text)
        if end_match is not None:
            end_t = int(end_match.group(1)) / 1000.0
        else:
            last_seen = max(by_n.values())
            end_t = max(last_seen, end_hint) if end_hint is not None else last_seen
        solved = _ENHSP_SOLVED_RE.search(text) is not None

        return SolverStatsExtractor._assemble_cost_layers(by_n, end_t, solved)

    @staticmethod
    def _extract_enhsp_engine_stats(text: str,
                                    duration: Optional[float] = None) -> Dict[str, Any]:
        """ENHSP engine numbers shared by the direct and via-UPF routes.

        Parses ENHSP's aggregate times (milliseconds -> seconds), the final
        expanded/evaluated counts, and the optimising cost-layers. Route-specific
        outcome fields (``solution_found`` / ``plan_length`` / ``score``) are left
        to the callers, which read them differently: the direct route from ENHSP's
        own ``Problem Solved`` / ``Plan-Length``, the UPF route from puzznic-upfplan's
        ``Steps:`` / ``Score:``. Safe to call on a Fast Downward / SymK engine log
        too - none of ENHSP's markers appear there, so it returns ``{}``.
        """
        stats: Dict[str, Any] = {}
        for key, pat in _ENHSP_TIME_PATTERNS.items():
            m = pat.search(text)
            if m:
                stats[key] = int(m.group(1)) / 1000.0
        for key, pat in _ENHSP_COUNT_PATTERNS.items():
            m = pat.search(text)
            if m:
                stats[key] = int(m.group(1))

        # A timed-out optimising run has no "Search Time" line, so bound the final
        # cost-layer at the search-relative kill time: phase duration less
        # grounding (grounding runs before ENHSP's search clock starts), mirroring
        # how the FD extractor derives end_hint = duration - translate_time.
        end_hint = None
        if duration is not None:
            end_hint = duration - stats.get('grounding_time', 0.0)
        stats.update(SolverStatsExtractor.extract_enhsp_search_layers(
            text, end_hint=end_hint))
        return stats

    @staticmethod
    def extract_enhsp_stats(stdout: str, stderr: str,
                            duration: Optional[float] = None) -> Dict[str, Any]:
        """Extract statistics from a direct ENHSP run (the ``enhsp`` solver).

        ENHSP writes its progress/summary to stdout (stderr folded in defensively).
        On top of the shared engine numbers this reads ENHSP's own outcome: it
        prints ``Problem Solved`` / ``Found Plan:`` on success and a
        ``Problem Detected as Unsolvable`` line on failure; ``Plan-Length:`` and
        ``Metric (Search):`` give the plan length and (optimised) cost.

        Args:
            stdout: Standard output from ENHSP execution.
            stderr: Standard error from ENHSP execution.
            duration: wall-clock of the solving phase, used only to bound a
                timed-out final cost-layer (see ``_extract_enhsp_engine_stats``).
        """
        text = f"{stdout}\n{stderr}"
        stats = SolverStatsExtractor._extract_enhsp_engine_stats(text, duration=duration)

        if _ENHSP_SOLVED_RE.search(text):
            stats['solution_found'] = True
        elif 'nsolvable' in text or 'No plan' in text:
            stats['solution_found'] = False

        plan_length_match = re.search(r'Plan-Length:\s*(\d+)', text)
        if plan_length_match:
            stats['plan_length'] = int(plan_length_match.group(1))
        metric_match = re.search(r'Metric \(Search\):\s*([\d.]+)', text)
        if metric_match:
            stats['plan_cost'] = float(metric_match.group(1))
        return stats

    @staticmethod
    def extract_savilerow_stats(stdout: str, stderr: str, horizon_number: Optional[int] = None) -> Dict[str, Any]:
        """
        Extract statistics from Savile Row output.
        
        Args:
            stdout: Standard output from Savile Row execution
            stderr: Standard error from Savile Row execution
            horizon_number: Horizon number if called during horizon scan (optional)
            
        Returns:
            Dictionary containing extracted statistics
        """
        stats = {}

        # Extract final horizon number from solution file creation
        solution_match = re.search(r'Created solution file.*-(\d+)\.(?:eprime-)?param\.solution', stdout)
        if solution_match:
            stats['final_horizon'] = int(solution_match.group(1))
            stats['solution_found'] = True
        else:
            stats['solution_found'] = False
            
        # Outside horizon-scan mode, ask the consumer for the produced solution's
        # reported objective. During a scan (horizon_number set) this is skipped:
        # the continue-scan decision reads objective values from .obj files, so
        # parsing every horizon's solution here would be needlessly expensive.
        solution_file_match = re.search(r'Created solution file (.+\.param\.solution)', stdout)
        if solution_file_match and horizon_number is None:
            reported = reported_score_from_solution(solution_file_match.group(1))
            if reported is not None:
                stats['score'] = reported

        # Extract SAT solver information
        if 'Created output SAT file' in stdout:
            stats['used_sat_solver'] = True
            
        # Look for warnings about intervals
        interval_warning_count = stdout.count('WARNING: interval')
        if interval_warning_count > 0:
            stats['interval_warnings'] = interval_warning_count
        
        # Count all warnings from both stdout and stderr
        total_warnings = stdout.count('WARNING:') + stderr.count('WARNING:')
        if total_warnings > 0:
            stats['warning_count'] = total_warnings
            
        # Check for timeout or other termination reasons
        if 'timeout' in stdout.lower() or 'timeout' in stderr.lower():
            stats['timed_out'] = True
            
        return stats
    
    @staticmethod
    def extract_savilerow_info_times(info_path) -> Dict[str, Any]:
        """Parse a Savile Row ``.info`` stats file for the tailoring/solver split.

        Savile Row writes ``<param>.info`` alongside each run: labelled
        ``Key:Value`` lines that include ``SavileRowTotalTime`` (time spent
        tailoring the model - flattening, CSE, SAT encoding) and
        ``SolverTotalTime`` (time the backend solver itself ran). The phase
        ``duration`` we already record is the wall-clock total (these two plus
        runsolver/process overhead), and the split between them is neither fixed
        nor predictable from that total, so recover the two components here to
        report and plot them separately.

        Returns a dict with ``savilerow_time`` and/or ``solver_time`` (floats),
        omitting either key that is absent or unparseable. Returns ``{}`` when
        the file does not exist - a hard timeout can kill Savile Row before it
        writes the file, and older runs may have had it cleaned up.
        """
        info_path = Path(info_path)
        if not info_path.exists():
            return {}

        # Only these two labelled times are of interest; other lines
        # (SATClauses, SolverNodes, ...) are ignored.
        key_map = {
            'SavileRowTotalTime': 'savilerow_time',
            'SolverTotalTime': 'solver_time',
        }
        stats: Dict[str, Any] = {}
        try:
            text = info_path.read_text()
        except OSError:
            return {}
        for line in text.splitlines():
            key, sep, value = line.partition(':')
            if not sep:
                continue
            out_key = key_map.get(key.strip())
            if out_key is None:
                continue
            try:
                stats[out_key] = float(value.strip())
            except ValueError:
                continue
        return stats

    @staticmethod
    def extract_fast_downward_stats(stdout: str, stderr: str,
                                    duration: Optional[float] = None) -> Dict[str, Any]:
        """
        Extract statistics from Fast Downward output.

        Args:
            stdout: Standard output from Fast Downward execution
            stderr: Standard error from Fast Downward execution
            duration: wall-clock of the whole solving phase, if known. Used only
                to bound the final cost-layer of a timed-out search: the search
                timeline is relative to search start, so ``duration -
                translate_time`` approximates the kill time on that timeline.

        Returns:
            Dictionary containing extracted statistics
        """
        stats = {}

        # Search/timing/translator metrics: one "search -> group(1) -> cast" per
        # entry in _FD_STAT_PATTERNS (see that table for the regex/cast per key).
        for key, (pattern, cast) in _FD_STAT_PATTERNS.items():
            match = re.search(pattern, stdout)
            if match:
                stats[key] = cast(match.group(1))

        # Plan length is table-shaped but also marks a solution as found.
        plan_length_match = re.search(r'Plan length: (\d+) step\(s\)', stdout)
        if plan_length_match:
            stats['plan_length'] = int(plan_length_match.group(1))
            stats['solution_found'] = True

        # Translator (grounding) wall-clock - the coarse "prepare" cost that runs
        # before the search process and grows on large tasks.
        translate_match = _TRANSLATE_TIME_RE.search(stdout)
        if translate_match:
            stats['translate_time'] = float(translate_match.group(1))

        # Per-cost-layer search progression (f = N / BOUND: N) plus the
        # search-side setup time. Shared by FD (A*) and SymK (symbolic), whose
        # cost bounds are the search's own "horizons". On a timeout, the phase
        # duration less the translate time bounds the final layer's real cost.
        end_hint = None
        if duration is not None:
            end_hint = duration - stats.get('translate_time', 0.0)
        stats.update(SolverStatsExtractor.extract_search_cost_layers(
            stdout, end_hint=end_hint))

        # Check for solution found (fallback if regex didn't match)
        if not stats.get('solution_found', False):
            if 'Solution found' in stdout or 'Created solution file' in stdout:
                stats['solution_found'] = True
            elif 'No solution' in stdout:
                stats['solution_found'] = False
            
            
        return stats
    
    @staticmethod
    def extract_conjure_stats(stdout: str, stderr: str) -> Dict[str, Any]:
        """
        Extract statistics from Conjure/Essence solver output.
        
        Args:
            stdout: Standard output from Conjure execution
            stderr: Standard error from Conjure execution
            
        Returns:
            Dictionary containing extracted statistics
        """
        stats = {}
        
        # Look for solution indicators - check negative first
        if 'UNSATISFIABLE' in stdout or 'No solution' in stdout:
            stats['solution_found'] = False
        elif 'Solution found' in stdout or 'SATISFIABLE' in stdout or 'Created solution file' in stdout:
            stats['solution_found'] = True
            
            
        return stats
    
    @staticmethod
    def extract_upf_stats(stdout: str, stderr: str) -> Dict[str, Any]:
        """
        Extract statistics from UPF (Unified Planning Framework) output.

        The current UPF CLI (``puzznic-upfplan``) prints a found plan as
        ``(action args)`` / ``Actions: [...]`` lines followed by a ``Steps: N``
        summary line (and ``Score: N`` on the scored constructed-build route); a
        no-plan run prints ``No plan found ...`` to stderr and exits non-zero.
        Older/other UPF phrasings (``SOLVED``, ``Solution found``,
        ``Plan length: N``, ``UNSOLVABLE``) are still honoured as a fallback.

        The summary latches ``solution_found`` off this stat and takes ``score``
        from it (``RunSummary.add_phase`` / the score reconciliation), so a solved
        upfplan run must set them here - otherwise it is reported as
        ``solution_found=False`` / ``score=None`` despite a real plan.

        Args:
            stdout: Standard output from UPF execution
            stderr: Standard error from UPF execution

        Returns:
            Dictionary containing extracted statistics
        """
        stats = {}

        # A found plan is signalled by the "Steps: N" summary line (both the
        # PDDL-domain and constructed-build routes print it). No-plan runs say so
        # on stderr/stdout; fall back to the legacy UPF phrasings.
        steps_match = re.search(r'^\s*Steps:\s*(\d+)', stdout, re.MULTILINE)
        if steps_match:
            stats['plan_length'] = int(steps_match.group(1))
            stats['solution_found'] = True
        elif ('No plan found' in stdout or 'No plan found' in stderr
              or 'UNSOLVABLE' in stdout or 'No solution' in stdout):
            stats['solution_found'] = False
        elif 'Solution found' in stdout or 'SOLVED' in stdout:
            stats['solution_found'] = True

        # Legacy "Plan length: N" phrasing (older UPF output; keep if Steps: absent).
        if 'plan_length' not in stats:
            plan_length_match = re.search(r'Plan length: (\d+)', stdout)
            if plan_length_match:
                stats['plan_length'] = int(plan_length_match.group(1))

        # Ask the consumer for the produced solution's reported objective; the
        # constructed-build route prints it on stdout (as "Score: N").
        reported = reported_score_from_stdout(stdout)
        if reported is not None:
            stats['score'] = reported

        # UPF streams the underlying engine's search log (Fast Downward / SymK)
        # to stderr - kept off stdout so the plan stays clean for the .solution -
        # so recover the same cost-layer progression and translator time as the
        # direct fd/symk paths from there. This is what makes a UPF run
        # comparable (horizon curves + a preprocessing baseline). The
        # f = N / BOUND: N / Done! lines belong to the engine log and do not
        # appear in UPF's own plan/score output, so they cannot collide with the
        # parsing above. No end-hint is passed: the phase duration includes the
        # UP compilation, which is not on the engine's search timeline, so it
        # would over-extend a timed-out final layer.
        engine_log = f"{stdout}\n{stderr}"
        translate_match = _TRANSLATE_TIME_RE.search(engine_log)
        if translate_match:
            stats['translate_time'] = float(translate_match.group(1))
        # UPF's model compilation (arrays/integers/usertype removal) that precedes
        # the engine - recorded on stderr by puzznic-upfplan (constructed --build
        # route only; --domain reads PDDL directly and has none).
        compile_match = _UP_COMPILE_TIME_RE.search(engine_log)
        if compile_match:
            stats['up_compile_time'] = float(compile_match.group(1))
        stats.update(SolverStatsExtractor.extract_search_cost_layers(engine_log))

        # The UPF engine may be ENHSP rather than Fast Downward / SymK, whose
        # search log has a different shape (grounding time; an
        # "f(n) = N (... Time: T)" progression the FD regex does not match). Its
        # markers are absent from FD/SymK output, so merging ENHSP's engine
        # numbers is a no-op there and recovers the grounding baseline +
        # cost-layers when ENHSP is the engine. No duration/end-hint: the phase
        # wall-clock includes UP compilation, off the engine's search timeline -
        # the same reason the FD cost-layer call above passes none.
        stats.update(SolverStatsExtractor._extract_enhsp_engine_stats(engine_log))

        return stats
    
    @staticmethod
    def extract_generic_stats(stdout: str, stderr: str) -> Dict[str, Any]:
        """
        Extract generic statistics that work for any solver.
        
        Args:
            stdout: Standard output from solver execution
            stderr: Standard error from solver execution
            
        Returns:
            Dictionary containing extracted statistics
        """
        stats = {}
        
        
        # Generic solution indicators - check negative indicators first
        no_solution_indicators = [
            'no solution', 'unsatisfiable', 'unsolvable', 'infeasible'
        ]
        solution_indicators = [
            'solution found', 'satisfiable', 'solved', 'optimal',
            'created solution file', 'plan found'
        ]
        
        stdout_lower = stdout.lower()
        stderr_lower = stderr.lower()
        
        # Check no solution indicators first (more specific)
        if any(indicator in stdout_lower or indicator in stderr_lower
               for indicator in no_solution_indicators):
            stats['solution_found'] = False
        elif any(indicator in stdout_lower or indicator in stderr_lower
                 for indicator in solution_indicators):
            stats['solution_found'] = True
            
        return stats
    
    @staticmethod
    def extract_stats(solver: str, stdout: str, stderr: str, duration: Optional[float] = None, horizon_number: Optional[int] = None) -> Dict[str, Any]:
        """
        Extract statistics based on solver type.
        
        Args:
            solver: Name of the solver used
            stdout: Standard output from solver execution
            stderr: Standard error from solver execution
            duration: Internal timing in seconds (optional)
            horizon_number: The horizon number being attempted (optional)
            
        Returns:
            Dictionary containing extracted statistics
        """
        solver_lower = solver.lower()

        if 'savilerow' in solver_lower:
            stats = SolverStatsExtractor.extract_savilerow_stats(stdout, stderr, horizon_number)
        elif ('fd' in solver_lower or 'fast-downward' in solver_lower
              or 'downward' in solver_lower or 'symk' in solver_lower):
            # SymK shares Fast Downward's output format (incl. the cost-layer
            # progression), so it uses the same extractor. Pass the phase
            # duration so a timed-out search's final layer can be bounded.
            stats = SolverStatsExtractor.extract_fast_downward_stats(
                stdout, stderr, duration=duration)
        elif 'conjure' in solver_lower or 'essence' in solver_lower:
            stats = SolverStatsExtractor.extract_conjure_stats(stdout, stderr)
        elif 'upf' in solver_lower or 'unified-planning' in solver_lower:
            # upfenhsp detects as 'upfplan' and lands here; extract_upf_stats now
            # also recovers ENHSP's engine numbers, so this covers ENHSP-via-UPF.
            stats = SolverStatsExtractor.extract_upf_stats(stdout, stderr)
        elif 'enhsp' in solver_lower:
            # Direct ENHSP: its own stats shape (grounding time, f(n) cost-layers).
            stats = SolverStatsExtractor.extract_enhsp_stats(
                stdout, stderr, duration=duration)
        else:
            stats = SolverStatsExtractor.extract_generic_stats(stdout, stderr)
        
        # Add internal timing if provided
        if duration is not None:
            stats['real_time'] = duration
            
        # Add horizon number if provided
        if horizon_number is not None:
            stats['horizon_number'] = horizon_number
        
        return stats


def detect_solver_from_command(command: List[str]) -> str:
    """
    Detect solver type from command line.
    
    Args:
        command: Command line arguments
        
    Returns:
        Detected solver name
    """
    if not command:
        return "unknown"
        
    cmd_str = " ".join(command).lower()

    # puzznic-upfplan is the UPF CLI; it must be detected BEFORE the engine-name
    # checks below, because its command embeds the chosen engine (e.g.
    # "-s fast-downward") which would otherwise misroute it to the fast-downward
    # extractor - one that does not understand upfplan's own plan format, so a
    # solved run would report solution_found=False / score=None.
    if 'upfplan' in cmd_str:
        return 'upfplan'
    elif 'savilerow' in cmd_str:
        return 'savilerow'
    elif 'conjure' in cmd_str:
        return 'conjure'
    elif 'enhsp' in cmd_str:
        # Direct ENHSP has its own stats shape (grounding time, f(n) cost-layers),
        # not Fast Downward's - keep it off the fd branch below even if a path
        # token happened to contain 'fd'/'symk'. (upfenhsp is caught by the
        # 'upfplan' check above, so it never reaches here.)
        return 'enhsp'
    elif ('fd' in cmd_str or 'fast-downward' in cmd_str or 'downward' in cmd_str
          or 'symk' in cmd_str):
        # SymK IS Fast Downward (same driver and output format), so its stats -
        # including the BOUND: cost-layer progression - are parsed by the FD
        # extractor. Without this it fell through to the generic extractor and
        # got none of them.
        return 'fast-downward'
    elif 'upf' in cmd_str:
        return 'upf'
    else:
        return command[0]  # Use first command as solver name
