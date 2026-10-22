# ENHSP logs

Real planner output, for the `enhsp_verdict` tests. Captured 2026-10-22 from
ENHSP 0.21.0 (`java -jar enhsp.jar`, as the `enhsp` wrapper runs it). Unlike Fast
Downward, ENHSP's verdict needs stderr as well: a logged exception goes there,
and the run beside it still exits 0. An empty stderr is not kept.

| files | command (from this directory) | exit |
|-------|-------------------------------|------|
| `enhsp-sat` | `enhsp -o ../fd/dom.pddl -f ../fd/sat.pddl -planner opt-blind` | 0 |
| `enhsp-exhaust` | `enhsp -o ../fd/dom.pddl -f ../fd/unsat.pddl -planner opt-blind` | 0 |
| `enhsp-ha` | `enhsp -o ../fd/dom.pddl -f ../fd/unsat.pddl -planner sat-hmrph` | 0 |
| `enhsp-dl` | `enhsp -o ../fd/dom.pddl -f ../fd/sat.pddl -planner opt-blind -dl 1` | 0 |
| `enhsp-syntax` | `enhsp -o broken.pddl -f ../fd/unsat.pddl -planner opt-blind` | 255 |
| `enhsp-nofile` | `enhsp -o missing.pddl -f ../fd/unsat.pddl -planner opt-blind` | 0 |
| `enhsp-oom` | `java -Xmx16m -jar enhsp.jar -o domain.pddl -f instance_20_2.pddl -planner opt-blind` (ENHSP's own `ijcai16_benchmarks/fn-counters-rnd`) | 1 |

Of the four runs that print `Problem unsolvable`, only `enhsp-exhaust` is a
proof. `enhsp-ha` pruned with helpful actions, and `enhsp-dl` refuted only plans
within its cost bound: `sat.pddl` has a plan of length 3. `enhsp-nofile` is
the hazard this set exists for: the domain file is missing, yet the run prints
`Domain parsed` and `Unsolvable Problem` and exits 0.

A search that throws is not represented, because no input here makes it throw.
`ENHSP.planning()` (`src/planners/ENHSP.java:229-231`) logs the exception at
SEVERE, the same way `enhsp-nofile.stderr` shows, and then returns without a
verdict line.
