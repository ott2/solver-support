# Fast Downward / SymK logs

Real planner output, for the `fd_verdict` tests: the verdict reads exit codes
*and* log lines, and a hand-written log would only test what the author already
believed the planner prints. Captured 2026-10-22 from Fast Downward `76b1d36e4`
(2026-06-11) and SymK `4e000d8a0` (2026-05-28), with local paths rewritten to
`/opt`. Only stdout is kept; the verdict does not read stderr.

| file | command (from this directory) | exit |
|------|-------------------------------|------|
| `fd-sat.stdout` | `fd dom.pddl sat.pddl --search "astar(blind())"` | 0 |
| `fd-exhaust.stdout` | `fd dom.pddl unsat.pddl --search "astar(blind())"` | 12 |
| `fd-bound.stdout` | `fd dom.pddl sat.pddl --search "astar(blind(), bound=2)"` | 12 |
| `fd-transunsat.stdout` | `fd dom.pddl trans.pddl --search "astar(blind())"` | 12 |
| `fd-maxtime.stdout` | `fd dom.pddl unsat.pddl --search "astar(blind(), max_time=0)"` | 12 |
| `fd-ehc.stdout` | `fd dom.pddl unsat.pddl --search "ehc(ff())"` | 12 |
| `fd-lmcut-ax.stdout` | `fd axdom.pddl axprob.pddl --search "astar(lmcut())"` | 34 |
| `symk-exhaust.stdout` | `symk dom.pddl unsat.pddl --search "sym_bd()"` | 12 |
| `symk-bound.stdout` | `symk dom.pddl sat.pddl --search "sym_bd(bound=2)"` | 12 |

`unsat.pddl` is relaxed-reachable but unsolvable (`p` and `q` delete each
other), so the translator cannot see it and the search must exhaust;
`trans.pddl` is one the translator *does* see (`Generating unsolvable task`).

Fast Downward 26.6 is not represented: it is not built here. Its log lines are
taken verbatim from `src/search/search_algorithm.cc` (`get_finished_search_status`)
instead.
