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


def test_conjure_builds_every_action(starter):
    """The argv shapes were checked against a real Conjure 2.6 run end to end;
    pinned here so an edit to the starter cannot silently break them."""
    assert starter.build_conjure_command('modelling', model_path='m.essence') == [
        'conjure', 'modelling', '-ac', 'm.essence']
    assert starter.build_conjure_command(
        'translate-parameter', essence_param='p-02.param', eprime='m.eprime') == [
        'conjure', 'translate-parameter', '--essence-param=p-02.param',
        '--eprime=m.eprime']
    cmd = starter.build_conjure_command(
        'translate-solution', essence_param='p-02.param', eprime='m.eprime',
        eprime_solution='s.solution', essence_solution='e.solution')
    assert cmd[:2] == ['conjure', 'translate-solution']


def test_conjure_names_its_savilerow_entry(starter):
    """The starter does not rely on the legacy `kissat` fallback: its conjure
    entry names the Savile Row entry it delegates to."""
    assert starter.get_conjure_savilerow() == 'savilerow'
    assert starter.get_tune_backend('conjure') == 'savilerow'
    assert starter.get_solver_type('savilerow') == 'savilerow'
    assert 'kissat' not in starter.config['solvers']


def test_one_pddl_model_runs_under_every_planner(starter):
    """Model and solver are orthogonal: one `pddl` model, three planners."""
    pddl_models = [m for m, c in starter.config['models'].items()
                   if c['path'].endswith('.pddl')]
    assert pddl_models == ['pddl']
    for solver in ('fd', 'symk', 'enhsp'):
        assert starter.is_solver_compatible_with_model(solver, 'pddl')
        assert starter.get_default_model_for_solver(solver) == 'pddl'
    assert not starter.is_solver_compatible_with_model('savilerow', 'pddl')


def test_every_solver_has_a_default_model_that_exists(starter):
    for name, solver in starter.config['solvers'].items():
        assert solver['default_model'] in starter.config['models'], name


def test_incomplete_tunes_do_not_claim_proofs(starter):
    assert starter.get_proves_unsat('pddl') is True
    # complete; where it prunes unsafely (axioms), the verdict says so
    assert starter.get_proves_unsat('pddl', tune='ff', solver_name='fd') is True
    assert starter.get_proves_unsat('pddl', tune='soup', solver_name='fd') is False
    assert starter.get_proves_unsat('eprime') is False
    assert starter.get_proves_unsat('essence') is False


# The solver types whose backend records a solving-phase verdict, the only
# thing a run's outcome can conclude `unsat` from (issue #6).
_VERDICT_SOLVER_TYPES = {'fd', 'enhsp'}
# Savile Row and Conjure record one only on a single solve: a model with no
# horizon. A horizon model's per-horizon verdicts never decide the run.
_SINGLE_SOLVE_VERDICT_SOLVER_TYPES = {'savilerow', 'conjure'}


def test_proofs_are_claimed_only_where_a_verdict_is_recorded(starter):
    """For every solver that can run a model claiming proves-unsat, the backend
    records a verdict, so the claim is never made off a bare `failed`."""
    for model in starter.config['models']:
        for solver in starter.list_solvers():
            if not starter.is_solver_compatible_with_model(solver, model):
                continue
            tunes = [None, *starter.get_solver_config(solver).get('tune', {})]
            if any(starter.get_proves_unsat(model, tune=t, solver_name=solver)
                   for t in tunes):
                kind = starter.get_solver_type(solver)
                if kind in _SINGLE_SOLVE_VERDICT_SOLVER_TYPES:
                    assert starter.get_horizon_bounds(model) is None, (model, solver)
                else:
                    assert kind in _VERDICT_SOLVER_TYPES, (model, solver)


def test_config_path_may_be_a_str(tmp_path):
    """The starter's own header wires it with a str. A str has no is_file(), and
    used to be silently skipped, so the registry raised as if nothing at all
    had been registered."""
    path = write_starter_config(tmp_path / 'models.yaml')
    saved = mr._default_config_path
    mr.set_default_config_path(str(path))
    try:
        assert 'essence' in ModelRegistry().config['models']
    finally:
        mr.set_default_config_path(saved)


def test_errors_say_what_to_do(tmp_path):
    saved = mr._default_config_path
    try:
        mr.set_default_config_path(None)
        with pytest.raises(ModelRegistryError, match='set_default_config_path'):
            ModelRegistry()
        missing = tmp_path / 'nope.yaml'
        mr.set_default_config_path(str(missing))
        with pytest.raises(ModelRegistryError, match='nope.yaml'):
            ModelRegistry()
    finally:
        mr.set_default_config_path(saved)


def test_starter_models_name_their_horizon_parameter(starter):
    """So a new user's horizon model declares one `given horizon`, and the
    starter shows a model with no horizon beside them."""
    for model in ('essence', 'eprime'):
        assert starter.get_horizon_params(model, ('legacy',)) == ['horizon']
    assert starter.get_horizon_bounds('essence') == (2, 100)
    assert starter.get_horizon_bounds('eprime') == (1, 100)
    assert starter.get_horizon_bounds('oneshot') is None
