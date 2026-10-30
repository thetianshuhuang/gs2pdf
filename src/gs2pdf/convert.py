"""Split and crop PDFs, preserving the original page's vector content."""

from __future__ import annotations

import io
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pikepdf
import pymupdf
from PIL import Image

from .background import remove_background
from .errors import GS2PDFError


@dataclass
class ConversionResult:
    """Record written files, skipped slides, and per-slide failures."""

    written: list[Path] = field(default_factory=list)
    empty: list[int] = field(default_factory=list)
    failed: dict[int, str] = field(default_factory=dict)


def select_slides(selection: str | None, page_count: int) -> list[int]:
    """Validate a slide selection and return sorted, one-based numbers."""
    if selection is None:
        return list(range(1, page_count + 1))
    numbers: set[int] = set()
    try:
        for part in selection.split(","):
            ends = part.strip().split("-")
            if not 1 <= len(ends) <= 2 or not all(
                e.strip().isascii() and e.strip().isdigit() for e in ends
            ):
                raise ValueError
            start = int(ends[0])
            end = int(ends[-1])
            if not 1 <= start <= end <= page_count:
                raise ValueError
            numbers.update(range(start, end + 1))
    except ValueError:
        raise GS2PDFError(
            f"Invalid slide selection {selection!r}; "
            f"use numbers/ranges within 1–{page_count}, e.g. 1,3-5."
        ) from None
    return sorted(numbers)


def _content_bounds(page: pymupdf.Page) -> pymupdf.Rect | None:
    """Measure visible artwork without flattening the eventual output."""
    scale = 300 / 72
    if page.rect.width * page.rect.height * scale**2 > 100_000_000:
        raise GS2PDFError(
            "Slide is too large to measure at 300 DPI (limit: 100 megapixels)."
        )
    pix = page.get_pixmap(dpi=300, alpha=True, annots=False)
    image = Image.frombytes("RGBA", (pix.width, pix.height), pix.samples)
    bounds = image.getchannel("A").getbbox()
    if bounds is None:
        return None
    left, top, right, bottom = bounds
    # Use the detected bounds directly, with no added padding.
    clip = (
        pymupdf.Rect(
            left / scale,
            top / scale,
            right / scale,
            bottom / scale,
        )
        & page.rect
    )
    # PDF object bounds remove raster rounding beyond the actual geometry.
    object_bounds = pymupdf.Rect()
    for kind, bounds in page.get_bboxlog():
        if kind != "ignore-text":
            object_bounds |= pymupdf.Rect(bounds)
    return clip & object_bounds if not object_bounds.is_empty else clip


def _cropped_pdf(data: bytes) -> bytes | None:
    with pymupdf.open(stream=data, filetype="pdf") as document:
        page = document[0]
        # Rendering and clipping must use the same coordinate system.
        page.remove_rotation()
        clip = _content_bounds(page)
        if clip is None:
            return None
        with pymupdf.open() as output:
            new_page = output.new_page(width=clip.width, height=clip.height)
            # Import as a PDF Form, retaining vectors, images, and font data.
            new_page.show_pdf_page(
                new_page.rect, document, 0, clip=clip, keep_proportion=False
            )
            return output.tobytes(garbage=4, deflate=True)


def _convert_slide(page: pikepdf.Page) -> bytes | None:
    with pikepdf.Pdf.new() as single:
        single.pages.append(page)
        remove_background(single, single.pages[0])
        buffer = io.BytesIO()
        single.save(buffer)
    return _cropped_pdf(buffer.getvalue())


def _write_output(path: Path, data: bytes, overwrite: bool) -> None:
    if not overwrite:
        # Exclusive creation also protects against another process racing us.
        with path.open("xb") as handle:
            try:
                handle.write(data)
            except BaseException:
                path.unlink(missing_ok=True)
                raise
        return
    # Replacing an existing PDF is atomic; a failed write keeps the old file.
    fd, name = tempfile.mkstemp(
        prefix=".gs2pdf-", suffix=".pdf", dir=path.parent
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def convert_pdf(
    source: Path,
    output_dir: Path,
    *,
    slides: str | None = None,
    overwrite: bool = False,
    report: Callable[[str], None] = print,
) -> ConversionResult:
    """Remove backgrounds and export selected slides as cropped PDFs."""
    try:
        source_pdf = pikepdf.Pdf.open(source)
    except pikepdf.PasswordError as exc:
        raise GS2PDFError(
            "Encrypted PDFs are not supported; export an unencrypted PDF first."
        ) from exc
    except (pikepdf.PdfError, OSError) as exc:
        raise GS2PDFError(f"Cannot read PDF {source}: {exc}") from exc
    with source_pdf:
        if source_pdf.is_encrypted:
            raise GS2PDFError(
                "Encrypted PDFs are not supported; "
                "export an unencrypted PDF first."
            )
        if not len(source_pdf.pages):
            raise GS2PDFError("The PDF contains no slides.")
        selected = select_slides(slides, len(source_pdf.pages))
        output_dir.mkdir(parents=True, exist_ok=True)
        result = ConversionResult()
        for number in selected:
            path = output_dir / f"slide-{number:03d}.pdf"
            try:
                if path.exists() and not overwrite:
                    raise GS2PDFError(
                        f"{path} already exists; use --overwrite to replace it."
                    )
                if path.resolve() == source.resolve():
                    raise GS2PDFError(
                        "Output would replace the input PDF; "
                        "choose a different output directory."
                    )
                data = _convert_slide(source_pdf.pages[number - 1])
                if data is None:
                    result.empty.append(number)
                    report(f"Slide {number}: empty; skipped.")
                    continue
                _write_output(path, data, overwrite)
                result.written.append(path)
                report(f"Slide {number}: {path}")
            except (
                GS2PDFError,
                pikepdf.PdfError,
                pymupdf.FileDataError,
                OSError,
                ValueError,
                RuntimeError,
            ) as exc:
                result.failed[number] = str(exc)
                report(f"Slide {number}: failed: {exc}")
        return result
