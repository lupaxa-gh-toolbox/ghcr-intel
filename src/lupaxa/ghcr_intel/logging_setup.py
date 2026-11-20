"""Logging that cannot print a GitHub token."""

from __future__ import annotations

import logging
import sys

__all__ = ["configure_logging"]


class _RedactingFormatter(logging.Formatter):
    """Format a record and remove known secrets."""

    def __init__(self, secrets: list[str]) -> None:
        super().__init__("%(levelname)s %(message)s")
        self._secrets = [secret for secret in secrets if secret]

    def format(self, record: logging.LogRecord) -> str:
        """Return the formatted line with secrets replaced."""

        text = super().format(record)
        for secret in self._secrets:
            text = text.replace(secret, "[redacted]")
        return text


def configure_logging(
    level: str,
    *,
    verbose: bool = False,
    debug: bool = False,
    secrets: list[str] | None = None,
) -> None:
    """
    Send diagnostics to stderr.

    ``--debug`` selects DEBUG. ``--verbose`` selects INFO. Otherwise ``level``
    from the configuration file is used.

    """

    chosen = "DEBUG" if debug else "INFO" if verbose else level
    logger = logging.getLogger("ghcr_intel")
    logger.handlers.clear()
    logger.setLevel(chosen.upper())
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_RedactingFormatter(secrets or []))
    logger.addHandler(handler)
    logger.propagate = False
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
