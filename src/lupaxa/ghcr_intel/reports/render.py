"""Plain table, JSON, CSV, and Markdown reports."""

from __future__ import annotations

from collections.abc import Sequence
import csv
import io
import json
from typing import Literal

from rich.console import Console
from rich.table import Table

from lupaxa.ghcr_intel.errors import UsageError
from lupaxa.ghcr_intel.layout import RIGHT_ALIGNED
from lupaxa.ghcr_intel.views import ImageView, field_text

__all__ = [
    "audit",
    "failing",
    "render_report",
]

_REPORT_FORMATS = frozenset({"csv", "json", "markdown", "table"})
_AUDIT_FORMATS = frozenset({"json", "table"})
_FAILED_STATUS = frozenset({"Error", "Forbidden", "Stale", "Unavailable"})


def render_report(views: Sequence[ImageView], output_format: str, *, date_format: str, fields: tuple[str, ...]) -> str:
    """
    Render list cells as a table, JSON, CSV, or Markdown.

    ``field_text`` already applied ``date_format``. Markdown, JSON, and CSV
    use that same text. The table is not coloured.

    """

    del date_format
    _require(output_format, _REPORT_FORMATS)
    rows = _rows(views, fields)
    if output_format == "json":
        return json.dumps(rows, indent=2) + "\n"
    if output_format == "csv":
        return _csv(rows, fields)
    if output_format == "markdown":
        return _markdown(rows, fields)
    return _table(rows, fields)


def audit(views: Sequence[ImageView], output_format: str, *, date_format: str, fields: tuple[str, ...]) -> str:
    """Render failing images. Only a table or JSON is accepted."""

    _require(output_format, _AUDIT_FORMATS)
    return render_report(failing(views), output_format, date_format=date_format, fields=fields)


def failing(views: Sequence[ImageView]) -> list[ImageView]:
    """
    Return images with a failing check.

    Status Stale, Error, Unavailable, or Forbidden is included. A non-empty
    architectures error is included, including when Status is Current.
    """

    return [view for view in views if view.status in _FAILED_STATUS or view.architectures_error != ""]


def _require(output_format: str, allowed: frozenset[str]) -> None:
    if output_format in allowed:
        return
    choices = ", ".join(sorted(allowed))
    raise UsageError(f"Unknown format {output_format}. Expected {choices}.")


def _rows(views: Sequence[ImageView], fields: tuple[str, ...]) -> list[dict[str, str]]:
    return [{field: field_text(view, field, detail=False) for field in fields} for view in views]


def _csv(rows: Sequence[dict[str, str]], fields: tuple[str, ...]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(fields), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row[field] for field in fields})
    return buffer.getvalue()


def _markdown(rows: Sequence[dict[str, str]], fields: tuple[str, ...]) -> str:
    heading = "| " + " | ".join(_markdown_cell(field) for field in fields) + " |"
    separator = "| " + " | ".join("---" for _field in fields) + " |"
    lines = ["# GHCR Intel Report", "", heading, separator]
    for row in rows:
        cells = (_markdown_cell(row[field]) for field in fields)
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def _table(rows: Sequence[dict[str, str]], fields: tuple[str, ...]) -> str:
    table = Table()
    for field in fields:
        table.add_column(field, justify=_justify(field))
    for row in rows:
        table.add_row(*(row[field] for field in fields))
    buffer = io.StringIO()
    console = Console(file=buffer, markup=False, highlight=False, soft_wrap=True, width=200, force_terminal=False, color_system=None)
    console.print(table)
    return buffer.getvalue()


def _justify(field: str) -> Literal["left", "right"]:
    if field in RIGHT_ALIGNED:
        return "right"
    return "left"
