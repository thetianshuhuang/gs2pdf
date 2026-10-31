"""Anonymous Google Slides URL handling and HTTP download tests."""

import io
from email.message import Message
from http.client import IncompleteRead
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from gs2pdf import download
from gs2pdf.errors import GS2PDFError


@pytest.mark.parametrize(
    "source,suffix",
    [
        (
            "https://docs.google.com/presentation/d/abc_123-xyz/edit#slide=id.p",
            "",
        ),
        (
            "https://docs.google.com/presentation/d/abc_123-xyz/edit?resourcekey=key",
            "?resourcekey=key",
        ),
        (
            "https://docs.google.com/presentation/u/1/d/abc_123-xyz/edit",
            "",
        ),
        (
            "https://docs.google.com/presentation/d/abc_123-xyz/edit?authuser=2",
            "",
        ),
        (
            "https://docs.google.com/presentation/d/abc_123-xyz/edit?authuser=1&resourcekey=a%2Bb",
            "?resourcekey=a%2Bb",
        ),
    ],
)
def test_export_url(source, suffix):
    """Keep resource keys while discarding Google account selection."""
    assert download.presentation_export_url(source) == (
        "https://docs.google.com/presentation/d/abc_123-xyz/export/pdf" + suffix
    )


@pytest.mark.parametrize(
    "source",
    [
        "http://docs.google.com/presentation/d/id/edit",
        "https://docs.google.com.evil.example/presentation/d/id/edit",
        "https://docs.google.com@evil.example/presentation/d/id/edit",
        "https://docs.google.com/document/d/id/edit",
        "https://docs.google.com/presentation/d/e/published/pub",
        "https://docs.google.com/presentation/d/",
        "https://drive.google.com/file/d/id/view",
    ],
)
def test_invalid_urls(source):
    """Reject unsupported links and untrusted Google lookalike hosts."""
    with pytest.raises(GS2PDFError):
        download.presentation_export_url(source)


@pytest.fixture
def http(monkeypatch):
    """Record requests and serve responses without network access."""
    state = SimpleNamespace(responses=[], calls=[], sleeps=[])

    def open_url(request, timeout):
        state.calls.append((request, timeout))
        response = state.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    monkeypatch.setattr(download, "urlopen", open_url)
    monkeypatch.setattr(download.time, "sleep", state.sleeps.append)
    return state


PDF = b"%PDF-1.7\n"
URL = "https://docs.google.com/presentation/d/id/edit"


def http_error(status, body=None):
    """Build an HTTP error response with a closable body."""
    return HTTPError(URL, status, "", Message(), body or io.BytesIO())


def test_download_pdf_without_credentials(http):
    """Fetch a PDF using an anonymous request and close the response."""
    response = io.BytesIO(PDF)
    http.responses = [response]
    data = download.download_presentation(
        "https://docs.google.com/presentation/u/1/d/id/edit?authuser=1",
        timeout=15,
    )
    assert data == PDF
    assert response.closed
    assert len(http.calls) == 1
    request, timeout = http.calls[0]
    assert timeout == 15
    assert request.full_url.endswith("/presentation/d/id/export/pdf")
    assert request.get_header("Authorization") is None
    assert request.get_header("Cookie") is None
    assert request.get_header("Accept") == "application/pdf"


@pytest.mark.parametrize(
    "data",
    [b"<html><title>Sign in</title></html>", b"Request access", b""],
)
def test_non_pdf_response_explains_link_sharing(http, data):
    """Reject sign-in prompts and other non-PDF responses immediately."""
    response = io.BytesIO(data)
    http.responses = [response]
    with pytest.raises(GS2PDFError, match="Anyone with the link"):
        download.download_presentation(URL)
    assert response.closed
    assert not http.sleeps


@pytest.mark.parametrize(
    "status,message",
    [
        (401, "Anyone with the link"),
        (403, "Anyone with the link"),
        (404, "Check the link"),
        (400, "HTTP 400"),
    ],
)
def test_http_failure_is_actionable(http, status, message):
    """Explain missing links, permissions, and disabled downloads."""
    body = io.BytesIO(b"Google error")
    http.responses = [http_error(status, body)]
    with pytest.raises(GS2PDFError, match=message):
        download.download_presentation(URL)
    assert body.closed
    assert len(http.calls) == 1


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_retries_are_bounded(http, status):
    """Retry rate limits and server errors a bounded number of times."""
    bodies = [io.BytesIO() for _ in range(4)]
    http.responses = [http_error(status, body) for body in bodies]
    with pytest.raises(GS2PDFError, match=f"HTTP {status}"):
        download.download_presentation(URL)
    assert len(http.calls) == 4
    assert http.sleeps == [2, 4, 8]
    assert all(body.closed for body in bodies)


def test_retry_can_succeed(http):
    """Return the PDF when an export succeeds after a transient failure."""
    http.responses = [
        http_error(503),
        io.BytesIO(PDF),
    ]
    assert download.download_presentation(URL) == PDF
    assert http.sleeps == [2]


@pytest.mark.parametrize("error", [URLError("offline"), TimeoutError()])
def test_network_failure_is_readable(http, error):
    """Report network problems as readable errors."""
    http.responses = [error]
    with pytest.raises(GS2PDFError, match="failed or timed out"):
        download.download_presentation(URL)


@pytest.mark.parametrize(
    "error,expected",
    [
        (OSError("read failed"), GS2PDFError),
        (IncompleteRead(b"partial", 10), GS2PDFError),
        (KeyboardInterrupt(), KeyboardInterrupt),
    ],
)
def test_read_failure_closes_response(http, monkeypatch, error, expected):
    """Close HTTP responses when reading fails or the user cancels."""
    response = io.BytesIO(PDF)
    http.responses = [response]

    def fail():
        raise error

    monkeypatch.setattr(response, "read", fail)
    with pytest.raises(expected):
        download.download_presentation(URL)
    assert response.closed
