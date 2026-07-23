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

**Alpha / under construction.** This package is being extracted, module by
module, from [`puzznic-support`](../puzznic), which is its first consumer and the
origin of every module here. The extraction is deliberately staged so the
originating test suite stays green throughout:

1. **decouple** the domain-scoring seams (in place, in puzznic-support);
2. **split** mixed-concern files (in place);
3. **extract** the now-clean core modules into this package and repoint
   puzznic-support at them;
4. **stand alone** — this package's own tests, docs, and install verification.

Until the extraction lands, this is a scaffold: an installable but empty package
reserving the name and layout.

## development

Intended to be installed editable alongside its consumer into a shared venv:

```bash
pip install -e /path/to/solver_support
```

## licence

BSD-3-Clause.
