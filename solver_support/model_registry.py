#!/usr/bin/env python3
"""
Model Registry for solver configurations.

This module provides a centralized registry for managing model files, solvers,
and their configurations. It replaces the hard-coded model paths and logic
from the original bash run script.
"""

from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple, Callable
import copy
import shutil
import yaml


class ModelRegistryError(Exception):
    """Exception raised for model registry errors."""
    pass


# Imported after ModelRegistryError so command_builders (which raises it) can
# back-import it without a cycle; the package __init__ always imports this
# module before anything reaches command_builders.
from .command_builders import CommandBuilderMixin


# --- consumer packaging injection (registered once by a consumer) ---
#
# The registry is a generic loader: it does not know which package ships the
# ``models.yaml`` it loads, nor where the model files that yaml references live,
# nor where a consumer keeps its optional user-override config. A consumer
# registers those once, at import (see the setters below), so the core carries
# no consumer package name. Until a consumer registers them the registry
# looks only for a config sitting next to this module (a bare core ships none, so
# construction then raises, telling the caller to supply a config).

_default_config_path = None          # path/Traversable to the default models.yaml
_resource_root = None                # path/Traversable to the consumer package dir
_user_config_finder: Optional[Callable[[], Optional[Path]]] = None


def set_default_config_path(path) -> None:
    """Register the default ``models.yaml`` the registry loads at startup.

    Stored as-is (a path or an ``importlib.resources`` traversable), so the
    loader can ``open`` it directly regardless of how the consumer resolved it.
    """
    global _default_config_path
    _default_config_path = path


def set_resource_root(root) -> None:
    """Register the consumer package root used to resolve model file paths."""
    global _resource_root
    _resource_root = root


def set_user_config_finder(finder: Optional[Callable[[], Optional[Path]]]) -> None:
    """Register ``finder() -> Optional[Path]`` for an optional user-override config.

    Called at registry *construction* (not import), so cwd-relative candidates
    resolve against the working directory in effect for the run. Unregistered
    means no user-override search.
    """
    global _user_config_finder
    _user_config_finder = finder


class ModelRegistry(CommandBuilderMixin):
    """
    Registry for models, solvers, and their configurations.
    
    Provides a centralized way to:
    - Map model types to file paths
    - Resolve model variants (e.g., compactrows2, compactrows3)
    - Select appropriate solvers for model types
    - Support custom configuration via YAML files
    """
    
    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize the model registry.
        
        Args:
            config_path: Optional path to YAML configuration file.
                        If None, will search for config in standard locations.
        """
        # First, load the default YAML config from package
        default_yaml = self._find_default_yaml_config()
        if not default_yaml:
            raise ModelRegistryError(
                "Default models.yaml configuration file not found. "
                "Please ensure the package is properly installed."
            )
        
        self.config = {'models': {}, 'solvers': {}}
        self._savilerow_native_warning_shown = False  # Track if we've shown the warning
        # Every file merged in, in the order merged, so a caller can report what
        # actually contributed. Without this the answer is only reconstructible
        # by re-running the same two searches by hand - and the user-override one
        # depends on the cwd at construction time, so it is not reproducible
        # later. See `dump_config`.
        self.config_sources: List[Path] = []
        self._load_yaml_config(default_yaml)

        # Then, load user configuration to override defaults
        user_yaml = config_path or self._find_user_yaml_config()
        if user_yaml:
            self._load_yaml_config(user_yaml)
    
    def _find_default_yaml_config(self) -> Optional[Path]:
        """
        Find the default YAML configuration the registry loads at startup.

        Prefers a consumer-registered path (see ``set_default_config_path``);
        otherwise looks for a config sitting next to this module, which a bare
        core does not ship.

        Returns:
            Path to default config file, or None if not found.
        """
        if _default_config_path is not None:
            try:
                if _default_config_path.is_file():
                    return _default_config_path
            except (AttributeError, OSError):
                pass

        beside_core = Path(__file__).parent / "config" / "models.yaml"
        return beside_core if beside_core.exists() else None

    def _find_user_yaml_config(self) -> Optional[Path]:
        """
        Find an optional user-override config via the consumer's finder.

        Returns:
            Path to the user config file, or None if no finder is registered or
            it finds nothing.
        """
        if _user_config_finder is None:
            return None
        return _user_config_finder()
    
    def _load_yaml_config(self, config_path: Path) -> None:
        """
        Load YAML configuration file and merge with defaults.
        
        Args:
            config_path: Path to YAML configuration file.
        """
        try:
            with open(config_path, 'r') as f:
                user_config = yaml.safe_load(f)

            # Deep merge user config with existing config
            self._deep_merge(self.config, user_config)
            self.config_sources.append(Path(str(config_path)))

        except Exception as e:
            raise ModelRegistryError(f"Failed to load config file {config_path}: {e}")
    
    def _deep_merge(self, base: Dict[str, Any], update: Dict[str, Any]) -> None:
        """
        Deep merge update dictionary into base dictionary.
        
        Args:
            base: Base dictionary to update.
            update: Dictionary with updates to apply.
        """
        for key, value in update.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

    def effective_config(self, resolved: bool = False) -> Dict[str, Any]:
        """The configuration actually in force, as a plain dict.

        A deep copy of the merge of every file in ``config_sources``: the
        consumer's bundled config first, then any user override merged over it.
        Answering "what is in force?" otherwise means reading both files and
        performing the merge by eye - and the override's location depends on the
        working directory at construction time, so it cannot reliably be
        reconstructed afterwards.

        With ``resolved``, values a *builder* supplies when a key is absent are
        written out explicitly - currently a savilerow solver's implicit
        ``backend: sat``, the one default whose absence changes which arg-set,
        timeout dialect and binary flag are used. Only real config keys are ever
        added, so a resolved dump still loads; see :meth:`dump_config` for why
        loading one back is not what it is for.

        The copy is deep, so a caller may edit the result without disturbing the
        live registry.
        """
        config = copy.deepcopy(self.config)
        if resolved:
            for solver_config in config.get('solvers', {}).values():
                if (isinstance(solver_config, dict)
                        and solver_config.get('solver_type') == 'savilerow'):
                    solver_config.setdefault('backend', 'sat')
        return config

    def dump_config(self, resolved: bool = False, header: bool = True) -> str:
        """The effective configuration as YAML text, for inspection.

        Intended for a consumer's ``--show-registry``-style flag: it answers what
        the registry resolved to and which files contributed. It is deliberately
        *not* a config generator. Two reasons, both stated in the emitted header:

        - Comments are not preserved. A config's comments carry the reasoning for
          its arg-sets and timeouts, and a YAML round-trip drops all of them, so
          the dump is a view of the configuration rather than a replacement.
        - Overrides are deep-merged, so adopting a full dump as a user config
          pins every key at today's value and silently shadows later changes to
          the bundled config. A partial override is the supported shape: copy
          only the keys being changed.
        """
        text = yaml.safe_dump(self.effective_config(resolved=resolved),
                              sort_keys=False, default_flow_style=False,
                              allow_unicode=True)
        if not header:
            return text

        lines = ["# Effective solver-support registry, merged in this order:"]
        lines.extend(f"#   {i}. {source}"
                     for i, source in enumerate(self.config_sources, start=1))
        if not self.config_sources:
            lines.append("#   (none)")
        if resolved:
            lines.append("#")
            lines.append("# Defaults a builder would supply are written out "
                         "explicitly here.")
        lines.extend([
            "#",
            "# For reading, not for adopting wholesale. Comments in the source",
            "# files are not preserved, and overrides are deep-merged - so using",
            "# this whole file as an override would pin every key at today's",
            "# value and silently shadow later changes. Copy only what you mean",
            "# to change.",
            "",
        ])
        return "\n".join(lines) + "\n" + text

    def get_model_path(self, model_type: str) -> Path:
        """
        Get the file path for a model type.
        
        Tries to resolve model files from the installed package first,
        then falls back to working directory relative paths.
        
        Args:
            model_type: The model type (e.g., 'compact', 'compactrows2').
            
        Returns:
            Path to the model file or command name for command-based models.
            
        Raises:
            ModelRegistryError: If model type is not found.
        """
        model_name, model_config = self.resolve_model_variant(model_type)
        return self._resolve_model_path(model_config, model_name)
    
    def _resolve_resource_path(self, path_str: str) -> Optional[Path]:
        """
        Resolve a model resource path against the consumer's package root.

        Handles paths that may be:
        1. In the consumer's package resources (the registered resource root)
        2. In the working directory

        Args:
            path_str: The path string to resolve.

        Returns:
            Path to the resource file if found, None if not found.
        """
        # Try to resolve from the consumer's registered package resources first.
        if _resource_root is not None:
            try:
                if path_str.startswith('models/'):
                    # Extract filename for environments where spec.origin is None
                    filename = path_str.split('/')[-1]
                    resource_file = _resource_root / 'models' / filename
                else:
                    # For non-models paths (like upf.py), resolve under the root.
                    resource_file = _resource_root / path_str
                if resource_file.is_file():
                    return resource_file
            except (FileNotFoundError, AttributeError, ValueError, TypeError):
                # Fall through to working directory lookup
                pass

        # Fallback to a working-directory relative path.
        fallback_path = Path(path_str)
        if fallback_path.exists():
            return fallback_path

        return None
    
    def _resolve_model_path(self, model_config: Dict[str, Any], model_type: str) -> Path:
        """
        Resolve a model path from config, trying package resources first.
        
        Args:
            model_config: The model configuration dictionary.
            model_type: The model type name for error messages.
            
        Returns:
            Path to the model file.
            
        Raises:
            ModelRegistryError: If model has no path specified.
        """
        # Handle path-based models
        if 'path' not in model_config:
            raise ModelRegistryError(f"Model {model_type} has no path specified")
        
        resolved_path = self._resolve_resource_path(model_config['path'])
        if resolved_path is None:
            # Return the path as-is if it couldn't be resolved (for backward compatibility)
            return Path(model_config['path'])
        
        return resolved_path
    
    def get_control_path(self, model_type: str) -> Optional[Path]:
        """
        Get the control parameter file path for a model type if one exists.
        
        Args:
            model_type: The model type to get control path for.
            
        Returns:
            Path to control file if configured and found, None otherwise.
            
        Raises:
            ModelRegistryError: If model type is not found.
        """
        _, model_config = self.resolve_model_variant(model_type)
        
        if 'control-path' not in model_config:
            return None
            
        return self._resolve_resource_path(model_config['control-path'])
    
    
    def resolve_model_variant(self, model_type: str) -> Tuple[str, Dict[str, Any]]:
        """
        Resolve model type to its canonical name and configuration.
        
        Args:
            model_type: The model type to resolve.
            
        Returns:
            Tuple of (canonical_model_name, model_config).
            
        Raises:
            ModelRegistryError: If model type cannot be resolved.
        """
        model_type_lower = model_type.lower()
        
        # Direct match
        if model_type_lower in self.config['models']:
            return model_type_lower, self.config['models'][model_type_lower]
        
        # Check aliases
        for model_name, model_config in self.config['models'].items():
            aliases = model_config.get('aliases', [])
            if model_type_lower in [alias.lower() for alias in aliases]:
                return model_name, model_config
        
        raise ModelRegistryError(f"Cannot resolve model type: {model_type}")
    
    def get_solver_for_model(self, model_path: Path) -> str:
        """
        Determine the appropriate solver for a model file.
        
        Args:
            model_path: Path to the model file.
            
        Returns:
            Solver command name.
            
        Raises:
            ModelRegistryError: If no solver can be determined.
        """
        suffix = model_path.suffix.lower()
        
        for solver_name, solver_config in self.config['solvers'].items():
            if suffix in solver_config['extensions']:
                return solver_name
        
        raise ModelRegistryError(f"No solver found for file extension: {suffix}")
    
    def get_solver_config(self, solver_name: str) -> Dict[str, Any]:
        """
        Get configuration for a solver.

        Args:
            solver_name: Name of the solver.

        Returns:
            Solver configuration dictionary.

        Raises:
            ModelRegistryError: If solver is not found.
        """
        if solver_name not in self.config['solvers']:
            raise ModelRegistryError(f"Unknown solver: {solver_name}")

        return self.config['solvers'][solver_name]

    def get_solver_type(self, solver_name: str) -> str:
        """
        Get the base solver type for a solver variant.

        Args:
            solver_name: Name of the solver (e.g., 'kissat', 'minion').

        Returns:
            Base solver type (e.g., 'savilerow', 'fd', 'conjure', 'upf').
            Falls back to solver_name if solver_type is not specified.

        Raises:
            ModelRegistryError: If solver is not found.
        """
        solver_config = self.get_solver_config(solver_name)
        return solver_config.get('solver_type', solver_name)

    def get_default_model_for_solver(self, solver_name: str) -> Optional[str]:
        """
        Get the default model for a given solver.

        Args:
            solver_name: Name of the solver.

        Returns:
            Default model name or None if no default defined.

        Raises:
            ModelRegistryError: If solver is not found.
        """
        solver_config = self.get_solver_config(solver_name)
        return solver_config.get('default_model')

    def is_solver_compatible_with_model(self, solver_name: str, model_name: str) -> bool:
        """
        Check if a solver is compatible with a model.

        Args:
            solver_name: Name of the solver to check.
            model_name: Name of the model to check.

        Returns:
            True if compatible, False otherwise.

        Raises:
            ModelRegistryError: If solver or model is not found.
        """
        # Get the solver type of the given solver
        solver_type = self.get_solver_type(solver_name)

        # Get the model configuration
        try:
            _, model_config = self.resolve_model_variant(model_name)
        except (ValueError, ModelRegistryError):
            return False

        # Get the solver configured for this model
        model_solver = model_config.get('solver')
        if not model_solver:
            return False

        # Get the solver type of the model's solver
        model_solver_type = self.get_solver_type(model_solver)

        # Compatible if they have the same solver type ...
        if solver_type == model_solver_type:
            return True

        # ... or if both consume the same instance format. fd / symk / enhsp / upffd
        # all take .pddl, so any of them can solve a .pddl model even though their
        # solver_types differ (direct binary vs UPF). This is what lets, e.g.,
        # `pddlnoaxiom --solver fd` or `pddl --solver enhsp` be expressed as a
        # model + --solver pair rather than minting a combination-model. (Whether the
        # solver then *succeeds* - e.g. enhsp rejects the axiom model - is a runtime
        # matter, not a registry incompatibility.)
        try:
            solver_exts = set(self.get_solver_config(solver_name).get('extensions', []))
            model_solver_exts = set(self.get_solver_config(model_solver).get('extensions', []))
        except (ValueError, ModelRegistryError):
            return False
        return bool(solver_exts & model_solver_exts)
    
    def list_models(self) -> List[str]:
        """
        Get list of all available model types, including aliases.
        
        Returns:
            List of model type names and their aliases.
        """
        models = list(self.config['models'].keys())
        
        # Add aliases
        for model_config in self.config['models'].values():
            aliases = model_config.get('aliases', [])
            models.extend(aliases)
        
        return sorted(models)
    
    def list_solvers(self) -> List[str]:
        """
        Get list of all available solvers.
        
        Returns:
            List of solver names.
        """
        return list(self.config['solvers'].keys())
    
    
    def get_prob2any_format(self, model_type: str) -> Optional[str]:
        """
        Get the prob2any format name for a model type.
        
        Args:
            model_type: The model type.
            
        Returns:
            The prob2any format name, or None if not found.
        """
        try:
            _, model_config = self.resolve_model_variant(model_type)
        except (ValueError, ModelRegistryError):
            return None
        return model_config.get('format-name')
    
    def get_continue_scan_enabled(self, model_type: str) -> bool:
        """
        Check if a model has continue-scan enabled for horizon scanning.
        
        Args:
            model_type: The model type.
            
        Returns:
            True if continue-scan is enabled, False otherwise.
        """
        try:
            _, model_config = self.resolve_model_variant(model_type)
        except (ValueError, ModelRegistryError):
            return False
        return model_config.get('continue-scan', False)

    def get_objective_in_solution(self, model_type: str) -> bool:
        """Whether a model encodes its objective inside its solution file.

        True for models whose optimised objective is read back from the produced
        solution - the scored* CP models write a per-cell ``score`` vector the
        horizon loop sums - as opposed to models that report their objective via
        Savile Row's separate ``.obj`` file (e.g. the move-minimising
        ``essence``/``essenceld`` models, and the ``reachability-*`` variants).
        The horizon loop uses this to decide where to read a solving horizon's
        objective, whether to force continue-scan under a timeout, and whether to
        salvage a timeout solution's score. Bounded non-optimisation models carry
        no such field and default to False.

        Deliberately kept distinct from ``opt`` even though today every
        objective-in-solution model is also an optimisation model (and vice versa
        on the CP horizon path): the distinction lets a future opt-but-not-scored
        model keep reading its objective from ``.obj``.

        Args:
            model_type: The model type.

        Returns:
            True if the model declares ``objective-in-solution: true``, else False.
        """
        try:
            _, model_config = self.resolve_model_variant(model_type)
        except (ValueError, ModelRegistryError):
            return False
        return model_config.get('objective-in-solution', False)

    def get_proves_unsat(self, model_type: str) -> bool:
        """Whether a model can soundly conclude unsatisfiability.

        True only for complete decision procedures (the pddl* planning models
        and the upf/upflocal viewpoints, flagged ``proves-unsat: true``) whose
        'no solution' run is a genuine unsat proof. Bounded CP models carry no
        such field and so default to False - their 'failed'/'no solution' is an
        error or an unfinished horizon search, never an unsat verdict.

        Args:
            model_type: The model type.

        Returns:
            True if the model declares ``proves-unsat: true``, else False.
        """
        try:
            _, model_config = self.resolve_model_variant(model_type)
        except (ValueError, ModelRegistryError):
            return False
        return model_config.get('proves-unsat', False)

    def get_model_status(self, model_type: str) -> Tuple[Optional[str], Optional[str]]:
        """Return a model's health status and an optional detail string.

        Status is ``'broken'`` (known to produce wrong results), ``'experimental'``
        (unfinished/slow but not known-wrong), or ``None`` (ordinary). The detail,
        when present, is a short human-readable reason. Resolves aliases, so
        ``compactroe`` reports its canonical entry's status. Never raises - an
        unknown model is simply ``(None, None)``.
        """
        try:
            _, model_config = self.resolve_model_variant(model_type)
        except (ValueError, ModelRegistryError):
            return None, None
        return model_config.get('status'), model_config.get('status-detail')

    def get_model_default_tune(self, model_type: str) -> Optional[str]:
        """Return a model's preferred ``--tune`` preset, or None.

        A model may pin a non-default backend tune via its ``tune:`` field -
        e.g. ``scored3`` pins ``safe`` to dodge a -deletevars bug. The runner
        uses this as the effective tune when the user passes no ``--tune``.
        Resolves aliases and never raises (unknown model -> None).
        """
        try:
            _, model_config = self.resolve_model_variant(model_type)
        except (ValueError, ModelRegistryError):
            return None
        return model_config.get('tune')

    def get_tune_backend(self, solver_name: str) -> str:
        """Return the backend whose tune presets govern ``--tune`` for a solver.

        Almost always the solver itself. The exception is the Conjure/Essence
        path: its per-horizon inner solve is delegated to kissat (see
        ConjureSolver), so an Essence run's ``--tune`` presets are kissat's, not
        the tune-less ``conjure`` entry's. Keeping this mapping here means the
        CLI validity check and ``build_*_command`` agree on what a tune means.
        """
        if self.get_solver_type(solver_name) == 'conjure':
            return 'kissat'
        return solver_name

    def get_valid_tunes(self, solver_name: str) -> List[str]:
        """Return the tune preset names a solver's tune backend accepts.

        Empty when the backend defines no presets (e.g. the upf* wrappers, which
        take no ``--tune``). Raises ModelRegistryError for an unknown solver.
        """
        backend = self.get_tune_backend(solver_name)
        return list(self.get_solver_config(backend).get('tune', {}).keys())

    def is_valid_tune(self, solver_name: str, tune: str) -> bool:
        """True if ``tune`` is a preset the solver's tune backend accepts.

        Mirrors ``build_*_command`` lookup: a name is honoured only when it is a
        key in the backend's tune dict; anything else silently falls back to
        ``default_tune`` - which this check exists to turn into an explicit error
        so a mistyped ``--tune`` cannot quietly run the default configuration.
        """
        return tune in self.get_valid_tunes(solver_name)

    @staticmethod
    def _effective_tune(solver_config: Dict[str, Any], tune: Optional[str],
                        key: str) -> Optional[str]:
        """Resolve the tune name whose preset carries ``key``, else default_tune.

        The lenient lookup shared by the ``build_*_command`` / ``get_upf_*``
        helpers: an explicit ``tune`` wins only if its preset actually supplies
        this backend's key (e.g. ``'search'``, ``'planner'``, ``'engine'``,
        ``'fd_search'``, or a savilerow ``sat_args``/``minion_args`` key);
        otherwise fall back to the solver's ``default_tune``. Callers then read
        the value out of the resolved preset themselves.
        """
        tunes = solver_config.get('tune', {})
        if tune and key in tunes.get(tune, {}):
            return tune
        return solver_config.get('default_tune')

    def get_upf_engine(self, solver_name: str,
                       tune: Optional[str] = None) -> Optional[str]:
        """Return the UPF OneshotPlanner engine for a upfplan solver under ``tune``.

        The upf* solvers pick their engine the way savilerow picks ``sat_args`` and
        the direct ``enhsp`` solver picks its ``-planner``: from the effective
        ``--tune`` preset (its ``engine`` key), falling back to the solver's
        ``default_tune`` and then to the base ``engine_name``. This is what makes
        optimality a ``--tune`` dial rather than a separate solver - ``upfenhsp``
        resolves to the metric-optimal ``enhsp-opt`` by default and ``--tune
        satisficing`` swaps in the plain ``enhsp`` engine. A solver with no ``tune``
        block (upffd/upfsymk) simply returns its ``engine_name``.

        Returns None only when the solver defines neither a tune engine nor an
        ``engine_name``; the caller then applies its own hardcoded default.
        """
        solver_config = self.get_solver_config(solver_name)
        tunes = solver_config.get('tune', {})
        name = self._effective_tune(solver_config, tune, 'engine')
        return tunes.get(name, {}).get('engine') or solver_config.get('engine_name')

    def get_upf_fd_search(self, solver_name: str,
                          tune: Optional[str] = None) -> Optional[str]:
        """Return the Fast Downward search config for a upfplan solver under ``tune``.

        The companion to :meth:`get_upf_engine` for the fd-driven upf* solvers
        (``upffd``): the search strategy (optimal blind A* vs satisficing greedy) is a
        ``--tune`` preset's ``fd_search`` string, resolved the same way - an explicit
        tune with an ``fd_search`` wins, else ``default_tune``, else the base
        ``fd_search`` field. UpfPlanSolver forwards the result to the engine as
        ``--fd-search`` (--domain route) or through ``compile_and_solve`` (--build
        route). Returns None for solvers that pin no search (enhsp/symk ignore it).
        """
        solver_config = self.get_solver_config(solver_name)
        tunes = solver_config.get('tune', {})
        name = self._effective_tune(solver_config, tune, 'fd_search')
        return tunes.get(name, {}).get('fd_search') or solver_config.get('fd_search')

    def is_optimization_model(self, model_type: str) -> bool:
        """
        Check if a model is an optimization model (has minimising/maximising objective).
        
        Args:
            model_type: The model type.
            
        Returns:
            True if the model has optimization, False otherwise.
        """
        try:
            _, model_config = self.resolve_model_variant(model_type)
            return model_config.get('opt', False)
        except Exception:
            return False
    
    def list_prob2any_formats(self) -> List[str]:
        """
        List all available prob2any format names from the model registry.
        
        Returns:
            List of format names that are supported by prob2any.
        """
        formats = set()
        for model_config in self.config['models'].values():
            format_name = model_config.get('format-name')
            if format_name:
                formats.add(format_name)
        return sorted(list(formats))

    @staticmethod
    def is_command_available(command: str) -> bool:
        """
        Check if a command is available in the system PATH.
        
        Args:
            command: Command name to check.
            
        Returns:
            True if command is available, False otherwise.
        """
        return shutil.which(command) is not None

    def get_tune_regime(self, solver_name: str, tune: Optional[str] = None) -> Optional[str]:
        """The *regime* of the effective tune for ``solver_name`` (the ``regime``
        field in the registry), or ``None`` when unset.

        The regime is the interpreted "how to search" dial, orthogonal to a
        tune's solver-arg set: ``anytime`` keeps emitting improving solutions
        (the fd portfolio "soup"; the CP horizon-scan's continue-scan), ``first``
        stops at the first solution found. A regime-only preset carries just this
        field and no args, so it composes with the default arg-set.

        The effective tune is ``tune`` when it names a preset of this solver,
        otherwise the solver's ``default_tune`` (whose regime is normally unset,
        so the common case returns ``None``). An unknown solver returns ``None``.
        """
        try:
            solver_config = self.get_solver_config(solver_name)
        except ModelRegistryError:
            return None
        tunes = solver_config.get('tune', {})
        name = tune if tune in tunes else solver_config.get('default_tune')
        return tunes.get(name, {}).get('regime')

    def is_anytime_tune(self, solver_name: str, tune: Optional[str] = None) -> bool:
        """Whether the effective tune for ``solver_name`` runs in the *anytime*
        regime (``regime: anytime`` in the registry).

        Anytime is the fd portfolio "soup" mode: it emits successive improving
        plans and tolerates portfolio members that bail out (exit code 34, no
        plan, or running out of time). This predicate is the single trigger for
        that runtime handling, replacing the old ``"pddlsoup" in model_type``
        name-sniff — soup is no longer a model but the ``soup`` tune of the fd
        backend, so the behaviour keys off the active tune, not the model name.

        Thin wrapper over :meth:`get_tune_regime` so the effective-tune resolution
        lives in exactly one place.
        """
        return self.get_tune_regime(solver_name, tune) == 'anytime'


def get_default_registry() -> ModelRegistry:
    """
    Get a default model registry instance.
    
    Returns:
        ModelRegistry instance with default configuration.
    """
    return ModelRegistry()


def check_model_registry_consistency(registry: Optional[ModelRegistry] = None) -> List[str]:
    """
    Check internal consistency of the model registry.
    
    Currently checks:
    - Models with optimization objectives match their 'opt' flag setting
    
    Args:
        registry: Model registry to check. If None, uses default registry.
        
    Returns:
        List of inconsistency messages. Empty list means everything is consistent.
    """
    import re
    
    if registry is None:
        registry = get_default_registry()
    
    inconsistencies = []
    opt_pattern = re.compile(r'^\s*(min|max)imi(s|z)ing\s', re.MULTILINE)
    
    for model_name, model_config in registry.config['models'].items():
        # Skip models without file paths (like tikz)
        if 'path' not in model_config:
            continue
            
        model_path = registry._resolve_resource_path(model_config['path'])
        if model_path is None or not model_path.exists():
            # Skip models whose files can't be found
            continue
            
        # Check if model file contains optimization directive
        try:
            with open(model_path, 'r') as f:
                content = f.read()
            has_opt_directive = bool(opt_pattern.search(content))
            
            # Check against opt flag
            has_opt_flag = model_config.get('opt', False)
            
            if has_opt_directive and not has_opt_flag:
                inconsistencies.append(
                    f"Model '{model_name}' has optimization directive but 'opt' flag is not set to True"
                )
            elif not has_opt_directive and has_opt_flag:
                inconsistencies.append(
                    f"Model '{model_name}' has 'opt' flag set to True but no optimization directive found"
                )
                
        except Exception as e:
            inconsistencies.append(
                f"Could not check model '{model_name}': {e}"
            )
    
    return inconsistencies


