"""
Whether a tune keeps the model's solution set (issue #12), read from the
Savile Row arguments it builds: only -S0 qualifies.
"""

import pytest

from solver_support import savilerow_args_preserve_solutions


@pytest.mark.parametrize('args, expected', [
    (['-run-solver', '-sat', '-S0'], True),
    (['-S0', '-O3', '-deletevars'], True),      # optimisations that keep the count
    (['-run-solver', '-sat'], False),           # the default is -S1
    (['-S1'], False),
    (['-S2'], False),
    (['-S0', '-S2'], False),
    (['-S2', '-S0'], False),                    # never depends on flag order
    (['-S0', '-remove-redundant-vars'], False),
    (['-S0', '-aux-non-functional'], False),
    (['-S0', '-var-sym-breaking'], False),
    ([], False),
])
def test_the_argv(args, expected):
    assert savilerow_args_preserve_solutions(args) is expected


class TestRegistry:

    def test_an_s0_tune_preserves(self, fixture_registry):
        assert fixture_registry.get_preserves_solutions('srsat', 'exact') is True

    def test_the_default_tune_does_not(self, fixture_registry):
        assert fixture_registry.get_preserves_solutions('srsat') is False
        assert fixture_registry.get_preserves_solutions('srsat', 'default') is False

    def test_a_tune_can_take_the_claim_away(self, fixture_registry):
        assert fixture_registry.get_preserves_solutions(
            'srsat', 'exact-withdrawn') is False

    def test_a_tune_cannot_grant_it(self, fixture_registry):
        fixture_registry.register_tune('srsat', 'claims', {
            'sat_args': ['-run-solver', '-sat'], 'preserves-solutions': True})
        assert fixture_registry.get_preserves_solutions('srsat', 'claims') is False

    def test_run_options_count(self, fixture_registry):
        assert fixture_registry.get_preserves_solutions(
            'srsat', 'exact', extra_options=['-S2']) is False
        assert fixture_registry.get_preserves_solutions(
            'srsat', 'default', extra_options=['-S0']) is True

    def test_an_unknown_tune_resolves_as_the_builder_does(self, fixture_registry):
        """A typo falls back to default_tune, which here is -S1."""
        assert fixture_registry.get_preserves_solutions('srsat', 'exactt') is False

    @pytest.mark.parametrize('solver', ['fd', 'conjure', 'upffd', 'no-such-solver'])
    def test_other_solvers_never_claim_it(self, fixture_registry, solver):
        assert fixture_registry.get_preserves_solutions(solver) is False
