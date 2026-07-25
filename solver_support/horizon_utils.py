"""
Horizon search utilities.

This module provides generic horizon-extending search functionality
that can be used by different solver types (Savile Row, Conjure, etc.).
"""

import logging
import shutil
import time
from pathlib import Path
from typing import Optional, Callable, Tuple

from .file_management import SolutionFileNamer
from .global_state import global_state
from .objective import reported_score_from_timeout_solution
from .solver_runner import SolverRunner


def reconcile_horizon_bounds(startat: int, stopat: int,
                             min_horizon: int = 1) -> Tuple[int, int]:
    """Reconcile the horizon-scan bounds into an effective (start, stop) pair.

    This is the single source of truth for how the requested bounds are
    resolved, shared by the CLI (``parse_args`` in ``scripts/run.py``) and the
    core loop (``horizon_search``) so the two cannot drift. The rule:

    - the start is floored to ``min_horizon`` - a solver-specific minimum
      (e.g. Conjure/Essence models need at least 2), 1 otherwise; then
    - the stop is raised to at least the (floored) start, so the scan always
      tries at least the starting horizon rather than running an empty loop.

    A contradictory ``startat=5, stopat=3`` therefore yields ``(5, 5)`` - the
    start wins and the cap is raised to meet it - not an empty range. The CLI
    omits ``min_horizon`` (the per-solver floor is applied later, in the core);
    it relies only on the start-vs-stop half of the rule.

    Args:
        startat: Requested starting horizon
        stopat: Requested highest horizon to try
        min_horizon: Solver-specific minimum horizon (default 1)

    Returns:
        ``(effective_start, effective_stop)`` with ``min_horizon <= start <= stop``.
    """
    effective_start = max(startat, min_horizon)
    effective_stop = max(stopat, effective_start)
    return effective_start, effective_stop


def _create_main_solution_from_best(runner: SolverRunner, best_solution_found: Path,
                                    logger: logging.Logger, context: str) -> Path:
    """
    Helper function to create the main solution file from the best solution found.

    Args:
        runner: SolverRunner instance
        best_solution_found: Path to the best solution file
        logger: Logger instance
        context: Context string for logging (e.g., "before timeout", "within horizon limit")

    Returns:
        Path to the created main solution file
    """
    logger.info(f"Found best solution {context}: {best_solution_found}")
    # Create the main solution file by copying the best one
    main_solution = Path(SolutionFileNamer.generate_solution_filename(
        runner.summary.instance, runner.summary.solver,
        runner.summary.model, runner.summary.timestamp,
        tune=runner.summary.tune_slot
    ))
    shutil.copy(best_solution_found, main_solution)
    logger.info(f"Created main solution file: {main_solution}")
    # Extract and set final score for timeout-based solutions
    _extract_final_score_for_timeout_solution(runner, main_solution, logger)
    return main_solution


def horizon_search(runner: SolverRunner,
                   param_file: Path,
                   startat: int,
                   horizon_callback: Callable[[int, Path], Optional[Path]],
                   logger: logging.Logger,
                   min_horizon: int = 1,
                   stopat: int = 100,
                   extra_constraints_callback: Optional[Callable[[Path, int], None]] = None) -> Optional[Path]:
    """
    Generic horizon-extending search loop.

    Args:
        runner: SolverRunner instance for execution (contains output directory)
        param_file: Original parameter file
        startat: Starting horizon value
        horizon_callback: Function(n, horizon_param_file) -> Optional[Path]
                         Returns solution file if found, None to continue
        logger: Logger instance
        min_horizon: Minimum allowed horizon value
        stopat: Highest horizon to try (inclusive upper limit of the scan)
        extra_constraints_callback: Optional function(horizon_param_file, n) to add extra constraints

    Returns:
        Path to solution file if found, None otherwise
    """
    # Get output directory from runner
    output_dir = Path(runner.summary.output_dir)

    # Single source of truth for the start/stop reconciliation, shared with the
    # CLI so the two cannot drift (see reconcile_horizon_bounds). Guarantees a
    # non-empty scan of at least the starting horizon.
    effective_start, effective_stop = reconcile_horizon_bounds(
        startat, stopat, min_horizon)
    logger.info(f"Starting horizon search from {effective_start} to {effective_stop}")

    # Track the best solution found for timeout-based continue-scan
    best_solution_found = None

    for n in range(effective_start, effective_stop + 1):  # inclusive of effective_stop
        # Check for interruption before starting each horizon
        # First interrupt: stop at natural phase boundary (between horizons)
        # Second interrupt: force immediate termination
        if global_state.interrupted:
            if global_state.should_force_termination:
                logger.warning("Forcing immediate termination of horizon search")
            else:
                logger.warning("Stopping horizon search due to interruption")
            # An interruption between horizons is just an early stop of
            # continue-scan, not a lost run: a best solution from an earlier
            # horizon is already on disk. This is exactly what happens when an
            # -O2 Savile Row run overruns its per-horizon timelimit and the
            # harness SIGTERMs it - the soft TimeoutError below never fires, so
            # without this salvage the found solution (and its score) is silently
            # discarded and the run reports a bare "-". Recover it the same way
            # the timeout path does; cheap enough to do even on forced termination.
            if best_solution_found and best_solution_found.exists():
                return _create_main_solution_from_best(
                    runner, best_solution_found, logger, "before interruption")
            return None
        
        # Create parameter file with horizon constraints
        horizon_param_file = create_horizon_param_file(param_file, output_dir, n, logger, runner)
        if not horizon_param_file:
            continue
            
        # Add any extra constraints if callback provided
        if extra_constraints_callback:
            extra_constraints_callback(horizon_param_file, n)
            
        logger.info(f"Trying horizon {n}: created {horizon_param_file}")
        
        # Call specific horizon logic
        try:
            solution = horizon_callback(n, horizon_param_file)
            if solution:
                logger.info(f"Solution found at horizon {n}")
                return solution
            else:
                logger.info(f"No solution found at horizon {n}")
                # Check if we have an intermediate solution saved from this horizon
                potential_best = Path(SolutionFileNamer.generate_solution_filename(
                    runner.summary.instance,
                    runner.summary.solver,
                    runner.summary.model,
                    runner.summary.timestamp,
                    tune=runner.summary.tune_slot,
                    horizon=n,
                    suffix=".best.solution"
                ))
                if potential_best.exists():
                    best_solution_found = potential_best
                    logger.info(f"Updated best solution from horizon {n}")
        except TimeoutError as e:
            # Solver timed out - stop horizon search immediately
            logger.error(f"Horizon search stopped due to timeout at horizon {n}: {e}")

            # Mark that the run terminated due to timeout
            runner.timeout_termination = True

            # For timeout-based continue-scan, copy the best solution to the main file
            if best_solution_found and best_solution_found.exists():
                return _create_main_solution_from_best(runner, best_solution_found, logger, "before timeout")

            # Only re-raise the TimeoutError if we have no solution to return
            logger.error("No solution found before timeout occurred")
            raise
            
    # No solution found within horizon limit
    # For timeout-based continue-scan, copy the best solution to the main file
    if best_solution_found and best_solution_found.exists():
        return _create_main_solution_from_best(runner, best_solution_found, logger, "within horizon limit")

    # The loop ran through every horizon up to the cap without finding a solution
    # and without timing out or being interrupted (those paths return/raise
    # earlier). This is "unable to decide before reaching the horizon limit" -
    # distinct from a genuine failure. Flag it so the orchestrator records
    # final_status 'hlim': a horizon-scanning CP model that exhausts its horizons
    # has NOT proven unsatisfiability (it cannot), it has merely run out of
    # horizons, so this must not be conflated with a planner's proven-unsat.
    runner.horizon_limit_reached = True
    logger.error(f"No solution found within horizon limit ({effective_start}-{effective_stop})")
    return None


def create_horizon_param_file(param_file: Path, output_dir: Path, n: int, logger: logging.Logger, runner: SolverRunner = None) -> Optional[Path]:
    """
    Create parameter file with base content, ready for horizon-specific constraints.
    
    Args:
        param_file: Original parameter file
        output_dir: Output directory
        n: Horizon value
        logger: Logger instance
        runner: SolverRunner instance for consistent naming (optional for backward compatibility)
        
    Returns:
        Path to created parameter file, or None if creation failed
    """
    n2 = f"{n:02d}"  # Zero-padded 2-digit number
    
    # Use consistent naming pattern with solution files if runner is provided
    if runner and hasattr(runner, 'summary'):
        instance_name = runner.summary.instance
        solver = runner.summary.solver
        model = runner.summary.model
        timestamp = runner.summary.timestamp
        tune = runner.summary.tune_slot
        tune_str = f"-{tune}" if tune else ""
        horizon_param_file = output_dir / f"{instance_name}-{solver}{tune_str}-{model}-{timestamp}-{n2}.param"
    else:
        # Fallback to old naming for backward compatibility
        horizon_param_file = output_dir / f"{param_file.stem}-{n2}.param"
    
    try:
        with open(param_file, 'r') as f:
            param_content = f.read()
        
        # Remove any existing horizon-related lines
        param_lines = []
        for line in param_content.splitlines():
            # Remove existing horizon constraint lines
            if not any(keyword in line for keyword in ['noSteps', 'maxMoves', 'horizon']):
                param_lines.append(line)
        
        # Write base parameter file
        with open(horizon_param_file, 'w') as f:
            f.write('\n'.join(param_lines) + '\n')
            
            # Add control file contents if available
            if runner and hasattr(runner, 'summary') and runner.summary.model:
                try:
                    control_path = global_state.model_registry.get_control_path(runner.summary.model)
                    if control_path and control_path.exists():
                        try:
                            with open(control_path, 'r') as cf:
                                control_content = cf.read()
                            # Add control file contents (horizon constraints added by caller after this)
                            f.write(control_content)
                            if not control_content.endswith('\n'):
                                f.write('\n')
                            logger.debug(f"Added control file contents from {control_path}")
                        except Exception as e:
                            logger.warning(f"Failed to read control file {control_path}: {e}")
                except Exception:
                    # Model type might not be in registry (e.g., testing unregistered models)
                    # This is expected and not an error, so silently continue
                    logger.debug(f"Model type '{runner.summary.model}' not found in registry, skipping control file")
                    pass
            
        return horizon_param_file
        
    except Exception as e:
        logger.error(f"Failed to create horizon param file: {e}")
        return None


def calculate_horizon_timeout(start_time: float, original_timeout: Optional[int], n: int, logger: logging.Logger) -> Optional[int]:
    """
    Calculate remaining timeout for a specific horizon step.

    Args:
        start_time: Start time of the overall solving process
        original_timeout: Original total timeout in seconds
        n: Current horizon number
        logger: Logger instance for warnings

    Returns:
        Remaining timeout in seconds (minimum 1), or None if no timeout set

    Raises:
        TimeoutError: If total timeout has already been exceeded
    """
    if original_timeout is None:
        return None

    elapsed_time = time.time() - start_time
    remaining_time = original_timeout - elapsed_time

    if remaining_time <= 0:
        # We've already exceeded the total timeout
        logger.warning(f"Total timeout of {original_timeout}s exceeded after {elapsed_time:.1f}s")
        raise TimeoutError(f"Total timeout of {original_timeout}s exceeded")

    # Use remaining time as timeout for this horizon, with minimum of 1 second
    horizon_timeout = max(1, int(remaining_time))
    logger.debug(f"Horizon {n}: {horizon_timeout}s remaining of {original_timeout}s total timeout")

    return horizon_timeout


def _extract_final_score_for_timeout_solution(runner: SolverRunner, solution_file: Path, logger: logging.Logger) -> None:
    """Extract score from final timeout solution and add it to the summary."""
    try:
        # Only salvage a score for models whose objective is encoded in the
        # solution (the scored* CP models); others report it via the .obj file.
        if not global_state.model_registry.get_objective_in_solution(runner.summary.model):
            return

        from .run_summary import PhaseResult

        if not solution_file.exists():
            logger.debug(f"Solution file {solution_file} does not exist for score extraction")
            return

        # A salvaged timeout solution may not survive Conjure parsing, so ask the
        # consumer for its reported objective straight from the solution file.
        display_score = reported_score_from_timeout_solution(str(solution_file))
        if display_score is not None:
            logger.info(f"Extracted final score for timeout solution: {display_score}")

            # Create a final stats phase to hold the score
            final_phase = PhaseResult(
                name="final-timeout-solution",
                command=["timeout-best-solution-extraction"],
                duration=0.0,
                success=True,
                output_files=[str(solution_file)],
                error_message="",
                solver_stats={"score": display_score, "solution_found": True, "timeout_solution": True}
            )

            # Add the phase to the summary
            runner.summary.phases.append(final_phase)

            # Trigger score recalculation by re-finalizing
            current_status = runner.summary.final_status
            runner.summary.finalize(current_status)

            logger.debug(f"Added final score phase, summary score now: {runner.summary.score}")

    except Exception as e:
        logger.warning(f"Failed to extract final score from timeout solution {solution_file}: {e}")