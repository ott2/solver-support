"""Backend dispatch in ``build_savilerow_command``.

Savile Row drives three backends: a SAT solver, Minion, and an OR-Tools FlatZinc
solver. Which one a config selects, which tune key holds its arguments, and how
its time limit is spelled all hang off one ``backend:`` key, so the dispatch is
worth pinning in one place -- including the two backwards-compatibility
properties that a third backend could quietly break: a config with NO ``backend:``
key must still behave as the SAT backend, and Minion's argv must not move.
"""

import pytest

from solver_support.model_registry import ModelRegistry, ModelRegistryError

MODEL = "model.eprime"
PARAM = "instance.param"


def build(registry, solver, **kwargs):
    return registry.build_savilerow_command(MODEL, PARAM, solver_name=solver, **kwargs)


@pytest.fixture
def registry(fixture_registry):
    return ModelRegistry()


class TestBackendArgSets:
    """Each backend takes its arguments from its own tune key."""

    def test_ortools_uses_ortools_args(self, registry):
        cmd = build(registry, "srortools")
        assert "-or-tools" in cmd
        assert "-run-solver" in cmd

    def test_sat_config_without_backend_key_still_uses_sat_args(self, registry):
        # The regression that matters: every pre-existing SAT config omits
        # `backend:` entirely, so the default must remain the SAT arm.
        cmd = build(registry, "srsat")
        assert "-sat" in cmd
        assert "-or-tools" not in cmd

    def test_minion_argv_unchanged(self, registry):
        cmd = build(registry, "srminion")
        assert "-run-solver" in cmd
        assert "-or-tools" not in cmd and "-sat" not in cmd

    def test_missing_arg_set_for_backend_raises(self, registry):
        # `kissat` in the fixture config has no tune presets at all.
        with pytest.raises(ModelRegistryError, match="sat_args"):
            build(registry, "kissat")


class TestTimeoutDialects:
    """Each backend spells its own solver-level time limit."""

    def test_ortools_passes_time_limit_in_seconds(self, registry):
        cmd = build(registry, "srortools", timeout=45)
        assert "--time_limit=45" in cmd
        assert "-timelimit" in cmd and "45" in cmd

    def test_sat_keeps_its_own_time_flag(self, registry):
        cmd = build(registry, "srsat", timeout=45)
        assert "--time=45" in cmd
        assert "--time_limit=45" not in cmd

    def test_minion_takes_timelimit_only(self, registry):
        cmd = build(registry, "srminion", timeout=45)
        assert "-timelimit" in cmd
        assert not any(a.startswith("--time") for a in cmd)

    def test_no_timeout_emits_no_limit(self, registry):
        for solver in ("srsat", "srminion", "srortools"):
            cmd = build(registry, solver)
            assert "-timelimit" not in cmd, solver


class TestObjectiveRecording:
    """Intermediate-objective recording is a SAT facility."""

    def test_sat_records_intermediate_objectives(self, registry):
        cmd = build(registry, "srsat", objective_file_path="obj.txt")
        assert "-record-intermediate-objective-values" in cmd
        assert "obj.txt" in cmd

    def test_ortools_does_not_record(self, registry):
        # A FlatZinc backend optimises internally; Savile Row is not running the
        # optimisation loop, so there are no intermediate values to record.
        cmd = build(registry, "srortools", objective_file_path="obj.txt")
        assert "-record-intermediate-objective-values" not in cmd

    def test_minion_does_not_record(self, registry):
        cmd = build(registry, "srminion", objective_file_path="obj.txt")
        assert "-record-intermediate-objective-values" not in cmd


class TestFznBinary:
    """The FlatZinc binary is named only when the config pins it."""

    def test_omitted_when_unset(self, registry):
        # Leaving it to Savile Row's default keeps one machine's binary name out
        # of the registry.
        assert "-fzn-bin" not in build(registry, "srortools")

    def test_emitted_when_set(self, registry):
        cmd = build(registry, "srortoolsnamed")
        assert "-fzn-bin" in cmd
        assert cmd[cmd.index("-fzn-bin") + 1] == "fzn-ortools"

    def test_not_emitted_for_other_backends(self, registry):
        for solver in ("srsat", "srminion"):
            assert "-fzn-bin" not in build(registry, solver), solver


def test_model_and_param_come_last(registry):
    """Savile Row takes the model and param as trailing positionals."""
    for solver in ("srsat", "srminion", "srortools"):
        cmd = build(registry, solver, timeout=10)
        assert cmd[-2:] == [MODEL, PARAM], solver
