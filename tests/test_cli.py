"""Command-line behavior and error reporting tests."""

import pikepdf
import pytest

from gs2pdf.cli import main


def test_cli_success_and_partial_failure(pdf_factory, tmp_path, capsys):
    """Continue exporting good slides while reporting failed slides."""
    source = pdf_factory([b"1 0 0 rg 40 30 80 60 re f", b"0 0 200 120 re B"])
    assert main([str(source), "--output-dir", str(tmp_path / "out")]) == 1
    assert (tmp_path / "out/slide-001.pdf").exists()
    assert "1 failed slide(s)" in capsys.readouterr().err


def test_cli_pdf_does_not_require_network(pdf_factory, tmp_path, monkeypatch):
    """Convert local PDFs without making network requests."""
    from gs2pdf import download

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
    assert "Input PDF not found" in capsys.readouterr().err


@pytest.mark.parametrize(
    "option,value",
    [
        ("--timeout", "nan"),
        ("--timeout", "-1"),
        ("--timeout", "0"),
        ("--timeout", "inf"),
    ],
)
def test_cli_bad_numbers(option, value):
    """Reject nonfinite, negative, or zero numeric options."""
    with pytest.raises(SystemExit) as exc:
        main(["input.pdf", option, value])
    assert exc.value.code == 2


def test_cli_cannot_add_margins():
    """Reject the former margin option so output padding stays zero."""
    with pytest.raises(SystemExit) as exc:
        main(["input.pdf", "--margin", "2"])
    assert exc.value.code == 2


def test_help_and_version(capsys):
    """Print help and package version without requiring an input."""
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "gs2pdf" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
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
def test_downloaded_source_lifetime(
    pdf_factory, tmp_path, monkeypatch, download_fails
):
    """Keep downloads through conversion and delete them on success or error."""
    from gs2pdf import download as downloader
    from gs2pdf.errors import GS2PDFError

    source = pdf_factory([b"1 0 0 rg 40 30 80 60 re f"])
    downloaded = []

    def download(url, destination, **kwargs):
        downloaded.append(destination)
        if download_fails:
            raise GS2PDFError("Download failed")
        destination.write_bytes(source.read_bytes())

    monkeypatch.setattr(downloader, "download_presentation", download)
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
    assert len(downloaded) == 1
    assert not downloaded[0].parent.exists()
