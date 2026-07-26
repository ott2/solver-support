#!/usr/bin/env python3
"""
Tests for SolverRunner class.

Tests the robust subprocess runner including process management, timeout handling,
signal handling, logging capabilities, phase tracking, summary generation,
and integration with statistics extraction.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import subprocess
import tempfile
import signal
import os
import time
import json
from pathlib import Path

from solver_support.solver_runner import SolverRunner, SolverResult
from solver_support.run_summary import RunSummary, PhaseResult
from solver_support import global_state


class TestSolverResult:
    """Test suite for SolverResult class."""
    
    def test_solver_result_creation(self):
        """Test basic SolverResult creation."""
        result = SolverResult(
            command=['echo', 'test'],
            returncode=0,
            stdout='test\n',
            stderr='',
            duration=0.1
        )
        
        assert result.command == ['echo', 'test']
        assert result.returncode == 0
        assert result.stdout == 'test\n'
        assert result.stderr == ''
        assert result.duration == 0.1
        assert not result.timed_out
        assert not result.interrupted
    
    def test_solver_result_success(self):
        """Test success property."""
        # Successful result
        result = SolverResult(['echo', 'test'], 0, 'test', '', 0.1)
        assert result.success
        assert not result.failed
        
        # Failed result (non-zero return code)
        result = SolverResult(['false'], 1, '', '', 0.1)
        assert not result.success
        assert result.failed
        
        # Timed out result
        result = SolverResult(['sleep', '10'], 0, '', '', 0.1, timed_out=True)
        assert not result.success
        assert result.failed
        
        # Interrupted result
        result = SolverResult(['sleep', '10'], 0, '', '', 0.1, interrupted=True)
        assert not result.success
        assert result.failed
    
    def test_solver_result_string_representation(self):
        """Test string representation of SolverResult."""
        # Success
        result = SolverResult(['echo', 'test'], 0, 'test', '', 0.1)
        result_str = str(result)
        assert 'SUCCESS' in result_str
        assert 'echo test' in result_str
        assert 'rc=0' in result_str
        assert 't=0.10s' in result_str
        
        # Failure
        result = SolverResult(['false'], 1, '', '', 0.1)
        result_str = str(result)
        assert 'FAILED' in result_str
        
        # Timeout
        result = SolverResult(['sleep', '10'], 1, '', '', 0.1, timed_out=True)
        result_str = str(result)
        assert 'TIMEOUT' in result_str
        
        # Interrupted
        result = SolverResult(['sleep', '10'], 1, '', '', 0.1, interrupted=True)
        result_str = str(result)
        assert 'INTERRUPTED' in result_str


class TestSolverRunner:
    """Test suite for SolverRunner class."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.log_file = Path(self.temp_dir) / 'test.log'
        yield  # Run the test
        # Cleanup
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_solver_runner_creation(self):
        """Test basic SolverRunner creation."""
        runner = SolverRunner()
        assert runner.log_file is None
        assert runner.timeout is None
        assert runner.logger is not None
    
    def test_solver_runner_with_log_file(self):
        """Test SolverRunner creation with log file."""
        runner = SolverRunner(log_file=self.log_file)
        assert runner.log_file == self.log_file
        assert len(runner.logger.handlers) >= 2  # Console + file handler
    
    def test_solver_runner_with_timeout(self):
        """Test SolverRunner creation with default timeout."""
        runner = SolverRunner(timeout=30.0)
        assert runner.timeout == 30.0
    
    
    def test_simple_command_execution(self):
        """Test running a simple command."""
        runner = SolverRunner()
        result = runner.run(['echo', 'hello world'])
        
        assert result.success
        assert result.returncode == 0
        assert 'hello world' in result.stdout
        assert result.stderr == ''
        assert result.duration > 0
        assert not result.timed_out
        assert not result.interrupted
    
    def test_command_with_failure(self):
        """Test running a command that fails."""
        runner = SolverRunner()
        result = runner.run(['false'])
        
        assert not result.success
        assert result.failed
        assert result.returncode != 0
        assert not result.timed_out
        assert not result.interrupted
    
    def test_command_with_stderr(self):
        """Test running a command that produces stderr."""
        runner = SolverRunner()
        # Use a command that writes to stderr
        result = runner.run(['sh', '-c', 'echo "error message" >&2'])
        
        assert result.success  # sh -c should succeed even if it writes to stderr
        assert 'error message' in result.stderr
    
    def test_command_with_timeout(self):
        """Test command timeout functionality."""
        runner = SolverRunner()
        result = runner.run(['sleep', '10'], timeout=0.1)
        
        assert not result.success
        assert result.failed
        assert result.timed_out
        assert result.duration >= 0.1
        assert result.duration < 5.0  # Should not take too long to timeout
    
    def test_command_with_current_directory(self):
        """Test running command that shows current directory."""
        runner = SolverRunner(output_dir=Path(self.temp_dir))
        
        # Since we changed to output_dir, pwd should show temp_dir
        # But we need to actually change to that directory first
        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(self.temp_dir)
            result = runner.run(['pwd'])
            assert result.success
            assert self.temp_dir in result.stdout
        finally:
            os.chdir(original_cwd)
    
    def test_command_with_environment(self):
        """Test running command with custom environment variables."""
        runner = SolverRunner()
        result = runner.run(['sh', '-c', 'echo $TEST_VAR'],
                          env={'TEST_VAR': 'test_value'})
        
        assert result.success
        assert 'test_value' in result.stdout
    
    def test_run_simple_interface(self):
        """Test the run_simple interface that mimics original doit()."""
        runner = SolverRunner()
        
        # Test successful command
        failed = runner.run_simple('echo', 'hello')
        assert not failed
        
        # Test failed command
        failed = runner.run_simple('false')
        assert failed
    
    def test_default_timeout_usage(self):
        """Test that default timeout is used when no timeout specified."""
        runner = SolverRunner(timeout=0.1)
        result = runner.run(['sleep', '10'])
        
        assert result.timed_out
        assert result.duration >= 0.1
        assert result.duration < 5.0
    
    @patch('solver_support.solver_runner.psutil')
    def test_process_tree_termination(self, mock_psutil):
        """Test process tree termination functionality."""
        # Mock psutil for process tree termination
        mock_parent = Mock()
        mock_child = Mock()
        mock_parent.children.return_value = [mock_child]
        mock_psutil.Process.return_value = mock_parent
        mock_psutil.wait_procs.return_value = ([mock_parent, mock_child], [])
        
        runner = SolverRunner()
        
        # Create a mock process
        mock_process = Mock()
        mock_process.pid = 12345
        
        # Test process tree termination
        runner._terminate_process_tree(mock_process)
        
        # Verify that terminate was called on child and parent
        mock_child.terminate.assert_called_once()
        mock_parent.terminate.assert_called_once()
    
    def test_signal_handler_setup(self):
        """Test that SolverRunner can be created without signal handling."""
        from solver_support import global_state
        
        # Reset global state for clean test
        global_state.interrupt_count = 0
        
        runner = SolverRunner()
        
        # Test that the runner can be created without errors
        assert not global_state.interrupted
        
        # SolverRunner no longer handles signals directly
        # Signal handling is now managed by the consumer's top-level runner
    
    def test_logger_setup_console_only(self):
        """Test logger setup with console handler only."""
        # Reset global_state to ensure clean test
        global_state.debug = False
        global_state.verbose = False
        
        runner = SolverRunner()
        
        assert len(runner.logger.handlers) >= 1  # At least console handler
        # Logger itself is always DEBUG, but console handler should respect log level
        console_handler = runner.logger.handlers[0]  # First handler should be console
        assert console_handler.level == 30  # logging.WARNING (new default)
    
    def test_logger_setup_with_file(self):
        """Test logger setup with file handler."""
        runner = SolverRunner(log_file=self.log_file)
        
        assert len(runner.logger.handlers) >= 2  # Console + file handler
        assert self.log_file.parent.exists()
    
    def test_timing_command_integration(self):
        """Test internal timing integration."""
        runner = SolverRunner()
        
        # Run command and check timing is tracked
        result = runner.run(['echo', 'hello'])
        assert result.success
        assert result.duration > 0
    
    def test_long_output_handling(self):
        """Test handling of commands with long output."""
        runner = SolverRunner()
        
        # Generate long output
        long_text = 'x' * 2000
        result = runner.run(['echo', long_text])
        
        assert result.success
        assert long_text in result.stdout
    
    @patch('subprocess.Popen')
    def test_subprocess_exception_handling(self, mock_popen):
        """Test handling of subprocess exceptions."""
        mock_popen.side_effect = OSError("Mock subprocess error")
        
        runner = SolverRunner()
        result = runner.run(['echo', 'test'])
        
        assert not result.success
        assert result.returncode == -1
        assert "Mock subprocess error" in result.stderr
        assert result.interrupted
    
    def test_command_with_special_characters(self):
        """Test running commands with special characters."""
        runner = SolverRunner()
        
        # Test with quotes and special characters
        result = runner.run(['echo', 'hello "world"'])
        assert result.success
        assert 'hello "world"' in result.stdout
    
    def test_empty_command_handling(self):
        """Test handling of empty commands."""
        runner = SolverRunner()
        
        # Empty command should fail
        result = runner.run([])
        assert not result.success


class TestSolverRunnerIntegration:
    """Integration tests for SolverRunner."""
    
    def test_real_command_execution(self):
        """Test executing real system commands."""
        runner = SolverRunner()
        
        # Test basic command
        result = runner.run(['echo', 'integration test'])
        assert result.success
        assert 'integration test' in result.stdout
        
        # Test command with arguments
        result = runner.run(['ls', '/'])
        assert result.success
        assert len(result.stdout.strip()) > 0
    
    def test_command_chaining_behavior(self):
        """Test behavior with shell command chaining."""
        runner = SolverRunner()
        
        # Test simple shell command
        result = runner.run(['sh', '-c', 'echo "first" && echo "second"'])
        assert result.success
        assert 'first' in result.stdout
        assert 'second' in result.stdout
    
    def test_python_command_execution(self):
        """Test executing Python commands."""
        runner = SolverRunner()
        
        # Test Python one-liner
        result = runner.run(['python', '-c', 'print("hello from python")'])
        assert result.success
        assert 'hello from python' in result.stdout


class TestSolverRunnerEnhancedFeatures:
    """Test enhanced SolverRunner features including phase tracking and summary generation."""
    
    def test_enhanced_initialization(self):
        """Test SolverRunner initialization with enhanced parameters."""
        summary_file = Path("/tmp/test.summary")
        
        runner = SolverRunner(
            summary_file=summary_file,
            instance="sample-instance",
            model="demo",
            solver="kissat",
            log_file=Path("/tmp/test.log")
        )
        
        assert runner.summary_file == summary_file
        assert runner.summary.instance == "sample-instance"
        assert runner.summary.model == "demo"
        assert runner.summary.solver == "kissat"
        assert runner.log_file == Path("/tmp/test.log")
    
    def test_enhanced_initialization_defaults(self):
        """Test SolverRunner with enhanced defaults."""
        runner = SolverRunner()
        
        assert runner.summary_file is None
        assert runner.summary.instance == ""
        assert runner.summary.model == ""
        assert runner.summary.solver == ""
        assert runner.summary.timestamp != ""
        assert runner.summary.start_time is not None
        assert len(runner.summary.phases) == 0
    
    @patch('solver_support.solver_runner.SolverRunner.run')
    def test_successful_phase_execution(self, mock_run):
        """Test successful phase execution with statistics extraction."""
        # Mock successful solver result
        mock_result = SolverResult(
            command=["savilerow", "model.eprime"],
            returncode=0,
            stdout="Created solution file test-01.param.solution\n        1.25 real         1.10 user         0.08 sys",
            stderr="",
            duration=1.25
        )
        # Patch the run method that we're calling, not the one we're extending
        mock_run.return_value = mock_result
        
        runner = SolverRunner(instance="test", model="compact")
        
        # Mock the actual run method call
        with patch.object(runner, 'run', return_value=mock_result):
            # Execute phase
            result = runner.run_phase(
                "solving",
                ["savilerow", "model.eprime", "problem.param"],
                expected_outputs=["solution.txt"]
            )
        
        # Verify result
        assert result == mock_result
        assert len(runner.summary.phases) == 1
        
        phase = runner.summary.phases[0]
        assert phase.name == "solving"
        assert phase.command == ["savilerow", "model.eprime", "problem.param"]
        assert phase.duration == 1.25
        assert phase.success is True
        assert phase.error_message == ""
        
        # Verify solver stats were extracted
        assert 'solution_found' in phase.solver_stats
        assert phase.solver_stats['solution_found'] is True
        assert 'real_time' in phase.solver_stats
        assert phase.solver_stats['real_time'] == 1.25
        
        # Verify summary was updated
        assert runner.summary.total_duration == 1.25
        assert runner.summary.solution_found is True
    
    @patch('solver_support.solver_runner.SolverRunner.run')
    def test_failed_phase_execution(self, mock_run):
        """Test failed phase execution handling."""
        # Mock failed solver result
        mock_result = SolverResult(
            command=["savilerow", "model.eprime"],
            returncode=1,
            stdout="",
            stderr="Error: Could not parse model file",
            duration=0.5
        )
        
        runner = SolverRunner()
        
        with patch.object(runner, 'run', return_value=mock_result):
            # Execute phase
            result = runner.run_phase("solving", ["savilerow", "model.eprime"])
        
        # Verify result
        assert result == mock_result
        assert len(runner.summary.phases) == 1
        
        phase = runner.summary.phases[0]
        assert phase.name == "solving"
        assert phase.duration == 0.5
        assert phase.success is False
        assert phase.error_message == "Error: Could not parse model file"
    
    def test_empty_command_validation(self):
        """Test that empty command raises ValueError."""
        runner = SolverRunner()
        
        with pytest.raises(ValueError, match="Command cannot be empty"):
            runner.run_phase("solving", [])
    
    def test_expected_outputs_detection(self):
        """Test detection of expected output files."""
        # Mock successful result
        mock_result = SolverResult(
            command=["my-translate", "--format", "compact", "test.param"],
            returncode=0,
            stdout="Generated test.param",
            stderr="",
            duration=0.1
        )
        
        runner = SolverRunner()
        
        # Create some test files
        with tempfile.TemporaryDirectory() as tmpdir:
            test_param = Path(tmpdir) / "test.param"
            missing_txt = Path(tmpdir) / "missing.txt"
            test_param.touch()
            missing_txt.touch()
            
            with patch.object(runner, 'run', return_value=mock_result):
                # Execute phase with expected outputs
                runner.run_phase(
                    "translation",
                    ["my-translate", "--format", "compact", "test.param"],
                    expected_outputs=[str(test_param), str(missing_txt), "nonexistent.file"]
                )
                
                phase = runner.summary.phases[0]
                assert set(phase.output_files) == {str(test_param), str(missing_txt)}
    
    @patch('solver_support.solver_runner.SolverRunner.run')
    def test_multiple_phases_execution(self, mock_run):
        """Test execution of multiple phases and summary aggregation."""
        # Mock different results for different phases
        def mock_run_side_effect(*args, **kwargs):
            command = args[0] if args else kwargs.get('command', [])
            if "my-translate" in command and "--format" in command and "compact" in command:
                return SolverResult(command, 0, "Generated param file", "", 0.1)
            elif "savilerow" in command:
                return SolverResult(command, 0, "Created solution file test-03.param.solution\n        2.5 real         2.0 user         0.1 sys", "", 2.5)
            elif "vis.py" in command:
                return SolverResult(command, 0, "Visualisation complete", "", 0.2)
        
        runner = SolverRunner(instance="test", model="compact")
        
        with patch.object(runner, 'run', side_effect=mock_run_side_effect):
            # Execute translation phase
            runner.run_phase("translation", ["my-translate", "--format", "compact", "test.param"])
            
            # Execute solving phase
            runner.run_phase("solving", ["savilerow", "model.eprime", "test.param"])
            
            # Execute visualisation phase
            runner.run_phase("visualisation", ["vis.py", "test.param", "solution.txt"])
        
        # Verify all phases were recorded
        assert len(runner.summary.phases) == 3
        
        translation = runner.get_phase_result("translation")
        solving = runner.get_phase_result("solving")
        visualisation = runner.get_phase_result("visualisation")
        
        assert translation.name == "translation"
        assert translation.duration == 0.1
        assert solving.name == "solving"
        assert solving.duration == 2.5
        assert visualisation.name == "visualisation"
        assert visualisation.duration == 0.2
        
        # Verify summary aggregation (use pytest.approx for floating point)
        assert runner.summary.total_duration == pytest.approx(2.8)  # 0.1 + 2.5 + 0.2
        assert runner.summary.solution_found is True  # From solving phase
    
    def test_get_summary(self):
        """Test getting the current summary."""
        runner = SolverRunner(instance="test", model="compact")
        
        summary = runner.get_summary()
        assert isinstance(summary, RunSummary)
        assert summary.instance == "test"
        assert summary.model == "compact"
    
    def test_get_phase_result(self):
        """Test getting specific phase results."""
        runner = SolverRunner()
        
        # No phases initially
        assert runner.get_phase_result("solving") is None
        
        # Add a mock phase
        mock_result = SolverResult(["echo", "test"], 0, "test", "", 0.1)
        with patch.object(runner, 'run', return_value=mock_result):
            runner.run_phase("solving", ["echo", "test"])
        
        # Should find the phase now
        phase = runner.get_phase_result("solving")
        assert phase is not None
        assert phase.name == "solving"
        
        # Non-existent phase
        assert runner.get_phase_result("nonexistent") is None
    
    def test_summary_status_methods(self):
        """Test summary status checking methods."""
        runner = SolverRunner()
        
        # Initially no phases (success defaults to False)
        assert runner.get_total_duration() == 0.0
        assert runner.was_successful() is False  # No phases = False (default value)
        assert runner.found_solution() is False
        
        # Add successful phase with solution
        mock_result = SolverResult(
            ["savilerow"], 0, "Created solution file test-01.param.solution", "", 1.5
        )
        with patch.object(runner, 'run', return_value=mock_result):
            runner.run_phase("solving", ["savilerow", "model.eprime"])
        
        assert runner.get_total_duration() == 1.5
        assert runner.was_successful() is True
        assert runner.found_solution() is True
    
    def test_finalize_summary_success(self):
        """Test successful summary finalization and file save."""
        with tempfile.TemporaryDirectory() as tmpdir:
            summary_file = Path(tmpdir) / "test.summary"
            runner = SolverRunner(summary_file=summary_file, instance="test")
            
            # Add a successful phase
            mock_result = SolverResult(["echo"], 0, "test", "", 0.1)
            with patch.object(runner, 'run', return_value=mock_result):
                runner.run_phase("solving", ["echo", "test"])
            
            # Finalize summary
            runner.finalize_summary("completed")
            
            # Verify summary was finalized
            assert runner.summary.final_status == "completed"
            assert runner.summary.success is True
            assert runner.summary.end_time is not None
            
            # Verify file was saved
            assert summary_file.exists()
            
            # Verify file contents
            summary_data = json.loads(summary_file.read_text())
            assert summary_data['instance'] == "test"
            assert summary_data['final_status'] == "completed"
            assert summary_data['success'] is True
            assert len(summary_data['phases']) == 1
    
    def test_finalize_summary_no_file(self):
        """Test summary finalization without file save."""
        runner = SolverRunner(summary_file=None)
        
        # Should not raise exception
        runner.finalize_summary("completed")
        
        assert runner.summary.final_status == "completed"
    
    def test_finalize_summary_save_error(self):
        """Test handling of file save errors during finalization."""
        # Use invalid path that will cause save to fail
        runner = SolverRunner(summary_file=Path("/invalid/path/test.summary"))
        
        # Mock the logger to capture error messages
        with patch.object(runner, 'logger') as mock_logger:
            # Should not raise exception
            runner.finalize_summary("completed")
            
            # Should log error
            mock_logger.error.assert_called()
            error_call = mock_logger.error.call_args[0][0]
            assert "Failed to save summary" in error_call
    
    def test_run_simple_compatibility(self):
        """Test that run_simple maintains compatibility."""
        runner = SolverRunner()
        
        with patch.object(runner, 'run') as mock_run:
            mock_run.return_value = SolverResult(["echo"], 1, "", "error", 0.1)  # Failed result
            
            # Should delegate to run method and return failure status
            result = runner.run_simple("echo", "test")
            
            assert result is True  # run_simple returns True for failure
            mock_run.assert_called_once_with(["echo", "test"])
            
            # Should not create phase tracking
            assert len(runner.summary.phases) == 0
    
    def test_savilerow_statistics_extraction(self):
        """Test statistics extraction for Savile Row output."""
        savilerow_output = """
WARNING: interval 7..6 is out of order. Rewriting to empty interval.
Created output SAT file test-01.param.dimacs
No solution found.
        0.14 real         0.08 user         0.03 sys
No solution found
Created output SAT file test-07.param.dimacs
Created solution file test-07.param.solution
        2.56 real         2.49 user         0.05 sys
"""
        
        mock_result = SolverResult(
            ["savilerow-native", "model.eprime"],
            0, savilerow_output, "", 2.56
        )
        
        runner = SolverRunner()
        
        with patch.object(runner, 'run', return_value=mock_result):
            runner.run_phase("solving", ["savilerow-native", "model.eprime", "problem.param"])
        
        phase = runner.summary.phases[0]
        stats = phase.solver_stats
        
        # horizon_number not automatically set in this test - would need explicit horizon parameter
        assert stats['final_horizon'] == 7
        assert stats['solution_found'] is True
        assert stats['real_time'] == 2.56
        assert stats['used_sat_solver'] is True
        assert stats['interval_warnings'] == 1
    
    def test_fast_downward_statistics_extraction(self):
        """Test statistics extraction for Fast Downward output."""
        fd_output = """
INFO     Running search (release).
[t=0.027425s, 34136572 KB] Solution found!
[t=0.027425s, 34136572 KB] Plan length: 20 step(s).
[t=0.027425s, 34136572 KB] Plan cost: 4
[t=0.027425s, 34136572 KB] Expanded 713 state(s).
[t=0.027425s, 34136572 KB] Evaluated 931 state(s).
[t=0.027425s, 34136572 KB] Generated 1265 state(s).
[t=0.027425s, 34136572 KB] Search time: 0.002670s
[t=0.027425s, 34136572 KB] Total time: 0.027425s
Peak memory: 34136572 KB
        0.44 real         0.33 user         0.06 sys
"""
        
        mock_result = SolverResult(
            ["fd", "domain.pddl", "problem.pddl"],
            0, fd_output, "", 0.44
        )
        
        runner = SolverRunner()
        
        with patch.object(runner, 'run', return_value=mock_result):
            runner.run_phase("solving", ["fd", "domain.pddl", "problem.pddl"])
        
        phase = runner.summary.phases[0]
        stats = phase.solver_stats
        
        assert stats['expanded_states'] == 713
        assert stats['evaluated_states'] == 931
        assert stats['generated_states'] == 1265
        assert stats['plan_length'] == 20
        assert stats['plan_cost'] == 4
        assert stats['peak_memory_kb'] == 34136572
        assert stats['search_time'] == 0.002670
        assert stats['total_time'] == 0.027425
        assert stats['solution_found'] is True
        assert stats['real_time'] == 0.44
    
    def test_memory_extraction_to_phase(self):
        """Test that peak memory from stats is copied to phase."""
        mock_result = SolverResult(
            ["fd", "domain.pddl"],
            0, "Peak memory: 1234567 KB", "", 1.0
        )
        
        runner = SolverRunner()
        
        with patch.object(runner, 'run', return_value=mock_result):
            runner.run_phase("solving", ["fd", "domain.pddl"])
        
        phase = runner.summary.phases[0]
        assert phase.peak_memory_kb == 1234567
        assert phase.solver_stats['peak_memory_kb'] == 1234567


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
