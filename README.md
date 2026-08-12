# solver-support

A generic, domain-agnostic pipeline for running constraint-modelling and
planning solvers: **Conjure** / **Savile Row** (+ SAT/CP backends), **Fast
Downward**, **SymK**, **ENHSP**, and **UPF**-constructed models. It handles the
mechanics that every such run needs — building solver command lines from a model
registry, spawning subprocesses with timeouts and clean teardown, naming the
per-run files, parsing solver output into a uniform timing / cost-layer model,
and driving the horizon (increasing-difficulty) loop.

It carries **no** notion of any particular problem domain or objective. A
consumer supplies:

- its own **model registry** (a `models.yaml`) via a config path — the loader is
  generic, the model content is the consumer's;
- its **scoring / objective strategy** via injection — the core reports the raw
  objective a solver emits and lets the consumer interpret it (is-this-an-
  optimisation-model, how to read the objective, any reporting multiplier).

## Status

**Alpha.** The pipeline is in day-to-day use by two consumers and is covered by
its own test suite, but this is the first public release and the API may still
shift between 0.x versions.

The package ships **unwired**: a bare `import solver_support` gives a pipeline
that resolves no model registry and interprets no objective, because it has no
way to guess either. A consumer registers its scoring, registry paths, warning
logger and UPF CLI once, before first use.

## Installation

```bash
pip install solver-support
```

Requires Python 3.9 or newer (3.9.6 is the system Python on macOS, and is
supported deliberately). The only runtime dependencies are `psutil` and `PyYAML`.

## Usage

See [`docs/injection-api.md`](https://github.com/ott2/solver-support/blob/main/docs/injection-api.md)
for the four injection points a consumer wires — model-registry paths, objective
extraction, warning logger, UPF CLI — with a worked example.

## Docs

- [`injection-api.md`](https://github.com/ott2/solver-support/blob/main/docs/injection-api.md)
  — the four seams a consumer wires.
- [`solver-runner.md`](https://github.com/ott2/solver-support/blob/main/docs/solver-runner.md)
  — running a solver as a subprocess: `SolverResult`, timeout vs duration, phases.
- [`timing-model.md`](https://github.com/ott2/solver-support/blob/main/docs/timing-model.md)
  — how solver timing is recorded, how the backends map onto one shape, and the
  invariants parsers rely on.

## Development

Clone and install editable. A consumer under development alongside it is
normally installed into the same virtualenv, so both are importable and edits to
either take effect immediately:

```bash
git clone https://github.com/ott2/solver-support
pip install -e solver-support
```

## Tests

`pytest tests/` — the core's own suite, and the home for solver-level
development. It runs against `solver_support` alone: no consumer need be
installed, and none is imported.

Since the core ships unwired, a test that needs a seam wired wires a stand-in
(`tests/conftest.py`):

| fixture | supplies |
|---------|----------|
| `fixture_registry` | a `ModelRegistry` over `tests/fixtures/models.yaml`, this suite's own synthetic config — never a consumer's |
| `wired_stdout_score` | a consumer-style `Score: N` extractor, so a score reaches `solver_stats` |
| `wired_timeout_score` | a consumer-style extractor for a salvaged timeout solution |

Wiring a fixture config rather than mocking the registry keeps the real loader
and accessors in the path. Anything a test needs from the registry goes in
`tests/fixtures/models.yaml`, keeping its shape faithful to a real consumer
config — that shape is the contract the loader implements.

## Licence

BSD-3-Clause — see
[`LICENSE`](https://github.com/ott2/solver-support/blob/main/LICENSE).

By András Salamon, with Claude Opus 4.8 and 5.
