'''
utility functions
'''

import sys
import logging
from datetime import datetime
from typing import Optional
from pathlib import Path

# Logger name that warn() routes through. Defaults to the package logger so the
# core is usable standalone; a consumer that configures its own named logger (with
# file/console handlers) can point warn() at it via set_warn_logger_name() so these
# warnings share the consumer's handlers. Read at call time, so wiring may happen
# any time before the first warn().
_warn_logger_name = 'solver_support'


def set_warn_logger_name(name: str) -> None:
    """Route warn() through the logger called ``name``.

    Consumers call this once (e.g. at import) to funnel core warnings into their
    own configured logger, preserving pre-extraction behaviour where warnings
    reached the runner's log file/console handlers.
    """
    global _warn_logger_name
    _warn_logger_name = name


def warn(s, verbose_only: bool = False):
    """
    Print a warning message to stderr.

    Args:
        s: Warning message
        verbose_only: If True, only print when verbose mode is enabled (but still logged to file)
    """
    # Get the logger for proper structured logging (name is consumer-configurable)
    logger = logging.getLogger(_warn_logger_name)

    if verbose_only:
        # Log at INFO level: will appear in log file but not console in non-verbose mode
        # (console handler is set to WARNING level in non-verbose mode)
        logger.info(s)
    else:
        # Regular warning: always print to stderr
        logger.warning(s)
        print('Warning: %s, continuing' % s, file=sys.stderr)

def arrayify(d):
    '''
    Make an array from a dict with contiguous string-numeric keys.
    '''
    return [ d[i] for i in sorted(d.keys(), key=int) ]

def indices(d):
    '''
    Return properly ordered array indices for pseudo-array dict.
    '''
    return [ i for i in sorted(d.keys(), key=int) ]

def format_timestamp() -> str:
    '''
    Generate timestamp format YYYYMMDDHHMMSS for use in filenames.
    '''
    return datetime.now().strftime("%Y%m%d%H%M%S")


class BrokenPipeHandler(logging.StreamHandler):
    """
    Custom logging handler that gracefully handles broken pipe errors.
    
    This is needed when piping output to commands like 'head' that may
    close the pipe early, causing BrokenPipeError exceptions.
    """
    def emit(self, record):
        try:
            super().emit(record)
        except BrokenPipeError:
            # Ignore broken pipe errors (e.g., when piping to head)
            pass
        except Exception:
            # Silently ignore other exceptions to prevent recursion
            pass


def setup_logger(name: str, log_file: Optional[Path] = None, log_level: int = logging.INFO) -> logging.Logger:
    """
    Set up a logger with consistent formatting and broken pipe handling.
    
    Args:
        name: Logger name
        log_file: Optional file path for logging (if None, only console logging)
        log_level: Console logging level (file always captures everything)
        
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)  # Logger itself should allow all levels
    
    # Clear any existing handlers
    logger.handlers.clear()
    
    # Shared formatter for both console and file output
    log_format = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y%m%d %H:%M:%S')
    
    # Console handler with broken pipe handling - respects user verbosity
    console_handler = BrokenPipeHandler(sys.stdout)
    console_handler.setLevel(log_level)  # Console respects user's verbosity setting
    console_handler.setFormatter(log_format)
    logger.addHandler(console_handler)
    
    # File handler if log file specified
    if log_file:
        file_handler = logging.FileHandler(log_file, mode='a')
        file_handler.setLevel(logging.DEBUG)  # File should capture everything
        file_handler.setFormatter(log_format)
        logger.addHandler(file_handler)
    
    return logger

def relativize_path(path: str, output_dir: Path) -> str:
    """
    Convert absolute paths to relative paths based on output directory.
    
    Args:
        path: File path (absolute or relative)
        output_dir: Output directory for relativization
        
    Returns:
        Path relative to output directory, or original path if conversion fails
    """
    try:
        path_obj = Path(path)
        if path_obj.is_absolute():
            # Convert to relative path from output directory
            relative_path = path_obj.relative_to(output_dir)
            return str(relative_path)
        else:
            # Already relative, return as-is
            return path
    except (ValueError, OSError):
        # If relative_to fails (path not under output_dir), return original
        return path

