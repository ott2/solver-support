#!/usr/bin/env python3
"""
Unit tests for solver classes.
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from solver_support.solver_base import Solver
from solver_support.solver_upfplan import UpfPlanSolver
from solver_support.solver_fd import FastDownwardSolver
from solver_support.solver_enhsp import EnhspSolver
from solver_support.solver_runner import SolverResult
from solver_support.run_summary import PhaseResult
from test_helpers import TempDirTestMixin


class TestSolverBase:
    """Test the abstract Solver base class."""
    
    def test_solver_is_abstract(self):
        """Test that Solver cannot be instantiated directly."""
        mock_runner = Mock()
        with pytest.raises(TypeError):
            Solver(mock_runner)
    
    def test_solver_initialization(self):
        """Test that concrete solver initializes with runner."""
        mock_runner = Mock()
        mock_runner.logger = Mock()
        
        # Create a concrete implementation
        class ConcreteSolver(Solver):
            def solve(self, param_file, output_dir, **kwargs):
                return None
        
        solver = ConcreteSolver(mock_runner)
        assert solver.runner is mock_runner
        assert solver.logger is mock_runner.logger


class TestUPFSolver(TempDirTestMixin):
    """Test UpfPlanSolver in --build mode (a model constructed in Python)."""

    @pytest.fixture(autouse=True)
    def _registry(self, fixture_registry):
        """solve() resolves its engine and FD search config from the registry, so
        these need one wired; the fixture config's upffd supplies both."""

    def setup_method(self):
        """Set up test fixtures."""
        self.setup_temp_working_dir()
        self.mock_runner = Mock()
        self.mock_runner.logger = Mock()
        self.mock_runner.output_dir = self.temp_path
        
        # Mock summary
        self.mock_summary = Mock()
        self.mock_summary.instance = "test-instance"
        self.mock_summary.solver = "upf"
        self.mock_summary.model = "upf"
        self.mock_summary.tune_slot = "defopt"
        self.mock_summary.timestamp = "20240101120000"
        self.mock_runner.summary = self.mock_summary
        
        # Create solver (constructed-model route: --build upf). solver_name=upffd
        # so the engine resolves deterministically to fast-downward. The core has no
        # default UPF CLI, so inject the command the consumer would supply.
        self.solver = UpfPlanSolver(self.mock_runner, solver_name="upffd", build_mode="upf",
                                    upfplan_command="my-upfplan")
    
    def teardown_method(self):
        """Clean up test fixtures."""
        self.teardown_temp_working_dir()
    
    def test_upf_solver_success(self):
        """Test successful UPF solving."""
        # Mock run_phase to return success
        from solver_support.solver_runner import SolverResult
        self.mock_runner.run_phase.return_value = SolverResult(
            command=["my-upfplan", "--build", "upf", "test.param"],
            returncode=0,
            stdout="solution output",
            stderr="",
            duration=10.0
        )
        # Already set to temp_dir in setup_method
        self.mock_summary.phases = []  # Initialize phases list
        
        # Run solver
        param_file = self.temp_path / "test.param"
        param_file.write_text("problem content")

        result = self.solver.solve(param_file)

        # Verify result
        assert result is not None
        assert result.exists()
        assert result.name == "test-instance-upf-defopt-upf-20240101120000.solution"
        assert result.read_text() == "solution output"

        # Verify run_phase was called correctly
        self.mock_runner.run_phase.assert_called_once_with(
            "solving",
            ["my-upfplan", "--build", "upf", "-s", "fast-downward", str(param_file),
             "--fd-search", "astar(blind())"],
            expected_outputs=[]
        )
    
    def test_upf_solver_failure(self):
        """Test UPF solving failure."""
        # Mock run_phase to return failure
        from solver_support.solver_runner import SolverResult
        self.mock_runner.run_phase.return_value = SolverResult(
            command=["my-upfplan", "--build", "upf", "test.param"],
            returncode=1,
            stdout="",
            stderr="Error: solving failed",
            duration=5.0
        )
        # Already set to temp_dir in setup_method
        
        # Run solver
        param_file = self.temp_path / "test.param"
        param_file.write_text("problem content")

        result = self.solver.solve(param_file)

        # Verify result
        assert result is None

        # Verify run_phase was called
        self.mock_runner.run_phase.assert_called_once_with(
            "solving",
            ["my-upfplan", "--build", "upf", "-s", "fast-downward", str(param_file),
             "--fd-search", "astar(blind())"],
            expected_outputs=[]
        )
    
    def test_upf_solver_exception(self):
        """Test UPF solving with timeout."""
        # Mock run_phase to return timeout
        from solver_support.solver_runner import SolverResult
        self.mock_runner.run_phase.return_value = SolverResult(
            command=["my-upfplan", "--build", "upf", "test.param"],
            returncode=-15,  # SIGTERM
            stdout="",
            stderr="",
            duration=60.0,
            timed_out=True
        )
        # Already set to temp_dir in setup_method
        
        # Run solver
        param_file = self.temp_path / "test.param"
        param_file.write_text("problem content")

        # UPF solver raises TimeoutError on timeout
        with pytest.raises(TimeoutError) as exc_info:
            result = self.solver.solve(param_file)

        assert "timed out after 60.0s" in str(exc_info.value)

        # Verify run_phase was called
        self.mock_runner.run_phase.assert_called_once_with(
            "solving",
            ["my-upfplan", "--build", "upf", "-s", "fast-downward", str(param_file),
             "--fd-search", "astar(blind())"],
            expected_outputs=[]
        )


class TestFastDownwardSolver(TempDirTestMixin):
    """Test the FastDownwardSolver class."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.setup_temp_working_dir()
        self.mock_runner = Mock()
        self.mock_runner.logger = Mock()
        self.mock_runner.output_dir = self.temp_path
        self.mock_runner.timeout = None  # Add timeout attribute

        # Mock summary
        self.mock_summary = Mock()
        self.mock_summary.instance = "test-instance"
        self.mock_summary.solver = "fd"
        self.mock_summary.model = "pddl"
        self.mock_summary.tune_slot = "defopt"
        self.mock_summary.timestamp = "20240101120000"
        self.mock_runner.summary = self.mock_summary

        # Create solver
        self.solver = FastDownwardSolver(self.mock_runner)
        
        # Mock global state model registry
        from solver_support.global_state import global_state
        self.mock_model_registry = Mock()
        self.mock_model_registry.build_fd_command.return_value = ["fd", "domain.pddl", "problem.pddl"]
        # No model-pinned tune: the solver falls back through to the CLI/solver
        # default (so the effective tune here is None).
        self.mock_model_registry.get_model_default_tune.return_value = None
        global_state._model_registry = self.mock_model_registry
    
    def teardown_method(self):
        """Clean up test fixtures."""
        self.teardown_temp_working_dir()
        from solver_support.global_state import global_state
        global_state._model_registry = None
    
    def test_fd_solver_success(self):
        """Test successful Fast Downward solving."""
        # Mock run_phase to create sas_plan in current directory (temp dir)
        def mock_run_phase(phase_name, cmd, expected_outputs=None):
            sas_plan = Path("sas_plan")
            sas_plan.write_text("solution content")
            return SolverResult(
                command=cmd,
                returncode=0,
                stdout="Solution found",
                stderr="",
                duration=5.0
            )
        
        self.mock_runner.run_phase = mock_run_phase
        self.mock_summary.get_phase.return_value = Mock(output_files=[])
        
        # Run solver
        param_file = self.temp_path / "problem.pddl"
        param_file.write_text("problem content")
        output_dir = self.temp_path / "output"
        output_dir.mkdir()
        
        result = self.solver.solve(
            param_file=param_file,
            output_dir=output_dir,
            model_path="domain.pddl",
            anytime=False
        )
        
        # Verify result
        assert result is not None
        assert result.exists()
        assert result.name == "test-instance-fd-defopt-pddl-20240101120000.1.solution"
        assert result.read_text() == "solution content"
        
        # Verify command was built correctly
        self.mock_model_registry.build_fd_command.assert_called_once_with(
            model_path="domain.pddl",
            param_path=str(param_file),
            anytime=False,
            timeout=None,
            tune=None,
            extra_options=[],
            solver_name="fd"
        )
        
        # No cleanup needed - files are in temp directory which is cleaned automatically
    
    def test_fd_solver_no_solution(self):
        """Test Fast Downward solving with no solution found."""
        # Mock run_phase
        self.mock_runner.run_phase.return_value = SolverResult(
            command=["fd", "domain.pddl", "problem.pddl"],
            returncode=0,
            stdout="No solution",
            stderr="",
            duration=3.0
        )
        
        # Run solver
        param_file = self.temp_path / "problem.pddl"
        param_file.write_text("problem content")
        output_dir = self.temp_path / "output"
        output_dir.mkdir()
        
        result = self.solver.solve(
            param_file=param_file,
            output_dir=output_dir,
            model_path="domain.pddl",
            anytime=False
        )
        
        # Verify result
        assert result is None
        self.mock_runner.logger.info.assert_any_call("No solution found")
    
    def test_fd_solver_failure(self):
        """Test Fast Downward solving failure."""
        # Mock run_phase to fail
        self.mock_runner.run_phase.return_value = SolverResult(
            command=["fd", "domain.pddl", "problem.pddl"],
            returncode=1,
            stdout="",
            stderr="Error: invalid domain",
            duration=0.5
        )
        
        # Run solver
        param_file = self.temp_path / "problem.pddl"
        param_file.write_text("problem content")
        output_dir = self.temp_path / "output"
        output_dir.mkdir()
        
        result = self.solver.solve(
            param_file=param_file,
            output_dir=output_dir,
            model_path="domain.pddl",
            anytime=False
        )
        
        # Verify result
        assert result is None
        self.mock_runner.logger.error.assert_called()
        assert "Fast Downward solver failed" in self.mock_runner.logger.error.call_args[0][0]
    
    def test_fd_solver_interrupted(self):
        """Test Fast Downward solving interrupted."""
        # Mock run_phase to be interrupted but with partial solution
        def mock_run_phase(phase_name, cmd, expected_outputs=None):
            sas_plan = Path("sas_plan")
            sas_plan.write_text("partial solution")
            return SolverResult(
                command=cmd,
                returncode=-1,
                stdout="Interrupted",
                stderr="",
                duration=10.0,
                interrupted=True
            )
        
        self.mock_runner.run_phase = mock_run_phase
        self.mock_summary.get_phase.return_value = Mock(output_files=[])
        
        # Run solver
        param_file = self.temp_path / "problem.pddl"
        param_file.write_text("problem content")
        output_dir = self.temp_path / "output"
        output_dir.mkdir()
        
        result = self.solver.solve(
            param_file=param_file,
            output_dir=output_dir,
            model_path="domain.pddl",
            anytime=False
        )
        
        # Verify result - should handle partial solution
        assert result is not None
        assert result.exists()
        assert result.read_text() == "partial solution"
        
        # No cleanup needed - files are in temp directory which is cleaned automatically
    
    def test_fd_solver_anytime_mode(self):
        """Test Fast Downward solving in anytime portfolio mode."""
        # Mock run_phase to create multiple sas_plan files
        def mock_run_phase(phase_name, cmd, expected_outputs=None):
            # Create multiple solution files
            Path("sas_plan.1").write_text("solution 1")
            Path("sas_plan.2").write_text("solution 2")
            Path("sas_plan.3").write_text("solution 3")
            return SolverResult(
                command=cmd,
                returncode=0,
                stdout="plan manager: found new plan with cost\n" * 3,
                stderr="",
                duration=15.0
            )
        
        self.mock_runner.run_phase = mock_run_phase
        self.mock_summary.get_phase.return_value = Mock(output_files=[])

        # Run solver
        param_file = self.temp_path / "problem.pddl"
        param_file.write_text("problem content")

        result = self.solver.solve(
            param_file=param_file,
            model_path="domain.pddl",
            anytime=True
        )
        
        # Verify result - should return the best (last) solution
        assert result is not None
        assert result.exists()
        assert result.name == "test-instance-fd-defopt-pddl-20240101120000.3.solution"
        assert result.read_text() == "solution 3"
        
        # Verify all solutions were processed (files are in current directory now)
        assert Path("test-instance-fd-defopt-pddl-20240101120000.1.solution").exists()
        assert Path("test-instance-fd-defopt-pddl-20240101120000.2.solution").exists()
        
        # Clean up
        for f in Path(".").glob("sas_plan*"):
            f.unlink()
    
    def test_fd_solver_anytime_interrupted(self):
        """Test Fast Downward solving in anytime portfolio mode when interrupted."""
        # Mock run_phase to simulate interrupted run with saved solutions
        mock_result = SolverResult(
            command=["fd", "domain.pddl", "problem.pddl"],
            returncode=-1,
            stdout="plan manager: found new plan with cost\n" * 2,
            stderr="",
            duration=20.0,
            interrupted=True,
            best_plan_number=2,
            saved_solutions={
                1: Path(self.temp_dir) / "temp_sol_1",
                2: Path(self.temp_dir) / "temp_sol_2"
            }
        )
        
        # Create the temporary solution files
        mock_result.saved_solutions[1].write_text("saved solution 1")
        mock_result.saved_solutions[2].write_text("saved solution 2")
        
        self.mock_runner.run_phase.return_value = mock_result
        self.mock_summary.get_phase.return_value = Mock(output_files=[])

        # Run solver
        param_file = self.temp_path / "problem.pddl"
        param_file.write_text("problem content")

        result = self.solver.solve(
            param_file=param_file,
            model_path="domain.pddl",
            anytime=True
        )
        
        # Verify result - should use saved solution
        assert result is not None
        assert result.exists()
        assert result.name == "test-instance-fd-defopt-pddl-20240101120000.2.solution"
        assert result.read_text() == "saved solution 2"
        
        # Verify temp files were cleaned up
        assert not mock_result.saved_solutions[2].exists()
    
    def test_fd_solver_missing_model_path(self):
        """Test Fast Downward solver with missing model_path."""
        param_file = self.temp_path / "problem.pddl"
        output_dir = self.temp_path / "output"
        output_dir.mkdir()
        
        with pytest.raises(ValueError, match="model_path is required"):
            self.solver.solve(
                param_file=param_file,
                output_dir=output_dir
            )


class TestEnhspSolver(TempDirTestMixin):
    """Test the EnhspSolver class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.setup_temp_working_dir()
        self.mock_runner = Mock()
        self.mock_runner.logger = Mock()
        self.mock_runner.output_dir = self.temp_path
        self.mock_runner.timeout = None

        self.mock_summary = Mock()
        self.mock_summary.instance = "test-instance"
        self.mock_summary.solver = "enhsp"
        self.mock_summary.model = "pddlnoaxiom"
        self.mock_summary.tune_slot = "defopt"
        self.mock_summary.timestamp = "20240101120000"
        self.mock_runner.summary = self.mock_summary

        self.solver = EnhspSolver(self.mock_runner)

        from solver_support.global_state import global_state
        self.mock_model_registry = Mock()
        # ENHSP writes to the -sp path (arg index 6); the solver passes the final
        # solution path there, so the mock just returns a plausible command.
        self.mock_model_registry.build_enhsp_command.return_value = [
            "enhsp", "-o", "domain.pddl", "-f", "problem.pddl",
            "-planner", "sat-hmrp", "-sp", "plan.solution"
        ]
        # No model-pinned tune (effective tune resolves to None here).
        self.mock_model_registry.get_model_default_tune.return_value = None
        global_state._model_registry = self.mock_model_registry

    def teardown_method(self):
        """Clean up test fixtures."""
        self.teardown_temp_working_dir()
        from solver_support.global_state import global_state
        global_state._model_registry = None

    def test_enhsp_solver_success(self):
        """A written plan file is registered and returned."""
        expected_name = "test-instance-enhsp-defopt-pddlnoaxiom-20240101120000.1.solution"

        def mock_run_phase(phase_name, cmd, expected_outputs=None):
            # ENHSP writes its plan straight to the -sp path (the final name).
            Path(expected_name).write_text("(matching_blocks )\n")
            return SolverResult(
                command=cmd, returncode=0, stdout="", stderr="", duration=1.0
            )

        self.mock_runner.run_phase = mock_run_phase
        self.mock_summary.get_phase.return_value = Mock(output_files=[])

        result = self.solver.solve(
            param_file=self.temp_path / "problem.pddl",
            model_path="domain.pddl"
        )

        assert result is not None
        assert result.name == expected_name
        assert result.exists()

    def test_enhsp_solver_no_plan_is_failure(self):
        """A non-zero exit with no plan written (e.g. axiom parse error) fails."""
        self.mock_runner.run_phase.return_value = SolverResult(
            command=["enhsp"], returncode=255, stdout="",
            stderr="Some Syntax Error", duration=0.1
        )
        self.mock_summary.get_phase.return_value = Mock(output_files=[])

        result = self.solver.solve(
            param_file=self.temp_path / "problem.pddl",
            model_path="domain.pddl"
        )

        assert result is None

    def test_enhsp_solver_timeout(self):
        """A timed-out run (no plan) raises TimeoutError."""
        self.mock_runner.run_phase.return_value = SolverResult(
            command=["enhsp"], returncode=-1, stdout="", stderr="",
            duration=30.0, timed_out=True
        )

        with pytest.raises(TimeoutError):
            self.solver.solve(
                param_file=self.temp_path / "problem.pddl",
                model_path="domain.pddl"
            )

    def test_enhsp_solver_missing_model_path(self):
        """Missing model_path raises ValueError."""
        with pytest.raises(ValueError, match="model_path is required"):
            self.solver.solve(param_file=self.temp_path / "problem.pddl")