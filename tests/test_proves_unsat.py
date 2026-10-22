"""
``ModelRegistry.get_proves_unsat``: model AND tune (issue #4).

For a planner, whether "no plan" is a proof depends on the search as well as
the model: blind A* is complete on any task, while FF prunes infinite-valued
states even where axioms make that unsafe. A tune preset can therefore take the
claim away (``proves-unsat: false``) but never grant it.
"""

import pytest


@pytest.fixture
def registry(fixture_registry):
    return fixture_registry


def test_default_tune_keeps_the_models_claim(registry):
    assert registry.get_proves_unsat('pddl') is True


def test_explicit_tune_can_take_it_away(registry):
    assert registry.get_proves_unsat('pddl', tune='blind') is True
    assert registry.get_proves_unsat('pddl', tune='ff') is False
    assert registry.get_proves_unsat('pddl', tune='soup') is False


def test_unknown_tune_falls_back_to_default_tune(registry):
    """The same lenient resolution build_fd_command uses: an unknown tune ran
    the default search, so it is the default search's claim that counts."""
    assert registry.get_proves_unsat('pddl', tune='nosuch') is True


def test_models_pinned_tune_counts(registry):
    assert registry.get_proves_unsat('pddlff') is False
    # ...and an explicit --tune overrides the pin, as it does for the command
    assert registry.get_proves_unsat('pddlff', tune='blind') is True


def test_a_tune_cannot_grant_what_the_model_lacks(registry):
    assert registry.get_proves_unsat('scored') is False
    assert registry.get_proves_unsat('scored', tune='blind', solver_name='fd') is False


def test_tune_less_solver_keeps_the_models_claim(registry):
    assert registry.get_proves_unsat('upf') is True


def test_unknown_model_cannot_prove(registry):
    assert registry.get_proves_unsat('nosuch') is False
