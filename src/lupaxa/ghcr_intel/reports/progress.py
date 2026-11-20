"""Synchronisation progress. Machine-readable output never uses this module."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import sys
from typing import IO

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from lupaxa.ghcr_intel.sync.engine import SyncReport

__all__ = ["print_sync", "status_line", "syncing"]


@contextmanager
def syncing() -> Iterator[Callable[[SyncReport], None]]:
    """
    Show synchronisation progress on stderr.

    A terminal gets a status bar. Any other stderr gets one line each time an
    image finishes or the rate-limit wait changes.

    Yields:
        Callback for :func:`synchronise`. It must not await.

    """

    console = Console(stderr=True)
    if not console.is_terminal:
        seen = (-1, False)

        def emit(report: SyncReport) -> None:
            nonlocal seen
            current = (report.successful + report.failed, report.rate_wait > 0)
            if current == seen:
                return
            seen = current
            console.print(status_line(report), markup=False)

        yield emit
        return

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )
    with progress:
        task = progress.add_task("Synchronising", total=None)

        def update(report: SyncReport) -> None:
            done = report.successful + report.failed
            progress.update(
                task,
                completed=done,
                total=report.images or None,
                description=status_line(report),
            )

        yield update


def print_sync(report: SyncReport, stream: IO[str] | None = None) -> None:
    """Print the completed run in the same shape as the other Intel tools."""

    title = (
        "Synchronisation did not finish"
        if report.incomplete
        else "Synchronisation completed"
    )
    lines = [
        title,
        "",
        f"Images: {report.images}",
        f"Successful: {report.successful}",
        f"Failed: {report.failed}",
        f"Skipped: {report.skipped}",
        "",
        f"API requests: {report.requests}",
        f"Cache hits: {report.cache_hits}",
        "",
        f"Elapsed: {report.elapsed:.1f}s",
    ]
    if report.rate_pauses:
        lines.extend(["", f"Rate-limit pauses: {report.rate_pauses}"])
    console = Console(file=stream or sys.stdout, markup=False, highlight=False)
    console.print("\n".join(lines))


def status_line(report: SyncReport) -> str:
    """One line of image, request, and cache progress."""

    done = report.successful + report.failed
    name = report.latest or "Synchronising"
    line = (
        f"{name}  {done}/{report.images}"
        f"  requests {report.requests}"
        f"  cache {report.cache_hits}"
        f"  failed {report.failed}"
    )
    if report.rate_wait > 0:
        return (
            f"{line}  waiting for GitHub rate limit, "
            f"requests resume in {_wait_label(report.rate_wait)}"
        )
    if report.rate_pauses:
        return f"{line}  rate limit pauses {report.rate_pauses}"
    return line


def _wait_label(seconds: float) -> str:
    remaining = max(0, int(seconds + 0.5))
    minutes, secs = divmod(remaining, 60)
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"
