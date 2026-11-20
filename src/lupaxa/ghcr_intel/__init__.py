"""GHCR Intel monitors ghcr.io container images from a local cache."""

from __future__ import annotations

from lupaxa.ghcr_intel.cli import main

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "main",
]
