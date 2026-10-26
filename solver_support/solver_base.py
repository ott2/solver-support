"""
Base solver class for puzzle solvers.

This module contains the abstract Solver class that all puzzle solvers inherit from.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional
import os
import shutil

from .file_management import SolutionFileNamer
from .utilities import relativize_path
from .global_state import global_state


class Solver(ABC):
    """Abstract base class for puzzle solvers."""
    
    def __init__(self, solver_runner):
        """
        Initialize solver with a SolverRunner instance.
        
        Args:
            solver_runner: SolverRunner instance that provides logging and phase tracking
        """
        self.runner = solver_runner
        self.logger = solver_runner.logger

    def _effective_tune(self) -> Optional[str]:
        """The effective ``--tune`` for this run.

        An explicit CLI ``--tune`` (held in ``global_state``) wins; otherwise
        the running model's pinned ``default`` (``models.yaml`` ``tune:``), or
        None if neither applies. This is the single resolution shared by the
        fd / enhsp / upfplan / horizon backends - what picks the search config,
        engine, or sat_args when the user passes no ``--tune``. Defensive:
        tolerates a ``global_state`` without ``tune`` and an unknown model.
        """
        explicit = getattr(global_state, 'tune', None)
        if explicit:
            # Rejected here as well as in the builders, so no backend can
            # swallow the error and run its default under the name asked for.
            self._require_known_tune(explicit)
            return explicit
        try:
            registry = global_state.model_registry
            model = self.runner.summary.model
            pinned = registry.get_model_default_tune(model)
            if pinned and not self._knows_tune(pinned):
                # The pin is the model's preference for its own solver. Under
                # another solver that lacks it, the solver's default applies;
                # under its own, an unknown pin is a typo and raises below.
                _, model_config = registry.resolve_model_variant(model)
                own = model_config.get('solver')
                run = self._run_solver_name()
                if own and (registry.get_tune_backend(own)
                            != registry.get_tune_backend(run)):
                    self.logger.debug(
                        f"Model {model}'s tune '{pinned}' is not a "
                        f"{self._run_solver_name()} tune; using its default")
                    return None
        except Exception:
            return None
        self._require_known_tune(pinned)
        return pinned

    def _run_solver_name(self) -> Optional[str]:
        """The registry solver whose tunes this run's ``--tune`` selects."""
        return getattr(self, 'solver_name', None) or getattr(
            getattr(self.runner, 'summary', None), 'solver', None)

    def _knows_tune(self, tune: str) -> bool:
        solver = self._run_solver_name()
        try:
            return global_state.model_registry.is_valid_tune(solver, tune)
        except Exception:
            # An unregistered solver: nothing to check against.
            return True

    def _require_known_tune(self, tune: Optional[str]) -> None:
        if tune and not self._knows_tune(tune):
            global_state.model_registry.require_known_tune(
                self._run_solver_name(), tune)

    @abstractmethod
    def solve(self, param_file: Path, **kwargs) -> Optional[Path]:
        """
        Execute the solver on the given parameter file.

        Args:
            param_file: Path to parameter file
            **kwargs: Additional solver-specific arguments

        Returns:
            Path to solution file if successful, None otherwise

        Note:
            Solver operates in current working directory after chdir.
            All file paths should be relative to current directory.
        """
        pass

    def _generate_solution_file_path(self, suffix: str = ".solution") -> Path:
        """
        Generate a solution file path using consistent naming.

        Args:
            suffix: Optional suffix to append to the filename

        Returns:
            Path to the solution file (relative to current directory)
        """
        instance_name = self.runner.summary.instance
        solver = self.runner.summary.solver
        model = self.runner.summary.model
        timestamp = self.runner.summary.timestamp

        return SolutionFileNamer.generate_solution_path(
            instance_name, solver, model, timestamp,
            tune=self.runner.summary.tune_slot, suffix=suffix
        )

    def _update_phase_output_files(self, phase_name: str, file_path: Path) -> None:
        """
        Update a phase's output files list with a new file.

        Args:
            phase_name: Name of the phase to update
            file_path: Path to the file to add to the phase outputs
        """
        phase = self.runner.summary.get_phase(phase_name)
        if phase:
            file_rel = relativize_path(str(file_path), self.runner.output_dir)
            phase.output_files.append(file_rel)

    def _handle_timeout_result(self, result, solver_name: str) -> None:
        """
        Handle timeout results consistently across solvers.

        Args:
            result: SolverResult instance
            solver_name: Name of the solver for error messages

        Raises:
            TimeoutError: If the result indicates a timeout
        """
        if result.timed_out:
            duration = getattr(result, 'duration', 0)
            self.logger.warning(f"{solver_name} timed out after {duration:.1f}s")
            raise TimeoutError(f"{solver_name} timed out after {duration:.1f}s")

    def _copy_solution_file(self, source: Path, destination: Path, move: bool = False) -> bool:
        """
        Copy or move a solution file with error handling.

        Args:
            source: Source file path
            destination: Destination file path
            move: If True, move the file instead of copying

        Returns:
            True if successful, False otherwise
        """
        try:
            if move:
                os.rename(str(source), str(destination))
                self.logger.info(f"Moved solution to: {destination}")
            else:
                shutil.copy2(str(source), str(destination))
                self.logger.info(f"Copied solution to: {destination}")
            return True
        except Exception as e:
            action = "move" if move else "copy"
            self.logger.error(f"Failed to {action} solution file: {e}")
            return False