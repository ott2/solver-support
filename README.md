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

## status

**Alpha.** The core has been extracted, module by module, from
[`puzznic-support`](../puzznic) — its first consumer and the origin of every
module here — which now consumes this package. The extraction was staged so the
originating test suite stayed green throughout:

1. **decouple** the domain-scoring seams (in place, in puzznic-support); *done*
2. **split** mixed-concern files (in place); *done*
3. **extract** the now-clean core modules into this package and repoint
   puzznic-support at them; *done*
4. **stand alone** — this package's own tests, docs, and install verification;
   *done*.

The pipeline modules now live here and are imported by puzznic-support. This
package still knows nothing about any problem domain: it ships **unwired** and a
consumer supplies its scoring, registry, logger and UPF CLI via injection.

## usage

See [`docs/injection-api.md`](docs/injection-api.md) for the injection points a
consumer wires (model-registry paths, objective extraction, warning logger, UPF
CLI) and a worked example of how puzznic-support wires them.

## docs

- [`injection-api.md`](docs/injection-api.md) — the four seams a consumer wires.
- [`solver-runner.md`](docs/solver-runner.md) — running a solver as a
  subprocess: `SolverResult`, timeout vs duration, phases.
- [`timing-model.md`](docs/timing-model.md) — how solver timing is recorded, how
  the backends map onto one shape, and the invariants parsers rely on.

## development

Intended to be installed editable alongside its consumer into a shared venv:

```bash
pip install -e /path/to/solver_support
```

## tests

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

## licence

BSD-3-Clause — see [`LICENSE`](LICENSE).
