"""Errors that can be presented directly to a command-line user."""


class GS2PDFError(Exception):
    """An expected download or conversion failure."""


class BackgroundError(GS2PDFError):
    """The background cannot safely be distinguished from the artwork."""
