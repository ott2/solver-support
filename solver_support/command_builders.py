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

    #: Which tune key holds each Savile Row backend's arg-set. A backend absent
    #: here uses ``sat_args``, so a config with no ``backend:`` keeps behaving as
    #: the kissat SAT backend it has always been.
    SAVILEROW_ARG_KEYS = {
        'minion': 'minion_args',
        'ortools': 'ortools_args',
    }

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
                The backend is a property of this config: ``backend: minion``
                uses its tune presets' minion_args and Minion's timeout dialect,
                ``backend: ortools`` uses ortools_args and drives a FlatZinc
                solver (optionally named by ``fzn_bin``), and no ``backend:`` key
                means the kissat SAT arg-set.
            tune: name of a ``tune:`` preset to apply instead of the config's
                ``default_tune`` (kissat: 'safe'/'bare'/'srdefault'/'gac';
                minion: 'bare'). The preset supplies this backend's arg-set
                (sat_args for kissat, minion_args for minion, ortools_args for
                or-tools). A name that is unknown or lacks this backend's arg key
                falls back to ``default_tune``. This is the savilerow arm of the
                same ``--tune`` dial fd uses for its search presets.

        Returns:
            Complete command as list of strings.

        Raises:
            ModelRegistryError: If savilerow solver not found in config, or the
                resolved tune has no arg-set for this backend.
        """
        solver_config = self.get_solver_config(solver_name)

        # The backend solver is declared by the config, not passed in: `--solver
        # minion` selects the minion backend config. This replaced the old
        # cross-cutting `--minion` boolean. Absent means the kissat SAT backend,
        # which is what every pre-existing config relies on.
        backend = solver_config.get('backend', 'sat')

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

        # Choose the argument set from the active tune preset. kissat presets
        # carry sat_args, minion presets carry minion_args, or-tools presets
        # carry ortools_args; a tune that is unknown or lacks this backend's key
        # falls back to default_tune (the lenient behaviour the fd builder uses
        # too). The model-default tune is resolved by the caller, so `tune` here
        # is already the effective name.
        arg_key = self.SAVILEROW_ARG_KEYS.get(backend, 'sat_args')
        tunes = solver_config.get('tune', {})
        tune_name = self._effective_tune(solver_config, tune, arg_key)
        preset = tunes.get(tune_name, {})
        if arg_key not in preset:
            raise ModelRegistryError(
                f"Solver '{solver_name}' tune '{tune_name}' has no {arg_key}"
            )
        args = preset[arg_key].copy()
        if backend == 'sat':
            # Add objective recording arguments only when needed. This is a SAT
            # facility: Savile Row records intermediate objectives as it drives
            # its own optimisation loop over the SAT solver. A FlatZinc backend
            # optimises internally, so there is nothing for it to record.
            if objective_file_path is not None:
                args.extend(["-record-intermediate-objective-values", objective_file_path])

        # A FlatZinc backend needs the solver binary named when it is not the one
        # Savile Row assumes. Left unset the SR default applies, so the registry
        # never has to bake in one machine's binary name.
        fzn_bin = solver_config.get('fzn_bin')
        if fzn_bin:
            args.extend(["-fzn-bin", fzn_bin])

        # Add timeout arguments
        if timeout is None:
            timeout = solver_config['default_timeout']

        # Each backend spells its own time limit differently. Savile Row's
        # -timelimit covers translation plus solving; the pass-through gives the
        # solver its own budget so it stops itself rather than being killed.
        if timeout is not None:
            if backend == 'minion':
                # For minion, just use -timelimit (no solver-options needed)
                args.extend(["-timelimit", str(timeout)])
            elif backend == 'ortools':
                # fzn-cp-sat takes --time_limit in seconds (an Abseil flag).
                args.extend([
                    "-solver-options", f"--time_limit={timeout}",
                    "-timelimit", str(timeout)
                ])
            else:
                # For SAT solver (kissat), use both -solver-options and -timelimit
                args.extend([
                    "-solver-options", f"--time={timeout}",
                    "-timelimit", str(timeout)
                ])

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
