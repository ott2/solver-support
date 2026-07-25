"""
ENHSP solver implementation.

This module provides the EnhspSolver class for executing the ENHSP forward-search
PDDL planner directly (running it via the Unified Planning Framework instead is
the separate upfenhsp solver). ENHSP takes the domain/problem as -o/-f and writes
its plan to a -sp file in the same `(action args)` form as Fast Downward's
sas_plan, so the PDDLSolutionParser consumes the result unchanged.
"""

from pathlib import Path
from typing import Optional

from .solver_base import Solver
from .global_state import global_state


class EnhspSolver(Solver):
    """Solver implementation for the ENHSP PDDL planner."""

    def solve(self, param_file: Path, **kwargs) -> Optional[Path]:
        """
        Execute ENHSP for a PDDL model.

        Args:
            param_file: Path to the PDDL problem file.
            **kwargs: Additional arguments including:
                - model_path: Path to the PDDL domain file (required).

        Returns:
            Path to the solution file if a plan was found, None otherwise.
        """
        model_path = kwargs.get('model_path')
        if not model_path:
            raise ValueError("model_path is required for the ENHSP solver")

        solver_name = self.runner.summary.solver or 'enhsp'

        # Optional --tune preset from the CLI, held in global_state like the other
        # backends. build_enhsp_command resolves it against the active solver's
        # planner presets (hmrp/hadd/hrmax/blind) and falls back to default_tune.
        # With no --tune, honour a model-pinned tune (models.yaml `tune:`, e.g.
        # pddlscorednoaxiom pins blind because enhsp's default hmrp/hrmax heuristics
        # NPE on its conditional effects) - the same resolution the upf/horizon
        # paths use.
        tune = self._effective_tune()

        # ENHSP writes its plan straight to the -sp path, so point it at the final
        # solution file name (the same `.1.solution` convention as the fd solver).
        solution_file = self._generate_solution_file_path(suffix=".1.solution")

        # Clean up any stale plan from a previous run.
        if solution_file.exists():
            try:
                solution_file.unlink()
            except Exception as e:
                self.logger.warning(f"Failed to remove existing solution file {solution_file}: {e}")

        cmd = global_state.model_registry.build_enhsp_command(
            model_path=model_path,
            param_path=str(param_file),
            plan_path=str(solution_file),
            tune=tune,
            extra_options=global_state.options,
            solver_name=solver_name
        )

        self.logger.info("Running ENHSP solver")
        # ENHSP writes straight to the final path, so the phase output is recorded
        # in _finalise (only when the file actually exists) rather than via
        # run_phase's expected_outputs, which would list it even on a failed parse.
        result = self.runner.run_phase("solving", cmd)

        if result.timed_out:
            # No internal time limit outside anytime mode: the runner terminated
            # ENHSP mid-search, so no plan was written - a genuine timeout.
            self._handle_timeout_result(result, "ENHSP")

        if result.interrupted:
            self.logger.warning("ENHSP solving was interrupted")
            return self._finalise(solution_file)

        if result.success:
            return self._finalise(solution_file)

        # ENHSP exits non-zero when it cannot parse the model (e.g. the axiom
        # `pddl` domain, which it does not support) or fails to plan. A plan may
        # still have been written on some paths, so salvage it if present.
        salvaged = self._finalise(solution_file)
        if salvaged is None:
            self.logger.error(f"ENHSP solver failed: {result.stderr}")
        return salvaged

    def _finalise(self, solution_file: Path) -> Optional[Path]:
        """Register and return the plan file if ENHSP wrote one, else None."""
        if solution_file.exists():
            self._update_phase_output_files("solving", solution_file)
            self.logger.info(f"ENHSP wrote solution: {solution_file}")
            return solution_file
        self.logger.info("No solution found")
        return None
