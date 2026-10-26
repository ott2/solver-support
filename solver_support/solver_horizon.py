"""
Horizon-based solver implementations.

This module provides solver classes that use horizon search with optimization support,
including the base HorizonBasedSolver class and specific implementations for
Savile Row (Essence Prime) and Conjure (Essence) solvers.
"""

import os
import shutil
import time
from pathlib import Path
from typing import Optional, List, Tuple

from .solver_stats import SolverStatsExtractor
from .solver_runner import SolverExecutableError
from .global_state import global_state
from .file_management import SolutionFileNamer, ObjectiveFileManager
from .solver_base import Solver
from .horizon_utils import (
    horizon_search, calculate_horizon_timeout, create_horizon_param_file)
from .command_builders import savilerow_backstop
from .objective import raw_objective_from_solution
from .model_registry import LEGACY_CONJURE_SAVILEROW


class HorizonBasedSolver(Solver):
    """Base class for solvers that use horizon search with optimization support.

    A model is scanned over horizons only if its registry entry (or its
    solver's) declares ``horizon-params``. Any other model is tailored and
    solved once, in a phase named ``solving`` that carries the run's verdict.
    """

    def __init__(self, solver_runner, solver_name: Optional[str] = None):
        """Initialize with solver runner and optimization tracking.

        ``solver_name`` is the Savile Row entry that solves each horizon. None
        resolves it from the registry when first needed (see
        :meth:`_default_solver_name`).
        """
        super().__init__(solver_runner)
        self._best_objective = None
        self._has_timeout = False  # Track if we're running with a timeout
        self._stop_at_first = False  # regime: first stops the scan at the first solving horizon
        self._solver_name = solver_name
        self._backstop = False  # size each horizon's kill above its in-band limits

    @property
    def solver_name(self) -> str:
        """The Savile Row entry that solves each horizon."""
        if self._solver_name is None:
            return self._default_solver_name()
        return self._solver_name

    @solver_name.setter
    def solver_name(self, value: Optional[str]) -> None:
        self._solver_name = value

    def _default_solver_name(self) -> str:
        """The entry to use when the caller named none: the run's own solver
        if it is a Savile Row entry, else the legacy name."""
        try:
            run_solver = self.runner.summary.solver
            if global_state.model_registry.get_solver_type(run_solver) == 'savilerow':
                return run_solver
        except Exception:
            pass
        return LEGACY_CONJURE_SAVILEROW

    def _horizon_params(self) -> List[str]:
        """The parameter names this run sets to the horizon (see
        :meth:`ModelRegistry.get_horizon_params`); empty for a model with no
        horizon."""
        try:
            return global_state.model_registry.get_horizon_params(
                self.runner.summary.model)
        except Exception:
            return []

    def _scan_bounds(self, kwargs) -> Optional[Tuple[int, int, int]]:
        """``(startat, floor, stopat)`` for this run's horizon scan, or None if
        its model has no horizon.

        The model's ``horizon-start`` floors any start the caller asks for, and
        its ``horizon-stop`` is the cap unless the caller passes ``stopat``.
        """
        bounds = global_state.model_registry.get_horizon_bounds(
            self.runner.summary.model)
        if bounds is None:
            given = [k for k in ('startat', 'stopat') if kwargs.get(k) is not None]
            if given:
                self.logger.debug(
                    f"Model {self.runner.summary.model} has no horizon; "
                    f"ignoring {', '.join(given)}")
            return None
        floor, stop = bounds
        startat = kwargs.get('startat')
        stopat = kwargs.get('stopat')
        return (floor if startat is None else startat, floor,
                stop if stopat is None else stopat)

    def _single_param_file(self, param_file: Path) -> Optional[Path]:
        """The instance for a single solve: a copy in the output directory,
        with the model's control file appended, as a horizon gets."""
        return create_horizon_param_file(
            param_file, Path(self.runner.summary.output_dir), None,
            self.logger, self.runner, horizon_params=())

    def _check_single_solve(self, result, what: str) -> None:
        """Raise as a horizon scan would for a launch failure or a timeout."""
        if getattr(result, 'launch_failed', False):
            raise SolverExecutableError(
                f"{what} could not be launched: {result.stderr.strip()}")
        if result.timed_out:
            self.logger.warning(f"{what} timed out")
            raise TimeoutError(f"{what} timed out")

    def _horizon_watchdog(self, horizon_timeout: Optional[int]) -> Optional[int]:
        """The kill for one horizon's Savile Row run, or None for the runner's.

        With ``backstop=True`` it is :func:`savilerow_backstop` over the same
        budget the command was built with: Savile Row's ``-timelimit`` and the
        solver's own limit each get ``horizon_timeout`` and run one after the
        other, so a kill below their sum can land before Savile Row writes its
        ``.info`` and loses every statistic. Off by default, because a scan
        killed at the backstop can overrun the runner's timeout by up to about
        that timeout again.
        """
        if self._backstop and horizon_timeout is not None:
            return savilerow_backstop(horizon_timeout)
        return None

    def _write_horizon(self, horizon_param_file: Path, n: int,
                       names: List[str]) -> bool:
        """Append ``letting <name> be n`` for each horizon parameter."""
        try:
            with open(horizon_param_file, 'a') as f:
                for name in names:
                    f.write(f"letting {name} be {n}\n")
            return True
        except Exception as e:
            self.logger.error(f"Failed to add horizon constraints: {e}")
            return False

    def _generate_objective_file_path(self, n: int) -> Optional[Path]:
        """Generate the objective file path for a given horizon, or None.

        None when the active backend does not write one. Only Savile Row's SAT
        arm runs the optimisation loop that emits intermediate objectives, so for
        a Minion or FlatZinc backend the file would be named, cleared, and then
        never appear. Returning None here rather than gating each call site keeps
        the three of them identical, and leaves continue-scan to read the
        objective out of the solution via the injected extractor - which is the
        only route those backends have.
        """
        if not global_state.model_registry.records_intermediate_objectives(
                self.solver_name):
            return None
        return ObjectiveFileManager.generate_objective_path(
            self.runner.summary.instance,
            self.runner.summary.solver,
            self.runner.summary.model,
            self.runner.summary.timestamp,
            n,
            tune=self.runner.summary.tune_slot
        )

    def _prepare_objective_file(self, obj_file: Optional[Path]) -> None:
        """Prepare the objective file by removing any existing file."""
        if obj_file is None:
            return
        ObjectiveFileManager.prepare_objective_file(obj_file, self.logger)

    def _parse_objective_file(self, obj_file: Path) -> Optional[int]:
        """Parse the objective values file created by Savile Row."""
        return ObjectiveFileManager.parse_objective_file(obj_file)

    def _extract_score_from_solution(self, solution_file: Path) -> Optional[int]:
        """Raw objective value from a solution file, via the injected extractor.

        The raw model-unit value is the correct objective for continue-scan
        comparisons; the reporting multiplier is applied only when a score is
        surfaced to the user, never here.
        """
        raw_objective = raw_objective_from_solution(str(solution_file))
        if raw_objective is not None:
            self.logger.debug(f"Extracted raw objective value from solution: {raw_objective}")
        return raw_objective

    def _check_continue_scan(self, obj_file: Optional[Path], n: int, continue_scan: bool, objective_value: Optional[int] = None) -> bool:
        """Check if horizon scanning should continue based on objective value."""
        if not continue_scan:
            return True  # Stop at first solution

        # regime: first (--tune first) stops as soon as any solution is found,
        # overriding both the continue-scan default and the timeout-driven
        # "keep improving" branch below. objbound is still supplied each horizon
        # (continue_scan stays True), so the model is well-formed; we just stop.
        if self._stop_at_first:
            self.logger.info(f"Solution found at horizon {n}; stopping horizon search (--tune first)")
            return True

        # Use provided objective value, or parse from obj_file if available
        best_value = objective_value
        if best_value is None and obj_file and obj_file.exists():
            best_value = self._parse_objective_file(obj_file)

        # If we have a timeout, always continue searching for better solutions
        if self._has_timeout:
            if best_value is not None:
                self.logger.info(f"Solution found at horizon {n} with objective value {best_value}, continuing search due to timeout mode")
            return False  # Continue to next horizon

        # Original behavior when no timeout
        if best_value is not None and best_value < 0:
            self.logger.info(f"Solution found at horizon {n} but best objective value is {best_value} (negative), continuing search")
            return False  # Continue to next horizon
        else:
            if best_value is not None:
                self.logger.info(f"Solution found at horizon {n} with best objective value {best_value}")
            return True  # Stop searching

    def _set_best_objective(self, value: Optional[int]) -> None:
        """Set the best objective value found during horizon search."""
        self._best_objective = value

    def _add_objective_constraint(self, horizon_param_file: Path, n: int) -> None:
        """Add objective constraint for continue-scan models if best objective exists."""
        if self._best_objective is None:
            # Set appropriate default objbound based on model type
            model_name = self.runner.summary.model.lower()
            if model_name == 'scored3':
                self._best_objective = -100000  # scored3 uses negative penalty-based scoring
            else:
                self._best_objective = 0  # scored and scored2 use positive scores

        # Add objbound constraint to the parameter file
        with open(horizon_param_file, 'a') as f:
            f.write(f"$ Added by horizon search for continue-scan\n")
            f.write(f"letting objbound be {self._best_objective}\n")

        self.logger.debug(f"Added objective constraint: obj > {self._best_objective}")

    def _setup_optimization(self, model_name: str, objbound: Optional[int] = None, timeout: Optional[int] = None):
        """Setup optimization parameters for the model.

        Returns:
            tuple: (is_optimization_model, continue_scan_enabled)
        """
        is_opt_model = global_state.model_registry.is_optimization_model(model_name)
        continue_scan = global_state.model_registry.get_continue_scan_enabled(model_name)

        # Override continue-scan under a timeout for models whose objective is
        # encoded in their solution (the scored* CP models). They already declare
        # continue-scan: true, so this is belt-and-braces - but under a timeout we
        # must keep threading objbound each horizon to improve the salvaged best.
        objective_in_solution = global_state.model_registry.get_objective_in_solution(model_name)
        if objective_in_solution and timeout is not None:
            continue_scan = True
            self.logger.debug(f"Overriding continue-scan to True for objective-in-solution model {model_name} with timeout")

        # The active tune's regime layers a stop control ON TOP of continue-scan:
        # `--tune first` stops the scan at the first solving horizon instead of
        # scanning on to improve the objective. It is deliberately kept separate
        # from continue_scan, which still governs whether objbound is threaded
        # through each horizon - the scored* models declare `given objbound` and
        # need it supplied even for a single-horizon solve, so flipping
        # continue_scan off here would leave the model ill-formed. A first-regime
        # run stays well-formed and merely stops early (see _check_continue_scan).
        regime = global_state.model_registry.get_tune_regime(self.solver_name, global_state.tune)
        self._stop_at_first = (regime == 'first')

        if is_opt_model:
            self.logger.debug(f"Model {model_name} is an optimization model")
            if continue_scan:
                self.logger.debug(f"Continue-scan enabled for {model_name}")
                # Set initial objective bound if provided
                if objbound is not None:
                    self._set_best_objective(objbound)
                    self.logger.debug(f"Set initial objective bound: {objbound}")
                # Track if we have a timeout for enhanced continue-scan behavior
                if timeout is not None:
                    self._has_timeout = True
                    self.logger.debug(f"Timeout mode enabled for continue-scan")

        return is_opt_model, continue_scan

    def _handle_objective_tracking(self, obj_file: Optional[Path], n: int, is_opt_model: bool, continue_scan: bool, solution_file: Optional[Path] = None) -> bool:
        """Handle objective tracking for optimization models.

        Returns:
            bool: True if horizon search should stop, False to continue
        """
        current_objective = None

        # For models whose objective is encoded in the solution, read it from
        # there; other optimisation models report it via the separate .obj file.
        objective_in_solution = global_state.model_registry.get_objective_in_solution(self.runner.summary.model)
        if is_opt_model and objective_in_solution and solution_file and solution_file.exists():
            current_objective = self._extract_score_from_solution(solution_file)
            # Fallback to .obj file if solution parsing fails (e.g., conjure bug with negative scores)
            if current_objective is None and obj_file and obj_file.exists():
                current_objective = self._parse_objective_file(obj_file)
                if current_objective is not None:
                    self.logger.debug(f"Used .obj file for objective value: {current_objective}")
        # Fallback to .obj file for other optimization models
        elif is_opt_model and obj_file and obj_file.exists():
            current_objective = self._parse_objective_file(obj_file)

        if current_objective is not None:
            # Update best objective
            if self._best_objective is None or current_objective > self._best_objective:
                self._set_best_objective(current_objective)
                self.logger.info(f"New best objective value: {current_objective}")

        # Check if we should continue scanning
        return self._check_continue_scan(obj_file, n, continue_scan, current_objective)

    def _update_final_stats(self, solver_type: str, result, horizon_n: int) -> None:
        """Update the final phase with horizon statistics."""
        self.logger.debug(f"Final horizon {horizon_n} - extracting stats")
        final_stats = SolverStatsExtractor.extract_stats(
            solver_type,
            result.stdout if hasattr(result, 'stdout') else "",
            result.stderr if hasattr(result, 'stderr') else "",
            result.duration if hasattr(result, 'duration') else None,
            horizon_number=None
        )

        if self.runner.summary.phases:
            self.runner.summary.phases[-1].solver_stats.update(final_stats)

    def _add_objbound_metadata(self, is_opt_model: bool, continue_scan: bool) -> None:
        """Add objbound metadata to the current phase if applicable."""
        if is_opt_model and continue_scan and self._best_objective is not None:
            if self.runner.summary.phases:
                self.runner.summary.phases[-1].solver_stats['objbound'] = self._best_objective

    def _record_savilerow_times(self, param_file: Path, n: Optional[int]) -> None:
        """Attach Savile Row's ``.info`` stats to horizon n's phase (the
        ``solving`` phase when ``n`` is None: a model solved once).

        Reads the ``<param_file>.info`` stats file Savile Row writes for this
        horizon and records ``savilerow_time`` (tailoring) and ``solver_time``
        on the phase, alongside the wall-clock ``duration`` already stored.

        The whole file is also kept verbatim under ``savilerow_info``. The two
        renamed times are what the phase model needs, but the same file carries
        the encoding-size and search-effort numbers (``SATVars``, ``SolverNodes``,
        ``SolverFailures``, ...) and the outcome flags (``SolverSatisfiable``
        distinguishes a refuted horizon from one that gave up; ``SolverTimeOut``
        / ``SavileRowTimeOut`` attribute a timeout to the solver or to
        tailoring). Without this a consumer wanting any of them has to re-parse
        the ``.info`` files itself, which duplicates the parser and needs the
        run's output directory rather than just its summary - so it stops working
        once those files are cleaned up.

        Kept **nested and verbatim** rather than flattened in. ``solver_stats``
        is the core's normalised, cross-solver vocabulary; these are one solver's
        own keys in its own spelling, and the backends do not even agree on which
        of them exist - Minion alone emits ``SolverSetupTime`` /
        ``SolverSolveTime`` / ``SolverSolutionsFound``, the FlatZinc backends
        alone ``SolverTotalWallTime``, kissat alone ``SATVars`` / ``SATClauses``.
        A curated allow-list would therefore be wrong already, and nesting also
        keeps CamelCase keys from colliding with the snake_case ones beside them.
        ``param_file`` is whatever parameter file Savile Row was run on - the
        Essence Prime ``.param`` for the direct Savile Row path, or the
        Conjure-translated ``.eprime-param`` for the Essence path - since Savile
        Row names the info file after that argument. Called for every horizon
        (not just the solving one), so an intermediate UNSAT horizon's split is
        captured too. Best-effort: a horizon that timed out before Savile Row
        wrote the file, or an old cleaned-up run, records neither. Guarded to
        the matching ``solving-horizon-{n}`` phase so it cannot scribble on an
        unrelated phase.

        Also records this horizon's ``verdict`` (see
        :meth:`SolverStatsExtractor.savilerow_verdict`), which is what separates
        a horizon that was *refuted* from one that merely ran out of budget -
        indistinguishable in ``solution_found``, since both are simply False.
        """
        info = SolverStatsExtractor.extract_savilerow_info(f"{param_file}.info")
        times = SolverStatsExtractor.extract_savilerow_info_times(f"{param_file}.info")
        phases = self.runner.summary.phases
        phase_name = "solving" if n is None else f"solving-horizon-{n}"
        if not (phases and phases[-1].name == phase_name):
            return
        phase = phases[-1]
        if info:
            phase.solver_stats.update(times)
            phase.solver_stats['savilerow_info'] = info
            phase.solver_stats.update(
                SolverStatsExtractor.savilerow_verdict_from_info(info))
        elif phase.timed_out or phase.launch_failed:
            # No ``.info``, but the phase records why: Savile Row was killed
            # before it could flush (it accumulates every statistic in memory and
            # writes the file once, after the solver it launched returns). Only
            # claim ``killed`` when there is that independent evidence - an absent
            # file is equally consistent with Savile Row never having run for this
            # horizon at all, and guessing would manufacture a diagnosis.
            phase.solver_stats['verdict'] = 'killed'

    def _build_savilerow_command(self, model_path: str, param_path: str, timeout: Optional[int], obj_file: Optional[Path] = None) -> List[str]:
        """
        Build a Savile Row command with consistent parameters.

        Args:
            model_path: Path to the model file
            param_path: Path to the parameter file
            timeout: Timeout in seconds
            obj_file: Optional objective file path

        Returns:
            Command list ready for execution
        """
        # Effective tune: an explicit --tune wins, else the model's pinned
        # default (e.g. scored3 -> safe), else the backend's default_tune
        # (resolved inside build_savilerow_command when this is None).
        registry = global_state.model_registry
        tune = self._effective_tune()
        return registry.build_savilerow_command(
            model_path=model_path,
            param_path=param_path,
            use_srjava=global_state.srjava,
            timeout=timeout,
            objective_file_path=str(obj_file) if obj_file else None,
            extra_options=global_state.options,
            solver_name=self.solver_name,
            tune=tune
        )

    def _save_intermediate_solution(self, solution: Path, n: int) -> None:
        """Copy a horizon-n solution to the standard .best.solution name.

        Used by the continue-scan timeout regime to bank the best objective
        found so far before extending the horizon. Callers gate this on an
        active timeout run with a known self._best_objective.
        """
        best_solution_path = Path(SolutionFileNamer.generate_solution_filename(
            self.runner.summary.instance,
            self.runner.summary.solver,
            self.runner.summary.model,
            self.runner.summary.timestamp,
            tune=self.runner.summary.tune_slot,
            horizon=n,
            suffix=".best.solution"
        ))
        shutil.copy(solution, best_solution_path)
        self.logger.info(f"Saved intermediate solution from horizon {n} with objective {self._best_objective}")


class SavileRowSolver(HorizonBasedSolver):
    """Solver implementation for Savile Row with horizon search."""

    def __init__(self, solver_runner, solver_name: Optional[str] = None):
        """Initialize with solver runner and solver name."""
        super().__init__(solver_runner, solver_name)

    def solve(self, param_file: Path, **kwargs) -> Optional[Path]:
        """
        Execute Savile Row solver with horizon search loop.
        Args:
            param_file: Path to parameter file
            **kwargs: Additional arguments including:
                - model_path: Path to Essence Prime model file (required)
                - startat: Starting horizon value (default: the model's
                  horizon-start, else 1; never below horizon-start)
                - stopat: Highest horizon to try (default: the model's
                  horizon-stop, else 100)
                - backstop: kill each horizon at savilerow_backstop of its
                  budget rather than at the runner's timeout, so Savile Row's
                  own limits fire first and its statistics survive
                  (default: False)
                - objbound: Objective bound constraint for optimization models
        Returns:
            Path to solution file if successful, None otherwise
        """
        # Extract required kwargs
        model_path = kwargs.get('model_path')
        if not model_path:
            raise ValueError("model_path is required for Savile Row solver")

        self._backstop = bool(kwargs.get('backstop', False))
        scan = self._scan_bounds(kwargs)
        if scan is None:
            return self._solve_once(param_file, model_path)
        startat, min_horizon, stopat = scan
        objbound = kwargs.get('objbound')

        # Setup optimization parameters
        model_name = self.runner.summary.model
        is_opt_model, continue_scan = self._setup_optimization(model_name, objbound, self.runner.timeout)

        # Track total elapsed time for timeout management
        total_start_time = time.time()
        original_timeout = self.runner.timeout
        horizon_params = self._horizon_params()

        def savilerow_horizon_callback(n: int, horizon_param_file: Path) -> Optional[Path]:
            """Execute Savile Row for a specific horizon."""
            # Calculate remaining timeout for this horizon step
            horizon_timeout = calculate_horizon_timeout(total_start_time, original_timeout, n, self.logger)

            # Only generate objective file for optimization models
            obj_file = None
            if is_opt_model:
                # Generate objective file name following our naming conventions
                obj_file = self._generate_objective_file_path(n)
                # Remove any existing objective file before this horizon run
                self._prepare_objective_file(obj_file)

            if not self._write_horizon(horizon_param_file, n, horizon_params):
                return None

            # Build Savile Row command for this horizon using helper method
            savilerow_cmd = self._build_savilerow_command(
                str(model_path), str(horizon_param_file), horizon_timeout, obj_file
            )

            # Expected solution file
            expected_solution = horizon_param_file.with_suffix('.param.solution')

            # Run Savile Row for this horizon
            result = self.runner.run_phase(
                f"solving-horizon-{n}",
                savilerow_cmd,
                expected_outputs=[str(expected_solution)],
                horizon_number=n,
                timeout=self._horizon_watchdog(horizon_timeout)
            )

            # Record Savile Row's tailoring vs solver time split from the .info
            # file for this horizon (best-effort; absent on a hard timeout).
            self._record_savilerow_times(horizon_param_file, n)

            # If the solver executable could not be launched, abort the whole
            # scan: a missing/non-executable binary will not appear on a later
            # horizon, so marching on to the horizon cap only wastes time and
            # buries the real cause. Surfaces as final_status=launch-failed.
            if getattr(result, 'launch_failed', False):
                raise SolverExecutableError(
                    f"Savile Row could not be launched at horizon {n}: {result.stderr.strip()}"
                )

            # Add objbound to phase metadata if it's being used
            self._add_objbound_metadata(is_opt_model, continue_scan)

            # Check for timeout - if so, stop horizon search immediately
            if result.timed_out:
                self.logger.warning(f"Horizon {n} timed out - stopping horizon search")
                # Use a special return value to signal timeout to horizon_search
                raise TimeoutError(f"Solver timed out at horizon {n}")

            if result.success and expected_solution.exists():
                # Handle objective tracking for optimization models
                should_stop = self._handle_objective_tracking(obj_file, n, is_opt_model, continue_scan, expected_solution)

                if should_stop:
                    # This is the final horizon - extract final stats
                    self._update_final_stats('savilerow', result, n)
                    return expected_solution
                elif is_opt_model and continue_scan:
                    # For timeout mode with continue-scan, save the solution but continue searching
                    if self._has_timeout and self._best_objective is not None:
                        # Save this solution as the best so far using standard naming
                        self._save_intermediate_solution(expected_solution, n)
                    return None  # Continue to next horizon
                else:
                    return expected_solution  # Fallback
            else:
                return None

        return horizon_search(
            self.runner, param_file, startat, savilerow_horizon_callback, self.logger,
            min_horizon=min_horizon,
            stopat=stopat,
            extra_constraints_callback=self._add_objective_constraint if (is_opt_model and continue_scan) else None,
            horizon_params=horizon_params
        )

    def _solve_once(self, param_file: Path, model_path) -> Optional[Path]:
        """Tailor and solve a model with no horizon, once, in a ``solving``
        phase whose ``.info`` verdict decides the run."""
        single_param = self._single_param_file(param_file)
        if single_param is None:
            return None
        timeout = self.runner.timeout
        cmd = self._build_savilerow_command(
            str(model_path), str(single_param), timeout)
        expected_solution = single_param.with_suffix('.param.solution')
        self.logger.info(f"Solving {single_param} once (the model has no horizon)")
        result = self.runner.run_phase(
            "solving", cmd, expected_outputs=[str(expected_solution)],
            timeout=self._horizon_watchdog(timeout))
        self._record_savilerow_times(single_param, None)
        self._check_single_solve(result, "Savile Row")
        if result.success and expected_solution.exists():
            self._update_final_stats('savilerow', result, None)
            return expected_solution
        return None


class ConjureSolver(HorizonBasedSolver):
    """Solver implementation for Conjure (Essence to EPrime to solution)."""

    def _default_solver_name(self) -> str:
        """The Savile Row entry the ``conjure`` config names, else the legacy
        name (see :meth:`ModelRegistry.get_conjure_savilerow`)."""
        try:
            return global_state.model_registry.get_conjure_savilerow()
        except Exception:
            return LEGACY_CONJURE_SAVILEROW

    def solve(self, param_file: Path, **kwargs) -> Optional[Path]:
        """
        Execute Conjure solver with horizon search loop for Essence models.
        Args:
            param_file: Path to Essence parameter file
            **kwargs: Additional arguments including:
                - model_path: Path to Essence model file (required)
                - startat: Starting horizon value (default: the model's
                  horizon-start, else 1; never below horizon-start)
                - stopat: Highest horizon to try (default: the model's
                  horizon-stop, else 100)
                - backstop: kill each horizon at savilerow_backstop of its
                  budget rather than at the runner's timeout, so Savile Row's
                  own limits fire first and its statistics survive
                  (default: False)
        Returns:
            Path to solution file if successful, None otherwise
        """
        # Extract required kwargs
        model_path = kwargs.get('model_path')
        if not model_path:
            raise ValueError("model_path is required for Conjure solver")

        self._backstop = bool(kwargs.get('backstop', False))
        scan = self._scan_bounds(kwargs)

        # Step 1: Modelling - create EPrime from Essence
        epm_path = self._do_conjure_modelling(str(model_path))
        if not epm_path:
            return None

        if scan is None:
            single_param = self._single_param_file(param_file)
            if single_param is None:
                return None
            self.logger.info(f"Solving {single_param} once (the model has no horizon)")
            return self._do_conjure_solve_translate(
                single_param, epm_path, None, horizon_timeout=self.runner.timeout)
        startat, min_horizon, stopat = scan

        # Setup optimization parameters
        model_name = self.runner.summary.model
        is_opt_model, continue_scan = self._setup_optimization(model_name, timeout=self.runner.timeout)

        # Track total elapsed time for timeout management
        total_start_time = time.time()
        original_timeout = self.runner.timeout
        horizon_params = self._horizon_params()

        def conjure_horizon_callback(n: int, horizon_param_file: Path) -> Optional[Path]:
            """Execute Conjure solve and translate for a specific horizon."""
            # Calculate remaining timeout for this horizon step
            horizon_timeout = calculate_horizon_timeout(total_start_time, original_timeout, n, self.logger)

            if not self._write_horizon(horizon_param_file, n, horizon_params):
                return None

            # Execute the solve and translate pipeline
            solution_file = self._do_conjure_solve_translate(horizon_param_file, epm_path, n, is_opt_model, continue_scan, horizon_timeout)

            if solution_file:
                # Handle objective tracking for optimization models
                if is_opt_model:
                    obj_file = self._generate_objective_file_path(n)
                else:
                    obj_file = None

                should_stop = self._handle_objective_tracking(obj_file, n, is_opt_model, continue_scan, solution_file)

                if should_stop:
                    # This is the final horizon - extract final stats
                    if is_opt_model and continue_scan:
                        self._update_final_stats('savilerow', type('obj', (), {'stdout': '', 'stderr': '', 'duration': None}), n)
                    return solution_file
                elif is_opt_model and continue_scan:
                    # For timeout mode with continue-scan, save the solution but continue searching
                    if self._has_timeout and self._best_objective is not None:
                        # Save this solution as the best so far using standard naming
                        self._save_intermediate_solution(solution_file, n)
                    return None  # Continue to next horizon
                else:
                    return solution_file
            else:
                return None

        # Step 2: Horizon-extending loop
        return horizon_search(
            self.runner, param_file, startat, conjure_horizon_callback, self.logger,
            min_horizon=min_horizon,
            stopat=stopat,
            extra_constraints_callback=self._add_objective_constraint if (is_opt_model and continue_scan) else None,
            horizon_params=horizon_params
        )

    def _do_conjure_modelling(self, model_path: str) -> Optional[Path]:
        """Execute Conjure modelling phase to create EPrime from Essence."""
        # Check if EPrime file already exists and is newer than the Essence model
        epm_file = Path(f"{Path(model_path).stem}.eprime")

        if epm_file.exists():
            essence_mtime = os.path.getmtime(model_path)
            eprime_mtime = os.path.getmtime(epm_file)

            if eprime_mtime > essence_mtime:
                self.logger.info(f"Reusing existing EPrime model: {epm_file} (newer than {Path(model_path).name})")
                return epm_file
            else:
                self.logger.info(f"EPrime model exists but is older than Essence model, regenerating")

        # Build Conjure modelling command
        modelling_cmd = global_state.model_registry.build_conjure_command(
            action="modelling",
            model_path=model_path
        )

        self.logger.warning("Generating EPrime model from Essence using Conjure")

        # Conjure outputs to conjure-output/model000001.eprime by default (relative to cwd)
        expected_conjure_file = Path("conjure-output") / "model000001.eprime"

        result = self.runner.run_phase("modelling", modelling_cmd, expected_outputs=[str(expected_conjure_file)])

        self.logger.debug(f"Modelling result: success={result.success}, file exists={expected_conjure_file.exists()}, path={expected_conjure_file}")

        if result.success and expected_conjure_file.exists():
            # Move the file to the expected location for consistency
            self.logger.debug(f"Moving {expected_conjure_file} to {epm_file}")
            expected_conjure_file.rename(epm_file)
            self.logger.info(f"EPrime model generated: {epm_file}")
            return epm_file
        else:
            self.logger.error("EPrime model generation failed")
            return None

    def _do_conjure_solve_translate(self, horizon_param_file: Path, epm_path: Path, n: Optional[int], is_opt_model: bool = False, continue_scan: bool = False, horizon_timeout: Optional[int] = None) -> Optional[Path]:
        """Execute Conjure solve and translate phase for a specific horizon.

        ``n`` None is a model solved once: its phases are ``translate-param``,
        ``solving`` and ``translate-solution``, and its files carry no horizon.
        """
        if n is None:
            param_stem, n2, tag = horizon_param_file.stem, "", ""
        else:
            param_stem = horizon_param_file.stem.rsplit('-', 1)[0]  # Remove horizon suffix
            n2, tag = f"-{n:02d}", f"-{n}"

        # Step 1: Translate Essence params to EPrime format
        epp_file = Path(f"{param_stem}{n2}.eprime-param")

        translate_cmd = global_state.model_registry.build_conjure_command(
            action="translate-parameter",
            essence_param=horizon_param_file,
            eprime=epm_path
        )

        result = self.runner.run_phase(
            f"translate-param{tag}",
            translate_cmd,
            expected_outputs=[str(epp_file)]
        )

        if n is None:
            self._check_single_solve(result, "Conjure translate-parameter")
        if not result.success:
            self.logger.error(f"Could not translate Essence param to EPrime for horizon {n}")
            return None

        # Step 2: Run Savile Row on EPrime model
        eps_file = epp_file.with_suffix('.eprime-param.solution')

        # Only generate objective file for optimization models
        obj_file = None
        if is_opt_model:
            # Generate objective file name for this horizon
            obj_file = self._generate_objective_file_path(n)

            # Remove any existing objective file before this horizon run
            self._prepare_objective_file(obj_file)

        # Build Savile Row command for this horizon using helper method
        savilerow_cmd = self._build_savilerow_command(
            str(epm_path), str(epp_file), horizon_timeout, obj_file
        )

        phase_kwargs = {} if n is None else {'horizon_number': n}
        result = self.runner.run_phase(
            "solving" if n is None else f"solving-horizon-{n}",
            savilerow_cmd,
            expected_outputs=[str(eps_file)],
            timeout=self._horizon_watchdog(horizon_timeout),
            **phase_kwargs
        )

        # Record Savile Row's tailoring vs solver time split. Here Savile Row is
        # run directly on the translated eprime-param (not via conjure), so its
        # .info sits next to epp_file, not the essence param file.
        self._record_savilerow_times(epp_file, n)

        # Add objbound to phase metadata if it's being used. continue_scan is the
        # value resolved once in _setup_optimization (registry plus any overrides)
        # and threaded in alongside is_opt_model - not re-read from the registry
        # here, so the metadata can never disagree with the scan behaviour it
        # describes.
        self._add_objbound_metadata(is_opt_model, continue_scan)

        # Check for timeout - if so, stop horizon search immediately
        if n is None:
            self._check_single_solve(result, "Savile Row")
        elif result.timed_out:
            self.logger.warning(f"Horizon {n} timed out - stopping horizon search")
            raise TimeoutError(f"Solver timed out at horizon {n}")

        if not (result.success and eps_file.exists()):
            return None

        self.logger.info(f"EPrime solution found at horizon {n}")

        # Step 3: Translate EPrime solution back to Essence format
        essence_solution = Path(f"{param_stem}{n2}.solution")

        translate_solution_cmd = global_state.model_registry.build_conjure_command(
            action="translate-solution",
            essence_param=horizon_param_file,
            eprime=epm_path,
            eprime_solution=eps_file,
            essence_solution=essence_solution
        )

        result = self.runner.run_phase(
            f"translate-solution{tag}",
            translate_solution_cmd,
            expected_outputs=[str(essence_solution)]
        )

        if not (result.success and essence_solution.exists()):
            self.logger.error(f"Could not translate EPrime solution back to Essence for horizon {n}")
            return None

        self.logger.info(f"Essence solution found at horizon {n}")

        # Create final solution file with proper naming using helper methods
        final_solution = self._generate_solution_file_path(
            suffix=".solution" if n is None else f".{n}.solution")

        if self._copy_solution_file(essence_solution, final_solution):
            return final_solution
        else:
            return essence_solution
