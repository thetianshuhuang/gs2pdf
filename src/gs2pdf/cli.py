"""gs2pdf's console entry point."""

import io
import math
import sys
from pathlib import Path
from typing import Annotated

import tyro

from . import __version__
from .convert import convert_pdf
from .download import download_presentation
from .errors import GS2PDFError


def gs2pdf(
    source: str,
    /,
    *,
    output_dir: Path = Path("figures"),
    slides: Annotated[str | None, tyro.conf.arg(metavar="RANGES")] = None,
    overwrite: bool = False,
    timeout: float = 90,
) -> int:
    """Download Google Slides diagrams as transparent, tightly cropped PDFs.

    Args:
        source: Publicly link-shared Google Slides URL or local PDF.
        output_dir: Output directory.
        slides: One-based slide numbers/ranges, e.g. 1,3-5; all if omitted.
        overwrite: Replace existing output PDFs.
        timeout: Timeout in seconds for each PDF download request.

    """
    if not math.isfinite(timeout) or timeout <= 0:
        print("gs2pdf: --timeout must be finite and positive.", file=sys.stderr)
        return 2
    try:
        if source.startswith(("http://", "https://")):
            pdf = io.BytesIO(download_presentation(source, timeout=timeout))
        else:
            pdf = Path(source)
        result = convert_pdf(
            pdf, output_dir, slides=slides, overwrite=overwrite
        )
    except (GS2PDFError, OSError) as exc:
        print(f"gs2pdf: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("gs2pdf: cancelled.", file=sys.stderr)
        return 130
    print(
        f"Saved {len(result.written)} PDF(s); "
        f"{len(result.empty)} empty slide(s); "
        f"{len(result.failed)} failed slide(s).",
        file=sys.stderr,
    )
    return 1 if result.failed else 0


def main(argv: list[str] | None = None) -> int:
    """Run the command and return its exit status."""
    args = sys.argv[1:] if argv is None else argv
    # tyro has no built-in version flag.
    if args == ["--version"]:
        print(f"gs2pdf {__version__}")
        return 0
    return tyro.cli(
        gs2pdf,
        prog="gs2pdf",
        args=args,
        config=(
            tyro.conf.FlagCreatePairsOff,
            tyro.conf.PositionalMetavarFromFieldName,
        ),
    )
