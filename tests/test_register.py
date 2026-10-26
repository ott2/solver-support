"""
Run-time registration (issue #11): a consumer that generates models as it goes
adds them to the registry, and makes its registry the one the solvers read.
"""

import pytest

from solver_support.global_state import global_state
from solver_support.model_registry import ModelRegistry, ModelRegistryError


def _entry(**kw):
    return {'path': '/abs/gen/m1.eprime', 'solver': 'kissat', **kw}


class TestRegisterModel:

    def test_a_registered_model_reads_like_a_yaml_one(self, fixture_registry):
        fixture_registry.register_model(
            'gen-1a2b', _entry(**{'proves-unsat': True}))
        assert fixture_registry.resolve_model_variant('gen-1a2b')[1]['path'] == \
            '/abs/gen/m1.eprime'
        assert fixture_registry.get_proves_unsat('gen-1a2b') is True
        assert 'gen-1a2b' in fixture_registry.list_models()

    def test_the_entry_is_copied(self, fixture_registry):
        entry = _entry(aliases=['g1'])
        fixture_registry.register_model('gen-1', entry)
        entry['aliases'].append('g2')
        entry['path'] = 'elsewhere'
        _, config = fixture_registry.resolve_model_variant('gen-1')
        assert config == _entry(aliases=['g1'])

    def test_the_same_entry_again_is_a_no_op(self, fixture_registry):
        fixture_registry.register_model('gen-1', _entry())
        fixture_registry.register_model('gen-1', _entry())

    def test_a_different_entry_under_the_same_name_raises(self, fixture_registry):
        fixture_registry.register_model('gen-1', _entry())
        with pytest.raises(ModelRegistryError, match='different entry'):
            fixture_registry.register_model('gen-1', _entry(path='/abs/other'))

    def test_a_yaml_model_counts_as_registered(self, fixture_registry):
        _, existing = fixture_registry.resolve_model_variant('pddl')
        fixture_registry.register_model('pddl', dict(existing))
        with pytest.raises(ModelRegistryError):
            fixture_registry.register_model('pddl', _entry())

    @pytest.mark.parametrize('name', ['', 'Gen1', ' gen1', 'gen 1', None, 3])
    def test_a_name_lookup_could_not_find_is_refused(self, fixture_registry, name):
        with pytest.raises(ModelRegistryError, match='name'):
            fixture_registry.register_model(name, _entry())

    @pytest.mark.parametrize('entry, match', [
        ('/abs/m.eprime', 'mapping'),
        ({'solver': 'kissat'}, 'path'),
        (_entry(solver='no-such-solver'), 'unknown solver'),
    ])
    def test_a_malformed_entry_is_refused(self, fixture_registry, entry, match):
        with pytest.raises(ModelRegistryError, match=match):
            fixture_registry.register_model('gen-1', entry)

    def test_another_models_alias_is_refused(self, fixture_registry):
        fixture_registry.register_model('gen-1', _entry(aliases=['shared']))
        with pytest.raises(ModelRegistryError, match='alias'):
            fixture_registry.register_model('shared', _entry())


class TestRegisterTune:

    def test_a_registered_tune_is_used(self, fixture_registry):
        fixture_registry.register_tune(
            'fd', 'lmcut-gen', {'search': 'astar(lmcut())', 'proves-unsat': True})
        assert fixture_registry.is_valid_tune('fd', 'lmcut-gen')
        assert fixture_registry.get_declaring_tune('fd', 'lmcut-gen', 'search')[
            'search'] == 'astar(lmcut())'

    def test_idempotent(self, fixture_registry):
        fixture_registry.register_tune('fd', 't', {'search': 'astar(blind())'})
        fixture_registry.register_tune('fd', 't', {'search': 'astar(blind())'})
        with pytest.raises(ModelRegistryError, match='different entry'):
            fixture_registry.register_tune('fd', 't', {'search': 'astar(ipdb())'})

    def test_an_unknown_solver_raises(self, fixture_registry):
        with pytest.raises(ModelRegistryError):
            fixture_registry.register_tune('nope', 't', {'search': 'x'})


class TestSetModelRegistry:

    def test_the_solvers_read_the_one_set(self, fixture_registry):
        mine = ModelRegistry()
        mine.register_model('gen-1', _entry())
        global_state.set_model_registry(mine)
        assert global_state.model_registry is mine

    def test_none_rebuilds_from_the_wired_config(self, fixture_registry):
        global_state.model_registry.register_model('gen-1', _entry())
        global_state.set_model_registry(None)
        with pytest.raises(ModelRegistryError):
            global_state.model_registry.resolve_model_variant('gen-1')
