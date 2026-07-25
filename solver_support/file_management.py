"""
File management utilities for puzzle solvers.

This module provides centralized file naming conventions, path generation,
and file operations used by various solver implementations.
"""

import hashlib
import logging
from pathlib import Path
from typing import List, Optional


def _normalise_tune(tune) -> List[str]:
    """Normalise a ``--tune`` value to a list of names.

    Accepts None, a single name, a comma-separated ``a,b`` string, or a list.
    """
    if not tune:
        return []
    if isinstance(tune, str):
        return [t for t in tune.split(',') if t]
    return [t for t in tune if t]


def compute_tune_slot(tune=None, options=None) -> str:
    """Build the ``‹tune›`` filename slot that sits between solver and model.

    The slot lets runs that differ only in their configuration produce distinct,
    directly comparable filenames (the user benchmarks e.g. default / -O1 / -O2
    against each other, so those must not collide). It is:

    - ``defopt`` when neither ``--tune`` nor ``--options`` was given (the
      default configuration);
    - the requested tune name when ``--tune`` was used (a comma-separated
      ``--tune a,b`` becomes ``a+b``, since commas read awkwardly in filenames);
    - suffixed ``+‹shorthash›`` - the first 8 hex of a sha1 of the joined raw
      options string, git-style and order-sensitive - whenever raw ``--options``
      are present, so two different option sets never share a filename.

    Args:
        tune: The requested ``--tune`` value (None, a name, a comma-separated
            string, or a list of names). The *requested* value, not the
            model/backend default, so an unadorned run always reads ``defopt``.
        options: The raw ``--options`` pass-through tokens (list; may be empty).

    Returns:
        The slot string, e.g. ``defopt``, ``hmax``, ``defopt+a1b2c3d4``.
    """
    names = _normalise_tune(tune)
    base = '+'.join(names) if names else 'defopt'

    options = options or []
    if options:
        joined = ' '.join(options)
        shorthash = hashlib.sha1(joined.encode('utf-8')).hexdigest()[:8]
        return f"{base}+{shorthash}"
    return base


def compute_options_label(tune=None, options=None) -> str:
    """Build the human-readable configuration label recorded in the summary.

    The readable sibling of :func:`compute_tune_slot`: it keeps the raw option
    text (``O2``) instead of hashing it, so ``puzznic-plot --by-options`` and
    ``puzznic-summary`` can fold runs into legible per-configuration series. The
    label is the ``+``-join of the tune names and the dash-stripped options, or
    ``defopt`` when there is neither:

    - ``()`` -> ``defopt``           (the default configuration)
    - ``-O2`` -> ``O2``              (options only; unchanged from the old label)
    - ``--tune hmax`` -> ``hmax``    (tune only)
    - ``--tune hmax -O2`` -> ``hmax+O2``

    Args:
        tune: The requested ``--tune`` value (see :func:`compute_tune_slot`).
        options: The raw ``--options`` pass-through tokens (list; may be empty).

    Returns:
        The readable label, e.g. ``defopt``, ``O2``, ``hmax+O2``.
    """
    names = _normalise_tune(tune)
    readable_options = [o.lstrip('-') for o in (options or [])]
    parts = names + readable_options
    return '+'.join(parts) if parts else 'defopt'


class SolutionFileNamer:
    """Manages consistent naming conventions for solution files."""
    
    @staticmethod
    def generate_solution_filename(instance: str, solver: str, model: str,
                                  timestamp: str, tune: str = "",
                                  horizon: Optional[int] = None,
                                  suffix: str = ".solution") -> str:
        """
        Generate a standardized solution filename.

        Args:
            instance: Instance/problem name
            solver: Solver name
            tune: Optional tune slot (see compute_tune_slot); inserted between
                solver and model when non-empty, omitted for backward compat
                when empty
            model: Model name
            timestamp: Timestamp string
            horizon: Optional horizon value
            suffix: File suffix (default: ".solution")

        Returns:
            Formatted filename string
        """
        if horizon is not None:
            horizon_str = f"-{horizon:02d}"
        else:
            horizon_str = ""

        tune_str = f"-{tune}" if tune else ""

        return f"{instance}-{solver}{tune_str}-{model}-{timestamp}{horizon_str}{suffix}"
    
    @staticmethod
    def generate_solution_path(instance: str, solver: str,
                              model: str, timestamp: str, tune: str = "",
                              horizon: Optional[int] = None,
                              suffix: str = ".solution") -> Path:
        """
        Generate a solution file path (relative to current directory).

        Args:
            instance: Instance/problem name
            solver: Solver name
            model: Model name
            timestamp: Timestamp string
            tune: Optional tune slot (see compute_tune_slot)
            horizon: Optional horizon value
            suffix: File suffix

        Returns:
            Path to solution file (relative to current directory)
        """
        filename = SolutionFileNamer.generate_solution_filename(
            instance, solver, model, timestamp, tune=tune, horizon=horizon,
            suffix=suffix
        )
        return Path(filename)


class ObjectiveFileManager:
    """Manages objective value files for optimization solvers."""
    
    @staticmethod
    def generate_objective_path(instance: str, solver: str,
                               model: str, timestamp: str, horizon: int,
                               tune: str = "") -> Path:
        """
        Generate objective file path (relative to current directory).

        Args:
            instance: Instance/problem name
            solver: Solver name
            model: Model name
            timestamp: Timestamp string
            horizon: Horizon value
            tune: Optional tune slot (see compute_tune_slot)

        Returns:
            Path to objective file (relative to current directory)
        """
        horizon_str = f"{horizon:02d}"
        tune_str = f"-{tune}" if tune else ""
        filename = f"{instance}-{solver}{tune_str}-{model}-{timestamp}-{horizon_str}.obj"
        return Path(filename)
    
    @staticmethod
    def prepare_objective_file(obj_file: Path, logger: Optional[logging.Logger] = None) -> None:
        """
        Prepare objective file by removing any existing file.
        
        Args:
            obj_file: Path to objective file
            logger: Optional logger for debug messages
        """
        if obj_file.exists():
            try:
                obj_file.unlink()
                if logger:
                    logger.debug(f"Removed existing objective file: {obj_file}")
            except Exception as e:
                if logger:
                    logger.warning(f"Failed to remove existing objective file: {e}")
    
    @staticmethod
    def parse_objective_file(obj_file: Path) -> Optional[int]:
        """
        Parse objective values file created by Savile Row or similar solvers.
        
        Args:
            obj_file: Path to objective file
            
        Returns:
            Best objective value found, or None if parsing fails
        """
        try:
            if not obj_file.exists():
                return None
                
            with open(obj_file, 'r') as f:
                lines = f.readlines()
                
            if not lines:
                return None
                
            # Parse the last line which contains the best objective value found
            # Format: "objective_value time_in_seconds"
            last_line = lines[-1].strip()
            if last_line:
                parts = last_line.split()
                if parts and parts[0].lstrip('-').isdigit():
                    return int(parts[0])
                    
            return None
        except Exception:
            return None


class VisualizationFileManager:
    """Manages visualization output files."""
    
    @staticmethod
    def open_templated_file(n: int, prefix: str = "vis", encoding: str = 'utf-8'):
        """
        Open file number n out of a sequence of output files.
        
        Args:
            n: Sequence number
            prefix: File prefix (default: "vis")
            encoding: File encoding (default: utf-8)
            
        Returns:
            Opened file handle
        """
        return open(f"{prefix}-{n:03d}.txt", 'w', encoding=encoding)
