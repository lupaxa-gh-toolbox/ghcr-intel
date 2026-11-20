"""Error types and their process exit codes."""

from __future__ import annotations


class IntelError(Exception):
    """Base error. Subclasses set ``exit_code``."""

    exit_code = 1


class ConfigError(IntelError):
    """Invalid configuration or usage."""

    exit_code = 2


class AuthError(IntelError):
    """The GitHub CLI token is missing."""

    exit_code = 3


class UsageError(IntelError):
    """The command was used incorrectly."""

    exit_code = 2


class NotConfigured(IntelError):
    """The named package is not in the configuration file."""

    exit_code = 5
