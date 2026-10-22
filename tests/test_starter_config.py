"""
The starter registry a new user writes out with ``write_starter_config``.

It ships as package data and must stay a config the real loader accepts, so it
is loaded and exercised here rather than merely parsed: a starter that drifted
from the builders would fail its first user on their first run. It must also
never wire anything by existing - the core ships unwired.
"""

import pytest
import yaml

import solver_support.model_registry as mr
from solver_support import (
    ModelRegistry, ModelRegistryError, starter_config_text, write_starter_config)


@pytest.fixture
def starter(tmp_path):
    """A registry wired to a freshly written starter config."""
    path = write_starter_config(tmp_path / 'config' / 'models.yaml')
    saved = mr._default_config_path
    mr.set_default_config_path(path)
    try:
        yield ModelRegistry()
    finally:
        mr.set_default_config_path(saved)


def test_writes_the_shipped_text(tmp_path):
    path = write_starter_config(tmp_path / 'models.yaml')
    assert path.read_text() == starter_config_text()
    assert '# ' in starter_config_text()        # the comments are the point


def test_refuses_to_overwrite_an_edited_registry(tmp_path):
    path = tmp_path / 'models.yaml'
    path.write_text('models: {}\n')
    with pytest.raises(FileExistsError):
        write_starter_config(path)
    assert path.read_text() == 'models: {}\n'
    write_starter_config(path, overwrite=True)
    assert path.read_text() == starter_config_text()


def test_writing_it_wires_nothing(tmp_path):
    write_starter_config(tmp_path / 'models.yaml')
    with pytest.raises(ModelRegistryError):
        ModelRegistry()


def test_every_model_names_a_defined_solver():
    config = yaml.safe_load(starter_config_text())
    for name, model in config['models'].items():
        assert model['solver'] in config['solvers'], name


def test_every_fd_tune_builds(starter):
    for solver in ('fd', 'symk'):
        for tune in starter.get_valid_tunes(solver):
            anytime = starter.is_anytime_tune(solver, tune)
            cmd = starter.build_fd_command('d.pddl', 'p.pddl', anytime=anytime,
                                           tune=tune, solver_name=solver)
            assert cmd[0] == solver
            assert '--search-time-limit' in cmd


def test_enhsp_tunes_build(starter):
    for tune in starter.get_valid_tunes('enhsp'):
        cmd = starter.build_enhsp_command('d.pddl', 'p.pddl', 'plan', tune=tune)
        assert cmd[cmd.index('-planner') + 1]


def test_savilerow_builds(starter):
    cmd = starter.build_savilerow_command('m.eprime', 'p.param', solver_name='savilerow',
                                          use_srjava=True)
    assert cmd[0] == 'savilerow'
    assert '-timelimit' in cmd


def test_incomplete_tunes_do_not_claim_proofs(starter):
    assert starter.get_proves_unsat('pddl') is True
    assert starter.get_proves_unsat('pddl', tune='ff') is False
    assert starter.get_proves_unsat('pddl', tune='soup') is False
    assert starter.get_proves_unsat('eprime') is False
