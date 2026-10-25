"""solver_support -- a generic solver pipeline.

Domain-agnostic infrastructure for driving a Conjure / Savile Row / Fast Downward
/ SymK / ENHSP / UPF pipeline: spawn solvers, manage phases and timeouts, name
files, parse solver output into timing / cost-layer stats, and run a horizon
loop.  A consumer supplies its own model registry and plugs its domain scoring
in via injection; this package knows nothing about any particular problem or
objective.

The core ships *unwired*: no objective extractor is registered and the registry
has no default config path, so a bare ``import solver_support`` yields an
unconfigured pipeline. A consumer registers its scoring (see :mod:`objective`)
and its packaging paths (see :mod:`model_registry`) once at its own import time.
"""

# Read back from the installed distribution metadata; the version itself is
# declared once, in pyproject.toml. There is deliberately no literal here to
# drift from it - the failure being avoided is a release where the metadata says
# one thing and `__version__` another, each individually plausible and nothing
# catching the disagreement.
#
# Resolved eagerly rather than behind a PEP 562 `__getattr__`: the lookup scans
# sys.path for a matching .dist-info, which costs a fraction of a millisecond,
# and this module already imports psutil, PyYAML and every solver backend below.
# Deferring it would save nothing measurable against that.
#
# The fallback fires only in a source tree that was never installed. Note an
# *editable* install pins its .dist-info at install time, so a checkout whose
# version has moved on still reports the older one here until it is reinstalled
# - the price of single-sourcing from metadata rather than from a literal.
try:
    from importlib.metadata import PackageNotFoundError as _PackageNotFoundError
    from importlib.metadata import version as _version

    __version__ = _version("solver-support")
except _PackageNotFoundError:  # a source tree, never installed
    __version__ = "0+unknown"

# --- model registry (loader + command builders + packaging injection) ---
from .model_registry import (
    ModelRegistry,
    ModelRegistryError,
    get_default_registry,
    check_model_registry_consistency,
    set_default_config_path,
    set_resource_root,
    set_user_config_finder,
    starter_config_text,
    write_starter_config,
)
from .command_builders import (
    savilerow_backstop, SAVILEROW_FLUSH_GRACE, fd_backstop, FD_EXIT_GRACE,
)

# --- run configuration singleton ---
from .global_state import global_state, GlobalState

# --- objective-extraction injection point (a consumer registers extractors) ---
from . import objective
from .objective import (
    set_reported_score_from_solution,
    set_reported_score_from_stdout,
    set_reported_score_from_timeout_solution,
    set_raw_objective_from_solution,
)

# --- solvers ---
from .solver_base import Solver
from .solver_fd import FastDownwardSolver
from .solver_enhsp import EnhspSolver
from .solver_upfplan import UpfPlanSolver
from .solver_horizon import HorizonBasedSolver, SavileRowSolver, ConjureSolver

# --- execution, stats, run model ---
from .solver_runner import (
    SolverRunner, SolverResult, SolverExecutableError, DIMACS_SAT_RETURNCODES,
)
from .solver_stats import (
    SolverStatsExtractor, detect_solver_from_command, FD_ANSWER_RETURNCODES,
    UPF_RESULT_SCHEMA, UPF_RESULT_KEYS, read_upf_result,
)
from .run_summary import RunSummary, PhaseResult, create_run_summary

# --- file naming and horizon loop ---
from .file_management import (
    SolutionFileNamer,
    ObjectiveFileManager,
    VisualizationFileManager,
    compute_tune_slot,
    compute_options_label,
)
from .horizon_utils import (
    horizon_search,
    calculate_horizon_timeout,
    reconcile_horizon_bounds,
)

# --- generic helpers ---
from .utilities import (
    setup_logger,
    relativize_path,
    warn,
    set_warn_logger_name,
    format_timestamp,
    arrayify,
    indices,
    BrokenPipeHandler,
)

__all__ = [
    # registry
    "ModelRegistry",
    "ModelRegistryError",
    "get_default_registry",
    "check_model_registry_consistency",
    "set_default_config_path",
    "set_resource_root",
    "set_user_config_finder",
    "starter_config_text",
    "write_starter_config",
    "savilerow_backstop",
    "SAVILEROW_FLUSH_GRACE",
    "fd_backstop",
    "FD_EXIT_GRACE",
    # run config
    "global_state",
    "GlobalState",
    # objective injection
    "objective",
    "set_reported_score_from_solution",
    "set_reported_score_from_stdout",
    "set_reported_score_from_timeout_solution",
    "set_raw_objective_from_solution",
    # solvers
    "Solver",
    "FastDownwardSolver",
    "EnhspSolver",
    "UpfPlanSolver",
    "HorizonBasedSolver",
    "SavileRowSolver",
    "ConjureSolver",
    # execution / stats / run model
    "SolverRunner",
    "SolverResult",
    "SolverExecutableError",
    "DIMACS_SAT_RETURNCODES",
    "FD_ANSWER_RETURNCODES",
    "UPF_RESULT_SCHEMA",
    "UPF_RESULT_KEYS",
    "read_upf_result",
    "SolverStatsExtractor",
    "detect_solver_from_command",
    "RunSummary",
    "PhaseResult",
    "create_run_summary",
    # file naming / horizon loop
    "SolutionFileNamer",
    "ObjectiveFileManager",
    "VisualizationFileManager",
    "compute_tune_slot",
    "compute_options_label",
    "horizon_search",
    "calculate_horizon_timeout",
    "reconcile_horizon_bounds",
    # helpers
    "setup_logger",
    "relativize_path",
    "warn",
    "set_warn_logger_name",
    "format_timestamp",
    "arrayify",
    "indices",
    "BrokenPipeHandler",
]
