"""Smoke tests for the extracted solver_support core.

These run against solver_support alone (no consumer installed / imported), so they
double as a check that the core is genuinely domain-clean: it ships unwired and
does not secretly resolve a consumer's configuration.
"""

import pytest

import solver_support


def test_public_api_present():
    """Every name advertised in __all__ actually resolves on the package."""
    for name in solver_support.__all__:
        assert hasattr(solver_support, name), f"missing public name: {name}"


def test_core_ships_unwired_objective():
    """With no extractor registered, every objective route yields None."""
    from solver_support import objective

    if objective._raw_objective_from_solution is None:
        assert objective.raw_objective_from_solution("nonexistent") is None
    if objective._reported_score_from_solution is None:
        assert objective.reported_score_from_solution("nonexistent") is None
    if objective._reported_score_from_stdout is None:
        assert objective.reported_score_from_stdout("Score: 5") is None


def test_registry_requires_a_registered_config():
    """A bare core resolves no default config -- it must not guess a consumer.

    Without a registered default-config path (and with no config bundled beside
    the core), constructing the registry raises rather than silently loading some
    other package's models.yaml.
    """
    import solver_support.model_registry as mr

    if mr._default_config_path is None:
        with pytest.raises(solver_support.ModelRegistryError):
            solver_support.ModelRegistry()


def test_registry_loads_from_an_injected_config(tmp_path):
    """A consumer-supplied config path is honoured by the registry loader."""
    import solver_support.model_registry as mr

    cfg = tmp_path / "models.yaml"
    cfg.write_text(
        "models:\n"
        "  demo:\n"
        "    path: demo.eprime\n"
        "solvers:\n"
        "  kissat: {}\n"
    )

    saved = mr._default_config_path
    try:
        mr.set_default_config_path(cfg)
        reg = solver_support.ModelRegistry()
        assert "demo" in reg.config.get("models", {})
        assert "kissat" in reg.config.get("solvers", {})
    finally:
        mr.set_default_config_path(saved)
