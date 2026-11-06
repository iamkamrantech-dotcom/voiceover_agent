"""Logging with two audiences.

  log.user("Detecting scenes...")    -> console, plain English, sentence case
  log.debug("histogram weight=0.6")  -> file only, technical detail

Every run writes its own file: outputs/logs/run_YYYYmmdd_HHMMSS.log
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

_USER = 25  # between INFO (20) and WARNING (30)
logging.addLevelName(_USER, "USER")


class _Logger(logging.Logger):
    def user(self, msg: str, *args, **kwargs) -> None:
        """A line meant for the person running the agent. Plain English."""
        if self.isEnabledFor(_USER):
            self._log(_USER, msg, args, **kwargs)


logging.setLoggerClass(_Logger)


class _ConsoleFormat(logging.Formatter):
    """Plain prefix for user lines, louder prefix for problems."""

    def format(self, record: logging.LogRecord) -> str:
        if record.levelno == _USER:
            return f"[agent] {record.getMessage()}"
        if record.levelno >= logging.ERROR:
            return f"[agent] Error: {record.getMessage()}"
        if record.levelno == logging.WARNING:
            return f"[agent] Warning: {record.getMessage()}"
        return f"[debug] {record.getMessage()}"


_run_file: Path | None = None


def setup(log_dir: Path | str = "outputs/logs",
          level: str = "INFO",
          name: str = "agent",
          run_name: str | None = None) -> _Logger:
    """Create the per-run log file and wire up console + file handlers."""
    global _run_file

    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_{run_name}" if run_name else ""
    _run_file = directory / f"run_{stamp}{suffix}.log"

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    console = logging.StreamHandler(sys.stdout)
    # User lines always show; debug chatter follows LOG_LEVEL.
    console.setLevel(min(getattr(logging, level.upper(), logging.INFO), _USER))
    console.setFormatter(_ConsoleFormat())
    logger.addHandler(console)

    to_file = logging.FileHandler(_run_file, encoding="utf-8")
    to_file.setLevel(logging.DEBUG)
    to_file.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    logger.addHandler(to_file)

    logger.debug("Log file: %s", _run_file)
    return logger  # type: ignore[return-value]


def get(name: str = "agent") -> _Logger:
    """Fetch an already-configured logger (or a bare one if setup never ran)."""
    return logging.getLogger(name)  # type: ignore[return-value]


def run_file() -> Path | None:
    return _run_file
