# timing model

How the core records solver timing, how different solvers map onto one shared
shape, and the invariants the parsers rely on. Read this when changing a
solver-stats parser, adding a solver, or reasoning about the numbers a consumer
plots. This is the core-behaviour reference; a consumer's own summary/plot tools
build on it (see *what the core owns*, below).

The design goal is a *fair*, uniform timing decomposition across very different
backends (Savile Row + SAT/CP, Fast Downward, SymK, UPF, ENHSP) rather than any
single solver's native profile. Every backend is squeezed into the same shape.

## the phase model

A run is a `RunSummary` with a `phases` list of `PhaseResult`s. Each phase carries:

- `name` — e.g. `translation`, `solving`, `solving-horizon-3`, `visualisation`.
- `duration` — **wall-clock seconds**, the ground truth for that phase. Every
  finer-grained time is a *component* of some phase's duration; components need
  not sum to it (JVM start-up, process spawn, plan writing and other overhead are
  real but unattributed).
- `solver_stats` — a dict of parsed metrics (see per-solver reference). The
  finer-grained timings live here.

`solver_stats` is populated by `SolverStatsExtractor.extract_stats(solver, ...)`
(`solver_stats.py`), dispatched on the solver name detected from the command line
(`detect_solver_from_command`, matched on generic substrings like `upfplan`,
`savilerow`, `fast-downward`, `enhsp`). The Savile Row tailoring/solver split is
attached after the fact from the `.info` file.

## two decompositions

Timing is captured along two orthogonal axes; a run uses whichever apply:

1. **Coarse phase split** — the pipeline stages of a run (translation, solving,
   visualisation, ...), each a `phase` with a wall-clock `duration`. Always
   present, solver-agnostic.
2. **Per-"horizon" curve** — within solving, a sequence of increasing-difficulty
   steps. Two kinds, never mixed in one run:
   - a **Savile Row horizon scan** (a move-budget sweep: `solving-horizon-1`,
     `-2`, ... phases), and
   - a **search cost-layer sweep** for the planners (Fast Downward / SymK / ENHSP
     raise an A\*/uniform-cost bound `N`; each bound is a "horizon"), living as a
     `cost_layers` list on the single `solving` phase.

## preprocess components

The one-time "prepare" cost that sits **outside** the per-horizon curve —
grounding the task, building search machinery, compiling a UPF model. The core
parsers emit whichever of these apply; a consumer sums the relevant ones for its
own "preprocess" baseline:

| field | meaning | emitted by |
|-------|---------|------------|
| `translate_time` | translator/grounding wall-clock | Fast Downward, SymK |
| `grounding_time` | lifted→grounded step | ENHSP |
| `h1_setup_time` | heuristic setup (before search) | ENHSP (heuristic configs) |
| `search_setup_time` | search-relative time before the first cost bound | FD, SymK, ENHSP (optimising) |
| `up_compile_time` | UP model compilation (arrays/integers/usertype removal) | UPF `--build` route |

An engine reports its grounding as **either** `translate_time` (FD/SymK) **or**
`grounding_time` (ENHSP), never both, so they never double-count.
`up_compile_time` is additive on top for constructed UPF models.

## per-solver reference

What each backend emits, where it is parsed from, and the key `solver_stats`
fields. All times are seconds unless noted.

### Savile Row (Conjure / Essence Prime models)

- **Shape**: a horizon scan — one `solving-horizon-N` phase per move budget.
  Each horizon reaches the model as `letting <name> be N`, appended to the
  instance for every name in the model's `horizon-params` (or its solver's).
  These must be exactly the parameters the model declares, since Conjure
  rejects a `letting` for one it lacks. A config without the key gets the
  historical names, `noSteps` (Savile Row) or `maxMoves` + `horizon` (Conjure),
  so models written for those keep working. Only `letting` lines for those names
  are dropped from the instance first, not any line that mentions one.
- **Which Savile Row entry**: Conjure only refines and translates. Each horizon
  is tailored and solved by the Savile Row entry the `conjure` config names
  (`savilerow: <entry>`), and an Essence run's `--tune` picks among that
  entry's presets. A direct Savile Row scan uses the run's own solver. Without
  either, it is `kissat`, the name older configs use.
- **Split source**: Savile Row writes `<param>.info` per horizon;
  `_record_savilerow_times` (`solver_horizon.py`) reads `SavileRowTotalTime`
  (tailoring) → `savilerow_time` and `SolverTotalTime` (backend search) →
  `solver_time`, attaching them to the matching phase. Recorded for *every*
  horizon, so intermediate UNSAT horizons carry the split too.
- **The rest of the file**: those two are what the phase model needs, but the
  same `.info` carries the encoding size, the search counters, and the outcome
  flags. All of it is recorded verbatim on the phase under
  `solver_stats['savilerow_info']`, so a consumer never has to go back to the
  `.info` files — which matters because that would depend on the run's output
  directory rather than its summary, and so stops working once those files are
  cleaned up. `SolverStatsExtractor.extract_savilerow_info(path)` is the parser
  (keys verbatim, values coerced to int/float, `{}` for a missing file);
  `extract_savilerow_info_times` is the renaming subset that produces the two
  fields above.
- **Nested, not flattened, and deliberately not curated.** `solver_stats` is the
  core's normalised cross-solver vocabulary; `savilerow_info` is one solver's own
  keys in its own spelling, so nesting keeps CamelCase off the snake_case
  namespace. It is verbatim because the backends do not agree on which keys
  exist — measured on one model:

  | key | minion | ortools | gecode | chuffed | kissat |
  |---|:-:|:-:|:-:|:-:|:-:|
  | `SavileRowTotalTime` `SolverTotalTime` `SolverSatisfiable` `SolverTimeOut` `SavileRowTimeOut` `SavileRowClauseOut` | ✓ | ✓ | ✓ | ✓ | ✓ |
  | `SolverNodes` | ✓ | — | ✓ | ✓ | ✓ |
  | `SolverFailures` `SolverTotalWallTime` | — | ✓ | ✓ | ✓ | — |
  | `SolverSetupTime` `SolverSolveTime` `SolverSolutionsFound` | ✓ | — | — | — | — |
  | `SATVars` `SATClauses` `SolverMemOut` | — | — | — | — | ✓ |

  Any allow-list would already be wrong, and would need extending for every new
  backend or Savile Row version. Note `SolverSatisfiable` is one of the six every
  backend emits: it distinguishes a **refuted horizon** from one that gave up —
  which is *not* problem-level unsat, since a bounded model cannot prove that
  (see `hlim`, above).
- **Best-effort**: a hard timeout can kill Savile Row before it writes `.info`,
  leaving the split absent (`None`).

#### Backends

Savile Row drives five backends, chosen by a solver config's `backend:` key and
described by one row each in `CommandBuilderMixin.SAVILEROW_BACKENDS`. A config
with no `backend:` key is the SAT arm, which is what every pre-existing config
relies on. The row supplies three things that vary and nothing that does not; the
arg-set key is *derived* as `<backend>_args`, so it needs no row.

| `backend:` | arg-set key | solver's own time limit | binary flag |
|---|---|---|---|
| *(absent)* / `sat` | `sat_args` | `--time=N` (s) | `-satsolver-bin` |
| `minion` | `minion_args` | *(none)* | `-minion-bin` |
| `ortools` | `ortools_args` | `--time_limit=N` (s) | `-fzn-bin` |
| `gecode` | `gecode_args` | `-time N` (**ms**) | `-gecode-bin` |
| `chuffed` | `chuffed_args` | `--time-out N` (**ms**) | `-chuffed-bin` |

Savile Row's own `-timelimit` is always added, always in seconds, and covers
translation plus solving. The solver's limit is passed through `-solver-options`
in addition, so the solver stops itself and reports rather than being killed with
nothing to show. Repeated `-solver-options` accumulate, so a tune preset may
carry its own without displacing the timeout.

**The units are a trap.** Gecode and Chuffed take milliseconds and neither says
so in its flag name. An unscaled value does not fail loudly — a 30s budget
becomes 30ms and the solver gives up instantly, which reads as a hard instance.
`timeout_scale` in the table is what converts; a config overriding
`solver_timeout_option` changes the flag's *name* only, since the unit belongs to
the solver rather than to the spelling.

**Only the SAT arm records intermediate objectives.** It is the one configuration
where Savile Row runs the optimisation loop itself; every other backend optimises
internally, leaving nothing for `-record-intermediate-objective-values` to see.
So for a non-SAT backend `_generate_objective_file_path` returns `None` and no
`.obj` file is named or cleared, and `_check_continue_scan` falls back to the raw
objective read out of the *solution* by the injected extractor. A consumer that
wires only the objective-file route will find continue-scan stops at the first
solving horizon on those backends instead of improving — wire
`set_raw_objective_from_solution` to use them for optimisation models.

#### The three time limits, and how they relate

A Savile Row run is bounded three times over, and the three are **not**
interchangeable. Getting the relationship wrong is silent, so it is worth
stating once:

| limit | set by | what it actually bounds |
|---|---|---|
| `-timelimit T` | `build_savilerow_command(timeout=T)` | tailoring. Nominally translation *plus* solving, but Savile Row stops binding it once a solver is running |
| the solver's own (`--time=S`, …) | `build_savilerow_command(solver_timeout=S)` | search |
| the external kill | `SolverRunner(timeout=…)` | the whole process tree |

**The first two are consecutive, so they add.** Savile Row tailors, and only
then launches the solver, so the wall clock the pair permits is `T + S` — not
`max(T, S)`. Left unsplit (`S` defaulting to `T`) that is `2T`, which is why an
external kill set at `T` can fire while the toolchain is still inside the limits
it was given.

**An early kill costs every statistic, not just the tail.** Savile Row
accumulates `SATVars`, `SATClauses`, `SolverNodes` and the
`SavileRowTotalTime` / `SolverTotalTime` split in memory and writes the `.info`
once, after the solver returns. Kill the tree and the output directory holds an
`.eprime` and perhaps an `.eprime-minion`, never an `.eprime-info`, so
`extract_savilerow_info` has nothing to read and the run cannot say even whether
it reached the solver.

So size the external kill with `savilerow_backstop(T, S)` — `T + S` plus grace
for the flush — and it comes to mean "the toolchain ignored its limits", a
genuine fault rather than an ordinary and lossy way for a run to end.

The built-in horizon scans do this per horizon when asked:
`SavileRowSolver.solve(..., backstop=True)` and `ConjureSolver.solve(...,
backstop=True)` kill each horizon at `savilerow_backstop(R)`, where `R` is the
budget that horizon's command was built with. Without it, each horizon is
killed at the runner's own timeout, as before. It is opt-in because a scan
killed at the backstop can overrun the runner's timeout by up to about that
timeout again, which would change the run times of an existing consumer.

**To hold a run to a wall-clock budget, shrink the pair, not the backstop.**
Give the solver `S = T - (what tailoring will cost)`. The core picks no reserve
policy: a horizon scan can estimate from the previous horizon's
`SavileRowTotalTime`, a single-shot run has nothing to estimate from, and only
the caller knows which it is.

#### Verdicts: refuted vs gave up

`No solution found.` is what Savile Row prints **both** when the solver proved
there is none and when it stopped at its own limit without deciding, and
`solution_found` is False for both. The first is a result; the second
establishes nothing. `SolverStatsExtractor.savilerow_verdict` separates them and
`_record_savilerow_times` records the answer on the phase as `verdict`:

| verdict | evidence | meaning |
|---|---|---|
| `sat` / `unsat` | `SolverSatisfiable` present | decided (`proven_optimal` too, on optimisation models) |
| `unknown` | absent, with `verdict_reason` | `solver-limit`, `tailoring-limit`, `memory`, `tailoring-memory`, `encoding-limit`, or `unexplained` |
| `error` | `SavileRowUncaughtException` | Savile Row itself failed |
| `killed` | no `.info` at all | recorded by the caller, from the phase's `timed_out` / `launch_failed` |

Decidedness keys on `SolverSatisfiable` being **present**, deliberately not on
the solver having run: Savile Row can settle an instance during tailoring and
never invoke a solver, leaving `SolverTotalTime: 0` beside a perfectly good
verdict. Testing "did the solver run?" reports every one of those as undecided.

**Each limit names the process it bound.** `solver-limit` / `tailoring-limit`
are the two time limits (see above), and `memory` / `tailoring-memory` are the
same split for the heap: `SolverMemOut` is the backend, `SavileRowOutOfMemory`
the JVM running Savile Row. The front-end memory-out is `unknown` and not
`error` for the same reason its timeout is — a resource cap stopped the run
before it decided; nothing malfunctioned. It is checked **before** the verdict,
and before `SavileRowUncaughtException`: one heap exhaustion sets both flags,
because the `OutOfMemoryError` reaches Savile Row's top-level handler, and the
cause is a more useful report than the symptom. `error` is then what is left
when Savile Row has no account of what went wrong.

You will mostly see this flag if your limits are **in band**. A harness that
caps memory externally (`runsolver` and friends) kills the JVM before it reaches
its own `-Xmx`, so Savile Row never writes the flag and you get a killed process
with no statistics instead — the same trade the time limits make. Bounding a run
from the inside buys diagnostics that killing it from outside destroys.

**A horizon's `unsat` is about the bound, not the problem.** It means "not at
this horizon", never "not at all" — refuting horizons 1..N says nothing about
N+1. Only the registry knows which models decide the problem itself
(`get_proves_unsat`), which is why the run-level `outcome` is derived in
`RunSummary.finalize` and never aggregated from these.

### Fast Downward / SymK (PDDL models)

- **Shape**: not a horizon scan — one `solving` phase whose A\* (blind) / symbolic
  uniform-cost search sweeps increasing cost bounds, printing a timestamped line
  per bound (`[t=Ts] f = N` for FD, `[t=Ts] BOUND: N` for SymK). SymK routes
  through the FD extractor (same driver, same output).
- **Parsed** (`extract_fast_downward_stats`, `extract_search_cost_layers`):
  `translate_time`, `search_setup_time` (time to the first bound ≥ 1),
  `cost_layers`, plus `plan_length`/`plan_cost`,
  `expanded/evaluated/generated_states`, `search_time`, `peak_memory_kb`, ...
- **Timeout**: the final, cut-off layer is bounded by `end_hint = duration -
  translate_time` (the search-relative kill time), so the blown-up deepest bound
  does not collapse to ~0.
- **Its own limit, and the backstop above it**: `--search-time-limit T` is a CPU
  rlimit on the search alone, while the runner's watchdog counts wall clock over
  translation plus search. A watchdog at `T` therefore always fires first, and
  the run loses the exit 23 and the final statistics block it would have
  written. Size the watchdog with `fd_backstop(T, translate_allowance)`, which
  is `T`, plus translation, plus `FD_EXIT_GRACE`, and pass it as
  `FastDownwardSolver.solve(..., watchdog=...)`. The runner's own timeout stays
  the in-band limit. Then an external kill once again means the planner ignored
  its limit. As with Savile Row, this is a
  backstop and not a budget: to hold a run to a wall-clock `T`, shrink the search
  limit.

#### Verdicts: proof, limit, or rejection

Fast Downward answers through its **exit code** (`driver/returncodes.py`), so
`run_phase` is handed `FD_ANSWER_RETURNCODES` and a proof is not logged as a
failed command. `SolverStatsExtractor.fd_verdict` reads the code, plus the log
where the code is ambiguous, into the same vocabulary as the Savile Row verdict.
`FastDownwardSolver` records it on the `solving` phase:

| exit | verdict | `verdict_reason` |
|---|---|---|
| 0–3 (plan; 1–3 then hit a limit) | `sat` | |
| 10, 11 | `unsat` | (the planner's own claim) |
| 12, see below | `unsat` / `unknown` | `exhausted`, or `search-limit` / `bounded` / `portfolio` / `incomplete` |
| 13 (26.6+) | `unknown` | `bounded` |
| 20 / 21 | `unknown` | `translate-memory` / `translate-limit` |
| 22, 24 / 23 | `unknown` | `memory` / `search-limit` |
| 34, 37 | `error` | `unsupported` (`unknown`/`portfolio` for an anytime portfolio) |
| 31, 33, 36 | `error` | `input-error` |
| 30, 32, 35, a signal | `error` | `crash` |
| killed by the runner | `killed` | |

**Exit 12 is ambiguous by construction.** Before 26.6 the planner exits
`found_solution() ? 0 : 12` however the search ended, so the log decides:

- `Time limit reached. Abort search.` (the search's `max_time=`) gives
  `search-limit`.
- `Completely explored state space -- no solution!` gives `unsat`/`exhausted`
  only for blind A* (below). It gives `bounded` instead if the search has a
  `bound=`, `unsafe-pruning` if an evaluator's dead ends are unreliable on this
  task, and `unverified-search` for any other search. If the run's tune
  declares `proves-unsat: true` and exactly its `search:` ran, that search
  counts too, as `exhausted-declared`. The exhaustion line looks the same either way, so the search is read
  from the driver's `search command line string`. That line has aliases
  expanded and `--evaluator` predefinitions included. If the line is missing,
  the search is read from the argv's `--search`. The translator's
  `Generating unsolvable task` lands here too, since the search then exhausts a
  trivially false goal.
- SymK prints no exhaustion line. Its last `BOUND: L < U` line, with `L ≥ U`, is
  `exhausted` when `U` is infinity (`2147483647`) and `bounded` otherwise.
- A portfolio's 12 is always `portfolio`. It can mask its components'
  time-outs, and its exhaustion line may come from a component that pruned
  unsafely.
- Anything else is `incomplete`: EHC giving up, or a 26.6 search that the
  planner itself judged incomplete (`Search terminated without finding a
  plan!`).

**`exhausted` is this library's reading, not the planner's.** It is sound only
if the search is complete and prunes nothing it should not. So it is given
only on an allowlist, which today is A* with the blind heuristic. Every call in
the logged search must be `astar` or `blind`; keyword options such as
`cost_type=` don't matter. A* is complete on a finite state space (Hart, Nilsson
& Raphael 1968), and blind never returns infinity, so it never prunes a state
from which the goal is reachable. An empty open list therefore proves there is
no plan (cf. Eriksson, Röger & Helmert, ICAPS 2017, which treats the expanded
states as an unsolvability certificate). Anything else needs a soundness
argument of its own: a heuristic that can return infinity, partial-order or
symmetry pruning (`pruning=`), another search algorithm, a new evaluator. So
these give `unknown`/`unverified-search`, even where they are in fact safe
(`lmcut`, the PDB heuristics). Widening the list is a deliberate change, made
one checked component at a time; the reasoning and the evidence for each
component are in [`unsat-soundness.md`](unsat-soundness.md).

Within that, the known-unsafe evaluators are named as such. Before 26.6, a search prunes
every state an evaluator calls a dead end, whether or not
`dead_ends_are_reliable()`. So a relaxation heuristic on a task with axioms,
where infinity is not a dead end, "exhausts" without proving anything. The log
does show the pruning half, and `fd_verdict` checks it as 26.6 does:

| evaluator in the logged search | dead ends unreliable when |
|---|---|
| `ff`, `add`, `hmax` | `Translator axioms:` is non-zero, or absent |
| `cg`, `cea` | always |
| `hm`, `lmcount` / `landmark_sum` | always: they are unreliable on tasks with conditional effects, which the log does not show |

Any such evaluator gives `unknown`/`unsafe-pruning`. From 26.6 the planner
checks it itself (upstream issue1201): a safe exhaustion exits 11 and an unsafe
one exits 12 with no exhaustion line. So `exhausted` only ever comes from older
planners and SymK. The registry's `proves-unsat`, which a tune preset can
switch off, still decides whether `exhausted` becomes a run-level `unsat`.

**The planner's own time limit is a timeout.** `search-limit` and
`translate-limit` make `solve()` raise `TimeoutError`, exactly as a watchdog
kill does, and so does an anytime portfolio that ends without a plan. Every other
no-plan ending returns `None`, and the run's `outcome` is derived from the
verdict.

### UPF (via the consumer's UPF CLI; PDDL or constructed models)

- **Shape**: the UPF CLI runs a UPF engine (Fast Downward / SymK / ENHSP) and
  streams the engine's search log to **stderr** (kept off stdout so the plan stays
  clean for the solution); `run_phase` tees it live and captures it.
- **Parsed** (`extract_upf_stats` over `stdout + stderr`): plan/score from the UPF
  CLI's own `Steps:` / `Score:` lines, then the engine's `cost_layers` +
  `translate_time` (FD/SymK) **or** ENHSP's `grounding_time` + `cost_layers`
  (whichever engine ran), plus `up_compile_time` on the `--build` route.
- **Timeout**: unlike the direct routes, the UPF path passes **no** `end_hint`
  (the phase wall-clock includes UP compilation, off the engine's search
  timeline), so a timed-out final layer falls back to the last printed timestamp.

### ENHSP (direct, or via UPF)

- **Aggregate times** (milliseconds → seconds; `Grounding Time:` alone has no
  `(msec)` suffix): `grounding_time` (the preprocessing analogue of FD's
  translate; often dominant), `h1_setup_time` (heuristic configs only),
  `search_time`, `heuristic_time`, `planning_time`, `expanded_states`,
  `evaluated_states`.
- **Cost-layers depend on the search mode**:
  - *optimising* prints a **timestamped** `f(n) = N (... Time: T)` progression, so
    it yields `cost_layers`; the sweep ends at ENHSP's reported `Search Time`.
  - *satisficing* prints ` g(n)= G h(n)= H` milestones with **no timestamps**, so
    those runs record the grounding baseline + aggregate times but **no**
    `cost_layers` — correctly, since there is nothing honest to time them by.
- **Direct vs via-UPF**: the direct route passes `end_hint = duration -
  grounding_time`; the UPF route reuses the parser but passes no end hint.

#### Verdicts: ENHSP exits 0 for almost everything

ENHSP 0.21.0 exits 0 after a plan, after a proof, and after a run that failed to
load its input. A missing domain file prints `Domain parsed`, then
`Unsolvable Problem`, and exits 0. The exception goes to **stderr**. So
`SolverStatsExtractor.enhsp_verdict` reads stdout and stderr, and
`EnhspSolver` records the result on the `solving` phase, first match wins:

| evidence | verdict | `verdict_reason` |
|---|---|---|
| `Problem Solved` | `sat` | |
| `java.lang.OutOfMemoryError` (exit 1) | `unknown` | `memory` |
| a logged exception (`SEVERE:`, `Exception in thread`) | `error` | `crash` |
| `Some Syntax Error` (exit 255) | `error` | `input-error` |
| any other non-zero exit | `error` | `crash` (signal) / `unexplained` |
| `Problem unsolvable`, with `-anytime` / `-autoanytime` | `unknown` | `anytime` |
| `Problem unsolvable`, with `-dl` | `unknown` | `bounded` |
| `Problem unsolvable`, helpful actions (`sat-hmrph`, `sat-hmrphj`; or `-ha true` / `-ht true` with no `-planner`) | `unknown` | `incomplete` |
| `Problem unsolvable`, a verified search: `blind` under `wastar` or `gbfs`, with AIBR preprocessing off (`-planner opt-blind`, or `-dap`) | `unsat` | `exhausted` |
| `Problem unsolvable`, the exact command of a tune declaring `proves-unsat: true` | `unsat` | `exhausted-declared` |
| `Problem unsolvable`, any other search | `unknown` | `unverified-search` |
| `Unsolvable Problem` (grounding returned nothing) | `unknown` | `grounding` |
| none of these | `error` | `unexplained` |

A `-planner` preset overrides `-h`, `-s`, `-ha` and `-ht`, as in ENHSP's own
`setPlanner`. Why only that search counts, and what checking it took, is in
[`unsat-soundness.md`](unsat-soundness.md). As with Fast Downward, `exhausted` becomes a run-level `unsat` only through
`proves-unsat`.

**Via UPF, the verdict needs the result file.** The UPF statuses are wrong in
both directions: `up_enhsp` reports any exit 0 without a plan as
`UNSOLVABLE_PROVEN`, and `up_fast_downward` reports any exit 12 as
`UNSOLVABLE_INCOMPLETELY`. So the core never reads them. A CLI that writes the
result file (`docs/injection-api.md`) names its engine. The core then runs that
engine's own reader on the engine log, under the same rules as a direct run
(`upf_result_stats`): the blind-A* allowlist for Fast Downward, the
configuration checks for ENHSP. Without the file, a UPF run records no
verdict.

## invariants and conventions

- **`duration` is ground truth; components are a subset.** Never assume the
  components sum to the phase wall-clock. The unattributed remainder (JVM start,
  spawn, I/O) is expected.
- **One grounding field per engine.** `translate_time` (FD/SymK) XOR
  `grounding_time` (ENHSP). `up_compile_time` is additive and only on UPF
  `--build`.
- **Shared cost-layer assembly.** FD/SymK and ENHSP differ only in how they read
  per-bound timestamps and the end marker; the layering (setup = first bound's
  timestamp, each layer's duration = gap to the next bound or to the end, only
  the deepest *solved* bound bears the solution) lives once in
  `_assemble_cost_layers`. Add a solver by writing its bound-line parser and
  calling that, not by re-deriving the arithmetic — it keeps the cross-solver
  comparison fair by construction.
- **Bounds need not start at 1.** A UPF score model's first reached bound may be a
  score value; `search_setup_time` is the first *tracked* bound's timestamp, so
  time below it counts as setup.
- **Split is Savile-Row-only and best-effort.** `savilerow_time` / `solver_time`
  are `None` for planners and for any Savile Row horizon whose `.info` was
  missing.
- **In-band limits add; the external kill must sit above their sum.** See "the
  three time limits" above. `savilerow_backstop` derives it from the same
  numbers the builder gets.
- **A clean exit is not an answer.** A solver given its own limit stops *itself*
  and exits 0, so every phase of a fruitless run can succeed. Run success is
  decided by `outcome`, never by exit codes.

## outcomes

`final_status` answers two questions at once — what was learned, and why the run
stopped — so `RunSummary.finalize` records them separately. `final_status` keeps
its meaning; these sit alongside it.

| field | values |
|---|---|
| `outcome` | `solved` / `unsat` / `unknown` / `error` |
| `stop_reason` | `complete` / `time` / `horizons` / `interrupt` / `error` |
| `outcome_reason` | `setup` / `launch` / `no-verdict` / the solving phase's `verdict_reason`, or empty |

`stop_reason` is `error`, not `complete`, whenever a completed status ends in
`error`: a crash, or a run that never handed its solver the input.

`success` is `outcome in {solved, unsat}`: **a run succeeded if it produced a
definitive answer in the budget it was given.** A proof that no solution exists
answers the question as surely as a solution does, even though the planner that
produced it exits non-zero.

`unsat` requires the caller to pass `finalize(..., proves_unsat=True)`, normally
from the registry's `get_proves_unsat`. The core cannot tell from the output — a
bounded CP model and a complete planner print the same thing — and left False no
run is ever concluded unsat, which understates rather than overstates what was
proved. A status that stopped early (`timeout`, `hlim`, `stopped`) is never
`unsat` however complete the model.

For a planner, being able to prove depends on the **search** as well as the model.
`get_proves_unsat(model, tune=..., solver_name=...)` is the model's
`proves-unsat` AND the effective tune's. A tune preset may carry
`proves-unsat: false` (for example an incomplete search such as EHC, or an
anytime portfolio), which takes the claim away. A tune can never grant it to
a model that cannot prove. An explicit `proves-unsat: true` on a tune does
something else as well: it vouches for that tune's search, so its exhaustion
counts even when it is not on the built-in allowlist (`exhausted-declared`,
see [`unsat-soundness.md`](unsat-soundness.md)). `finalize` keeps the value on the summary as
`proves_unsat`, and a later `finalize` that passes None reuses it. So a
re-finalize that folds in a late phase, as `horizon_utils` does, cannot drop it.

**A `solving` phase's verdict decides a completed run.** `failed` is the status
of both a planner's proof and its crash, so `proves_unsat` alone would promote
every crash of a complete model to `unsat`. Where the `solving` phase recorded a
`verdict` (Fast Downward, SymK and ENHSP do, see above), it decides:

| verdict | `proves_unsat` | outcome |
|---|---|---|
| `unsat` | True | `unsat` |
| `unsat` | False | `unknown`: the search ran out, but cannot prove |
| `unknown` / `killed` | either | `unknown` |
| `error` | either | `error` |
| none | True | `unknown`: it may have been a proof, or a crash |
| none | False | `error` |

**No verdict, no `unsat`.** Without a verdict, a completed run's status is all
there is to go on, and `failed` cannot tell a proof from a crash, so the run is
`unknown` however complete the model. A UPF run without a result file is the
usual case, and so is any consumer that drives a solver itself. To let such a run conclude `unsat`,
record a verdict on its `solving` phase (`phase.solver_stats['verdict']`).
Per-horizon verdicts
(`solving-horizon-N`) are about one bound each and never decide the run.

**A failed setup phase is an error, before any verdict is read.** When a phase
that prepares the solver's input fails other than by the timeout, no search
ran. These phases are the consumer's `translation`, Conjure's `modelling`, and
`translate-param-N`. A completed run with such a failure is `error` with
`stop_reason` `error`, whatever `proves_unsat` says. Its `outcome_reason` is
`setup`, or `launch` if the phase's binary could not be started. A
`translate-param-N` that fails mid-scan counts too: the scan ended on a
failure, not on a limit. Phases are classified by name, so the rule also
applies when an older saved summary is re-finalized.

## what the core owns

| concern | location |
|---------|----------|
| per-solver stat parsers, cost-layer assembly, dispatch | `solver_stats.py` |
| per-run Savile Row verdict (refuted vs gave up) | `solver_stats.py` (`savilerow_verdict`) |
| per-run Fast Downward / SymK verdict (exit code + log) | `solver_stats.py` (`fd_verdict`) |
| per-run ENHSP verdict (stdout + stderr) | `solver_stats.py` (`enhsp_verdict`) |
| Savile Row `.info` split recording (per horizon) | `solver_horizon.py` (`_record_savilerow_times`) |
| stats attached to a phase after each solve | `solver_runner.py`, `solver_horizon.py` |
| the phase model (`RunSummary` / `PhaseResult`), outcome and success | `run_summary.py` |
| relating the in-band limits to the external kill | `command_builders.py` (`savilerow_backstop`, `fd_backstop`) |

Turning these per-phase stats into a unified per-horizon curve for display, a
single "preprocess" baseline, and plots is the **consumer's** job — it reads the
`solver_stats` fields documented above from the `RunSummary`.
