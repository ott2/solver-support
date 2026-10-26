"""
Data structures for representing structured run summaries and phase results.

These classes provide a machine-readable representation of puzzle solver runs,
including timing information, success status, and solver-specific statistics.
"""

from dataclasses import dataclass, field, asdict, fields
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple, Type
import json
from pathlib import Path


# --- consumer-owned fields the core does not interpret ---
#
# The core owns the *shape* of a run record, but a consumer often has a datum
# that belongs on the run and that the core has no concept of - a domain
# cross-check, a provenance tag. Such a field must not be declared on the core's
# dataclasses, or the "domain-agnostic" record grows a consumer's vocabulary (as
# a `canonical_score`, naming one consumer's replay scorer, once did). It goes
# in ``extra`` instead.
#
# ``extra`` is *flattened* on the way out and re-gathered on the way in, so the
# JSON is exactly as it would have been with a declared field. That keeps every
# reader that indexes the saved summary by key working unchanged, and is what
# makes moving a field into ``extra`` a no-op on disk. The gathering half also
# means a summary written by a *different* consumer loads here instead of
# raising TypeError on its unknown keys - which matters now more than one
# consumer writes these files.


def _flatten_extra(data: Dict[str, Any]) -> Dict[str, Any]:
    """Fold ``extra`` up into the top level of a serialised record.

    A core field always wins a name collision (``setdefault``), so a consumer
    cannot shadow the record's own vocabulary - and a field later *removed* from
    the core is transparently served from ``extra`` instead.
    """
    extra = data.pop('extra', None) or {}
    for key, value in extra.items():
        data.setdefault(key, value)
    return data


def _split_known(cls: Type, data: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Partition ``data`` into (keys declared on ``cls``, everything else)."""
    known = {f.name for f in fields(cls)}
    declared = {k: v for k, v in data.items() if k in known}
    extra = {k: v for k, v in data.items() if k not in known}
    return declared, extra


@dataclass
class PhaseResult:
    """Individual phase (translation/solving/visualisation) results."""
    
    name: str                                # Phase name: "translation", "solving", "visualisation"
    command: List[str] = field(default_factory=list)  # Command that was run
    duration: float = 0.0                   # Execution time in seconds
    success: bool = False                   # Whether phase completed successfully
    output_files: List[str] = field(default_factory=list)  # Files created by this phase
    error_message: str = ""                 # Error details if failed
    peak_memory_kb: Optional[int] = None    # Peak memory usage in KB
    solver_stats: Dict[str, Any] = field(default_factory=dict)  # Solver-specific statistics
    warnings: List[str] = field(default_factory=list)  # Warning messages from stderr
    # Why the phase ended, kept alongside the bare `success` flag so the outcome
    # can be reconstructed from a saved summary. `success` conflates the three
    # failure kinds SolverResult distinguishes, and the distinction matters to a
    # reader of the JSON: a timed-out baseline is a result, whereas a launch
    # failure (missing / non-executable binary) is an infrastructure error that
    # aborts a horizon scan. None/False means "not recorded" - phases built by
    # hand, or by the error path of run_phase, leave them at their defaults.
    returncode: Optional[int] = None        # Process return code (None if never launched)
    timed_out: bool = False                 # Killed for exceeding the timeout
    launch_failed: bool = False             # Executable could not be started
    # Consumer-owned per-phase fields the core does not interpret. Most phase
    # extras belong in `solver_stats` (that is what it is for); this is for a
    # datum that is about the phase itself rather than about the solver's run.
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization (``extra`` flattened in)."""
        return _flatten_extra(asdict(self))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PhaseResult':
        """Build from a serialised phase, gathering unknown keys into ``extra``."""
        declared, extra = _split_known(cls, data)
        phase = cls(**declared)
        phase.extra.update(extra)
        return phase


# Phases that translate / model / visualise never "find a solution". Their
# stdout can still trip the generic solution-indicator parse in solver_stats
# (e.g. the word "optimal" in a prob2any .param comment), which would otherwise
# latch a spurious solution_found=True onto the run. Only solving phases may set
# it. (This is a known summary-generation imperfection.)
_NON_SOLVING_PHASES = frozenset({"translation", "modelling", "visualisation"})

# Phases that prepare the input for a solver: a consumer's own translation of
# the instance, Conjure's modelling, and Conjure's parameter translation (per
# horizon, or once for a model with no horizon). When one of these fails, the solver never saw the input, so
# the run ended on an error and not on a search. Classified by name, as the
# phases above are, so the rule also applies when a saved summary is
# re-finalized.
_SETUP_PHASES = frozenset({"translation", "modelling", "translate-param"})
_SETUP_PHASE_PREFIXES = ("translate-param-",)


def _is_setup_phase(name: str) -> bool:
    return name in _SETUP_PHASES or name.startswith(_SETUP_PHASE_PREFIXES)


def _is_solving_phase(name: str) -> bool:
    """May this phase report a solution? (See _NON_SOLVING_PHASES.)"""
    return name not in _NON_SOLVING_PHASES and not _is_setup_phase(name)

# Best-effort post-processing phases. The runner runs these after a solution is
# already in hand and explicitly treats their failure as non-fatal, so their
# success must NOT decide the overall run success. (Without this, a broken
# visualiser or .sol export would silently flip a solved run to success=False.)
_OPTIONAL_PHASES = frozenset({"visualisation", "export"})

# --- what a run concluded, and why it stopped ---
#
# `final_status` alone cannot answer "did this run learn anything?", because it
# conflates two independent questions. 'timeout' covers both a run that found a
# solution before the budget ran out and one that found nothing; 'failed' covers
# both a planner proving no plan exists and a crash. So the two questions are
# recorded separately:
#
#   outcome      what was learned:  solved / unsat / unknown / error
#   stop_reason  why it ended:      complete / time / horizons / interrupt / error
#
# `final_status` keeps its current meaning and is untouched - it is the
# orchestrator's own label, and consumers key on it.
OUTCOMES = ('solved', 'unsat', 'unknown', 'error')
STOP_REASONS = ('complete', 'time', 'horizons', 'interrupt', 'error')

# A run succeeded if it produced a DEFINITIVE ANSWER in the budget it was given
# - a solution, or a sound proof that none exists. Both answer the question the
# run was asked; 'unknown' and 'error' do not. Note this is decided on the
# outcome and not on exit codes: a planner proving unsatisfiability exits
# non-zero and its phase records success=False, yet the run succeeded.
_SUCCESS_OUTCOMES = frozenset({'solved', 'unsat'})

# final_status values where the search RAN TO COMPLETION, so "no solution" can
# be a verdict rather than an interruption. On any other status the search
# stopped early and the outcome can only be 'unknown', however complete the
# model - which is what stops 'hlim' being read as a proof. ('failed' is here
# because that is the status a planner's proven-unsat arrives under: it exits
# non-zero, having answered the question. It is also the status of a crash, which
# is why a solving phase's `verdict`, where there is one, decides between them.)
_COMPLETED_STATUSES = frozenset({"done", "success", "failed"})

# A completed run with no solution, read through its solving phase's verdict
# (see SolverStatsExtractor.fd_verdict). `unsat` is handled separately: it needs
# the model's proves_unsat as well.
_OUTCOME_BY_VERDICT = {
    'unknown': 'unknown',   # stopped at a limit, or a search that cannot refute
    'killed': 'unknown',
    'error': 'error',
    'sat': 'error',         # claimed a plan, yet none reached the summary
}

# final_status -> why the run stopped. Statuses absent here leave stop_reason
# empty rather than guessing: an unrecognised label is a consumer's own, and
# inventing a reason for it would put words in its mouth.
_STOP_REASONS_BY_STATUS = {
    "done": "complete",
    "success": "complete",
    "failed": "complete",
    "launch-failed": "error",
    "timeout": "time",
    "hlim": "horizons",
    "stopped": "interrupt",
}


@dataclass
class RunSummary:
    """Complete puzzle run summary with structured timing data."""
    
    instance: str = ""                      # Problem instance name
    model: str = ""                         # Model type used
    solver: str = ""                        # Solver used
    tune_slot: str = ""                     # Filename config slot (defopt / tune / tune+shorthash)
    options: str = ""                       # Readable config label (defopt / tune / tune+O2), for grouping/plots
    timestamp: str = ""                     # Execution timestamp
    command_line: str = ""                  # Original command line used
    output_dir: str = ""                    # Output directory for results
    start_time: Optional[datetime] = None   # Run start time
    end_time: Optional[datetime] = None     # Run completion time
    total_duration: float = 0.0             # Total execution time in seconds
    success: bool = False                   # Overall success
    solution_found: bool = False            # Whether solution was found
    final_status: str = ""                  # Final status message
    # What the run concluded and why it stopped - the two questions final_status
    # conflates (see OUTCOMES / STOP_REASONS above). Derived by finalize();
    # empty on a summary that was never finalized, and on one loaded from a file
    # written before these existed.
    outcome: str = ""                       # solved / unsat / unknown / error
    stop_reason: str = ""                   # complete / time / horizons / interrupt / error
    # Why the outcome is what it is, where the summary can tell: `setup` (a
    # phase preparing the solver's input failed), `launch` (a binary could not
    # be started), `no-verdict`, or the solving phase's own `verdict_reason`
    # (`exhausted`, `memory`, `crash`, ...). Empty when there is nothing to add.
    outcome_reason: str = ""
    # Whether this run's model AND search can prove unsatisfiability (the
    # registry's get_proves_unsat), as last given to finalize(). Kept so that a
    # later re-finalize - after appending a phase, say - cannot drop it.
    proves_unsat: bool = False
    score: Optional[int] = None             # Total score the model reported (scored/upf models only)
    startat: Optional[int] = None           # Starting horizon for Savile Row models (if specified)
    stopat: Optional[int] = None            # Highest horizon tried, if the default cap (100) was overridden
    objbound: Optional[int] = None          # Objective bound constraint for optimization models (if specified)
    phases: List[PhaseResult] = field(default_factory=list)  # Individual phase results
    # Consumer-owned fields the core does not interpret, flattened into the saved
    # JSON so they are indistinguishable on disk from a declared field. This is
    # where a domain datum belongs - e.g. a `canonical_score`, the score of an
    # independent replay of the solution, which cross-checks the solver-reported
    # `score` and which the core has no way to compute or interpret.
    extra: Dict[str, Any] = field(default_factory=dict)
    
    def add_phase(self, phase: PhaseResult) -> None:
        """Add a phase result and update summary statistics."""
        self.phases.append(phase)
        self.total_duration += phase.duration

        # Update solution found status (solving phases only: see _NON_SOLVING_PHASES)
        if (_is_solving_phase(phase.name)
                and phase.solver_stats.get('solution_found', False)):
            self.solution_found = True

    @property
    def required_phases(self) -> List[PhaseResult]:
        """Phases that decide overall success (excludes best-effort ones).

        Best-effort post-processing (visualisation/.sol export) is non-fatal
        and must not fail an otherwise-solved run, so it is filtered out here.
        """
        return [p for p in self.phases if p.name not in _OPTIONAL_PHASES]

    def update_required_success(self) -> None:
        """Set success from the required phases as a running interim value.

        Applied by run_phase after each phase; finalize() later re-derives
        success authoritatively, honouring final_status and timeout solutions.
        Note all() over no required phases is True (matched by finalize's
        explicit bool(required) guard for the authoritative pass).
        """
        self.success = all(p.success for p in self.required_phases)

    def _derive_outcome(self, final_status: str, proves_unsat: bool,
                        has_timeout_solution: bool) -> Tuple[str, str]:
        """What this run learned, and why: (outcome, outcome_reason)."""
        # A solution in hand wins over any status label: a run that solved the
        # instance and was then interrupted, or that banked an anytime solution
        # before its budget expired, still solved it.
        if self.solution_found or has_timeout_solution:
            return 'solved', ''

        if final_status == "launch-failed":
            return 'error', 'launch'

        if final_status in _COMPLETED_STATUSES:
            # A phase preparing the input failed, so no search ran and there is
            # nothing for proves_unsat or a verdict to say. Also when it fails
            # mid-scan (Conjure's translate-param-5 after horizons 1-4): the scan
            # ended on a failure, not on a limit. A phase killed by the timeout
            # is a limit, and is left to the rules below.
            failed = self._failed_setup_phase()
            if failed is not None:
                return 'error', 'launch' if failed.launch_failed else 'setup'

            stats = self._solving_stats()
            verdict = stats.get('verdict')
            if verdict is None:
                # No account of how the search ended, so the status is all
                # there is, and it cannot prove anything: `failed` is equally
                # what a proof, a rejected input and a crash look like. From a
                # model that could prove, it may have been a proof, so the
                # answer is unknown. From any other model, finishing unsolved
                # means something went wrong. To conclude unsat, the solving
                # phase must record a verdict.
                return ('unknown' if proves_unsat else 'error'), 'no-verdict'
            # The solver said how its search ended. Only a refutation from a
            # search that can prove one is a proof; an exhausted search that
            # cannot (an unsafe heuristic, a tune switched off) concluded
            # nothing, but nothing went wrong either.
            reason = stats.get('verdict_reason') or ''
            if verdict == 'unsat':
                return ('unsat' if proves_unsat else 'unknown'), reason
            return _OUTCOME_BY_VERDICT.get(verdict, 'error'), reason

        # 'timeout', 'hlim', 'stopped', or a label this core does not know: the
        # search stopped early. Nothing was decided, and no completeness of the
        # model can change that -- refuting horizons 1..N says nothing about
        # N+1, which is exactly why 'hlim' must never become 'unsat'.
        return 'unknown', ''

    def _failed_setup_phase(self) -> Optional[PhaseResult]:
        """The first setup phase (see _SETUP_PHASES) that failed other than by
        the timeout, if any."""
        for phase in self.phases:
            if (_is_setup_phase(phase.name) and not phase.success
                    and not phase.timed_out):
                return phase
        return None

    def _solving_stats(self) -> Dict[str, Any]:
        """The ``solving`` phase's stats, where its ``verdict`` would be.

        Only the single-phase solvers' ``solving`` phase: a horizon scan's
        per-horizon verdicts are about one bound each, and never decide the run.
        """
        phase = self.get_phase("solving")
        return phase.solver_stats if phase is not None else {}

    def finalize(self, final_status: str = "done",
                 proves_unsat: Optional[bool] = None) -> None:
        """Finalize the summary: end time, outcome, stop reason, success.

        ``proves_unsat`` says whether this run's model, under the search it
        ran, is a complete decision procedure, i.e. whether its "no solution" is
        a sound proof. The core cannot know that from the output - a bounded CP
        model and a planner print the same thing - so the caller supplies it,
        normally from the registry's ``get_proves_unsat``. Left False, a
        no-solution run is never concluded unsat, which is the safe direction:
        understating what was proved. None keeps the value from the previous
        finalize, so re-finalizing to fold in a late phase cannot drop it.
        """
        self.end_time = datetime.now()
        self.final_status = final_status
        if proves_unsat is not None:
            self.proves_unsat = proves_unsat
        proves_unsat = self.proves_unsat

        # Check if we have a successful timeout-based solution phase: an anytime
        # run that found a solution before the budget ran out still succeeded.
        has_timeout_solution = any(
            phase.name == "final-timeout-solution" and phase.success
            and 'timeout_solution' in phase.solver_stats
            and phase.solver_stats['timeout_solution']
            for phase in self.phases
        )

        self.outcome, self.outcome_reason = self._derive_outcome(
            final_status, proves_unsat, has_timeout_solution)
        self.stop_reason = _STOP_REASONS_BY_STATUS.get(final_status, "")
        # A completed status that ended in error did not complete anything: a
        # crash, or a phase that never handed the solver its input.
        if self.stop_reason == "complete" and self.outcome == 'error':
            self.stop_reason = "error"
        # Decided on the answer, not on exit codes. The previous rule -- "no
        # declared failure status AND every required phase exited 0" -- called a
        # run successful whenever its subprocesses were tidy, so a horizon scan
        # that ran out of time having found nothing reported success=True: the
        # solvers had each stopped themselves cleanly at the limit they were
        # given. Under this rule that run is 'unknown', and a planner's
        # proven-unsat (which exits non-zero) is a success.
        self.success = self.outcome in _SUCCESS_OUTCOMES


        # Extract the highest score from any phase that has one
        scores = []
        for phase in self.phases:
            if 'score' in phase.solver_stats and phase.solver_stats['score'] is not None:
                scores.append(phase.solver_stats['score'])
        
        # Set summary score to the maximum found, or None if no scores found
        self.score = max(scores) if scores else None
    
    def get_phase(self, name: str) -> Optional[PhaseResult]:
        """Get a phase by name."""
        for phase in self.phases:
            if phase.name == name:
                return phase
        return None
    
    def get_solving_time(self) -> float:
        """Get the duration of the solving phase."""
        solving_phase = self.get_phase("solving")
        return solving_phase.duration if solving_phase else 0.0
    
    def get_translation_time(self) -> float:
        """Get the duration of the translation phase."""
        translation_phase = self.get_phase("translation")
        return translation_phase.duration if translation_phase else 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)

        # Consumer-owned fields sit at the top level, exactly where a declared
        # field would be, so moving a field into `extra` does not change the
        # file. Phases get the same treatment (asdict has already flattened them
        # to plain dicts, `extra` key and all).
        data = _flatten_extra(data)
        data['phases'] = [_flatten_extra(p) for p in data['phases']]

        # Convert datetime objects to ISO format strings
        if data['start_time']:
            data['start_time'] = self.start_time.isoformat()
        if data['end_time']:
            data['end_time'] = self.end_time.isoformat()
        
        # Drop the filename tune slot when it is the empty default: it is
        # redundant with the filename itself and only clutters older-style
        # summaries that never set it.
        if not data.get('tune_slot'):
            data.pop('tune_slot', None)
        # Same for the readable options label - absent means "reconstruct from
        # the command line" for consumers, keeping older summaries unchanged.
        if not data.get('options'):
            data.pop('options', None)
        # Remove startat, stopat and objbound if they are None (defaults)
        if data.get('startat') is None:
            del data['startat']
        if data.get('stopat') is None:
            del data['stopat']
        if data.get('objbound') is None:
            del data['objbound']
        # Consumer fields need no such pruning: `extra` holds only what a
        # consumer actually put there, so an uncomputed one is simply absent.

        return data
    
    def to_json(self, indent: int = 2) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)
    
    def save(self, path: Path) -> None:
        """Save summary to file."""
        path.write_text(self.to_json())
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'RunSummary':
        """Create RunSummary from dictionary.

        Keys the core does not declare are gathered into ``extra`` rather than
        rejected, so a summary written by another consumer - or by a later
        version that added a field - loads here instead of raising TypeError.
        """
        # Make a copy to avoid modifying the original data
        data = data.copy()

        # Convert datetime strings back to datetime objects
        if data.get('start_time'):
            data['start_time'] = datetime.fromisoformat(data['start_time'])
        if data.get('end_time'):
            data['end_time'] = datetime.fromisoformat(data['end_time'])

        # Convert phase dictionaries to PhaseResult objects
        phases_data = data.pop('phases', [])
        phases = [PhaseResult.from_dict(phase_data) for phase_data in phases_data]

        declared, extra = _split_known(cls, data)
        summary = cls(**declared)
        summary.extra.update(extra)
        summary.phases = phases
        return summary
    
    @classmethod
    def from_json(cls, json_str: str) -> 'RunSummary':
        """Create RunSummary from JSON string."""
        data = json.loads(json_str)
        return cls.from_dict(data)
    
    @classmethod
    def load(cls, path: Path) -> 'RunSummary':
        """Load summary from file."""
        json_str = path.read_text()
        return cls.from_json(json_str)


def create_run_summary(instance: str, model: str, solver: str = "", timestamp: str = None,
                      output_dir: str = "") -> RunSummary:
    """Create a new RunSummary with basic information filled in."""
    summary = RunSummary()
    summary.instance = instance
    summary.model = model
    summary.solver = solver
    summary.timestamp = timestamp
    summary.output_dir = output_dir
    summary.start_time = datetime.now()
    return summary


def create_phase_result(name: str, command: List[str]) -> PhaseResult:
    """Create a new PhaseResult with basic information filled in."""
    return PhaseResult(name=name, command=command.copy())
