"""Objective extraction injection point for the generic solver core.

The core records whatever objective value a solver produces, but it has no notion
of a particular domain's objective -- how it is encoded in a solution file or
solver output, nor what units it is reported in. A consumer registers extractors
here, once at setup; each returns the value the core should record (already in the
consumer's reported units) or None. Until a consumer registers one, every route
yields None, so the standalone core simply records no score.

For example, Puzznic wires these from ``puzznic_support/__init__`` via
``puzznic_scoring.wire_into_core()``, so every entry point and test that imports
the package configures the core uniformly; another consumer of the extracted core
would register its own.
"""

from typing import Callable, Optional


# --- reported-objective extractors (registered by a consumer) ---
#
# The core asks for the *reported* objective of a produced solution at two points
# but does not know how a domain encodes it: it may live in a solution file, or be
# printed on solver stdout. A consumer registers an extractor for whichever routes
# it uses; an unregistered route yields None, so the standalone core simply
# records no score.

_reported_score_from_solution: Optional[Callable[[str], Optional[int]]] = None
_reported_score_from_stdout: Optional[Callable[[str], Optional[int]]] = None


def set_reported_score_from_solution(fn: Callable[[str], Optional[int]]) -> None:
    """Register ``fn(solution_file) -> Optional[int]`` for the file-based route."""
    global _reported_score_from_solution
    _reported_score_from_solution = fn


def set_reported_score_from_stdout(fn: Callable[[str], Optional[int]]) -> None:
    """Register ``fn(stdout) -> Optional[int]`` for the stdout-based route."""
    global _reported_score_from_stdout
    _reported_score_from_stdout = fn


def reported_score_from_solution(solution_file: str) -> Optional[int]:
    """The reported objective of a solution file, via the registered extractor."""
    if _reported_score_from_solution is None:
        return None
    return _reported_score_from_solution(solution_file)


def reported_score_from_stdout(stdout: str) -> Optional[int]:
    """The reported objective from solver stdout, via the registered extractor."""
    if _reported_score_from_stdout is None:
        return None
    return _reported_score_from_stdout(stdout)


# A salvaged timeout solution gets its own extractor: it may need a parser that
# does not rely on the normal (Conjure) solution loader, which can fail on a
# solution the solver was killed mid-write of.
_reported_score_from_timeout_solution: Optional[Callable[[str], Optional[int]]] = None


def set_reported_score_from_timeout_solution(fn: Callable[[str], Optional[int]]) -> None:
    """Register ``fn(solution_file) -> Optional[int]`` for a salvaged timeout solution."""
    global _reported_score_from_timeout_solution
    _reported_score_from_timeout_solution = fn


def reported_score_from_timeout_solution(solution_file: str) -> Optional[int]:
    """The reported objective of a salvaged timeout solution, via the extractor."""
    if _reported_score_from_timeout_solution is None:
        return None
    return _reported_score_from_timeout_solution(solution_file)


# The raw search objective is separate from the reported-score extractors above:
# it is the value the horizon scan compares between horizons, so it stays in model
# units and is never scaled for display.
_raw_objective_from_solution: Optional[Callable[[str], Optional[int]]] = None


def set_raw_objective_from_solution(fn: Callable[[str], Optional[int]]) -> None:
    """Register ``fn(solution_file) -> Optional[int]`` for the raw search objective."""
    global _raw_objective_from_solution
    _raw_objective_from_solution = fn


def raw_objective_from_solution(solution_file: str) -> Optional[int]:
    """The raw (unscaled) search objective of a solution file, via the extractor."""
    if _raw_objective_from_solution is None:
        return None
    return _raw_objective_from_solution(solution_file)
