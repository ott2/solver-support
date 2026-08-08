# solver runner

`solver_runner.py` runs a solver as a subprocess and reports what happened. It
exists because a bare `subprocess.run` cannot answer the question the pipeline
actually asks — *did this solver fail, or did it run out of time, or did the user
stop it?* — and because a solver that is killed must not leave a process tree
behind. It handles:

- timeouts, with the whole process tree torn down (via `psutil`);
- signal handling, so an interrupted run shuts down cleanly rather than orphaning
  a solver;
- separate stdout/stderr capture (the plan stays clean on stdout while an engine
  logs its search to stderr);
- structured logging to console and file;
- phase tracking, feeding the `RunSummary` (see [`timing-model.md`](timing-model.md)).

## SolverResult

The complete result of one execution — the raw data plus the status properties
the pipeline branches on.

```python
SolverResult(command, returncode, stdout, stderr, duration,
             timed_out=False, interrupted=False)
```

| field | meaning |
|-------|---------|
| `command: List[str]` | the command and arguments that ran |
| `returncode: int` | process return code (0 for success) |
| `stdout: str` / `stderr: str` | captured output, kept separate |
| `duration: float` | **actual** seconds the command ran |
| `timed_out: bool` | it was killed for exceeding the timeout |
| `interrupted: bool` | it was stopped by a signal |

### timeout vs duration

Three easily-confused numbers:

- `default_timeout` (a `SolverRunner` parameter) — the *limit*;
- `duration` (a `SolverResult` field) — the *actual* time it ran, whether it
  finished or was killed;
- `timed_out` (a `SolverResult` field) — whether that limit was hit.

```python
runner = SolverRunner(default_timeout=10.0)

runner.run(['sleep', '3'])    # duration ~3.0,  timed_out False, returncode 0
runner.run(['sleep', '15'])   # duration ~10.0, timed_out True,  returncode -1
```

`duration` is ground truth for the phase, and every finer-grained solver time is
a component of it — see the invariants in [`timing-model.md`](timing-model.md).

### properties

- `success: bool` — completed with return code 0, no timeout, no interruption.
- `failed: bool` — the inverse; useful as a direct `if` on a run.

Note that `failed` covers three *different* outcomes, and the pipeline usually
cares which: check `timed_out` and `interrupted` before treating a run as a
genuine solver failure. A timed-out anytime solver may still have banked a
solution worth keeping.

The runner applies that rule to its own logging: a timeout or an interrupt is
recorded at INFO (`Command did not complete`), and only a solver that ran and
exited non-zero is logged as `Command failed` at ERROR. A launch failure logs its
own error before returning. So a consumer whose timed-out run *is* the result -
a benchmark baseline, say - gets no spurious error rows.

`run_phase` carries the same distinction into the persisted summary: the phase
records `returncode`, `timed_out` and `launch_failed` alongside `success`, so the
outcome can be reconstructed from a saved run without re-reading the log.

- `__str__()` renders `SolverResult({command}) -> {STATUS} (rc={returncode},
  t={duration}s)` with `STATUS` one of `SUCCESS` / `FAILED` / `TIMEOUT` /
  `INTERRUPTED`:

```
SolverResult(echo hello) -> SUCCESS (rc=0, t=0.01s)
SolverResult(sleep 10) -> TIMEOUT (rc=-1, t=5.00s)
SolverResult(ls /nonexistent) -> FAILED (rc=1, t=0.01s)
```

## usage

```python
from solver_support import SolverRunner

runner = SolverRunner(default_timeout=30.0)
result = runner.run(['savilerow', 'model.eprime', 'problem.param'])

if result.timed_out:
    print(f"solver timed out after {result.duration:.1f}s")
elif result.interrupted:
    print("solver was interrupted")
elif result.failed:
    print(f"solver failed with return code {result.returncode}: {result.stderr}")
else:
    print(f"solved in {result.duration:.2f}s")
```

Two variants of the same call:

- `run_phase(phase_name, command, expected_outputs=..., horizon_number=...)` —
  what the pipeline uses. It runs the command *and* records a `PhaseResult` on
  the `RunSummary`, with the parsed solver stats attached; use it for anything
  whose timing should appear in the summary.
- `run_simple(*command_parts)` — the terse variant, returning `result.failed`
  (truthy on failure) for call sites that only branch on success and want no
  phase recorded.

Two optional controls, both defaulting to the historical behaviour:

- `cwd=` on `run` / `run_phase` — the subprocess's working directory. Omitted, it
  inherits the caller's, which is what the `Solver` classes rely on (they are
  documented to run after a chdir, with paths relative to it). A consumer that
  would rather not chdir a whole process can place each subprocess instead.
- `log_level=` on the `SolverRunner` constructor — console verbosity for this
  runner. `global_state.log_level()` can only ever *raise* verbosity (it floors
  at WARNING), so this is the way to make a runner quieter. The file handler
  captures everything regardless.

## tests

`tests/test_solver_runner.py` — execution outcomes (success / failure / timeout /
interruption), process-tree and child-process cleanup, signal handling, logging
setup, environment and working-directory handling, output edge cases (long
output, special characters, empty commands), and integration tests that run real
system commands.

Signals are *not* handled here: `SolverRunner` runs a command and reports, while
installing the handler and deciding what an interrupt means for the run as a
whole belongs to the consumer's top-level runner.
