# When an exhausted search proves unsat

A planner that runs out of states without finding a plan prints a line like
Fast Downward's `Completely explored state space -- no solution!` or ENHSP's
`Problem unsolvable`. `fd_verdict` and `enhsp_verdict` read that line as
`unsat` / `exhausted`, a proof that the task has no plan, only when the search
that ran is on an **allowlist** of configurations whose soundness has been
checked. Every other exhaustion is `unknown` / `unverified-search`, or
`unsafe-pruning` where the configuration is known to be unsound. This note
records why the list is what it is, and what checking it took, so it can be
widened later one component at a time.

Two things are kept separate here. The *verdict* says what the search showed.
Turning it into a run-level `unsat` still needs the registry's `proves-unsat`
(see `timing-model.md`, "outcomes").

## The principle

An exhausted search is a proof of unsolvability if it **drops no state except**

1. a duplicate: a state already reached at no greater cost, whose cheaper copy
   is itself expanded or still queued; or
2. a true dead end: a state from which no goal is reachable.

Then every reachable state is eventually expanded, and an empty open list means
no reachable state satisfies the goal. The expanded states form an
unsolvability certificate: they contain the initial state and no goal state,
and every successor of one of them is either among them or a dead end
(Eriksson, Röger & Helmert, "Unsolvability Certificates for Classical
Planning", ICAPS 2017).

The **order** in which states are expanded does not matter for this. A*,
weighted A*, and greedy best-first search all qualify if they drop nothing
else. The order matters for optimality, which is a different question. A* is
the textbook case: on a finite state space it is complete (Hart, Nilsson &
Raphael 1968), so an empty open list means no plan.

So a configuration is checked by asking what it removes:

| removal | sound? |
|---|---|
| duplicate detection keyed on g | yes |
| a heuristic that returns ∞ (a "dead end") | only if its dead ends are reliable on this task |
| helpful actions / preferred operators used as pruning | no |
| partial-order or symmetry pruning | needs its own result |
| a cost or depth bound (`bound=`, `-dl`) | proves only "no plan within the bound" |
| preprocessing that removes actions before search | only if its reachability analysis over-approximates |
| a time or memory limit | not exhaustion at all |

The **blind heuristic** satisfies the second row trivially, because it never
returns ∞. That is why both allowlists start from it.

## Fast Downward

The allowlist is: every call in the logged `search command line string` is
`astar` or `blind`. Keyword options such as `cost_type=` and `verbosity=` are
ignored. Any other call, including `pruning=...` and `let(...)`, is unverified.
A `bound=` gives `bounded`. The log line is used in preference to the argv
because the driver logs aliases expanded and `--evaluator` definitions
included.

Before Fast Downward 26.6, a search prunes every state an evaluator calls a dead
end, whether or not `dead_ends_are_reliable()` says so. Evaluators known to be
unreliable keep the more specific reason `unsafe-pruning`:

| evaluator | unreliable when |
|---|---|
| `cg`, `cea` | always |
| `hm`, `lmcount` / `landmark_sum` | with conditional effects, which the log does not show, so always |
| `ff`, `add`, `hmax` | `Translator axioms:` non-zero or absent |

From 26.6 the planner checks this itself (upstream issue1201): a safe
exhaustion exits 11, which `fd_verdict` reads as `unsat` with no reason.

**Not yet checked:** FD's eager and lazy search code. By the principle above,
`eager_greedy([blind()])` would also be sound, but that needs FD's open list
and duplicate handling read first. The same goes for safe heuristics that can
return ∞, such as `lmcut` and the PDB family.

## ENHSP

Checked against the ENHSP-20 repository (`~/repos/enhsp`, commit `bb0415c`).
Its own source is only `src/main.java` and `src/planners/ENHSP.java`. The
search engines and heuristics are in the bundled `libs/JPddl_.jar`, package
`com.hstairs.ppmajal`, which has no source in the repository; those classes
were read with `javap -c -p`.

**How the configuration resolves** (`ENHSP.java`):

- `-h` defaults to `hadd`, and `-s` defaults to `gbfs` (`ENHSP.java:312-318`).
- A `-planner` preset (`setPlanner`, `:455-534`) overrides `-h` and `-s`, and
  resets helpful actions and transitions. An unknown preset falls back to
  `gbfs` + `hadd`.
- `opt-blind` is `WAStar` + `blind` + `larger_g` tie-breaking, and sets
  `aibrPreprocessing = false`. Otherwise AIBR preprocessing is on unless `-dap`
  is given (`:418`).
- `-wh` (the h weight) defaults to 1.0 (`:550`). `-dl` becomes the search's
  g-bound (`:553`).

**How the search is built** (`PDDLPlanner`): `-s` is lowercased and then
mapped as follows:

- `wastar` builds `WAStar(hw, optimality=true)`, with f = g + w·h.
- `gbfs` builds the same `WAStar` class with `optimality=false`, so f = w·h.
- `ehs`, `ida`, `lazygbfs` and `lazywastar` build other classes.
- Any unrecognised string builds `WAStar(optimality=false)`.

**What `WAStar` drops** (`WAStar.queueSuccessor` and `search`):

- A successor is not queued if it was reached before at a g no higher than its
  new g. That is duplicate detection, and it is sound.
- A successor whose h is `Float.MAX_VALUE` is dropped as a dead end.
- A successor whose g is at or above the bound (`-dl`) is dropped.
- A dequeued node whose g is no longer the best known for its state is
  skipped; its cheaper copy is in the queue.
- With helpful actions on, successors come only from the heuristic's helpful
  actions (`SearchEngine.getActionsToSearch`); otherwise from
  `getTransitions(false)`.
- A state whose goal check returns null counts as a dead end. That is a state
  violating the problem's own constraints, part of the semantics, not pruning.

**The blind heuristic** (`BlindHeuristic`): `computeEstimate` is
`return 0f`, and `getTransitions` returns every grounded action. So it never
reports a dead end.

**Consequence: the weight does not matter.** With `blind`, h = 0, so
w·h = 0 for every `-wh`, and nothing is ever dropped as a dead end, in either
`wastar` or `gbfs` mode. Both drop only duplicates, so both exhaust the
reachable state space.

**AIBR preprocessing** (`PDDLProblem.prepareForSearch` →
`simplifyAndSetupInit` → `easyCleanUp`) runs the `Aibr` heuristic once on the
initial state:

- If its estimate is ∞, it prints "Problem Detected as Unsolvable by AIBR during
  preprocessing" and `prepareForSearch` fails. ENHSP then prints
  `Unsolvable Problem`, which `enhsp_verdict` reads as `unknown` / `grounding`.
- Otherwise it **replaces the action set** with `Aibr.getAllTransitions()`,
  the actions its relaxed exploration reached.

So with AIBR on, search completeness rests on that exploration
over-approximating true reachability. The AIBR paper (Scala, Haslum, Thiébaux
& Ramírez, ECAI 2016, citation from memory) claims the relaxation is sound in
that sense. The implementation has not been checked against it:

- `HomeMadeRealInterval` delegates `+ − × ÷` to `ia_math`, which rounds
  outward (`RMath.add_lo` / `add_hi`, `prevfp` / `nextfp`).
- `pown`, `pow` and the trigonometric functions use plain `java.lang.Math`,
  which can round inward.
- The supporter construction (`generateNumericSupporters`,
  `generateInfSupporter`) and the fixpoint (`fixPointComputation`) were not
  verified.

Until they are, AIBR preprocessing must be off for a proof.

**The allowlist** (`_enhsp_exhausted_verdict`) is therefore:

- heuristic `blind`;
- search `wastar` or `gbfs`, any `-wh`;
- no helpful actions or transitions;
- no `-dl`, and not anytime;
- AIBR preprocessing off: `-planner opt-blind`, or `-dap`.

Everything else is `unverified-search`, except helpful actions, which give
`incomplete`.

**UPF's ENHSP engines** (`up_enhsp/enhsp_planner.py`) all report a run that
exits 0 without a plan as `UNSOLVABLE_PROVEN`. Their configurations:

| UPF engine | ENHSP argv | reading |
|---|---|---|
| `enhsp` | none (`gbfs` + `hadd`) | `unverified-search` |
| `enhsp-opt` | `-s WAStar -h hrmax` | `unverified-search` |
| `BLIND-enhsp` | `-s WAStar -h blind -ties larger_g` | `unverified-search`: AIBR is on |
| `enhsp-any` | `-anytime` | `anytime` |

A UPF result file needs `engine_command` for any of these to be read at all.

## Widening the lists

To add a component, check what it removes against the table under "The
principle", in the planner's own code, and record the evidence here. Then add
it to `_FD_VERIFIED_CALLS` or `_ENHSP_VERIFIED_SEARCHES` (or the ENHSP
configuration check), with a test that runs the real log under the new
configuration.

*Citations:* the citations here come from memory and have not been checked
against the published versions. Confirm them before using them anywhere
formal.
