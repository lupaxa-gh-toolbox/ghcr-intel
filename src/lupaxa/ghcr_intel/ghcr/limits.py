"""How long a 429 or an exhausted quota asked us to wait."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import time

__all__ = ["limit_delay"]


def limit_delay(status: int, headers: Mapping[str, str]) -> float | None:
    """
    Return the pause a response requested.

    ``Retry-After`` on a 429 wins. Otherwise a remaining quota of zero uses
    ``X-RateLimit-Reset`` or ``RateLimit-Reset``. ``None`` means the response
    did not name a wait.

    """

    folded = {str(key).lower(): str(value) for key, value in headers.items()}
    if status == 429 and "retry-after" in folded:
        return _retry_after(folded.get("retry-after"))
    remaining = _number(folded.get("x-ratelimit-remaining") or folded.get("ratelimit-remaining"))
    reset = _number(folded.get("x-ratelimit-reset") or folded.get("ratelimit-reset"))
    if remaining == 0 and reset is not None:
        return _reset_delay(reset)
    return None


def _retry_after(raw: str | None) -> float | None:
    if raw is None or raw.strip() == "":
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        moment = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0.0, (moment - datetime.now(UTC)).total_seconds())


def _reset_delay(reset: float) -> float:
    if reset > 1_000_000_000:
        return max(0.0, reset - time.time())
    return max(0.0, reset)


def _number(raw: str | None) -> float | None:
    if raw is None or raw.strip() == "":
        return None
    token = raw.split(";", 1)[0].strip()
    try:
        return float(token)
    except ValueError:
        return None
