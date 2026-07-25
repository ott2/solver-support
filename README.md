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
   *in progress*.

The pipeline modules now live here and are imported by puzznic-support. This
package still knows nothing about any problem domain: it ships **unwired** and a
consumer supplies its scoring, registry, logger and UPF CLI via injection.

## usage

See [`docs/injection-api.md`](docs/injection-api.md) for the injection points a
consumer wires (model-registry paths, objective extraction, warning logger, UPF
CLI) and a worked example of how puzznic-support wires them.

## development

Intended to be installed editable alongside its consumer into a shared venv:

```bash
pip install -e /path/to/solver_support
```

Run the core's own smoke tests with `pytest tests/`.

## licence

BSD-3-Clause.
