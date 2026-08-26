#!/usr/bin/env python3
"""Solver command builders for the model registry.

The ``build_*_command`` methods assemble the argv for each solver backend (Savile
Row, Fast Downward / SymK, ENHSP, Conjure) from a solver's registry config and the
effective ``--tune`` preset. This is a distinct concern from the registry's config
loading and its model/solver queries, so the builders live here as a mixin that
:class:`ModelRegistry` inherits.

The split is behaviour-preserving: the methods reference the registry only through
``self`` (``get_solver_config``, ``_effective_tune``, ``is_command_available``, the
``_savilerow_native_warning_shown`` flag), so on the combined instance they resolve
exactly as before. ``ModelRegistryError`` is imported from ``model_registry``; that
back-import is safe because the package ``__init__`` always imports
``model_registry`` first, and ``model_registry`` imports this mixin only after the
exception is defined.
"""

from typing import List, Optional

from .model_registry import ModelRegistryError


class CommandBuilderMixin:
    """The ``build_*_command`` methods mixed into :class:`ModelRegistry`."""

    #: The Savile Row backends, one row each. Everything the argv assembler needs
    #: to know about a backend lives here, so adding one is a single entry plus
    #: its ``<backend>_args`` presets in the config - not an edit spread across an
    #: arg-set lookup, a binary flag and a timeout dialect. The arg-set key is
    #: *derived* (``<backend>_args``) rather than tabulated, so it needs no row.
    #:
    #: ``sat`` is the implicit default: a config with no ``backend:`` key is the
    #: kissat SAT backend every pre-existing config relies on.
    #:
    #: ``timeout_option`` is the *solver's own* time limit, handed to it through
    #: Savile Row's ``-solver-options``; Savile Row's own ``-timelimit`` (covering
    #: translation plus solving) is added for every backend regardless. Giving the
    #: solver its own budget means it stops itself and reports, rather than being
    #: killed with nothing to show.
    #:
    #: ``timeout_scale`` converts the seconds this API speaks into the unit that
    #: solver expects. Gecode and Chuffed both want milliseconds and neither says
    #: so in its flag name, which makes the mistake silent rather than loud: a 30s
    #: budget passed unscaled becomes 30ms, and the solver gives up instantly
    #: looking for all the world like a hard instance.
    SAVILEROW_BACKENDS = {
        'sat': {
            'bin_option': '-satsolver-bin',
            'timeout_option': '--time={value}',
            'timeout_scale': 1,
            # Savile Row drives the optimisation loop itself when the backend is a
            # SAT solver, so it can record each improving objective as it goes.
            # Every other backend optimises internally, leaving SR nothing to see.
            'records_objectives': True,
        },
        'minion': {
            'bin_option': '-minion-bin',
            # Minion takes no time limit of its own here: -timelimit is enough.
            'timeout_option': None,
            'timeout_scale': 1,
            'records_objectives': False,
        },
        'ortools': {
            # SR calls OR-Tools through its "standard FlatZinc" route, so the
            # generic -fzn-bin names the binary; Gecode and Chuffed have their own.
            'bin_option': '-fzn-bin',
            'timeout_option': '--time_limit={value}',  # an Abseil flag, seconds
            'timeout_scale': 1,
            'records_objectives': False,
        },
        'gecode': {
            'bin_option': '-gecode-bin',
            'timeout_option': '-time {value}',  # "time (in ms) cutoff"
            'timeout_scale': 1000,
            'records_objectives': False,
        },
        'chuffed': {
            'bin_option': '-chuffed-bin',
            # Named --time-out but documented "Time out in milliseconds".
            'timeout_option': '--time-out {value}',
            'timeout_scale': 1000,
            'records_objectives': False,
        },
    }

    def records_intermediate_objectives(self, solver_name: str) -> bool:
        """Whether this solver's backend writes the intermediate-objective file.

        True only for Savile Row's SAT arm, which is the one configuration where
        Savile Row runs the optimisation loop and can report improving objectives
        as it finds them. Callers use this to avoid naming (and clearing) a file
        that will never be written; with no such file, continue-scan falls back to
        the injected solution extractor. Resolves through
        :meth:`get_tune_backend`, so the Conjure path answers for the kissat
        solve it delegates to, and non-savilerow solvers answer False.
        """
        backend_solver = self.get_tune_backend(solver_name)
        if self.get_solver_type(backend_solver) != 'savilerow':
            return False
        backend = self.get_solver_config(backend_solver).get('backend', 'sat')
        spec = self.SAVILEROW_BACKENDS.get(backend)
        return bool(spec and spec['records_objectives'])

    def build_savilerow_command(self, model_path: str, param_path: str,
                               use_srjava: bool = False,
                               timeout: Optional[int] = None,
                               objective_file_path: Optional[str] = None,
                               extra_options: Optional[List[str]] = None,
                               solver_name: str = 'kissat',
                               tune: Optional[str] = None) -> List[str]:
        """
        Build a Savile Row command with proper flags for horizon scanning.

        original run script's SRARGS logic:
        - Minion mode: just "-run-solver"
        - SAT mode: "-run-solver -O0 -deletevars -identical-cse -sat -sat-polarity -sat-family kissat"

        Args:
            model_path: path to the model file
            param_path: path to the parameter file
            use_srjava: use Java savilerow instead of savilerow-native
            timeout: override timeout in seconds (uses default_timeout if None)
            objective_file_path: path for intermediate objective values file (default: obj.txt)
            extra_options: additional command line options to pass to Savile Row
            solver_name: name of the solver configuration to use (default: 'kissat').
                The backend is a property of this config, named by its
                ``backend:`` key and described by ``SAVILEROW_BACKENDS``: it
                selects the arg-set key, the solver's own timeout dialect, and
                the flag that names its binary. No ``backend:`` key means the
                kissat SAT backend, which is what every pre-existing config
                relies on.
            tune: name of a ``tune:`` preset to apply instead of the config's
                ``default_tune`` (kissat: 'safe'/'bare'/'srdefault'/'gac';
                minion: 'bare'). The preset supplies this backend's arg-set,
                under the key ``<backend>_args`` - sat_args, minion_args,
                ortools_args, and so on. A name that is unknown or lacks this
                backend's arg key falls back to ``default_tune``. This is the
                savilerow arm of the same ``--tune`` dial fd uses for its search
                presets.

        Returns:
            Complete command as list of strings.

        Raises:
            ModelRegistryError: If savilerow solver not found in config, if the
                config names a backend that is not in ``SAVILEROW_BACKENDS``, or
                if the resolved tune has no arg-set for this backend.
        """
        solver_config = self.get_solver_config(solver_name)

        # The backend solver is declared by the config, not passed in: `--solver
        # minion` selects the minion backend config. This replaced the old
        # cross-cutting `--minion` boolean. Absent means the kissat SAT backend,
        # which is what every pre-existing config relies on.
        #
        # An unrecognised name is an error rather than a fallback. The lenient
        # `_effective_tune` lookup below can afford to shrug at a typo because a
        # wrong tune still runs the right solver; a typo'd backend would silently
        # build the SAT arm's argv - a run that succeeds while comparing the wrong
        # solver, which is worse than no run at all. Same reasoning as
        # `is_valid_tune`, applied one level up.
        backend = solver_config.get('backend', 'sat')
        spec = self.SAVILEROW_BACKENDS.get(backend)
        if spec is None:
            known = ', '.join(sorted(self.SAVILEROW_BACKENDS))
            raise ModelRegistryError(
                f"Solver '{solver_name}' declares unknown backend '{backend}' "
                f"(known backends: {known})"
            )

        # Choose command variant
        if use_srjava:
            # User explicitly requested Java version
            command = solver_config['command']
        else:
            # Try to use native version if available
            native_command = solver_config.get('command_native', 'savilerow-native')
            if self.is_command_available(native_command):
                command = native_command
            else:
                # Fall back to Java version with warning (only warn once)
                if not self._savilerow_native_warning_shown:
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.warning(f"savilerow-native not found in PATH, falling back to Java version (savilerow)")
                    self._savilerow_native_warning_shown = True
                command = solver_config['command']

        # Choose the argument set from the active tune preset. Each backend reads
        # its own key, named after it: sat_args, minion_args, ortools_args, and so
        # on. Deriving the name rather than tabulating it is what lets a new
        # backend arrive without touching this line. A tune that is unknown or
        # lacks this backend's key falls back to default_tune (the lenient
        # behaviour the fd builder uses too). The model-default tune is resolved
        # by the caller, so `tune` here is already the effective name.
        arg_key = f'{backend}_args'
        tunes = solver_config.get('tune', {})
        tune_name = self._effective_tune(solver_config, tune, arg_key)
        preset = tunes.get(tune_name, {})
        if arg_key not in preset:
            raise ModelRegistryError(
                f"Solver '{solver_name}' tune '{tune_name}' has no {arg_key}"
            )
        args = preset[arg_key].copy()
        if spec['records_objectives'] and objective_file_path is not None:
            # Only the SAT arm has intermediate objectives to record, because it
            # is the only one where Savile Row runs the optimisation loop rather
            # than the solver. Asking any other backend for them names a file that
            # is never written.
            args.extend(["-record-intermediate-objective-values", objective_file_path])

        # Name the backend's binary only when the config pins one. Each backend
        # has its own flag for this (-fzn-bin, -gecode-bin, -minion-bin, ...), and
        # left unset Savile Row's default applies - so the registry never has to
        # bake one machine's build into a shared config.
        solver_bin = solver_config.get('solver_bin')
        if solver_bin:
            args.extend([spec['bin_option'], solver_bin])

        # Add timeout arguments
        if timeout is None:
            timeout = solver_config['default_timeout']

        if timeout is not None:
            # The solver's own limit, in whatever flag and unit that solver
            # speaks. A config may override the dialect (or set it to null to
            # suppress it) without the core having to learn a new backend.
            option = solver_config.get('solver_timeout_option', spec['timeout_option'])
            if option:
                scale = spec['timeout_scale']
                # Left untouched when the solver already speaks seconds, so those
                # argvs keep their exact previous form (--time=45, never 45.0).
                value = timeout if scale == 1 else int(timeout * scale)
                args.extend(["-solver-options", option.format(value=value)])
            # Savile Row's own limit covers translation plus solving and is always
            # in seconds. Always added, so a backend with no limit of its own is
            # still bounded - and one that has its own stops itself and reports,
            # rather than being killed with nothing to show.
            args.extend(["-timelimit", str(timeout)])

        # Add extra options if provided
        if extra_options:
            args.extend(extra_options)

        # Build complete command
        cmd = [command] + args + [str(model_path), str(param_path)]

        return cmd

    def build_fd_command(self, model_path: str, param_path: str,
                        anytime: bool = False, timeout: Optional[int] = None,
                        tune: Optional[str] = None,
                        extra_options: Optional[List[str]] = None,
                        solver_name: str = 'fd') -> List[str]:
        """
        Build a Fast Downward command with proper flags.

        Args:
            model_path: Path to the PDDL domain file.
            param_path: Path to the PDDL problem file.
            anytime: Whether to run the anytime portfolio (the ``soup`` tune).
                When true the pre-file ``--alias`` form is emitted and ``tune``
                is ignored; the caller decides this via ``is_anytime_tune`` (the
                tune's ``regime: anytime``), not the model name.
            timeout: Override timeout in seconds (uses default_timeout if None).
            tune: Name of a ``tune:`` preset to apply instead of the config's
                ``default_tune`` (fd: 'hmax'/'ff'/'lmcut'/'verbose';
                symk: 'fw'/'bw'/'bd'). A search-bearing preset becomes the
                post-file ``--search`` argument; the ``soup`` preset (an
                ``alias``) is the pre-file portfolio. An unknown or searchless
                name falls back to ``default_tune``. This is the fd-family arm
                of the same ``--tune`` dial savilerow uses for its arg-sets.
            extra_options: raw ``--options`` tokens passed straight through,
                appended after all generated arguments (the runner does not
                interpret them - the caller is responsible for their placement
                being valid for Fast Downward's command line).
            solver_name: Which fd-type solver config to use ('fd' or 'symk').
                SymK is a Fast Downward fork, so it shares this builder; the
                config supplies the command (fd vs symk) and its tune presets.

        Returns:
            Complete command as list of strings.

        Raises:
            ModelRegistryError: If the solver is not found in config, or has no
                usable tune preset for the requested mode.
        """
        solver_config = self.get_solver_config(solver_name)

        # Add timeout argument
        if timeout is None:
            timeout = solver_config['default_timeout']

        tunes = solver_config.get('tune', {})

        # Build command with correct argument order for FD
        if anytime:
            # Anytime portfolio: the pre-file --alias from the soup tune preset,
            # e.g. fd --alias seq-sat-fdss-2018 --search-time-limit <timeout>
            # domain.pddl problem.pddl. `soup` is currently the only anytime fd
            # tune, so it is the one resolved here.
            alias = tunes.get('soup', {}).get('alias')
            if not alias:
                raise ModelRegistryError(
                    f"Solver '{solver_name}' has no 'soup' tune preset for anytime mode"
                )
            pre_file_args = ["--alias", alias]
            if timeout is not None:
                pre_file_args.extend(["--search-time-limit", str(timeout)])
            cmd = [solver_config['command']] + pre_file_args + [str(model_path), str(param_path)]
        else:
            # Standard mode: the post-file --search string from a tune preset,
            # e.g. fd --search-time-limit <timeout> domain.pddl problem.pddl
            # --search "astar(blind())". A requested tune that doesn't exist or
            # carries no search string (e.g. soup) falls back to default_tune.
            name = self._effective_tune(solver_config, tune, 'search')
            search = tunes.get(name, {}).get('search')
            if not search:
                raise ModelRegistryError(
                    f"Solver '{solver_name}' has no search string for tune '{name}'"
                )
            pre_file_args = []
            if timeout is not None:
                pre_file_args = ["--search-time-limit", str(timeout)]
            post_file_args = ["--search", search]
            cmd = [solver_config['command']] + pre_file_args + [str(model_path), str(param_path)] + post_file_args

        # Raw --options passthrough, appended last (uninterpreted).
        if extra_options:
            cmd.extend(extra_options)

        return cmd

    def build_enhsp_command(self, model_path: str, param_path: str,
                            plan_path: str, tune: Optional[str] = None,
                            extra_options: Optional[List[str]] = None,
                            solver_name: str = 'enhsp') -> List[str]:
        """
        Build an ENHSP command.

        ENHSP takes the domain and problem as ``-o``/``-f`` and writes its plan
        (the same ``(action args)`` form as Fast Downward's ``sas_plan``) to the
        ``-sp`` file. The active tune preset selects the ``-planner``
        preconfiguration (sat-* satisficing / opt-* metric-optimal); an unknown
        or planner-less ``tune`` falls back to the config's ``default_tune``,
        the same leniency as the fd/savilerow builders. ENHSP has no internal
        time limit outside anytime mode, so no timeout flag is emitted - the
        SolverRunner's external timeout terminates it.

        Args:
            model_path: Path to the PDDL domain file.
            param_path: Path to the PDDL problem file.
            plan_path: Where ENHSP should write the plan (the ``-sp`` argument).
            tune: Name of a ``tune:`` preset (a planner config) to apply instead
                of the config's ``default_tune``.
            extra_options: raw ``--options`` tokens passed straight through,
                appended after the generated arguments (uninterpreted).
            solver_name: Which enhsp-type solver config to use.

        Returns:
            Complete command as list of strings.

        Raises:
            ModelRegistryError: If the solver has no usable planner tune.
        """
        solver_config = self.get_solver_config(solver_name)
        tunes = solver_config.get('tune', {})
        name = self._effective_tune(solver_config, tune, 'planner')
        planner = tunes.get(name, {}).get('planner')
        if not planner:
            raise ModelRegistryError(
                f"Solver '{solver_name}' has no planner for tune '{name}'"
            )
        cmd = [
            solver_config['command'],
            "-o", str(model_path),
            "-f", str(param_path),
            "-planner", planner,
            "-sp", str(plan_path),
        ]
        if extra_options:
            cmd.extend(extra_options)
        return cmd

    def build_conjure_command(self, action: str, **kwargs) -> List[str]:
        """
        Build a Conjure command for different actions.

        Args:
            action: Action type ('modelling', 'translate-parameter', 'translate-solution')
            **kwargs: Action-specific arguments:
                - modelling: model_path
                - translate-parameter: essence_param, eprime
                - translate-solution: essence_param, eprime, eprime_solution, essence_solution

        Returns:
            Complete command as list of strings.

        Raises:
            ModelRegistryError: If conjure solver not found in config or invalid action.
        """
        solver_config = self.get_solver_config('conjure')

        # Get base arguments for the action
        action_key = f"{action.replace('-', '_')}_args"
        if action_key not in solver_config:
            raise ModelRegistryError(f"Unknown conjure action: {action}")

        args = solver_config[action_key].copy()

        # Build action-specific arguments
        if action == 'modelling':
            model_path = kwargs.get('model_path')
            if not model_path:
                raise ModelRegistryError("model_path required for conjure modelling")
            cmd = [solver_config['command']] + args + [str(model_path)]

        elif action == 'translate-parameter':
            essence_param = kwargs.get('essence_param')
            eprime = kwargs.get('eprime')
            if not essence_param or not eprime:
                raise ModelRegistryError("essence_param and eprime required for translate-parameter")
            cmd = [solver_config['command']] + args + [
                f"--essence-param={essence_param}",
                f"--eprime={eprime}"
            ]

        elif action == 'translate-solution':
            essence_param = kwargs.get('essence_param')
            eprime = kwargs.get('eprime')
            eprime_solution = kwargs.get('eprime_solution')
            essence_solution = kwargs.get('essence_solution')
            if not all([essence_param, eprime, eprime_solution, essence_solution]):
                raise ModelRegistryError(
                    "essence_param, eprime, eprime_solution, and essence_solution required for translate-solution"
                )
            cmd = [solver_config['command']] + args + [
                f"--essence-param={essence_param}",
                f"--eprime={eprime}",
                f"--eprime-solution={eprime_solution}",
                f"--essence-solution={essence_solution}"
            ]

        else:
            raise ModelRegistryError(f"Unknown conjure action: {action}")

        return cmd
