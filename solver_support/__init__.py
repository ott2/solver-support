"""solver_support -- a generic solver pipeline.

Domain-agnostic infrastructure for driving a Conjure / Savile Row / Fast Downward
/ SymK / ENHSP / UPF pipeline: spawn solvers, manage phases and timeouts, name
files, parse solver output into timing / cost-layer stats, and run a horizon
loop.  A consumer (such as puzznic-support) supplies its own model registry and
plugs its domain scoring in via injection; this package knows nothing about any
particular puzzle or objective.

This package is being extracted, module by module, from puzznic-support. It is
currently a scaffold: modules land here during the physical-extraction phase
once their domain-scoring coupling has been decoupled in place. See README.md.
"""

__version__ = "0.1.0"
