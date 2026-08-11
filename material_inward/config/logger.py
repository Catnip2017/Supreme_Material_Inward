"""
logger.py — Centralized logging configuration.
All modules import their logger from here.
Logs go to both console and rotating log files.
"""

import logging
import os
import threading
from logging.handlers import RotatingFileHandler

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# FIX: get_logger() used to build a fresh RotatingFileHandler (and error/
# console handler) on every call, keyed only by logger *name*. Since each
# module calls get_logger(__name__) with its own module name, every one
# of the ~30 modules in this codebase got its own independent file handle
# open on the SAME logs/application.log (and errors.log). That's harmless
# until any one handler hits the 5MB maxBytes and tries to rotate: its
# doRollover() calls os.rename() on a file the other ~29 handles still
# have open, and Windows refuses to rename a file that's open elsewhere --
# raising PermissionError: WinError 32 (seen as "--- Logging error ---" in
# the console). Rotation then silently never succeeds and the log grows
# unbounded, throwing the same error again next time.
#
# Fix: build each handler exactly once (module-level singletons, guarded
# by a lock since get_logger() can be called from multiple threads during
# startup) and attach those same three handler objects to every named
# logger. One open file handle per log file, no matter how many modules
# call get_logger().
_handler_lock = threading.Lock()
_console_handler = None
_file_handler = None
_error_handler = None


def _get_shared_handlers():
    global _console_handler, _file_handler, _error_handler

    if _file_handler is not None:
        return _console_handler, _file_handler, _error_handler

    with _handler_lock:
        if _file_handler is not None:
            return _console_handler, _file_handler, _error_handler

        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        # Console handler — INFO and above
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)

        # File handler — DEBUG and above, rotates at 5MB, keeps 5 backups
        log_file = os.path.join(LOG_DIR, "application.log")
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)

        # Error-only file handler — separate file for critical issues
        error_file = os.path.join(LOG_DIR, "errors.log")
        error_handler = RotatingFileHandler(
            error_file,
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8"
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)

        _console_handler = console_handler
        _file_handler = file_handler
        _error_handler = error_handler

    return _console_handler, _file_handler, _error_handler


def get_logger(name: str) -> logging.Logger:
    """
    Returns a named logger. Each module still gets its own logger (so
    %(name)s in log lines still shows which module logged what), but all
    loggers share the same three handler instances -- see
    _get_shared_handlers() -- so there's only one open file handle on
    application.log and one on errors.log regardless of how many modules
    call get_logger().
    Call this at the top of every module:
        logger = get_logger(__name__)
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    console_handler, file_handler, error_handler = _get_shared_handlers()

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.addHandler(error_handler)

    return logger
