"""
UPF solver implementation - the single class behind every UPF-routed model.

UpfPlanSolver drives the consumer's UPF planner CLI through run_phase, so it
inherits the timeout/signal handling and SIGTERM summary generation every solver
gets. The CLI name is not baked in: the consumer supplies it (``upfplan_command=``
or by overriding the ``UPFPLAN_COMMAND`` class attribute) - puzznic passes
``puzznic-upfplan``. It reaches a UPF engine by the two routes that CLI supports,
selected here by ``build_mode``:

  - build_mode is None (PDDL models, e.g. pddlnoaxiom): pass ``--domain`` so the
    CLI reads the PDDL through unified_planning's PDDLReader and solves with the
    engine chosen by the effective ``--tune`` (enhsp-opt / fast-downward / symk;
    see ModelRegistry.get_upf_engine). The same class therefore backs the upfenhsp,
    upffd and upfsymk solvers. The CLI streams that engine's progress live,
    so run_phase tees it to the terminal the way fd/symk do.

  - build_mode is 'upf'/'upflocal' (constructed models): pass ``--build`` so the
    CLI builds the Problem in Python and solves it with the tune's engine (Fast
    Downward blind by default; upfenhsp defaults to the metric-optimal enhsp-opt).
    This replaces the former UPFSolver/UPFLocalSolver classes; that path buffers the
    engine output (it surfaces only once solving finishes) and emits a scored plan.

Optimality is a ``--tune`` dial, not a solver name: every upf* solver optimises the
score by default (upffd/upfsymk are cost-optimal by construction, upfenhsp defaults
to enhsp-opt), and satisficing ENHSP is opt-in via ``--tune satisficing``.
"""

from pathlib import Path
from typing import Optional

from .solver_base import Solver
from .global_state import global_state


class UpfPlanSolver(Solver):
    """Solver that runs a model through a UPF engine (PDDL or constructed)."""

    # CLI entry point that does the UPF PDDLReader/construction + OneshotPlanner
    # solve. No generic default (the CLI is consumer-provided); a consumer sets it
    # per instance via ``upfplan_command=`` or by overriding this class attribute.
    UPFPLAN_COMMAND: Optional[str] = None

    def __init__(self, solver_runner, solver_name: str = "upfenhsp",
                 build_mode: Optional[str] = None,
                 upfplan_command: Optional[str] = None):
        super().__init__(solver_runner)
        self.solver_name = solver_name
        # None -> read PDDL via --domain; 'upf'/'upflocal' -> construct via --build.
        self.build_mode = build_mode
        # The consumer's UPF planner CLI (puzznic supplies 'puzznic-upfplan'); an
        # explicit argument wins, else the class-attribute override, else unset.
        self.upfplan_command = upfplan_command or self.UPFPLAN_COMMAND

    def _tune_fd_search(self, registry, tune, engine_name) -> Optional[str]:
        """FD search config for the active tune - only for fast-downward engines
        (enhsp/symk ignore it, and get_upf_fd_search returns None for them anyway)."""
        if not (engine_name and engine_name.startswith('fast-downward')):
            return None
        try:
            return registry.get_upf_fd_search(self.solver_name, tune)
        except Exception:
            return None

    def solve(self, param_file: Path, **kwargs) -> Optional[Path]:
        """
        Solve the problem in ``param_file`` through a UPF engine.

        Args:
            param_file: generated PDDL problem (PDDL models) or .upf instance
                (constructed upf/upflocal models)
            **kwargs: model_path (PDDL domain) - required unless build_mode is set

        Returns:
            Path to the solution file if a plan was found, else None.
        """
        # Effective --tune: an explicit --tune wins, else the model's pinned default.
        # It selects the UPF engine (the optimality mode) the way it selects
        # savilerow's sat_args or the direct enhsp -planner (see get_upf_engine).
        # upfenhsp defaults to the metric-optimal engine so a plain run maximises the
        # score, matching the cost-optimal upffd/upfsymk; --tune satisficing opts out.
        if not self.upfplan_command:
            raise ValueError(
                "no UPF planner command configured; pass upfplan_command= to "
                "UpfPlanSolver or set its UPFPLAN_COMMAND to the consumer's UPF CLI")

        registry = global_state.model_registry
        tune = self._effective_tune()

        if self.build_mode:
            # Constructed models build the Problem in Python (no PDDL domain). The
            # engine comes from the effective tune, so the axiom-free builds
            # (upflocalnoaxiom) can be solved by an axiom-rejecting engine like ENHSP,
            # not just Fast Downward.
            try:
                engine_name = registry.get_upf_engine(self.solver_name, tune)
            except Exception:
                engine_name = None
            engine_name = engine_name or 'fast-downward'
            cmd = [self.upfplan_command, "--build", self.build_mode,
                   "-s", engine_name, str(param_file)]
            # For the fast-downward engine the tune also picks the FD search config
            # (upffd: optimal blind vs satisficing greedy); forward it so --build
            # honours --tune instead of the search compile_and_solve would pin.
            fd_search = self._tune_fd_search(registry, tune, engine_name)
            if fd_search:
                cmd += ["--fd-search", fd_search]
            self.logger.info(f"Building UPF model '{self.build_mode}' (engine: {engine_name})")
        else:
            model_path = kwargs.get('model_path')
            if not model_path:
                raise ValueError(
                    "model_path (PDDL domain) is required for the upfplan solver")

            # UPF engine name comes from the effective tune (upfenhsp: optimal ->
            # enhsp-opt by default, satisficing -> enhsp, anytime -> enhsp-any),
            # defaulting to the bare 'enhsp' engine if the solver defines none.
            try:
                engine_name = registry.get_upf_engine(self.solver_name, tune)
            except Exception:
                engine_name = None
            engine_name = engine_name or 'enhsp'

            cmd = [
                self.upfplan_command,
                "--domain", str(model_path),
                "--engine", engine_name,
            ]
            # Optional fast-downward search config from the active tune (upffd: optimal
            # blind vs satisficing greedy), so the upffd config both measures pure UPF
            # overhead against the direct fd path (blind) and exposes the search dial.
            fd_search = self._tune_fd_search(registry, tune, engine_name)
            if fd_search:
                cmd += ["--fd-search", fd_search]
            cmd.append(str(param_file))

            self.logger.info(f"Running UPF engine '{engine_name}'")

        # Forward debug so the upfplan subprocess's diagnostics (the compilation
        # trace, the dumped compiled problem) print on its stderr under the
        # consumer's debug flag (e.g. `puzznic-run -d`) - where run_phase
        # captures/tees them, and clear of the plan-only stdout the .solution is
        # built from.
        if global_state.debug:
            cmd.append("--debug")

        result = self.runner.run_phase("solving", cmd, expected_outputs=[])

        if result.interrupted:
            self.logger.warning("upfplan solver was interrupted")
            return None

        # Timeout with no plan is a timeout (mirrors UPF/pddl handling).
        self._handle_timeout_result(result, "upfplan solver")

        if result.success and result.stdout and result.stdout.strip():
            solution_file = self._generate_solution_file_path()
            solution_file.write_text(result.stdout)
            if self.runner.summary.phases:
                self.runner.summary.phases[-1].output_files = [str(solution_file)]
            self.logger.info(f"upfplan solution written to {solution_file}")
            return solution_file

        if result.success:
            self.logger.warning("upfplan solver produced no plan")
            return None

        self.logger.warning(f"upfplan solver failed: {result.stderr}")
        return None
