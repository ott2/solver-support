"""
Fast Downward solver implementation.

This module provides the FastDownwardSolver class for executing the Fast Downward
PDDL planner with support for both standard and anytime (portfolio) modes.
"""

import os
from pathlib import Path
from typing import Optional

from .solver_base import Solver
from .solver_runner import SolverResult
from .file_management import SolutionFileNamer
from .utilities import relativize_path
from .global_state import global_state


class FastDownwardSolver(Solver):
    """Solver implementation for Fast Downward PDDL planner."""

    def solve(self, param_file: Path, **kwargs) -> Optional[Path]:
        """
        Execute Fast Downward solver for PDDL models.

        Args:
            param_file: Path to PDDL problem file
            **kwargs: Additional arguments including:
                - model_path: Path to PDDL domain file (required)
                - anytime: Whether to run the anytime portfolio (default: False).
                  The runner decides this via registry.is_anytime_tune (the
                  active tune's regime), not the model name.
                - instance_file: Original problem instance file

        Returns:
            Path to solution file if successful, None otherwise
        """
        # Extract required kwargs
        model_path = kwargs.get('model_path')
        if not model_path:
            raise ValueError("model_path is required for Fast Downward solver")

        anytime = kwargs.get('anytime', False)
        instance_file = kwargs.get('instance_file')

        # The resolved solver name selects the fd-type config. SymK is a Fast
        # Downward fork, so it flows through this same solver; its config just
        # supplies command:symk and the symbolic search_args.
        solver_name = self.runner.summary.solver or 'fd'

        # Optional --tune preset from the CLI, held in global_state like the
        # savilerow extra-option flags. A single flag covers both engines;
        # build_fd_command resolves it against the active solver's tune presets
        # (fd: blind/hmax/ff/lmcut/verbose; symk: fw/bw/bd) and falls back to the
        # solver's default_tune if the name is not valid for this solver. With no
        # --tune, honour a model-pinned tune (models.yaml `tune:`) - the same
        # resolution the upf/horizon paths use - so a model can select a working
        # non-default config as its default.
        tune = self._effective_tune()

        # Build command using global model registry. Raw --options are appended
        # unchanged (the runner does not interpret them).
        cmd = global_state.model_registry.build_fd_command(
            model_path=model_path,
            param_path=str(param_file),
            anytime=anytime,
            timeout=self.runner.timeout,
            tune=tune,
            extra_options=global_state.options,
            solver_name=solver_name
        )

        # Execute solver
        expected_solution = Path("sas_plan")  # FD writes to current directory

        # Clean up any existing solution files
        for existing in Path.cwd().glob("sas_plan*"):
            try:
                existing.unlink()
            except Exception as e:
                self.logger.warning(f"Failed to remove existing solution file {existing}: {e}")

        self.logger.info("Running Fast Downward solver")
        result = self.runner.run_phase("solving", cmd, expected_outputs=[str(expected_solution)])

        if result.interrupted:
            self.logger.warning("Fast Downward solving was interrupted")
            # Check if partial solution exists
            if anytime:
                return self._handle_anytime_solutions(result)
            elif expected_solution.exists():
                return self._handle_single_solution(expected_solution)
            return None

        if result.timed_out:
            self.logger.info(f"Fast Downward solver timed out after {result.duration:.1f}s")
            if anytime:
                # Anytime portfolio: check if any plans were banked before the budget ran out
                return self._handle_anytime_solutions(result)
            else:
                # For regular PDDL, timeout with no solution is a timeout error
                raise TimeoutError(f"Fast Downward solver timed out after {result.duration:.1f}s")

        if result.success:
            if anytime:
                return self._handle_anytime_solutions(result)
            else:
                return self._handle_single_solution(expected_solution)
        else:
            # Anytime portfolio tolerance: exit code 34 is expected when some
            # portfolio configurations don't support axioms (as in original run
            # script). This tolerance is specific to the seq-sat-fdss portfolio,
            # currently the only anytime fd tune.
            if (anytime and ("Error: Unexpected exit codes: [34]" in result.stderr or
                                "Error: Unexpected exit codes: [34]" in result.stdout)):
                if global_state.verbose:
                    self.logger.info("Anytime portfolio encountered expected exit code 34 (unsupported features), continuing with available solutions")

                solution_file = self._handle_anytime_solutions(result)
                if solution_file is None:
                    # No solutions found in anytime mode - this typically happens when the
                    # portfolio runs out of time (components get shorter time allocations)
                    # Treat this as a timeout case, not a failure (same pattern as UPF solver)
                    if global_state.verbose:
                        self.logger.info("Anytime portfolio found no solutions within timeout - treating as timeout")
                    raise TimeoutError(f"Anytime portfolio timed out after {result.duration:.1f}s with no solutions found")

                return solution_file
            else:
                self.logger.error(f"Fast Downward solver failed: {result.stderr}")
                return None

    def _handle_single_solution(self, expected_solution: Path) -> Optional[Path]:
        """Handle single PDDL solution (standard mode)."""
        if expected_solution.exists():
            solution_file = self._generate_solution_file_path(suffix=".1.solution")

            if self._copy_solution_file(expected_solution, solution_file, move=True):
                # Update the phase output files to reflect the moved file
                solving_phase = self.runner.summary.get_phase("solving")
                if solving_phase:
                    expected_solution_rel = str(expected_solution)
                    solution_file_rel = str(solution_file)

                    # Replace the expected path with actual path in output_files
                    if expected_solution_rel in solving_phase.output_files:
                        solving_phase.output_files.remove(expected_solution_rel)
                    solving_phase.output_files.append(solution_file_rel)

                return solution_file
            else:
                return None
        else:
            self.logger.info("No solution found")
            return None

    def _handle_anytime_solutions(self, result: SolverResult) -> Optional[Path]:
        """Handle multiple PDDL solutions (anytime portfolio mode)."""
        # For interrupted runs, use the best plan tracked during streaming
        if result.interrupted and hasattr(result, 'best_plan_number') and result.best_plan_number > 0:
            plancount = result.best_plan_number
            self.logger.info(f"Anytime portfolio was interrupted, using best plan found so far: {plancount}")
        else:
            # Extract plan count from output by counting "plan manager: found new plan" lines
            plancount = 0
            for line in result.stdout.splitlines():
                if "plan manager: found new plan with cost" in line:
                    plancount += 1

            self.logger.info(f"Anytime portfolio found {plancount} plans")

        if plancount > 0:
            solution_files = []
            solving_phase = self.runner.summary.get_phase("solving")

            # For interrupted runs with saved solutions, use those instead of original files
            if (result.interrupted and hasattr(result, 'saved_solutions') and
                result.saved_solutions and result.best_plan_number in result.saved_solutions):

                self.logger.info(f"Using saved solutions from interrupted run")
                # Copy the best saved solution to final location
                self._bank_solution(
                    result.saved_solutions[result.best_plan_number],
                    result.best_plan_number, solving_phase, solution_files)
            else:
                # Original logic: move all numbered solution files
                for n in range(1, plancount + 1):
                    # First check if we have a saved solution for this plan
                    if (hasattr(result, 'saved_solutions') and
                        result.saved_solutions and n in result.saved_solutions):

                        self._bank_solution(result.saved_solutions[n], n,
                                            solving_phase, solution_files)
                    else:
                        # Original file-based logic for non-interrupted runs
                        current_solution = Path(f"sas_plan.{n}")

                        if current_solution.exists():
                            self._bank_solution(current_solution, n,
                                                solving_phase, solution_files,
                                                move=True)

            if solution_files:
                self.logger.info(f"Processed {len(solution_files)} solution files")
                # Return the best solution (highest numbered, like original line 630)
                return solution_files[-1]
            else:
                self.logger.error("No solution files could be processed")
                return None
        else:
            self.logger.info("No solutions found")
            return None

    def _bank_solution(self, source: Path, plan_num: int, solving_phase,
                       solution_files: list, move: bool = False) -> None:
        """Bank one anytime plan file under the run's numbered solution name.

        Generates the ``.{plan_num}.solution`` path, then either moves
        (``move=True``, for the planner's own ``sas_plan.N`` files) or copies
        (``move=False``, for in-memory saved temps, which are unlinked once
        banked) ``source`` onto it, recording the result in ``solution_files``
        and the solving phase's ``output_files``. Failures are logged and
        swallowed, matching the per-plan best-effort banking of the portfolio
        loop; the copy-vs-move split also selects the log/error wording.
        """
        solution_file = SolutionFileNamer.generate_solution_path(
            self.runner.summary.instance,
            self.runner.summary.solver,
            self.runner.summary.model,
            self.runner.summary.timestamp,
            tune=self.runner.summary.tune_slot,
            suffix=f".{plan_num}.solution"
        )

        try:
            if move:
                os.rename(str(source), str(solution_file))
            else:
                import shutil
                shutil.copy2(str(source), str(solution_file))
            solution_files.append(solution_file)

            # Update phase output files
            if solving_phase:
                solving_phase.output_files.append(relativize_path(str(solution_file), self.runner.output_dir))

            if move:
                self.logger.info(f"Moved solution {plan_num} to: {solution_file}")
            else:
                # Clean up temporary file
                try:
                    source.unlink()
                except:
                    pass  # Ignore cleanup errors
                self.logger.info(f"Used saved solution for plan {plan_num}")

        except Exception as e:
            if move:
                self.logger.error(f"Failed to move solution file {plan_num}: {e}")
            else:
                self.logger.error(f"Failed to copy saved solution file {plan_num}: {e}")