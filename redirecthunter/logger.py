"""Centralized Rich-based logging for RedirectHunter.

Every module obtains its logger via :func:`get_logger` rather than calling
``logging.getLogger`` directly, and the CLI is the *only* place that calls
:func:`configure_logging`. This keeps log formatting, verbosity, and output
routing (console vs. file) controlled from a single entry point instead of
being scattered — and duplicated — across modules.

A single shared :class:`rich.console.Console` instance (:data:`console`) is
exported for reuse by ``cli.py``'s progress bars and summary tables, so log
lines and live progress output render through the same terminal renderer
without interleaving artifacts.
"""

from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

#: Shared console instance. Reused by Rich ``Progress`` displays and summary
#: tables so all terminal output is coordinated through one renderer.
console = Console()

_LOGGER_NAME = "redirecthunter"
_LOG_FORMAT = "%(message)s"
_FILE_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

_configured = False


class LogLevel(str, Enum):
    """Supported log verbosity levels, exposed to the CLI as a choice enum."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    ERROR = "ERROR"


def configure_logging(
    level: LogLevel | str = LogLevel.INFO,
    log_file: Path | None = None,
    *,
    quiet: bool = False,
) -> None:
    """Configure the ``redirecthunter`` logger tree.

    Safe to call multiple times — later calls fully reconfigure handlers
    rather than stacking duplicate ones, which matters in tests and in
    interactive sessions that re-invoke CLI commands.

    Args:
        level: Minimum severity to emit. Accepts a :class:`LogLevel` or a
            plain string (``"DEBUG"``, ``"INFO"``, ``"ERROR"``).
        log_file: If provided, a plain-text (non-Rich-formatted) log file
            is written in addition to console output — useful for long
            unattended scans where the console history is not persisted.
        quiet: If True, suppress console output entirely (file logging,
            if configured, still occurs). Used by ``--quiet`` on the CLI.
    """
    global _configured

    resolved_level = LogLevel(level) if isinstance(level, str) else level
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(resolved_level.value)

    # Clear any handlers from a previous configure_logging() call to avoid
    # duplicate log lines on repeated invocation.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    if not quiet:
        rich_handler = RichHandler(
            console=console,
            show_time=True,
            show_path=resolved_level == LogLevel.DEBUG,
            rich_tracebacks=True,
            tracebacks_suppress=[],
            markup=False,
        )
        rich_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        logger.addHandler(rich_handler)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(_FILE_LOG_FORMAT))
        logger.addHandler(file_handler)

    if not logger.handlers:
        # quiet=True with no log_file — attach a NullHandler so calls to
        # logger.info() etc. don't trigger Python's "no handlers found"
        # stderr warning.
        logger.addHandler(logging.NullHandler())

    logger.propagate = False
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a module-scoped child logger under the ``redirecthunter`` tree.

    If :func:`configure_logging` has not yet been called (e.g. the package
    is being used as a library rather than via the CLI), a sane default
    (INFO level, console output) is applied automatically so log calls
    never silently vanish or raise.

    Args:
        name: Typically ``__name__`` of the calling module.

    Returns:
        A logger named ``redirecthunter.<name>``.
    """
    if not _configured:
        configure_logging(LogLevel.INFO)

    if name == _LOGGER_NAME or name.startswith(f"{_LOGGER_NAME}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_LOGGER_NAME}.{name}")


__all__ = ["LogLevel", "console", "configure_logging", "get_logger"]
