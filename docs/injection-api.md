# injection API

`solver_support` ships **unwired**: a bare `import solver_support` gives a working
pipeline that knows nothing about any problem domain. It records whatever
objective a solver emits but cannot interpret it, resolves no model registry, and
routes warnings to its own package logger. A **consumer** supplies the missing
domain policy once, at its own import time, through the injection points below.

Every seam follows the same shape: a module-level default, a `set_*` setter, and
a stable wrapper (or constructor argument) that reads the current value **at call
time**. So wiring may happen any time before first use, and the core never imports
the consumer — the dependency arrow points one way, consumer → core.

## the four seams

### 1. model registry paths (`solver_support.model_registry`)

The loader is generic; the model content and its packaging are the consumer's.

| setter | supplies |
|--------|----------|
| `set_default_config_path(path)` | the consumer's `models.yaml` (a `Path` / str) |
| `set_resource_root(root)` | root that model-file paths in the config resolve against |
| `set_user_config_finder(finder)` | a **callable** returning an optional per-invocation user-config `Path` |

`set_user_config_finder` takes a *finder*, not a fixed path, so cwd-relative
candidates bind when the registry is constructed rather than at import. Until a
default config path is registered, constructing `ModelRegistry()` raises
`ModelRegistryError` rather than guessing a consumer's config.

To start a config from scratch, `write_starter_config(path)` writes out the
commented starter registry that ships as package data (`starter_config_text()`
returns its text). Writing it out wires nothing. The registry still raises until
`set_default_config_path` names it, so a starter file on disk can never change
what an unwired core does.

### 2. objective extraction (`solver_support.objective`)

The core asks for a solution's objective at a few points but does not know how a
domain encodes it. Register an extractor for each route the consumer uses; an
unregistered route yields `None` (the standalone core simply records no score).

| setter | extractor signature | used for |
|--------|--------------------|----------|
| `set_reported_score_from_solution(fn)` | `fn(solution_file) -> int \| None` | reported score read from a solution file |
| `set_reported_score_from_stdout(fn)` | `fn(stdout) -> int \| None` | reported score printed on solver stdout |
| `set_reported_score_from_timeout_solution(fn)` | `fn(solution_file) -> int \| None` | a salvaged, possibly-truncated timeout solution |
| `set_raw_objective_from_solution(fn)` | `fn(solution_file) -> int \| None` | the **raw** (unscaled) objective the horizon scan compares between horizons |

Keep the raw objective in model units — it drives horizon comparison — while the
reported extractors may apply any display scaling the consumer wants.

### 3. warning logger name (`solver_support.utilities`)

`warn()` routes through a logger named `_warn_logger_name` (default
`'solver_support'`). A consumer that configures its own named logger (with file /
console handlers) points `warn()` at it so these warnings share those handlers:

```python
from solver_support import set_warn_logger_name
set_warn_logger_name('my_runner')   # matches setup_logger('my_runner', ...)
```

### 4. UPF planner CLI (`solver_support.solver_upfplan.UpfPlanSolver`)

The generic UPF driver shells out to a CLI that performs the UPF
PDDLReader/construction and solve. There is **no** built-in default (the CLI is
consumer-provided). Inject it per instance, or override the class attribute:

```python
UpfPlanSolver(runner, solver_name='upffd', build_mode='upf',
              upfplan_command='my-upfplan')      # per instance (preferred)
# or, once:
UpfPlanSolver.UPFPLAN_COMMAND = 'my-upfplan'     # class-attribute override
```

`solve()` refuses with a clear `ValueError` if no command is configured, rather
than shelling out to a `None`-prefixed argv.

**The result file (opt-in).** Without it, the core learns how a UPF run went
only from the CLI's exit code (0 with a non-empty stdout is a plan) and text in
its output, and records no verdict. A CLI that can report its result declares
it with `result_file=True`, or `UPFPLAN_RESULT_FILE = True` on the class. The
core then appends `--result PATH` to the argv. It never passes the option
otherwise, because a CLI that does not know it would fail on it.

The CLI writes JSON to `PATH` twice: once as the engine is about to start, with
`"finished": false`, and again when solving ends, with `"finished": true`. The
keys are `solver_support.UPF_RESULT_KEYS`; every key but `schema` and `finished`
may be absent or null:

```json
{
  "schema": 1,
  "finished": true,
  "plan_found": false,
  "steps": null,
  "score": null,
  "engine": "fast-downward",
  "engine_command": null,
  "engine_returncode": null,
  "engine_status": "UNSOLVABLE_INCOMPLETELY",
  "times": {"read": 0.004, "compile": 0.215, "engine": 0.090}
}
```

The file reports facts, and the core judges the verdict from the engine's own
log, with the same `fd_verdict` / `enhsp_verdict` a direct run gets.
`engine_status` is recorded but never trusted: `up_enhsp` reports every run
that exits 0 without a plan as `UNSOLVABLE_PROVEN`. So the engine's log must
reach the CLI's stdout or stderr, as UPF's `output_stream` does.

- `engine` picks the reader. Names starting `fast-downward` or `symk` get
  `fd_verdict`, and names starting `enhsp` get `enhsp_verdict`. Any other
  engine gets no verdict.
- `engine_returncode` is optional. For Fast Downward the driver's own
  `search exit code:` line stands in for it. UPF does not pass ENHSP's exit
  code on, but ENHSP's endings are on its log.
- `engine_command` is the engine's argv, where the CLI knows it. ENHSP's
  configuration (`-planner`, `-ha`, `-anytime`) is read from it; Fast
  Downward's search comes from its own log.
- `times.read` and `times.compile` come before the engine starts. A run killed
  at the timeout uses them to bound its last cost layer.

With a finished file, `plan_found` decides whether the stdout is a plan, so
the exit code only says whether the CLI itself ran properly. If the file was
written but never finished, and the runner did not kill the CLI, the verdict
is `error` / `crash`. If the CLI was opted in but wrote no file, it is
`error` / `no-result`.

## worked example

Wire everything once, at the consumer's own import time — in its
`__init__.py`, so that every entry point and every test that imports the package
gets an identically configured core, and nothing has to remember to wire it:

```python
# in mypuzzle_support/__init__.py

# 1. scoring: register the objective extractors this domain uses. Grouping them
#    behind one wire_into_core() keeps the registration next to the parsing.
from . import mypuzzle_scoring
mypuzzle_scoring.wire_into_core()
```

```python
# in mypuzzle_support/mypuzzle_scoring.py
import re
from solver_support import (
    set_reported_score_from_stdout, set_raw_objective_from_solution,
)

def _score_from_stdout(stdout):
    m = re.search(r'^Score:\s*(-?\d+)', stdout, re.MULTILINE)
    return int(m.group(1)) if m else None

def wire_into_core():
    set_reported_score_from_stdout(_score_from_stdout)
    set_raw_objective_from_solution(_objective_from_solution)   # model units
```

```python
# back in __init__.py

# 2. registry packaging: where this consumer's models.yaml and model files live.
#    A finder (not a fixed path) for the user override, so cwd-relative
#    candidates bind when the registry is built rather than at import.
from pathlib import Path
from solver_support import (
    set_default_config_path, set_resource_root, set_user_config_finder,
)

_here = Path(__file__).parent
set_default_config_path(_here / 'config' / 'models.yaml')
set_resource_root(_here)
set_user_config_finder(lambda: next(
    (p for p in (Path.cwd() / 'models.yaml',) if p.is_file()), None))

# 3. warnings: funnel the core's warn() into this consumer's configured logger,
#    so they reach the same file and console handlers as its own messages.
from solver_support import set_warn_logger_name
set_warn_logger_name('mypuzzle_runner')
```

and inject the UPF CLI where the solver is constructed:

```python
UpfPlanSolver(runner, solver_name=solver, build_mode=build_mode,
              upfplan_command='mypuzzle-upfplan')
```

Each consumer registers its own scoring, its own `models.yaml`, its own logger
name and its own UPF CLI. Nothing above is built into the core, and the core
never imports the consumer.
