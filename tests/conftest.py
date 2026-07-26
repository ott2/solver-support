"""Pytest configuration and fixtures for the solver_support core tests.

The core ships **unwired** (see ``docs/injection-api.md``): with no consumer
imported it resolves no model registry and interprets no objective. Tests that
need either wire a stand-in here rather than depending on whichever consumer
happens to be installed beside the core.
"""

from pathlib import Path

import pytest

FIXTURE_CONFIG = Path(__file__).parent / "fixtures" / "models.yaml"


@pytest.fixture(autouse=True)
def reset_global_state():
    """Reset global state before and after each test.

    The core keeps a process-wide singleton (the resolved model registry and the
    interrupt counter); a test that sets either must not leak into its
    neighbours, and a test that reads it must not inherit a predecessor's value.
    """
    from solver_support.global_state import global_state

    # Store original state
    original_registry = global_state._model_registry
    original_interrupt_count = global_state.interrupt_count

    # Reset state
    global_state._model_registry = None
    global_state.interrupt_count = 0

    yield

    # Restore state
    global_state._model_registry = original_registry
    global_state.interrupt_count = original_interrupt_count


@pytest.fixture
def fixture_registry():
    """Wire the tests' own synthetic models.yaml as the registry for one test.

    Anything that reaches ``global_state.model_registry`` (the solvers resolving
    an engine, the horizon scan asking whether a model optimises) raises in a
    bare core, because there is no config to load. Injecting a fixture config -
    rather than mocking the registry - keeps the real loader and accessors in the
    path, so they are covered by every test that leans on them.
    """
    import solver_support.model_registry as mr
    from solver_support.global_state import global_state

    saved = mr._default_config_path
    mr.set_default_config_path(FIXTURE_CONFIG)
    global_state._model_registry = None
    try:
        yield global_state.model_registry
    finally:
        mr.set_default_config_path(saved)
        global_state._model_registry = None


@pytest.fixture
def wired_stdout_score():
    """Register a consumer-style ``Score: N`` extractor for one test.

    Stands in for a consumer's scoring hook: the core hands it a solver's stdout
    and records whatever it returns, applying no interpretation of its own. The
    x100 here mimics a consumer that reports its score scaled, and is what makes
    the difference between "the core parsed a score" and "the core routed the
    stdout through the injected extractor" visible in an assertion.
    """
    import re

    from solver_support import objective

    def extractor(stdout):
        m = re.search(r'^Score:\s*(-?\d+)', stdout, re.MULTILINE)
        return int(m.group(1)) * 100 if m else None

    saved = objective._reported_score_from_stdout
    objective.set_reported_score_from_stdout(extractor)
    try:
        yield extractor
    finally:
        objective._reported_score_from_stdout = saved


@pytest.fixture
def wired_timeout_score():
    """Register a consumer-style salvaged-solution extractor for one test.

    Returns a fixed score for any solution file that exists, so an assertion on
    the recorded value shows the core reached the extractor with the *salvaged*
    file - the part of the contract that is the core's, the parsing being the
    consumer's.
    """
    from solver_support import objective

    def extractor(solution_file):
        return 300 if Path(solution_file).exists() else None

    saved = objective._reported_score_from_timeout_solution
    objective.set_reported_score_from_timeout_solution(extractor)
    try:
        yield extractor
    finally:
        objective._reported_score_from_timeout_solution = saved
