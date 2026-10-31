"""Command-line behavior and error reporting tests."""

import pikepdf
import pytest

from gs2pdf import cli, download
from gs2pdf.cli import main
from gs2pdf.errors import GS2PDFError


def test_cli_success_and_partial_failure(pdf_factory, tmp_path, capsys):
    """Continue exporting good slides while reporting failed slides."""
    source = pdf_factory([b"1 0 0 rg 40 30 80 60 re f", b"0 0 200 120 re B"])
    assert main([str(source), "--output-dir", str(tmp_path / "out")]) == 1
    assert (tmp_path / "out/slide-001.pdf").exists()
    assert "1 failed slide(s)" in capsys.readouterr().err


def test_cli_pdf_does_not_require_network(pdf_factory, tmp_path, monkeypatch):
    """Convert local PDFs without making network requests."""
    monkeypatch.setattr(
        download,
        "urlopen",
        lambda *args, **kwargs: pytest.fail("Local PDFs must not use HTTP"),
    )
    source = pdf_factory([b"1 0 0 rg 40 30 80 60 re f"])
    assert main([str(source), "--output-dir", str(tmp_path / "out")]) == 0


def test_cli_missing_input(tmp_path, capsys):
    """Report a missing local file with a failure exit status."""
    assert main([str(tmp_path / "missing.pdf")]) == 1
    assert "Cannot read PDF" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["nan", "-1", "0", "inf"])
def test_cli_bad_timeout(value):
    """Reject nonfinite, negative, or zero timeouts."""
    assert main(["input.pdf", "--timeout", value]) == 2


@pytest.mark.parametrize(
    "argv", [[], ["input.pdf", "--timeout", "soon"], ["a.pdf", "--margin", "1"]]
)
def test_cli_invalid_arguments(argv):
    """Exit with usage status for unparseable arguments."""
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2


def test_help_and_version(capsys):
    """Print help and package version without requiring an input."""
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "gs2pdf" in capsys.readouterr().out
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "gs2pdf 0.1.0"


@pytest.mark.parametrize("password", ["", "secret"])
def test_encrypted_input_has_readable_error(tmp_path, capsys, password):
    """Report encrypted input without exposing a traceback."""
    source = tmp_path / "encrypted.pdf"
    with pikepdf.Pdf.new() as doc:
        doc.add_blank_page()
        doc.save(
            source, encryption=pikepdf.Encryption(owner="owner", user=password)
        )
    assert main([str(source)]) == 1
    assert "Encrypted PDFs are not supported" in capsys.readouterr().err


@pytest.mark.parametrize("download_fails", [False, True])
def test_cli_downloads_urls(pdf_factory, tmp_path, monkeypatch, download_fails):
    """Convert downloaded PDFs and report download failures."""
    source = pdf_factory([b"1 0 0 rg 40 30 80 60 re f"])

    def download(url, **kwargs):
        if download_fails:
            raise GS2PDFError("Download failed")
        return source.read_bytes()

    monkeypatch.setattr(cli, "download_presentation", download)
    output = tmp_path / "out"
    status = main(
        [
            "https://docs.google.com/presentation/d/id/edit",
            "--output-dir",
            str(output),
        ]
    )
    assert status == int(download_fails)
    assert (output / "slide-001.pdf").exists() == (not download_fails)
