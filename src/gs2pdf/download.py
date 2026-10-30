"""Download PDF exports from anonymously accessible Google Slides links."""

from __future__ import annotations

import io
import re
import time
from collections.abc import Callable
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

import pikepdf

from . import __version__
from .errors import GS2PDFError

_SHARING_HELP = (
    "Set Share → General access → Anyone with the link → Viewer, "
    "and allow viewers to download, print, and copy. "
    "Links that require Google sign-in are unsupported."
)


def presentation_export_url(url: str) -> str:
    """Build an anonymous PDF export URL, preserving any resource key."""
    parsed = urlparse(url)
    match = re.fullmatch(
        r"/presentation/(?:u/\d+/)?d/([A-Za-z0-9_-]+)(?:/[^?#]*)?",
        parsed.path,
    )
    if (
        parsed.scheme != "https"
        or parsed.netloc != "docs.google.com"
        or not match
    ):
        raise GS2PDFError(
            "Expected an https://docs.google.com/presentation/d/… "
            "Google Slides URL or a local PDF path."
        )
    identifier = match[1]
    if identifier == "e":
        raise GS2PDFError(
            "Use the original presentation's /presentation/d/… link, "
            "not a published /d/e/… link."
        )
    export_url = (
        f"https://docs.google.com/presentation/d/{identifier}/export/pdf"
    )
    key = parse_qs(parsed.query).get("resourcekey")
    if key:
        export_url += "?" + urlencode({"resourcekey": key[0]})
    return export_url


def _validate_pdf(data: bytes) -> None:
    if not data.lstrip().startswith(b"%PDF-"):
        raise GS2PDFError(
            "Google did not return an anonymously downloadable PDF. "
            + _SHARING_HELP
        )
    try:
        with pikepdf.open(io.BytesIO(data), attempt_recovery=False) as pdf:
            if not len(pdf.pages) or pdf.is_encrypted:
                raise ValueError("empty or encrypted PDF")
    except (pikepdf.PdfError, pikepdf.PasswordError, ValueError) as exc:
        raise GS2PDFError(
            "Google returned an unreadable PDF export; retry the download."
        ) from exc


def _fetch_pdf(url: str, timeout: float) -> bytes:
    request = Request(
        url,
        headers={
            "Accept": "application/pdf",
            "User-Agent": f"gs2pdf/{__version__}",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        data = response.read()
    _validate_pdf(data)
    return data


def _http_error(status: int) -> GS2PDFError:
    if status in {401, 403}:
        return GS2PDFError(
            "Google denied anonymous PDF export. " + _SHARING_HELP
        )
    if status == 404:
        return GS2PDFError(
            "Presentation not found or not publicly accessible. "
            "Check the link. " + _SHARING_HELP
        )
    if status == 429 or status >= 500:
        return GS2PDFError(
            f"Google PDF export is temporarily unavailable (HTTP {status}); "
            "try again later."
        )
    return GS2PDFError(f"Google PDF export failed (HTTP {status}).")


def download_presentation(
    url: str,
    destination: Path,
    *,
    timeout: float = 90,
    report: Callable[[str], None] = print,
) -> None:
    """Download a link-shared presentation without credentials or a browser."""
    export_url = presentation_export_url(url)
    report("Downloading Google Slides PDF…")
    for attempt in range(4):
        try:
            data = _fetch_pdf(export_url, timeout)
            break
        except HTTPError as exc:
            status = exc.code
            exc.close()
            if (status == 429 or status >= 500) and attempt < 3:
                time.sleep(2 ** (attempt + 1))
                continue
            raise _http_error(status) from exc
        except (OSError, HTTPException) as exc:
            raise GS2PDFError(
                "PDF download failed or timed out. "
                "Check your internet connection and retry."
            ) from exc
    destination.write_bytes(data)
