"""Shared fixtures for building small PDF documents."""

from pathlib import Path

import pikepdf
import pytest


@pytest.fixture
def pdf_factory(tmp_path):
    """Provide a factory for PDFs with custom content streams."""

    def make(streams: list[bytes], *, resources=None, size=(200, 120)) -> Path:
        destination = tmp_path / "input.pdf"
        with pikepdf.Pdf.new() as pdf:
            for stream in streams:
                page = pdf.add_blank_page(page_size=size)
                page.Contents = pdf.make_stream(stream)
                page.Resources = resources or pikepdf.Dictionary()
            pdf.save(destination)
        return destination

    return make
