"""Conservatively remove a PDF's first visible, page-sized solid fill.

Google exports rectangles as both `re` and closed `m/l/h` paths. Work in
PDF page coordinates, including transforms and rectangular clipping paths.
Only the selected paint instruction is suppressed: never delete resources,
white shapes, or a whole group containing other artwork.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from math import isclose

import pikepdf

from .errors import BackgroundError

Box = tuple[float, float, float, float]
Point = tuple[float, float]
FILL = {"f", "F", "f*", "B", "B*", "b", "b*"}
STROKE = {"S", "s", "B", "B*", "b", "b*"}
PAINT = FILL | STROKE | {"n"}


def _box(points: list[Point]) -> Box:
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


def _intersect(a: Box, b: Box) -> Box:
    return max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])


def _nonempty(box: Box) -> bool:
    return box[0] < box[2] and box[1] < box[3]


def _same_box(a: Box, b: Box) -> bool:
    return all(isclose(x, y, abs_tol=0.05, rel_tol=1e-6) for x, y in zip(a, b))


def _transform_box(box: Box, matrix: pikepdf.Matrix) -> Box:
    x0, y0, x1, y1 = box
    return _box(
        [matrix.transform(p) for p in [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]]
    )


@dataclass
class _Path:
    subpaths: list[list[Point]] = field(default_factory=list)
    curved: bool = False

    def add(self, op: str, operands, ctm: pikepdf.Matrix) -> None:
        if op == "m":
            self.subpaths.append([ctm.transform(tuple(map(float, operands)))])
        elif op == "l" and self.subpaths:
            self.subpaths[-1].append(ctm.transform(tuple(map(float, operands))))
        elif op == "re":
            x, y, w, h = map(float, operands)
            self.subpaths.append(
                [
                    ctm.transform(p)
                    for p in [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
                ]
            )
        elif op in {"c", "v", "y"}:
            self.curved = True
            values = list(map(float, operands))
            if self.subpaths:
                self.subpaths[-1].extend(
                    ctm.transform(p) for p in zip(values[::2], values[1::2])
                )

    @property
    def points(self) -> list[Point]:
        return [p for subpath in self.subpaths for p in subpath]

    @property
    def bounds(self) -> Box | None:
        points = self.points
        return _box(points) if points else None

    @property
    def rectangle(self) -> Box | None:
        if self.curved or len(self.subpaths) != 1:
            return None
        points = self.subpaths[0][:]
        while len(points) > 1 and points[-1] == points[0]:
            points.pop()
        if len(points) != 4:
            return None
        bounds = _box(points)
        x0, y0, x1, y1 = bounds
        corners = {(x0, y0), (x1, y0), (x1, y1), (x0, y1)}
        if set(points) != corners or not _nonempty(bounds):
            return None
        # Four corners in a bow tie are not a rectangle.
        if any(
            a[0] != b[0] and a[1] != b[1]
            for a, b in zip(points, points[1:] + points[:1])
        ):
            return None
        return bounds

    @property
    def has_fill_area(self) -> bool:
        if self.curved:
            return True
        for points in self.subpaths:
            if len(points) >= 3:
                area = sum(
                    a[0] * b[1] - b[0] * a[1]
                    for a, b in zip(points, points[1:] + points[:1])
                )
                if abs(area) > 1e-10:
                    return True
        return False


@dataclass
class _State:
    ctm: pikepdf.Matrix
    clip: Box
    clip_known: bool = True
    fill_alpha: float = 1
    stroke_alpha: float = 1
    solid: bool = True
    ordinary_blend: bool = True
    text_mode: int = 0

    def apply_ext_gstate(self, gs) -> None:
        self.fill_alpha = float(gs.get("/ca", self.fill_alpha))
        self.stroke_alpha = float(gs.get("/CA", self.stroke_alpha))
        if "/BM" in gs:
            self.ordinary_blend = str(gs.BM) in {"/Normal", "/Compatible"}
        if "/SMask" in gs and str(gs.SMask) != "/None":
            self.ordinary_blend = False

    def for_form(self, obj) -> _State:
        child = replace(self)
        child.ctm = (
            pikepdf.Matrix(*map(float, obj.get("/Matrix", [1, 0, 0, 1, 0, 0])))
            @ self.ctm
        )
        if "/BBox" in obj:
            child.clip = _intersect(
                child.clip,
                _transform_box(tuple(map(float, obj.BBox)), child.ctm),
            )
        if obj.get("/Group", {}).get("/S") == pikepdf.Name.Transparency:
            child.ordinary_blend = False
        return child


@dataclass
class _Mark:
    """The first visible mark, addressed by its chain of Do instructions."""

    chain: tuple[int, ...]
    index: int
    bounds: Box
    removable: bool = False
    reason: str = ""


def _has_text(operands) -> bool:
    for obj in operands:
        if isinstance(obj, pikepdf.String) and bytes(obj):
            return True
        if isinstance(obj, pikepdf.Array) and _has_text(obj):
            return True
    return False


def _path_mark(
    path: _Path, state: _State, op: str, chain: tuple[int, ...], index: int
) -> _Mark | None:
    bounds = path.bounds
    fill = op in FILL and state.fill_alpha > 0 and path.has_fill_area
    stroke = op in STROKE and state.stroke_alpha > 0
    if bounds is None or not (fill or stroke):
        return None
    visible = _intersect(bounds, state.clip)
    if not _nonempty(visible):
        return None
    removable = (
        op in {"f", "F", "f*"}
        and path.rectangle is not None
        and state.fill_alpha == 1
        and state.solid
        and state.ordinary_blend
        and state.clip_known
    )
    return _Mark(
        chain,
        index,
        visible,
        removable=removable,
        reason="non-solid, translucent, stroked, or complex background",
    )


def _xobject_mark(
    name, resources, state: _State, chain: tuple[int, ...], index: int
) -> _Mark | None:
    obj = resources.get("/XObject", {}).get(name)
    if obj is None:
        raise BackgroundError("PDF references a missing drawing resource.")
    if str(obj.get("/Subtype")) == "/Form":
        return _first_mark(
            obj,
            obj.get("/Resources", resources),
            state.for_form(obj),
            chain + (index,),
        )
    if state.fill_alpha > 0:
        return _Mark(
            chain,
            index,
            _intersect(_transform_box((0, 0, 1, 1), state.ctm), state.clip),
            reason="image background",
        )
    return None


def _first_mark(
    stream,
    resources,
    state: _State,
    chain: tuple[int, ...] = (),
) -> _Mark | None:
    if len(chain) > 32:
        raise BackgroundError(
            "PDF has recursive or excessively nested drawing groups."
        )
    stack: list[_State] = []
    path = _Path()
    pending_clip = False
    for index, instruction in enumerate(pikepdf.parse_content_stream(stream)):
        if isinstance(instruction, pikepdf.ContentStreamInlineImage):
            if state.fill_alpha > 0 and _nonempty(state.clip):
                return _Mark(
                    chain,
                    index,
                    _transform_box((0, 0, 1, 1), state.ctm),
                    reason="image background",
                )
            continue
        operands, operator = instruction
        op = str(operator)
        if op == "q":
            stack.append(replace(state))
        elif op == "Q":
            if not stack:
                raise BackgroundError(
                    "PDF contains an unbalanced graphics-state stack."
                )
            state = stack.pop()
        elif op == "cm":
            state.ctm = pikepdf.Matrix(*map(float, operands)) @ state.ctm
        elif op == "gs":
            gs = resources.get("/ExtGState", {}).get(operands[0], {})
            state.apply_ext_gstate(gs)
        elif op in {"g", "rg", "k"}:
            state.solid = True
        elif op == "cs":
            state.solid = str(operands[0]) in {
                "/DeviceGray",
                "/DeviceRGB",
                "/DeviceCMYK",
            }
        elif op == "scn" and any(isinstance(o, pikepdf.Name) for o in operands):
            state.solid = False
        elif op in {"m", "l", "re", "c", "v", "y"}:
            path.add(op, operands, state.ctm)
        elif op in {"W", "W*"}:
            pending_clip = True
        elif op in PAINT:
            mark = _path_mark(path, state, op, chain, index)
            if mark is not None:
                return mark
            bounds = path.bounds
            if pending_clip:
                state.clip = (
                    _intersect(state.clip, bounds) if bounds else (0, 0, 0, 0)
                )
                state.clip_known = (
                    state.clip_known and path.rectangle is not None
                )
            path = _Path()
            pending_clip = False
        elif op == "Do" and _nonempty(state.clip):
            mark = _xobject_mark(operands[0], resources, state, chain, index)
            if mark is not None:
                return mark
        elif op == "sh" and state.fill_alpha > 0:
            return _Mark(chain, index, state.clip, reason="gradient background")
        elif op == "Tr":
            state.text_mode = int(operands[0])
        elif op in {"Tj", "TJ", "'", '"'} and _has_text(operands):
            if state.text_mode >= 4:
                state.clip_known = False
            visible_fill = (
                state.text_mode in {0, 2, 4, 6} and state.fill_alpha > 0
            )
            visible_stroke = (
                state.text_mode in {1, 2, 5, 6} and state.stroke_alpha > 0
            )
            if visible_fill or visible_stroke:
                # Text is foreground, even when it spans the whole slide.
                return _Mark(chain, index, (0, 0, 0, 0))
    return None


def _rewrite(pdf: pikepdf.Pdf, stream, resources, mark: _Mark, depth: int = 0):
    instructions = list(pikepdf.parse_content_stream(stream))
    resources = pikepdf.Dictionary(resources)
    if depth == len(mark.chain):
        # `n` ends the path and applies any pending clip, without painting.
        instructions[mark.index] = ([], pikepdf.Operator("n"))
    else:
        index = mark.chain[depth]
        operands, _ = instructions[index]
        xobjects = pikepdf.Dictionary(resources.get("/XObject", {}))
        original = xobjects[operands[0]]
        data, child_resources = _rewrite(
            pdf,
            original,
            original.get("/Resources", resources),
            mark,
            depth + 1,
        )
        clone = pdf.make_stream(data)
        for key, value in original.items():
            if key not in {"/Length", "/Filter", "/DecodeParms", "/Resources"}:
                clone[key] = value
        clone.Resources = child_resources
        number = 0
        while (name := pikepdf.Name(f"/GS2PDFBackground{number}")) in xobjects:
            number += 1
        xobjects[name] = clone
        resources.XObject = xobjects
        instructions[index] = ([name], pikepdf.Operator("Do"))
    return pikepdf.unparse_content_stream(instructions), resources


def remove_background(pdf: pikepdf.Pdf, page: pikepdf.Page) -> bool:
    """Remove one unambiguous background; return whether a fill was removed.

    A full-page first mark that is not a plain opaque fill is an explicit
    unsupported case. Smaller first marks are treated as foreground on an
    already transparent page. Shared Form XObjects are cloned per invocation.
    """
    box = tuple(map(float, page.cropbox))
    state = _State(pikepdf.Matrix(), box)
    resources = page.resources
    mark = _first_mark(page, resources, state)
    if mark is None or not _same_box(mark.bounds, box):
        return False
    if not mark.removable:
        raise BackgroundError(
            f"Cannot safely remove {mark.reason}; "
            "only solid slide backgrounds are supported."
        )
    data, resources = _rewrite(pdf, page, resources, mark)
    page.Contents = pdf.make_stream(data)
    page.Resources = resources
    next_mark = _first_mark(page, resources, _State(pikepdf.Matrix(), box))
    if next_mark is not None and _same_box(next_mark.bounds, box):
        raise BackgroundError(
            "Multiple page-sized background layers are ambiguous; "
            "simplify the slide background first."
        )
    return True
