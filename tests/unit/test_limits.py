"""Rate-limit pauses named by a response."""

from __future__ import annotations

import time

from lupaxa.ghcr_intel.ghcr.limits import limit_delay


def test_retry_after_seconds_on_429() -> None:
    assert limit_delay(429, {"Retry-After": "2"}) == 2.0


def test_retry_after_is_ignored_unless_the_status_is_429() -> None:
    assert limit_delay(500, {"Retry-After": "9"}) is None


def test_a_429_without_a_wait_does_not_pause() -> None:
    assert limit_delay(429, {}) is None


def test_remaining_zero_uses_a_reset_delta() -> None:
    delay = limit_delay(200, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "3"})
    assert delay == 3.0


def test_remaining_zero_uses_an_epoch_reset() -> None:
    reset = time.time() + 30
    delay = limit_delay(200, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)})
    assert delay is not None
    assert 25 <= delay <= 31
