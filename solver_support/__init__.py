"""solver_support -- a generic solver pipeline.

Domain-agnostic infrastructure for driving a Conjure / Savile Row / Fast Downward
/ SymK / ENHSP / UPF pipeline: spawn solvers, manage phases and timeouts, name
files, parse solver output into timing / cost-layer stats, and run a horizon
loop.  A consumer (such as puzznic-support) supplies its own model registry and
plugs its domain scoring in via injection; this package knows nothing about any
particular puzzle or objective.

The core ships *unwired*: no objective extractor is registered and the registry
has no default config path, so a bare ``import solver_support`` yields an
unconfigured pipeline. A consumer registers its scoring (see :mod:`objective`)
and its packaging paths (see :mod:`model_registry`) once at its own import time.
"""

__version__ = "0.1.0"

# --- model registry (loader + command builders + packaging injection) ---
from .model_registry import (
    ModelRegistry,
    ModelRegistryError,
    get_default_registry,
    check_model_registry_consistency,
    set_default_config_path,
    set_resource_root,
    set_user_config_finder,
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
from .solver_runner import SolverRunner, SolverResult, SolverExecutableError
from .solver_stats import SolverStatsExtractor, detect_solver_from_command
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
    "format_timestamp",
    "arrayify",
    "indices",
    "BrokenPipeHandler",
]
