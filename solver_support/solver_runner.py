#!/usr/bin/env python
"""
Robust subprocess runner for solver execution with proper error handling,
signal management, logging capabilities, and structured summary generation.
"""

import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, List, Dict
import psutil
import os
import re

from .run_summary import RunSummary, PhaseResult, create_run_summary
from .file_management import SolutionFileNamer
from .solver_stats import SolverStatsExtractor, detect_solver_from_command
from .utilities import setup_logger, relativize_path
from .global_state import global_state


class SolverExecutableError(RuntimeError):
    """Raised when a solver executable cannot be launched (missing / not
    executable). Signals the horizon scan to abort: the binary will not appear
    on a later horizon, so retrying every horizon to the cap is pointless."""


class SolverResult:
    """Encapsulates the result of a solver execution."""
    
    def __init__(self,
                 command: List[str],
                 returncode: int,
                 stdout: str,
                 stderr: str,
                 duration: float,
                 timed_out: bool = False,
                 interrupted: bool = False,
                 best_plan_number: int = 0,
                 saved_solutions: dict = None,
                 launch_failed: bool = False):
        self.command = command
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.duration = duration
        self.timed_out = timed_out
        self.interrupted = interrupted
        # True when the executable itself could not be started (missing binary /
        # not executable). Distinct from interrupted: a launch failure is an
        # infrastructure error that will not resolve on a later horizon, so the
        # horizon scan should abort rather than retry. See docs/server-harness.md.
        self.launch_failed = launch_failed
        self.best_plan_number = best_plan_number  # For anytime portfolio interrupt handling
        self.saved_solutions = saved_solutions or {}  # Map plan_num -> saved_file_path
    
    @property
    def success(self) -> bool:
        """True if the command completed successfully."""
        return self.returncode == 0 and not self.timed_out and not self.interrupted
    
    @property
    def failed(self) -> bool:
        """True if the command failed (inverse of success)."""
        return not self.success
    
    def __str__(self) -> str:
        status = "SUCCESS" if self.success else "FAILED"
        if self.timed_out:
            status = "TIMEOUT"
        elif self.interrupted:
            status = "INTERRUPTED"
        
        return f"SolverResult({' '.join(self.command)}) -> {status} (rc={self.returncode}, t={self.duration:.2f}s)"


class SolverRunner:
    """
    Robust subprocess runner with timeout, signal handling, comprehensive logging,
    and structured summary generation.
    
    Designed to replace the basic doit() function in the run script with better
    error handling, process management, logging capabilities, and optional
    phase-based execution tracking with automatic statistics extraction.
    """
    
    def __init__(self,
                 log_file: Optional[Path] = None,
                 timeout: Optional[int] = None,
                 summary_file: Optional[Path] = None,
                 instance: str = "",
                 model: str = "",
                 solver: str = "",
                 timestamp: str = None,
                 output_dir: Optional[Path] = None,
                 log_level: Optional[int] = None):
        self.log_file = log_file
        self.timeout = timeout
        # Console verbosity for this runner's logger. ``global_state`` can only
        # ever *raise* verbosity (its log_level() floors at WARNING), so a
        # consumer that wants a quieter runner - e.g. a benchmark harness where
        # a timed-out row is an ordinary result - passes an explicit level here.
        # None keeps the global behaviour. The file handler always captures
        # everything regardless.
        self.log_level = log_level
        self.logger = self._setup_logger()
        self.summary_file = summary_file
        # Output directory should already exist and we should already be in it
        self.output_dir = output_dir or Path.cwd()
        
        # Track whether we created the output directory for cleanup purposes
        self.created_output_dir = False  # the higher-level runner handles directory creation
        
        # Create summary with directory information
        self.summary = create_run_summary(
            instance, model, solver, timestamp,
            output_dir=str(self.output_dir)
        )
        self._current_phase: Optional[PhaseResult] = None
    
    def _setup_logger(self) -> logging.Logger:
        """Set up logging to both console and file if specified."""
        level = self.log_level if self.log_level is not None else global_state.log_level()
        return setup_logger(f'solver_runner_{id(self)}', log_file=self.log_file, log_level=level)
    
    def cleanup_output_dir(self) -> None:
        """Clean up output directory if it's empty and we created it."""
        try:
            if self.created_output_dir and self.output_dir.exists():
                # Check if directory is empty (ignoring hidden files)
                files = list(self.output_dir.iterdir())
                if not files:
                    self.output_dir.rmdir()
                    self.logger.debug(f"Removed empty output directory: {self.output_dir}")
        except Exception as e:
            self.logger.warning(f"Failed to clean up output directory: {e}")
    
    def _terminate_process_tree(self, process: subprocess.Popen) -> None:
        """Terminate a process and all its children."""
        try:
            parent_pid = process.pid
            parent = psutil.Process(parent_pid)
            children = parent.children(recursive=True)
            
            # Terminate children first
            for child in children:
                try:
                    self.logger.debug(f"Terminating child process {child.pid}")
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            
            # Terminate parent
            try:
                self.logger.debug(f"Terminating parent process {parent_pid}")
                parent.terminate()
            except psutil.NoSuchProcess:
                pass
            
            # Wait for graceful termination
            gone, still_alive = psutil.wait_procs(children + [parent], timeout=3)
            
            # Kill any remaining processes
            for proc in still_alive:
                try:
                    self.logger.debug(f"Force killing process {proc.pid}")
                    proc.kill()
                except psutil.NoSuchProcess:
                    pass
                    
        except psutil.NoSuchProcess:
            # Process already terminated
            pass
        except Exception as e:
            self.logger.error(f"Error terminating process tree: {e}")
    
    def run(self,
            command: List[str],
            timeout: Optional[int] = None,
            env: Optional[Dict[str, str]] = None,
            phase_name: Optional[str] = None,
            stream_output: bool = True,
            cwd: Optional[Path] = None) -> SolverResult:
        """
        Run a command with robust error handling and logging.

        Args:
            command: Command and arguments to run
            timeout: Timeout in seconds (uses instance timeout if None)
            env: Environment variables for the command
            phase_name: "translation", "solving", "visualisation"
            cwd: Working directory for the subprocess. None (the default) keeps
                the caller's own working directory, which is what the solver
                classes rely on - they are documented to run after a chdir and
                to use paths relative to it. A consumer that would rather not
                chdir a whole process can instead point each subprocess here.

        Returns:
            SolverResult containing execution details
        """
        if timeout is None:
            timeout = self.timeout
        
        # Prepare the command
        full_command = command.copy()
        
        self.logger.info(f"Executing: {' '.join(full_command)}")
        if timeout:
            self.logger.info(f"Timeout: {timeout}s")
        
        start_time = time.time()
        process = None
        timed_out = False
        interrupted = False
        best_plan_number = 0
        saved_solutions = {}
        
        try:
            # Set up environment
            run_env = os.environ.copy()
            if env:
                run_env.update(env)
            
            # Start the process
            process = subprocess.Popen(
                full_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(cwd) if cwd is not None else None,
                env=run_env,
                preexec_fn=os.setsid if hasattr(os, 'setsid') else None
            )
            
            if stream_output:
                # Use threading to read stdout/stderr like `tee` - both print and capture
                import threading
                
                stdout_lines = []
                stderr_lines = []
                
                # Track anytime portfolio solutions in real-time
                best_plan_number = 0
                saved_solutions = {}  # Map plan_num -> saved_file_path
                
                def read_stdout():
                    nonlocal best_plan_number, saved_solutions
                    try:
                        while True:
                            # Check for interrupt before reading
                            if global_state.should_force_termination:
                                break
                            line = process.stdout.readline()
                            if not line:  # EOF
                                break
                            
                            # process the line for solution detection first
                            stdout_lines.append(line)
                            
                            # Track portfolio plan numbers for interrupt handling
                            if "plan manager: found new plan with cost" in line:
                                # a plan was found and registered
                                best_plan_number += 1

                                # save completed plan file to results
                                current_plan_file = Path(f"sas_plan.{best_plan_number}")
                                if current_plan_file.exists() and best_plan_number not in saved_solutions and self.output_dir:
                                    try:
                                        timestamp = self.summary.timestamp
                                        instance_name = self.summary.instance
                                        # Throwaway streaming copy, distinct from the final
                                        # SolutionFileNamer path (the `.partial` infix) so
                                        # _handle_anytime_solutions can copy then unlink it.
                                        # Built through SolutionFileNamer (with the tune slot)
                                        # rather than a hardcoded "-fd-pddlsoup-" so symk soup
                                        # runs name their partials correctly and the slot stays
                                        # in sync with the final solution file.
                                        solution_file = self.output_dir / SolutionFileNamer.generate_solution_filename(
                                            instance_name, self.summary.solver,
                                            self.summary.model, timestamp,
                                            tune=self.summary.tune_slot,
                                            suffix=f".{best_plan_number}.partial.solution"
                                        )
                                        
                                        import shutil
                                        shutil.copy2(str(current_plan_file), str(solution_file))
                                        saved_solutions[best_plan_number] = solution_file
                                        
                                    except Exception as e:
                                        # Log failure but continue
                                        pass
                            
                            # Filter warnings unless in verbose mode
                            should_print = True
                            if "WARNING:" in line and not global_state.verbose:
                                should_print = False

                            # Suppress translation phase output unless in verbose mode
                            if phase_name == "translation" and not global_state.verbose:
                                should_print = False

                            # finish reading solutions even on broken pipe
                            if should_print:
                                try:
                                    # Replace full paths with relative paths in printed output only (unless verbose mode)
                                    display_line = line
                                    if not global_state.verbose and ("Created information file" in line or "Created solution file" in line or "Created output" in line):
                                        # Extract the file path from the line
                                        # Match patterns like "Created output SAT file" or "Created information file"
                                        match = re.search(r'(Created (?:\w+ )*(?:file|SAT file) )(.+)$', line)
                                        if match and self.output_dir:
                                            prefix = match.group(1)
                                            full_path = match.group(2).strip()
                                            rel_path = relativize_path(full_path, self.output_dir)
                                            display_line = prefix + rel_path + '\n'
                                    
                                    print(display_line.rstrip(), flush=True)
                                except BrokenPipeError:
                                    pass
                                    
                    except (BrokenPipeError, ValueError):
                        # silently process remaining lines in the buffer
                        pass
                
                def read_stderr():
                    try:
                        while True:
                            # Check for interrupt before reading
                            if global_state.should_force_termination:
                                break
                            line = process.stderr.readline()
                            if not line:  # EOF
                                break
                            # Always append to capture for log file
                            stderr_lines.append(line)
                            
                            # Filter warnings unless in verbose mode
                            should_print = True
                            if "WARNING:" in line and not global_state.verbose:
                                should_print = False

                            # Suppress translation phase output unless in verbose mode
                            if phase_name == "translation" and not global_state.verbose:
                                should_print = False

                            if should_print:
                                print(line.rstrip(), file=sys.stderr, flush=True)  # Print to user
                    except (BrokenPipeError, ValueError):
                        # Handle broken pipe gracefully
                        pass
                
                # Start reader threads
                stdout_thread = threading.Thread(target=read_stdout)
                stderr_thread = threading.Thread(target=read_stderr)
                stdout_thread.start()
                stderr_thread.start()
                
                try:
                    # Poll for process completion and check for interrupts
                    while process.poll() is None:
                        # Check for timeout
                        if timeout and (time.time() - start_time) > timeout:
                            self.logger.warning(f"Command timed out after {timeout}s")
                            timed_out = True
                            self._terminate_process_tree(process)
                            break
                        
                        # Check for interruption
                        if global_state.should_force_termination:
                            self.logger.warning("Terminating process due to interrupt")
                            interrupted = True
                            self._terminate_process_tree(process)
                            break
                        
                        # Sleep briefly to avoid busy waiting
                        time.sleep(0.1)
                    
                    returncode = process.returncode if process.returncode is not None else -1
                    
                    # Wait for threads to finish reading
                    stdout_thread.join(timeout=2.0)  # Give threads time to finish
                    stderr_thread.join(timeout=2.0)
                    
                    stdout = ''.join(stdout_lines)
                    stderr = ''.join(stderr_lines)
                    
                except subprocess.TimeoutExpired:
                    self.logger.warning(f"Command timed out after {timeout}s")
                    timed_out = True
                    self._terminate_process_tree(process)
                    stdout, stderr = process.communicate()
                    returncode = -1
            else:
                # Use simple communicate() for commands that don't need streaming
                try:
                    stdout, stderr = process.communicate(timeout=timeout)
                    returncode = process.returncode
                except subprocess.TimeoutExpired:
                    self.logger.warning(f"Command timed out after {timeout}s")
                    timed_out = True
                    self._terminate_process_tree(process)
                    stdout, stderr = process.communicate()
                    returncode = -1
            
        except (FileNotFoundError, PermissionError, NotADirectoryError) as e:
            # The executable itself could not be started. This is an
            # infrastructure failure (binary missing / not executable), not a
            # per-horizon UNSAT and not a user interrupt, so flag it distinctly
            # (launch_failed) rather than as interrupted: callers abort the scan.
            self.logger.error(f"Could not launch command: {e}")
            return SolverResult(
                command=command,
                returncode=-1,
                stdout="",
                stderr=str(e),
                duration=time.time() - start_time,
                launch_failed=True,
                best_plan_number=0,
                saved_solutions={}
            )
        except Exception as e:
            self.logger.error(f"Error executing command: {e}")
            if process:
                self._terminate_process_tree(process)
            return SolverResult(
                command=command,
                returncode=-1,
                stdout="",
                stderr=str(e),
                duration=time.time() - start_time,
                interrupted=True,
                best_plan_number=0,
                saved_solutions={}
            )
        
        # Check if we were interrupted
        if global_state.should_force_termination:
            interrupted = True
            if process and process.poll() is None:
                self._terminate_process_tree(process)
        
        duration = time.time() - start_time
        
        # Create result
        result = SolverResult(
            command=command,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            duration=duration,
            timed_out=timed_out,
            interrupted=interrupted,
            best_plan_number=best_plan_number if stream_output else 0,
            saved_solutions=saved_solutions if stream_output else {}
        )
        
        # Log the result
        self.logger.info(f"Command completed in {duration:.2f}s with return code {returncode}")
        
        # `failed` covers three different outcomes and only one of them is a
        # solver failure. A timeout or an interrupt is an expected result the
        # caller decides the meaning of - an anytime solver may have banked a
        # solution worth keeping, and a benchmark's timed-out baseline IS the
        # result - so neither is logged as an error here. Both are already
        # reported at WARNING where they are detected (and a launch failure
        # logs its own error before returning early), so this only records the
        # completed result.
        if result.timed_out or result.interrupted:
            self.logger.info(f"Command did not complete: {result}")
        elif result.failed:
            self.logger.error(f"Command failed: {result}")
        
        # Log stderr (always, not just on failure)
        if stderr.rstrip():
            if '\n' in stderr.rstrip():
                self.logger.info("STDERR:\n" + stderr.rstrip())
            else:
                self.logger.info(f"STDERR: {stderr.rstrip()}")
        
        # Log stdout
        if stdout.rstrip():
            if '\n' in stdout.rstrip():
                self.logger.info("STDOUT:\n" + stdout.rstrip())
            else:
                self.logger.info(f"STDOUT: {stdout.rstrip()}")
        
        # Flush log handlers for immediate output visibility and interrupt robustness
        for handler in self.logger.handlers:
            handler.flush()
        
        return result
    
    def run_simple(self, *args, **kwargs) -> bool:
        """
        Simplified interface that returns True if command failed.
        
        Note: For phase tracking, use run_phase() instead.
        """
        result = self.run(list(args), **kwargs)
        return result.failed
    
    def run_phase(self,
                  phase_name: str,
                  command: List[str],
                  expected_outputs: Optional[List[str]] = None,
                  horizon_number: Optional[int] = None,
                  **kwargs) -> SolverResult:
        """
        Execute a command as a named phase and track results in summary.
        
        Args:
            phase_name: Name of the phase ("translation", "solving", "visualisation")
            command: Command and arguments to run
            expected_outputs: List of files expected to be created by this phase
            horizon_number: The horizon number being attempted (for horizon phases)
            **kwargs: Additional arguments passed to SolverRunner.run()
                (timeout, env, stream_output, cwd)

        Returns:
            SolverResult containing execution details
        """
        if not command:
            raise ValueError("Command cannot be empty")
        
        # Create phase result object
        phase = PhaseResult(name=phase_name, command=command.copy())
        self._current_phase = phase
        
        self.logger.info(f"Starting phase: {phase_name}")
        
        try:
            # Execute the command using existing run method
            result = self.run(command, phase_name=phase_name, **kwargs)
            
            # Update phase with results. The outcome fields are recorded
            # alongside `success` so a saved summary can tell a timeout or a
            # missing binary from a solver that ran and failed.
            phase.duration = result.duration
            phase.success = result.success
            phase.returncode = result.returncode
            phase.timed_out = result.timed_out
            phase.launch_failed = result.launch_failed
            phase.error_message = result.stderr if result.failed else ""
            
            # Extract warnings from stderr
            if result.stderr:
                for line in result.stderr.split('\n'):
                    if 'WARNING:' in line and line.strip():
                        phase.warnings.append(line.strip())
            
            # Detect solver type and extract statistics
            solver_name = detect_solver_from_command(command)
            phase.solver_stats = SolverStatsExtractor.extract_stats(
                solver_name, result.stdout, result.stderr, result.duration, horizon_number
            )
            
            # Add timeout information from the actual result
            if result.timed_out:
                phase.solver_stats['timed_out'] = True
            
            # Extract memory usage if available in stats
            if 'peak_memory_kb' in phase.solver_stats:
                phase.peak_memory_kb = phase.solver_stats['peak_memory_kb']
            
            # Update solver name in summary if not set
            if not self.summary.solver and solver_name != command[0]:
                self.summary.solver = solver_name
            
            # Detect output files that were created and relativize paths
            if expected_outputs:
                phase.output_files = [
                    relativize_path(str(f), self.output_dir) for f in expected_outputs
                    if Path(f).exists()
                ]
            
            # Add phase to summary. add_phase() also updates solution_found, but
            # only for genuine solving phases (it skips translation/modelling/
            # visualisation, whose output can falsely trip solution detection).
            self.summary.add_phase(phase)

            # Update overall success status (required phases must succeed;
            # best-effort post-processing phases never decide it). finalize()
            # re-derives this authoritatively, honouring final_status.
            self.summary.update_required_success()
            
            self.logger.info(f"Phase {phase_name} completed: {result}")
            
        except Exception as e:
            # Handle errors gracefully
            phase.success = False
            phase.error_message = str(e)
            phase.duration = 0.0
            
            self.summary.add_phase(phase)
            
            # Update overall success status (required phases must succeed;
            # best-effort post-processing phases never decide it). finalize()
            # re-derives this authoritatively, honouring final_status.
            self.summary.update_required_success()
            
            self.logger.error(f"Phase {phase_name} failed: {e}")
            
            # Re-raise the exception
            raise
        
        finally:
            self._current_phase = None
        
        return result
    
    def finalize_summary(self, final_status: str = "done") -> None:
        """
        Finalize and save the run summary.
        
        Args:
            final_status: Final status message for the run
        """
        try:
            self.summary.finalize(final_status)
            
            if self.summary_file:
                self.summary.save(self.summary_file)
                self.logger.info(f"Summary saved to {self.summary_file}")
            
        except Exception as e:
            self.logger.error(f"Failed to save summary: {e}")
        
        # Force flush all log handlers to ensure all output is written
        for handler in self.logger.handlers:
            handler.flush()
    
    def get_summary(self) -> RunSummary:
        """
        Get the current run summary.
        
        Returns:
            Current RunSummary object
        """
        return self.summary
    
    def get_phase_result(self, phase_name: str) -> Optional[PhaseResult]:
        """
        Get results for a specific phase.
        
        Args:
            phase_name: Name of the phase to retrieve
            
        Returns:
            PhaseResult if found, None otherwise
        """
        return self.summary.get_phase(phase_name)
    
    def get_total_duration(self) -> float:
        """
        Get total duration of all completed phases.
        
        Returns:
            Total duration in seconds
        """
        return self.summary.total_duration
    
    def was_successful(self) -> bool:
        """
        Check if all phases completed successfully.
        
        Returns:
            True if all phases succeeded, False otherwise
        """
        return self.summary.success
    
    def found_solution(self) -> bool:
        """
        Check if a solution was found by any phase.
        
        Returns:
            True if solution was found, False otherwise
        """
        return self.summary.solution_found
    
    def __repr__(self) -> str:
        """
        String representation of SolverRunner for debugging.
        
        Returns:
            String representation containing main aspects of the runner
        """
        parts = [
            f"instance={self.summary.instance!r}",
            f"model={self.summary.model!r}",
            f"solver={self.summary.solver!r}",
            f"timeout={self.timeout}",
            f"log_file={self.log_file}",
            f"summary_file={self.summary_file}",
            f"output_dir={self.output_dir}",
            f"current_phase={self._current_phase.name if self._current_phase else None!r}",
            f"phases={len(self.summary.phases)}"
        ]
        return f"SolverRunner({', '.join(parts)})"


