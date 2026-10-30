"""PDF background removal and vector-preserving crop tests."""

import io

import pikepdf
import pymupdf
import pytest
from PIL import Image

from gs2pdf.convert import convert_pdf, select_slides
from gs2pdf.errors import GS2PDFError


@pytest.mark.parametrize("shape", ["rectangle", "ellipse"])
def test_output_has_no_added_margin(tmp_path, shape):
    """Crop every edge flush to artwork, including curved diagrams."""
    source = tmp_path / "input.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=200, height=120)
        bounds = pymupdf.Rect(48, 24, 120, 96)
        if shape == "rectangle":
            page.draw_rect(bounds, color=None, fill=(1, 0, 0))
        else:
            page.draw_oval(bounds, color=None, fill=(1, 0, 0))
        doc.save(source)
    result = convert_pdf(source, tmp_path / "out", report=lambda _: None)
    assert not result.failed
    with pymupdf.open(result.written[0]) as doc:
        page = doc[0]
        assert page.rect == pymupdf.Rect(0, 0, 72, 72)
        pix = page.get_pixmap(dpi=300, alpha=True)
        image = Image.frombytes("RGBA", (pix.width, pix.height), pix.samples)
        assert image.getchannel("A").getbbox() == (0, 0, pix.width, pix.height)
        if shape == "ellipse":
            assert pix.pixel(0, 0)[3] == 0


def test_colored_background_white_shapes_fonts_images_and_vectors(tmp_path):
    """Remove the slide fill while preserving diagram content."""
    source = tmp_path / "source.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=200, height=120)
        page.draw_rect(page.rect, color=None, fill=(0.2, 0.4, 0.8))
        page.draw_rect(
            pymupdf.Rect(40, 30, 120, 90), color=None, fill=(1, 1, 1)
        )
        page.draw_rect(pymupdf.Rect(50, 40, 60, 50), color=None, fill=(1, 0, 0))
        font = pymupdf.Font("helv")
        page.insert_font(fontname="DiagramFont", fontbuffer=font.buffer)
        page.insert_text(
            (65, 65), "Diagram", fontsize=10, fontname="DiagramFont"
        )
        png = io.BytesIO()
        Image.new("RGBA", (8, 8), (0, 200, 0, 128)).save(png, format="PNG")
        page.insert_image(pymupdf.Rect(70, 70, 80, 80), stream=png.getvalue())
        expected_font = doc.extract_font(page.get_fonts()[0][0])[3]
        doc.save(source)
    output = tmp_path / "out"
    result = convert_pdf(source, output, report=lambda _: None)
    assert not result.failed
    with pymupdf.open(result.written[0]) as doc:
        page = doc[0]
        assert page.rect.width == pytest.approx(80, abs=0.24)
        assert page.rect.height == pytest.approx(60, abs=0.24)
        assert "Diagram" in page.get_text()
        assert expected_font in [
            doc.extract_font(font[0])[3] for font in page.get_fonts(full=True)
        ]
        assert len(page.get_images()) == 1
        assert len(page.get_drawings()) >= 2
        pix = page.get_pixmap(alpha=True)
        assert pix.pixel(8, 55) == (255, 255, 255, 255)  # white box preserved
        assert pix.pixel(17, 17) == (255, 0, 0, 255)


def test_google_polygon_coordinates_and_invisible_fills(pdf_factory, tmp_path):
    # The same structure as Google's real PDF export: reflected coordinates,
    # large source units, polygon rectangles, page clip, and zero-alpha shapes.
    """Handle the path and graphics-state patterns in Slides exports."""
    resources = pikepdf.Dictionary(
        ExtGState=pikepdf.Dictionary(
            Invisible=pikepdf.Dictionary(ca=0, CA=0),
            Opaque=pikepdf.Dictionary(ca=1, CA=1),
        )
    )
    source = pdf_factory(
        [
            b"""
        q 1 0 0 -1 0 120 cm .002 0 0 .002 0 0 cm
        0 0 m 100000 0 l 100000 60000 l 0 60000 l 0 0 l h W n
        q /Invisible gs 0 0 100000 60000 re f Q
        q /Opaque gs .4 .4 .4 rg
        0 0 m 100000 0 l 100000 60000 l 0 60000 l h f* Q
        q /Invisible gs 0 0 100000 60000 re f Q
        1 0 0 rg 20000 15000 m 60000 15000 l 60000 45000 l 20000 45000 l h f* Q
    """
        ],
        resources=resources,
    )
    result = convert_pdf(source, tmp_path / "out", report=lambda _: None)
    assert not result.failed
    with pymupdf.open(result.written[0]) as doc:
        assert doc[0].rect.width < 90


def test_transparent_foreground_and_small_clipped_fill_are_preserved(
    pdf_factory, tmp_path
):
    """Keep a full-sized fill restricted by a smaller diagram clip."""
    source = pdf_factory([b"q 40 30 80 60 re W n 1 1 1 rg 0 0 200 120 re f Q"])
    result = convert_pdf(source, tmp_path / "out", report=lambda _: None)
    assert not result.failed
    with pymupdf.open(result.written[0]) as doc:
        assert doc[0].rect.width < 82
        assert doc[0].get_pixmap(alpha=True).pixel(10, 10) == (
            255,
            255,
            255,
            255,
        )


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_edges_thin_lines_arrows_shadows_and_rotation(tmp_path, rotation):
    """Include thin strokes and shadows when cropping rotated pages."""
    source = tmp_path / "input.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=200, height=120)
        page.draw_rect(page.rect, fill=(1, 1, 1), color=None)
        page.draw_rect(
            pymupdf.Rect(4, 34, 104, 94),
            fill=(0, 0, 0),
            color=None,
            fill_opacity=0.15,
        )
        page.draw_rect(
            pymupdf.Rect(0, 30, 100, 90), fill=(0.5, 0, 0.5), color=None
        )
        page.draw_line((0, 20), (130, 20), color=(0, 0, 0), width=0.05)
        page.draw_polyline(
            [(125, 15), (130, 20), (125, 25)], color=(0, 0, 0), width=0.5
        )
        page.set_rotation(rotation)
        doc.save(source)
    result = convert_pdf(source, tmp_path / "out", report=lambda _: None)
    assert not result.failed
    with pymupdf.open(result.written[0]) as doc:
        page = doc[0]
        assert 79 < min(page.rect.width, page.rect.height) < 80
        assert 130 < max(page.rect.width, page.rect.height) < 131
        assert len(page.get_drawings()) >= 4


def test_selection_empty_overwrite_and_partial_failure(pdf_factory, tmp_path):
    """Handle empty slides, existing files, and independent failures."""
    source = pdf_factory(
        [
            b"1 1 1 rg 0 0 200 120 re f",  # background only
            b"1 1 1 rg 0 0 200 120 re f 1 0 0 rg 40 30 80 60 re f",
            b"1 1 1 rg 0 0 200 120 re f 1 1 1 rg 0 0 200 120 re f",  # ambiguous
            b"0 0 1 rg 10 10 30 40 re f",
        ]
    )
    output = tmp_path / "out"
    result = convert_pdf(source, output, report=lambda _: None)
    assert result.empty == [1]
    assert list(result.failed) == [3]
    assert [p.name for p in result.written] == [
        "slide-002.pdf",
        "slide-004.pdf",
    ]
    original = (output / "slide-002.pdf").read_bytes()
    result = convert_pdf(source, output, slides="2", report=lambda _: None)
    assert "already exists" in result.failed[2]
    assert (output / "slide-002.pdf").read_bytes() == original
    (output / "slide-002.pdf").write_bytes(b"existing output to replace")
    result = convert_pdf(
        source,
        output,
        slides="2",
        overwrite=True,
        report=lambda _: None,
    )
    assert not result.failed
    with pymupdf.open(output / "slide-002.pdf") as doc:
        assert len(doc) == 1


def test_shared_form_background_removed_only_for_selected_invocation(tmp_path):
    """Preserve other uses of a shared drawing group."""
    source = tmp_path / "source.pdf"
    with pikepdf.Pdf.new() as doc:
        form = doc.make_stream(
            b"1 1 1 rg 0 0 200 120 re f 1 0 0 rg 40 30 80 60 re f"
        )
        form.Type = pikepdf.Name.XObject
        form.Subtype = pikepdf.Name.Form
        form.BBox = [0, 0, 200, 120]
        form.Resources = pikepdf.Dictionary()
        for _ in range(2):
            page = doc.add_blank_page(page_size=(200, 120))
            page.Resources = pikepdf.Dictionary(
                XObject=pikepdf.Dictionary(Shared=form)
            )
            page.Contents = doc.make_stream(
                b"/Shared Do q .2 0 0 .2 150 90 cm /Shared Do Q"
            )
        doc.save(source)
    result = convert_pdf(source, tmp_path / "out", report=lambda _: None)
    assert not result.failed
    for path in result.written:
        with pymupdf.open(path) as doc:
            # Keep white in the small reused Form; remove full-page white.
            white = [d for d in doc[0].get_drawings() if d["fill"] == (1, 1, 1)]
            assert len(white) == 1
            assert white[0]["rect"].width == pytest.approx(40)
    with pikepdf.open(source) as doc:
        assert b"re f" in doc.pages[1].Resources.XObject.Shared.read_bytes()


@pytest.mark.parametrize(
    "stream,resources",
    [
        (
            b"/Fade gs 0 0 200 120 re f",
            pikepdf.Dictionary(
                ExtGState=pikepdf.Dictionary(Fade=pikepdf.Dictionary(ca=0.5))
            ),
        ),
        (b"0 0 200 120 re B", None),
        (b"0 0 m 200 0 l 180 120 l 0 120 l h f", None),
        (b"0 0 m 200 0 l 180 120 l 0 120 l h W n 0 0 200 120 re f", None),
    ],
)
def test_ambiguous_full_page_artwork_fails_safely(
    pdf_factory, tmp_path, stream, resources
):
    """Reject page-sized artwork that cannot be treated as background."""
    source = pdf_factory([stream], resources=resources)
    result = convert_pdf(source, tmp_path / "out", report=lambda _: None)
    assert 1 in result.failed
    assert not result.written


def test_full_page_image_background_is_unsupported(tmp_path):
    """Reject image backgrounds without removing diagram content."""
    source = tmp_path / "source.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=200, height=120)
        data = io.BytesIO()
        Image.new("RGB", (20, 12), "green").save(data, format="PNG")
        page.insert_image(page.rect, stream=data.getvalue())
        doc.save(source)
    result = convert_pdf(source, tmp_path / "out", report=lambda _: None)
    assert "image background" in result.failed[1]


def test_full_page_gradient_is_unsupported(pdf_factory, tmp_path):
    """Reject gradient backgrounds without writing an output."""
    function = pikepdf.Dictionary(
        FunctionType=2, Domain=[0, 1], C0=[1, 0, 0], C1=[0, 0, 1], N=1
    )
    shading = pikepdf.Dictionary(
        ShadingType=2,
        ColorSpace=pikepdf.Name.DeviceRGB,
        Coords=[0, 0, 200, 120],
        Function=function,
        Extend=[True, True],
    )
    source = pdf_factory(
        [b"/Gradient sh"],
        resources=pikepdf.Dictionary(
            Shading=pikepdf.Dictionary(Gradient=shading)
        ),
    )
    result = convert_pdf(source, tmp_path / "out", report=lambda _: None)
    assert "gradient background" in result.failed[1]
    assert not result.written


@pytest.mark.parametrize(
    "selection",
    ["", "0", "3", "2-1", "1-3", "1,", "nan", "1--2", "-1", "1.5", "１"],
)
def test_invalid_slide_selection(selection):
    """Reject malformed or out-of-range slide selections."""
    with pytest.raises(GS2PDFError):
        select_slides(selection, 2)


def test_valid_slide_selection():
    """Sort and deduplicate slide numbers and overlapping ranges."""
    assert select_slides("5,1,3-5, 2", 5) == [1, 2, 3, 4, 5]
    assert select_slides(None, 3) == [1, 2, 3]


def test_invalid_pdf_and_selection_do_not_write(tmp_path, pdf_factory):
    """Validate input and slide selection before creating outputs."""
    invalid = tmp_path / "invalid.pdf"
    invalid.write_bytes(b"<html>not a PDF</html>")
    with pytest.raises(GS2PDFError, match="Cannot read PDF"):
        convert_pdf(invalid, tmp_path / "out")
    source = pdf_factory([b""])
    with pytest.raises(GS2PDFError, match="Invalid slide selection"):
        convert_pdf(source, tmp_path / "out", slides="2")
    assert not (tmp_path / "out").exists()


def test_cannot_overwrite_input(pdf_factory, tmp_path):
    """Protect the source PDF even when overwrite is enabled."""
    source = pdf_factory([b"0 0 1 rg 10 10 30 40 re f"])
    renamed = tmp_path / "slide-001.pdf"
    source.rename(renamed)
    before = renamed.read_bytes()
    result = convert_pdf(
        renamed, tmp_path, overwrite=True, report=lambda _: None
    )
    assert "input PDF" in result.failed[1]
    assert renamed.read_bytes() == before


def test_comments_do_not_expand_artwork_crop(tmp_path):
    """Exclude annotation appearances from the diagram crop."""
    source = tmp_path / "commented.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=200, height=120)
        page.draw_rect(pymupdf.Rect(40, 30, 80, 60), color=None, fill=(1, 0, 0))
        page.add_text_annot((170, 100), "Reviewer note")
        doc.save(source)
    result = convert_pdf(source, tmp_path / "out", report=lambda _: None)
    assert not result.failed
    with pymupdf.open(result.written[0]) as doc:
        assert doc[0].rect.width < 46
        assert doc[0].first_annot is None


@pytest.mark.parametrize(
    "box,matrix,transparency,expected_color",
    [
        ([0, 0, 400, 240], [0.5, 0, 0, 0.5, 0, 0], False, (255, 0, 0, 255)),
        ([40, 30, 120, 90], [1, 0, 0, 1, 0, 0], False, (255, 255, 255, 255)),
        ([0, 0, 400, 240], [0.5, 0, 0, 0.5, 0, 0], True, None),
    ],
)
def test_form_transform_clip_and_transparency(
    tmp_path, box, matrix, transparency, expected_color
):
    """Respect Form transforms, clips, and transparency during removal."""
    source = tmp_path / "form.pdf"
    with pikepdf.Pdf.new() as doc:
        form = doc.make_stream(
            b"1 1 1 rg 0 0 400 240 re f 1 0 0 rg 80 60 160 120 re f"
        )
        form.Type = pikepdf.Name.XObject
        form.Subtype = pikepdf.Name.Form
        form.BBox = box
        form.Matrix = matrix
        form.Resources = pikepdf.Dictionary()
        if transparency:
            form.Group = pikepdf.Dictionary(S=pikepdf.Name.Transparency)
        page = doc.add_blank_page(page_size=(200, 120))
        page.Resources = pikepdf.Dictionary(
            XObject=pikepdf.Dictionary(Diagram=form)
        )
        page.Contents = doc.make_stream(b"/Diagram Do")
        doc.save(source)
    result = convert_pdf(source, tmp_path / "out", report=lambda _: None)
    if transparency:
        assert 1 in result.failed
        assert not result.written
        return
    assert not result.failed
    with pymupdf.open(result.written[0]) as doc:
        assert doc[0].rect.width == pytest.approx(80, abs=0.24)
        assert doc[0].rect.height == pytest.approx(60, abs=0.24)
        pix = doc[0].get_pixmap(alpha=True)
        assert pix.pixel(8, 8) == expected_color
