"""extract_text reads page by page. No network, no real PDF."""

import pytest

from skemman_scraper import titlepage_load as tl


class _Page:
    def __init__(self, text="", error=None):
        self.text = text
        self.error = error

    def extract_text(self):
        if self.error:
            raise self.error
        return self.text


class _Reader:
    def __init__(self, pages):
        self.pages = pages


def _patch(monkeypatch, pages):
    monkeypatch.setattr(tl.pypdf, "PdfReader", lambda path: _Reader(pages))


def test_a_page_that_cannot_be_read_is_left_out(monkeypatch, tmp_path):
    # 26756: pages 19-20 use a font whose width table runs backwards.
    _patch(monkeypatch, [
        _Page("Faculty of Civil Engineering"),
        _Page(error=ValueError("Invalid CID width range: 19..17.")),
        _Page("Abstract"),
    ])
    text, total = tl.extract_text(tmp_path / "x.pdf", pages=20)
    assert total == 3
    assert "Faculty of Civil Engineering" in text
    assert "Abstract" in text


def test_the_failed_page_keeps_its_place(monkeypatch, tmp_path):
    _patch(monkeypatch, [_Page("one"), _Page(error=ValueError("x")), _Page("three")])
    text, _ = tl.extract_text(tmp_path / "x.pdf", pages=20)
    assert text.split("\n") == ["one", "", "three"]


def test_a_document_with_no_readable_page_is_still_an_error(monkeypatch, tmp_path):
    _patch(monkeypatch, [_Page(error=ValueError("bad")), _Page(error=ValueError("bad"))])
    with pytest.raises(ValueError):
        tl.extract_text(tmp_path / "x.pdf", pages=20)


def test_only_the_requested_pages_are_read(monkeypatch, tmp_path):
    # Page 3 would fail, but only two are asked for.
    _patch(monkeypatch, [_Page("a"), _Page("b"), _Page(error=ValueError("never read"))])
    text, total = tl.extract_text(tmp_path / "x.pdf", pages=2)
    assert (text, total) == ("a\nb", 3)
