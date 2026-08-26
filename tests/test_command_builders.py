"""Backend dispatch in ``build_savilerow_command``.

Savile Row drives five backends: a SAT solver, Minion, and three FlatZinc solvers
(OR-Tools CP-SAT, Gecode, Chuffed). Which one a config selects, which tune key
holds its arguments, how its time limit is spelled and in what unit, and which
flag names its binary all hang off one ``backend:`` key, so the dispatch is worth
pinning in one place -- including the two backwards-compatibility properties a
new backend could quietly break: a config with NO ``backend:`` key must still
behave as the SAT backend, and Minion's argv must not move.

The unit conversions are the reason several of these tests exist. Gecode and
Chuffed both take their limit in milliseconds and neither says so in the flag
name, so an unscaled value is not a crash but a solver that gives up in 30ms and
reports the instance as hard.
"""

import pytest

from solver_support.model_registry import ModelRegistry, ModelRegistryError

MODEL = "model.eprime"
PARAM = "instance.param"

#: Every backend the builder knows, with the arg it is expected to emit.
ALL_BACKENDS = [
    ("srsat", "-sat"),
    ("srminion", "-run-solver"),
    ("srortools", "-or-tools"),
    ("srgecode", "-gecode"),
    ("srchuffed", "-chuffed"),
]


def build(registry, solver, **kwargs):
    return registry.build_savilerow_command(MODEL, PARAM, solver_name=solver, **kwargs)


def solver_options(cmd):
    """The values of every ``-solver-options`` flag in a command."""
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == "-solver-options"]


@pytest.fixture
def registry(fixture_registry):
    return ModelRegistry()


class TestBackendArgSets:
    """Each backend takes its arguments from its own ``<backend>_args`` key."""

    @pytest.mark.parametrize("solver,expected", ALL_BACKENDS)
    def test_backend_uses_its_own_arg_set(self, registry, solver, expected):
        assert expected in build(registry, solver)

    def test_sat_config_without_backend_key_still_uses_sat_args(self, registry):
        # The regression that matters: every pre-existing SAT config omits
        # `backend:` entirely, so the default must remain the SAT arm.
        cmd = build(registry, "srsat")
        assert "-sat" in cmd
        assert "-or-tools" not in cmd

    def test_minion_argv_unchanged(self, registry):
        cmd = build(registry, "srminion")
        assert cmd == ["savilerow", "-run-solver", MODEL, PARAM]

    def test_missing_arg_set_for_backend_raises(self, registry):
        # `kissat` in the fixture config has no tune presets at all.
        with pytest.raises(ModelRegistryError, match="sat_args"):
            build(registry, "kissat")


class TestUnknownBackend:
    """A misspelled backend is an error, not a quiet fallback to SAT."""

    def test_unknown_backend_raises(self, registry):
        # `srbadbackend` says `backend: or-tools` -- the hyphenated spelling of
        # Savile Row's own flag, and so the near-miss a config author is most
        # likely to write -- and carries sat_args, so the lenient path would have
        # built a working SAT command line: a run that succeeds while measuring
        # the wrong solver.
        with pytest.raises(ModelRegistryError, match="unknown backend 'or-tools'"):
            build(registry, "srbadbackend")

    def test_error_lists_the_known_backends(self, registry):
        with pytest.raises(ModelRegistryError, match="chuffed, gecode, minion"):
            build(registry, "srbadbackend")


class TestTimeoutDialects:
    """Each backend spells its own time limit, in its own unit."""

    def test_sat_takes_seconds(self, registry):
        assert solver_options(build(registry, "srsat", timeout=45)) == ["--time=45"]

    def test_ortools_takes_seconds(self, registry):
        assert solver_options(build(registry, "srortools", timeout=45)) == [
            "--time_limit=45"]

    def test_gecode_takes_milliseconds(self, registry):
        assert solver_options(build(registry, "srgecode", timeout=45)) == [
            "-time 45000"]

    def test_chuffed_takes_milliseconds(self, registry):
        assert solver_options(build(registry, "srchuffed", timeout=45)) == [
            "--time-out 45000"]

    def test_minion_takes_no_solver_option(self, registry):
        cmd = build(registry, "srminion", timeout=45)
        assert solver_options(cmd) == []
        assert cmd[cmd.index("-timelimit") + 1] == "45"

    @pytest.mark.parametrize("solver,_", ALL_BACKENDS)
    def test_savilerow_timelimit_is_always_seconds(self, registry, solver, _):
        # SR's own -timelimit covers translation plus solving and never scales,
        # whatever unit the backend solver happens to speak.
        cmd = build(registry, solver, timeout=45)
        assert cmd[cmd.index("-timelimit") + 1] == "45"

    @pytest.mark.parametrize("solver,_", ALL_BACKENDS)
    def test_no_timeout_emits_no_limit(self, registry, solver, _):
        cmd = build(registry, solver)
        assert "-timelimit" not in cmd
        assert solver_options(cmd) == []

    def test_config_may_override_the_dialect(self, registry):
        # The flag name is the config's to change; the unit stays the solver's,
        # so the override is still scaled to milliseconds.
        assert solver_options(build(registry, "srgecodebin", timeout=45)) == [
            "-overall-time 45000"]


class TestObjectiveRecording:
    """Intermediate-objective recording is a SAT facility."""

    def test_sat_records_intermediate_objectives(self, registry):
        cmd = build(registry, "srsat", objective_file_path="obj.txt")
        assert "-record-intermediate-objective-values" in cmd
        assert cmd[cmd.index("-record-intermediate-objective-values") + 1] == "obj.txt"

    @pytest.mark.parametrize("solver", ["srminion", "srortools", "srgecode",
                                        "srchuffed"])
    def test_other_backends_do_not_record(self, registry, solver):
        # They optimise internally, so Savile Row is not running the loop and has
        # no intermediate values to see.
        cmd = build(registry, solver, objective_file_path="obj.txt")
        assert "-record-intermediate-objective-values" not in cmd

    @pytest.mark.parametrize("solver,expected", [
        ("srsat", True),
        ("kissat", True),       # no `backend:` key -- still the SAT arm
        ("srminion", False),
        ("srortools", False),
        ("srgecode", False),
        ("srchuffed", False),
        ("upffd", False),       # not a savilerow solver at all
    ])
    def test_records_intermediate_objectives_query(self, registry, solver, expected):
        # The horizon loop asks this before naming an objective file, so that it
        # does not name and clear a file the backend will never write.
        assert registry.records_intermediate_objectives(solver) is expected


class TestSolverBinary:
    """The solver binary is named only when pinned, with that backend's flag."""

    @pytest.mark.parametrize("solver,_", ALL_BACKENDS)
    def test_omitted_when_unset(self, registry, solver, _):
        # Leaving it to Savile Row's default keeps one machine's build out of a
        # shared registry.
        cmd = build(registry, solver)
        assert not any(a.endswith("-bin") for a in cmd)

    def test_ortools_uses_the_standard_flatzinc_flag(self, registry):
        cmd = build(registry, "srortoolsbin")
        assert cmd[cmd.index("-fzn-bin") + 1] == "fzn-ortools"

    def test_gecode_uses_its_own_flag(self, registry):
        # The point of the pair: a per-backend flag, not one hardcoded name.
        cmd = build(registry, "srgecodebin")
        assert "-fzn-bin" not in cmd
        assert cmd[cmd.index("-gecode-bin") + 1] == "fzn-gecode-6"


class TestBackendTable:
    """The table is the single place a backend is described."""

    def test_every_backend_is_fully_specified(self):
        required = {"bin_option", "timeout_option", "timeout_scale",
                    "records_objectives"}
        for name, spec in ModelRegistry.SAVILEROW_BACKENDS.items():
            assert set(spec) == required, name

    def test_only_the_sat_arm_records_objectives(self):
        recording = {n for n, s in ModelRegistry.SAVILEROW_BACKENDS.items()
                     if s["records_objectives"]}
        assert recording == {"sat"}


@pytest.mark.parametrize("solver,_", ALL_BACKENDS)
def test_model_and_param_come_last(registry, solver, _):
    """Savile Row takes the model and param as trailing positionals."""
    cmd = build(registry, solver, timeout=10, objective_file_path="obj.txt")
    assert cmd[-2:] == [MODEL, PARAM]
