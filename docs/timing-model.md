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
- **Split source**: Savile Row writes `<param>.info` per horizon;
  `_record_savilerow_times` (`solver_horizon.py`) reads `SavileRowTotalTime`
  (tailoring) → `savilerow_time` and `SolverTotalTime` (backend search) →
  `solver_time`, attaching them to the matching phase. Recorded for *every*
  horizon, so intermediate UNSAT horizons carry the split too.
- **The rest of the file**: those two are what the phase model needs, but the
  same `.info` carries the encoding size and search counters (`SATVars`,
  `SATClauses`, `SolverNodes`, `SolverSatisfiable`, ...).
  `SolverStatsExtractor.extract_savilerow_info(path)` returns every `Key:Value`
  pair with keys verbatim and values coerced to int/float, for a consumer
  reporting those columns; `extract_savilerow_info_times` is the renaming subset
  of it that the core itself uses.
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

## what the core owns

| concern | location |
|---------|----------|
| per-solver stat parsers, cost-layer assembly, dispatch | `solver_stats.py` |
| Savile Row `.info` split recording (per horizon) | `solver_horizon.py` (`_record_savilerow_times`) |
| stats attached to a phase after each solve | `solver_runner.py`, `solver_horizon.py` |
| the phase model (`RunSummary` / `PhaseResult`) | `run_summary.py` |

Turning these per-phase stats into a unified per-horizon curve for display, a
single "preprocess" baseline, and plots is the **consumer's** job — it reads the
`solver_stats` fields documented above from the `RunSummary`.
