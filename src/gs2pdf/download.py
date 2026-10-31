"""Download PDF exports from anonymously accessible Google Slides links."""

import re
import sys
import time
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from . import __version__
from .errors import GS2PDFError

_SHARING_HELP = (
    "Set Share → General access → Anyone with the link → Viewer, "
    "and allow viewers to download, print, and copy. "
    "Links that require Google sign-in are unsupported."
)
_RETRY_DELAYS = [2, 4, 8]


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


def _is_transient(status: int) -> bool:
    return status == 429 or status >= 500


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
    if _is_transient(status):
        return GS2PDFError(
            f"Google PDF export is temporarily unavailable (HTTP {status}); "
            "try again later."
        )
    return GS2PDFError(f"Google PDF export failed (HTTP {status}).")


def download_presentation(url: str, *, timeout: float = 90) -> bytes:
    """Download a link-shared presentation without credentials or a browser."""
    request = Request(
        presentation_export_url(url),
        headers={
            "Accept": "application/pdf",
            "User-Agent": f"gs2pdf/{__version__}",
        },
    )
    print("Downloading Google Slides PDF…", file=sys.stderr)
    delays = iter(_RETRY_DELAYS)
    while True:
        try:
            with urlopen(request, timeout=timeout) as response:
                data = response.read()
            break
        except HTTPError as exc:
            exc.close()
            delay = next(delays, None)
            if delay is None or not _is_transient(exc.code):
                raise _http_error(exc.code) from exc
            time.sleep(delay)
        except (OSError, HTTPException) as exc:
            raise GS2PDFError(
                "PDF download failed or timed out. "
                "Check your internet connection and retry."
            ) from exc
    # Google answers restricted links with an HTML sign-in page.
    if not data.startswith(b"%PDF-"):
        raise GS2PDFError(
            "Google did not return an anonymously downloadable PDF. "
            + _SHARING_HELP
        )
    return data
