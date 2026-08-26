# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`solver-support` is a **library**, not an application: a domain-agnostic pipeline for
driving constraint-modelling and planning solvers (Conjure / Savile Row + SAT/CP
backends, Fast Downward, SymK, ENHSP, and UPF-constructed models). It has no CLI,
no entry points, and no `models.yaml` of its own. It was extracted module-by-module
from `../puzznic` (`puzznic-support`), its first consumer, which now imports it.

The governing constraint: **the core ships unwired and knows nothing about any
problem domain.** The dependency arrow points one way, consumer → core. Never import
a consumer here, never hardcode a consumer's package name, config path, CLI name, or
scoring rule. If new code needs domain policy, add an injection seam instead.

## Commands

`python` is not on PATH; use a venv with the package installed editable. Several
exist and all have `solver_support` editable-installed — the shared one is
`/Users/as456/projects/claudecode/venv`:

```bash
V=/Users/as456/projects/claudecode/venv/bin/python

$V -m pytest tests/ -q                            # full suite (223 tests, ~5s)
$V -m pytest tests/test_solver_stats.py -q        # one file
$V -m pytest tests/test_solvers.py::TestFastDownwardSolver::test_solve_success -q   # one test
$V -m pytest tests/ -m "not slow" -q              # skip the `slow` marker
$V -m pytest tests/ --cov=solver_support          # coverage (pytest-cov is a dev dep)

pip install -e /Users/as456/projects/claudecode/solver_support   # editable install
```

**Python 3.9 is a real constraint, not a formality.** It is the macOS system Python
(`/usr/bin/python3` is 3.9.6), where most people installing this already are, so a
source install must not need a newer interpreter: no 3.10+ syntax (`match`, `X | Y`
annotations) — use the `typing` equivalents. Every local venv is 3.14, so nothing
exercises the floor by default. Check it explicitly when touching syntax or
packaging:

```bash
rm -rf /tmp/py39 && /usr/bin/python3 -m venv /tmp/py39
/tmp/py39/bin/python -m pip install -q . pytest && /tmp/py39/bin/python -m pytest tests/ -q
```

The `build-system` floor of `setuptools>=77` is what the PEP 639 SPDX `license`
field needs (76.1.0 rejects a bare string). It costs no 3.9 support — setuptools
kept 3.9 until 83.0.0 — but raising it past 82.x would.

The suite runs against `solver_support` alone — no consumer is installed or imported.
Tests never shell out to a real solver; they mock `SolverRunner`/subprocess or feed
captured solver output to the parsers.

After changing anything a consumer relies on, check the consumer suite too. It needs
that consumer's console scripts on `PATH` — without them ~11 tests fail on a missing
`puzznic-vis` / `prob2any`, which is a PATH artifact, not a regression:

```bash
env PATH="/Users/as456/projects/claudecode/puzznic/venv/bin:$PATH" \
  /Users/as456/projects/claudecode/puzznic/venv/bin/python -m pytest ../puzznic/tests -q
```

## The four injection seams

Every seam has the same shape: a module-level default, a `set_*` setter, and a stable
wrapper that reads the current value **at call time** — so wiring can happen any time
before first use. Full detail in `docs/injection-api.md`.

| seam | module | unwired behaviour |
|------|--------|-------------------|
| model-registry packaging (`set_default_config_path` / `set_resource_root` / `set_user_config_finder`) | `model_registry.py` | `ModelRegistry()` **raises** `ModelRegistryError` rather than guessing a config |
| objective extraction (4 `set_*` setters) | `objective.py` | every route returns `None`; the core records no score |
| warning logger name (`set_warn_logger_name`) | `utilities.py` | routes to the `'solver_support'` logger |
| UPF planner CLI (`upfplan_command=` / `UPFPLAN_COMMAND`) | `solver_upfplan.py` | `solve()` raises `ValueError` rather than shelling out to a `None`-prefixed argv |

The objective seam splits **reported** score (display units, consumer may scale) from
the **raw** objective (model units — it drives horizon-to-horizon comparison and must
never be scaled). Keep that distinction when touching `solver_horizon.py`.

## Architecture

Roughly a pipeline, bottom-up:

- **`global_state.py`** — a process-wide singleton (`global_state`) holding the
  interrupt counter, debug/verbose/srjava flags, `--options` / `--tune`, and a lazily
  built `ModelRegistry`. Solvers reach configuration through it rather than through
  constructor plumbing. Interrupt semantics: 1 interrupt = stop at the next phase
  boundary, 2 (or SIGTERM) = force termination.
- **`model_registry.py` + `command_builders.py`** — a generic YAML loader plus the
  `build_*_command` argv assemblers, which are a `CommandBuilderMixin` that
  `ModelRegistry` inherits. There is a deliberate back-import cycle: `command_builders`
  imports `ModelRegistryError` from `model_registry`, which imports the mixin *after*
  defining that exception. Don't reorder those imports. Config loading is a **deep
  merge** of the consumer's bundled yaml then an optional user override, so a partial
  override is the supported shape; `config_sources` records what was merged and
  `effective_config()` / `dump_config()` report the result for a consumer's
  `show-registry`-style command. The dump is a *view* — it drops comments, and a
  whole-file copy readopted as an override would pin every key at today's value.
- **`solver_base.py`** — the abstract `Solver`; `solve(param_file, **kwargs) -> Optional[Path]`.
  Solvers run in the *current working directory* (the caller has already chdir'd) and
  use relative paths.
- **the four solver backends** — `solver_fd.py` (Fast Downward *and* SymK: a fork with
  the same driver and output), `solver_enhsp.py`, `solver_upfplan.py` (the single class
  behind every UPF-routed model, `--domain` vs `--build` route chosen by `build_mode`),
  and `solver_horizon.py` (`SavileRowSolver`, `ConjureSolver`).
- **`solver_runner.py`** — subprocess execution: timeouts with full process-tree teardown
  (`psutil`), separate stdout/stderr capture (plan stays clean on stdout while engines log
  search to stderr), live tee-ing via reader threads, and phase recording. `run_phase()`
  records a `PhaseResult`; `run()` doesn't; `run_simple()` returns `result.failed`.
- **`solver_stats.py`** — per-solver output parsers, dispatched by
  `detect_solver_from_command` on generic substrings of the argv.
- **`run_summary.py`** — the persisted record: `RunSummary` with a list of `PhaseResult`.

## Cross-cutting rules that are easy to break

**Tune resolution lives in one place.** `Solver._effective_tune()` is the single
resolution shared by every backend: an explicit CLI `--tune` (in `global_state`) wins,
else the model's pinned `tune:` from `models.yaml`, else the backend's `default_tune`
(resolved inside the builder). Backends must call it rather than re-deriving.
`ModelRegistry._effective_tune` is the *lenient* lookup builders use — an unknown or
key-less tune silently falls back to `default_tune`, which is why `is_valid_tune()`
exists for a CLI to turn a typo into an explicit error before that leniency hides it.

**A Savile Row backend is one row in `SAVILEROW_BACKENDS`, and nothing else.** The
table in `command_builders.py` holds the whole description of each backend (`sat`,
`minion`, `ortools`, `gecode`, `chuffed`) — its binary flag, its own timeout flag
and unit, and whether it records intermediate objectives. The arg-set key is
derived (`<backend>_args`), never tabulated. Adding a sixth backend must stay a
one-row change; if you find yourself writing `if backend == ...` in the builder,
the fact belongs in the row instead. Unlike an unknown *tune*, an unknown
*backend* raises: a wrong tune still runs the right solver, but a typo'd backend
would silently build the SAT argv and measure a different solver than the one
asked for. **`timeout_scale` is load-bearing** — Gecode and Chuffed take
milliseconds without saying so, and an unscaled limit fails silently as a solver
that gives up instantly. Only the SAT arm records objectives, so
`_generate_objective_file_path` returns `None` elsewhere and continue-scan relies
on the injected solution extractor (see `docs/timing-model.md`).

**`detect_solver_from_command` is order-sensitive.** `upfplan` must be matched before
the engine names, because a UPF command embeds its engine (`-s fast-downward`) and
would otherwise be parsed by the FD extractor, which can't read upfplan's plan format.
`enhsp` must be matched before the `fd`/`symk` branch for the same reason.

**Two horizon models, never mixed in one run** (see `docs/timing-model.md`):
a Savile Row *horizon scan* produces one `solving-horizon-N` phase per move budget,
while the planners produce a single `solving` phase carrying a `cost_layers` list from
their A*/uniform-cost bound sweep. Add a solver by writing its bound-line parser and
calling `_assemble_cost_layers`, not by re-deriving the layer arithmetic — that keeps
cross-solver timing comparisons fair by construction.

**Phase `duration` is ground truth; parsed components are a subset of it.** They need
not sum to it (JVM start-up, spawn, plan writing are real but unattributed). An engine
reports grounding as `translate_time` (FD/SymK) XOR `grounding_time` (ENHSP), never
both; `up_compile_time` is additive on the UPF `--build` route only.

**Run success is decided by `final_status`, not by exit codes.** `RunSummary.finalize`
treats `failed` / `launch-failed` / `hlim` as failures regardless of clean subprocess
exits — a solver that runs fine but yields no solution is not a success. Best-effort
phases (`visualisation`, `export`) are excluded from the success decision, and
non-solving phases (`translation`, `modelling`, `visualisation`) may not set
`solution_found`. `hlim` (horizon budget exhausted) is deliberately distinct from a
planner's proven unsat: a bounded CP model *cannot* prove unsatisfiability.

**Consumer-owned data goes in `extra`, never a declared field.** `RunSummary` and
`PhaseResult` each carry an `extra` dict for data the core cannot interpret. Declaring
such a field would grow a consumer's vocabulary into a domain-agnostic record — which
is what `canonical_score` (puzznic's replay scorer) did before it was removed. `extra`
is *flattened* into the serialised form and re-gathered on load, so the JSON is
identical to what a declared field produced and readers that index a saved summary by
key are unaffected. A core field wins a name collision. The gathering half also means
`from_dict` no longer raises `TypeError` on a key it doesn't know, so one consumer can
read another's summaries. For per-phase data, prefer `solver_stats` — that is what it
is for; `PhaseResult.extra` is for data about the phase rather than the solver run.

**Failure kinds are distinguished on purpose.** `SolverResult.failed` covers three
different outcomes — check `timed_out`, `interrupted`, and `launch_failed` before
treating a run as a genuine solver failure. A timed-out anytime solver may have banked
a solution worth salvaging. `launch_failed` (missing/non-executable binary) raises
`SolverExecutableError` to abort a horizon scan, since retrying to the horizon cap
cannot help. The runner follows its own rule: only a genuine failure logs at ERROR,
and `run_phase` records `returncode` / `timed_out` / `launch_failed` on the phase so
the outcome survives into the saved JSON.

## Tests

`tests/conftest.py` holds the stand-ins a test wires when it needs a seam:
`fixture_registry` (a `ModelRegistry` over `tests/fixtures/models.yaml`),
`wired_stdout_score`, `wired_timeout_score`, plus an autouse `reset_global_state`
that saves/restores the singleton so tests don't leak into each other.

Two rules for the fixture config: **never** point a test at a consumer's `models.yaml`,
and wire the fixture config rather than mocking the registry — that keeps the real
loader and accessors in the code path. Anything a test needs goes into
`tests/fixtures/models.yaml`, keeping its shape faithful to a real consumer config,
because that shape *is* the contract the loader implements.

`tests/test_import.py` is the domain-cleanliness guard: it asserts the core stays
unwired (registry raises, extractors return `None`, no baked-in UPF CLI). Changes that
make it pass by adding a default rather than by injection are going the wrong way.

## Docs

- `docs/injection-api.md` — the four seams, with a worked example of how
  puzznic-support wires them.
- `docs/solver-runner.md` — `SolverResult`, timeout vs duration, `run` / `run_phase` /
  `run_simple`.
- `docs/timing-model.md` — the phase model, per-solver stat reference, and the
  invariants parsers rely on. Read before changing a parser or adding a solver.
