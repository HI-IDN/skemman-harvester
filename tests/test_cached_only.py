"""Offline title-page reading never touches the network."""

import pytest

from skemman_scraper import titlepage_load as tl


class _NoNetwork:
    def download_binary(self, *args, **kwargs):
        raise AssertionError("an offline read must not fetch")


def test_offline_parses_a_short_cache_as_it_is(tmp_path):
    # Eight pages cached, twenty wanted: online this is refetched, offline it
    # is parsed as it stands.
    (tmp_path / "1.txt").write_text(
        tl.PAGE_MARKER + "90\t8\nFaculty of Civil Engineering", encoding="utf-8"
    )
    text, n_pages, cached = tl._ensure_text(
        1, "u", tmp_path, tmp_path, _NoNetwork(), False, 20, offline=True
    )
    assert cached is True
    assert n_pages == 90
    assert "Civil Engineering" in text


def test_offline_without_a_cache_is_skipped_not_fetched(tmp_path):
    with pytest.raises(tl._NotCached):
        tl._ensure_text(2, "u", tmp_path, tmp_path, _NoNetwork(), False, 20, offline=True)
