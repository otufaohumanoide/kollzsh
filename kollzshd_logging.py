"""Centralized logging configuration for the kollzsh daemon."""

import logging

LOG_FILE: str = "/tmp/kollzsh_debug.log"

_configured: bool = False


def setup_logging(log_file: str = LOG_FILE) -> None:
    """Initialize logging once.

    Args:
        log_file: Path to the log file.
    """
    global _configured
    if _configured:
        return
    logging.basicConfig(
        filename=log_file,
        level=logging.DEBUG,
        format="[%(asctime)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _configured = True


def log_debug(message: str, data: str | None = None) -> None:
    """Log a debug message to the daemon log.

    Args:
        message: Main message.
        data: Optional additional data (payload, output, etc).
    """
    if data is not None:
        logging.debug(
            f"{message}\nData: {data}\n"
            "----------------------------------------",
        )
    else:
        logging.debug(message)
