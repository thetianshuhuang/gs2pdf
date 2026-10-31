# gs2pdf

Download Google Slides diagrams as individual, transparent, tightly cropped PDFs.
Shapes stay vector graphics, text stays selectable, and exported fonts stay embedded.

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run this
from a checkout of this repository:

```bash
uv tool install .
gs2pdf "https://docs.google.com/presentation/d/YOUR_PRESENTATION_ID/edit" --output-dir figures
```

The package requires Python 3.11 or later; uv can provision a compatible Python.
If the command is not on your PATH, run `uv tool update-shell` and open a new terminal.
The package is not on PyPI, so install from the checkout or a built wheel.
Alternatively, run it without installing via `uv run gs2pdf …` inside the checkout.

## Google Slides access

In Google Slides, set **Share → General access → Anyone with the link → Viewer**
and keep viewer downloads enabled in **Share → Settings**. Copy the presentation
link and pass it to gs2pdf. See [Google's sharing instructions](https://support.google.com/drive/answer/7166529?hl=en).

The tool downloads Google's PDF export anonymously over HTTPS, so no browser,
Google login, or API key is required. Links restricted to individual accounts or
an organization that requires sign-in are unsupported. If the file is private or
the owner disables downloads, the command explains which sharing settings are
needed. Resource keys in sharing links are preserved.

## Output and options

```bash
# All slides, one PDF per slide, with zero margin
gs2pdf "SLIDES_URL" --output-dir figures

# Selected slides; preserve original slide numbers in output filenames
gs2pdf "SLIDES_URL" --slides 1,3-5 --output-dir figures

# Convert an already downloaded PDF; no network required
gs2pdf presentation.pdf --output-dir figures

# Explicitly replace existing PDFs
gs2pdf presentation.pdf --output-dir figures --overwrite

gs2pdf --help
```

The default output directory is `figures`. Files are named `slide-001.pdf`,
`slide-002.pdf`, and so on. Each page is cropped flush to the artwork, measured
at 300 DPI and refined by PDF object bounds; the output itself is vector PDF,
not a screenshot.

Empty slides are skipped. Existing files are preserved unless `--overwrite` is
specified. Individual failures are reported and other slides continue. Exit status
is 0 for success (including skipped empty slides), 1 for download/conversion
failures, 2 for invalid CLI arguments, and 130 for cancellation. Progress and
summaries go to stderr. Use `--timeout SECONDS` to change the default 90-second
HTTP request timeout. Rate limits and temporary server errors are retried up to
three times with delays of 2, 4, and 8 seconds.

## Supported diagrams

Each slide should contain one diagram on a solid background. All foreground
shapes, text, and images are retained, including white-filled diagram boxes.
Titles and footers are also foreground and will be kept. Use slides containing
only the artwork you want to export.

Background detection is intentionally conservative: it removes the first visible
opaque solid rectangular fill covering the slide. It handles Google Slides'
transforms, polygon rectangles, invisible fills, and rectangular clips, as well
as backgrounds inside shared drawing groups. Pages beginning with smaller
foreground objects are treated as already transparent.

Photo, gradient, translucent, stroked, and multiple-layer page backgrounds are
unsupported and produce errors when detected. A full-slide solid foreground
rectangle is indistinguishable from a background and will be treated as one.
Complex clipping or themed artwork can also prevent background identification.
PDF links, comments, and interactive annotations are not copied to the outputs;
this tool exports the diagram's page artwork. Very large slides exceeding
100 megapixels at measurement resolution are rejected to bound memory use.

## Development

The local development environment is pinned to Python 3.15 in `.python-version`.
uv downloads the pinned interpreter if it is not already installed.

```bash
uv sync --locked
uv run gs2pdf --help
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv build
```

Dependencies are declared in `pyproject.toml`; `uv.lock` records the development
environment. Use `uv add PACKAGE` or `uv add --dev PACKAGE` to change dependencies.
Use `uv run ruff format .` to apply the project's 80-column formatting rules.
For an editable command available outside the project:

```bash
uv tool install --editable .
```

`uv build` produces a wheel and source distribution in `dist/`. Install the wheel
with `uv tool install dist/gs2pdf-0.1.0-py3-none-any.whl`.
