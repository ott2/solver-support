# solver-support

A generic, domain-agnostic pipeline for running constraint-modelling and
planning solvers: **Conjure** / **Savile Row** (driving a SAT solver, **Minion**,
**OR-Tools CP-SAT**, **Gecode** or **Chuffed**), **Fast Downward**, **SymK**,
**ENHSP**, and **UPF**-constructed models. It handles the mechanics that every
such run needs — building solver command lines from a model registry, spawning
subprocesses with timeouts and clean teardown, naming the per-run files, parsing
solver output into a uniform timing / cost-layer model, and driving the horizon
(increasing-difficulty) loop.

Its main value is a **unified semantics for solver behaviour**. Each solver
reports how a run ended in its own way, and the raw signals are misleading
taken at face value:

- Fast Downward's exit 12 can mean a proof, a time limit or a search that gave up.
- ENHSP exits 0 after a proof and after failing to read its input alike.
- Savile Row prints the same "no solution" whether the backend refuted the
  instance or stopped at its own limit.
- A SAT solver's 10 and 20 are answers, not failures.

solver-support reads each solver's exit codes, logs and info files. It reports
every run in one vocabulary: a `verdict` (`sat` / `unsat` / `unknown` / `error` /
`killed`, with a reason) for each solve, and an `outcome` and `stop_reason` for
the run. So `unsat` means a proof, whichever solver produced it. A run that
merely found nothing, or crashed, never reads as one. The rules for each solver
are checked against its source and its real output, and are documented in
[`timing-model.md`](https://github.com/ott2/solver-support/blob/main/docs/timing-model.md).

It carries **no** notion of any particular problem domain or objective. A
consumer supplies:

- its own **model registry** (a `models.yaml`) via a config path — the loader is
  generic, the model content is the consumer's;
- its **scoring / objective strategy** via injection — the core reports the raw
  objective a solver emits and lets the consumer interpret it (is-this-an-
  optimisation-model, how to read the objective, any reporting multiplier).

## Status

**Beta.** The pipeline is in day-to-day use by several consumers and is covered
by its own test suite. The injection seams and the run-summary vocabulary
(`outcome`, `stop_reason`, `verdict`) are stable: later releases add to them
rather than rename them. Other API may still shift between 0.x releases. Each
release's changes are listed on the
[releases page](https://github.com/ott2/solver-support/releases).

The package ships **unwired**, by design and on every release: a bare
`import solver_support` gives a pipeline that resolves no model registry and
interprets no objective, because it has no way to guess either. Constructing a
`ModelRegistry` *raises* rather than falling back on a guess. A consumer
registers its scoring, registry paths, warning logger and UPF CLI once, before
first use — see [Usage](#usage).

## Installation

```bash
pip install solver-support
```

Requires Python 3.9 or newer (3.9.6 is the system Python on macOS, and is
supported deliberately — each release is built and tested against it). The only
runtime dependencies are `psutil` and `PyYAML`.

## Quick start

The package ships no model registry, but it carries a commented starter one
(Conjure, Savile Row, Fast Downward, SymK and ENHSP) to copy and edit. Writing it out
does not wire it; you point the registry at it:

```python
from pathlib import Path
from solver_support import (
    write_starter_config, set_default_config_path, set_resource_root,
    global_state, SolverRunner, FastDownwardSolver, fd_backstop,
)

config = write_starter_config("config/models.yaml")   # once; then edit it
set_default_config_path(config)
set_resource_root(".")            # where the models' `path:` entries resolve

registry = global_state.model_registry
runner = SolverRunner(instance="p01", model="pddl", solver="fd", timeout=60,
                      timestamp="run1", summary_file=Path("p01.json"))
try:
    plan = FastDownwardSolver(runner).solve(
        Path("p01.pddl"), model_path=str(registry.get_model_path("pddl")),
        watchdog=fd_backstop(60))         # let the planner's own limit fire
    status = "done" if plan else "failed"
except TimeoutError:
    status = "timeout"
runner.finalize_summary(status, proves_unsat=registry.get_proves_unsat("pddl"))
print(runner.summary.outcome)     # solved / unsat / unknown / error
```

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
- [`unsat-soundness.md`](https://github.com/ott2/solver-support/blob/main/docs/unsat-soundness.md)
  — when a planner's exhausted search counts as a proof of unsatisfiability,
  and the evidence behind each allowed configuration.

## Development

To work on this package, clone and install it editable:

```bash
git clone https://github.com/ott2/solver-support
pip install -e solver-support
```

Consumers pin a **released** version from PyPI rather than the working tree, so a
change here reaches them only when it is released. To try an unreleased change
against a consumer, install this editable into that consumer's environment
deliberately — it is not the default arrangement.

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

By András Salamon, with Claude Opus 4.8, 5 and 5.5.
