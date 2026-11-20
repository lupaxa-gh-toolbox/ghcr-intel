"""Read a GitHub token from the GitHub CLI."""

from __future__ import annotations

import subprocess

from lupaxa.ghcr_intel.errors import AuthError

__all__ = [
    "token_from_gh",
]

_MISSING = "The GitHub CLI token is missing."


def token_from_gh() -> str:
    """
    Return the token from ``gh auth token``.

    A missing ``gh``, a non-zero exit, or an empty token raises ``AuthError``.
    The token is not logged.

    """

    try:
        completed = subprocess.run(
            ["gh", "auth", "token"],  # noqa: S607
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise AuthError(_MISSING) from exc
    if completed.returncode != 0:
        raise AuthError(_MISSING)
    token = completed.stdout.strip()
    if token == "":
        raise AuthError(_MISSING)
    return token
