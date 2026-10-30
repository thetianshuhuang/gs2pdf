"""gs2pdf's console entry point."""

from __future__ import annotations

import argparse
import math
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from . import __version__
from .errors import GS2PDFError


def _positive(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number") from None
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return number


def parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    result = argparse.ArgumentParser(
        prog="gs2pdf",
        description=(
            "Download Google Slides diagrams as transparent, "
            "tightly cropped PDFs."
        ),
    )
    result.add_argument(
        "source", help="publicly link-shared Google Slides URL or local PDF"
    )
    result.add_argument(
        "--version", action="version", version=f"gs2pdf {__version__}"
    )
    result.add_argument(
        "--output-dir",
        type=Path,
        default=Path("figures"),
        help="output directory (default: figures)",
    )
    result.add_argument(
        "--slides",
        help="one-based slide numbers/ranges, e.g. 1,3-5 (default: all)",
    )
    result.add_argument(
        "--overwrite", action="store_true", help="replace existing output PDFs"
    )
    result.add_argument(
        "--timeout",
        type=_positive,
        default=90,
        metavar="SECONDS",
        help="timeout for each PDF download request (default: 90)",
    )
    return result


@contextmanager
def _source_pdf(
    args: argparse.Namespace, report: Callable[[str], None]
) -> Iterator[Path]:
    """Keep a downloaded PDF available for the duration of conversion."""
    if urlparse(args.source).scheme.lower() not in {"http", "https"}:
        source = Path(args.source).expanduser()
        if not source.is_file():
            raise GS2PDFError(f"Input PDF not found: {source}")
        yield source
        return

    from .download import download_presentation

    with tempfile.TemporaryDirectory(prefix="gs2pdf-") as temporary:
        source = Path(temporary) / "presentation.pdf"
        download_presentation(
            args.source,
            source,
            timeout=args.timeout,
            report=report,
        )
        yield source


def main(argv: list[str] | None = None) -> int:
    """Run the command and return its exit status."""
    args = parser().parse_args(argv)
    from .convert import convert_pdf

    def report(message: str) -> None:
        print(message, file=sys.stderr)

    try:
        with _source_pdf(args, report) as source:
            result = convert_pdf(
                source,
                args.output_dir.expanduser(),
                slides=args.slides,
                overwrite=args.overwrite,
                report=report,
            )
        report(
            f"Saved {len(result.written)} PDF(s); "
            f"{len(result.empty)} empty slide(s); "
            f"{len(result.failed)} failed slide(s)."
        )
        return 1 if result.failed else 0
    except (GS2PDFError, OSError) as exc:
        report(f"gs2pdf: {exc}")
        return 1
    except KeyboardInterrupt:
        report("gs2pdf: cancelled.")
        return 130
