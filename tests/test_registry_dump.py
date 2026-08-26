"""Reporting what the registry actually resolved to.

``effective_config`` / ``dump_config`` exist to answer "what is in force?", which
is otherwise only answerable by reading the bundled config and any user override
and merging them by eye -- and the override's location depends on the working
directory at construction time, so after the fact it may not be reconstructible
at all.

The dump is deliberately a *view*, not a config generator, and the tests below
pin the two properties that keep it honest: it reports its sources, and its
header warns against adopting it wholesale. Both matter because overrides are
deep-merged, so a full dump adopted as an override pins every key at today's
value and silently shadows later changes to the bundled config.
"""

import yaml

import pytest

from solver_support.model_registry import ModelRegistry


@pytest.fixture
def registry(fixture_registry):
    return ModelRegistry()


class TestEffectiveConfig:

    def test_returns_the_merged_config(self, registry):
        config = registry.effective_config()
        assert set(config) >= {"models", "solvers"}
        assert "srortools" in config["solvers"]

    def test_copy_is_deep(self, registry):
        config = registry.effective_config()
        config["solvers"]["srortools"]["backend"] = "mutated"
        # The live registry must be untouched, or a caller inspecting the config
        # could silently reconfigure the run it is inspecting.
        assert registry.get_solver_config("srortools")["backend"] == "ortools"

    def test_unresolved_leaves_implicit_backend_absent(self, registry):
        # srsat has no `backend:` key; unresolved output mirrors the source.
        assert "backend" not in registry.effective_config()["solvers"]["srsat"]

    def test_resolved_writes_out_the_implicit_backend(self, registry):
        config = registry.effective_config(resolved=True)
        assert config["solvers"]["srsat"]["backend"] == "sat"
        # An explicit one is left alone.
        assert config["solvers"]["srortools"]["backend"] == "ortools"

    def test_resolved_leaves_non_savilerow_solvers_alone(self, registry):
        # `backend` means nothing to a upfplan solver, so inventing one would be
        # a lie that also happens to load.
        assert "backend" not in registry.effective_config(resolved=True)["solvers"]["upffd"]


class TestConfigSources:

    def test_records_the_files_merged(self, registry):
        assert len(registry.config_sources) == 1
        assert registry.config_sources[0].name == "models.yaml"


class TestDumpConfig:

    def test_dump_round_trips_as_yaml(self, registry):
        loaded = yaml.safe_load(registry.dump_config())
        assert loaded == registry.effective_config()

    def test_resolved_dump_also_round_trips(self, registry):
        # Only real config keys are ever added, so a resolved dump still loads.
        loaded = yaml.safe_load(registry.dump_config(resolved=True))
        assert loaded["solvers"]["srsat"]["backend"] == "sat"

    def test_header_names_the_sources(self, registry):
        assert "models.yaml" in registry.dump_config().splitlines()[1]

    def test_header_warns_against_adopting_it(self, registry):
        header = registry.dump_config().split("\n\n")[0]
        assert "not for adopting wholesale" in header
        assert "deep-merged" in header

    def test_header_can_be_suppressed(self, registry):
        text = registry.dump_config(header=False)
        assert not text.startswith("#")
        assert yaml.safe_load(text) == registry.effective_config()

    def test_key_order_follows_the_source(self, registry):
        # sort_keys=False: alphabetising a registry makes it unreadable, and the
        # source order is how a maintainer arranged it.
        text = registry.dump_config(header=False)
        assert text.index("models:") < text.index("solvers:")
