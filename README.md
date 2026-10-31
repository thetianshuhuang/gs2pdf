# gs2pdf

Download Google Slides diagrams as individual, transparent, tightly cropped PDFs. Shapes stay vector graphics, text stays selectable, and fonts stay embedded.

## Install

With [uv](https://docs.astral.sh/uv/getting-started/installation/):

```bash
uv tool install git+https://github.com/thetianshuhuang/gs2pdf
```

## Usage

Share the presentation as **Anyone with the link → Viewer**, then:

```bash
gs2pdf "https://docs.google.com/presentation/d/PRESENTATION_ID/edit"
gs2pdf "SLIDES_URL" --slides 1,3-5 --output-dir figures
gs2pdf presentation.pdf  # an already downloaded PDF
```

Each slide is written to `figures/slide-001.pdf`, `slide-002.pdf`, and so on. Empty slides are skipped, and existing files are kept unless `--overwrite` is given. See `gs2pdf --help` for all options.

Each slide should contain one diagram on a solid background; everything else on the slide, including titles, is kept. Image, gradient, and translucent slide backgrounds are not supported.
