"""
Global state for interrupt handling and runtime configuration.
"""

import logging
import signal

class GlobalState:
    """Global state for tracking interrupts and runtime configuration."""
    
    def __init__(self):
        self.interrupt_count = 0
        self.debug = False
        self.verbose = False
        self.srjava = False
        # Free-form options passed through with --options to the active solver
        # toolchain (kissat/minion Savile Row, fd/symk, enhsp). The runner does
        # NOT interpret these: interpreted presets belong on --tune instead. A
        # list, since --options is repeatable.
        self.options = []
        # Tune preset for the active backend, selected with --tune and resolved
        # against the backend's presets in the registry (fd: blind/hmax/ff/lmcut;
        # symk: fw/bw/bd). None means the backend's default.
        self.tune = None
        # Model registry (lazy loaded)
        self._model_registry = None
    
    @property
    def interrupted(self) -> bool:
        return self.interrupt_count > 0
    
    @property
    def should_force_termination(self) -> bool:
        return self.interrupt_count >= 2
    
    def on_interrupt(self, signum: int = None) -> None:
        """Handle interrupt signals. SIGTERM forces immediate termination."""
        if signum == signal.SIGTERM:
            self.interrupt_count = 2  # Force immediate termination
        else:
            self.interrupt_count += 1
    
    def log_level(self) -> int:
        """Return appropriate logging level based on debug/verbose flags."""
        if self.debug:
            return logging.DEBUG
        elif self.verbose:
            return logging.INFO
        else:
            return logging.WARNING
    
    @property
    def model_registry(self):
        """Get the model registry, creating it if needed (lazy loading to avoid circular imports)."""
        if self._model_registry is None:
            from .model_registry import ModelRegistry
            self._model_registry = ModelRegistry()
        return self._model_registry


# Global instance
global_state = GlobalState()